"""App permits endpoints (§5.2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from scud.api.deps import CurrentSession, CurrentUser, DbSession
from scud.db.repositories import devices as dev_repo
from scud.db.repositories import grants as grant_repo
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo
from scud.domain.permits import revoke_permit_by_user

router = APIRouter(prefix="/permits", tags=["app-permits"])


class PermitListItem(BaseModel):
    permit_id: str
    display_name: str
    description: str | None
    reader_id: str
    reader_display_name: str
    valid_from: str
    valid_until: str
    n_parallel: int
    max_token_ttl_seconds: int
    max_total_issued: int | None
    active_keys_count: int
    has_grant_for_device: bool


@router.get("", response_model=dict)
async def list_permits(
    current_user: CurrentUser,
    current_session: CurrentSession,
    db: DbSession,
) -> dict:
    permits_raw = await permit_repo.list_permits_for_user(db, current_user.user_id)
    items = []
    for p in permits_raw:
        reader = await reader_repo.get_reader_by_id(db, p.reader_id)
        active_count = await permit_repo.count_active_keys(db, p.permit_id)

        has_grant = False
        if current_session.device_id:
            device = await dev_repo.get_device_by_id(db, current_session.device_id)
            if device:
                grant = await grant_repo.get_active_grant_for_permit_pubkey(
                    db, p.permit_id, device.phone_pubkey
                )
                has_grant = grant is not None

        items.append(
            PermitListItem(
                permit_id=str(p.permit_id),
                display_name=p.display_name,
                description=p.description,
                reader_id=p.reader_id.hex(),
                reader_display_name=reader.display_name if reader else "",
                valid_from=p.valid_from.isoformat(),
                valid_until=p.valid_until.isoformat(),
                n_parallel=p.n_parallel,
                max_token_ttl_seconds=p.max_token_ttl_seconds,
                max_total_issued=p.max_total_issued,
                active_keys_count=active_count,
                has_grant_for_device=has_grant,
            )
        )
    return {"items": [i.model_dump() for i in items]}


class KeyItem(BaseModel):
    key_id: str
    device_id: str
    device_label: str | None
    is_current_device: bool
    issued_at: str
    expires_at: str
    status: str
    full_key_bytes: str


@router.get("/{permit_id}/keys", response_model=dict)
async def list_permit_keys(
    permit_id: uuid.UUID,
    current_user: CurrentUser,
    current_session: CurrentSession,
    db: DbSession,
) -> dict:
    permit = await permit_repo.get_permit_by_id(db, permit_id)
    if permit is None or permit.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_owner")

    import base64
    from scud.db.repositories import keys as key_repo
    from scud.db.repositories import devices as dev_repo

    keys = await key_repo.list_keys_for_permit(db, permit_id)
    # Возвращаем ВСЕ ключи permit'а — клиент сам решает, показать архив или
    # только активные. Иначе ключи, выпущенные с других устройств того же
    # пользователя, не попадают на экран (если они не active — скрываются).
    items = []
    for k in keys:
        device = await dev_repo.get_device_by_id(db, k.device_id)
        items.append(
            KeyItem(
                key_id=k.key_id.hex(),
                device_id=str(k.device_id),
                device_label=device.device_label if device else None,
                is_current_device=(k.device_id == current_session.device_id),
                issued_at=k.issued_at.isoformat(),
                expires_at=k.expires_at.isoformat(),
                status=k.status,
                full_key_bytes=base64.b64encode(k.full_key_bytes).decode(),
            )
        )
    return {"items": [i.model_dump() for i in items]}


@router.post("/{permit_id}/revoke")
async def revoke_permit(
    permit_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    try:
        await revoke_permit_by_user(db, permit_id, current_user.user_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_owner")
    except ValueError as e:
        msg = str(e)
        if "has_active_keys" in msg:
            count = int(msg.split(":")[1])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "has_active_keys", "active_count": count},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    await db.commit()
    return {"ok": True}
