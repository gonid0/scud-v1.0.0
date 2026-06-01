"""End-to-end workflow tests (§10.2).

Note: These tests use SQLite in-memory via conftest, which doesn't support
FOR UPDATE SKIP LOCKED or PostgreSQL-specific functions.
The filter generation worker handler is tested in isolation.
"""

from __future__ import annotations

import base64
import math
import os
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def enrolled_reader(db_session: AsyncSession, admin_api_key: str, client: AsyncClient) -> dict:
    """Enroll a reader and return its details."""
    reader_id = os.urandom(16)
    reader_privkey = SigningKey.generate()
    reader_pubkey_b64 = base64.b64encode(bytes(reader_privkey.verify_key)).decode()

    resp = await client.post(
        "/api/v1/admin/reader-groups",
        json={"name": "TestGroup"},
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 201
    group_id = resp.json()["group_id"]

    resp = await client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id.hex(),
            "reader_pubkey": reader_pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "Test Reader",
        },
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 200
    data = resp.json()

    return {
        "reader_id": reader_id,
        "reader_id_hex": reader_id.hex(),
        "group_id": group_id,
        "server_ed25519_pubkey": base64.b64decode(data["server_ed25519_pubkey"]),
        "server_x25519_pubkey": base64.b64decode(data["server_x25519_pubkey"]),
        "reader_privkey": reader_privkey,
    }


@pytest_asyncio.fixture
async def user_with_session(client: AsyncClient, admin_api_key: str, enrolled_reader: dict) -> dict:
    """Create user, login, register device, return session data."""
    import secrets as _secrets
    suffix = _secrets.token_hex(4)
    login = f"e2euser_{suffix}"
    password = "e2epassword123"

    # Create user via admin
    resp = await client.post(
        "/api/v1/admin/users",
        json={
            "login": login,
            "password": password,
            "display_name": "E2E User",
            "user_group_id": enrolled_reader["group_id"],
        },
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 201
    user_id = resp.json()["user_id"]

    # Login
    login_resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": login, "password": password},
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()
    token = tokens["session_token"]

    # Register device
    phone_privkey = SigningKey.generate()
    phone_pubkey = bytes(phone_privkey.verify_key)
    reg_resp = await client.post(
        "/api/v1/app/auth/register-device",
        json={
            "phone_pubkey": base64.b64encode(phone_pubkey).decode(),
            "device_label": "E2E Phone",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reg_resp.status_code == 200
    device_id = reg_resp.json()["device_id"]

    return {
        "user_id": user_id,
        "token": token,
        "device_id": device_id,
        "phone_privkey": phone_privkey,
        "phone_pubkey": phone_pubkey,
    }


@pytest_asyncio.fixture
async def active_permit(
    client: AsyncClient,
    admin_api_key: str,
    enrolled_reader: dict,
    user_with_session: dict,
) -> dict:
    """Create a permit for the test user."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    resp = await client.post(
        "/api/v1/admin/permits",
        json={
            "user_id": user_with_session["user_id"],
            "reader_id": enrolled_reader["reader_id_hex"],
            "display_name": "Test Permit",
            "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_until": (now + timedelta(days=30)).isoformat(),
            "n_parallel": 2,
            "max_token_ttl_seconds": 3600,
        },
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 201
    return {"permit_id": resp.json()["permit_id"]}


@pytest.mark.asyncio
async def test_request_key(
    client: AsyncClient,
    user_with_session: dict,
    active_permit: dict,
) -> None:
    resp = await client.post(
        "/api/v1/app/keys/request",
        json={
            "permit_id": active_permit["permit_id"],
            "validity_seconds": 3600,
            "request_grant": True,
        },
        headers={"Authorization": f"Bearer {user_with_session['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "issued_key" in data
    key_bytes = base64.b64decode(data["issued_key"]["full_key_bytes"])
    assert len(key_bytes) == 151, f"issued_key must be 151 B, got {len(key_bytes)}"

    # Grant should be returned on first request
    assert data["time_grant"] is not None
    grant_bytes = base64.b64decode(data["time_grant"]["full_grant_bytes"])
    assert len(grant_bytes) == 148, f"time_grant must be 148 B, got {len(grant_bytes)}"


@pytest.mark.asyncio
async def test_request_key_second_grant_null(
    client: AsyncClient,
    user_with_session: dict,
    active_permit: dict,
) -> None:
    """Second key request for same device should return null grant."""
    headers = {"Authorization": f"Bearer {user_with_session['token']}"}

    r1 = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 1800, "request_grant": True},
        headers=headers,
    )
    assert r1.status_code == 200
    # Revoke first key to make room
    key_id = r1.json()["issued_key"]["key_id"]
    rev = await client.post(
        f"/api/v1/app/keys/{key_id}/revoke-on-server",
        headers=headers,
    )
    assert rev.status_code == 200

    r2 = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 1800, "request_grant": True},
        headers=headers,
    )
    assert r2.status_code == 200
    # Grant should be null — already exists
    assert r2.json()["time_grant"] is None


@pytest.mark.asyncio
async def test_n_parallel_exceeded(
    client: AsyncClient,
    user_with_session: dict,
    active_permit: dict,
) -> None:
    """With n_parallel=2, third key request should get 409."""
    headers = {"Authorization": f"Bearer {user_with_session['token']}"}

    r1 = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 3600},
        headers=headers,
    )
    assert r1.status_code == 200

    r2 = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 3600},
        headers=headers,
    )
    assert r2.status_code == 200

    r3 = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 3600},
        headers=headers,
    )
    assert r3.status_code == 409
    assert r3.json()["detail"]["error"] == "n_parallel_exceeded"


@pytest.mark.asyncio
async def test_ttl_too_long(
    client: AsyncClient,
    user_with_session: dict,
    active_permit: dict,
) -> None:
    resp = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 999999},
        headers={"Authorization": f"Bearer {user_with_session['token']}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "ttl_too_long"


@pytest.mark.asyncio
async def test_revoke_on_server(
    client: AsyncClient,
    user_with_session: dict,
    active_permit: dict,
) -> None:
    headers = {"Authorization": f"Bearer {user_with_session['token']}"}
    issue_resp = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 3600},
        headers=headers,
    )
    key_id = issue_resp.json()["issued_key"]["key_id"]

    revoke_resp = await client.post(
        f"/api/v1/app/keys/{key_id}/revoke-on-server",
        headers=headers,
    )
    assert revoke_resp.status_code == 200

    # Revoking again should fail with not_active
    revoke2 = await client.post(
        f"/api/v1/app/keys/{key_id}/revoke-on-server",
        headers=headers,
    )
    assert revoke2.status_code == 409


@pytest.mark.asyncio
async def test_generate_filter_idempotent(db_session: AsyncSession, enrolled_reader: dict) -> None:
    """Running generate_filter twice should create two sequential versions, not duplicate."""
    from scud.domain.filters import handle_generate_filter

    reader_id = enrolled_reader["reader_id"]

    fp1 = await handle_generate_filter(db_session, reader_id)
    await db_session.commit()
    assert fp1.filter_version == 1

    fp2 = await handle_generate_filter(db_session, reader_id)
    await db_session.commit()
    assert fp2.filter_version == 2
    assert fp2.is_current is True

    # Check fp1 is no longer current
    from scud.db.repositories.filters import get_filter_by_version
    fp1_refreshed = await get_filter_by_version(db_session, reader_id, 1)
    assert fp1_refreshed is not None
    assert fp1_refreshed.is_current is False


@pytest.mark.asyncio
async def test_key_id_matches_blake2s(
    client: AsyncClient,
    user_with_session: dict,
    active_permit: dict,
) -> None:
    """Verify the issued key_id matches what BLAKE2s would compute."""
    import struct

    from scud.crypto.key_id import compute_key_id

    headers = {"Authorization": f"Bearer {user_with_session['token']}"}
    resp = await client.post(
        "/api/v1/app/keys/request",
        json={"permit_id": active_permit["permit_id"], "validity_seconds": 3600},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["issued_key"]

    full_bytes = base64.b64decode(data["full_key_bytes"])
    # Extract fields from header
    reader_id = full_bytes[1:17]
    phone_pubkey = full_bytes[17:49]
    issued_at = int.from_bytes(full_bytes[49:57], "little")
    serial = int.from_bytes(full_bytes[81:85], "little")

    expected_key_id = compute_key_id(reader_id, phone_pubkey, issued_at, serial)
    actual_key_id = bytes.fromhex(data["key_id"])
    assert actual_key_id == expected_key_id
