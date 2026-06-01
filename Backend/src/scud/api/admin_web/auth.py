"""Cookie-сессия для admin web-panel.

Логика: пользователь вводит plaintext admin API key → backend сравнивает
sha256-хеш с api_keys.key_hash → подписывает api_key_id (urlsafe-timed) →
кладёт в httpOnly cookie. На каждый запрос dependency декодирует cookie,
по api_key_id находит row, проверяет revoked_at/expires_at.

Это **не** заменяет X-Api-Key — раздельные пути:
  /api/v1/admin/*          — JSON API, X-Api-Key (для скриптов, mobile)
  /admin/*                 — HTML панель, cookie (для людей в браузере)
Один и тот же ApiKey-row может использоваться обоими путями.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scud.api.admin_web.config import COOKIE_NAME, decode_session
from scud.db.models import ApiKey
from scud.db.session import get_session


async def authenticate_api_key(
    db: AsyncSession, plaintext: str
) -> Optional[ApiKey]:
    """Validate plaintext API key, return ApiKey row if valid (admin kind)."""
    if not plaintext:
        return None
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return None
    if api_key.expires_at is not None and api_key.expires_at <= now:
        return None
    if api_key.kind != "admin":
        return None
    return api_key


async def get_admin_web_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> ApiKey:
    """Dependency для всех HTML-страниц админки.

    Если cookie невалиден / истёк / api_key revoked → 303 редирект на /admin/login.
    HTTPException(303) — это «инверсия» обычного 401: для HTML удобнее
    редиректить, а не показывать голый JSON-error.
    """
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        raise _redirect_to_login(request)
    payload = decode_session(cookie)
    if not payload or "akid" not in payload:
        raise _redirect_to_login(request)

    import uuid as _uuid
    try:
        api_key_id = _uuid.UUID(payload["akid"])
    except (ValueError, TypeError):
        raise _redirect_to_login(request)

    api_key = await db.get(ApiKey, api_key_id)
    now = datetime.now(timezone.utc)
    if (api_key is None
            or api_key.revoked_at is not None
            or (api_key.expires_at and api_key.expires_at <= now)
            or api_key.kind != "admin"):
        raise _redirect_to_login(request)

    return api_key


def _redirect_to_login(request: Request) -> HTTPException:
    """Возвращает HTTPException, который FastAPI отдаст как 303 redirect.

    Сохраняем `next` query для возврата после логина.
    """
    next_url = str(request.url.path)
    if request.url.query:
        next_url += "?" + request.url.query
    return HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/admin/login?next={next_url}"},
    )


CurrentAdmin = Annotated[ApiKey, Depends(get_admin_web_user)]
WebDb = Annotated[AsyncSession, Depends(get_session)]
