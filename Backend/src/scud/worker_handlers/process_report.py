"""Worker handler: process_report (§6.2)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.repositories import readers as reader_repo
from scud.db.repositories import reports as report_repo
from scud.domain.reports import dispatch_report

logger = logging.getLogger(__name__)


async def handle(session: AsyncSession, payload: dict) -> None:
    report_id = uuid.UUID(payload["report_id"])
    report = await report_repo.get_report_by_id(session, report_id)
    if report is None:
        raise ValueError(f"report {report_id} not found")

    reader = await reader_repo.get_reader_by_id(session, report.reader_id)
    if reader is None:
        await report_repo.mark_report_processed(session, report_id, error="reader_not_found")
        return

    logger.info("processing %s report %s", report.report_type, report_id)
    try:
        await dispatch_report(
            session,
            report.report_type,
            report.raw_bytes,
            reader,
            delivered_by_user_id=report.delivered_by_user_id,
        )
        await report_repo.mark_report_processed(session, report_id)
    except Exception as e:
        logger.exception("failed to process report %s", report_id)
        await report_repo.mark_report_processed(session, report_id, error=str(e))
        raise
