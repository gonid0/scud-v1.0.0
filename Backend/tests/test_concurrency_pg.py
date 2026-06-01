"""TESTIN-03: PostgreSQL-only concurrency tests.

The default test suite runs on aiosqlite, which cannot exercise the
Postgres-specific concurrency primitives the backend relies on:

  * ``SELECT ... FOR UPDATE`` row-locks (permit gate in ``issue_key``)
  * ``FOR UPDATE SKIP LOCKED`` (single-claim task queue in ``fetch_next_task``)

Both tests below open *independent* sessions (each on its own DB connection)
and run them concurrently so the locking semantics are genuinely tested.

They are marked ``@pytest.mark.postgres`` and AUTO-SKIP when the engine is not
PostgreSQL (see ``conftest.pytest_runtest_setup``), so the normal sqlite suite
stays green. CI runs them via the ``backend-postgres`` job which sets
``SCUD_TEST_DATABASE_URL`` to a real Postgres and runs ``alembic upgrade head``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from nacl.public import PrivateKey as XPrivateKey
from nacl.signing import SigningKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scud.db.models import (
    BackgroundTask,
    IssuedKey,
    Permit,
    Reader,
    ReaderGroup,
    User,
    UserDevice,
)

pytestmark = pytest.mark.postgres


# ---------------------------------------------------------------------------
# Seed helpers — committed in a setup session before the concurrent race
# ---------------------------------------------------------------------------

async def _seed_user(db: AsyncSession) -> User:
    from scud.domain.auth import hash_password

    user = User(
        login=f"cc_user_{secrets.token_hex(4)}",
        password_hash=hash_password("pw"),
        display_name="Concurrency User",
        user_group_id=uuid.uuid4(),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _seed_reader(db: AsyncSession) -> Reader:
    group = ReaderGroup(name="cc-grp-" + secrets.token_hex(4))
    db.add(group)
    await db.flush()

    reader_sk = SigningKey.generate()
    server_sk = SigningKey.generate()
    server_x = XPrivateKey.generate()
    reader = Reader(
        reader_id=os.urandom(16),
        reader_group_id=group.group_id,
        display_name="cc-reader",
        reader_pubkey=bytes(reader_sk.verify_key),
        server_ed25519_privkey=bytes(server_sk),
        server_ed25519_pubkey=bytes(server_sk.verify_key),
        server_x25519_privkey=bytes(server_x),
        server_x25519_pubkey=bytes(server_x.public_key),
    )
    db.add(reader)
    await db.flush()
    return reader


async def _seed_device(db: AsyncSession, user_id: int) -> UserDevice:
    device = UserDevice(
        user_id=user_id,
        phone_pubkey=os.urandom(32),
        device_label="cc-phone",
        is_active=True,
    )
    db.add(device)
    await db.flush()
    await db.refresh(device)
    return device


async def _seed_permit(
    db: AsyncSession, user_id: int, reader_id: bytes, *, n_parallel: int
) -> Permit:
    now = datetime.now(timezone.utc)
    permit = Permit(
        user_id=user_id,
        reader_id=reader_id,
        display_name="cc-permit",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(days=30),
        n_parallel=n_parallel,
        max_token_ttl_seconds=3600,
    )
    db.add(permit)
    await db.flush()
    await db.refresh(permit)
    return permit


# ---------------------------------------------------------------------------
# Test 1 — n_parallel=1 permit: two concurrent issuances, exactly one wins.
# Exercises permits.get_permit_for_update() (SELECT ... FOR UPDATE).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_key_request_respects_n_parallel_one(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """With n_parallel=1, two concurrent issue_key calls must yield exactly one
    success and one ``n_parallel_exceeded``.

    On Postgres the FOR UPDATE lock on the permit serializes the two
    transactions: the loser only counts active keys after the winner commits,
    sees the count == n_parallel and raises. On sqlite there is no real row
    lock so both could succeed — which is exactly why this test is PG-only.
    """
    from scud.domain.keys import issue_key

    # --- seed (committed) ---
    async with sessionmaker() as setup:
        user = await _seed_user(setup)
        reader = await _seed_reader(setup)
        await _seed_device(setup, user.user_id)
        permit = await _seed_permit(
            setup, user.user_id, reader.reader_id, n_parallel=1
        )
        await setup.commit()
        user_id = user.user_id
        permit_id = permit.permit_id

    results: list[object] = [None, None]

    async def worker(idx: int) -> None:
        async with sessionmaker() as session:
            try:
                key, _grant = await issue_key(
                    session,
                    permit_id=permit_id,
                    user_id=user_id,
                    validity_seconds=600,
                    request_grant=False,
                )
                await session.commit()
                results[idx] = ("ok", key.key_id)
            except ValueError as exc:
                await session.rollback()
                results[idx] = ("err", str(exc))

    await asyncio.gather(worker(0), worker(1))

    statuses = [r[0] for r in results]  # type: ignore[index]
    assert statuses.count("ok") == 1, f"exactly one issuance must win: {results}"
    assert statuses.count("err") == 1, f"the other must be rejected: {results}"

    loser = next(r for r in results if r[0] == "err")  # type: ignore[index]
    assert "n_parallel_exceeded" in loser[1], loser  # type: ignore[index]

    # And the DB must hold exactly one active key for the permit.
    async with sessionmaker() as check:
        count = (
            await check.execute(
                select(IssuedKey).where(
                    IssuedKey.permit_id == permit_id,
                    IssuedKey.is_active.is_(True),
                )
            )
        ).scalars().all()
        assert len(count) == 1, f"DB must hold exactly one active key, got {len(count)}"


# ---------------------------------------------------------------------------
# Test 2 — FOR UPDATE SKIP LOCKED: a single pending task, two racing workers,
# only one claims it. Exercises tasks.fetch_next_task().
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_task_claim_skip_locked_single_winner(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Two workers race to claim one pending BackgroundTask via SKIP LOCKED.

    Exactly one must get the task; the other must get None (it skips the locked
    row rather than blocking on it). The claimed task must be 'processing' and
    owned by the winning worker.
    """
    from scud.db.repositories.tasks import enqueue_task, fetch_next_task

    async with sessionmaker() as setup:
        task = await enqueue_task(
            setup, "generate_filter", {"reader_id": os.urandom(16).hex()}
        )
        await setup.commit()
        task_id = task.task_id

    claims: list[uuid.UUID | None] = [None, None]
    # A barrier ensures both workers hold their transactions open at the same
    # time, so the SKIP LOCKED contention is real rather than sequential.
    barrier = asyncio.Barrier(2)

    async def worker(idx: int, worker_id: str) -> None:
        async with sessionmaker() as session:
            async with session.begin():
                await barrier.wait()
                claimed = await fetch_next_task(session, worker_id)
                claims[idx] = claimed.task_id if claimed is not None else None

    await asyncio.gather(
        worker(0, "worker-A"),
        worker(1, "worker-B"),
    )

    got = [c for c in claims if c is not None]
    missed = [c for c in claims if c is None]
    assert len(got) == 1, f"exactly one worker must claim the task: {claims}"
    assert len(missed) == 1, f"the other worker must skip the locked row: {claims}"
    assert got[0] == task_id

    # The task is now 'processing' and assigned to exactly one worker.
    async with sessionmaker() as check:
        refreshed = await check.get(BackgroundTask, task_id)
        assert refreshed is not None
        assert refreshed.status == "processing"
        assert refreshed.worker_id in {"worker-A", "worker-B"}
