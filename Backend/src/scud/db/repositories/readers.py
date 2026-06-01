from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import Reader, ReaderGroup


async def get_reader_by_id(session: AsyncSession, reader_id: bytes) -> Optional[Reader]:
    return await session.get(Reader, reader_id)


async def list_readers(
    session: AsyncSession,
    cursor: int = 0,
    limit: int = 50,
    group_id: Optional[uuid.UUID] = None,
    active_only: bool = True,
) -> list[Reader]:
    q = select(Reader)
    if active_only:
        q = q.where(Reader.is_active.is_(True))
    if group_id is not None:
        q = q.where(Reader.reader_group_id == group_id)
    q = q.offset(cursor).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def list_readers_for_group(
    session: AsyncSession,
    group_id: uuid.UUID,
) -> list[Reader]:
    result = await session.execute(
        select(Reader).where(
            Reader.reader_group_id == group_id, Reader.is_active.is_(True)
        )
    )
    return list(result.scalars().all())


async def insert_reader(session: AsyncSession, reader: Reader) -> Reader:
    session.add(reader)
    await session.flush()
    await session.refresh(reader)
    return reader


async def update_reader(session: AsyncSession, reader_id: bytes, **kwargs) -> Optional[Reader]:
    await session.execute(
        update(Reader).where(Reader.reader_id == reader_id).values(**kwargs)
    )
    return await session.get(Reader, reader_id)


# --- ReaderGroup ---


async def get_group_by_id(
    session: AsyncSession, group_id: uuid.UUID
) -> Optional[ReaderGroup]:
    return await session.get(ReaderGroup, group_id)


async def list_groups(
    session: AsyncSession, cursor: int = 0, limit: int = 50
) -> list[ReaderGroup]:
    result = await session.execute(
        select(ReaderGroup)
        .offset(cursor)
        .limit(limit)
        .order_by(ReaderGroup.created_at)
    )
    return list(result.scalars().all())


async def create_group(
    session: AsyncSession,
    name: str,
    description: Optional[str] = None,
) -> ReaderGroup:
    group = ReaderGroup(name=name, description=description)
    session.add(group)
    await session.flush()
    await session.refresh(group)
    return group


async def update_group(
    session: AsyncSession, group_id: uuid.UUID, **kwargs
) -> Optional[ReaderGroup]:
    await session.execute(
        update(ReaderGroup).where(ReaderGroup.group_id == group_id).values(**kwargs)
    )
    return await session.get(ReaderGroup, group_id)


async def delete_group(session: AsyncSession, group_id: uuid.UUID) -> bool:
    """Delete group if no readers reference it. Returns True on success."""
    from sqlalchemy import delete
    readers = await list_readers_for_group(session, group_id)
    if readers:
        return False
    await session.execute(
        delete(ReaderGroup).where(ReaderGroup.group_id == group_id)
    )
    return True
