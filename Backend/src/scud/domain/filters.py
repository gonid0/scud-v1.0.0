"""Filter package generation (§6.1)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.config import settings
from scud.crypto.bloom import bloom_contains, build_bloom
from scud.crypto.serialization import pack_filter_package
from scud.db.models import DeliveryTask, FilterPackage, IssuedKey, Reader
from scud.db.repositories import filters as filter_repo
from scud.db.repositories import tasks as task_repo
from scud.observability.filter_metrics import (
    scud_filter_oversaturated_total,
    scud_filter_whitelist_cap_utilization,
)

logger = logging.getLogger(__name__)

_LN2 = math.log(2)


class ReaderOversaturated(RuntimeError):
    """Whitelist cap is unreachable even at max m_bits — the reader needs re-key.

    Subclasses RuntimeError so the worker still marks the task 'failed' exactly as
    before (the failure *logic* is unchanged); the difference is a distinct,
    diagnostic message + a metric so the terminal 'failed' row is actionable
    instead of an opaque "cannot fit whitelist".
    """

    def __init__(
        self,
        *,
        n: int,
        active: int,
        m_bits: int,
        k_hashes: int,
        expected_whitelist: float,
        best_whitelist: int | None,
        cap: int,
    ) -> None:
        self.n = n
        self.active = active
        self.m_bits = m_bits
        self.k_hashes = k_hashes
        self.expected_whitelist = expected_whitelist
        self.best_whitelist = best_whitelist
        self.cap = cap
        best = "n/a" if best_whitelist is None else str(best_whitelist)
        super().__init__(
            f"reader oversaturated: revoked={n} active={active} m_bits={m_bits} "
            f"k_hashes={k_hashes} E[whitelist]={expected_whitelist:.0f} "
            f"best_seen={best} > cap={cap}; reader needs re-key"
        )


@dataclass
class BloomSelection:
    """Result of select_bloom_params — everything the package needs."""

    m_bits: int
    k_hashes: int
    hash_seed: int
    bloom_bytes: bytes
    whitelist: list[tuple[bytes, int]]  # (key_id, expires_at)
    expected_whitelist: float


def _byte_align(m_bits: int) -> int:
    return ((m_bits + 7) // 8) * 8


def _size_for_fp(n: int, fp_rate: float, budget_bits: int) -> int:
    """§8 optimum m_bits for n elements at target fp_rate, byte-aligned, capped."""
    m = math.ceil(-n * math.log(fp_rate) / (_LN2 ** 2))
    return min(_byte_align(m), budget_bits)


def _k_for(m_bits: int, n: int) -> int:
    return max(1, math.ceil(m_bits / n * _LN2))


def _expected_whitelist(m_bits: int, k_hashes: int, n: int, active_count: int) -> float:
    """Closed-form E[whitelist] = active_count * q, q = (1 - e^{-k*n/m})^k.

    The realized whitelist varies (std ~ sqrt(E)) around this mean; the hash seed
    moves only the realization, never the mean — only m_bits/k/n can.
    """
    if active_count == 0 or m_bits == 0:
        return 0.0
    q = (1.0 - math.exp(-k_hashes * n / m_bits)) ** k_hashes
    return active_count * q


def select_bloom_params(
    candidates: list[bytes],
    active: list[tuple[bytes, int]],  # (key_id, expires_at_ts)
    cfg=settings,
) -> BloomSelection:
    """Pick (m_bits, k_hashes, hash_seed) and build the bloom + whitelist.

    Strategy (per the seed-scheme audit — the seed alone could never lower the
    whitelist mean):
      1. size m_bits from THIS reader's n at the configured FP target;
      2. if the closed-form E[whitelist] is near the cap, GROW m_bits (binary
         search up to the byte budget) — the only lever that lowers E[whitelist];
      3. if even the max-size filter is hopeless (E[whitelist] far above the cap),
         fail fast with ReaderOversaturated instead of building doomed blooms;
      4. otherwise run a seed search (a cheap tiebreaker on the *realized* count):
         a normal budget when E[whitelist] is comfortably under cap, an extended
         budget in the marginal band. Seed steps by k_hashes so successive blooms
         use DISJOINT murmur windows (genuinely independent rerolls), and we never
         ship a seed whose murmur window would overflow uint32 on the reader.

    Pure function (no DB / no side effects) so the saturation branches are unit
    testable. cfg duck-types Settings.
    """
    n = len(candidates)
    cap = cfg.whitelist_hard_cap

    if n == 0:
        return BloomSelection(64, 1, 0, bytes(8), [], 0.0)

    active_count = len(active)
    budget_bits = cfg.filter_max_bloom_bytes * 8

    m_bits = _size_for_fp(n, cfg.bloom_fp_rate, budget_bits)
    k_hashes = _k_for(m_bits, n)

    # 2) grow m_bits toward the budget until E[whitelist] is safely under the cap.
    grow_target = cfg.bloom_grow_margin * cap
    if (
        _expected_whitelist(m_bits, k_hashes, n, active_count) > grow_target
        and m_bits < budget_bits
    ):
        lo_b, hi_b = m_bits // 8, budget_bits // 8
        while lo_b < hi_b:
            mid_b = (lo_b + hi_b) // 2
            mid = mid_b * 8
            if _expected_whitelist(mid, _k_for(mid, n), n, active_count) <= grow_target:
                hi_b = mid_b
            else:
                lo_b = mid_b + 1
        m_bits = lo_b * 8
        k_hashes = _k_for(m_bits, n)

    expected = _expected_whitelist(m_bits, k_hashes, n, active_count)

    # 3) hopeless even at max size — fail fast, no doomed builds.
    if expected > cfg.bloom_saturation_skip_factor * cap:
        raise ReaderOversaturated(
            n=n, active=active_count, m_bits=m_bits, k_hashes=k_hashes,
            expected_whitelist=expected, best_whitelist=None, cap=cap,
        )

    # 4) seed search — extended budget only in the marginal band.
    iters = (
        cfg.seed_search_extended_iterations
        if expected > grow_target
        else cfg.seed_search_max_iterations
    )

    best_wl: list[tuple[bytes, int]] | None = None
    for t in range(iters):
        seed = t * k_hashes  # disjoint murmur windows {seed .. seed+k-1}
        if seed + k_hashes - 1 >= (1 << 32):
            # Never ship a seed whose window overflows the reader's uint32 recompute
            # (firmware wraps mod 2^32, backend does not — they would diverge).
            break
        b = build_bloom(candidates, m_bits, k_hashes, seed)
        wl = [
            (kid, ts)
            for (kid, ts) in active
            if bloom_contains(b, kid, m_bits, k_hashes, seed)
        ]
        if best_wl is None or len(wl) < len(best_wl):
            best_wl = wl
        if len(wl) <= cap:
            return BloomSelection(m_bits, k_hashes, seed, b, wl, expected)

    raise ReaderOversaturated(
        n=n, active=active_count, m_bits=m_bits, k_hashes=k_hashes,
        expected_whitelist=expected,
        best_whitelist=(len(best_wl) if best_wl is not None else None),
        cap=cap,
    )


async def handle_generate_filter(
    session: AsyncSession,
    reader_id: bytes,
) -> FilterPackage:
    """Generate a new filter_package for the given reader and commit it."""
    reader = await session.get(Reader, reader_id)
    if reader is None:
        raise LookupError(f"reader not found: {reader_id.hex()}")

    # Next filter version
    max_v = await filter_repo.get_max_filter_version(session, reader_id)
    V = max_v + 1

    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    # Candidates = revoked keys not yet expired
    cand_result = await session.execute(
        select(IssuedKey.key_id, IssuedKey.expires_at)
        .where(
            IssuedKey.reader_id == reader_id,
            IssuedKey.expires_at > now,
            IssuedKey.status.in_(
                ["revoked_by_server", "revoked_by_reader", "revoked_in_bloom"]
            ),
        )
    )
    cand_rows = cand_result.all()
    candidates = [row[0] for row in cand_rows]

    # Active keys for whitelist check
    active_result = await session.execute(
        select(IssuedKey.key_id, IssuedKey.expires_at)
        .where(
            IssuedKey.reader_id == reader_id,
            IssuedKey.status == "active",
            IssuedKey.expires_at > now,
        )
    )
    active_rows = active_result.all()
    active = [(row[0], int(row[1].timestamp())) for row in active_rows]

    # Bloom sizing + seed selection (per-reader §3.4/§8): adaptively grow m_bits to
    # drive E[whitelist] under the cap, with the hash seed only as a cheap
    # tiebreaker on the realized count. ReaderOversaturated means the reader
    # genuinely needs re-key — kept as a terminal task failure (unchanged logic),
    # now a distinct, metered, diagnostic signal instead of an opaque RuntimeError.
    try:
        sel = select_bloom_params(candidates, active, settings)
    except ReaderOversaturated as exc:
        scud_filter_oversaturated_total.inc()
        logger.error("reader %s: %s", reader_id.hex(), exc)
        raise

    m_bits = sel.m_bits
    k_hashes = sel.k_hashes
    hash_seed = sel.hash_seed
    bloom_bytes = sel.bloom_bytes
    whitelist = sel.whitelist
    scud_filter_whitelist_cap_utilization.observe(
        len(whitelist) / settings.whitelist_hard_cap
    )

    # Commit filter_version to candidates that don't have one yet
    if candidates:
        await session.execute(
            update(IssuedKey)
            .where(
                IssuedKey.key_id.in_(candidates),
                IssuedKey.committed_filter_version.is_(None),
            )
            .values(committed_filter_version=V)
        )

    # Compute blacklist_delta
    prev_version_result = await session.execute(
        select(Reader.last_applied_filter_version).where(Reader.reader_id == reader_id)
    )
    prev_version = prev_version_result.scalar_one_or_none() or 0

    bl_delta_result = await session.execute(
        select(IssuedKey.key_id)
        .where(
            IssuedKey.reader_id == reader_id,
            IssuedKey.status == "revoked_by_reader",
            IssuedKey.committed_filter_version.between(prev_version + 1, V),
        )
    )
    bl_delta = [row[0] for row in bl_delta_result.all()][:256]

    # Sort whitelist by key_id
    whitelist.sort(key=lambda x: x[0])

    package_bytes = pack_filter_package(
        reader_id=reader_id,
        filter_version=V,
        generated_at=now_ts,
        m_bits=m_bits,
        k_hashes=k_hashes,
        hash_seed=hash_seed,
        bloom_bytes=bloom_bytes,
        whitelist=whitelist,
        blacklist_delta=bl_delta,
        server_privkey=reader.server_ed25519_privkey,
    )

    # Mark previous filters as not current
    await filter_repo.mark_all_not_current(session, reader_id)

    fp = FilterPackage(
        filter_version=V,
        reader_id=reader_id,
        generated_at=now,
        m_bits=m_bits,
        k_hashes=k_hashes,
        hash_seed=hash_seed,
        filter_bytes_len=len(bloom_bytes),
        whitelist_count=len(whitelist),
        blacklist_delta_count=len(bl_delta),
        package_bytes=package_bytes,
        is_current=True,
    )
    await filter_repo.insert_filter_package(session, fp)

    # Create delivery task
    task = DeliveryTask(reader_id=reader_id, filter_version=V)
    session.add(task)

    # Close superseded open delivery tasks
    await session.execute(
        update(DeliveryTask)
        .where(
            DeliveryTask.reader_id == reader_id,
            DeliveryTask.filter_version < V,
            DeliveryTask.completed_at.is_(None),
        )
        .values(completed_at=now, completion_source="superseded")
    )

    await session.flush()
    return fp
