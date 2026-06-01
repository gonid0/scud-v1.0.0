"""FastAPI dependency injectors for authentication."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import ApiKey, Session as SessionModel, User
from scud.db.session import get_session


async def get_current_session(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> SessionModel:
    """Validate Bearer token and return the active session."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_token")

    token = authorization[7:]
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(SessionModel).where(
            SessionModel.session_token == token,
            SessionModel.revoked_at.is_(None),
            SessionModel.session_expires_at > now,
        )
    )
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    return sess


async def get_current_user(
    sess: Annotated[SessionModel, Depends(get_current_session)],
    db: AsyncSession = Depends(get_session),
) -> User:
    user = await db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_inactive")
    return user


async def get_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
    session: AsyncSession = Depends(get_session),
) -> ApiKey:
    """Validate X-Api-Key header and return api_key row."""
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_api_key")

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key")
    if api_key.expires_at is not None and api_key.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="api_key_expired")

    # Update last_used_at (flush only; caller controls commit)
    await session.execute(
        update(ApiKey)
        .where(ApiKey.api_key_id == api_key.api_key_id)
        .values(last_used_at=now)
    )
    await session.flush()
    return api_key


async def get_admin_api_key(
    api_key: Annotated[ApiKey, Depends(get_api_key)],
) -> ApiKey:
    """Extend get_api_key: require kind == 'admin'.

    BE-SEC-02: 'integration' keys are valid API keys but must NOT access admin
    management endpoints. Raises HTTP 403 (not 401) so callers can distinguish
    "authenticated but insufficiently privileged" from "not authenticated at all".
    """
    if api_key.kind != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin_key_required",
        )
    return api_key


# Type aliases for dependency injection
CurrentSession = Annotated[SessionModel, Depends(get_current_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
# Any valid API key (admin or integration) — use for read-only / integration endpoints
AdminApiKey = Annotated[ApiKey, Depends(get_admin_api_key)]
# Backwards-compatible alias kept for clarity; all admin routers already import AdminApiKey
DbSession = Annotated[AsyncSession, Depends(get_session)]
