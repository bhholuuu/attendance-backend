from app.core.config import settings
from app.providers.base import MessageProviderInterface
from app.providers.mock import MockMessageProvider


def build_message_provider() -> MessageProviderInterface:
    """Return the configured message provider implementation.

    Supports:
      - "mock"     : simulated successful send (development, no credentials).
      - "whatsapp" : official WhatsApp Business Cloud API (server-side
                     credentials from settings).
    """
    provider = (settings.MESSAGE_PROVIDER or "mock").strip().lower()
    if provider == "whatsapp":
        from app.providers.whatsapp import WhatsAppCloudProvider

        return WhatsAppCloudProvider()
    return MockMessageProvider()
