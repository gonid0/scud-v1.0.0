"""Admin observability endpoints (§5.7)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import AdminAuditLog, BackgroundTask, DeliveryTask, FilterPackage, Reader
from scud.db.repositories import filters as filter_repo
from scud.db.repositories import tasks as task_repo

router = APIRouter(tags=["admin-observability"])


@router.get("/readers/{reader_id_hex}/delivery-status")
async def reader_delivery_status(
    reader_id_hex: str,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    try:
        reader_id = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid hex")

    reader = await db.get(Reader, reader_id)
    if reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    current_filter = await filter_repo.get_current_filter(db, reader_id)
    current_version = current_filter.filter_version if current_filter else 0

    result = await db.execute(
        select(DeliveryTask).where(
            DeliveryTask.reader_id == reader_id,
            DeliveryTask.completed_at.is_(None),
        )
    )
    open_tasks = result.scalars().all()

    now = datetime.now(timezone.utc)
    open_tasks_out = [
        {
            "task_id": str(t.task_id),
            "filter_version": t.filter_version,
            "created_at": t.created_at.isoformat(),
            "age_hours": round((now - t.created_at).total_seconds() / 3600, 2),
        }
        for t in open_tasks
    ]

    time_drift = None
    if reader.last_known_time and reader.last_contact_at:
        time_drift = int(
            (reader.last_contact_at - reader.last_known_time).total_seconds()
        )

    return {
        "reader_id": reader_id_hex,
        "last_applied_filter_version": reader.last_applied_filter_version,
        "current_server_version": current_version,
        "open_delivery_tasks": open_tasks_out,
        "last_contact_at": reader.last_contact_at.isoformat() if reader.last_contact_at else None,
        "last_known_time": reader.last_known_time.isoformat() if reader.last_known_time else None,
        "time_drift_seconds": time_drift,
    }


@router.get("/background-tasks")
async def list_background_tasks(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
    status_filter: Optional[str] = Query(None, alias="status"),
) -> dict:
    tasks = await task_repo.list_tasks_admin(db, cursor=cursor, limit=limit, status=status_filter)
    return {
        "items": [
            {
                "task_id": str(t.task_id),
                "task_type": t.task_type,
                "status": t.status,
                "payload": t.payload,
                "scheduled_at": t.scheduled_at.isoformat(),
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "error": t.error,
                "retry_count": t.retry_count,
            }
            for t in tasks
        ]
    }


@router.get("/audit-log")
async def list_audit_log(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
    actor_id: Optional[uuid.UUID] = Query(None),
    target_type: Optional[str] = Query(None),
) -> dict:
    q = select(AdminAuditLog)
    if actor_id is not None:
        q = q.where(AdminAuditLog.actor_id == actor_id)
    if target_type is not None:
        q = q.where(AdminAuditLog.target_type == target_type)
    q = q.offset(cursor).limit(limit).order_by(AdminAuditLog.occurred_at.desc())
    result = await db.execute(q)
    rows = result.scalars().all()
    return {
        "items": [
            {
                "audit_id": r.audit_id,
                "occurred_at": r.occurred_at.isoformat(),
                "actor_type": r.actor_type,
                "actor_id": str(r.actor_id),
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "details": r.details,
            }
            for r in rows
        ]
    }
