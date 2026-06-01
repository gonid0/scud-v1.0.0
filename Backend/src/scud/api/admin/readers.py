"""Admin readers endpoints including /enroll (§5.7) and Phase-1 config (§0007)."""

from __future__ import annotations

import base64
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status
from nacl.public import PrivateKey
from nacl.signing import SigningKey
from pydantic import BaseModel
from sqlalchemy import select

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import (
    AdminAuditLog,
    ParamBounds,
    Reader,
    ReaderParamOverride,
    ReaderProfile,
)
from scud.db.repositories import readers as reader_repo
from scud.domain.reader_config_resolver import resolve_reader_params
from scud.domain.reader_param_catalog import HARDWARE_CLASSES, PARAMS_BY_KEY

router = APIRouter(prefix="/readers", tags=["admin-readers"])


async def _audit(db, api_key, action: str, target_id: str, details: dict = None) -> None:
    db.add(
        AdminAuditLog(
            actor_type="api_key",
            actor_id=api_key.api_key_id,
            action=action,
            target_type="reader",
            target_id=target_id,
            details=details,
        )
    )


async def _validate_overrides(
    db: Any,
    hardware_class: str,
    overrides: dict[str, int],
) -> None:
    """Validate override param_keys and values against param_bounds.

    Raises HTTP 422 on any unknown key or out-of-range value.
    """
    for key, value in overrides.items():
        if key not in PARAMS_BY_KEY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown param_key '{key}'",
            )
        result = await db.execute(
            select(ParamBounds).where(
                ParamBounds.param_key == key,
                ParamBounds.hardware_class == hardware_class,
            )
        )
        bounds = result.scalar_one_or_none()
        if bounds is None:
            spec = PARAMS_BY_KEY[key]
            lo, hi = spec.min_value, spec.max_value
        else:
            lo = bounds.server_floor if bounds.server_floor is not None else bounds.min_value
            hi = bounds.server_ceiling if bounds.server_ceiling is not None else bounds.max_value

        if not (lo <= value <= hi):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"param '{key}' value {value} out of range [{lo}, {hi}] "
                    f"for hardware_class '{hardware_class}'"
                ),
            )


# ---------------------------------------------------------------------------
# Enroll
# ---------------------------------------------------------------------------

class EnrollRequest(BaseModel):
    reader_id: str  # hex (16 B = 32 hex chars)
    reader_pubkey: str  # base64
    reader_group_id: uuid.UUID
    display_name: str
    description: Optional[str] = None
    # Phase 3 (docs/11): optional firmware-provisioned parameters.
    # The backend stores this dict verbatim and does not interpret individual
    # fields; semantics are owned by the firmware / provisioning tool.
    config: Optional[dict[str, Any]] = None
    # Phase 1: optional structured parameterization
    profile_id: Optional[uuid.UUID] = None
    hardware_class: Optional[str] = None
    overrides: Optional[dict[str, int]] = None


class EnrollResponse(BaseModel):
    reader_id: str
    server_ed25519_pubkey: str
    server_x25519_pubkey: str
    reader_group_id: str


@router.post("/enroll", response_model=EnrollResponse)
async def enroll_reader(
    body: EnrollRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> EnrollResponse:
    try:
        reader_id_bytes = bytes.fromhex(body.reader_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid reader_id hex")
    if len(reader_id_bytes) != 16:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="reader_id must be 16 bytes")

    try:
        reader_pubkey = base64.b64decode(body.reader_pubkey)
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid reader_pubkey base64")
    if len(reader_pubkey) != 32:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="reader_pubkey must be 32 bytes")

    # Check uniqueness
    existing = await reader_repo.get_reader_by_id(db, reader_id_bytes)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="reader_id_exists")

    # Verify group exists
    group = await reader_repo.get_group_by_id(db, body.reader_group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group_not_found")

    hw_class = body.hardware_class or "esp32_mains_ble"
    if hw_class not in HARDWARE_CLASSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown hardware_class '{hw_class}'",
        )

    # Validate profile if given
    if body.profile_id is not None:
        profile = await db.get(ReaderProfile, body.profile_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_not_found")

    # Validate overrides
    if body.overrides:
        await _validate_overrides(db, hw_class, body.overrides)

    # Generate keypairs
    ed25519_key = SigningKey.generate()
    ed25519_priv = bytes(ed25519_key)
    ed25519_pub = bytes(ed25519_key.verify_key)

    x25519_key = PrivateKey.generate()
    x25519_priv = bytes(x25519_key)
    x25519_pub = bytes(x25519_key.public_key)

    reader = Reader(
        reader_id=reader_id_bytes,
        reader_group_id=body.reader_group_id,
        display_name=body.display_name,
        description=body.description,
        reader_pubkey=reader_pubkey,
        server_ed25519_privkey=ed25519_priv,
        server_ed25519_pubkey=ed25519_pub,
        server_x25519_privkey=x25519_priv,
        server_x25519_pubkey=x25519_pub,
        is_active=True,
        config=body.config,
        profile_id=body.profile_id,
        hardware_class=hw_class,
    )
    await reader_repo.insert_reader(db, reader)
    await db.flush()

    # Insert overrides
    if body.overrides:
        for k, v in body.overrides.items():
            db.add(ReaderParamOverride(
                reader_id=reader_id_bytes,
                param_key=k,
                value=v,
            ))

    await _audit(db, api_key, "enroll_reader", body.reader_id)
    await db.commit()

    return EnrollResponse(
        reader_id=reader_id_bytes.hex(),
        server_ed25519_pubkey=base64.b64encode(ed25519_pub).decode(),
        server_x25519_pubkey=base64.b64encode(x25519_pub).decode(),
        reader_group_id=str(body.reader_group_id),
    )


# ---------------------------------------------------------------------------
# List readers
# ---------------------------------------------------------------------------

@router.get("")
async def list_readers(
    api_key: AdminApiKey,
    db: DbSession,
    cursor: int = Query(0),
    limit: int = Query(50, le=200),
    group_id: Optional[uuid.UUID] = Query(None),
) -> dict:
    readers = await reader_repo.list_readers(db, cursor=cursor, limit=limit, group_id=group_id)
    return {
        "items": [
            {
                "reader_id": r.reader_id.hex(),
                "display_name": r.display_name,
                "description": r.description,
                "reader_group_id": str(r.reader_group_id),
                "is_active": r.is_active,
                "enrolled_at": r.enrolled_at.isoformat(),
                "last_contact_at": r.last_contact_at.isoformat() if r.last_contact_at else None,
                "last_applied_filter_version": r.last_applied_filter_version,
            }
            for r in readers
        ]
    }


# ---------------------------------------------------------------------------
# GET config-script — must be registered BEFORE /{reader_id_hex} to avoid
# FastAPI routing the literal "config-script" segment as a reader_id_hex
# ---------------------------------------------------------------------------

@router.get("/{reader_id_hex}/config-script")
async def get_reader_config_script(
    reader_id_hex: str,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    """Return the resolved SET-* provisioning script for this reader.

    This is what the desktop provisioning tool plays verbatim over serial.
    The script is deterministic and idempotent.
    """
    try:
        reader_id = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid hex")

    reader = await reader_repo.get_reader_by_id(db, reader_id)
    if reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    # Read-only: do NOT pass api_key — config-script is a GET and must not write
    # audit rows. Security-downgrade auditing happens on the PATCH (override) path.
    resolved = await resolve_reader_params(db, reader)

    return {
        "reader_id": reader_id_hex,
        "hardware_class": reader.hardware_class,
        "profile_id": str(reader.profile_id) if reader.profile_id else None,
        "params": resolved.params,
        "script": [
            {"command": cmd, "arg": arg}
            for cmd, arg in resolved.script
        ],
        "warnings": resolved.warnings,
    }


# ---------------------------------------------------------------------------
# Get one reader
# ---------------------------------------------------------------------------

@router.get("/{reader_id_hex}")
async def get_reader(reader_id_hex: str, api_key: AdminApiKey, db: DbSession) -> dict:
    try:
        reader_id = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid hex")

    reader = await reader_repo.get_reader_by_id(db, reader_id)
    if reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    # Count open delivery tasks
    from sqlalchemy import func, select
    from scud.db.models import DeliveryTask
    result = await db.execute(
        select(func.count()).select_from(DeliveryTask).where(
            DeliveryTask.reader_id == reader_id,
            DeliveryTask.completed_at.is_(None),
        )
    )
    open_tasks = result.scalar_one()

    return {
        "reader_id": reader.reader_id.hex(),
        "display_name": reader.display_name,
        "description": reader.description,
        "reader_group_id": str(reader.reader_group_id),
        "is_active": reader.is_active,
        "enrolled_at": reader.enrolled_at.isoformat(),
        "last_contact_at": reader.last_contact_at.isoformat() if reader.last_contact_at else None,
        "last_known_time": reader.last_known_time.isoformat() if reader.last_known_time else None,
        "last_applied_filter_version": reader.last_applied_filter_version,
        "open_delivery_tasks_count": open_tasks,
        "config": reader.config,
        "profile_id": str(reader.profile_id) if reader.profile_id else None,
        "hardware_class": reader.hardware_class,
    }


# ---------------------------------------------------------------------------
# PATCH reader — now also accepts profile_id, hardware_class, overrides
# ---------------------------------------------------------------------------

class PatchReaderRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    reader_group_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None
    # Phase 1 additions
    profile_id: Optional[uuid.UUID] = None
    hardware_class: Optional[str] = None
    overrides: Optional[dict[str, int]] = None


@router.patch("/{reader_id_hex}")
async def patch_reader(
    reader_id_hex: str,
    body: PatchReaderRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    try:
        reader_id = bytes.fromhex(reader_id_hex)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid hex")

    reader = await reader_repo.get_reader_by_id(db, reader_id)
    if reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reader_not_found")

    # Base updates (non-Phase-1)
    base_updates = {
        k: v for k, v in body.model_dump(exclude_none=True).items()
        if k not in ("profile_id", "hardware_class", "overrides")
    }
    if base_updates:
        await reader_repo.update_reader(db, reader_id, **base_updates)

    # Phase 1 updates
    hw_class = body.hardware_class or reader.hardware_class

    if body.hardware_class is not None:
        if body.hardware_class not in HARDWARE_CLASSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown hardware_class '{body.hardware_class}'",
            )
        reader.hardware_class = body.hardware_class

    if body.profile_id is not None:
        profile = await db.get(ReaderProfile, body.profile_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_not_found")
        reader.profile_id = body.profile_id

    if body.overrides is not None:
        await _validate_overrides(db, hw_class, body.overrides)
        # Replace all existing overrides
        result = await db.execute(
            select(ReaderParamOverride).where(
                ReaderParamOverride.reader_id == reader_id
            )
        )
        for row in result.scalars().all():
            await db.delete(row)
        await db.flush()
        for k, v in body.overrides.items():
            db.add(ReaderParamOverride(
                reader_id=reader_id,
                param_key=k,
                value=v,
            ))

        # Security-downgrade audit (write path): resolve with the api_key so any
        # ignored downgrade (e.g. an override trying to disable a security profile's
        # handover/BLE gate) is recorded in AdminAuditLog before commit. The GET
        # config-script path deliberately does NOT do this (reads must not write).
        await resolve_reader_params(db, reader, api_key=api_key)

    # Use mode="json" so Pydantic v2 serializes UUIDs to strings for JSON storage
    await _audit(
        db, api_key, "patch_reader", reader_id_hex,
        body.model_dump(mode="json", exclude_none=True),
    )
    await db.commit()
    return {"ok": True}
