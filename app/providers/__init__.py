"""Message provider abstractions and implementations.

API routes must call the messaging service, which in turn calls a provider
through the MessageProviderInterface. This keeps provider details out of the
API layer and makes it easy to integrate a real WhatsApp provider later.
"""

from app.providers.base import (
    MessagePayload,
    MessageProviderInterface,
    MessageSendResult,
)

__all__ = [
    "MessagePayload",
    "MessageProviderInterface",
    "MessageSendResult",
]
