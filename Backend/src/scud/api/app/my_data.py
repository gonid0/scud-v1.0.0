"""GET /app/my-data endpoint (§5.2)."""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from scud.api.deps import CurrentSession, CurrentUser, DbSession
from scud.db.repositories import devices as dev_repo
from scud.db.repositories import grants as grant_repo
from scud.db.repositories import keys as key_repo
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo

router = APIRouter(tags=["app-my-data"])


class ReaderInfo(BaseModel):
    reader_id: str
    display_name: str
    group_id: str


class PermitItem(BaseModel):
    permit_id: str
    display_name: str
    reader: ReaderInfo
    valid_from: str
    valid_until: str
    n_parallel: int
    max_token_ttl_seconds: int
    max_total_issued: int | None
    active_keys_count: int
    has_grant_on_this_device: bool


class DeviceItem(BaseModel):
    device_id: str
    device_label: str | None
    registered_at: str
    is_current: bool


class MyDataResponse(BaseModel):
    user: dict
    permits: list[PermitItem]
    devices: list[DeviceItem]


@router.get("/my-data", response_model=MyDataResponse)
async def get_my_data(
    current_user: CurrentUser,
    current_session: CurrentSession,
    db: DbSession,
) -> MyDataResponse:
    permits_raw = await permit_repo.list_permits_for_user(db, current_user.user_id)
    devices_raw = await dev_repo.list_devices_for_user(db, current_user.user_id)

    permit_items = []
    for p in permits_raw:
        reader = await reader_repo.get_reader_by_id(db, p.reader_id)
        active_count = await permit_repo.count_active_keys(db, p.permit_id)

        # Check if current device has grant
        has_grant = False
        if current_session.device_id:
            device = await dev_repo.get_device_by_id(db, current_session.device_id)
            if device:
                grant = await grant_repo.get_active_grant_for_permit_pubkey(
                    db, p.permit_id, device.phone_pubkey
                )
                has_grant = grant is not None

        permit_items.append(
            PermitItem(
                permit_id=str(p.permit_id),
                display_name=p.display_name,
                reader=ReaderInfo(
                    reader_id=p.reader_id.hex() if reader else "",
                    display_name=reader.display_name if reader else "",
                    group_id=str(reader.reader_group_id) if reader else "",
                ),
                valid_from=p.valid_from.isoformat(),
                valid_until=p.valid_until.isoformat(),
                n_parallel=p.n_parallel,
                max_token_ttl_seconds=p.max_token_ttl_seconds,
                max_total_issued=p.max_total_issued,
                active_keys_count=active_count,
                has_grant_on_this_device=has_grant,
            )
        )

    device_items = [
        DeviceItem(
            device_id=str(d.device_id),
            device_label=d.device_label,
            registered_at=d.registered_at.isoformat(),
            is_current=(d.device_id == current_session.device_id),
        )
        for d in devices_raw
    ]

    return MyDataResponse(
        user={
            "user_id": current_user.user_id,
            "display_name": current_user.display_name,
            "user_group_id": str(current_user.user_group_id),
        },
        permits=permit_items,
        devices=device_items,
    )
