"""API auth endpoint tests."""

from __future__ import annotations

import base64
import os

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, test_user: dict) -> None:
    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": test_user["login"], "password": test_user["password"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_token" in data
    assert "refresh_token" in data
    assert data["user_id"] == test_user["user_id"]


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, test_user: dict) -> None:
    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": test_user["login"], "password": "wrongpassword"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": "nobody", "password": "anything"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient, test_user: dict) -> None:
    login_resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": test_user["login"], "password": test_user["password"]},
    )
    refresh_token = login_resp.json()["refresh_token"]

    refresh_resp = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_resp.status_code == 200
    new_data = refresh_resp.json()
    assert "session_token" in new_data
    assert new_data["session_token"] != login_resp.json()["session_token"]


@pytest.mark.asyncio
async def test_refresh_invalid_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": "totally_invalid"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_device(client: AsyncClient, test_user: dict) -> None:
    login_resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": test_user["login"], "password": test_user["password"]},
    )
    token = login_resp.json()["session_token"]
    phone_pubkey = base64.b64encode(os.urandom(32)).decode()

    resp = await client.post(
        "/api/v1/app/auth/register-device",
        json={"phone_pubkey": phone_pubkey, "device_label": "Test Phone"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "device_id" in resp.json()


@pytest.mark.asyncio
async def test_logout(client: AsyncClient, test_user: dict) -> None:
    login_resp = await client.post(
        "/api/v1/app/auth/login",
        json={"login": test_user["login"], "password": test_user["password"]},
    )
    token = login_resp.json()["session_token"]

    logout_resp = await client.post(
        "/api/v1/app/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp.status_code == 200

    # Token should now be invalid
    me_resp = await client.get(
        "/api/v1/app/my-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 401


@pytest.mark.asyncio
async def test_no_bearer_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/app/my-data")
    assert resp.status_code == 401
