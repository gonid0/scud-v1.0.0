"""Starlette middleware: считает http_requests_total + http_request_duration_seconds.

Route label берётся из `request.scope["route"].path` (если уже зарезолвлен
роутером) — это даёт стабильное low-cardinality имя вроде
`/api/v1/admin/permits/{permit_id}` вместо взрыва кардинальности от
конкретных UUID'ов.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from scud.observability.registry import Counter, Histogram

http_requests_total = Counter(
    "scud_http_requests_total",
    "Number of HTTP requests received, partitioned by method+path+status",
    label_names=("method", "path", "status"),
)
http_request_duration_seconds = Histogram(
    "scud_http_request_duration_seconds",
    "HTTP request duration in seconds",
    label_names=("method", "path"),
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed = time.perf_counter() - start
            # Достать route pattern. У /metrics и /health route может не успеть
            # зарезолвиться — fallback на raw path.
            route = request.scope.get("route")
            path = getattr(route, "path", None) or request.url.path
            method = request.method
            status = response.status_code if response is not None else 500

            http_requests_total.inc(labels={
                "method": method, "path": path, "status": str(status),
            })
            http_request_duration_seconds.observe(elapsed, labels={
                "method": method, "path": path,
            })
