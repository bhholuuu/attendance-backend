from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.models.attendance import AttendanceStatus
from app.models.message import MessageBatchStatus, MessageDeliveryStatus, MessageType


class MessageBatchCreateRequest(BaseModel):
    attendance_session_id: int


class ParentMessageDetail(BaseModel):
    """An individual parent message as exposed by the API.

    Deliberately excludes raw provider response internals; only the safe
    provider message id and any safe error message are surfaced.

    V2.5 additions: message_type, attendance_record_id, cancellation info.
    """

    id: int
    student_id: int
    student_name: str
    roll_number: str
    parent_name: str
    parent_whatsapp_number: str
    attendance_status: AttendanceStatus
    delivery_status: MessageDeliveryStatus
    message_content: str
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    attempt_count: int = 0
    sent_at: Optional[datetime] = None
    # V2.5 fields
    message_type: str = MessageType.ABSENCE_NOTIFICATION.value
    attendance_record_id: Optional[int] = None
    cancelled_at: Optional[datetime] = None
    cancelled_reason: Optional[str] = None


class MessageBatchSummary(BaseModel):
    """Summary row used by message batch list/pending/sent endpoints."""

    id: int
    attendance_session_id: int
    class_name: str
    division_name: str
    attendance_date: date
    status: MessageBatchStatus
    total_messages: int
    sent_count: int
    pending_count: int
    failed_count: int
    skipped_count: int = 0
    created_at: datetime


class MessageBatchDetail(BaseModel):
    """Full detail of a single message batch, including its messages."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    attendance_session_id: int
    class_name: str
    division_name: str
    attendance_date: date
    status: MessageBatchStatus
    total_messages: int
    sent_count: int
    pending_count: int
    failed_count: int
    skipped_count: int = 0
    created_at: datetime
    updated_at: datetime
    messages: List[ParentMessageDetail] = []


class MessageBatchListResponse(BaseModel):
    items: List[MessageBatchSummary]
