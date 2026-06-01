"""Permits: list, create, detail (with active keys), admin revoke (two-phase)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import AdminAuditLog, IssuedKey, Permit, Reader, ReaderGroup, User
from scud.db.repositories import keys as key_repo
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo
from scud.db.repositories import users as user_repo
from scud.domain.permits import admin_revoke_permit

router = APIRouter(prefix="/permits")


def _audit(db, admin, action: str, target_id: str, details=None):
    db.add(AdminAuditLog(
        actor_type="api_key", actor_id=admin.api_key_id,
        action=action, target_type="permit", target_id=target_id, details=details,
    ))


def _status(p: Permit) -> str:
    if p.revoked_at:           return "revoked"
    if p.revoke_initiated_at:  return "revoking"
    return "active"


@router.get("", include_in_schema=False)
async def list_permits(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: Optional[int] = None,
    reader_id: Optional[str] = None,
    cursor: int = 0,
    limit: int = 50,
):
    rid_bytes = None
    if reader_id:
        try:
            rid_bytes = bytes.fromhex(reader_id)
        except ValueError:
            rid_bytes = None
    permits = await permit_repo.list_permits_admin(
        db, cursor=cursor, limit=limit, user_id=user_id, reader_id=rid_bytes
    )
    # Резолвим имя пользователя и ридера для каждой строки.
    user_ids = {p.user_id for p in permits}
    reader_ids = {p.reader_id for p in permits}
    users_map = {}
    if user_ids:
        for u in (await db.execute(select(User).where(User.user_id.in_(user_ids)))).scalars():
            users_map[u.user_id] = u
    readers_map = {}
    if reader_ids:
        for r in (await db.execute(select(Reader).where(Reader.reader_id.in_(reader_ids)))).scalars():
            readers_map[r.reader_id] = r

    return TEMPLATES.TemplateResponse(
        request,
        "permits/list.html",
        {
            "current_admin": admin,
            "permits": permits,
            "users_map": users_map,
            "readers_map": readers_map,
            "status_of": _status,
            "filter_user_id": user_id,
            "filter_reader_id": reader_id,
            "next_cursor": cursor + limit if len(permits) == limit else None,
        },
    )


@router.get("/new", include_in_schema=False)
async def new_permit_form(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: Optional[int] = None,
):
    from scud.domain.permit_templates import TEMPLATES as PRESETS, materialize

    users = (await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.user_id).limit(500)
    )).scalars().all()
    readers = (await db.execute(
        select(Reader).where(Reader.is_active.is_(True)).order_by(Reader.display_name).limit(500)
    )).scalars().all()

    # Materialize presets «здесь и сейчас» — это даёт юзеру preview ровно
    # тех дат, которые попадут в form-input при клике на preset (через JS).
    preset_previews = []
    for p in PRESETS:
        mat = materialize(p)
        preset_previews.append({
            "id": p.id,
            "label": p.label,
            "description": p.description,
            # ISO формат "YYYY-MM-DDTHH:MM" — то, что ждёт <input type="datetime-local">.
            "valid_from": mat["valid_from"].strftime("%Y-%m-%dT%H:%M"),
            "valid_until": mat["valid_until"].strftime("%Y-%m-%dT%H:%M"),
            "n_parallel": p.n_parallel,
            "max_token_ttl_hours": p.max_token_ttl_hours,
        })

    return TEMPLATES.TemplateResponse(
        request, "permits/new.html",
        {
            "current_admin": admin, "users": users, "readers": readers,
            "preselect_user_id": user_id,
            "presets": preset_previews,
        },
    )


@router.post("", include_in_schema=False)
async def create_permit(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    user_id: int = Form(...),
    reader_id_hex: str = Form(...),
    display_name: str = Form(..., max_length=128),
    description: str = Form(""),
    valid_from: str = Form(...),
    valid_until: str = Form(...),
    n_parallel: int = Form(1, ge=1, le=10),
    max_token_ttl_hours: int = Form(24, ge=1),
):
    try:
        reader_id = bytes.fromhex(reader_id_hex)
    except ValueError:
        return RedirectResponse(url="/admin/permits/new?error=Невалидный reader_id", status_code=303)
    if await user_repo.get_user_by_id(db, user_id) is None:
        return RedirectResponse(url="/admin/permits/new?error=Пользователь не найден", status_code=303)
    if await reader_repo.get_reader_by_id(db, reader_id) is None:
        return RedirectResponse(url="/admin/permits/new?error=Ридер не найден", status_code=303)

    try:
        vf = datetime.fromisoformat(valid_from)
        vu = datetime.fromisoformat(valid_until)
    except ValueError:
        return RedirectResponse(url="/admin/permits/new?error=Невалидная дата", status_code=303)

    permit = await permit_repo.create_permit(
        db, user_id=user_id, reader_id=reader_id,
        display_name=display_name, description=description or None,
        valid_from=vf, valid_until=vu,
        n_parallel=n_parallel,
        max_token_ttl_seconds=max_token_ttl_hours * 3600,
        created_by_api_key=admin.api_key_id,
    )
    _audit(db, admin, "create_permit", str(permit.permit_id))
    await db.commit()
    return RedirectResponse(url=f"/admin/permits/{permit.permit_id}?flash=Создан", status_code=303)


@router.get("/{permit_id}", include_in_schema=False)
async def permit_detail(
    request: Request, admin: CurrentAdmin, db: WebDb, permit_id: uuid.UUID
):
    permit = await permit_repo.get_permit_by_id(db, permit_id)
    if permit is None:
        raise HTTPException(status_code=404)
    user = await user_repo.get_user_by_id(db, permit.user_id)
    reader = await reader_repo.get_reader_by_id(db, permit.reader_id)
    keys = await key_repo.list_keys_for_permit(db, permit_id)
    active_count = sum(1 for k in keys if k.is_active)
    return TEMPLATES.TemplateResponse(
        request, "permits/detail.html",
        {
            "current_admin": admin,
            "permit": permit, "user": user, "reader": reader,
            "keys": keys, "active_count": active_count,
            "status": _status(permit),
        },
    )


@router.get("/bulk", include_in_schema=False)
async def bulk_permits_form(
    request: Request, admin: CurrentAdmin, db: WebDb
):
    """Форма bulk-выдачи: «user U × readers in group G» или «users in group GU × reader R».

    Это самый частый сценарий админа в реальной эксплуатации: например, «выдать
    всему этажу доступ к турникету в холле». Раньше каждый permit нужно было
    создавать вручную.
    """
    users = (await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.display_name)
    )).scalars().all()
    readers = (await db.execute(
        select(Reader).where(Reader.is_active.is_(True)).order_by(Reader.display_name)
    )).scalars().all()
    reader_groups = (await db.execute(
        select(ReaderGroup).order_by(ReaderGroup.name)
    )).scalars().all()
    # user_group_id хранится плоско в users; формируем уникальный set с count.
    ug_rows = (await db.execute(
        select(User.user_group_id, func.count(User.user_id))
        .where(User.is_active.is_(True))
        .group_by(User.user_group_id)
    )).all()
    user_groups = [{"id": str(gid), "count": cnt} for gid, cnt in ug_rows]
    return TEMPLATES.TemplateResponse(
        request, "permits/bulk.html",
        {
            "current_admin": admin,
            "users": users, "readers": readers,
            "reader_groups": reader_groups, "user_groups": user_groups,
        },
    )


@router.post("/bulk", include_in_schema=False)
async def bulk_permits_apply(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    mode: str = Form(...),                          # "user_x_group" | "group_x_reader"
    user_id: Optional[int] = Form(None),
    reader_group_id: Optional[uuid.UUID] = Form(None),
    user_group_id: Optional[uuid.UUID] = Form(None),
    reader_id_hex: Optional[str] = Form(None),
    display_name_template: str = Form(..., max_length=128),
    valid_from: str = Form(...),
    valid_until: str = Form(...),
    n_parallel: int = Form(1, ge=1, le=10),
    max_token_ttl_hours: int = Form(24, ge=1),
    skip_existing: bool = Form(True),
):
    """Создание batch permits в одной транзакции.

    `display_name_template` — поддерживает плейсхолдеры {user} и {reader}.
    `skip_existing` — пропустить пары (user_id, reader_id), для которых уже
    есть незавершённый permit. Защита от дублей при повторном клике.
    """
    try:
        vf = datetime.fromisoformat(valid_from)
        vu = datetime.fromisoformat(valid_until)
    except ValueError:
        return RedirectResponse(url="/admin/permits/bulk?error=Невалидная дата", status_code=303)

    # Resolve users + readers based on mode.
    target_users: list[User]
    target_readers: list[Reader]
    if mode == "user_x_group":
        if not user_id or not reader_group_id:
            return RedirectResponse(url="/admin/permits/bulk?error=user_id и reader_group_id обязательны", status_code=303)
        u = await user_repo.get_user_by_id(db, user_id)
        if u is None:
            return RedirectResponse(url="/admin/permits/bulk?error=Пользователь не найден", status_code=303)
        target_users = [u]
        target_readers = (await db.execute(
            select(Reader).where(Reader.reader_group_id == reader_group_id, Reader.is_active.is_(True))
        )).scalars().all()
    elif mode == "group_x_reader":
        if not user_group_id or not reader_id_hex:
            return RedirectResponse(url="/admin/permits/bulk?error=user_group_id и reader_id обязательны", status_code=303)
        try:
            rid = bytes.fromhex(reader_id_hex)
        except ValueError:
            return RedirectResponse(url="/admin/permits/bulk?error=Невалидный reader_id", status_code=303)
        reader = await reader_repo.get_reader_by_id(db, rid)
        if reader is None:
            return RedirectResponse(url="/admin/permits/bulk?error=Ридер не найден", status_code=303)
        target_readers = [reader]
        target_users = (await db.execute(
            select(User).where(User.user_group_id == user_group_id, User.is_active.is_(True))
        )).scalars().all()
    else:
        return RedirectResponse(url="/admin/permits/bulk?error=Bad mode", status_code=303)

    if not target_users or not target_readers:
        return RedirectResponse(
            url="/admin/permits/bulk?error=Пустой набор пользователей или ридеров", status_code=303
        )

    # Existing pairs for skip_existing.
    existing_pairs: set[tuple[int, bytes]] = set()
    if skip_existing:
        ex_rows = (await db.execute(
            select(Permit.user_id, Permit.reader_id).where(
                Permit.user_id.in_([u.user_id for u in target_users]),
                Permit.reader_id.in_([r.reader_id for r in target_readers]),
                Permit.revoked_at.is_(None),
            )
        )).all()
        existing_pairs = {(uid, rid) for uid, rid in ex_rows}

    created = 0
    skipped = 0
    for u in target_users:
        for r in target_readers:
            if skip_existing and (u.user_id, r.reader_id) in existing_pairs:
                skipped += 1
                continue
            name = (display_name_template
                    .replace("{user}", u.display_name)
                    .replace("{reader}", r.display_name))[:128]
            permit = await permit_repo.create_permit(
                db,
                user_id=u.user_id, reader_id=r.reader_id,
                display_name=name, description=None,
                valid_from=vf, valid_until=vu,
                n_parallel=n_parallel,
                max_token_ttl_seconds=max_token_ttl_hours * 3600,
                created_by_api_key=admin.api_key_id,
            )
            _audit(db, admin, "bulk_create_permit", str(permit.permit_id),
                   {"mode": mode, "u": u.user_id, "r": r.reader_id.hex()})
            created += 1
    await db.commit()
    return RedirectResponse(
        url=f"/admin/permits?flash=Создано:%20{created}%20(пропущено:%20{skipped})",
        status_code=303,
    )


@router.post("/{permit_id}/revoke", include_in_schema=False)
async def revoke_permit(
    request: Request, admin: CurrentAdmin, db: WebDb, permit_id: uuid.UUID
):
    try:
        reader_ids = await admin_revoke_permit(db, permit_id)
    except LookupError:
        raise HTTPException(status_code=404)

    from scud.db.repositories import tasks as task_repo
    for rid in reader_ids:
        await task_repo.enqueue_generate_filter_debounced(db, rid.hex())
    _audit(db, admin, "admin_revoke_permit", str(permit_id))
    await db.commit()
    return RedirectResponse(
        url=f"/admin/permits/{permit_id}?flash=Revoke%20запущен", status_code=303
    )
