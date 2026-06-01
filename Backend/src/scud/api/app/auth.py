"""App auth endpoints (§5.1)."""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from scud.api.deps import CurrentSession, DbSession
from scud.db.repositories import users as user_repo
from scud.domain import auth as auth_domain

router = APIRouter(prefix="/auth", tags=["app-auth"])


class DeviceInfo(BaseModel):
    model: str = ""
    os: str = ""
    app_version: str = ""


class LoginRequest(BaseModel):
    login: str
    password: str
    device_info: DeviceInfo = DeviceInfo()


class TokenResponse(BaseModel):
    session_token: str
    refresh_token: str
    user_id: int
    user_group_id: str
    display_name: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    try:
        sess, session_token, refresh_token = await auth_domain.login(
            db, body.login, body.password
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user_inactive")

    user = await user_repo.get_user_by_id(db, sess.user_id)
    await db.commit()
    return TokenResponse(
        session_token=session_token,
        refresh_token=refresh_token,
        user_id=user.user_id,
        user_group_id=str(user.user_group_id),
        display_name=user.display_name,
    )


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: DbSession) -> TokenResponse:
    try:
        sess, session_token, refresh_token = await auth_domain.refresh(db, body.refresh_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_refresh_token"
        )
    user = await user_repo.get_user_by_id(db, sess.user_id)
    await db.commit()
    return TokenResponse(
        session_token=session_token,
        refresh_token=refresh_token,
        user_id=user.user_id,
        user_group_id=str(user.user_group_id),
        display_name=user.display_name,
    )


class RegisterDeviceRequest(BaseModel):
    phone_pubkey: str  # base64
    device_label: Optional[str] = None


class RegisterDeviceResponse(BaseModel):
    device_id: str


@router.post("/register-device", response_model=RegisterDeviceResponse)
async def register_device(
    body: RegisterDeviceRequest,
    current_session: CurrentSession,
    db: DbSession,
) -> RegisterDeviceResponse:
    try:
        phone_pubkey = base64.b64decode(body.phone_pubkey)
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_base64")

    try:
        device = await auth_domain.register_device(
            db, current_session, phone_pubkey, body.device_label
        )
    except ValueError as e:
        if "pubkey_conflict" in str(e):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="pubkey_conflict")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    return RegisterDeviceResponse(device_id=str(device.device_id))


@router.post("/logout")
async def logout(current_session: CurrentSession, db: DbSession) -> dict:
    await auth_domain.logout(db, current_session)
    await db.commit()
    return {"ok": True}
