"""GET /metrics — Prometheus exposition.

Доступен без auth — стандартное допущение для k8s/Prometheus scraping
(защита на уровне сети: либо ограничить port внутренней сетью, либо в
nginx-overlay добавить basic-auth).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from scud.observability.business_metrics import refresh_business_gauges
from scud.observability.registry import registry


async def metrics_endpoint(request: Request) -> Response:
    # Бизнес-gauge'и пересчитываем по требованию — это упрощает race-condition
    # обнаружения по сравнению с раздельным фоновым job.
    await refresh_business_gauges()
    return Response(
        content=registry.render_all(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
