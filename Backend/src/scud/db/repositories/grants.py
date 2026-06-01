from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import TimeGrant


async def get_grant_by_id(session: AsyncSession, grant_id: uuid.UUID) -> Optional[TimeGrant]:
    return await session.get(TimeGrant, grant_id)


async def get_active_grant_for_permit_pubkey(
    session: AsyncSession,
    permit_id: uuid.UUID,
    authority_pubkey: bytes,
) -> Optional[TimeGrant]:
    result = await session.execute(
        select(TimeGrant).where(
            TimeGrant.permit_id == permit_id,
            TimeGrant.authority_pubkey == authority_pubkey,
            TimeGrant.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def insert_grant(session: AsyncSession, grant: TimeGrant) -> TimeGrant:
    session.add(grant)
    await session.flush()
    await session.refresh(grant)
    return grant


async def revoke_grants_for_permit(
    session: AsyncSession, permit_id: uuid.UUID
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(TimeGrant)
        .where(TimeGrant.permit_id == permit_id, TimeGrant.revoked_at.is_(None))
        .values(revoked_at=now)
    )
