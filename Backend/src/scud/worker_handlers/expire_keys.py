"""Worker handler: expire_keys (§6.3).

Runs periodically; self-schedules next run 1 hour later on completion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.repositories import tasks as task_repo

logger = logging.getLogger(__name__)


async def handle(session: AsyncSession, payload: dict) -> None:
    logger.info("running expire_keys")

    await session.execute(
        text("""
            UPDATE issued_keys
            SET status='expired', revoke_reason=2, revoked_at=expires_at, is_active=FALSE
            WHERE expires_at < NOW() AND is_active
        """)
    )

    await session.execute(
        text("""
            UPDATE permits
            SET revoked_at=valid_until
            WHERE valid_until < NOW() AND revoked_at IS NULL
        """)
    )

    await session.execute(
        text("""
            UPDATE time_grants
            SET revoked_at=expires_at
            WHERE expires_at < NOW() AND revoked_at IS NULL
        """)
    )

    await session.execute(
        text("""
            DELETE FROM sessions
            WHERE refresh_expires_at < NOW()
        """)
    )

    # Self-schedule next run in 1 hour
    next_run = datetime.now(timezone.utc) + timedelta(hours=1)
    await task_repo.enqueue_task(session, "expire_keys", {}, scheduled_at=next_run)
    logger.info("expire_keys done; next run at %s", next_run.isoformat())
