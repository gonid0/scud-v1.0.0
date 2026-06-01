"""Worker entry point.

Run via: python -m scud.worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from scud.config import settings
from scud.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"

HANDLERS: dict[str, object] = {}


def _load_handlers() -> None:
    from scud.worker_handlers import (
        cleanup,
        expire_keys,
        generate_filter,
        notify_webhook,
        process_report,
    )
    HANDLERS["generate_filter"] = generate_filter.handle
    HANDLERS["process_report"] = process_report.handle
    HANDLERS["expire_keys"] = expire_keys.handle
    HANDLERS["cleanup"] = cleanup.handle
    HANDLERS["notify_webhook"] = notify_webhook.handle


async def seed_periodic_tasks() -> None:
    """Seed expire_keys and cleanup tasks at startup if absent (§6.5)."""
    from scud.db.repositories.tasks import enqueue_task, has_pending_task

    async with AsyncSessionFactory() as session:
        async with session.begin():
            if not await has_pending_task(session, "expire_keys"):
                await enqueue_task(session, "expire_keys", {})
                logger.info("seeded expire_keys task")
            if not await has_pending_task(session, "cleanup"):
                await enqueue_task(session, "cleanup", {})
                logger.info("seeded cleanup task")


async def fetch_next_task():
    from scud.db.repositories.tasks import fetch_next_task as _fetch
    async with AsyncSessionFactory() as session:
        async with session.begin():
            task = await _fetch(session, WORKER_ID)
            return task


async def handle_task(task) -> None:
    handler = HANDLERS.get(task.task_type)
    if handler is None:
        raise ValueError(f"unknown task_type: {task.task_type}")

    async with AsyncSessionFactory() as session:
        async with session.begin():
            logger.info("processing task %s type=%s", task.task_id, task.task_type)
            await handler(session, task.payload)


async def mark_done(task) -> None:
    from scud.db.repositories.tasks import mark_task_done
    async with AsyncSessionFactory() as session:
        async with session.begin():
            await mark_task_done(session, task.task_id)


async def mark_failed(task, error: str) -> None:
    from scud.db.repositories.tasks import mark_task_failed
    async with AsyncSessionFactory() as session:
        async with session.begin():
            await mark_task_failed(session, task.task_id, error)


async def run_worker() -> None:
    _load_handlers()
    await seed_periodic_tasks()
    logger.info("worker started, id=%s", WORKER_ID)

    while True:
        task = await fetch_next_task()
        if task is None:
            await asyncio.sleep(settings.worker_poll_interval_seconds)
            continue
        try:
            await handle_task(task)
            await mark_done(task)
            logger.info("task %s done", task.task_id)
        except Exception as e:
            logger.exception("task %s failed: %s", task.task_id, e)
            await mark_failed(task, str(e))


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
