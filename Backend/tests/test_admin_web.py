"""Admin web-panel tests: login flow + smoke render of all top-level pages.

Покрывает «не упало» по основным маршрутам. Глубокие interaction-тесты
(create/revoke в UI) лежат в JSON-API тестах — UI просто их зеркалит.
"""

from __future__ import annotations

import secrets
import hashlib

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def admin_plaintext_key(db_session) -> str:
    """Create a fresh admin api_key, return its plaintext (for /admin/login)."""
    from scud.db.models import ApiKey

    plaintext = "sk_admin_" + secrets.token_urlsafe(36)
    k = ApiKey(
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        key_prefix=plaintext[:8],
        kind="admin",
        name="test_web_admin",
    )
    db_session.add(k)
    await db_session.commit()
    return plaintext


@pytest.fixture
async def logged_in_client(client: AsyncClient, admin_plaintext_key) -> AsyncClient:
    """Authenticate the client by posting to /admin/login."""
    resp = await client.post(
        "/admin/login",
        data={"api_key": admin_plaintext_key, "next": "/admin/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    cookie = resp.cookies.get("scud_admin_session")
    assert cookie, "expected session cookie after login"
    return client


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------

async def test_login_page_renders(client: AsyncClient):
    resp = await client.get("/admin/login")
    assert resp.status_code == 200
    assert "Admin API key" in resp.text


async def test_login_with_bad_key_returns_401(client: AsyncClient):
    resp = await client.post(
        "/admin/login",
        data={"api_key": "nope", "next": "/admin/"},
    )
    assert resp.status_code == 401
    assert "Неверный" in resp.text


async def test_login_with_valid_key_sets_cookie(
    client: AsyncClient, admin_plaintext_key
):
    resp = await client.post(
        "/admin/login",
        data={"api_key": admin_plaintext_key, "next": "/admin/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/"
    assert "scud_admin_session" in resp.cookies


async def test_login_rejects_open_redirect(
    client: AsyncClient, admin_plaintext_key
):
    """next=https://evil — должно безопасно редиректить на /admin/, а не наружу."""
    resp = await client.post(
        "/admin/login",
        data={"api_key": admin_plaintext_key, "next": "https://evil.example.com/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/"


async def test_pages_redirect_to_login_without_cookie(client: AsyncClient):
    """Без cookie любая страница → 303 на /admin/login."""
    for path in ["/admin/", "/admin/users", "/admin/passages", "/admin/permits",
                 "/admin/readers", "/admin/keys", "/admin/api-keys", "/admin/audit"]:
        resp = await client.get(path, follow_redirects=False)
        assert resp.status_code == 303, f"{path}: {resp.status_code}"
        assert "/admin/login" in resp.headers["location"], path


async def test_logout_deletes_cookie(logged_in_client: AsyncClient):
    resp = await logged_in_client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 303
    # Дальнейший GET должен снова редиректить на login.
    resp2 = await logged_in_client.get("/admin/", follow_redirects=False)
    assert resp2.status_code == 303
    assert "/admin/login" in resp2.headers["location"]


# ---------------------------------------------------------------------------
# Smoke render of all top-level pages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/admin/",
    "/admin/users",
    "/admin/users/new",
    "/admin/reader-groups",
    "/admin/reader-groups/new",
    "/admin/readers",
    "/admin/readers/new",
    "/admin/permits",
    "/admin/permits/new",
    "/admin/keys",
    "/admin/passages",
    "/admin/api-keys",
    "/admin/audit",
])
async def test_authenticated_page_renders(logged_in_client: AsyncClient, path: str):
    resp = await logged_in_client.get(path)
    assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:200]}"
    assert "<html" in resp.text.lower()


# ---------------------------------------------------------------------------
# A couple of action endpoints (idempotent / safe)
# ---------------------------------------------------------------------------

async def test_create_reader_group_via_form(logged_in_client: AsyncClient, db_session):
    from sqlalchemy import select
    from scud.db.models import ReaderGroup

    resp = await logged_in_client.post(
        "/admin/reader-groups",
        data={"name": "test-grp-from-web", "description": "via UI"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # Проверяем что появилась запись.
    rows = (await db_session.execute(
        select(ReaderGroup).where(ReaderGroup.name == "test-grp-from-web")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].description == "via UI"


async def test_revoke_self_api_key_blocked(
    logged_in_client: AsyncClient, admin_plaintext_key, db_session
):
    """Защита от того, чтобы admin случайно отозвал ключ, которым он сейчас залогинен."""
    from scud.db.models import ApiKey
    from sqlalchemy import select
    import hashlib as _h
    k = (await db_session.execute(
        select(ApiKey).where(
            ApiKey.key_hash == _h.sha256(admin_plaintext_key.encode()).hexdigest()
        )
    )).scalar_one()

    resp = await logged_in_client.post(
        f"/admin/api-keys/{k.api_key_id}/revoke",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Нельзя" in resp.headers["location"] or "error" in resp.headers["location"]

    # Сам ключ должен остаться активным.
    await db_session.refresh(k)
    assert k.revoked_at is None
