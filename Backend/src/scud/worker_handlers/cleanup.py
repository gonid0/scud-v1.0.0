"""Worker handler: cleanup (§6.4).

Runs once a day. Self-schedules next run 24 hours later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.repositories import tasks as task_repo

logger = logging.getLogger(__name__)


async def handle(session: AsyncSession, payload: dict) -> None:
    logger.info("running cleanup")

    await session.execute(
        text("DELETE FROM reader_reports WHERE processed_at < NOW() - INTERVAL '30 days'")
    )
    await session.execute(
        text("DELETE FROM background_tasks WHERE completed_at < NOW() - INTERVAL '7 days'")
    )
    await session.execute(
        text("DELETE FROM admin_audit_log WHERE occurred_at < NOW() - INTERVAL '365 days'")
    )

    next_run = datetime.now(timezone.utc) + timedelta(days=1)
    await task_repo.enqueue_task(session, "cleanup", {}, scheduled_at=next_run)
    logger.info("cleanup done; next run at %s", next_run.isoformat())
