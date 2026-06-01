"""App keys endpoints (§5.3)."""

from __future__ import annotations

import base64
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from scud.api.deps import CurrentSession, CurrentUser, DbSession
from scud.domain.keys import issue_key, revoke_key_on_server

router = APIRouter(prefix="/keys", tags=["app-keys"])


class KeyRequest(BaseModel):
    permit_id: uuid.UUID
    validity_seconds: int
    request_grant: bool = False
    # Опциональное начало действия ключа (unix-секунды). Если None — now.
    # Позволяет выпускать future-dated ключи (UI date-picker «start date»).
    issued_at_ts: Optional[int] = None


class IssuedKeyOut(BaseModel):
    key_id: str
    full_key_bytes: str
    issued_at: str
    expires_at: str


class TimeGrantOut(BaseModel):
    grant_id: str
    full_grant_bytes: str
    expires_at: str


class KeyResponse(BaseModel):
    issued_key: IssuedKeyOut
    time_grant: Optional[TimeGrantOut] = None


@router.post("/request", response_model=KeyResponse)
async def request_key(
    body: KeyRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> KeyResponse:
    try:
        key, grant = await issue_key(
            db,
            permit_id=body.permit_id,
            user_id=current_user.user_id,
            validity_seconds=body.validity_seconds,
            request_grant=body.request_grant,
            issued_at_ts=body.issued_at_ts,
        )
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_owner")
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        msg = str(e)
        if msg.startswith("ttl_too_long:"):
            max_ttl = int(msg.split(":")[1])
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "ttl_too_long", "max": max_ttl},
            )
        if msg == "ttl_invalid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "ttl_invalid"},
            )
        if msg == "permit_expired":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "permit_expired"},
            )
        if msg == "permit_not_started":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "permit_not_started"},
            )
        if msg.startswith("n_parallel_exceeded:"):
            parts = msg.split(":")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "n_parallel_exceeded", "active_count": int(parts[1]), "max": int(parts[2])},
            )
        if msg.startswith("unexpired_cap_exceeded:"):
            parts = msg.split(":")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "unexpired_cap_exceeded", "unexpired": int(parts[1]), "max": int(parts[2])},
            )
        if msg == "no_active_device":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "no_active_device"},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    await db.commit()

    grant_out = None
    if grant is not None:
        grant_out = TimeGrantOut(
            grant_id=str(grant.grant_id),
            full_grant_bytes=base64.b64encode(grant.full_grant_bytes).decode(),
            expires_at=grant.expires_at.isoformat(),
        )

    return KeyResponse(
        issued_key=IssuedKeyOut(
            key_id=key.key_id.hex(),
            full_key_bytes=base64.b64encode(key.full_key_bytes).decode(),
            issued_at=key.issued_at.isoformat(),
            expires_at=key.expires_at.isoformat(),
        ),
        time_grant=grant_out,
    )


@router.post("/{key_id}/revoke-on-server")
async def revoke_key(
    key_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    try:
        await revoke_key_on_server(db, key_id, current_user.user_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_owner")
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")
    except ValueError as e:
        if "not_active" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "not_active"},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    return {"ok": True}
