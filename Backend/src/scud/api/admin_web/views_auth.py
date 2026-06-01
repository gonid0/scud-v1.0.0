"""Login / logout / cookie management для admin web-panel."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from scud.api.admin_web.auth import authenticate_api_key
from scud.api.admin_web.config import (
    COOKIE_MAX_AGE_SECONDS,
    COOKIE_NAME,
    TEMPLATES,
    encode_session,
)
from scud.api.deps import DbSession

router = APIRouter()


@router.get("/login", include_in_schema=False)
async def login_page(request: Request, next: Optional[str] = None, error: Optional[str] = None):
    return TEMPLATES.TemplateResponse(
        request, "login.html", {"next": next, "error": error}
    )


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    db: DbSession,
    api_key: str = Form(...),
    next: str = Form("/admin/"),
):
    row = await authenticate_api_key(db, api_key)
    if row is None:
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"next": next, "error": "Неверный ключ или это не admin-key"},
            status_code=401,
        )

    # Защита от open-redirect: `next` обязан начинаться с /admin/.
    safe_next = next if next.startswith("/admin/") else "/admin/"
    resp = RedirectResponse(url=safe_next, status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=encode_session(str(row.api_key_id)),
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # secure=True в prod должен выставляться через прокси/настройку,
        # здесь оставляем False чтобы dev-режим работал без TLS.
        secure=False,
        path="/",
    )
    return resp


@router.post("/logout", include_in_schema=False)
async def logout_submit() -> RedirectResponse:
    resp = RedirectResponse(url="/admin/login?flash=Вы%20вышли", status_code=303)
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp
