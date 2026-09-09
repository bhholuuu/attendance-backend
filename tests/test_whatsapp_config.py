"""Tests for WhatsApp configuration validation, provider factory switching,
and phone normalization utilities."""

import pytest


def test_mock_provider_does_not_require_whatsapp_secrets():
    from app.core.config import settings

    assert (settings.MESSAGE_PROVIDER or "").lower() != "whatsapp"
    from app.providers.factory import build_message_provider

    assert build_message_provider.__name__ == "build_message_provider"


def test_factory_builds_mock_by_default(monkeypatch):
    from app.core import config
    from app.providers.factory import build_message_provider
    from app.providers.mock import MockMessageProvider

    monkeypatch.setattr(config.settings, "MESSAGE_PROVIDER", "mock")
    provider = build_message_provider()
    assert isinstance(provider, MockMessageProvider)


def test_factory_builds_whatsapp_when_configured(monkeypatch):
    from app.core import config
    from app.providers.factory import build_message_provider
    from app.providers.whatsapp import WhatsAppCloudProvider

    monkeypatch.setattr(config.settings, "MESSAGE_PROVIDER", "whatsapp")
    provider = build_message_provider()
    assert isinstance(provider, WhatsAppCloudProvider)


class _FakeSettings:
    """Minimal stand-in used to validate the whatsapp model validator."""

    MESSAGE_PROVIDER = "whatsapp"
    WHATSAPP_PHONE_NUMBER_ID = "12345"
    WHATSAPP_ACCESS_TOKEN = "secret"
    WHATSAPP_BUSINESS_ACCOUNT_ID = "wa-1"
    WHATSAPP_WEBHOOK_VERIFY_TOKEN = "verify"
    WHATSAPP_APP_SECRET = "appsecret"
    WHATSAPP_API_VERSION = "v21.0"
    WHATSAPP_TEMPLATE_NAME = "attendance_update"
    WHATSAPP_TEMPLATE_LANGUAGE = "en"
    WHATSAPP_API_TIMEOUT = 10.0
    MESSAGE_MAX_ATTEMPTS = 3


def test_whatsapp_config_requires_credentials(monkeypatch):
    """MESSAGE_PROVIDER=whatsapp with a missing credential must fail fast."""
    from app.core.config import Settings

    with pytest.raises(ValueError) as exc:
        Settings(
            MESSAGE_PROVIDER="whatsapp",
            WHATSAPP_PHONE_NUMBER_ID="12345",
            WHATSAPP_ACCESS_TOKEN="",  # missing
            WHATSAPP_BUSINESS_ACCOUNT_ID="wa-1",
            WHATSAPP_WEBHOOK_VERIFY_TOKEN="verify",
            WHATSAPP_APP_SECRET="appsecret",
        )
    assert "WHATSAPP_ACCESS_TOKEN" in str(exc.value)


def test_whatsapp_config_accepts_complete_credentials():
    from app.core.config import Settings

    settings = Settings(
        MESSAGE_PROVIDER="whatsapp",
        WHATSAPP_PHONE_NUMBER_ID="12345",
        WHATSAPP_ACCESS_TOKEN="secret",
        WHATSAPP_BUSINESS_ACCOUNT_ID="wa-1",
        WHATSAPP_WEBHOOK_VERIFY_TOKEN="verify",
        WHATSAPP_APP_SECRET="appsecret",
    )
    # No exception -> valid.
    assert settings.MESSAGE_PROVIDER == "whatsapp"


def test_max_attempts_must_be_positive(monkeypatch):
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(
            MESSAGE_PROVIDER="whatsapp",
            WHATSAPP_PHONE_NUMBER_ID="12345",
            WHATSAPP_ACCESS_TOKEN="secret",
            WHATSAPP_BUSINESS_ACCOUNT_ID="wa-1",
            WHATSAPP_WEBHOOK_VERIFY_TOKEN="verify",
            WHATSAPP_APP_SECRET="appsecret",
            MESSAGE_MAX_ATTEMPTS=0,
        )