"""Tests for the third round of additions:
   - /metrics endpoint (Prometheus exposition)
   - permit templates list + issue-from-template flow
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from nacl.public import PrivateKey as XPrivateKey
from nacl.signing import SigningKey


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def admin_plaintext_key(db_session) -> str:
    from scud.db.models import ApiKey
    plaintext = "sk_admin_" + secrets.token_urlsafe(36)
    k = ApiKey(
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        key_prefix=plaintext[:8],
        kind="admin",
        name="obs_templates_admin",
    )
    db_session.add(k)
    await db_session.commit()
    return plaintext


async def _make_reader(db_session) -> bytes:
    """Bootstrap a reader. Returns reader_id (16 B)."""
    from scud.db.models import Reader, ReaderGroup

    g = ReaderGroup(name="t-grp-" + secrets.token_hex(4))
    db_session.add(g)
    await db_session.flush()

    ed = SigningKey.generate()
    x = XPrivateKey.generate()
    r = Reader(
        reader_id=secrets.token_bytes(16),
        reader_group_id=g.group_id,
        display_name="t-reader",
        reader_pubkey=bytes(ed.verify_key),
        server_ed25519_privkey=bytes(ed),
        server_ed25519_pubkey=bytes(ed.verify_key),
        server_x25519_privkey=bytes(x),
        server_x25519_pubkey=bytes(x.public_key),
    )
    db_session.add(r)
    await db_session.commit()
    return r.reader_id


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------

async def test_metrics_endpoint_responds_prometheus_format(client: AsyncClient):
    # /health сначала — чтобы у нас был хотя бы один request в registry.
    await client.get("/health")
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text

    # Должны увидеть HTTP metrics и бизнес-gauge'и.
    assert "scud_http_requests_total" in body
    assert "scud_http_request_duration_seconds" in body
    # Бизнес-метрики (запрашиваются on-demand при scrape).
    assert "scud_users_active" in body
    assert "scud_passages_total_now" in body
    assert "# TYPE" in body
    assert "# HELP" in body


async def test_metrics_counts_health_request(client: AsyncClient):
    await client.get("/health")
    await client.get("/health")
    await client.get("/health")
    resp = await client.get("/metrics")
    body = resp.text
    # Должно быть >= 3 successful /health requests.
    health_lines = [
        l for l in body.splitlines()
        if l.startswith("scud_http_requests_total{")
        and 'path="/health"' in l and 'status="200"' in l
    ]
    assert len(health_lines) == 1, f"expected 1 line, got: {health_lines}"
    # Значение в конце строки — парсим.
    value = float(health_lines[0].rsplit(" ", 1)[1])
    assert value >= 3, f"expected ≥3, got {value}"


async def test_metrics_histogram_format_valid(client: AsyncClient):
    await client.get("/health")
    resp = await client.get("/metrics")
    body = resp.text
    # Histogram = bucket{le="..."} + _sum + _count.
    assert 'scud_http_request_duration_seconds_bucket{' in body
    assert "scud_http_request_duration_seconds_sum" in body
    assert "scud_http_request_duration_seconds_count" in body
    # Должен быть бакет +Inf.
    assert 'le="+Inf"' in body


# ---------------------------------------------------------------------------
# Permit templates
# ---------------------------------------------------------------------------

async def test_list_permit_templates_returns_presets(
    client: AsyncClient, admin_plaintext_key
):
    resp = await client.get(
        "/api/v1/admin/permits/templates",
        headers={"X-Api-Key": admin_plaintext_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    ids = [t["id"] for t in data["items"]]
    # Известные пресеты.
    for expected in ["today", "week", "month", "quarter", "year", "dual_device"]:
        assert expected in ids, f"missing {expected}"
    # Структура каждого пресета.
    week = next(t for t in data["items"] if t["id"] == "week")
    assert week["duration_days"] == 7
    assert week["n_parallel"] == 1
    assert "label" in week
    assert "description" in week


async def test_issue_from_template_creates_permit_with_correct_dates(
    client: AsyncClient, admin_plaintext_key, db_session, test_user
):
    from scud.db.models import Permit
    from sqlalchemy import select

    reader_id = await _make_reader(db_session)

    resp = await client.post(
        "/api/v1/admin/permits/issue-from-template",
        headers={"X-Api-Key": admin_plaintext_key, "Content-Type": "application/json"},
        json={
            "user_id": test_user["user_id"],
            "reader_id": reader_id.hex(),
            "template_id": "month",
            "display_name": "Test monthly",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template"] == "month"
    assert "permit_id" in body

    permit = (await db_session.execute(
        select(Permit).where(Permit.permit_id == uuid.UUID(body["permit_id"]))
    )).scalar_one()
    assert permit.display_name == "Test monthly"
    assert permit.user_id == test_user["user_id"]
    # Срок должен быть ~30 дней.
    delta = (permit.valid_until - permit.valid_from).total_seconds()
    assert 29 * 86400 < delta < 31 * 86400


async def test_issue_from_template_404_on_unknown_template(
    client: AsyncClient, admin_plaintext_key, db_session, test_user
):
    reader_id = await _make_reader(db_session)
    resp = await client.post(
        "/api/v1/admin/permits/issue-from-template",
        headers={"X-Api-Key": admin_plaintext_key, "Content-Type": "application/json"},
        json={
            "user_id": test_user["user_id"],
            "reader_id": reader_id.hex(),
            "template_id": "century",   # не существует
            "display_name": "x",
        },
    )
    assert resp.status_code == 404
    assert "template_not_found" in resp.json()["detail"]


async def test_issue_from_template_today_ends_at_eod(
    client: AsyncClient, admin_plaintext_key, db_session, test_user
):
    from scud.db.models import Permit
    from sqlalchemy import select

    reader_id = await _make_reader(db_session)
    resp = await client.post(
        "/api/v1/admin/permits/issue-from-template",
        headers={"X-Api-Key": admin_plaintext_key, "Content-Type": "application/json"},
        json={
            "user_id": test_user["user_id"],
            "reader_id": reader_id.hex(),
            "template_id": "today",
            "display_name": "Day-pass",
        },
    )
    assert resp.status_code == 201

    permit = (await db_session.execute(
        select(Permit).where(Permit.permit_id == uuid.UUID(resp.json()["permit_id"]))
    )).scalar_one()
    # 'today' = до 23:59 текущих суток. Проверяем что valid_until попадает в
    # этот день и не выходит сильно вперёд.
    now = datetime.now(timezone.utc)
    delta_sec = (permit.valid_until - now).total_seconds()
    assert 0 < delta_sec <= 24 * 3600
