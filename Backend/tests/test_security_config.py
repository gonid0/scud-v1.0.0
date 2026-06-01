"""Tests for security config hardening.

BE-SEC-04: SCUD_WEB_SECRET default value is rejected in production.
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# BE-SEC-04: web secret fail-fast
# ---------------------------------------------------------------------------


def test_check_web_secret_raises_in_production_with_default():
    """check_web_secret must raise RuntimeError when environment is prod and
    the secret equals the dev default."""
    # Temporarily patch SECRET to the dev default to isolate from real env
    import scud.api.admin_web.config as web_cfg

    original = web_cfg.SECRET
    try:
        web_cfg.SECRET = web_cfg._DEV_DEFAULT_SECRET
        web_cfg._SERIALIZER  # ensure module loaded

        with pytest.raises(RuntimeError, match="BE-SEC-04"):
            web_cfg.check_web_secret(is_production=True)
    finally:
        web_cfg.SECRET = original


def test_check_web_secret_only_warns_in_dev(caplog):
    """check_web_secret must only log a warning (not raise) in dev mode."""
    import logging

    import scud.api.admin_web.config as web_cfg

    original = web_cfg.SECRET
    try:
        web_cfg.SECRET = web_cfg._DEV_DEFAULT_SECRET
        with caplog.at_level(logging.WARNING, logger="scud.api.admin_web.config"):
            # Must not raise
            web_cfg.check_web_secret(is_production=False)
        assert any("BE-SEC-04" in r.message or "INSECURE" in r.message for r in caplog.records)
    finally:
        web_cfg.SECRET = original


def test_check_web_secret_passes_with_custom_secret():
    """check_web_secret must not raise or warn when a non-default secret is set."""
    import scud.api.admin_web.config as web_cfg

    original = web_cfg.SECRET
    try:
        web_cfg.SECRET = "a-very-strong-random-production-secret-xyz"
        # Should pass silently in both modes
        web_cfg.check_web_secret(is_production=True)
        web_cfg.check_web_secret(is_production=False)
    finally:
        web_cfg.SECRET = original


# ---------------------------------------------------------------------------
# BE-DAT-01: seed migration guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Settings: is_production property
# ---------------------------------------------------------------------------


def test_settings_is_production_true():
    from scud.config import Settings
    s = Settings(environment="production")
    assert s.is_production is True


def test_settings_is_production_prod_alias():
    from scud.config import Settings
    s = Settings(environment="prod")
    assert s.is_production is True


def test_settings_is_production_false_for_dev():
    from scud.config import Settings
    s = Settings(environment="dev")
    assert s.is_production is False


def test_settings_is_production_false_for_staging():
    from scud.config import Settings
    s = Settings(environment="staging")
    assert s.is_production is False
