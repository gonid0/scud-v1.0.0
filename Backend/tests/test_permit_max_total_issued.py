"""Tests for the permit unexpired-key quota (permits.max_total_issued).

The column name is historical; the gate no longer counts *all ever-issued*
keys but only those still inside their validity window — issued_keys rows with
expires_at > now, regardless of status. This caps how many keys of one permit
can sit in the revocation bloom filter at once (bloom-bloat protection): an
expired key is dropped by the reader on expiry, never needs a bloom entry, and
therefore must free its slot in the quota.

Covers:
  - issue_key enforces the quota (unexpired_cap_exceeded once reached)
  - a revoked-but-still-unexpired key STILL occupies the quota (status-agnostic
    while expires_at > now), unlike count_active_keys which filters on is_active
  - an EXPIRED key frees its slot — issuance resumes even at a full historical
    count, which is the whole point of the new semantics
  - null cap = unlimited issuance
  - admin API rejects a zero/negative cap (Field ge=1)
  - admin PATCH can clear the cap back to NULL (exclude_none would otherwise
    drop an explicit null — see permits.py patch_permit special-casing)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from nacl.public import PrivateKey as XPrivateKey
from nacl.signing import SigningKey
from sqlalchemy import update

from scud.db.models import IssuedKey, Permit, Reader, ReaderGroup, UserDevice


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_revoke_and_delivery.py)
# ---------------------------------------------------------------------------

async def _make_reader(db) -> Reader:
    group = ReaderGroup(name="grp-" + os.urandom(4).hex())
    db.add(group)
    await db.flush()
    server_sk = SigningKey.generate()
    server_x = XPrivateKey.generate()
    reader = Reader(
        reader_id=os.urandom(16),
        reader_group_id=group.group_id,
        display_name="cap-reader",
        reader_pubkey=bytes(SigningKey.generate().verify_key),
        server_ed25519_privkey=bytes(server_sk),
        server_ed25519_pubkey=bytes(server_sk.verify_key),
        server_x25519_privkey=bytes(server_x),
        server_x25519_pubkey=bytes(server_x.public_key),
    )
    db.add(reader)
    await db.flush()
    return reader


async def _make_permit(
    db, user_id: int, reader_id: bytes, *, max_total_issued, n_parallel: int = 5
) -> Permit:
    permit = Permit(
        user_id=user_id,
        reader_id=reader_id,
        display_name="cap-permit",
        valid_from=datetime.now(timezone.utc),
        valid_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
        n_parallel=n_parallel,
        max_token_ttl_seconds=86400,
        max_total_issued=max_total_issued,
    )
    db.add(permit)
    await db.flush()
    await db.refresh(permit)
    return permit


async def _make_device(db, user_id: int) -> UserDevice:
    device = UserDevice(
        user_id=user_id,
        phone_pubkey=os.urandom(32),
        device_label="cap-phone",
    )
    db.add(device)
    await db.flush()
    await db.refresh(device)
    return device


# ---------------------------------------------------------------------------
# Domain: issue_key enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_key_blocked_at_unexpired_cap(db_session, test_user):
    from scud.domain.keys import issue_key

    reader = await _make_reader(db_session)
    permit = await _make_permit(
        db_session, test_user["user_id"], reader.reader_id,
        max_total_issued=2, n_parallel=5,
    )
    await _make_device(db_session, test_user["user_id"])

    for _ in range(2):
        await issue_key(
            db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
            validity_seconds=3600, request_grant=False,
        )
        await db_session.flush()

    with pytest.raises(ValueError, match="unexpired_cap_exceeded"):
        await issue_key(
            db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
            validity_seconds=3600, request_grant=False,
        )


@pytest.mark.asyncio
async def test_revoked_but_unexpired_key_still_counts(db_session, test_user):
    """A revoked (revoked_in_bloom, is_active=False) key whose expires_at is still
    in the future MUST keep occupying the quota: it is still in the bloom filter,
    so the gate must count it. Proves the gate filters on expires_at, not status."""
    from scud.domain.keys import issue_key

    reader = await _make_reader(db_session)
    permit = await _make_permit(
        db_session, test_user["user_id"], reader.reader_id,
        max_total_issued=1, n_parallel=5,
    )
    await _make_device(db_session, test_user["user_id"])

    key, _ = await issue_key(
        db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
        validity_seconds=3600, request_grant=False,
    )
    await db_session.flush()

    # Archive it completely (no longer "active" by any counter) but leave its
    # expires_at in the future — it is still a live bloom entry.
    await db_session.execute(
        update(IssuedKey)
        .where(IssuedKey.key_id == key.key_id)
        .values(status="revoked_in_bloom", is_active=False)
    )
    await db_session.flush()

    # Quota of 1 is still consumed despite zero *active* keys.
    with pytest.raises(ValueError, match="unexpired_cap_exceeded"):
        await issue_key(
            db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
            validity_seconds=3600, request_grant=False,
        )


@pytest.mark.asyncio
async def test_expired_key_frees_its_quota_slot(db_session, test_user):
    """The core of the new semantics: a key past its expires_at no longer counts.
    The reader drops it on expiry, it carries no bloom entry, so its quota slot is
    released and issuance resumes even though the historical total still grows."""
    from scud.domain.keys import issue_key

    reader = await _make_reader(db_session)
    permit = await _make_permit(
        db_session, test_user["user_id"], reader.reader_id,
        max_total_issued=1, n_parallel=5,
    )
    await _make_device(db_session, test_user["user_id"])

    key, _ = await issue_key(
        db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
        validity_seconds=3600, request_grant=False,
    )
    await db_session.flush()

    # Push the key out of its validity window (leave its status untouched to prove
    # expiry alone, not status, frees the slot).
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.execute(
        update(IssuedKey)
        .where(IssuedKey.key_id == key.key_id)
        .values(expires_at=past)
    )
    await db_session.flush()

    # Slot freed: a fresh issue under the same cap=1 must succeed.
    key2, _ = await issue_key(
        db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
        validity_seconds=3600, request_grant=False,
    )
    await db_session.flush()
    assert key2 is not None
    assert key2.key_id != key.key_id


@pytest.mark.asyncio
async def test_null_cap_allows_unlimited_issuance(db_session, test_user):
    from scud.domain.keys import issue_key

    reader = await _make_reader(db_session)
    permit = await _make_permit(
        db_session, test_user["user_id"], reader.reader_id,
        max_total_issued=None, n_parallel=5,
    )
    await _make_device(db_session, test_user["user_id"])

    for _ in range(3):
        key, _ = await issue_key(
            db_session, permit_id=permit.permit_id, user_id=test_user["user_id"],
            validity_seconds=3600, request_grant=False,
        )
        await db_session.flush()
        assert key is not None


# ---------------------------------------------------------------------------
# Admin API: validation + clear-to-null
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_permit_rejects_zero_cap(client, admin_api_key, test_user):
    """max_total_issued=0 would brick issuance (count >= 0 always true); Field ge=1
    must reject it at the request boundary (422) before any DB work."""
    resp = await client.post(
        "/api/v1/admin/permits",
        json={
            "user_id": test_user["user_id"],
            "reader_id": "00" * 16,
            "display_name": "zero-cap",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "n_parallel": 1,
            "max_token_ttl_seconds": 3600,
            "max_total_issued": 0,
        },
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_patch_can_clear_cap_to_null(client, admin_api_key, db_session, test_user):
    reader = await _make_reader(db_session)
    permit = await _make_permit(
        db_session, test_user["user_id"], reader.reader_id,
        max_total_issued=5, n_parallel=3,
    )
    await db_session.commit()
    pid = str(permit.permit_id)

    get1 = await client.get(
        f"/api/v1/admin/permits/{pid}", headers={"X-Api-Key": admin_api_key}
    )
    assert get1.status_code == 200
    assert get1.json()["max_total_issued"] == 5

    patch = await client.patch(
        f"/api/v1/admin/permits/{pid}",
        json={"max_total_issued": None},
        headers={"X-Api-Key": admin_api_key},
    )
    assert patch.status_code == 200

    get2 = await client.get(
        f"/api/v1/admin/permits/{pid}", headers={"X-Api-Key": admin_api_key}
    )
    assert get2.status_code == 200
    assert get2.json()["max_total_issued"] is None
