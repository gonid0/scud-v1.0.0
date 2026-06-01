from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import User


async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    return await session.get(User, user_id)


async def get_user_by_login(session: AsyncSession, login: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.login == login))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    login: str,
    password_hash: str,
    display_name: str,
    user_group_id: uuid.UUID,
) -> User:
    user = User(
        login=login,
        password_hash=password_hash,
        display_name=display_name,
        user_group_id=user_group_id,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def list_users(
    session: AsyncSession,
    cursor: int = 0,
    limit: int = 50,
) -> list[User]:
    result = await session.execute(
        select(User).where(User.user_id > cursor).order_by(User.user_id).limit(limit)
    )
    return list(result.scalars().all())


async def update_user(
    session: AsyncSession,
    user_id: int,
    **kwargs,
) -> Optional[User]:
    from datetime import datetime, timezone
    kwargs["updated_at"] = datetime.now(timezone.utc)
    await session.execute(
        update(User).where(User.user_id == user_id).values(**kwargs)
    )
    return await session.get(User, user_id)


async def revoke_user_sessions(session: AsyncSession, user_id: int) -> None:
    from datetime import datetime, timezone
    from scud.db.models import Session as SessionModel
    now = datetime.now(timezone.utc)
    await session.execute(
        update(SessionModel)
        .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
        .values(revoked_at=now)
    )
