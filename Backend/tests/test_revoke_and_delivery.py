"""Tests for TESTIN-04: two-phase admin revoke + delivery-receipt worker handler.

Covers:
  (a) Two-phase permit revoke flow:
      - active -> revoking (revoke_initiated_at set, revoked_at NULL)
      - revoking -> revoked once keys transition to is_active=False
      - Short-circuit: immediate finalize when no active keys exist
  (b) process_delivery_receipt:
      - revoked_by_server keys with committed_filter_version <= applied version
        transition to revoked_in_bloom (is_active=False)
      - finalize_pending_revokes is triggered: pending permit gets revoked_at
      - DeliveryTask is completed
      - Reader last_applied_filter_version / last_contact_at updated
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scud.crypto.signing import DOMAIN_RCP, sign_detached
from scud.db.models import (
    DeliveryTask,
    FilterPackage,
    IssuedKey,
    Permit,
    Reader,
    ReaderGroup,
    UserDevice,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _make_reader(db: AsyncSession, reader_sk: SigningKey) -> Reader:
    """Persist a Reader with the given signing key as its public identity."""
    from nacl.public import PrivateKey as XPrivateKey

    group = ReaderGroup(name="grp-" + os.urandom(4).hex())
    db.add(group)
    await db.flush()

    server_sk = SigningKey.generate()
    server_x = XPrivateKey.generate()
    reader = Reader(
        reader_id=os.urandom(16),
        reader_group_id=group.group_id,
        display_name="test-reader",
        reader_pubkey=bytes(reader_sk.verify_key),
        server_ed25519_privkey=bytes(server_sk),
        server_ed25519_pubkey=bytes(server_sk.verify_key),
        server_x25519_privkey=bytes(server_x),
        server_x25519_pubkey=bytes(server_x.public_key),
    )
    db.add(reader)
    await db.flush()
    return reader


async def _make_permit(db: AsyncSession, user_id: int, reader_id: bytes) -> Permit:
    permit = Permit(
        user_id=user_id,
        reader_id=reader_id,
        display_name="test-permit",
        valid_from=datetime.now(timezone.utc),
        valid_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
        n_parallel=3,
        max_token_ttl_seconds=86400,
    )
    db.add(permit)
    await db.flush()
    await db.refresh(permit)
    return permit


async def _make_device(db: AsyncSession, user_id: int) -> UserDevice:
    device = UserDevice(
        user_id=user_id,
        phone_pubkey=os.urandom(32),
        device_label="test-phone",
    )
    db.add(device)
    await db.flush()
    await db.refresh(device)
    return device


async def _make_issued_key(
    db: AsyncSession,
    permit: Permit,
    device: UserDevice,
    *,
    status: str = "active",
    is_active: bool = True,
    committed_filter_version: int | None = None,
) -> IssuedKey:
    key = IssuedKey(
        key_id=os.urandom(16),
        permit_id=permit.permit_id,
        reader_id=permit.reader_id,
        phone_pubkey=device.phone_pubkey,
        device_id=device.device_id,
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        serial=1,
        status=status,
        is_active=is_active,
        committed_filter_version=committed_filter_version,
        full_key_bytes=b"\x00" * 151,
    )
    db.add(key)
    await db.flush()
    return key


async def _fetch_permit(db: AsyncSession, permit_id: uuid.UUID) -> Permit:
    """Re-fetch permit from DB bypassing ORM identity-map cache."""
    result = await db.execute(select(Permit).where(Permit.permit_id == permit_id))
    return result.scalar_one()


async def _fetch_key(db: AsyncSession, key_id: bytes) -> IssuedKey:
    """Re-fetch issued key from DB bypassing ORM identity-map cache."""
    result = await db.execute(select(IssuedKey).where(IssuedKey.key_id == key_id))
    return result.scalar_one()


async def _fetch_reader(db: AsyncSession, reader_id: bytes) -> Reader:
    """Re-fetch reader from DB bypassing ORM identity-map cache."""
    result = await db.execute(select(Reader).where(Reader.reader_id == reader_id))
    return result.scalar_one()


def _build_delivery_receipt(
    reader_id: bytes,
    reader_sk: SigningKey,
    filter_version: int,
    applied_at: int | None = None,
    courier_id: bytes | None = None,
) -> bytes:
    """Build a valid 112-byte delivery_receipt blob."""
    if applied_at is None:
        applied_at = int(datetime.now(timezone.utc).timestamp())
    if courier_id is None:
        courier_id = os.urandom(16)
    body = (
        reader_id
        + filter_version.to_bytes(8, "little")
        + applied_at.to_bytes(8, "little")
        + courier_id
    )
    assert len(body) == 48
    sig = sign_detached(bytes(reader_sk), DOMAIN_RCP, body)
    blob = body + sig
    assert len(blob) == 112
    return blob


async def _make_filter_package(
    db: AsyncSession,
    reader_id: bytes,
    filter_version: int,
) -> FilterPackage:
    fp = FilterPackage(
        filter_version=filter_version,
        reader_id=reader_id,
        m_bits=8192,
        k_hashes=7,
        hash_seed=42,
        filter_bytes_len=1024,
        whitelist_count=0,
        blacklist_delta_count=0,
        package_bytes=b"\x00" * 1024,
        is_current=True,
    )
    db.add(fp)
    await db.flush()
    return fp


# ===========================================================================
# (a) Two-phase permit revoke — domain/permits.py
# ===========================================================================


@pytest.mark.asyncio
async def test_admin_revoke_no_active_keys_finalizes_immediately(db_session, test_user):
    """With no active keys the permit should be finalized immediately (revoked_at set)."""
    from scud.domain.permits import admin_revoke_permit

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)

    # No active keys at all — short-circuit path
    reader_ids = await admin_revoke_permit(db_session, permit.permit_id)
    await db_session.flush()

    # Re-fetch to bypass SQLAlchemy identity-map cache
    refreshed = await _fetch_permit(db_session, permit.permit_id)
    assert refreshed.revoked_at is not None, "permit must be immediately finalized"
    assert refreshed.revoke_initiated_at is not None
    # reader_ids list may be empty (no keys to mark)
    assert isinstance(reader_ids, list)


@pytest.mark.asyncio
async def test_admin_revoke_with_active_keys_enters_revoking_state(db_session, test_user):
    """With active keys the permit enters 'revoking' (revoke_initiated_at set, revoked_at NULL)."""
    from scud.domain.permits import admin_revoke_permit

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)
    device = await _make_device(db_session, test_user["user_id"])
    key = await _make_issued_key(db_session, permit, device, status="active", is_active=True)

    reader_ids = await admin_revoke_permit(db_session, permit.permit_id)
    await db_session.flush()

    refreshed = await _fetch_permit(db_session, permit.permit_id)
    assert refreshed.revoke_initiated_at is not None, "Phase 1 must stamp revoke_initiated_at"
    assert refreshed.revoked_at is None, "Phase 1 must NOT finalize yet"
    assert len(reader_ids) > 0, "affected reader_ids must be returned"

    # The key must now be revoked_by_server (is_active=True until reader applies bloom)
    key_row = await _fetch_key(db_session, key.key_id)
    assert key_row.status == "revoked_by_server"
    assert key_row.is_active is True  # still active until reader applies bloom


@pytest.mark.asyncio
async def test_finalize_pending_revokes_completes_when_all_keys_inactive(db_session, test_user):
    """After keys transition to is_active=False, finalize_pending_revokes sets revoked_at."""
    from sqlalchemy import update
    from scud.domain.permits import admin_revoke_permit, finalize_pending_revokes

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)
    device = await _make_device(db_session, test_user["user_id"])
    key = await _make_issued_key(db_session, permit, device, status="active", is_active=True)

    # Phase 1
    await admin_revoke_permit(db_session, permit.permit_id)
    await db_session.flush()

    # Simulate reader applying the bloom: key transitions to revoked_in_bloom (is_active=False)
    await db_session.execute(
        update(IssuedKey)
        .where(IssuedKey.key_id == key.key_id)
        .values(status="revoked_in_bloom", is_active=False)
    )
    await db_session.flush()

    # Phase 2: finalize
    finalized = await finalize_pending_revokes(db_session, reader.reader_id)
    await db_session.flush()

    assert finalized == 1
    refreshed = await _fetch_permit(db_session, permit.permit_id)
    assert refreshed.revoked_at is not None, "Phase 2 must set revoked_at"


@pytest.mark.asyncio
async def test_finalize_pending_revokes_skips_permit_with_remaining_active_keys(db_session, test_user):
    """finalize_pending_revokes must NOT finalize if some keys are still is_active=True."""
    from scud.domain.permits import admin_revoke_permit, finalize_pending_revokes

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)
    device = await _make_device(db_session, test_user["user_id"])

    # Two active keys
    await _make_issued_key(db_session, permit, device, status="active", is_active=True)
    await _make_issued_key(db_session, permit, device, status="active", is_active=True)

    # Phase 1 revoke: marks both revoked_by_server (is_active=True)
    await admin_revoke_permit(db_session, permit.permit_id)
    await db_session.flush()

    # Neither key has transitioned to bloom yet — finalize must return 0
    finalized = await finalize_pending_revokes(db_session, reader.reader_id)
    await db_session.flush()

    assert finalized == 0
    refreshed = await _fetch_permit(db_session, permit.permit_id)
    assert refreshed.revoked_at is None, "must remain in revoking state"


@pytest.mark.asyncio
async def test_admin_revoke_nonexistent_permit_raises(db_session):
    """admin_revoke_permit must raise LookupError for unknown permit_id."""
    from scud.domain.permits import admin_revoke_permit

    with pytest.raises(LookupError):
        await admin_revoke_permit(db_session, uuid.uuid4())


# ===========================================================================
# (b) process_delivery_receipt — domain/reports.py
# ===========================================================================


@pytest.mark.asyncio
async def test_delivery_receipt_promotes_revoked_by_server_to_bloom(db_session, test_user):
    """Keys with status=revoked_by_server and committed_filter_version <= applied version
    must transition to revoked_in_bloom with is_active=False."""
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)
    device = await _make_device(db_session, test_user["user_id"])

    filter_version = 5
    await _make_filter_package(db_session, reader.reader_id, filter_version)

    # Key committed at version 3 — should be promoted when version 5 is applied
    key = await _make_issued_key(
        db_session, permit, device,
        status="revoked_by_server",
        is_active=True,
        committed_filter_version=3,
    )

    receipt_blob = _build_delivery_receipt(
        reader.reader_id, reader_sk, filter_version
    )
    await process_delivery_receipt(db_session, receipt_blob, reader)

    refreshed = await _fetch_key(db_session, key.key_id)
    assert refreshed.status == "revoked_in_bloom"
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_delivery_receipt_does_not_promote_newer_committed_version(db_session, test_user):
    """Keys committed to a future filter version must NOT be promoted prematurely."""
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)
    device = await _make_device(db_session, test_user["user_id"])

    filter_version = 5
    await _make_filter_package(db_session, reader.reader_id, filter_version)

    # Key committed at version 10 — future, should NOT be promoted
    key = await _make_issued_key(
        db_session, permit, device,
        status="revoked_by_server",
        is_active=True,
        committed_filter_version=10,
    )

    receipt_blob = _build_delivery_receipt(
        reader.reader_id, reader_sk, filter_version
    )
    await process_delivery_receipt(db_session, receipt_blob, reader)

    refreshed = await _fetch_key(db_session, key.key_id)
    assert refreshed.status == "revoked_by_server", "must not promote future version"
    assert refreshed.is_active is True


@pytest.mark.asyncio
async def test_delivery_receipt_updates_reader_state(db_session, test_user):
    """last_applied_filter_version and last_contact_at must be updated on reader."""
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)

    filter_version = 7
    await _make_filter_package(db_session, reader.reader_id, filter_version)

    receipt_blob = _build_delivery_receipt(
        reader.reader_id, reader_sk, filter_version
    )
    await process_delivery_receipt(db_session, receipt_blob, reader)

    # Re-fetch to bypass ORM identity-map
    refreshed = await _fetch_reader(db_session, reader.reader_id)
    assert refreshed.last_applied_filter_version == filter_version
    assert refreshed.last_contact_at is not None


@pytest.mark.asyncio
async def test_delivery_receipt_completes_delivery_task(db_session, test_user):
    """An open DeliveryTask for the applied filter version must be marked completed."""
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)

    filter_version = 3
    await _make_filter_package(db_session, reader.reader_id, filter_version)

    # Create an open delivery task for that version
    task = DeliveryTask(
        reader_id=reader.reader_id,
        filter_version=filter_version,
    )
    db_session.add(task)
    await db_session.flush()

    receipt_blob = _build_delivery_receipt(
        reader.reader_id, reader_sk, filter_version
    )
    await process_delivery_receipt(db_session, receipt_blob, reader)

    # Re-fetch the task
    result = await db_session.execute(
        select(DeliveryTask).where(DeliveryTask.task_id == task.task_id)
    )
    refreshed_task = result.scalar_one()
    assert refreshed_task.completed_at is not None
    assert refreshed_task.completion_source == "receipt"


@pytest.mark.asyncio
async def test_delivery_receipt_triggers_phase2_finalize(db_session, test_user):
    """process_delivery_receipt must call finalize_pending_revokes so a permit
    in 'revoking' state with all keys now revoked_in_bloom gets revoked_at set."""
    from sqlalchemy import update
    from scud.domain.permits import admin_revoke_permit
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit(db_session, test_user["user_id"], reader.reader_id)
    device = await _make_device(db_session, test_user["user_id"])

    filter_version = 4
    await _make_filter_package(db_session, reader.reader_id, filter_version)

    # Issue a key then admin-revoke the permit (enters 'revoking')
    key = await _make_issued_key(db_session, permit, device, status="active", is_active=True)
    await admin_revoke_permit(db_session, permit.permit_id)
    await db_session.flush()

    # Verify permit is in revoking state
    mid_state = await _fetch_permit(db_session, permit.permit_id)
    assert mid_state.revoked_at is None
    assert mid_state.revoke_initiated_at is not None

    # Simulate worker: key gets committed_filter_version set
    await db_session.execute(
        update(IssuedKey)
        .where(IssuedKey.key_id == key.key_id)
        .values(committed_filter_version=filter_version)
    )
    await db_session.flush()

    # Deliver the receipt — should promote key to revoked_in_bloom AND finalize permit
    receipt_blob = _build_delivery_receipt(
        reader.reader_id, reader_sk, filter_version
    )
    await process_delivery_receipt(db_session, receipt_blob, reader)

    final_permit = await _fetch_permit(db_session, permit.permit_id)
    assert final_permit.revoked_at is not None, (
        "Phase 2: permit must be finalized after delivery_receipt promotes all keys to bloom"
    )


@pytest.mark.asyncio
async def test_delivery_receipt_rejects_wrong_reader_id(db_session, test_user):
    """delivery_receipt with mismatched reader_id must raise ValueError."""
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)

    # Build receipt claiming a different reader_id
    wrong_reader_id = os.urandom(16)
    receipt_blob = _build_delivery_receipt(wrong_reader_id, reader_sk, filter_version=1)

    with pytest.raises(ValueError, match="reader_id mismatch"):
        await process_delivery_receipt(db_session, receipt_blob, reader)


@pytest.mark.asyncio
async def test_delivery_receipt_rejects_bad_signature(db_session, test_user):
    """delivery_receipt signed by a wrong key must raise ValueError."""
    from scud.domain.reports import process_delivery_receipt

    reader_sk = SigningKey.generate()
    wrong_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)

    # Sign with wrong key
    receipt_blob = _build_delivery_receipt(reader.reader_id, wrong_sk, filter_version=1)

    with pytest.raises(ValueError, match="signature"):
        await process_delivery_receipt(db_session, receipt_blob, reader)


# ===========================================================================
# Phase 3 — enroll with config (TESTIN coverage for finding 5)
# ===========================================================================


@pytest.mark.asyncio
async def test_enroll_stores_config(client, admin_api_key):
    """Enroll endpoint must persist the optional config dict on the Reader row."""
    import base64

    reader_id = os.urandom(16)
    reader_sk = SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(reader_sk.verify_key)).decode()

    # Create reader group first
    group_resp = await client.post(
        "/api/v1/admin/reader-groups",
        json={"name": "cfg-test-group"},
        headers={"X-Api-Key": admin_api_key},
    )
    assert group_resp.status_code == 201
    group_id = group_resp.json()["group_id"]

    config_payload = {"ble_tx_power": -12, "mode": "nfc_only", "timeout_ms": 5000}

    resp = await client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id.hex(),
            "reader_pubkey": pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "Config Reader",
            "config": config_payload,
        },
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 200

    # Retrieve the reader and verify config was persisted
    get_resp = await client.get(
        f"/api/v1/admin/readers/{reader_id.hex()}",
        headers={"X-Api-Key": admin_api_key},
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["config"] == config_payload


@pytest.mark.asyncio
async def test_enroll_without_config_is_backward_compatible(client, admin_api_key):
    """Enroll without config field must succeed and reader.config must be None."""
    import base64

    reader_id = os.urandom(16)
    reader_sk = SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(reader_sk.verify_key)).decode()

    group_resp = await client.post(
        "/api/v1/admin/reader-groups",
        json={"name": "noconfig-group"},
        headers={"X-Api-Key": admin_api_key},
    )
    assert group_resp.status_code == 201
    group_id = group_resp.json()["group_id"]

    resp = await client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id.hex(),
            "reader_pubkey": pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "No-Config Reader",
            # config intentionally omitted
        },
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 200

    get_resp = await client.get(
        f"/api/v1/admin/readers/{reader_id.hex()}",
        headers={"X-Api-Key": admin_api_key},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["config"] is None


# ===========================================================================
# BE-SEC-02 — integration key blocked from admin endpoints
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_key_blocked_from_admin_endpoint(client, db_session):
    """An API key with kind='integration' must receive HTTP 403 on admin endpoints."""
    import hashlib
    import secrets
    from scud.db.models import ApiKey

    plaintext = "sk_integration_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    integration_key = ApiKey(
        key_hash=key_hash,
        key_prefix=plaintext[:8],
        kind="integration",
        name="test-integration",
    )
    db_session.add(integration_key)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/admin/readers",
        headers={"X-Api-Key": plaintext},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_key_required"


@pytest.mark.asyncio
async def test_admin_key_allowed_on_admin_endpoint(client, admin_api_key):
    """A key with kind='admin' must be allowed through to admin endpoints."""
    resp = await client.get(
        "/api/v1/admin/readers",
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 200
