"""Admin reader-profiles endpoints (Phase 1 — server parameterization).

CRUD for /admin/reader-profiles and config-script endpoint for readers.
All endpoints require admin API key authentication.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from scud.api.deps import AdminApiKey, DbSession
from scud.db.models import (
    AdminAuditLog,
    ParamBounds,
    Reader,
    ReaderParamOverride,
    ReaderProfile,
    ReaderProfileParam,
)
from scud.domain.reader_config_resolver import resolve_reader_params
from scud.domain.reader_param_catalog import (
    HARDWARE_CLASSES,
    PARAMS_BY_KEY,
)

router = APIRouter(prefix="/reader-profiles", tags=["admin-reader-profiles"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audit(db, api_key, action: str, target_id: str, details: dict | None = None) -> None:
    db.add(AdminAuditLog(
        actor_type="api_key",
        actor_id=api_key.api_key_id,
        action=action,
        target_type="reader_profile",
        target_id=target_id,
        details=details,
    ))


async def _validate_params(
    db: Any,
    hardware_class: str,
    params: dict[str, int],
) -> None:
    """Validate every param_key exists in the catalog and value is in bounds.

    Raises HTTP 422 with a descriptive error if any param fails validation.
    """
    if hardware_class not in HARDWARE_CLASSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown hardware_class '{hardware_class}'. Valid: {HARDWARE_CLASSES}",
        )

    for key, value in params.items():
        if key not in PARAMS_BY_KEY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown param_key '{key}'. Valid keys: {sorted(PARAMS_BY_KEY.keys())}",
            )

        # Look up bounds from DB (the authoritative source after migration seed)
        result = await db.execute(
            select(ParamBounds).where(
                ParamBounds.param_key == key,
                ParamBounds.hardware_class == hardware_class,
            )
        )
        bounds = result.scalar_one_or_none()
        if bounds is None:
            # Fallback to catalog
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
# Pydantic schemas
# ---------------------------------------------------------------------------

class CreateProfileRequest(BaseModel):
    name: str
    hardware_class: str
    description: Optional[str] = None
    params: dict[str, int] = {}


class PatchProfileRequest(BaseModel):
    name: Optional[str] = None
    hardware_class: Optional[str] = None
    description: Optional[str] = None
    params: Optional[dict[str, int]] = None


class ProfileResponse(BaseModel):
    profile_id: str
    name: str
    hardware_class: str
    description: Optional[str]
    params: dict[str, int]
    created_at: str
    updated_at: str


def _profile_to_dict(profile: ReaderProfile) -> dict:
    return {
        "profile_id": str(profile.profile_id),
        "name": profile.name,
        "hardware_class": profile.hardware_class,
        "description": profile.description,
        "params": {p.param_key: p.value for p in profile.params},
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /admin/reader-profiles — list all profiles
# ---------------------------------------------------------------------------

@router.get("")
async def list_profiles(api_key: AdminApiKey, db: DbSession) -> dict:
    result = await db.execute(select(ReaderProfile))
    profiles = result.scalars().unique().all()
    # Eagerly load params for each profile
    for p in profiles:
        await db.refresh(p, ["params"])
    return {"items": [_profile_to_dict(p) for p in profiles]}


# ---------------------------------------------------------------------------
# POST /admin/reader-profiles — create a profile
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: CreateProfileRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    await _validate_params(db, body.hardware_class, body.params)

    profile = ReaderProfile(
        profile_id=uuid.uuid4(),
        name=body.name,
        hardware_class=body.hardware_class,
        description=body.description,
    )
    db.add(profile)
    await db.flush()  # get profile_id

    for k, v in body.params.items():
        db.add(ReaderProfileParam(
            profile_id=profile.profile_id,
            param_key=k,
            value=v,
        ))

    _audit(db, api_key, "create_reader_profile", str(profile.profile_id), {"name": body.name})
    await db.commit()
    await db.refresh(profile, ["params"])
    return _profile_to_dict(profile)


# ---------------------------------------------------------------------------
# GET /admin/reader-profiles/{profile_id} — get one profile
# ---------------------------------------------------------------------------

@router.get("/{profile_id}")
async def get_profile(
    profile_id: uuid.UUID,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    profile = await db.get(ReaderProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_not_found")
    await db.refresh(profile, ["params"])
    return _profile_to_dict(profile)


# ---------------------------------------------------------------------------
# PATCH /admin/reader-profiles/{profile_id} — update a profile
# ---------------------------------------------------------------------------

@router.patch("/{profile_id}")
async def patch_profile(
    profile_id: uuid.UUID,
    body: PatchProfileRequest,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    from datetime import datetime, timezone

    profile = await db.get(ReaderProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_not_found")

    hw_class = body.hardware_class or profile.hardware_class

    if body.params is not None:
        await _validate_params(db, hw_class, body.params)

    if body.name is not None:
        profile.name = body.name
    if body.hardware_class is not None:
        profile.hardware_class = body.hardware_class
    if body.description is not None:
        profile.description = body.description
    profile.updated_at = datetime.now(timezone.utc)

    if body.params is not None:
        # Replace all params for this profile
        result = await db.execute(
            select(ReaderProfileParam).where(
                ReaderProfileParam.profile_id == profile_id
            )
        )
        for row in result.scalars().all():
            await db.delete(row)
        await db.flush()
        for k, v in body.params.items():
            db.add(ReaderProfileParam(
                profile_id=profile_id,
                param_key=k,
                value=v,
            ))

    _audit(db, api_key, "patch_reader_profile", str(profile_id))
    await db.commit()
    await db.refresh(profile, ["params"])
    return _profile_to_dict(profile)


# ---------------------------------------------------------------------------
# DELETE /admin/reader-profiles/{profile_id} — delete a profile
# ---------------------------------------------------------------------------

@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: uuid.UUID,
    api_key: AdminApiKey,
    db: DbSession,
) -> dict:
    profile = await db.get(ReaderProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_not_found")
    _audit(db, api_key, "delete_reader_profile", str(profile_id))
    await db.delete(profile)
    await db.commit()
    return {"ok": True}
