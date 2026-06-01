"""Admin API keys management (§5.7)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import AdminAuditLog, ApiKey

router = APIRouter(prefix="/api-keys", tags=["admin-api-keys"])


async def _audit(db, api_key, action: str, target_id: str) -> None:
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=api_key.api_key_id,
            action=action,
            target_type="api_key",
            target_id=target_id,
        )
    )


class CreateApiKeyRequest(BaseModel):
    name: str
    kind: str  # "admin" | "integration"
    expires_at: Optional[datetime] = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    if body.kind not in ("admin", "integration"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid kind")

    prefix = "sk_admin_" if body.kind == "admin" else "sk_integration_"
    plaintext = prefix + secrets.token_urlsafe(36)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    key_prefix = plaintext[:8]

    new_key = ApiKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        kind=body.kind,
        name=body.name,
        created_by=None,
        expires_at=body.expires_at,
    )
    db.add(new_key)
    await db.flush()
    await db.refresh(new_key)
    await _audit(db, api_key, "create_api_key", str(new_key.api_key_id))
    await db.commit()

    return {
        "api_key_id": str(new_key.api_key_id),
        "plaintext": plaintext,
        "key_prefix": key_prefix,
        "name": body.name,
        "expires_at": body.expires_at.isoformat() if body.expires_at else None,
    }


@router.get("")
async def list_api_keys(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
) -> dict:
    from sqlalchemy import select
    result = await db.execute(
        select(ApiKey)
        .offset(cursor)
        .limit(limit)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return {
        "items": [
            {
                "api_key_id": str(k.api_key_id),
                "key_prefix": k.key_prefix,
                "kind": k.kind,
                "name": k.name,
                "created_at": k.created_at.isoformat(),
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            }
            for k in keys
        ]
    }


@router.post("/{api_key_id}/revoke")
async def revoke_api_key(
    api_key_id: uuid.UUID,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    from datetime import timezone
    now = datetime.now(timezone.utc)
    await db.execute(
        update(ApiKey)
        .where(ApiKey.api_key_id == api_key_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await _audit(db, api_key, "revoke_api_key", str(api_key_id))
    await db.commit()
    return {"ok": True}
