"""Courier domain: available deliveries and download."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import DeliveryTask, FilterPackage, Reader

# Fixed namespace UUID for courier_id derivation (§5.5)
NAMESPACE_COURIER = uuid.UUID("a1a11111-2222-3333-4444-555555555555")


def compute_courier_id(user_id: int, device_id: uuid.UUID) -> bytes:
    """Deterministic courier_id = first 16 bytes of uuid5(NAMESPACE_COURIER, f'{user_id}:{device_id}')."""
    u = uuid.uuid5(NAMESPACE_COURIER, f"{user_id}:{device_id}")
    return u.bytes


async def list_available(
    session: AsyncSession,
    accessible_group_ids: set[uuid.UUID],
) -> list[dict]:
    """Return open delivery tasks for readers in any group accessible to the user.

    Accessible = user's own group + groups of readers user has a permit for.
    Это унифицирует доступ с /app/readers и /app/readers/{id}.
    """
    if not accessible_group_ids:
        return []
    result = await session.execute(
        select(
            DeliveryTask.task_id,
            Reader.reader_id,
            Reader.display_name,
            FilterPackage.filter_version,
            FilterPackage.package_bytes,
            DeliveryTask.created_at,
        )
        .join(Reader, DeliveryTask.reader_id == Reader.reader_id)
        .join(
            FilterPackage,
            (FilterPackage.reader_id == DeliveryTask.reader_id)
            & (FilterPackage.filter_version == DeliveryTask.filter_version),
        )
        .where(
            DeliveryTask.completed_at.is_(None),
            Reader.reader_group_id.in_(accessible_group_ids),
        )
    )
    rows = result.all()
    items = []
    for row in rows:
        items.append(
            {
                "delivery_id": str(row[0]),
                "target_reader_id": row[1].hex(),
                "target_reader_display_name": row[2],
                "filter_version": row[3],
                "package_size_bytes": len(row[4]),
                "created_at": row[5].isoformat(),
            }
        )
    return items


async def download_package(
    session: AsyncSession,
    delivery_id: uuid.UUID,
    accessible_group_ids: set[uuid.UUID],
    user_id: int,
    device_id: uuid.UUID,
) -> dict:
    """Return the filter package bytes for a delivery task."""
    result = await session.execute(
        select(DeliveryTask, Reader, FilterPackage)
        .join(Reader, DeliveryTask.reader_id == Reader.reader_id)
        .join(
            FilterPackage,
            (FilterPackage.reader_id == DeliveryTask.reader_id)
            & (FilterPackage.filter_version == DeliveryTask.filter_version),
        )
        .where(DeliveryTask.task_id == delivery_id)
    )
    row = result.first()
    if row is None:
        raise LookupError("delivery_not_found")

    task, reader, fp = row
    if reader.reader_group_id not in accessible_group_ids:
        raise PermissionError("wrong_group")
    if task.completed_at is not None:
        raise ValueError("delivery_closed")

    courier_id = compute_courier_id(user_id, device_id)

    return {
        "delivery_id": task.task_id,
        "target_reader_id": reader.reader_id.hex(),
        "filter_version": fp.filter_version,
        "filter_package_bytes": fp.package_bytes,
        "courier_id": courier_id.hex(),
    }
