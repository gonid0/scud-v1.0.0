from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import UserDevice


async def get_device_by_id(session: AsyncSession, device_id: uuid.UUID) -> Optional[UserDevice]:
    return await session.get(UserDevice, device_id)


async def get_active_device_for_user(
    session: AsyncSession, user_id: int
) -> Optional[UserDevice]:
    result = await session.execute(
        select(UserDevice)
        .where(UserDevice.user_id == user_id, UserDevice.is_active.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_device_by_pubkey(
    session: AsyncSession, phone_pubkey: bytes
) -> Optional[UserDevice]:
    result = await session.execute(
        select(UserDevice).where(UserDevice.phone_pubkey == phone_pubkey)
    )
    return result.scalar_one_or_none()


async def list_devices_for_user(session: AsyncSession, user_id: int) -> list[UserDevice]:
    result = await session.execute(
        select(UserDevice).where(UserDevice.user_id == user_id).order_by(UserDevice.registered_at)
    )
    return list(result.scalars().all())


async def deactivate_all_user_devices(session: AsyncSession, user_id: int) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(UserDevice)
        .where(UserDevice.user_id == user_id, UserDevice.is_active.is_(True))
        .values(is_active=False, deactivated_at=now)
    )


async def create_device(
    session: AsyncSession,
    user_id: int,
    phone_pubkey: bytes,
    device_label: Optional[str] = None,
) -> UserDevice:
    device = UserDevice(
        user_id=user_id,
        phone_pubkey=phone_pubkey,
        device_label=device_label,
        is_active=True,
    )
    session.add(device)
    await session.flush()
    await session.refresh(device)
    return device
