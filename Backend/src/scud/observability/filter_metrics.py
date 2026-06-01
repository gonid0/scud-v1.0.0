"""Метрики генерации filter_package (seed-scheme).

Событийные (выставляются в момент генерации фильтра), в отличие от
business_metrics, которые считаются по БД при scrape. Дают видимость
приближения ридера к насыщению ДО того, как выдача начнёт молча отказывать.
"""

from __future__ import annotations

from scud.observability.registry import Counter, Histogram

# Сколько генераций упало из-за насыщения ридера (whitelist-cap недостижим даже
# при максимальном m_bits) — ридер нужно перевыпускать (re-key).
scud_filter_oversaturated_total = Counter(
    "scud_filter_oversaturated_total",
    "filter generations that failed because the reader is oversaturated "
    "(whitelist cap unreachable even at max m_bits)",
)

# Распределение whitelist_count / whitelist_hard_cap по сгенерированным пакетам.
# 1.0 = ровно на cap. Рост хвоста к 1.0 = ридеры приближаются к насыщению.
scud_filter_whitelist_cap_utilization = Histogram(
    "scud_filter_whitelist_cap_utilization",
    "whitelist_count / whitelist_hard_cap per generated filter_package (1.0 = at cap)",
    buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0),
)
