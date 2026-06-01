"""App readers endpoints (§5.4)."""

from __future__ import annotations

import base64
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from scud.api.deps import CurrentUser, DbSession
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo

router = APIRouter(prefix="/readers", tags=["app-readers"])


class ReaderDetail(BaseModel):
    reader_id: str
    display_name: str
    description: Optional[str]
    reader_pubkey: str  # base64
    group_id: str


async def _accessible_group_ids(
    db, current_user
) -> set[uuid.UUID]:
    """Return group_ids accessible to the current user."""
    groups: set[uuid.UUID] = {current_user.user_group_id}
    permits = await permit_repo.list_permits_for_user(db, current_user.user_id, active_only=False)
    for p in permits:
        reader = await reader_repo.get_reader_by_id(db, p.reader_id)
        if reader:
            groups.add(reader.reader_group_id)
    return groups


@router.get("/{reader_id_hex}", response_model=ReaderDetail)
async def get_reader(
    reader_id_hex: str,
    current_user: CurrentUser,
    db: DbSession,
) -> ReaderDetail:
    try:
        reader_id = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid reader_id hex")

    reader = await reader_repo.get_reader_by_id(db, reader_id)
    if reader is None or not reader.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    accessible = await _accessible_group_ids(db, current_user)
    if reader.reader_group_id not in accessible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_accessible")

    return ReaderDetail(
        reader_id=reader.reader_id.hex(),
        display_name=reader.display_name,
        description=reader.description,
        reader_pubkey=base64.b64encode(reader.reader_pubkey).decode(),
        group_id=str(reader.reader_group_id),
    )


@router.get("", response_model=dict)
async def list_readers(
    current_user: CurrentUser,
    db: DbSession,
    group_id: Optional[uuid.UUID] = Query(None),
) -> dict:
    accessible = await _accessible_group_ids(db, current_user)
    if group_id is not None and group_id not in accessible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_accessible")

    gid = group_id or None
    readers = await reader_repo.list_readers(db, group_id=gid)
    items = []
    for r in readers:
        if r.reader_group_id in accessible:
            items.append(
                ReaderDetail(
                    reader_id=r.reader_id.hex(),
                    display_name=r.display_name,
                    description=r.description,
                    reader_pubkey=base64.b64encode(r.reader_pubkey).decode(),
                    group_id=str(r.reader_group_id),
                ).model_dump()
            )
    return {"items": items}
