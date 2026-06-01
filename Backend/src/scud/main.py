"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scud.config import settings

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan: run startup checks before serving requests."""
    # BE-SEC-04: fail fast if the admin web panel is configured with the dev
    # default secret in a production environment.
    from scud.api.admin_web.config import check_web_secret
    check_web_secret(is_production=settings.is_production)

    yield
    # (shutdown logic goes here if needed)


def create_app() -> FastAPI:
    application = FastAPI(
        title="SCUD Backend",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Prometheus middleware подключаем ДО routers — иначе он не увидит запросы,
    # обработанные в downstream-роутерах.
    from scud.observability import PrometheusMiddleware
    application.add_middleware(PrometheusMiddleware)

    # App routers
    from scud.api.app.auth import router as auth_router
    from scud.api.app.courier import router as courier_router
    from scud.api.app.keys import router as keys_router
    from scud.api.app.my_data import router as my_data_router
    from scud.api.app.permits import router as permits_router
    from scud.api.app.readers import router as readers_router
    from scud.api.app.reports import router as reports_router

    app_prefix = "/api/v1/app"
    application.include_router(auth_router, prefix=app_prefix)
    application.include_router(my_data_router, prefix=app_prefix)
    application.include_router(permits_router, prefix=app_prefix)
    application.include_router(keys_router, prefix=app_prefix)
    application.include_router(readers_router, prefix=app_prefix)
    application.include_router(courier_router, prefix=app_prefix)
    application.include_router(reports_router, prefix=app_prefix)

    # Admin routers
    from scud.api.admin.api_keys import router as admin_api_keys_router
    from scud.api.admin.keys import router as admin_keys_router
    from scud.api.admin.observability import router as observability_router
    from scud.api.admin.passages import router as admin_passages_router
    from scud.api.admin.permits import router as admin_permits_router
    from scud.api.admin.reader_groups import router as admin_groups_router
    from scud.api.admin.reader_profiles import router as admin_reader_profiles_router
    from scud.api.admin.readers import router as admin_readers_router
    from scud.api.admin.users import router as admin_users_router

    admin_prefix = "/api/v1/admin"
    application.include_router(admin_users_router, prefix=admin_prefix)
    application.include_router(admin_groups_router, prefix=admin_prefix)
    application.include_router(admin_readers_router, prefix=admin_prefix)
    application.include_router(admin_reader_profiles_router, prefix=admin_prefix)
    application.include_router(admin_permits_router, prefix=admin_prefix)
    application.include_router(admin_keys_router, prefix=admin_prefix)
    application.include_router(admin_api_keys_router, prefix=admin_prefix)
    application.include_router(observability_router, prefix=admin_prefix)
    application.include_router(admin_passages_router, prefix=admin_prefix)

    # Server-rendered admin web panel (Jinja2 + HTMX). Подключается под /admin,
    # не пересекается с /api/v1/admin (JSON API).
    from scud.api.admin_web.router import router as admin_web_router
    application.include_router(admin_web_router)

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    # Prometheus exposition. Без auth — стандартно для k8s/Prometheus scraping.
    # В prod-overlay nginx может закрыть external access; для внутренней scrape
    # достаточно internal-network reachability.
    from scud.observability import metrics_endpoint
    application.add_route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False)

    @application.get("/")
    async def root_redirect():
        # Удобный «корень» — направляем в админку. JSON-API остаётся на /api/v1/.
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin/", status_code=303)

    return application


app = create_app()
