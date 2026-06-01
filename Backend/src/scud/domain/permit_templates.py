"""Permit-templates: типовые «шаблоны выдачи» (день/неделя/месяц/год/etc.)
для UI и интеграций.

Идея: вместо того, чтобы каждый раз вводить даты руками, админ выбирает
preset «месяц с сегодня» — и `valid_from`/`valid_until` подставляются
автоматически. Шаблоны не хранятся в БД — это чистая функция от текущего
времени и параметров permit'а (n_parallel, max_token_ttl).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class PermitTemplate:
    """Шаблон permit с относительными датами."""

    id: str               # "day" | "week" | "month" | "year" | ...
    label: str            # человеческое название
    description: str      # для tooltip / sidebar
    duration_days: int    # сколько дней покрывает с момента выпуска
    n_parallel: int = 1
    max_token_ttl_hours: int = 24
    # На какое смещение от now ставится `valid_from`. Чаще всего 0
    # (started_at = now), но для шаблона «со следующего пн» может быть > 0.
    start_offset_hours: int = 0


# Каталог зашит здесь, потому что эти шаблоны — UX-presets, а не business-data.
# Если заказчик попросит свой «срок 47 дней» — это отдельный admin endpoint
# (создать кастомный permit, не из template).
TEMPLATES: list[PermitTemplate] = [
    PermitTemplate(
        id="today",
        label="Сегодня",
        description="Доступ только на сегодня (до 23:59).",
        duration_days=1,
        max_token_ttl_hours=12,
    ),
    PermitTemplate(
        id="week",
        label="Неделя",
        description="Доступ на 7 дней с момента выпуска. Подходит для краткосрочных задач.",
        duration_days=7,
    ),
    PermitTemplate(
        id="month",
        label="Месяц",
        description="Доступ на 30 дней. Типовой выбор для подрядчиков.",
        duration_days=30,
    ),
    PermitTemplate(
        id="quarter",
        label="Квартал",
        description="90 дней. Для проектных команд и стажёров.",
        duration_days=90,
        max_token_ttl_hours=48,
    ),
    PermitTemplate(
        id="year",
        label="Год",
        description="365 дней. Стандарт для штатных сотрудников.",
        duration_days=365,
        max_token_ttl_hours=72,
    ),
    PermitTemplate(
        id="dual_device",
        label="Два устройства (год)",
        description="Год + n_parallel=2 (телефон + планшет одновременно).",
        duration_days=365,
        n_parallel=2,
        max_token_ttl_hours=72,
    ),
]


def get_template(template_id: str) -> PermitTemplate | None:
    return next((t for t in TEMPLATES if t.id == template_id), None)


def materialize(template: PermitTemplate, now: datetime | None = None) -> dict:
    """Превратить шаблон в конкретные `valid_from`/`valid_until` + параметры.

    Returns dict, готовый для передачи в `create_permit`. Используется как
    в JSON API endpoint'е, так и в admin web-форме (preview перед commit).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    valid_from = now + timedelta(hours=template.start_offset_hours)
    # «Сегодня» — до конца дня, иначе — ровно N дней (с округлением до конца суток).
    if template.id == "today":
        end_of_day = valid_from.replace(hour=23, minute=59, second=59, microsecond=0)
        valid_until = end_of_day
    else:
        valid_until = valid_from + timedelta(days=template.duration_days)
    return {
        "valid_from": valid_from,
        "valid_until": valid_until,
        "n_parallel": template.n_parallel,
        "max_token_ttl_seconds": template.max_token_ttl_hours * 3600,
    }
