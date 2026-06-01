from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import ReaderReport


async def insert_report(
    session: AsyncSession,
    report_type: str,
    reader_id: bytes,
    raw_bytes: bytes,
    delivered_by_user_id: Optional[int] = None,
) -> ReaderReport:
    report = ReaderReport(
        report_type=report_type,
        reader_id=reader_id,
        raw_bytes=raw_bytes,
        delivered_by_user_id=delivered_by_user_id,
    )
    session.add(report)
    await session.flush()
    await session.refresh(report)
    return report


async def get_report_by_id(
    session: AsyncSession, report_id: uuid.UUID
) -> Optional[ReaderReport]:
    return await session.get(ReaderReport, report_id)


async def mark_report_processed(
    session: AsyncSession,
    report_id: uuid.UUID,
    error: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ReaderReport)
        .where(ReaderReport.report_id == report_id)
        .values(processed_at=now, processing_error=error)
    )
