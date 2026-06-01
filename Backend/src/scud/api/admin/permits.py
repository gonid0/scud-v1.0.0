"""Admin permits endpoints (§5.7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import AdminAuditLog
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo
from scud.db.repositories import users as user_repo
from scud.domain.permits import admin_revoke_permit

router = APIRouter(prefix="/permits", tags=["admin-permits"])


def _permit_status(permit) -> str:
    """Derived state: active / revoking / revoked.

    "revoking" — двухфазный admin-revoke в Phase 1: revoke_initiated_at стоит,
    но revoked_at пока NULL (ждём пока ридер применит свежий bloom и ключи
    permit'а перейдут is_active=False).
    """
    if permit.revoked_at is not None:
        return "revoked"
    if permit.revoke_initiated_at is not None:
        return "revoking"
    return "active"


async def _audit(db, api_key, action: str, target_id: str, details: dict = None) -> None:
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=api_key.api_key_id,
            action=action,
            target_type="permit",
            target_id=target_id,
            details=details,
        )
    )


class CreatePermitRequest(BaseModel):
    user_id: int
    reader_id: str  # hex
    display_name: str
    description: Optional[str] = None
    valid_from: datetime
    valid_until: datetime
    n_parallel: int = 1
    max_token_ttl_seconds: int = 86400
    # Пожизненный потолок выпуска ключей по permit'у. None = без лимита.
    # ge=1: 0 совпал бы с «нет лимита» по смыслу, но навсегда заблокировал бы выпуск
    # (count >= 0 всегда истинно), поэтому 0/отрицательные значения запрещаем.
    max_total_issued: Optional[int] = Field(default=None, ge=1)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_permit(
    body: CreatePermitRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    try:
        reader_id = bytes.fromhex(body.reader_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid reader_id hex")

    user = await user_repo.get_user_by_id(db, body.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    reader = await reader_repo.get_reader_by_id(db, reader_id)
    if reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    permit = await permit_repo.create_permit(
        db,
        user_id=body.user_id,
        reader_id=reader_id,
        display_name=body.display_name,
        description=body.description,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        n_parallel=body.n_parallel,
        max_token_ttl_seconds=body.max_token_ttl_seconds,
        max_total_issued=body.max_total_issued,
        created_by_api_key=api_key.api_key_id,
    )
    await _audit(db, api_key, "create_permit", str(permit.permit_id))
    await db.commit()
    return {"permit_id": str(permit.permit_id)}


@router.get("")
async def list_permits(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
    user_id: Optional[int] = Query(None),
    reader_id: Optional[str] = Query(None),
) -> dict:
    reader_id_bytes = bytes.fromhex(reader_id) if reader_id else None
    permits = await permit_repo.list_permits_admin(
        db, cursor=cursor, limit=limit, user_id=user_id, reader_id=reader_id_bytes
    )
    return {
        "items": [
            {
                "permit_id": str(p.permit_id),
                "user_id": p.user_id,
                "reader_id": p.reader_id.hex(),
                "display_name": p.display_name,
                "valid_from": p.valid_from.isoformat(),
                "valid_until": p.valid_until.isoformat(),
                "n_parallel": p.n_parallel,
                "status": _permit_status(p),
                "revoke_initiated_at": p.revoke_initiated_at.isoformat() if p.revoke_initiated_at else None,
                "revoked_at": p.revoked_at.isoformat() if p.revoked_at else None,
            }
            for p in permits
        ]
    }


@router.get("/templates")
async def list_templates(
    api_key: AdminApiKey,
) -> dict:
    """Список доступных permit-шаблонов (день/неделя/месяц/год/...).

    Не зависит от БД — статический каталог из domain/permit_templates.py.
    Удобен для UI dropdown'ов и интеграций, которые хотят выдавать «месячный
    пропуск» без вычисления дат руками.

    ВАЖНО: этот маршрут должен быть ОБЪЯВЛЕН ВЫШЕ `/{permit_id}` — иначе
    FastAPI попытается распарсить "templates" как UUID и отдаст 422.
    """
    from scud.domain.permit_templates import TEMPLATES
    return {
        "items": [
            {
                "id": t.id,
                "label": t.label,
                "description": t.description,
                "duration_days": t.duration_days,
                "n_parallel": t.n_parallel,
                "max_token_ttl_hours": t.max_token_ttl_hours,
            }
            for t in TEMPLATES
        ],
    }


class IssueFromTemplateRequest(BaseModel):
    user_id: int
    reader_id: str  # hex
    template_id: str
    display_name: str
    description: Optional[str] = None


@router.post("/issue-from-template", status_code=status.HTTP_201_CREATED)
async def issue_from_template(
    body: IssueFromTemplateRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    """Создать permit из template одной командой.

    Сервер сам вычисляет valid_from/until из шаблона и текущего времени —
    клиенту не нужно беспокоиться о часовых поясах или високосных годах.
    """
    from scud.domain.permit_templates import get_template, materialize

    template = get_template(body.template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"template_not_found:{body.template_id}",
        )

    try:
        reader_id = bytes.fromhex(body.reader_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid reader_id hex",
        )

    user = await user_repo.get_user_by_id(db, body.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    reader = await reader_repo.get_reader_by_id(db, reader_id)
    if reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    params = materialize(template)
    permit = await permit_repo.create_permit(
        db,
        user_id=body.user_id,
        reader_id=reader_id,
        display_name=body.display_name,
        description=body.description,
        valid_from=params["valid_from"],
        valid_until=params["valid_until"],
        n_parallel=params["n_parallel"],
        max_token_ttl_seconds=params["max_token_ttl_seconds"],
        created_by_api_key=api_key.api_key_id,
    )
    await _audit(db, api_key, "create_permit_from_template",
                 str(permit.permit_id), {"template": body.template_id})
    await db.commit()
    return {
        "permit_id": str(permit.permit_id),
        "valid_from": params["valid_from"].isoformat(),
        "valid_until": params["valid_until"].isoformat(),
        "template": body.template_id,
    }


@router.get("/{permit_id}")
async def get_permit(
    permit_id: uuid.UUID, api_key: AdminApiKey, db: DbSession
) -> dict:
    permit = await permit_repo.get_permit_by_id(db, permit_id)
    if permit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="permit_not_found")

    from scud.db.repositories import keys as key_repo
    keys = await key_repo.list_keys_for_permit(db, permit_id)
    active_keys_count = sum(1 for k in keys if k.is_active)

    return {
        "permit_id": str(permit.permit_id),
        "user_id": permit.user_id,
        "reader_id": permit.reader_id.hex(),
        "display_name": permit.display_name,
        "description": permit.description,
        "valid_from": permit.valid_from.isoformat(),
        "valid_until": permit.valid_until.isoformat(),
        "n_parallel": permit.n_parallel,
        "max_token_ttl_seconds": permit.max_token_ttl_seconds,
        "max_total_issued": permit.max_total_issued,
        "status": _permit_status(permit),
        "revoke_initiated_at": permit.revoke_initiated_at.isoformat() if permit.revoke_initiated_at else None,
        "revoked_at": permit.revoked_at.isoformat() if permit.revoked_at else None,
        "active_keys_count": active_keys_count,
        "keys": [
            {
                "key_id": k.key_id.hex(),
                "status": k.status,
                "is_active": k.is_active,
                "issued_at": k.issued_at.isoformat(),
                "expires_at": k.expires_at.isoformat(),
            }
            for k in keys
        ],
    }


class PatchPermitRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    n_parallel: Optional[int] = None
    max_token_ttl_seconds: Optional[int] = None
    # ge=1 как и при создании; None (если явно прислан) = снять лимит (см. patch ниже).
    max_total_issued: Optional[int] = Field(default=None, ge=1)


@router.patch("/{permit_id}")
async def patch_permit(
    permit_id: uuid.UUID,
    body: PatchPermitRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    permit = await permit_repo.get_permit_by_id(db, permit_id)
    if permit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="permit_not_found")
    updates = body.model_dump(exclude_none=True)
    # exclude_none не даёт сбросить лимит обратно в NULL (None = «поле опущено»).
    # Если клиент ЯВНО прислал max_total_issued (в т.ч. null) — пробрасываем его,
    # чтобы лимит можно было снять (вернуть к «без лимита»).
    if "max_total_issued" in body.model_fields_set:
        updates["max_total_issued"] = body.max_total_issued
    if updates:
        from sqlalchemy import update
        from scud.db.models import Permit
        await db.execute(update(Permit).where(Permit.permit_id == permit_id).values(**updates))
    await _audit(db, api_key, "patch_permit", str(permit_id), updates)
    await db.commit()
    return {"ok": True}


@router.post("/{permit_id}/revoke")
async def admin_revoke(
    permit_id: uuid.UUID,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    try:
        reader_ids = await admin_revoke_permit(db, permit_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="permit_not_found")

    # Enqueue filter generation for affected readers
    from scud.db.repositories import tasks as task_repo
    for rid in reader_ids:
        await task_repo.enqueue_generate_filter_debounced(db, rid.hex())

    await _audit(db, api_key, "admin_revoke_permit", str(permit_id))
    await db.commit()

    # Перечитаем permit, чтобы вернуть актуальный статус: "revoking" если
    # ещё есть active ключи, "revoked" — если финализировали сразу.
    permit = await permit_repo.get_permit_by_id(db, permit_id)
    return {
        "ok": True,
        "status": _permit_status(permit) if permit else "revoked",
        "revoke_initiated_at": permit.revoke_initiated_at.isoformat()
        if permit and permit.revoke_initiated_at else None,
        "revoked_at": permit.revoked_at.isoformat()
        if permit and permit.revoked_at else None,
    }
