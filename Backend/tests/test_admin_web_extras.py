"""Tests for the second round of admin web features:
   - dashboard chart data shape
   - bulk-issue permits
   - per-user attendance page
   - webhooks CRUD + fan-out enqueue
   - reader search
"""

from __future__ import annotations

import hashlib
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from nacl.public import PrivateKey as XPrivateKey
from nacl.signing import SigningKey
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Fixtures (reuse admin_plaintext_key + logged_in_client from test_admin_web.py
# by defining their own — keep tests independent).
# ---------------------------------------------------------------------------

@pytest.fixture
async def admin_plaintext_key(db_session) -> str:
    from scud.db.models import ApiKey
    plaintext = "sk_admin_" + secrets.token_urlsafe(36)
    k = ApiKey(
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        key_prefix=plaintext[:8],
        kind="admin",
        name="extras_admin",
    )
    db_session.add(k)
    await db_session.commit()
    return plaintext


@pytest.fixture
async def logged_in_client(client: AsyncClient, admin_plaintext_key) -> AsyncClient:
    resp = await client.post(
        "/admin/login",
        data={"api_key": admin_plaintext_key, "next": "/admin/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


async def _make_reader_with_group(db_session, group_name: str, reader_name: str):
    """Bootstrap a reader + group, return (group, reader)."""
    from scud.db.models import Reader, ReaderGroup

    g = ReaderGroup(name=group_name)
    db_session.add(g)
    await db_session.flush()

    ed = SigningKey.generate()
    x = XPrivateKey.generate()
    r = Reader(
        reader_id=secrets.token_bytes(16),
        reader_group_id=g.group_id,
        display_name=reader_name,
        reader_pubkey=bytes(ed.verify_key),
        server_ed25519_privkey=bytes(ed),
        server_ed25519_pubkey=bytes(ed.verify_key),
        server_x25519_privkey=bytes(x),
        server_x25519_pubkey=bytes(x.public_key),
    )
    db_session.add(r)
    await db_session.flush()
    return g, r


# ---------------------------------------------------------------------------
# Dashboard chart data
# ---------------------------------------------------------------------------

async def test_dashboard_includes_chart_data(logged_in_client: AsyncClient):
    resp = await logged_in_client.get("/admin/")
    assert resp.status_code == 200
    # Сериализованные JSON-массивы для labels и counts.
    assert "daily_labels_json" not in resp.text  # это переменная Python, не в HTML
    # Канвас отрисован.
    assert 'id="dailyPassagesChart"' in resp.text
    # Chart.js CDN.
    assert "cdn.jsdelivr.net/npm/chart.js" in resp.text


# ---------------------------------------------------------------------------
# Bulk-issue permits
# ---------------------------------------------------------------------------

async def test_bulk_user_x_group_creates_one_per_reader(
    logged_in_client: AsyncClient, db_session, test_user
):
    from scud.db.models import Permit

    g, r1 = await _make_reader_with_group(db_session, "bulk-grp-a", "door-1")
    _, r2 = await _make_reader_with_group(db_session, "bulk-grp-b", "door-2")
    # r1 + r3 in same group g_a:
    from scud.db.models import Reader
    ed = SigningKey.generate(); x = XPrivateKey.generate()
    r3 = Reader(
        reader_id=secrets.token_bytes(16),
        reader_group_id=g.group_id,
        display_name="door-3",
        reader_pubkey=bytes(ed.verify_key),
        server_ed25519_privkey=bytes(ed),
        server_ed25519_pubkey=bytes(ed.verify_key),
        server_x25519_privkey=bytes(x),
        server_x25519_pubkey=bytes(x.public_key),
    )
    db_session.add(r3)
    await db_session.commit()

    resp = await logged_in_client.post(
        "/admin/permits/bulk",
        data={
            "mode": "user_x_group",
            "user_id": str(test_user["user_id"]),
            "reader_group_id": str(g.group_id),
            "display_name_template": "{user} -> {reader}",
            "valid_from": "2026-01-01T00:00",
            "valid_until": "2027-01-01T00:00",
            "n_parallel": "1",
            "max_token_ttl_hours": "24",
            "skip_existing": "true",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Создано:" in urllib.parse.unquote(resp.headers["location"])

    permits = (await db_session.execute(
        select(Permit).where(Permit.user_id == test_user["user_id"])
    )).scalars().all()
    # Должны были создаться 2 permit'а — для r1 и r3 (оба в group g).
    # r2 в другой группе и не входит.
    reader_ids = {p.reader_id for p in permits}
    assert r1.reader_id in reader_ids
    assert r3.reader_id in reader_ids
    assert r2.reader_id not in reader_ids


async def test_bulk_skip_existing_avoids_duplicates(
    logged_in_client: AsyncClient, db_session, test_user
):
    from scud.db.models import Permit
    from scud.db.repositories import permits as permit_repo
    from datetime import datetime, timezone

    g, r = await _make_reader_with_group(db_session, "dedup-grp", "dedup-door")
    # Pre-existing permit:
    await permit_repo.create_permit(
        db_session,
        user_id=test_user["user_id"], reader_id=r.reader_id,
        display_name="existing", description=None,
        valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        valid_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
        n_parallel=1, max_token_ttl_seconds=86400,
    )
    await db_session.commit()

    resp = await logged_in_client.post(
        "/admin/permits/bulk",
        data={
            "mode": "user_x_group",
            "user_id": str(test_user["user_id"]),
            "reader_group_id": str(g.group_id),
            "display_name_template": "x",
            "valid_from": "2026-01-01T00:00",
            "valid_until": "2027-01-01T00:00",
            "n_parallel": "1",
            "max_token_ttl_hours": "24",
            "skip_existing": "true",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    decoded = urllib.parse.unquote(resp.headers["location"])
    assert "пропущено: 1" in decoded

    permits = (await db_session.execute(
        select(Permit).where(Permit.user_id == test_user["user_id"], Permit.reader_id == r.reader_id)
    )).scalars().all()
    # Только один — старый — остался.
    assert len(permits) == 1


# ---------------------------------------------------------------------------
# Per-user attendance page
# ---------------------------------------------------------------------------

async def test_user_attendance_page_renders(
    logged_in_client: AsyncClient, db_session, test_user
):
    resp = await logged_in_client.get(
        f"/admin/users/{test_user['user_id']}/attendance"
    )
    assert resp.status_code == 200
    assert "Табель" in resp.text
    assert 'id="attendanceChart"' in resp.text


# ---------------------------------------------------------------------------
# Webhooks CRUD
# ---------------------------------------------------------------------------

async def test_webhooks_list_renders(logged_in_client: AsyncClient):
    resp = await logged_in_client.get("/admin/webhooks")
    assert resp.status_code == 200
    assert "Webhook" in resp.text


async def test_webhook_create_validates_url(logged_in_client: AsyncClient, db_session):
    resp = await logged_in_client.post(
        "/admin/webhooks",
        data={"name": "x", "url": "ftp://nope", "event_types": "passage_event"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


async def test_webhook_create_then_toggle_then_delete(
    logged_in_client: AsyncClient, db_session
):
    from scud.db.models import WebhookSubscription

    # Create
    resp = await logged_in_client.post(
        "/admin/webhooks",
        data={
            "name": "test-hook",
            "url": "https://example.com/hook",
            "event_types": "passage_event,test",
            "secret": "s3cret",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    rows = (await db_session.execute(
        select(WebhookSubscription).where(WebhookSubscription.name == "test-hook")
    )).scalars().all()
    assert len(rows) == 1
    sub = rows[0]
    assert sub.is_active is True
    assert sub.secret == "s3cret"
    assert "passage_event" in sub.event_types

    # Toggle (disable)
    resp = await logged_in_client.post(
        f"/admin/webhooks/{sub.webhook_id}/toggle", follow_redirects=False
    )
    assert resp.status_code == 303
    await db_session.refresh(sub)
    assert sub.is_active is False

    # Delete
    resp = await logged_in_client.post(
        f"/admin/webhooks/{sub.webhook_id}/delete", follow_redirects=False
    )
    assert resp.status_code == 303
    rows = (await db_session.execute(
        select(WebhookSubscription).where(WebhookSubscription.name == "test-hook")
    )).scalars().all()
    assert len(rows) == 0


async def test_webhook_test_button_enqueues_task(
    logged_in_client: AsyncClient, db_session
):
    from scud.db.models import BackgroundTask, WebhookSubscription

    sub = WebhookSubscription(
        name="t", url="https://example.com/h", event_types="test"
    )
    db_session.add(sub)
    await db_session.commit()

    resp = await logged_in_client.post(
        f"/admin/webhooks/{sub.webhook_id}/test", follow_redirects=False
    )
    assert resp.status_code == 303

    tasks = (await db_session.execute(
        select(BackgroundTask).where(BackgroundTask.task_type == "notify_webhook")
    )).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].payload["webhook_id"] == str(sub.webhook_id)
    assert tasks[0].payload["event_type"] == "test"


# ---------------------------------------------------------------------------
# Webhook fan-out on passage ingestion
# ---------------------------------------------------------------------------

async def test_passage_ingestion_enqueues_webhook(db_session, test_user):
    """Когда приходит valid passage_receipt и есть подписка на passage_event —
    в очередь falls notify_webhook task с payload содержащим event details."""
    from datetime import datetime as _dt, timezone as _tz
    import os as _os
    from scud.db.models import (
        BackgroundTask, PassageEvent, Permit, Reader, ReaderGroup, WebhookSubscription,
    )
    from scud.crypto.signing import DOMAIN_PSG, sign_detached
    from scud.domain.reports import process_passage_receipt

    # Reader.
    sk = SigningKey.generate()
    x = XPrivateKey.generate()
    g = ReaderGroup(name="psg-" + secrets.token_hex(4))
    db_session.add(g)
    await db_session.flush()
    reader = Reader(
        reader_id=_os.urandom(16),
        reader_group_id=g.group_id,
        display_name="hook-reader",
        reader_pubkey=bytes(sk.verify_key),
        server_ed25519_privkey=bytes(sk),
        server_ed25519_pubkey=bytes(sk.verify_key),
        server_x25519_privkey=bytes(x),
        server_x25519_pubkey=bytes(x.public_key),
    )
    db_session.add(reader)
    await db_session.flush()

    permit = Permit(
        user_id=test_user["user_id"], reader_id=reader.reader_id,
        display_name="p", valid_from=_dt.now(_tz.utc),
        valid_until=_dt(2099, 1, 1, tzinfo=_tz.utc),
        n_parallel=1, max_token_ttl_seconds=86400,
    )
    db_session.add(permit)
    await db_session.flush()

    # Active subscription.
    sub = WebhookSubscription(
        name="psg-hook", url="https://example.com/", event_types="passage_event",
    )
    db_session.add(sub)
    # Inactive subscription — не должна получить event.
    inactive = WebhookSubscription(
        name="inactive", url="https://example.com/", event_types="passage_event",
        is_active=False,
    )
    db_session.add(inactive)
    # Subscription on другой type — не должна получить.
    other_type = WebhookSubscription(
        name="other", url="https://example.com/", event_types="delivery_receipt",
    )
    db_session.add(other_type)
    await db_session.commit()

    # Build receipt.
    now_ts = int(_dt.now(_tz.utc).timestamp())
    body = (
        b"\x01"
        + reader.reader_id
        + _os.urandom(16)
        + _os.urandom(16)
        + (now_ts - 30).to_bytes(8, "little")
        + bytes([1, 0])
        + (5).to_bytes(4, "little")
        + bytes([1])
        + _os.urandom(32)
        + permit.permit_id.bytes
        + (1700000000).to_bytes(8, "little")
        + (1).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
    )
    sig = sign_detached(bytes(sk), DOMAIN_PSG, body)
    blob = body + sig

    await process_passage_receipt(db_session, blob, reader)

    # Engine session-scoped → leftover notify_webhook tasks из других тестов
    # могут лежать в той же БД. Фильтруем строго на этот webhook_id.
    tasks = (await db_session.execute(
        select(BackgroundTask).where(
            BackgroundTask.task_type == "notify_webhook",
            BackgroundTask.payload["webhook_id"].as_string() == str(sub.webhook_id),
        )
    )).scalars().all()
    assert len(tasks) == 1, f"expected 1 task for {sub.webhook_id}, got {len(tasks)}"
    payload = tasks[0].payload
    assert payload["webhook_id"] == str(sub.webhook_id)
    assert payload["event_type"] == "passage_event"
    assert payload["data"]["reader_id"] == reader.reader_id.hex()
    assert payload["data"]["user_id"] == test_user["user_id"]

    # Inactive и other_type подписки НЕ должны были получить event.
    inactive_tasks = (await db_session.execute(
        select(BackgroundTask).where(
            BackgroundTask.task_type == "notify_webhook",
            BackgroundTask.payload["webhook_id"].as_string() == str(inactive.webhook_id),
        )
    )).scalars().all()
    assert len(inactive_tasks) == 0

    other_tasks = (await db_session.execute(
        select(BackgroundTask).where(
            BackgroundTask.task_type == "notify_webhook",
            BackgroundTask.payload["webhook_id"].as_string() == str(other_type.webhook_id),
        )
    )).scalars().all()
    assert len(other_tasks) == 0


# ---------------------------------------------------------------------------
# Reader search
# ---------------------------------------------------------------------------

async def test_reader_search_filters_by_name(
    logged_in_client: AsyncClient, db_session
):
    # NOTE: используем ASCII-имена. SQLite (тесты) LIKE — case-sensitive для
    # не-ASCII без ICU-расширения; PostgreSQL ilike в prod работает корректно
    # для Cyrillic. Здесь тестируем именно семантику фильтрации.
    await _make_reader_with_group(db_session, "search-grp-a", "MainGate")
    await _make_reader_with_group(db_session, "search-grp-b", "Turnstile-Lobby")
    await db_session.commit()

    resp = await logged_in_client.get("/admin/readers?q=turnstile")
    assert resp.status_code == 200
    assert "Turnstile-Lobby" in resp.text
    assert "MainGate" not in resp.text
