"""Dashboard страница: верхнеуровневые счётчики + последние события + chart."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import (
    IssuedKey,
    PassageEvent,
    Permit,
    Reader,
    ReaderGroup,
    ReaderReport,
    User,
)

router = APIRouter()


async def _daily_passages_series(db, days: int = 30) -> list[dict]:
    """Возвращает [{date: 'YYYY-MM-DD', count: N}, ...] за последние N дней.

    Заполняет нулями отсутствующие дни — важно для корректной отрисовки
    line-chart'а без «прыжков» по оси X.
    """
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # func.date(...) портабельно работает в SQLite (используем в тестах) и
    # PostgreSQL. Для PG date_trunc был бы каноничнее, но date() — общий
    # знаменатель, ничего не теряем по точности.
    rows = (await db.execute(
        select(
            func.date(PassageEvent.passed_at).label("day"),
            func.count(PassageEvent.event_id).label("cnt"),
        )
        .where(PassageEvent.passed_at >= since)
        .group_by(func.date(PassageEvent.passed_at))
    )).all()

    by_day: dict[str, int] = {}
    for day, cnt in rows:
        # SQLite возвращает str, PG — date — приводим к ISO.
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        by_day[key] = int(cnt)

    series: list[dict] = []
    for i in range(days):
        d = (since + timedelta(days=i)).date().isoformat()
        series.append({"date": d, "count": by_day.get(d, 0)})
    return series


@router.get("/", include_in_schema=False)
async def dashboard(request: Request, admin: CurrentAdmin, db: WebDb):
    """Главная: 6 ключевых счётчиков + последние 10 passages + recent reports.

    Все запросы — отдельные COUNT'ы, ничего тяжёлого. На больших объёмах
    (>10M passage_events) COUNT(*) на passage_events за всё время потребует
    отдельной materialized-view стратегии, но для VKR-масштабов прямой COUNT
    мгновенный.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    async def _count(stmt) -> int:
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    counts = {
        "users":              await _count(select(func.count()).select_from(User).where(User.is_active.is_(True))),
        "readers":            await _count(select(func.count()).select_from(Reader).where(Reader.is_active.is_(True))),
        "reader_groups":      await _count(select(func.count()).select_from(ReaderGroup)),
        "active_permits":     await _count(select(func.count()).select_from(Permit).where(Permit.revoked_at.is_(None))),
        "active_keys":        await _count(select(func.count()).select_from(IssuedKey).where(IssuedKey.is_active.is_(True))),
        "passages_today":     await _count(select(func.count()).select_from(PassageEvent).where(PassageEvent.passed_at >= today_start)),
        "passages_week":      await _count(select(func.count()).select_from(PassageEvent).where(PassageEvent.passed_at >= week_ago)),
        "unprocessed_reports": await _count(select(func.count()).select_from(ReaderReport).where(ReaderReport.processed_at.is_(None))),
    }

    # Последние 10 passages с join'ом user + reader.
    recent_passages = (await db.execute(
        select(PassageEvent, User, Reader)
        .outerjoin(User, PassageEvent.user_id == User.user_id)
        .join(Reader, PassageEvent.reader_id == Reader.reader_id)
        .order_by(PassageEvent.passed_at.desc())
        .limit(10)
    )).all()

    # Top-5 readers by passages this week.
    top_readers_rows = (await db.execute(
        select(
            Reader.reader_id, Reader.display_name,
            func.count(PassageEvent.event_id).label("cnt"),
        )
        .join(PassageEvent, PassageEvent.reader_id == Reader.reader_id)
        .where(PassageEvent.passed_at >= week_ago)
        .group_by(Reader.reader_id, Reader.display_name)
        .order_by(func.count(PassageEvent.event_id).desc())
        .limit(5)
    )).all()

    daily_series = await _daily_passages_series(db, days=30)

    # Stale readers — давно не контактировали.
    stale_threshold = now - timedelta(days=2)
    stale_readers = (await db.execute(
        select(Reader).where(
            Reader.is_active.is_(True),
            (Reader.last_contact_at.is_(None)) | (Reader.last_contact_at < stale_threshold),
        ).order_by(Reader.last_contact_at.asc().nulls_first()).limit(5)
    )).scalars().all()

    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "current_admin": admin,
            "counts": counts,
            "recent_passages": recent_passages,
            "top_readers": top_readers_rows,
            "stale_readers": stale_readers,
            # Chart.js хочет JSON; самый безопасный путь — pre-encoded в template.
            "daily_labels_json": json.dumps([s["date"] for s in daily_series]),
            "daily_counts_json": json.dumps([s["count"] for s in daily_series]),
        },
    )
