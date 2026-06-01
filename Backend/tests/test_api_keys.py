"""Admin API keys management tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_api_key(client: AsyncClient, admin_api_key: str) -> None:
    resp = await client.post(
        "/api/v1/admin/api-keys",
        json={"name": "test-key", "kind": "integration"},
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "plaintext" in data
    assert data["plaintext"].startswith("sk_integration_")
    assert "api_key_id" in data


@pytest.mark.asyncio
async def test_create_admin_api_key(client: AsyncClient, admin_api_key: str) -> None:
    resp = await client.post(
        "/api/v1/admin/api-keys",
        json={"name": "admin-key", "kind": "admin"},
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["plaintext"].startswith("sk_admin_")


@pytest.mark.asyncio
async def test_list_api_keys(client: AsyncClient, admin_api_key: str) -> None:
    resp = await client.get(
        "/api/v1/admin/api-keys",
        headers={"X-Api-Key": admin_api_key},
    )
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_revoke_api_key(client: AsyncClient, admin_api_key: str) -> None:
    create_resp = await client.post(
        "/api/v1/admin/api-keys",
        json={"name": "to-revoke", "kind": "integration"},
        headers={"X-Api-Key": admin_api_key},
    )
    assert create_resp.status_code == 201
    new_key_id = create_resp.json()["api_key_id"]
    new_plaintext = create_resp.json()["plaintext"]

    # BE-SEC-02: integration key must be denied on admin endpoints (403, not 401)
    list_resp_integration = await client.get(
        "/api/v1/admin/api-keys",
        headers={"X-Api-Key": new_plaintext},
    )
    assert list_resp_integration.status_code == 403, (
        "integration key must be blocked from admin endpoints (BE-SEC-02)"
    )

    # Admin key can still list
    list_resp_admin = await client.get(
        "/api/v1/admin/api-keys",
        headers={"X-Api-Key": admin_api_key},
    )
    assert list_resp_admin.status_code == 200

    # Revoke the integration key
    revoke_resp = await client.post(
        f"/api/v1/admin/api-keys/{new_key_id}/revoke",
        headers={"X-Api-Key": admin_api_key},
    )
    assert revoke_resp.status_code == 200

    # After revoke the key should fail with 401 (revoked = not found in active keys)
    after_resp = await client.get(
        "/api/v1/admin/api-keys",
        headers={"X-Api-Key": new_plaintext},
    )
    assert after_resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/admin/users",
        headers={"X-Api-Key": "sk_invalid_key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401
