"""Admin reader-groups endpoints (§5.7)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import AdminAuditLog
from scud.db.repositories import readers as reader_repo

router = APIRouter(prefix="/reader-groups", tags=["admin-reader-groups"])


async def _audit(db, api_key, action: str, target_id: str, details: dict = None) -> None:
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=api_key.api_key_id,
            action=action,
            target_type="reader_group",
            target_id=target_id,
            details=details,
        )
    )


class CreateGroupRequest(BaseModel):
    name: str
    description: Optional[str] = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_group(body: CreateGroupRequest, api_key: AdminApiKey, db: DbSession) -> dict:
    group = await reader_repo.create_group(db, body.name, body.description)
    await _audit(db, api_key, "create_reader_group", str(group.group_id))
    await db.commit()
    return {"group_id": str(group.group_id)}


@router.get("")
async def list_groups(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
) -> dict:
    groups = await reader_repo.list_groups(db, cursor=cursor, limit=limit)
    return {
        "items": [
            {
                "group_id": str(g.group_id),
                "name": g.name,
                "description": g.description,
                "created_at": g.created_at.isoformat(),
            }
            for g in groups
        ]
    }


class PatchGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@router.patch("/{group_id}")
async def patch_group(
    group_id: uuid.UUID,
    body: PatchGroupRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    group = await reader_repo.get_group_by_id(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group_not_found")
    updates = body.model_dump(exclude_none=True)
    if updates:
        await reader_repo.update_group(db, group_id, **updates)
    await _audit(db, api_key, "patch_reader_group", str(group_id), updates)
    await db.commit()
    return {"ok": True}


@router.delete("/{group_id}")
async def delete_group(
    group_id: uuid.UUID, api_key: AdminApiKey, db: DbSession
) -> dict:
    group = await reader_repo.get_group_by_id(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group_not_found")
    deleted = await reader_repo.delete_group(db, group_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="group_has_readers"
        )
    await _audit(db, api_key, "delete_reader_group", str(group_id))
    await db.commit()
    return {"ok": True}
