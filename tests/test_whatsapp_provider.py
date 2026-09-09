import json

import httpx

from app.providers.base import MessagePayload
from app.providers.whatsapp import (
    WhatsAppCloudProvider,
    _mask_number,
    _safe_response_text,
    verify_webhook_signature,
)


def _payload(**overrides):
    defaults = dict(
        to_number="+919876543210",
        content="Dear Parent, Rahul was present on 31 August 2026.",
        parent_name="Parent Name",
        student_name="Rahul Patel",
        attendance_status="present",
        attendance_date="2026-08-31",
    )
    defaults.update(overrides)
    return MessagePayload(**defaults)


def _provider(handler, **provider_kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    defaults = dict(phone_number_id="12345", access_token="secret-token")
    defaults.update(provider_kwargs)
    return WhatsAppCloudProvider(**defaults, client=client), client


def _json_ok(status_code=200, body=None):
    if body is None:
        body = {"messaging_product": "whatsapp", "messages": [{"id": "wamid.ABC123"}]}
    return httpx.Response(status_code, json=body)


# ---------------------------------------------------------------------------
# Configuration / validation
# ---------------------------------------------------------------------------
def test_provider_requires_configuration():
    provider = WhatsAppCloudProvider(phone_number_id="", access_token="")
    result = provider.send(payload=_payload())
    assert result.success is False
    assert "not configured" in result.error_message


def test_provider_rejects_missing_recipient():
    provider = WhatsAppCloudProvider(phone_number_id="12345", access_token="tok")
    result = provider.send(payload=_payload(to_number=""))
    assert result.success is False
    assert "phone number" in result.error_message


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------
def test_provider_sends_single_template_message_and_returns_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return _json_ok()

    provider, _ = _provider(handler)
    result = provider.send(payload=_payload())

    assert result.success is True
    assert result.provider_message_id == "wamid.ABC123"
    assert captured["auth"] == "Bearer secret-token"
    # Single recipient only — one "to" per request, never a group/array.
    assert captured["body"]["to"] == "919876543210"
    assert captured["body"]["type"] == "template"
    assert captured["body"]["template"]["name"] == "attendance_update"
    assert captured["body"]["template"]["language"]["code"] == "en"
    assert captured["body"]["template"]["components"][0]["parameters"][1]["text"] == (
        "Rahul Patel"
    )


def test_provider_sends_plain_text_when_no_template_context():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _json_ok()

    provider, _ = _provider(handler)
    result = provider.send(
        payload=_payload(parent_name="", student_name="", attendance_status="", attendance_date="")
    )
    assert result.success is True
    assert captured["body"]["type"] == "text"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
def test_provider_timeout_returns_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider, client = _provider(handler)
    try:
        result = provider.send(payload=_payload())
    finally:
        client.close()
    assert result.success is False
    assert "timed out" in result.error_message


def test_provider_rate_limited_returns_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    provider, _ = _provider(handler)
    result = provider.send(payload=_payload())
    assert result.success is False
    assert "rate limit" in result.error_message


def test_provider_http_error_returns_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    provider, _ = _provider(handler)
    result = provider.send(payload=_payload())
    assert result.success is False
    assert "rejected" in result.error_message


# ---------------------------------------------------------------------------
# Privacy / safe helpers
# ---------------------------------------------------------------------------
def test_provider_persists_only_safe_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_ok(
            body={
                "messages": [{"id": "wamid.ABC123"}],
                "meta": {"key": "x" * 5000},
            }
        )

    provider, _ = _provider(handler)
    result = provider.send(payload=_payload())
    assert result.success is True
    # Safe response is truncated, not the full (large) payload.
    assert result.provider_response is not None
    assert len(result.provider_response) <= 2100


def test_mask_number_never_exposes_whole_number():
    assert "+91" not in _mask_number("+919876543210")
    assert _mask_number("+919876543210").endswith("3210")
    assert _mask_number("12") == "****"


def test_safe_response_text_truncates_long_bodies():
    long_text = "a" * 5000
    out = _safe_response_text(long_text)
    assert out is not None
    assert len(out) < 2500
    assert "...(truncated)" in out


def test_verify_webhook_signature_accepts_valid_and_rejects_invalid(
    monkeypatch,
):
    from app.core import config

    monkeypatch.setattr(config.settings, "WHATSAPP_APP_SECRET", "app-secret")
    body = b'{"entry":[]}'
    # Compute expected signature using the same algorithm.
    import hashlib
    import hmac

    expected = "sha256=" + hmac.new(
        b"app-secret", body, hashlib.sha256
    ).hexdigest()
    assert verify_webhook_signature(body, expected) is True
    assert verify_webhook_signature(body, "sha256=deadbeef") is False
    assert verify_webhook_signature(body, "") is False