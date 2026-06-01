"""Пользователи: список, создание, детали, деактивация."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import AdminAuditLog, Permit, User, UserDevice
from scud.db.repositories import users as user_repo
from scud.domain.auth import hash_password

router = APIRouter(prefix="/users")


def _audit(db, admin, action: str, target_id: str, details: dict | None = None) -> None:
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=admin.api_key_id,
            action=action,
            target_type="user",
            target_id=target_id,
            details=details,
        )
    )


@router.get("", include_in_schema=False)
async def list_users(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    q: Optional[str] = None,
    cursor: int = 0,
    limit: int = 50,
):
    stmt = select(User).order_by(User.user_id).limit(limit)
    if cursor:
        stmt = stmt.where(User.user_id > cursor)
    if q:
        like = f"%{q.lower()}%"
        # SQLAlchemy 2: ilike portable; на SQLite это case-insensitive LIKE.
        from sqlalchemy import or_
        stmt = stmt.where(or_(User.login.ilike(like), User.display_name.ilike(like)))

    users = (await db.execute(stmt)).scalars().all()
    next_cursor = users[-1].user_id if len(users) == limit else None
    return TEMPLATES.TemplateResponse(
        request,
        "users/list.html",
        {
            "current_admin": admin,
            "users": users,
            "q": q,
            "next_cursor": next_cursor,
        },
    )


@router.get("/new", include_in_schema=False)
async def new_user_form(request: Request, admin: CurrentAdmin):
    return TEMPLATES.TemplateResponse(
        request,
        "users/new.html",
        {"current_admin": admin},
    )


@router.post("", include_in_schema=False)
async def create_user(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    login: str = Form(..., min_length=2, max_length=64),
    password: str = Form(..., min_length=6),
    display_name: str = Form(..., min_length=1, max_length=128),
    user_group_id: str = Form(...),
):
    existing = await user_repo.get_user_by_login(db, login)
    if existing is not None:
        return RedirectResponse(
            url="/admin/users/new?error=login_taken", status_code=303
        )
    try:
        group_uuid = uuid.UUID(user_group_id)
    except ValueError:
        return RedirectResponse(
            url="/admin/users/new?error=invalid_group_id", status_code=303
        )

    user = await user_repo.create_user(
        db, login, hash_password(password), display_name, group_uuid
    )
    _audit(db, admin, "create_user", str(user.user_id))
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user.user_id}?flash=Создан", status_code=303
    )


@router.get("/{user_id}", include_in_schema=False)
async def user_detail(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: int,
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")

    devices = (await db.execute(
        select(UserDevice).where(UserDevice.user_id == user_id)
        .order_by(UserDevice.registered_at.desc())
    )).scalars().all()

    permits = (await db.execute(
        select(Permit).where(Permit.user_id == user_id)
        .order_by(Permit.created_at.desc()).limit(50)
    )).scalars().all()

    return TEMPLATES.TemplateResponse(
        request,
        "users/detail.html",
        {
            "current_admin": admin,
            "user": user,
            "devices": devices,
            "permits": permits,
        },
    )


@router.post("/{user_id}/deactivate", include_in_schema=False)
async def deactivate_user(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: int,
):
    """Деактивация: is_active=False + revoke всех session'ов (logout с устройств).

    Permits не трогаем — пусть остаются read-only «история». Если нужно реально
    отозвать доступ — отдельный экшен `revoke all permits`.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    user.is_active = False
    await user_repo.revoke_user_sessions(db, user_id)
    _audit(db, admin, "deactivate_user", str(user_id))
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}?flash=Деактивирован", status_code=303
    )


@router.get("/{user_id}/attendance", include_in_schema=False)
async def user_attendance_report(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: int,
    days: int = 30,
):
    """Табельный отчёт по конкретному пользователю.

    Показывает: ежедневные счётчики проходов за последние N дней (по умолчанию 30),
    разбивку по ридерам, первый и последний проход в каждом дне. Это та таблица,
    которую HR обычно копирует в Excel — все нужные колонки сразу.
    """
    import json
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import and_, func, select
    from scud.db.models import PassageEvent, Reader

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)

    days = max(1, min(days, 365))
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 1) Дневной chart: count(*) по дням за N дней. Нулевые дни добиваются позже.
    daily_rows = (await db.execute(
        select(
            func.date(PassageEvent.passed_at).label("day"),
            func.count(PassageEvent.event_id).label("cnt"),
        ).where(
            PassageEvent.user_id == user_id,
            PassageEvent.passed_at >= since,
        ).group_by(func.date(PassageEvent.passed_at))
    )).all()
    by_day: dict[str, int] = {}
    for day, cnt in daily_rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        by_day[key] = int(cnt)
    daily_series = []
    for i in range(days):
        d = (since + timedelta(days=i)).date().isoformat()
        daily_series.append({"date": d, "count": by_day.get(d, 0)})

    # 2) Раскладка по ридерам.
    per_reader_rows = (await db.execute(
        select(
            Reader.reader_id, Reader.display_name,
            func.count(PassageEvent.event_id).label("cnt"),
        )
        .join(PassageEvent, PassageEvent.reader_id == Reader.reader_id)
        .where(PassageEvent.user_id == user_id, PassageEvent.passed_at >= since)
        .group_by(Reader.reader_id, Reader.display_name)
        .order_by(func.count(PassageEvent.event_id).desc())
    )).all()

    # 3) Per-day first/last «время прихода» и «время ухода» (полезно для HR).
    pf_rows = (await db.execute(
        select(
            func.date(PassageEvent.passed_at).label("day"),
            func.min(PassageEvent.passed_at).label("first_at"),
            func.max(PassageEvent.passed_at).label("last_at"),
            func.count(PassageEvent.event_id).label("cnt"),
        )
        .where(PassageEvent.user_id == user_id, PassageEvent.passed_at >= since)
        .group_by(func.date(PassageEvent.passed_at))
        .order_by(func.date(PassageEvent.passed_at).desc())
    )).all()
    per_day_rows = [
        {
            "day": (d.isoformat() if hasattr(d, "isoformat") else str(d)),
            "first_at": f.isoformat() if f else None,
            "last_at": l.isoformat() if l else None,
            "cnt": int(c),
        }
        for d, f, l, c in pf_rows
    ]

    total = sum(s["count"] for s in daily_series)

    return TEMPLATES.TemplateResponse(
        request,
        "users/attendance.html",
        {
            "current_admin": admin,
            "user": user,
            "days": days,
            "total": total,
            "daily_labels_json": json.dumps([s["date"] for s in daily_series]),
            "daily_counts_json": json.dumps([s["count"] for s in daily_series]),
            "per_reader": per_reader_rows,
            "per_day_rows": per_day_rows,
        },
    )


@router.post("/{user_id}/activate", include_in_schema=False)
async def activate_user(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: int,
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    user.is_active = True
    _audit(db, admin, "activate_user", str(user_id))
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}?flash=Активирован", status_code=303
    )
