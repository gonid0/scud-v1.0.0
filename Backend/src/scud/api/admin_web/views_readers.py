"""Reader groups + readers (enroll, list, detail)."""

from __future__ import annotations

import base64
import uuid
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from nacl.public import PrivateKey as XPrivateKey
from nacl.signing import SigningKey
from sqlalchemy import func, select

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import (
    AdminAuditLog,
    DeliveryTask,
    PassageEvent,
    Reader,
    ReaderGroup,
)
from scud.db.repositories import readers as reader_repo

router = APIRouter()


def _audit(db, admin, action: str, target_type: str, target_id: str, details=None):
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=admin.api_key_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    )


# ----------------------------------------------------------------------------
# Reader groups
# ----------------------------------------------------------------------------

@router.get("/reader-groups", include_in_schema=False)
async def list_groups(request: Request, admin: CurrentAdmin, db: WebDb):
    groups = (await db.execute(
        select(ReaderGroup).order_by(ReaderGroup.created_at)
    )).scalars().all()

    # Считаем количество ридеров в каждой группе одним запросом.
    counts_rows = (await db.execute(
        select(Reader.reader_group_id, func.count(Reader.reader_id))
        .group_by(Reader.reader_group_id)
    )).all()
    reader_counts = {gid: cnt for gid, cnt in counts_rows}

    return TEMPLATES.TemplateResponse(
        request,
        "readers/groups_list.html",
        {
            "current_admin": admin,
            "groups": groups,
            "reader_counts": reader_counts,
        },
    )


@router.get("/reader-groups/new", include_in_schema=False)
async def new_group_form(request: Request, admin: CurrentAdmin):
    return TEMPLATES.TemplateResponse(
        request, "readers/group_new.html", {"current_admin": admin}
    )


@router.post("/reader-groups", include_in_schema=False)
async def create_group(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    name: str = Form(..., max_length=128),
    description: str = Form(""),
):
    group = await reader_repo.create_group(db, name, description or None)
    _audit(db, admin, "create_reader_group", "reader_group", str(group.group_id))
    await db.commit()
    return RedirectResponse(url="/admin/reader-groups?flash=Создана", status_code=303)


@router.post("/reader-groups/{group_id}/delete", include_in_schema=False)
async def delete_group_action(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    group_id: uuid.UUID,
):
    ok = await reader_repo.delete_group(db, group_id)
    if not ok:
        return RedirectResponse(
            url="/admin/reader-groups?error=В группе есть ридеры", status_code=303
        )
    _audit(db, admin, "delete_reader_group", "reader_group", str(group_id))
    await db.commit()
    return RedirectResponse(url="/admin/reader-groups?flash=Удалена", status_code=303)


# ----------------------------------------------------------------------------
# Readers
# ----------------------------------------------------------------------------

@router.get("/readers", include_in_schema=False)
async def list_readers(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    group_id: Optional[uuid.UUID] = None,
    q: Optional[str] = None,
    cursor: int = 0,
    limit: int = 50,
):
    stmt = select(Reader).order_by(Reader.display_name)
    if group_id:
        stmt = stmt.where(Reader.reader_group_id == group_id)
    if q:
        like = f"%{q.lower()}%"
        # ilike портабельно: на SQLite это case-insensitive LIKE.
        stmt = stmt.where(Reader.display_name.ilike(like))
    stmt = stmt.offset(cursor).limit(limit)

    readers = (await db.execute(stmt)).scalars().all()
    groups = (await db.execute(select(ReaderGroup))).scalars().all()
    group_by_id = {g.group_id: g for g in groups}

    next_cursor = cursor + limit if len(readers) == limit else None

    return TEMPLATES.TemplateResponse(
        request,
        "readers/list.html",
        {
            "current_admin": admin,
            "readers": readers,
            "groups": groups,
            "group_by_id": group_by_id,
            "filter_group_id": group_id,
            "filter_q": q,
            "next_cursor": next_cursor,
        },
    )


@router.get("/readers/new", include_in_schema=False)
async def new_reader_form(request: Request, admin: CurrentAdmin, db: WebDb):
    groups = (await db.execute(select(ReaderGroup))).scalars().all()
    return TEMPLATES.TemplateResponse(
        request,
        "readers/new.html",
        {"current_admin": admin, "groups": groups},
    )


@router.post("/readers/enroll", include_in_schema=False)
async def enroll_reader(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    reader_id_hex: str = Form(..., min_length=32, max_length=32),
    reader_pubkey_b64: str = Form(...),
    reader_group_id: uuid.UUID = Form(...),
    display_name: str = Form(..., max_length=128),
    description: str = Form(""),
):
    try:
        reader_id_bytes = bytes.fromhex(reader_id_hex)
    except ValueError:
        return RedirectResponse(url="/admin/readers/new?error=Невалидный reader_id hex", status_code=303)
    if len(reader_id_bytes) != 16:
        return RedirectResponse(url="/admin/readers/new?error=reader_id должен быть 16 байт", status_code=303)
    try:
        reader_pubkey = base64.b64decode(reader_pubkey_b64)
    except Exception:
        return RedirectResponse(url="/admin/readers/new?error=Невалидный base64 pubkey", status_code=303)
    if len(reader_pubkey) != 32:
        return RedirectResponse(url="/admin/readers/new?error=reader_pubkey должен быть 32 байта", status_code=303)
    if await reader_repo.get_reader_by_id(db, reader_id_bytes):
        return RedirectResponse(url="/admin/readers/new?error=reader_id уже существует", status_code=303)
    if await reader_repo.get_group_by_id(db, reader_group_id) is None:
        return RedirectResponse(url="/admin/readers/new?error=Группа не найдена", status_code=303)

    ed = SigningKey.generate()
    x = XPrivateKey.generate()
    reader = Reader(
        reader_id=reader_id_bytes,
        reader_group_id=reader_group_id,
        display_name=display_name,
        description=description or None,
        reader_pubkey=reader_pubkey,
        server_ed25519_privkey=bytes(ed),
        server_ed25519_pubkey=bytes(ed.verify_key),
        server_x25519_privkey=bytes(x),
        server_x25519_pubkey=bytes(x.public_key),
        is_active=True,
    )
    await reader_repo.insert_reader(db, reader)
    _audit(db, admin, "enroll_reader", "reader", reader_id_hex)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/readers/{reader_id_hex}?flash=Enrolled", status_code=303
    )


@router.get("/readers/{reader_id_hex}", include_in_schema=False)
async def reader_detail(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    reader_id_hex: str,
):
    try:
        rid = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad hex")
    reader = await reader_repo.get_reader_by_id(db, rid)
    if reader is None:
        raise HTTPException(status_code=404)

    group = await reader_repo.get_group_by_id(db, reader.reader_group_id)

    open_tasks = int((await db.execute(
        select(func.count()).select_from(DeliveryTask).where(
            DeliveryTask.reader_id == rid, DeliveryTask.completed_at.is_(None)
        )
    )).scalar_one() or 0)

    passages_total = int((await db.execute(
        select(func.count()).select_from(PassageEvent).where(PassageEvent.reader_id == rid)
    )).scalar_one() or 0)

    recent_passages = (await db.execute(
        select(PassageEvent).where(PassageEvent.reader_id == rid)
        .order_by(PassageEvent.passed_at.desc()).limit(10)
    )).scalars().all()

    return TEMPLATES.TemplateResponse(
        request,
        "readers/detail.html",
        {
            "current_admin": admin,
            "reader": reader,
            "group": group,
            "server_ed_pub_b64": base64.b64encode(reader.server_ed25519_pubkey).decode(),
            "server_x_pub_b64": base64.b64encode(reader.server_x25519_pubkey).decode(),
            "open_tasks": open_tasks,
            "passages_total": passages_total,
            "recent_passages": recent_passages,
        },
    )


@router.post("/readers/{reader_id_hex}/toggle-active", include_in_schema=False)
async def toggle_reader(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    reader_id_hex: str,
):
    try:
        rid = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=400)
    reader = await reader_repo.get_reader_by_id(db, rid)
    if reader is None:
        raise HTTPException(status_code=404)
    reader.is_active = not reader.is_active
    _audit(db, admin, "toggle_reader_active", "reader", reader_id_hex,
           {"is_active": reader.is_active})
    await db.commit()
    return RedirectResponse(url=f"/admin/readers/{reader_id_hex}?flash=Обновлено", status_code=303)
