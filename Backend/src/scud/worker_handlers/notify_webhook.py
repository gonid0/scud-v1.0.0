"""Worker handler: notify_webhook.

Принимает payload {"webhook_id": uuid, "event_type": str, "data": {...}}
и POST'ит на URL подписки. На ошибку увеличивает consecutive_failures;
после 10 подряд — деактивирует подписку (защита от мусорного backlog'а).

Внешние зависимости: httpx (уже есть в dev/тестах; в prod нужно перенести в
основные deps, что и сделано — см. pyproject.toml).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import WebhookSubscription
from scud.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 10
TIMEOUT_SECONDS = 10


async def handle(session: AsyncSession, payload: dict) -> None:
    webhook_id = uuid.UUID(payload["webhook_id"])
    event_type = payload["event_type"]
    data = payload.get("data", {})

    sub = await session.get(WebhookSubscription, webhook_id)
    if sub is None:
        logger.warning("webhook %s not found", webhook_id)
        return
    if not sub.is_active:
        logger.info("webhook %s inactive, skip", webhook_id)
        return

    body = json.dumps(
        {"event_type": event_type, "data": data, "webhook_id": str(webhook_id)},
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "scud-webhook/1.0",
    }
    if sub.secret:
        sig = hmac.new(sub.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-SCUD-Signature"] = f"sha256={sig}"

    now = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(sub.url, content=body, headers=headers)
        if 200 <= resp.status_code < 300:
            sub.last_success_at = now
            sub.consecutive_failures = 0
            sub.last_error = None
            logger.info("webhook %s ok (status=%s)", webhook_id, resp.status_code)
        else:
            _record_failure(sub, now, f"http {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        _record_failure(sub, now, f"{type(e).__name__}: {e}")


def _record_failure(sub: WebhookSubscription, now: datetime, msg: str) -> None:
    sub.last_failure_at = now
    sub.last_error = msg
    sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
    if sub.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        sub.is_active = False
        logger.warning(
            "webhook %s deactivated after %d consecutive failures",
            sub.webhook_id, sub.consecutive_failures,
        )
    else:
        logger.warning(
            "webhook %s failed (%d/%d): %s",
            sub.webhook_id, sub.consecutive_failures, MAX_CONSECUTIVE_FAILURES, msg,
        )


async def enqueue_for_event(
    session: AsyncSession, event_type: str, data: dict
) -> int:
    """Найти активные подписки на event_type и поставить notify_webhook task на каждую.

    Возвращает количество поставленных задач. Вызывается из ingestion-пути
    (passage_receipt и т.п.) с уже открытой session — НЕ комитит.
    """
    from sqlalchemy import select
    from scud.db.repositories.tasks import enqueue_task

    subs = (await session.execute(
        select(WebhookSubscription).where(WebhookSubscription.is_active.is_(True))
    )).scalars().all()

    enqueued = 0
    for s in subs:
        types = [t.strip() for t in (s.event_types or "").split(",") if t.strip()]
        if event_type not in types:
            continue
        await enqueue_task(
            session, "notify_webhook",
            {
                "webhook_id": str(s.webhook_id),
                "event_type": event_type,
                "data": data,
            },
        )
        enqueued += 1
    return enqueued
