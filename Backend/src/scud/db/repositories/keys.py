from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import IssuedKey, Permit


async def get_key_by_id(session: AsyncSession, key_id: bytes) -> Optional[IssuedKey]:
    return await session.get(IssuedKey, key_id)


async def list_keys_for_permit(
    session: AsyncSession,
    permit_id: uuid.UUID,
    active_only: bool = False,
) -> list[IssuedKey]:
    q = select(IssuedKey).where(IssuedKey.permit_id == permit_id)
    if active_only:
        q = q.where(IssuedKey.status == "active")
    result = await session.execute(q)
    return list(result.scalars().all())


async def list_keys_for_device(
    session: AsyncSession,
    device_id: uuid.UUID,
) -> list[IssuedKey]:
    result = await session.execute(
        select(IssuedKey).where(
            IssuedKey.device_id == device_id, IssuedKey.is_active.is_(True)
        )
    )
    return list(result.scalars().all())


async def get_next_serial(session: AsyncSession, permit_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(IssuedKey.serial), 0))
        .where(IssuedKey.permit_id == permit_id)
    )
    return result.scalar_one() + 1


async def insert_issued_key(session: AsyncSession, key: IssuedKey) -> IssuedKey:
    session.add(key)
    await session.flush()
    await session.refresh(key)
    return key


async def revoke_key(
    session: AsyncSession,
    key_id: bytes,
    status: str,
    reason: int,
) -> None:
    now = datetime.now(timezone.utc)
    # is_active=True for active/revoked_by_server; False for others (mirrors PG trigger).
    # Server-revoked ключ считается «ещё активным» на ридере до применения нового bloom,
    # поэтому в DB он остаётся is_active=True до обновления статуса воркером на
    # revoked_in_bloom (после delivery_receipt / FDI).
    is_active = status not in ("revoked_by_reader", "revoked_in_bloom", "expired")
    await session.execute(
        update(IssuedKey)
        .where(IssuedKey.key_id == key_id)
        .values(status=status, revoke_reason=reason, revoked_at=now, is_active=is_active)
    )


async def revoke_all_keys_for_permit(
    session: AsyncSession,
    permit_id: uuid.UUID,
) -> list[bytes]:
    """Revoke all active keys for permit. Returns list of reader_ids affected."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(IssuedKey.key_id, IssuedKey.reader_id).where(
            IssuedKey.permit_id == permit_id,
            IssuedKey.is_active.is_(True),
        )
    )
    rows = result.all()
    if not rows:
        return []
    key_ids = [r[0] for r in rows]
    reader_ids = list({r[1] for r in rows})
    # is_active=True for revoked_by_server (mirrors PG trigger)
    await session.execute(
        update(IssuedKey)
        .where(IssuedKey.key_id.in_(key_ids))
        .values(status="revoked_by_server", revoke_reason=0, revoked_at=now, is_active=True)
    )
    return reader_ids


async def list_keys_admin(
    session: AsyncSession,
    cursor: int = 0,
    limit: int = 50,
    permit_id: Optional[uuid.UUID] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
) -> list[IssuedKey]:
    q = select(IssuedKey)
    if permit_id is not None:
        q = q.where(IssuedKey.permit_id == permit_id)
    if user_id is not None:
        q = q.join(Permit, IssuedKey.permit_id == Permit.permit_id).where(
            Permit.user_id == user_id
        )
    if status is not None:
        q = q.where(IssuedKey.status == status)
    q = q.offset(cursor).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())
