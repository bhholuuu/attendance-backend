from app.providers.base import MessagePayload, MessageProviderInterface, MessageSendResult


class MockMessageProvider(MessageProviderInterface):
    """Development-only provider that simulates a successful send.

    It does NOT send any real message. Used so the full send/status/retry flow
    can be exercised without a WhatsApp account. In production the same
    interface is backed by a real WhatsApp Cloud API client.
    """

    def send(self, *, payload: MessagePayload) -> MessageSendResult:
        # Simulated provider-assigned message id (stable per simulated send).
        import hashlib

        digest = hashlib.sha1(
            f"{payload.to_number}|{payload.content}".encode("utf-8")
        ).hexdigest()
        return MessageSendResult(
            success=True,
            provider_message_id=f"mock-{digest[:24]}",
            provider_response='{"status":"delivered","wa_id":null}',
        )
