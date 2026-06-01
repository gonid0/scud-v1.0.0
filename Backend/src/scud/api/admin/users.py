"""Admin users endpoints (§5.7)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import AdminAuditLog
from scud.db.repositories import users as user_repo
from scud.domain.auth import hash_password

router = APIRouter(prefix="/users", tags=["admin-users"])


async def _audit(db, api_key, action: str, target_id: str, details: dict = None) -> None:
    log = AdminAuditLog(
        actor_type="api_key",
        actor_id=api_key.api_key_id,
        action=action,
        target_type="user",
        target_id=target_id,
        details=details,
    )
    db.add(log)


class CreateUserRequest(BaseModel):
    login: str
    password: str
    display_name: str
    user_group_id: uuid.UUID


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    existing = await user_repo.get_user_by_login(db, body.login)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="login_taken")
    pw_hash = hash_password(body.password)
    user = await user_repo.create_user(
        db, body.login, pw_hash, body.display_name, body.user_group_id
    )
    await _audit(db, api_key, "create_user", str(user.user_id))
    await db.commit()
    return {"user_id": user.user_id}


@router.get("")
async def list_users(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
) -> dict:
    users = await user_repo.list_users(db, cursor=cursor, limit=limit)
    return {
        "items": [
            {
                "user_id": u.user_id,
                "login": u.login,
                "display_name": u.display_name,
                "user_group_id": str(u.user_group_id),
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ]
    }


@router.get("/{user_id}")
async def get_user(user_id: int, api_key: AdminApiKey, db: DbSession) -> dict:
    user = await user_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    return {
        "user_id": user.user_id,
        "login": user.login,
        "display_name": user.display_name,
        "user_group_id": str(user.user_group_id),
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


class PatchUserRequest(BaseModel):
    display_name: Optional[str] = None
    user_group_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None


@router.patch("/{user_id}")
async def patch_user(
    user_id: int,
    body: PatchUserRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    user = await user_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    updates = body.model_dump(exclude_none=True)
    if updates:
        await user_repo.update_user(db, user_id, **updates)

    if body.is_active is False:
        await user_repo.revoke_user_sessions(db, user_id)

    await _audit(db, api_key, "patch_user", str(user_id), updates)
    await db.commit()
    return {"ok": True}


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    user = await user_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    pw_hash = hash_password(body.new_password)
    await user_repo.update_user(db, user_id, password_hash=pw_hash)
    await user_repo.revoke_user_sessions(db, user_id)
    await _audit(db, api_key, "reset_password", str(user_id))
    await db.commit()
    return {"ok": True}
