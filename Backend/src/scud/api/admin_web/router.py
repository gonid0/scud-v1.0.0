"""Главный роутер admin web-panel: подключает все sub-routers под /admin."""

from __future__ import annotations

from fastapi import APIRouter

from scud.api.admin_web.views_auth import router as auth_router
from scud.api.admin_web.views_dashboard import router as dashboard_router
from scud.api.admin_web.views_keys import router as keys_router
from scud.api.admin_web.views_meta import router as meta_router
from scud.api.admin_web.views_passages import router as passages_router
from scud.api.admin_web.views_permits import router as permits_router
from scud.api.admin_web.views_readers import router as readers_router
from scud.api.admin_web.views_users import router as users_router

router = APIRouter(prefix="/admin", include_in_schema=False)

# Auth first — нужны до того, как другие views попытаются require admin.
router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(users_router)
router.include_router(readers_router)
router.include_router(permits_router)
router.include_router(keys_router)
router.include_router(passages_router)
router.include_router(meta_router)
