"""Admin keys endpoints (§5.7)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import AdminAuditLog
from scud.db.repositories import keys as key_repo
from scud.domain.keys import admin_revoke_key

router = APIRouter(prefix="/keys", tags=["admin-keys"])


async def _audit(db, api_key, action: str, target_id: str) -> None:
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=api_key.api_key_id,
            action=action,
            target_type="issued_key",
            target_id=target_id,
        )
    )


@router.get("")
async def list_keys(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
    permit_id: Optional[uuid.UUID] = Query(None),
    user_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
) -> dict:
    keys = await key_repo.list_keys_admin(
        db,
        cursor=cursor,
        limit=limit,
        permit_id=permit_id,
        user_id=user_id,
        status=status_filter,
    )
    return {
        "items": [
            {
                "key_id": k.key_id.hex(),
                "permit_id": str(k.permit_id),
                "status": k.status,
                "issued_at": k.issued_at.isoformat(),
                "expires_at": k.expires_at.isoformat(),
                "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            }
            for k in keys
        ]
    }


@router.get("/{key_id}")
async def get_key(key_id: str, api_key: AdminApiKey, db: DbSession) -> dict:
    import base64
    try:
        kid = bytes.fromhex(key_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid hex")
    key = await key_repo.get_key_by_id(db, kid)
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")
    return {
        "key_id": key.key_id.hex(),
        "permit_id": str(key.permit_id),
        "reader_id": key.reader_id.hex(),
        "device_id": str(key.device_id),
        "status": key.status,
        "serial": key.serial,
        "issued_at": key.issued_at.isoformat(),
        "expires_at": key.expires_at.isoformat(),
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
        "revoke_reason": key.revoke_reason,
        "committed_filter_version": key.committed_filter_version,
        "full_key_bytes": base64.b64encode(key.full_key_bytes).decode(),
    }


@router.post("/{key_id}/revoke")
async def revoke_key(key_id: str, api_key: AdminApiKey, db: DbSession) -> dict:
    try:
        await admin_revoke_key(db, key_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")
    await _audit(db, api_key, "admin_revoke_key", key_id)
    await db.commit()
    return {"ok": True}
