"""Мета-страницы админки: API keys + Audit log."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from scud.api.admin_web.auth import CurrentAdmin, WebDb
from scud.api.admin_web.config import TEMPLATES
from scud.db.models import AdminAuditLog, ApiKey, WebhookSubscription

router = APIRouter()


# ----------------------------------------------------------------------------
# API keys
# ----------------------------------------------------------------------------

@router.get("/api-keys", include_in_schema=False)
async def list_api_keys(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    flash_plaintext: Optional[str] = None,
):
    keys = (await db.execute(
        select(ApiKey).order_by(ApiKey.created_at.desc()).limit(200)
    )).scalars().all()
    return TEMPLATES.TemplateResponse(
        request,
        "meta/api_keys.html",
        {
            "current_admin": admin,
            "keys": keys,
            "flash_plaintext": flash_plaintext,
        },
    )


@router.post("/api-keys", include_in_schema=False)
async def create_api_key(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    name: str = Form(..., max_length=128),
    kind: str = Form("admin"),
):
    if kind not in ("admin", "integration"):
        return RedirectResponse(url="/admin/api-keys?error=Bad+kind", status_code=303)

    prefix = "sk_admin_" if kind == "admin" else "sk_integration_"
    plaintext = prefix + secrets.token_urlsafe(36)
    new_key = ApiKey(
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        key_prefix=plaintext[:8],
        kind=kind,
        name=name,
    )
    db.add(new_key)
    await db.flush()
    db.add(AdminAuditLog(
        actor_type="api_key", actor_id=admin.api_key_id,
        action="create_api_key", target_type="api_key",
        target_id=str(new_key.api_key_id),
    ))
    await db.commit()
    # Plaintext показывается один раз и только в URL для упрощения flow;
    # для гарантии «не уйдёт в логи прокси» здесь production-ready реализация
    # передавала бы через flash в сессии. MVP-trade-off в пользу простоты.
    return RedirectResponse(
        url=f"/admin/api-keys?flash_plaintext={plaintext}",
        status_code=303,
    )


@router.post("/api-keys/{api_key_id}/revoke", include_in_schema=False)
async def revoke_api_key_action(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    api_key_id: uuid.UUID,
):
    if admin.api_key_id == api_key_id:
        return RedirectResponse(
            url="/admin/api-keys?error=Нельзя+отозвать+самого+себя", status_code=303
        )
    await db.execute(
        update(ApiKey)
        .where(ApiKey.api_key_id == api_key_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.add(AdminAuditLog(
        actor_type="api_key", actor_id=admin.api_key_id,
        action="revoke_api_key", target_type="api_key",
        target_id=str(api_key_id),
    ))
    await db.commit()
    return RedirectResponse(url="/admin/api-keys?flash=Revoked", status_code=303)


# ----------------------------------------------------------------------------
# Audit log
# ----------------------------------------------------------------------------

@router.get("/audit", include_in_schema=False)
async def audit_log(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    cursor: Optional[int] = None,
    limit: int = 100,
):
    stmt = select(AdminAuditLog).order_by(AdminAuditLog.audit_id.desc()).limit(limit)
    if cursor:
        stmt = stmt.where(AdminAuditLog.audit_id < cursor)
    entries = (await db.execute(stmt)).scalars().all()

    # Резолвим actor (api key name).
    actor_ids = {e.actor_id for e in entries}
    actors_map = {}
    if actor_ids:
        for k in (await db.execute(select(ApiKey).where(ApiKey.api_key_id.in_(actor_ids)))).scalars():
            actors_map[k.api_key_id] = k

    next_cursor = entries[-1].audit_id if len(entries) == limit else None
    return TEMPLATES.TemplateResponse(
        request,
        "meta/audit.html",
        {
            "current_admin": admin,
            "entries": entries,
            "actors_map": actors_map,
            "next_cursor": next_cursor,
        },
    )


# ----------------------------------------------------------------------------
# Webhook subscriptions
# ----------------------------------------------------------------------------

@router.get("/webhooks", include_in_schema=False)
async def webhooks_list(
    request: Request, admin: CurrentAdmin, db: WebDb
):
    subs = (await db.execute(
        select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
    )).scalars().all()
    return TEMPLATES.TemplateResponse(
        request, "meta/webhooks.html",
        {"current_admin": admin, "subs": subs},
    )


@router.post("/webhooks", include_in_schema=False)
async def webhooks_create(
    request: Request,
    admin: CurrentAdmin,
    db: WebDb,
    name: str = Form(..., max_length=128),
    url: str = Form(..., max_length=512),
    event_types: str = Form("passage_event"),
    secret: str = Form(""),
):
    # Минимальная валидация URL: должна быть http(s) схема.
    if not (url.startswith("http://") or url.startswith("https://")):
        return RedirectResponse(url="/admin/webhooks?error=URL+должен+быть+http(s)", status_code=303)

    sub = WebhookSubscription(
        name=name, url=url, event_types=event_types,
        secret=secret or None,
        created_by_api_key=admin.api_key_id,
    )
    db.add(sub)
    await db.flush()
    db.add(AdminAuditLog(
        actor_type="api_key", actor_id=admin.api_key_id,
        action="create_webhook", target_type="webhook",
        target_id=str(sub.webhook_id),
        details={"url": url, "event_types": event_types},
    ))
    await db.commit()
    return RedirectResponse(url="/admin/webhooks?flash=Создан", status_code=303)


@router.post("/webhooks/{webhook_id}/toggle", include_in_schema=False)
async def webhooks_toggle(
    request: Request, admin: CurrentAdmin, db: WebDb, webhook_id: uuid.UUID
):
    sub = await db.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise HTTPException(status_code=404)
    sub.is_active = not sub.is_active
    # При ручной реактивации сбрасываем счётчик подряд-фейлов, иначе следующий
    # же неудачный POST моментально вернёт её обратно в disabled.
    if sub.is_active:
        sub.consecutive_failures = 0
    await db.commit()
    return RedirectResponse(url="/admin/webhooks?flash=Обновлено", status_code=303)


@router.post("/webhooks/{webhook_id}/delete", include_in_schema=False)
async def webhooks_delete(
    request: Request, admin: CurrentAdmin, db: WebDb, webhook_id: uuid.UUID
):
    sub = await db.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise HTTPException(status_code=404)
    await db.delete(sub)
    db.add(AdminAuditLog(
        actor_type="api_key", actor_id=admin.api_key_id,
        action="delete_webhook", target_type="webhook",
        target_id=str(webhook_id),
    ))
    await db.commit()
    return RedirectResponse(url="/admin/webhooks?flash=Удалён", status_code=303)


@router.post("/webhooks/{webhook_id}/test", include_in_schema=False)
async def webhooks_test_fire(
    request: Request, admin: CurrentAdmin, db: WebDb, webhook_id: uuid.UUID
):
    """Поставить test event в очередь — отлично подходит для smoke-проверки
    URL и HMAC-подписи на стороне приёмника без необходимости провоцировать
    реальный проход."""
    from scud.db.repositories.tasks import enqueue_task

    sub = await db.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise HTTPException(status_code=404)
    await enqueue_task(db, "notify_webhook", {
        "webhook_id": str(webhook_id),
        "event_type": "test",
        "data": {
            "fired_by": str(admin.api_key_id),
            "fired_at": datetime.now(timezone.utc).isoformat(),
        },
    })
    await db.commit()
    return RedirectResponse(url="/admin/webhooks?flash=Test+поставлен+в+очередь", status_code=303)
