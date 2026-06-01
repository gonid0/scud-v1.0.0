"""Authentication domain logic: login, refresh, register-device, logout."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.config import settings
from scud.db.models import Session as SessionModel
from scud.db.models import UserDevice
from scud.db.repositories import devices as dev_repo
from scud.db.repositories import users as user_repo

# Fixed argon2id params per shared §2.6
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hash: str, password: str) -> bool:
    try:
        return _ph.verify(hash, password)
    except VerifyMismatchError:
        return False


def _new_tokens() -> tuple[str, str]:
    return secrets.token_urlsafe(48), secrets.token_urlsafe(48)


async def login(
    session: AsyncSession,
    login: str,
    password: str,
) -> tuple[SessionModel, str, str]:
    """Authenticate and create a new session. Returns (session_obj, session_token, refresh_token)."""
    user = await user_repo.get_user_by_login(session, login)
    if user is None or not verify_password(user.password_hash, password):
        raise ValueError("invalid_credentials")
    if not user.is_active:
        raise PermissionError("user_inactive")

    session_token, refresh_token = _new_tokens()
    now = datetime.now(timezone.utc)
    sess = SessionModel(
        user_id=user.user_id,
        session_token=session_token,
        refresh_token=refresh_token,
        issued_at=now,
        session_expires_at=now + timedelta(hours=settings.session_lifetime_hours),
        refresh_expires_at=now + timedelta(days=settings.refresh_lifetime_days),
    )
    session.add(sess)
    await session.flush()
    await session.refresh(sess)
    return sess, session_token, refresh_token


async def refresh(
    session: AsyncSession,
    refresh_token: str,
) -> tuple[SessionModel, str, str]:
    """Rotate refresh token and return new session."""
    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(SessionModel).where(
            SessionModel.refresh_token == refresh_token,
            SessionModel.revoked_at.is_(None),
            SessionModel.refresh_expires_at > now,
        )
    )
    old = result.scalar_one_or_none()
    if old is None:
        raise ValueError("invalid_refresh_token")

    # Revoke old
    old.revoked_at = now

    new_sess_token, new_refresh_token = _new_tokens()
    new_sess = SessionModel(
        user_id=old.user_id,
        device_id=old.device_id,
        session_token=new_sess_token,
        refresh_token=new_refresh_token,
        issued_at=now,
        session_expires_at=now + timedelta(hours=settings.session_lifetime_hours),
        refresh_expires_at=now + timedelta(days=settings.refresh_lifetime_days),
    )
    session.add(new_sess)
    await session.flush()
    await session.refresh(new_sess)
    return new_sess, new_sess_token, new_refresh_token


async def register_device(
    session: AsyncSession,
    current_session: SessionModel,
    phone_pubkey: bytes,
    device_label: Optional[str],
) -> UserDevice:
    """Register a new device, deactivating old ones.

    Идемпотентность: если device с таким phone_pubkey уже зарегистрирован
    на текущего пользователя — просто привязываем текущую сессию к
    существующей записи (поднимая is_active=True на случай, если его
    деактивировало предыдущей регистрацией с другого устройства).
    Раньше в этом случае бросалось `pubkey_conflict` → сессия оставалась
    без device_id → /courier/download возвращал 400 no_device_registered.
    """
    if len(phone_pubkey) != 32:
        raise ValueError("phone_pubkey must be 32 bytes")

    existing = await dev_repo.get_device_by_pubkey(session, phone_pubkey)
    now = datetime.now(timezone.utc)

    if existing is not None:
        # Pubkey принадлежит другому пользователю — это реальный конфликт.
        if existing.user_id != current_session.user_id:
            raise ValueError("pubkey_conflict")
        # Повторный логин с того же телефона. Привязываем текущую сессию
        # к уже существующему device, поднимаем is_active если его выключали.
        from scud.db.models import UserDevice as UserDeviceModel
        await session.execute(
            update(UserDeviceModel)
            .where(UserDeviceModel.device_id == existing.device_id)
            .values(is_active=True)
        )
        await session.execute(
            update(SessionModel)
            .where(SessionModel.session_id == current_session.session_id)
            .values(device_id=existing.device_id)
        )
        current_session.device_id = existing.device_id
        return existing

    # Revoke sessions for currently-active devices
    await dev_repo.deactivate_all_user_devices(session, current_session.user_id)

    # Revoke sessions for old devices, except current
    await session.execute(
        update(SessionModel)
        .where(
            SessionModel.user_id == current_session.user_id,
            SessionModel.session_id != current_session.session_id,
            SessionModel.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    device = await dev_repo.create_device(
        session,
        user_id=current_session.user_id,
        phone_pubkey=phone_pubkey,
        device_label=device_label,
    )

    # Bind current session to new device
    await session.execute(
        update(SessionModel)
        .where(SessionModel.session_id == current_session.session_id)
        .values(device_id=device.device_id)
    )
    current_session.device_id = device.device_id
    return device


async def logout(session: AsyncSession, current_session: SessionModel) -> None:
    now = datetime.now(timezone.utc)
    current_session.revoked_at = now
    await session.flush()
