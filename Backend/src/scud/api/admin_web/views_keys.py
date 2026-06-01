"""Issued keys: list (with filters), detail, force-revoke."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import AdminAuditLog, IssuedKey, Permit, Reader, User, UserDevice
from scud.db.repositories import keys as key_repo
from scud.domain.keys import admin_revoke_key

router = APIRouter(prefix="/keys")


def _audit(db, admin, action, target_id):
    db.add(AdminAuditLog(
        actor_type="api_key", actor_id=admin.api_key_id,
        action=action, target_type="issued_key", target_id=target_id,
    ))


@router.get("", include_in_schema=False)
async def list_keys(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    status: Optional[str] = None,
    permit_id: Optional[uuid.UUID] = None,
    user_id: Optional[int] = None,
    cursor: int = 0,
    limit: int = 50,
):
    keys = await key_repo.list_keys_admin(
        db, cursor=cursor, limit=limit, status=status, permit_id=permit_id, user_id=user_id
    )
    # Дополнительные lookups для UX (имя ридера + название permit).
    permit_ids = {k.permit_id for k in keys}
    reader_ids = {k.reader_id for k in keys}
    permits_map = {}
    if permit_ids:
        for p in (await db.execute(select(Permit).where(Permit.permit_id.in_(permit_ids)))).scalars():
            permits_map[p.permit_id] = p
    readers_map = {}
    if reader_ids:
        for r in (await db.execute(select(Reader).where(Reader.reader_id.in_(reader_ids)))).scalars():
            readers_map[r.reader_id] = r

    return TEMPLATES.TemplateResponse(
        request,
        "keys/list.html",
        {
            "current_admin": admin,
            "keys": keys,
            "permits_map": permits_map,
            "readers_map": readers_map,
            "filter_status": status,
            "filter_permit_id": permit_id,
            "filter_user_id": user_id,
            "next_cursor": cursor + limit if len(keys) == limit else None,
        },
    )


@router.get("/{key_id_hex}", include_in_schema=False)
async def key_detail(
    request: Request, admin: CurrentAdmin, db: WebDb, key_id_hex: str
):
    try:
        key_id = bytes.fromhex(key_id_hex)
    except ValueError:
        raise HTTPException(status_code=400)
    key = await db.get(IssuedKey, key_id)
    if key is None:
        raise HTTPException(status_code=404)

    permit = await db.get(Permit, key.permit_id)
    reader = await db.get(Reader, key.reader_id)
    device = await db.get(UserDevice, key.device_id)
    user = await db.get(User, permit.user_id) if permit else None

    return TEMPLATES.TemplateResponse(
        request, "keys/detail.html",
        {
            "current_admin": admin,
            "key": key, "permit": permit, "reader": reader,
            "device": device, "user": user,
        },
    )


@router.post("/{key_id_hex}/revoke", include_in_schema=False)
async def revoke_key_action(
    request: Request, admin: CurrentAdmin, db: WebDb, key_id_hex: str
):
    try:
        await admin_revoke_key(db, key_id_hex)
    except LookupError:
        raise HTTPException(status_code=404)
    _audit(db, admin, "admin_revoke_key", key_id_hex)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/keys/{key_id_hex}?flash=Revoked", status_code=303
    )
