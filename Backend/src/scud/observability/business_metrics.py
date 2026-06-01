"""Бизнес-gauge'и: текущие значения по БД, обновляются при scrape /metrics.

Считаем недорого (один COUNT(*) с index) — для prod-нагрузок (десятки тысяч
ридеров/permits/keys) это микросекунды. Если в будущем будут миллионы строк
и узкое место — переложим на materialized view + cron-refresh.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from scud.db.models import (
    IssuedKey,
    PassageEvent,
    Permit,
    Reader,
    User,
    WebhookSubscription,
)
from scud.db.session import AsyncSessionFactory
from scud.observability.registry import Gauge

logger = logging.getLogger(__name__)

scud_users_active = Gauge(
    "scud_users_active",
    "Number of active users (is_active=true)",
)
scud_readers_active = Gauge(
    "scud_readers_active",
    "Number of active readers (is_active=true)",
)
scud_permits_active = Gauge(
    "scud_permits_active",
    "Number of permits with revoked_at IS NULL",
)
scud_issued_keys_active = Gauge(
    "scud_issued_keys_active",
    "Number of issued_keys with is_active=true",
)
scud_passages_total = Gauge(
    "scud_passages_total_now",
    "Total passage_events ever recorded (snapshot at scrape time)",
)
scud_webhooks_active = Gauge(
    "scud_webhooks_active",
    "Number of active webhook subscriptions",
)
scud_webhooks_failing = Gauge(
    "scud_webhooks_failing",
    "Number of webhooks with consecutive_failures > 0",
)


async def refresh_business_gauges() -> None:
    """Один-проходом обновляет все бизнес-gauge'и. Тихо проглатывает ошибки —
    /metrics не должен падать из-за временной DB-проблемы."""
    try:
        async with AsyncSessionFactory() as session:
            async def _count(stmt) -> int:
                r = await session.execute(stmt)
                return int(r.scalar_one() or 0)

            scud_users_active.set(await _count(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            ))
            scud_readers_active.set(await _count(
                select(func.count()).select_from(Reader).where(Reader.is_active.is_(True))
            ))
            scud_permits_active.set(await _count(
                select(func.count()).select_from(Permit).where(Permit.revoked_at.is_(None))
            ))
            scud_issued_keys_active.set(await _count(
                select(func.count()).select_from(IssuedKey).where(IssuedKey.is_active.is_(True))
            ))
            scud_passages_total.set(await _count(
                select(func.count()).select_from(PassageEvent)
            ))
            scud_webhooks_active.set(await _count(
                select(func.count()).select_from(WebhookSubscription)
                .where(WebhookSubscription.is_active.is_(True))
            ))
            scud_webhooks_failing.set(await _count(
                select(func.count()).select_from(WebhookSubscription)
                .where(WebhookSubscription.consecutive_failures > 0)
            ))
    except Exception as e:
        logger.warning("metrics refresh failed: %s", e)
