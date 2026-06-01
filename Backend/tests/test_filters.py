"""Filter package generation — per-reader bloom sizing (§3.4) + size budget.

Closes TESTIN-04 (filter generation was untested). Verifies that the bloom is
sized from THIS reader's own revocation population (not a global constant), that
the §3.4 size budget caps the package so it always fits the reader transport,
that the blacklist-delta carries reader-revoked keys, and that every generated
package is a valid server-signed filter_package.
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from nacl.public import PrivateKey as XPrivateKey
from nacl.signing import SigningKey, VerifyKey

from scud.config import settings
from scud.crypto.signing import DOMAIN_FLT
from scud.db.models import IssuedKey, Reader, ReaderGroup
from scud.domain.filters import ReaderOversaturated, handle_generate_filter

pytestmark = pytest.mark.asyncio


async def _make_reader(db_session) -> Reader:
    group = ReaderGroup(name="filter-test")
    db_session.add(group)
    await db_session.flush()

    server_sk = SigningKey.generate()
    server_x = XPrivateKey.generate()
    reader_sk = SigningKey.generate()
    reader = Reader(
        reader_id=os.urandom(16),
        reader_group_id=group.group_id,
        display_name="Filter reader",
        reader_pubkey=bytes(reader_sk.verify_key),
        server_ed25519_privkey=bytes(server_sk),
        server_ed25519_pubkey=bytes(server_sk.verify_key),
        server_x25519_privkey=bytes(server_x),
        server_x25519_pubkey=bytes(server_x.public_key),
    )
    db_session.add(reader)
    await db_session.flush()
    return reader


def _issued_key(reader_id: bytes, status: str, expires_at: datetime) -> IssuedKey:
    return IssuedKey(
        key_id=os.urandom(16),
        permit_id=uuid.uuid4(),
        reader_id=reader_id,
        phone_pubkey=os.urandom(32),
        device_id=uuid.uuid4(),
        issued_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        serial=1,
        full_key_bytes=bytes(151),
        status=status,
        is_active=(status == "active"),
    )


def _verify_pkg(pkg: bytes, server_pub: bytes) -> bool:
    """The package is valid iff its trailing 64 B verify over DOMAIN_FLT||body."""
    try:
        VerifyKey(server_pub).verify(DOMAIN_FLT + pkg[:-64], pkg[-64:])
        return True
    except Exception:
        return False


async def test_no_revocations_empty_bloom(db_session):
    reader = await _make_reader(db_session)
    fp = await handle_generate_filter(db_session, reader.reader_id)

    assert fp.filter_version == 1
    assert fp.m_bits == 64
    assert fp.filter_bytes_len == 8
    assert fp.whitelist_count == 0
    assert fp.blacklist_delta_count == 0
    assert _verify_pkg(fp.package_bytes, reader.server_ed25519_pubkey)


async def test_per_reader_sizing_scales_with_n(db_session):
    reader = await _make_reader(db_session)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    n = 20
    for _ in range(n):
        db_session.add(_issued_key(reader.reader_id, "revoked_by_server", future))
    for _ in range(3):
        db_session.add(_issued_key(reader.reader_id, "active", future))
    await db_session.flush()

    fp = await handle_generate_filter(db_session, reader.reader_id)

    # m_bits is the §8 optimum for THIS reader's n, byte-aligned — not a constant.
    expected = math.ceil(-n * math.log(settings.bloom_fp_rate) / (math.log(2) ** 2))
    expected = ((expected + 7) // 8) * 8
    assert fp.m_bits == expected
    assert fp.filter_bytes_len == fp.m_bits // 8
    assert fp.k_hashes >= 1
    assert fp.filter_version == 1
    assert _verify_pkg(fp.package_bytes, reader.server_ed25519_pubkey)


async def test_size_budget_caps_m_bits(db_session, monkeypatch):
    # A tiny budget forces the §3.4 cap to bite even for a modest population:
    # the unbounded optimum for n=50 is ~150 B, far over the 8-byte budget.
    monkeypatch.setattr(settings, "filter_max_bloom_bytes", 8)
    reader = await _make_reader(db_session)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    for _ in range(50):
        db_session.add(_issued_key(reader.reader_id, "revoked_by_server", future))
    await db_session.flush()

    fp = await handle_generate_filter(db_session, reader.reader_id)

    assert fp.m_bits == 8 * 8           # capped exactly at the budget
    assert fp.filter_bytes_len == 8
    assert fp.k_hashes >= 1
    assert _verify_pkg(fp.package_bytes, reader.server_ed25519_pubkey)


async def test_oversaturated_reader_raises(db_session, monkeypatch):
    # A 1-byte bloom budget (m_bits clamps to 64) with many revoked keys drives the
    # per-key FP ≈ 0.54; 20 active keys then give E[whitelist] ≈ 11, far over a
    # whitelist cap of 4 — even at max m_bits no seed can fit, so generation must
    # fail fast with ReaderOversaturated (the re-key signal), not loop or hang.
    monkeypatch.setattr(settings, "filter_max_bloom_bytes", 8)
    monkeypatch.setattr(settings, "whitelist_hard_cap", 4)
    reader = await _make_reader(db_session)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    for _ in range(50):
        db_session.add(_issued_key(reader.reader_id, "revoked_by_server", future))
    for _ in range(20):
        db_session.add(_issued_key(reader.reader_id, "active", future))
    await db_session.flush()

    with pytest.raises(ReaderOversaturated):
        await handle_generate_filter(db_session, reader.reader_id)


async def test_blacklist_delta_carries_reader_revoked(db_session):
    reader = await _make_reader(db_session)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.add(_issued_key(reader.reader_id, "revoked_by_reader", future))
    await db_session.flush()

    fp = await handle_generate_filter(db_session, reader.reader_id)

    assert fp.blacklist_delta_count == 1
    assert _verify_pkg(fp.package_bytes, reader.server_ed25519_pubkey)


async def test_version_increments_each_generation(db_session):
    reader = await _make_reader(db_session)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.add(_issued_key(reader.reader_id, "revoked_by_server", future))
    await db_session.flush()

    fp1 = await handle_generate_filter(db_session, reader.reader_id)
    fp2 = await handle_generate_filter(db_session, reader.reader_id)

    assert fp1.filter_version == 1
    assert fp2.filter_version == 2
    assert fp2.is_current is True
