from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import BackgroundTask


async def enqueue_task(
    session: AsyncSession,
    task_type: str,
    payload: dict[str, Any],
    scheduled_at: Optional[datetime] = None,
) -> BackgroundTask:
    task = BackgroundTask(
        task_type=task_type,
        payload=payload,
        status="pending",
        scheduled_at=scheduled_at or datetime.now(timezone.utc),
    )
    session.add(task)
    await session.flush()
    await session.refresh(task)
    return task


async def enqueue_generate_filter_debounced(
    session: AsyncSession,
    reader_id_hex: str,
) -> Optional[BackgroundTask]:
    """Enqueue generate_filter only if no pending task already exists."""
    from sqlalchemy import cast, func, String
    from sqlalchemy.dialects import postgresql

    # Try PostgreSQL JSONB path first; fall back to JSON string contains for SQLite
    try:
        result = await session.execute(
            select(BackgroundTask).where(
                BackgroundTask.task_type == "generate_filter",
                BackgroundTask.status == "pending",
                BackgroundTask.payload["reader_id"].astext == reader_id_hex,
            )
        )
    except Exception:
        # Fallback: scan all pending generate_filter tasks (SQLite compat)
        result = await session.execute(
            select(BackgroundTask).where(
                BackgroundTask.task_type == "generate_filter",
                BackgroundTask.status == "pending",
            )
        )
        tasks = result.scalars().all()
        if any(t.payload.get("reader_id") == reader_id_hex for t in tasks):
            return None
        return await enqueue_task(
            session, "generate_filter", {"reader_id": reader_id_hex}
        )

    existing = result.scalar_one_or_none()
    if existing is not None:
        return None
    return await enqueue_task(
        session, "generate_filter", {"reader_id": reader_id_hex}
    )


async def fetch_next_task(
    session: AsyncSession,
    worker_id: str,
) -> Optional[BackgroundTask]:
    """Atomically claim the next pending task using SKIP LOCKED."""
    result = await session.execute(
        text("""
            WITH next AS (
                SELECT task_id FROM background_tasks
                WHERE status = 'pending' AND scheduled_at <= NOW()
                ORDER BY scheduled_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE background_tasks bt
            SET status='processing', started_at=NOW(), worker_id=:worker_id
            FROM next
            WHERE bt.task_id = next.task_id
            RETURNING bt.*
        """),
        {"worker_id": worker_id},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return await session.get(BackgroundTask, row["task_id"])


async def mark_task_done(session: AsyncSession, task_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(BackgroundTask)
        .where(BackgroundTask.task_id == task_id)
        .values(status="done", completed_at=now)
    )


async def mark_task_failed(
    session: AsyncSession, task_id: uuid.UUID, error: str
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(BackgroundTask)
        .where(BackgroundTask.task_id == task_id)
        .values(status="failed", completed_at=now, error=error, retry_count=BackgroundTask.retry_count + 1)
    )


async def has_pending_task(session: AsyncSession, task_type: str) -> bool:
    result = await session.execute(
        select(BackgroundTask).where(
            BackgroundTask.task_type == task_type,
            BackgroundTask.status == "pending",
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def list_tasks_admin(
    session: AsyncSession,
    cursor: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
) -> list[BackgroundTask]:
    q = select(BackgroundTask)
    if status is not None:
        q = q.where(BackgroundTask.status == status)
    q = q.offset(cursor).limit(limit).order_by(BackgroundTask.scheduled_at.desc())
    result = await session.execute(q)
    return list(result.scalars().all())
