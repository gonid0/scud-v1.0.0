"""Минимальный Prometheus-совместимый registry.

Поддерживает Counter, Gauge и Histogram (linear buckets), сериализацию в
text/plain exposition format v0.0.4. Labels хранятся как dict[frozenset, value]
— stable order при render.

Thread-safety: используем простой Lock — счётчики в FastAPI обычно
инкрементятся из event-loop'а, contention минимальный.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Mapping


def _labelkey(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def _escape_label_value(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in labels) + "}"


class _Metric:
    """Base class — subclasses register themselves in registry."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()):
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._lock = threading.Lock()
        registry.register(self)

    def render(self) -> str:
        raise NotImplementedError


class Counter(_Metric):
    """Monotonically increasing counter."""

    METRIC_TYPE = "counter"

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()):
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        key = _labelkey(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}",
                 f"# TYPE {self.name} {self.METRIC_TYPE}"]
        with self._lock:
            items = list(self._values.items())
        if not items:
            return "\n".join(lines)
        for key, value in items:
            lines.append(f"{self.name}{_render_labels(key)} {value:g}")
        return "\n".join(lines)


class Gauge(_Metric):
    """Point-in-time value (can go up/down)."""

    METRIC_TYPE = "gauge"

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()):
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def set(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        key = _labelkey(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        key = _labelkey(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        self.inc(-amount, labels)

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}",
                 f"# TYPE {self.name} {self.METRIC_TYPE}"]
        with self._lock:
            items = list(self._values.items())
        for key, value in items:
            lines.append(f"{self.name}{_render_labels(key)} {value:g}")
        return "\n".join(lines)


@dataclass
class _HistogramSample:
    """Aggregated counts per bucket + sum + count."""

    bucket_counts: list[int]  # cumulative >=upper_bound для каждого bound, len == len(bounds)+1 (last = +Inf)
    sum: float = 0.0
    count: int = 0


class Histogram(_Metric):
    """Histogram with explicit upper bounds (linear / custom).

    Реализация — простая incremental counter в подходящем bucket'е (без reservoir).
    Этого хватает для p50/p95 SLI на трафике до 100 RPS.
    """

    METRIC_TYPE = "histogram"
    DEFAULT_BUCKETS = (
        0.005, 0.01, 0.025, 0.05, 0.075,
        0.1, 0.25, 0.5, 0.75,
        1.0, 2.5, 5.0, 7.5, 10.0,
    )

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ):
        super().__init__(name, help_text, label_names)
        self.buckets = tuple(sorted(buckets))
        self._samples: dict[tuple[tuple[str, str], ...], _HistogramSample] = {}

    def observe(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        key = _labelkey(labels)
        with self._lock:
            sample = self._samples.get(key)
            if sample is None:
                sample = _HistogramSample(bucket_counts=[0] * (len(self.buckets) + 1))
                self._samples[key] = sample
            sample.sum += value
            sample.count += 1
            # Find first bucket that covers value.
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    sample.bucket_counts[i] += 1
                    # Cumulative: всё что попало в bucket[i], попадает и в [i+1], [i+2], ...
                    # Сериализуем cumulative позже, в render(). Здесь храним incremental.
                    return
            sample.bucket_counts[-1] += 1  # +Inf bucket

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}",
                 f"# TYPE {self.name} {self.METRIC_TYPE}"]
        with self._lock:
            samples = list(self._samples.items())
        for key, sample in samples:
            cumulative = 0
            for i, bound in enumerate(self.buckets):
                cumulative += sample.bucket_counts[i]
                lbl_key = key + (("le", _format_bucket(bound)),)
                lines.append(f"{self.name}_bucket{_render_labels(lbl_key)} {cumulative}")
            cumulative += sample.bucket_counts[-1]
            lbl_inf = key + (("le", "+Inf"),)
            lines.append(f"{self.name}_bucket{_render_labels(lbl_inf)} {cumulative}")
            lines.append(f"{self.name}_sum{_render_labels(key)} {sample.sum:g}")
            lines.append(f"{self.name}_count{_render_labels(key)} {sample.count}")
        return "\n".join(lines)


def _format_bucket(b: float) -> str:
    # 0.005 → "0.005", 1.0 → "1.0" (но не "1"), 10.0 → "10.0".
    if b == int(b):
        return f"{b:.1f}"
    return f"{b:g}"


class _Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._metrics: dict[str, _Metric] = {}

    def register(self, metric: _Metric) -> None:
        with self._lock:
            if metric.name in self._metrics:
                # Идемпотентность для перезагрузок (например в тестах).
                # Если повторно зарегистрировали с другим типом — это баг.
                existing = self._metrics[metric.name]
                if type(existing) is type(metric):
                    return
                raise ValueError(
                    f"metric {metric.name} already registered as {type(existing).__name__}"
                )
            self._metrics[metric.name] = metric

    def render_all(self) -> str:
        with self._lock:
            metrics = list(self._metrics.values())
        return "\n\n".join(m.render() for m in metrics) + "\n"

    def _reset_for_tests(self) -> None:
        """Только для unit-тестов: сбрасывает все registered metrics."""
        with self._lock:
            self._metrics.clear()


registry = _Registry()
