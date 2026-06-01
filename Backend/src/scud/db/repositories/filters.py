from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import FilterPackage


async def get_current_filter(
    session: AsyncSession, reader_id: bytes
) -> Optional[FilterPackage]:
    result = await session.execute(
        select(FilterPackage).where(
            FilterPackage.reader_id == reader_id,
            FilterPackage.is_current.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_filter_by_version(
    session: AsyncSession, reader_id: bytes, filter_version: int
) -> Optional[FilterPackage]:
    result = await session.execute(
        select(FilterPackage).where(
            FilterPackage.reader_id == reader_id,
            FilterPackage.filter_version == filter_version,
        )
    )
    return result.scalar_one_or_none()


async def get_latest_filter(
    session: AsyncSession, reader_id: bytes
) -> Optional[FilterPackage]:
    """Последняя версия фильтра в БД (max filter_version).

    Отличается от get_current_filter: тот фильтрует по is_current-флагу
    (который теоретически может быть не синхронизирован с max-version при
    редких race'ах). Используется там, где нужен именно «самый свежий» snapshot
    — например, для FP-проверки при issue_key.
    """
    result = await session.execute(
        select(FilterPackage)
        .where(FilterPackage.reader_id == reader_id)
        .order_by(FilterPackage.filter_version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_max_filter_version(
    session: AsyncSession, reader_id: bytes
) -> int:
    from sqlalchemy import func
    result = await session.execute(
        select(func.coalesce(func.max(FilterPackage.filter_version), 0)).where(
            FilterPackage.reader_id == reader_id
        )
    )
    return result.scalar_one()


async def mark_all_not_current(session: AsyncSession, reader_id: bytes) -> None:
    await session.execute(
        update(FilterPackage)
        .where(FilterPackage.reader_id == reader_id, FilterPackage.is_current.is_(True))
        .values(is_current=False)
    )


async def insert_filter_package(
    session: AsyncSession, fp: FilterPackage
) -> FilterPackage:
    session.add(fp)
    await session.flush()
    await session.refresh(fp)
    return fp
