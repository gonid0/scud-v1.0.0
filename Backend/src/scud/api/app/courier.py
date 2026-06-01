"""App courier endpoints (§5.5)."""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from scud.api.deps import CurrentSession, CurrentUser, DbSession
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo
from scud.domain.courier import download_package, list_available

router = APIRouter(prefix="/courier", tags=["app-courier"])


async def _accessible_group_ids(db, current_user) -> set[uuid.UUID]:
    """Группы, к которым пользователь имеет доступ: своя + группы ридеров по permit'ам.
    Симметрично с api/app/readers.py::_accessible_group_ids."""
    groups: set[uuid.UUID] = {current_user.user_group_id}
    permits = await permit_repo.list_permits_for_user(
        db, current_user.user_id, active_only=False
    )
    for p in permits:
        reader = await reader_repo.get_reader_by_id(db, p.reader_id)
        if reader:
            groups.add(reader.reader_group_id)
    return groups


@router.get("/available", response_model=dict)
async def get_available(
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    accessible = await _accessible_group_ids(db, current_user)
    items = await list_available(db, accessible)
    return {"items": items}


class DownloadRequest(BaseModel):
    delivery_id: uuid.UUID


@router.post("/download", response_model=dict)
async def download(
    body: DownloadRequest,
    current_user: CurrentUser,
    current_session: CurrentSession,
    db: DbSession,
) -> dict:
    if current_session.device_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="no_device_registered"
        )

    accessible = await _accessible_group_ids(db, current_user)
    try:
        result = await download_package(
            db,
            delivery_id=body.delivery_id,
            accessible_group_ids=accessible,
            user_id=current_user.user_id,
            device_id=current_session.device_id,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="delivery_not_found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="wrong_group")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="delivery_closed")

    return {
        "delivery_id": str(result["delivery_id"]),
        "target_reader_id": result["target_reader_id"],
        "filter_version": result["filter_version"],
        "filter_package_bytes": base64.b64encode(result["filter_package_bytes"]).decode(),
        "courier_id": result["courier_id"],
    }
