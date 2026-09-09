"""Official WhatsApp Business Cloud API message provider.

Implements :class:`MessageProviderInterface` using the Meta WhatsApp Cloud
API. Only the Graph API (config *server-side* credentials) is used; no
WhatsApp Web/Selenium or unofficial API is ever invoked.

Security and privacy rules enforced here:
  * Credentials (access token/app secret) are read from configuration only
    and are never logged or returned to callers.
  * Phone numbers are masked in logs, never printed in full.
  * Only a small, safe subset of the provider response is persisted.
  * Each call sends exactly one message to one recipient (never a group, never
    multiple students combined).
"""

import hashlib
import hmac
import logging
import re
from typing import Optional
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.providers.base import MessagePayload, MessageProviderInterface, MessageSendResult

logger = logging.getLogger("app.providers.whatsapp")

_GRAPH_HOST = "https://graph.facebook.com"
# Characters commonly appearing in phone formatting that we strip before
# building the numeric ``to`` field expected by the WhatsApp API.
_FORMAT_CHARS = re.compile(r"[\s\-().+]")

# Short-namespace for idempotency so duplicate sends are suppressed upstream.
_IDEMPOTENCY_PREFIX = "attendance"


def _mask_number(number: str) -> str:
    """Return a masked phone number safe for logging (never the full number).

    Shows the last 4 digits (which pair a message to its recipient for
    debugging) while hiding the rest. Empty/invalid values yield a placeholder.
    """
    cleaned = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(cleaned) < 4:
        return "****"
    return "*" * (len(cleaned) - 4) + cleaned[-4:]


def _safe_response_text(text: Optional[str]) -> Optional[str]:
    """Return a safe, truncated copy of a provider response for persistence.

    Message id + numeric status codes are useful for debugging; the body is
    truncated so large or noisy payloads are never stored in full. Credentials
    are never part of received bodies.
    """
    if not text:
        return None
    stripped = " ".join(str(text).split())
    if len(stripped) > 2000:
        stripped = stripped[:2000] + "...(truncated)"
    return stripped


def _normalize_to_field(number: str) -> str:
    """Coerce an E.164 number (e.g. +919876543210) to the digits the API wants."""
    return _FORMAT_CHARS.sub("", number or "").strip()


class WhatsAppCloudProvider(MessageProviderInterface):
    """Send individual messages through the WhatsApp Cloud API."""

    def __init__(
        self,
        *,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: Optional[float] = None,
        template_name: Optional[str] = None,
        template_language: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.api_version = api_version or settings.WHATSAPP_API_VERSION
        self.timeout = timeout if timeout is not None else settings.WHATSAPP_API_TIMEOUT
        self.template_name = template_name or settings.WHATSAPP_TEMPLATE_NAME
        self.template_language = (
            template_language or settings.WHATSAPP_TEMPLATE_LANGUAGE
        )
        self._client = client

    def _messages_url(self) -> str:
        return (
            f"{_GRAPH_HOST}/{quote(self.api_version)}/"
            f"{quote(self.phone_number_id)}/messages"
        )

    def _build_template_payload(self, payload: MessagePayload) -> dict:
        """Build a template-message request body with the required variables.

        Template variables follow the configurable template contract:
          {{1}} Parent Name, {{2}} Student Name, {{3}} Attendance Status,
          {{4}} Attendance Date.
        """
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": payload.parent_name or "Parent"},
                    {"type": "text", "text": payload.student_name or "Student"},
                    {
                        "type": "text",
                        "text": payload.attendance_status or "absent",
                    },
                    {
                        "type": "text",
                        "text": payload.attendance_date or "today",
                    },
                ],
            }
        ]
        return {
            "messaging_product": "whatsapp",
            "to": _normalize_to_field(payload.to_number),
            "type": "template",
            "template": {
                "name": self.template_name,
                "language": {"code": self.template_language},
                "components": components,
            },
        }

    def _build_text_payload(self, payload: MessagePayload) -> dict:
        """Build a plain-text (free-form) message request body."""
        return {
            "messaging_product": "whatsapp",
            "to": _normalize_to_field(payload.to_number),
            "type": "text",
            "text": {"preview_url": False, "body": payload.content or ""},
        }

    def _post(self, url: str, headers: dict, body: dict) -> httpx.Response:
        if self._client is not None:
            return self._client.post(url, headers=headers, json=body)
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(url, headers=headers, json=body)

    def send(self, *, payload: MessagePayload) -> MessageSendResult:
        """Send a single message and return a safe result (does not raise).

        Uses the configured message template when structured context is
        provided; otherwise falls back to a plain-text message.
        """
        if not (self.phone_number_id or "").strip() or not (
            self.access_token or ""
        ).strip():
            return MessageSendResult(
                success=False,
                error_message="WhatsApp provider is not configured.",
            )

        to = _normalize_to_field(payload.to_number)
        if not to:
            return MessageSendResult(
                success=False,
                error_message="Recipient phone number is missing or invalid.",
            )

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        try:
            idempotency_key = self._idempotency_header(payload.idempotency_key)
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
        except Exception:  # pragma: no cover - defensive
            pass

        use_template = bool(
            payload.parent_name
            or payload.student_name
            or payload.attendance_status
            or payload.attendance_date
        )
        if use_template:
            body = self._build_template_payload(payload)
        else:
            body = self._build_text_payload(payload)

        masked = _mask_number(to)
        logger.info(
            "whatsapp_send start to=%s template=%s",
            masked,
            use_template,
        )
        try:
            response = self._post(self._messages_url(), headers, body)
        except httpx.TimeoutException:
            logger.warning("whatsapp_send timeout to=%s", masked)
            return MessageSendResult(
                success=False,
                error_message="WhatsApp API request timed out.",
            )
        except httpx.HTTPError as exc:
            logger.warning("whatsapp_send http_error to=%s exc=%s", masked, exc)
            return MessageSendResult(
                success=False,
                error_message="WhatsApp API request failed.",
            )

        if response.status_code == 429:
            logger.warning("whatsapp_send rate_limited to=%s", masked)
            return MessageSendResult(
                success=False,
                error_message="WhatsApp API rate limit reached. Try again later.",
            )
        if response.status_code >= 400:
            logger.warning(
                "whatsapp_send rejected to=%s status=%s",
                masked,
                response.status_code,
            )
            return MessageSendResult(
                success=False,
                error_message=(
                    f"WhatsApp API rejected the request (HTTP {response.status_code})."
                ),
            )

        try:
            data = response.json()
        except ValueError:  # non-JSON success body
            data = {}

        message_id = None
        if isinstance(data, dict):
            msg = data.get("messages")
            if isinstance(msg, list) and msg and isinstance(msg[0], dict):
                message_id = msg[0].get("id")

        logger.info(
            "whatsapp_send ok to=%s message_id=%s",
            masked,
            (message_id or "none")[:16],
        )
        return MessageSendResult(
            success=True,
            provider_message_id=message_id,
            provider_response=_safe_response_text(
                response.text if response.text else None
            ),
        )

    @staticmethod
    def _idempotency_header(raw_key: Optional[str]) -> str:
        """Build a stable idempotency key header from a supplied key.

        The key is namespaced and hashed so it never leaks raw message
        content and stays a bounded width.
        """
        if not raw_key:
            return ""
        digest = hashlib.sha256(str(raw_key).encode("utf-8")).hexdigest()
        return f"{_IDEMPOTENCY_PREFIX}-{digest[:40]}"


def verify_webhook_signature(payload: bytes, signature_header: str) -> bool:
    """Verify the Meta ``X-Hub-Signature-256`` header for a webhook payload.

    Computes HMAC-SHA256 of the raw request body using the app secret and
    compares it (constant-time) against the provided ``sha256=...`` signature.
    """
    app_secret = settings.WHATSAPP_APP_SECRET or ""
    if not app_secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
