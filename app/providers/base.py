from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class MessageSendResult:
    """Safe outcome of a single message send attempt.

    Only limited, safe metadata is returned so routers/consumers never expose
    provider internals or secrets.
    """

    success: bool
    provider_message_id: Optional[str] = None
    provider_response: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class MessagePayload:
    """Input for a single message send.

    Carries only the *one* parent's data needed to build the request, so no
    other student's information is ever combined into a single send.

    ``content`` is the pre-rendered fallback text used when the provider sends
    a plain-text (non-template) message. The structured fields (parent_name,
    student_name, attendance_status, attendance_date) let a provider that
    supports message templates (e.g. the WhatsApp Cloud API) supply variables
    without leaking extra data.
    """

    to_number: str
    content: str = ""
    parent_name: str = ""
    student_name: str = ""
    attendance_status: str = ""
    attendance_date: str = ""
    idempotency_key: Optional[str] = None


class MessageProviderInterface(ABC):
    """Abstraction over a message delivery provider.

    Implementations deliver a single parent message. The caller is responsible
    for passing only that message's own recipient number and content, so no
    other student's data is ever exposed.
    """

    @abstractmethod
    def send(self, *, payload: MessagePayload) -> MessageSendResult:
        """Send a single message to one parent and return the safe result."""
        raise NotImplementedError
