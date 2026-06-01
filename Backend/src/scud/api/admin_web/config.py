"""Templating + cookie session config для admin web-panel."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer

_THIS_DIR = Path(__file__).parent
_logger = logging.getLogger(__name__)

# Шаблоны лежат рядом с модулем — это даёт точный pathlib.Path без guessing.
TEMPLATES = Jinja2Templates(directory=str(_THIS_DIR / "templates"))

# Static — пока пустой, но Mount готов (favicon, иконки).
STATIC_DIR = _THIS_DIR / "static"

# Cookie session: itsdangerous подписывает (HMAC-SHA256) и проверяет TTL.
# Секрет — из env. Дефолт безопасный только для dev; в prod docker-compose.prod.yml
# обязан проставить SCUD_WEB_SECRET через .env.prod.
_DEV_DEFAULT_SECRET = "dev-only-secret-CHANGE-IN-PROD"
SECRET = os.getenv("SCUD_WEB_SECRET", _DEV_DEFAULT_SECRET)

_SERIALIZER = URLSafeTimedSerializer(SECRET, salt="scud-admin-web")

COOKIE_NAME = "scud_admin_session"
# 8 часов — окно рабочего дня; дольше нет смысла, refresh идёт повторным
# логином с API key.
COOKIE_MAX_AGE_SECONDS = 8 * 60 * 60


def check_web_secret(is_production: bool) -> None:
    """BE-SEC-04: enforce that the default dev secret is never used in production.

    Call this once at application startup (main.py lifespan).
    - In production (is_production=True): raises RuntimeError, refusing to start.
    - In dev: logs a warning so developers notice the insecure default.
    """
    if SECRET == _DEV_DEFAULT_SECRET:
        msg = (
            "SCUD_WEB_SECRET is the dev default value. "
            "Set a strong random secret via the SCUD_WEB_SECRET environment variable."
        )
        if is_production:
            raise RuntimeError(
                f"[BE-SEC-04] Refusing to start in production with insecure default: {msg}"
            )
        _logger.warning("[BE-SEC-04] INSECURE CONFIGURATION — %s", msg)


def encode_session(api_key_id: str) -> str:
    return _SERIALIZER.dumps({"akid": api_key_id})


def decode_session(token: str) -> dict | None:
    from itsdangerous import BadSignature, SignatureExpired
    try:
        return _SERIALIZER.loads(token, max_age=COOKIE_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
