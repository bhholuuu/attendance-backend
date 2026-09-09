import logging
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.attendance import AttendanceSession, AttendanceStatus, SessionStatus, StudentAttendance
from app.models.message import (
    MessageBatch,
    MessageBatchStatus,
    MessageDeliveryStatus,
    MessageType,
    PARENT_WHATSAPP_UNAVAILABLE,
    ParentMessage,
    generate_idempotency_key,
)
from app.models.student import Student
from app.models.user import User
from app.providers.base import MessagePayload, MessageProviderInterface
from app.providers.factory import build_message_provider
from app.services.message_template import MessageTemplateService
from app.core.config import settings

logger = logging.getLogger("app.services.message")


def _http(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def _batch_scope(division_map: Optional[dict[int, set[int]]]):
    """SQLAlchemy condition restricting batches to allowed (class, division) pairs.

    Returns None when ``division_map`` is None (admin / unscoped). Returns a
    false() condition when the map is empty (a teacher with no access to a
    class would otherwise match nothing requested).
    """
    from sqlalchemy import false

    if division_map is None:
        return None
    if not division_map:
        return false()
    return or_(
        *[
            (MessageBatch.class_id == cid) & (MessageBatch.division_id.in_(divs))
            for cid, divs in division_map.items()
        ]
    )


def _records_for_session(db: Session, session_id: int) -> List[StudentAttendance]:
    return (
        db.query(StudentAttendance)
        .filter(StudentAttendance.attendance_session_id == session_id)
        .all()
    )


def _messages_for_batch(db: Session, batch_id: int) -> List[ParentMessage]:
    return (
        db.query(ParentMessage)
        .filter(ParentMessage.message_batch_id == batch_id)
        .all()
    )


def get_batch(db: Session, batch_id: int) -> MessageBatch:
    batch = db.query(MessageBatch).filter(MessageBatch.id == batch_id).first()
    if batch is None:
        raise _http(404, "Message batch not found")
    return batch


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------
def _load_student_map(db: Session, student_ids: set[int]) -> dict[int, Student]:
    if not student_ids:
        return {}
    students = db.query(Student).filter(Student.id.in_(student_ids)).all()
    return {s.id: s for s in students}


def _recompute_batch(db: Session, batch: MessageBatch) -> MessageBatch:
    """Recalculate a batch's summary counts and status from its messages.

    Status rules:
      COMPLETED        - every non-cancelled message is SENT.
      PARTIAL_FAILED   - some sent and some failed, with none pending.
      PENDING          - messages still pending/processing, or failed messages
                         that may be retried (including batches whose messages
                         all failed at creation and were never sent).
    """
    messages = _messages_for_batch(db, batch.id)

    def _is_sent(m) -> bool:
        return m.delivery_status in (
            MessageDeliveryStatus.SENT,
            MessageDeliveryStatus.DELIVERED,
            MessageDeliveryStatus.READ,
        )

    sent = sum(1 for m in messages if _is_sent(m))
    failed = sum(
        1 for m in messages if m.delivery_status == MessageDeliveryStatus.FAILED
    )
    cancelled = sum(
        1 for m in messages if m.delivery_status == MessageDeliveryStatus.CANCELLED
    )
    pending = sum(
        1
        for m in messages
        if m.delivery_status
        in (MessageDeliveryStatus.PENDING, MessageDeliveryStatus.PROCESSING)
    )

    batch.sent_count = sent
    batch.failed_count = failed
    batch.pending_count = pending
    batch.skipped_count = cancelled
    batch.total_messages = len(messages)

    # Active messages are all except cancelled.
    active = len(messages) - cancelled

    if active > 0 and sent == active:
        batch.status = MessageBatchStatus.COMPLETED
    elif sent > 0 and failed > 0 and pending == 0:
        batch.status = MessageBatchStatus.PARTIAL_FAILED
    else:
        batch.status = MessageBatchStatus.PENDING

    batch.updated_at = datetime.utcnow()
    return batch


def _validate_whatsapp(number: str) -> bool:
    """Return whether a phone number is a plausible usable WhatsApp number."""
    if not number or not number.strip():
        return False
    try:
        from app.core.phone import normalize_phone_number

        normalize_phone_number(number)
        return True
    except ValueError:
        return False


def batch_summary(batch: MessageBatch) -> dict:
    return {
        "id": batch.id,
        "attendance_session_id": batch.attendance_session_id,
        "class_name": batch.school_class.name,
        "division_name": batch.division.name,
        "attendance_date": batch.attendance_date,
        "status": batch.status,
        "total_messages": batch.total_messages,
        "sent_count": batch.sent_count,
        "pending_count": batch.pending_count,
        "failed_count": batch.failed_count,
        "skipped_count": getattr(batch, "skipped_count", 0),
        "created_at": batch.created_at,
    }


def batch_detail(db: Session, batch: MessageBatch) -> dict:
    """Full batch detail including individual parent messages.

    Student name and roll number are read from the current student record for
    display; the message record itself keeps the historical snapshot of the
    parent and attendance status.

    V2.5: includes message_type, attendance_record_id, cancellation info.
    """
    messages = _messages_for_batch(db, batch.id)
    student_map = _load_student_map(db, {m.student_id for m in messages})

    message_items = []
    for m in messages:
        student = student_map.get(m.student_id)
        message_items.append(
            {
                "id": m.id,
                "student_id": m.student_id,
                "student_name": student.name if student else "",
                "roll_number": student.roll_number if student else "",
                "parent_name": m.parent_name,
                "parent_whatsapp_number": m.parent_whatsapp_number,
                "attendance_status": m.attendance_status,
                "delivery_status": m.delivery_status,
                "message_content": m.message_content,
                "provider_message_id": m.provider_message_id,
                "error_message": m.error_message,
                "attempt_count": m.attempt_count,
                "sent_at": m.sent_at,
                # V2.5 fields
                "message_type": getattr(m, "message_type", MessageType.ABSENCE_NOTIFICATION.value),
                "attendance_record_id": getattr(m, "attendance_record_id", None),
                "cancelled_at": getattr(m, "cancelled_at", None),
                "cancelled_reason": getattr(m, "cancelled_reason", None),
            }
        )

    return {
        "id": batch.id,
        "attendance_session_id": batch.attendance_session_id,
        "class_name": batch.school_class.name,
        "division_name": batch.division.name,
        "attendance_date": batch.attendance_date,
        "status": batch.status,
        "total_messages": batch.total_messages,
        "sent_count": batch.sent_count,
        "pending_count": batch.pending_count,
        "failed_count": batch.failed_count,
        "skipped_count": getattr(batch, "skipped_count", 0),
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "messages": message_items,
    }


# ---------------------------------------------------------------------------
# Create batch (V2.5: only ABSENCE_NOTIFICATION, idempotent)
# ---------------------------------------------------------------------------
def create_batch(
    db: Session,
    *,
    attendance_session_id: int,
    created_by: User,
    template_service: Optional[MessageTemplateService] = None,
) -> MessageBatch:
    """Create a message batch for a completed attendance session.

    V2.5 rules:
    - Only creates ABSENCE_NOTIFICATION messages for ABSENT students.
    - Students on APPROVED leave are excluded (parents are not notified).
    - Students with PRESENT status are skipped (no notification needed).
    - Each message links to its source attendance_record_id.
    - Idempotency key prevents duplicate logical notifications across batches.
    - Messages without a usable parent WhatsApp number are FAILED (not dropped).
    - skipped_count tracks how many students were excluded.
    """
    session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.id == attendance_session_id)
        .first()
    )
    if session is None:
        raise _http(404, "Attendance session not found")
    if session.status != SessionStatus.COMPLETED:
        raise _http(
            400, "Message batch can only be created for a completed attendance session"
        )

    existing = (
        db.query(MessageBatch)
        .filter(MessageBatch.attendance_session_id == attendance_session_id)
        .first()
    )
    if existing is not None:
        raise _http(409, "A message batch already exists for this attendance session")

    from app.services.effective_attendance_service import (
        is_holiday,
        on_leave_student_ids,
    )

    if is_holiday(db, session.attendance_date):
        raise _http(
            400,
            "Message batch cannot be created for a session recorded on a school holiday",
        )

    records = _records_for_session(db, session.id)
    if not records:
        raise _http(400, "Attendance session has no student records")

    # Filter out students with approved leave.
    on_leave = on_leave_student_ids(
        db, session.class_id, session.division_id, session.attendance_date
    )

    student_map = _load_student_map(db, {r.student_id for r in records})
    template = template_service or MessageTemplateService()

    batch = MessageBatch(
        attendance_session_id=session.id,
        class_id=session.class_id,
        division_id=session.division_id,
        attendance_date=session.attendance_date,
        status=MessageBatchStatus.PENDING,
        created_by=created_by.id,
    )
    db.add(batch)
    db.flush()  # assign batch.id

    attendance_date_str = session.attendance_date.isoformat()
    skipped_count = 0
    message_count = 0

    for record in records:
        student = student_map.get(record.student_id)

        # V2.5: Skip students on approved leave.
        if record.student_id in on_leave:
            skipped_count += 1
            continue

        # V2.5: Only create ABSENCE_NOTIFICATION for ABSENT students.
        if record.attendance_status != AttendanceStatus.ABSENT:
            skipped_count += 1
            continue

        if student is None:
            parent_name = ""
            whatsapp = ""
        else:
            parent_name = student.parent_name or ""
            whatsapp = student.parent_whatsapp_number or ""

        student_name = student.name if student else ""
        content = template.render(
            parent_name=parent_name or "Parent",
            student_name=student_name or "Student",
            attendance_status=record.attendance_status,
            attendance_date=attendance_date_str,
        )

        if _validate_whatsapp(whatsapp):
            delivery_status = MessageDeliveryStatus.PENDING
            error_message = None
        else:
            delivery_status = MessageDeliveryStatus.FAILED
            error_message = PARENT_WHATSAPP_UNAVAILABLE

        # V2.5: Generate idempotency key from attendance record + message type.
        idempotency_key = generate_idempotency_key(
            record.id, MessageType.ABSENCE_NOTIFICATION.value
        )

        db.add(
            ParentMessage(
                message_batch_id=batch.id,
                student_id=record.student_id,
                attendance_record_id=record.id,
                message_type=MessageType.ABSENCE_NOTIFICATION.value,
                idempotency_key=idempotency_key,
                parent_name=parent_name,
                parent_whatsapp_number=whatsapp,
                attendance_status=record.attendance_status,
                message_content=content,
                delivery_status=delivery_status,
                error_message=error_message,
            )
        )
        message_count += 1

    if message_count == 0 and skipped_count > 0:
        raise _http(
            400,
            "All students are either present or on approved leave; "
            "no absence notifications to create",
        )

    # Flush the message rows so the recompute below can count them.
    db.flush()

    # Recompute from the just-created messages. Initial status stays PENDING
    # per spec; counts reflect any FAILED (unavailable-number) messages so the
    # batch presents an accurate summary before any send is triggered.
    _recompute_batch(db, batch)
    batch.status = MessageBatchStatus.PENDING
    batch.skipped_count = skipped_count
    batch.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(batch)
    logger.info(
        "message_batch_created batch_id=%d session_id=%d messages=%d skipped=%d",
        batch.id,
        session.id,
        message_count,
        skipped_count,
    )
    return batch


# ---------------------------------------------------------------------------
# Auto-create batch after server-confirmed attendance (V2.5)
# ---------------------------------------------------------------------------
def auto_create_batch_if_absences(
    db: Session,
    *,
    session: AttendanceSession,
    taker: User,
) -> Optional[MessageBatch]:
    """Automatically create a message batch after attendance is confirmed.

    Called after save_attendance() or sync_attendance() completes successfully.
    If no students are absent (or all absences are on leave/holiday), no batch
    is created. Errors are logged but never propagated so attendance saving
    is never blocked by messaging.

    Returns the created batch, or None if no batch was needed.
    """
    try:
        # Check if a batch already exists for this session.
        existing = (
            db.query(MessageBatch)
            .filter(MessageBatch.attendance_session_id == session.id)
            .first()
        )
        if existing is not None:
            return None

        return create_batch(
            db,
            attendance_session_id=session.id,
            created_by=taker,
        )
    except Exception:
        # Attendance saving must never fail due to messaging errors.
        logger.exception(
            "auto_create_batch_failed session_id=%d", session.id
        )
        db.rollback()
        return None


# ---------------------------------------------------------------------------
# Cancel pending messages (V2.5: attendance correction)
# ---------------------------------------------------------------------------
def cancel_pending_messages_for_student(
    db: Session,
    *,
    attendance_record_id: int,
    student_id: int,
    batch_id: int,
    reason: str = "Attendance corrected by teacher",
) -> int:
    """Cancel PENDING messages for a student when their attendance is corrected.

    If the message is still PENDING (not yet sent), it is cancelled and will
    not be sent. If the message is already SENT/DELIVERED/READ, it is NOT
    cancelled (the notification was already delivered).

    Returns the number of messages cancelled.
    """
    messages = (
        db.query(ParentMessage)
        .filter(
            ParentMessage.message_batch_id == batch_id,
            ParentMessage.student_id == student_id,
            ParentMessage.attendance_record_id == attendance_record_id,
        )
        .all()
    )

    cancelled_count = 0
    for msg in messages:
        if msg.delivery_status in (
            MessageDeliveryStatus.PENDING,
            MessageDeliveryStatus.PROCESSING,
        ):
            msg.delivery_status = MessageDeliveryStatus.CANCELLED
            msg.cancelled_at = datetime.utcnow()
            msg.cancelled_reason = reason
            msg.updated_at = datetime.utcnow()
            cancelled_count += 1

    if cancelled_count > 0:
        db.commit()
        logger.info(
            "messages_cancelled record_id=%d student_id=%d batch_id=%d count=%d",
            attendance_record_id,
            student_id,
            batch_id,
            cancelled_count,
        )

    return cancelled_count


def cancel_messages_on_attendance_correction(
    db: Session,
    *,
    session_id: int,
    updates: List[dict],
) -> int:
    """Cancel pending messages when attendance records are corrected.

    Called from update_attendance() when a teacher corrects attendance.
    For each corrected record, checks if a message batch exists and cancels
    any PENDING message for that student.

    Returns total number of messages cancelled.
    """
    # Find if a message batch exists for this session.
    batch = (
        db.query(MessageBatch)
        .filter(MessageBatch.attendance_session_id == session_id)
        .first()
    )
    if batch is None:
        return 0

    total_cancelled = 0
    for upd in updates:
        record_id = upd.get("record_id")
        student_id = upd["student_id"]
        new_status = upd["status"]

        if record_id is None:
            continue

        # If the student was changed to PRESENT, cancel any pending absence
        # notification for this record.
        if new_status == AttendanceStatus.PRESENT:
            cancelled = cancel_pending_messages_for_student(
                db,
                attendance_record_id=record_id,
                student_id=student_id,
                batch_id=batch.id,
                reason="Attendance corrected: ABSENT -> PRESENT",
            )
            total_cancelled += cancelled

    if total_cancelled > 0:
        # Recompute batch counts after cancellations.
        _recompute_batch(db, batch)
        db.commit()

    return total_cancelled


# ---------------------------------------------------------------------------
# List / pending / sent
# ---------------------------------------------------------------------------
def list_batches(
    db: Session,
    *,
    status: Optional[MessageBatchStatus] = None,
    class_id: Optional[int] = None,
    division_id: Optional[int] = None,
    attendance_date: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    division_map: Optional[dict[int, set[int]]] = None,
) -> List[dict]:
    query = db.query(MessageBatch)

    if status is not None:
        query = query.filter(MessageBatch.status == status)
    if class_id is not None:
        query = query.filter(MessageBatch.class_id == class_id)
    if division_id is not None:
        query = query.filter(MessageBatch.division_id == division_id)
    if attendance_date is not None:
        query = query.filter(MessageBatch.attendance_date == attendance_date)
    if start_date is not None:
        query = query.filter(MessageBatch.attendance_date >= start_date)
    if end_date is not None:
        query = query.filter(MessageBatch.attendance_date <= end_date)
    scope = _batch_scope(division_map)
    if scope is not None:
        query = query.filter(scope)

    batches = query.order_by(
        MessageBatch.attendance_date.desc(),
        MessageBatch.id.desc(),
    ).all()

    return [batch_summary(b) for b in batches]


def list_pending_batches(
    db: Session, division_map: Optional[dict[int, set[int]]] = None
) -> List[dict]:
    """Batches that are not fully completed (PENDING/PROCESSING/PARTIAL_FAILED)."""
    query = db.query(MessageBatch).filter(
        MessageBatch.status.in_(
            [
                MessageBatchStatus.PENDING,
                MessageBatchStatus.PROCESSING,
                MessageBatchStatus.PARTIAL_FAILED,
            ]
        )
    )
    scope = _batch_scope(division_map)
    if scope is not None:
        query = query.filter(scope)
    batches = query.order_by(
        MessageBatch.attendance_date.desc(),
        MessageBatch.id.desc(),
    ).all()
    return [batch_summary(b) for b in batches]


def list_sent_batches(
    db: Session, division_map: Optional[dict[int, set[int]]] = None
) -> List[dict]:
    query = db.query(MessageBatch).filter(
        MessageBatch.status == MessageBatchStatus.COMPLETED
    )
    scope = _batch_scope(division_map)
    if scope is not None:
        query = query.filter(scope)
    batches = query.order_by(
        MessageBatch.attendance_date.desc(),
        MessageBatch.id.desc(),
    ).all()
    return [batch_summary(b) for b in batches]


# ---------------------------------------------------------------------------
# Sending / retry
# ---------------------------------------------------------------------------
def _status_word(value) -> str:
    """Map an AttendanceStatus enum value to the natural-language word used in
    WhatsApp template variables."""
    try:
        return "present" if value and value.name == "PRESENT" else "absent"
    except AttributeError:
        return "absent"

def _build_payload(
    batch: MessageBatch,
    message: ParentMessage,
    student_name: str = "",
) -> MessagePayload:
    """Build the single-recipient payload for one parent message.

    Only this message's own data is included (one student per request). The
    idempotency key is stable per parent message so duplicate sends are
    suppressed upstream.
    """
    return MessagePayload(
        to_number=message.parent_whatsapp_number,
        content=message.message_content,
        parent_name=message.parent_name,
        student_name=student_name,
        attendance_status=_status_word(message.attendance_status),
        attendance_date=batch.attendance_date.isoformat() if batch.attendance_date else "",
        idempotency_key=f"msg-{message.id}",
    )


def _max_attempts() -> int:
    try:
        return int(settings.MESSAGE_MAX_ATTEMPTS)
    except (TypeError, ValueError):
        return 3


def _compute_next_retry_at(attempt_count: int) -> Optional[datetime]:
    """Compute the next retry time using exponential backoff.

    Attempt 1 -> immediate (no delay)
    Attempt 2 -> 1 minute
    Attempt 3 -> 5 minutes
    Attempt 4+ -> 15 minutes
    """
    if attempt_count <= 1:
        return None  # immediate
    elif attempt_count == 2:
        return datetime.utcnow() + timedelta(minutes=1)
    elif attempt_count == 3:
        return datetime.utcnow() + timedelta(minutes=5)
    else:
        return datetime.utcnow() + timedelta(minutes=15)


def _process_messages(
    db: Session,
    batch: MessageBatch,
    *,
    provider: MessageProviderInterface,
    should_process: Callable[[ParentMessage], bool],
) -> MessageBatch:
    """Drive the individual delivery flow for a set of messages.

    V2.5 changes:
    - CANCELLED messages are never processed.
    - next_retry_at is set on temporary failures.
    - Each message update is committed individually for progress persistence.
    """
    messages = _messages_for_batch(db, batch.id)
    student_map = _load_student_map(db, {m.student_id for m in messages})
    for message in messages:
        # V2.5: Never process cancelled messages.
        if message.delivery_status == MessageDeliveryStatus.CANCELLED:
            continue

        if not should_process(message):
            continue

        # Guard: transition to PROCESSING and increment attempts before
        # contacting the provider.
        message.delivery_status = MessageDeliveryStatus.PROCESSING
        message.attempt_count = (message.attempt_count or 0) + 1
        message.updated_at = datetime.utcnow()
        db.commit()

        student = student_map.get(message.student_id)
        student_name = student.name if student else ""
        payload = _build_payload(batch, message, student_name=student_name)

        try:
            result = provider.send(payload=payload)
        except Exception as exc:
            logger.exception(
                "message_send_exception message_id=%d", message.id
            )
            result = type("Result", (), {
                "success": False,
                "error_message": f"Unexpected error: {exc}",
                "provider_message_id": None,
                "provider_response": None,
            })()

        if result.success:
            message.delivery_status = MessageDeliveryStatus.SENT
            message.sent_at = datetime.utcnow()
            message.provider_message_id = result.provider_message_id
            message.provider_response = result.provider_response
            message.error_message = None
            message.next_retry_at = None
        else:
            message.delivery_status = MessageDeliveryStatus.FAILED
            message.error_message = (
                result.error_message or "Message delivery failed."
            )
            message.sent_at = None
            # V2.5: Schedule retry with exponential backoff.
            if (message.attempt_count or 0) < _max_attempts():
                message.next_retry_at = _compute_next_retry_at(message.attempt_count)
            else:
                message.next_retry_at = None
        message.updated_at = datetime.utcnow()
        db.commit()

    _recompute_batch(db, batch)
    db.commit()
    db.refresh(batch)
    return batch


def _acquire_for_operation(db: Session, batch: MessageBatch) -> None:
    """Guard against concurrent send/retry operations on the same batch."""
    if batch.status == MessageBatchStatus.PROCESSING:
        raise _http(409, "A send or retry operation is already in progress")
    batch.status = MessageBatchStatus.PROCESSING
    batch.updated_at = datetime.utcnow()
    db.commit()


def send_batch(
    db: Session,
    *,
    batch_id: int,
    provider: Optional[MessageProviderInterface] = None,
) -> MessageBatch:
    """Send all PENDING messages in a batch, each handled individually."""
    batch = get_batch(db, batch_id)

    if batch.total_messages == 0:
        raise _http(400, "Message batch is empty")

    _acquire_for_operation(db, batch)
    _process_messages(
        db,
        batch,
        provider=provider or build_message_provider(),
        should_process=lambda m: m.delivery_status == MessageDeliveryStatus.PENDING,
    )
    return batch


def retry_failed_batch(
    db: Session,
    *,
    batch_id: int,
    provider: Optional[MessageProviderInterface] = None,
) -> MessageBatch:
    """Retry only FAILED messages in a batch; never resend SENT ones."""
    batch = get_batch(db, batch_id)

    failed = [
        m
        for m in _messages_for_batch(db, batch.id)
        if m.delivery_status == MessageDeliveryStatus.FAILED
    ]
    if not failed:
        raise _http(400, "No failed messages to retry in this batch")

    retryable = [m for m in failed if (m.attempt_count or 0) < _max_attempts()]
    if not retryable:
        raise _http(
            400,
            f"Failed messages have reached the maximum of {_max_attempts()} "
            "attempts and cannot be retried.",
        )

    _acquire_for_operation(db, batch)
    _process_messages(
        db,
        batch,
        provider=provider or build_message_provider(),
        should_process=(
            lambda m: m.delivery_status == MessageDeliveryStatus.FAILED
            and (m.attempt_count or 0) < _max_attempts()
        ),
    )
    return batch


def retry_message(
    db: Session,
    *,
    message_id: int,
    provider: Optional[MessageProviderInterface] = None,
) -> MessageBatch:
    """Retry a single FAILED message and return its parent batch detail source."""
    message = db.query(ParentMessage).filter(ParentMessage.id == message_id).first()
    if message is None:
        raise _http(404, "Message not found")
    if message.delivery_status != MessageDeliveryStatus.FAILED:
        raise _http(400, "Only failed messages can be retried")
    if (message.attempt_count or 0) >= _max_attempts():
        raise _http(
            400,
            f"Message has reached the maximum of {_max_attempts()} attempts "
            "and cannot be retried.",
        )

    batch = get_batch(db, message.message_batch_id)
    student = (
        db.query(Student).filter(Student.id == message.student_id).first()
    )
    student_name = student.name if student else ""
    payload = _build_payload(batch, message, student_name=student_name)

    # Per-message processing: no batch-wide PROCESSING lock is taken so a
    # single-message retry does not block an in-progress batch send.
    message.delivery_status = MessageDeliveryStatus.PROCESSING
    message.attempt_count = (message.attempt_count or 0) + 1
    message.updated_at = datetime.utcnow()
    db.commit()

    try:
        result = (provider or build_message_provider()).send(payload=payload)
    except Exception as exc:
        logger.exception("message_retry_exception message_id=%d", message.id)
        result = type("Result", (), {
            "success": False,
            "error_message": f"Unexpected error: {exc}",
            "provider_message_id": None,
            "provider_response": None,
        })()

    if result.success:
        message.delivery_status = MessageDeliveryStatus.SENT
        message.sent_at = datetime.utcnow()
        message.provider_message_id = result.provider_message_id
        message.provider_response = result.provider_response
        message.error_message = None
        message.next_retry_at = None
    else:
        message.delivery_status = MessageDeliveryStatus.FAILED
        message.error_message = result.error_message or "Message delivery failed."
        message.sent_at = None
        # V2.5: Schedule next retry.
        if (message.attempt_count or 0) < _max_attempts():
            message.next_retry_at = _compute_next_retry_at(message.attempt_count)
        else:
            message.next_retry_at = None
    message.updated_at = datetime.utcnow()
    db.commit()

    _recompute_batch(db, batch)
    db.commit()
    db.refresh(batch)
    return batch
