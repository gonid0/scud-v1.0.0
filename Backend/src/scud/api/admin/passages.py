"""Admin passage_events endpoints (shared §15).

Это «учёт проходов» — список доставленных подписанных квитанций. Используется
HR / охраной / руководителем для:
  - сверки табелей;
  - инцидент-аудита (кто проходил в момент X на ридере Y);
  - выгрузки CSV в внешние системы учёта рабочего времени.

Все эндпоинты read-only — passage_events не модифицируется руками (только
ingestion path через /app/reports/submit). Это нужное свойство:
крипто-цепочка «receipt подписан reader_ed_priv» становится бесполезной,
если админ может править строки.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import Select, func, select

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import PassageEvent, Reader, User

router = APIRouter(prefix="/passages", tags=["admin-passages"])


def _direction_name(d: int) -> str:
    return {0: "unknown", 1: "entry", 2: "exit"}.get(d, f"unknown:{d}")


def _serialize_event(ev: PassageEvent, user: Optional[User], reader: Reader) -> dict:
    return {
        "event_id": str(ev.event_id),
        "reader_id": ev.reader_id.hex(),
        "reader_name": reader.display_name if reader else None,
        "key_id": ev.key_id.hex(),
        "permit_id": str(ev.permit_id),
        "user_id": ev.user_id,
        "user_login": user.login if user else None,
        "user_display_name": user.display_name if user else None,
        "phone_pubkey": ev.phone_pubkey.hex(),
        "passed_at": ev.passed_at.isoformat(),
        "direction": _direction_name(ev.direction),
        "direction_code": ev.direction,
        "session_seq": ev.session_seq,
        "verdict": ev.verdict,
        "flags": ev.flags,
        "delivered_at": ev.delivered_at.isoformat(),
        "delivered_by": ev.delivered_by,
    }


def _base_events_query() -> Select:
    return (
        select(PassageEvent, User, Reader)
        .outerjoin(User, PassageEvent.user_id == User.user_id)
        .join(Reader, PassageEvent.reader_id == Reader.reader_id)
    )


def _apply_filters(
    stmt: Select,
    *,
    user_id: Optional[int] = None,
    reader_id_bytes: Optional[bytes] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    cursor_dt: Optional[datetime] = None,
) -> Select:
    if user_id is not None:
        stmt = stmt.where(PassageEvent.user_id == user_id)
    if reader_id_bytes is not None:
        stmt = stmt.where(PassageEvent.reader_id == reader_id_bytes)
    if since is not None:
        stmt = stmt.where(PassageEvent.passed_at >= since)
    if until is not None:
        stmt = stmt.where(PassageEvent.passed_at <= until)
    if cursor_dt is not None:
        stmt = stmt.where(PassageEvent.passed_at < cursor_dt)
    return stmt


def _parse_reader_id_or_422(reader_id: Optional[str]) -> Optional[bytes]:
    if not reader_id:
        return None
    try:
        return bytes.fromhex(reader_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid reader_id hex",
        )


@router.get("")
async def list_passages(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: Optional[str] = Query(None, description="ISO timestamp; вернёт события строго старше"),
    limit: int = Query(100, ge=1, le=500),
    user_id: Optional[int] = Query(None),
    reader_id: Optional[str] = Query(None, description="hex"),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
) -> dict:
    """Постраничный курсор по `passed_at DESC`.

    Cursor — это timestamp последнего элемента предыдущей страницы;
    клиент передаёт его обратно, чтобы получить следующую партию. Это
    эффективнее offset/limit на больших таблицах (нет seek-cost при росте
    числа строк).
    """
    cursor_dt: Optional[datetime] = None
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid cursor (expected ISO timestamp)",
            )

    stmt = _apply_filters(
        _base_events_query(),
        user_id=user_id,
        reader_id_bytes=_parse_reader_id_or_422(reader_id),
        since=since,
        until=until,
        cursor_dt=cursor_dt,
    ).order_by(PassageEvent.passed_at.desc()).limit(limit)

    rows = (await db.execute(stmt)).all()
    items = [_serialize_event(ev, user, reader) for ev, user, reader in rows]
    next_cursor = rows[-1][0].passed_at.isoformat() if len(rows) == limit else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/stats")
async def passage_stats(
    api_key: AdminApiKey,
    db: DbSession,
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    group_by: str = Query("reader", pattern="^(reader|user|day)$"),
) -> dict:
    """Агрегаты для дашборда: топ-ридеры / топ-пользователи / по дням."""

    def _bind_window(stmt: Select) -> Select:
        if since is not None:
            stmt = stmt.where(PassageEvent.passed_at >= since)
        if until is not None:
            stmt = stmt.where(PassageEvent.passed_at <= until)
        return stmt

    if group_by == "reader":
        stmt = (
            select(
                PassageEvent.reader_id,
                Reader.display_name,
                func.count(PassageEvent.event_id).label("count"),
            )
            .join(Reader, PassageEvent.reader_id == Reader.reader_id)
        )
        stmt = _bind_window(stmt).group_by(
            PassageEvent.reader_id, Reader.display_name
        ).order_by(func.count(PassageEvent.event_id).desc()).limit(100)
        rows = (await db.execute(stmt)).all()
        return {
            "group_by": "reader",
            "buckets": [
                {"reader_id": rid.hex(), "reader_name": name, "count": cnt}
                for rid, name, cnt in rows
            ],
        }

    if group_by == "user":
        stmt = (
            select(
                PassageEvent.user_id,
                User.display_name,
                func.count(PassageEvent.event_id).label("count"),
            )
            .outerjoin(User, PassageEvent.user_id == User.user_id)
        )
        stmt = _bind_window(stmt).group_by(
            PassageEvent.user_id, User.display_name
        ).order_by(func.count(PassageEvent.event_id).desc()).limit(100)
        rows = (await db.execute(stmt)).all()
        return {
            "group_by": "user",
            "buckets": [
                {"user_id": uid, "user_display_name": name, "count": cnt}
                for uid, name, cnt in rows
            ],
        }

    # group_by == "day"
    # date_trunc — PostgreSQL-only, но в SQLite (тесты) тоже работает через
    # func.date_trunc → строка день в ISO. На SQLite-only тестах добавим
    # CASE/strftime fallback при необходимости.
    day_expr = func.date_trunc("day", PassageEvent.passed_at).label("day")
    stmt = select(day_expr, func.count(PassageEvent.event_id).label("count"))
    stmt = _bind_window(stmt).group_by(day_expr).order_by(day_expr.desc()).limit(366)
    rows = (await db.execute(stmt)).all()
    return {
        "group_by": "day",
        "buckets": [
            {"day": (day.isoformat() if day else None), "count": cnt}
            for day, cnt in rows
        ],
    }


@router.get("/export.csv", response_class=Response)
async def export_csv(
    api_key: AdminApiKey,
    db: DbSession,
    user_id: Optional[int] = Query(None),
    reader_id: Optional[str] = Query(None, description="hex"),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    limit: int = Query(50000, ge=1, le=100000),
) -> Response:
    """CSV-выгрузка для табельных систем (1С/Битрикс/Excel).

    Сразу пишем в память (StringIO) — для текущих масштабов (десятки тысяч
    строк) этого хватает. Если потребуется > 100k — переключиться на
    StreamingResponse + AsyncResult.partitions().
    """
    stmt = _apply_filters(
        _base_events_query(),
        user_id=user_id,
        reader_id_bytes=_parse_reader_id_or_422(reader_id),
        since=since,
        until=until,
    ).order_by(PassageEvent.passed_at.desc()).limit(limit)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "event_id", "passed_at", "direction",
        "user_id", "user_login", "user_display_name",
        "reader_id", "reader_name",
        "key_id", "permit_id",
        "delivered_at", "delivered_by", "session_seq",
    ])
    for ev, user, reader in (await db.execute(stmt)).all():
        writer.writerow([
            str(ev.event_id),
            ev.passed_at.isoformat(),
            _direction_name(ev.direction),
            ev.user_id or "",
            user.login if user else "",
            user.display_name if user else "",
            ev.reader_id.hex(),
            reader.display_name if reader else "",
            ev.key_id.hex(),
            str(ev.permit_id),
            ev.delivered_at.isoformat(),
            ev.delivered_by or "",
            ev.session_seq,
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="passages.csv"'},
    )
