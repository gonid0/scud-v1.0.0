"""Passages — учёт проходов (shared §15). UI-обёртка над admin/passages JSON API."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import PassageEvent, Reader, User

router = APIRouter(prefix="/passages")


def _direction_name(d: int) -> str:
    return {0: "—", 1: "entry", 2: "exit"}.get(d, f"?{d}")


@router.get("", include_in_schema=False)
async def list_passages(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    cursor: Optional[str] = None,
    limit: int = 100,
    user_id: Optional[int] = None,
    reader_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    cursor_dt = None
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="bad cursor")
    rid_bytes = None
    if reader_id:
        try:
            rid_bytes = bytes.fromhex(reader_id)
        except ValueError:
            pass
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    stmt = (
        select(PassageEvent, User, Reader)
        .outerjoin(User, PassageEvent.user_id == User.user_id)
        .join(Reader, PassageEvent.reader_id == Reader.reader_id)
    )
    if cursor_dt:    stmt = stmt.where(PassageEvent.passed_at < cursor_dt)
    if user_id:      stmt = stmt.where(PassageEvent.user_id == user_id)
    if rid_bytes:    stmt = stmt.where(PassageEvent.reader_id == rid_bytes)
    if since_dt:     stmt = stmt.where(PassageEvent.passed_at >= since_dt)
    if until_dt:     stmt = stmt.where(PassageEvent.passed_at <= until_dt)

    rows = (await db.execute(stmt.order_by(PassageEvent.passed_at.desc()).limit(limit))).all()
    next_cursor = rows[-1][0].passed_at.isoformat() if len(rows) == limit else None

    # Для фильтра «по ридеру» подсовываем список ридеров.
    readers = (await db.execute(
        select(Reader).order_by(Reader.display_name)
    )).scalars().all()

    return TEMPLATES.TemplateResponse(
        request,
        "passages/list.html",
        {
            "current_admin": admin,
            "rows": rows,
            "direction_name": _direction_name,
            "next_cursor": next_cursor,
            "readers": readers,
            "filter_user_id": user_id,
            "filter_reader_id": reader_id,
            "filter_since": since,
            "filter_until": until,
        },
    )


@router.get("/export.csv", include_in_schema=False)
async def export_passages_csv(
    admin: CurrentAdmin,
    db: WebDb,
    user_id: Optional[int] = None,
    reader_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50000,
) -> Response:
    """CSV-выгрузка проходов для админ-панели (cookie-сессия).

    Зеркалит /api/v1/admin/passages/export.csv, но аутентифицируется по cookie
    (CurrentAdmin), а не по X-Api-Key — иначе кнопка из браузера получает
    `missing_api_key` (браузер шлёт только cookie сессии панели).
    """
    limit = max(1, min(limit, 100000))
    rid_bytes = None
    if reader_id:
        try:
            rid_bytes = bytes.fromhex(reader_id)
        except ValueError:
            rid_bytes = None
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    stmt = (
        select(PassageEvent, User, Reader)
        .outerjoin(User, PassageEvent.user_id == User.user_id)
        .join(Reader, PassageEvent.reader_id == Reader.reader_id)
    )
    if user_id:
        stmt = stmt.where(PassageEvent.user_id == user_id)
    if rid_bytes:
        stmt = stmt.where(PassageEvent.reader_id == rid_bytes)
    if since_dt:
        stmt = stmt.where(PassageEvent.passed_at >= since_dt)
    if until_dt:
        stmt = stmt.where(PassageEvent.passed_at <= until_dt)
    stmt = stmt.order_by(PassageEvent.passed_at.desc()).limit(limit)

    _dir = {0: "unknown", 1: "entry", 2: "exit"}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "event_id", "passed_at", "direction",
        "user_id", "user_login", "user_display_name",
        "reader_id", "reader_name",
        "key_id", "permit_id",
        "delivered_at", "delivered_by", "session_seq",
    ])
    for ev, user, reader in (await db.execute(stmt)).all():
        w.writerow([
            str(ev.event_id),
            ev.passed_at.isoformat(),
            _dir.get(ev.direction, f"unknown:{ev.direction}"),
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
