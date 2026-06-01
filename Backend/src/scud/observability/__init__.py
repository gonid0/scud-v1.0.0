"""Lightweight observability — Prometheus exposition + structured logging.

Намеренно НЕ тянем prometheus_client как dep: проекту нужны 5–7 метрик
(http_requests_total, http_request_duration_seconds, реальные бизнес-счётчики),
и собственная реализация — это ~120 строк + zero deps. Если когда-нибудь
понадобятся histogram с reservoir-sampling, summary, exemplars — переходим
на prometheus_client.

Структура:
  - registry.py    — глобальный registry + Counter / Gauge / Histogram
  - middleware.py  — Starlette middleware: http_requests + duration
  - endpoint.py    — /metrics handler с text/plain expositon format
"""

from scud.observability.registry import (
    Counter,
    Gauge,
    Histogram,
    registry,
)
from scud.observability.endpoint import metrics_endpoint
from scud.observability.middleware import PrometheusMiddleware

__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "registry",
    "metrics_endpoint",
    "PrometheusMiddleware",
]
