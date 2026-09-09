"""Processing of WhatsApp Cloud API webhook events (delivery status updates).

WhatsApp delivers asynchronous ``statuses`` events (sent / delivered / read /
failed) keyed by the provider message id (``wamid``). We map those back to the
stored ``parent_messages.provider_message_id`` and advance the delivery status
with strict monotonic progression and idempotency so duplicates and
out-of-order events never cause a downgrade.
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.message import (
    DELIVERY_STATUS_RANK,
    MessageDeliveryStatus,
    ParentMessage,
)

logger = logging.getLogger("app.webhooks")

# WhatsApp statuses we accept, mapped to our delivery status (except FAILED,
# which is handled separately).
_STATUS_TO_DELIVERY = {
    "sent": MessageDeliveryStatus.SENT,
    "delivered": MessageDeliveryStatus.DELIVERED,
    "read": MessageDeliveryStatus.READ,
}


def _try_status_rank(status: MessageDeliveryStatus) -> int:
    return DELIVERY_STATUS_RANK.get(status, -1)


def _extract_status_events(payload: dict) -> List[dict]:
    """Flatten the ``changes``/``statuses`` array into per-event dicts.

    Each item is a dict with keys ``message_id`` (str) and ``status`` (str).
    """
    events: List[dict] = []
    try:
        entries = payload.get("entry") or []
        for entry in entries:
            changes = entry.get("changes") or []
            for change in changes:
                value = change.get("value") or {}
                statuses = value.get("statuses") or []
                for status in statuses:
                    wamid = status.get("id")
                    status_name = (status.get("status") or "").lower()
                    if not wamid or not status_name:
                        continue
                    events.append(
                        {
                            "message_id": wamid,
                            "status": status_name,
                            "statuses_payload": status,
                        }
                    )
    except (TypeError, AttributeError):
        return []
    return events


def apply_status_event(db: Session, message_id: str, status_name: str) -> bool:
    """Apply a single delivery-status event for a provider message id.

    Returns True if the message was found and updated (or was an idempotent
    no-op on a message already at that state), False if the message id could
    not be matched to a stored record.

    Rules:
      * PENDING -> PROCESSING -> SENT -> DELIVERED -> READ (monotonic; never
        downgrade, e.g. READ never becomes SENT).
      * FAILED is allowed from PENDING/PROCESSING/SENT (permanent failure).
    """
    status = _STATUS_TO_DELIVERY.get(status_name)
    if status is None and status_name != "failed":
        # Unknown status (e.g. "deleted"); ignore safely.
        return False

    message: Optional[ParentMessage] = (
        db.query(ParentMessage)
        .filter(ParentMessage.provider_message_id == message_id)
        .first()
    )
    if message is None:
        logger.warning(
            "webhook status for unknown message_id=%s status=%s",
            message_id[:16],
            status_name,
        )
        return False

    current = message.delivery_status

    if status_name == "failed":
        # Failure is only allowed from PENDING/PROCESSING/SENT per spec; a
        # message already DELIVERED/READ cannot later be downgraded to FAILED.
        if current in (
            MessageDeliveryStatus.FAILED,
            MessageDeliveryStatus.DELIVERED,
            MessageDeliveryStatus.READ,
        ):
            return True  # idempotent / no downgrade
        message.delivery_status = MessageDeliveryStatus.FAILED
        message.error_message = message.error_message or (
            "WhatsApp reported the message as failed."
        )
    else:
        if current == MessageDeliveryStatus.FAILED:
            # A FAILED message cannot be revived by a delivery webhook.
            return True
        if _try_status_rank(status) <= _try_status_rank(current):
            # Same or earlier state: idempotent / out-of-order -> no downgrade.
            return True
        message.delivery_status = status

    from datetime import datetime

    message.updated_at = datetime.utcnow()
    db.commit()
    return True


def process_webhook_payload(db: Session, payload: dict) -> Tuple[int, int]:
    """Process a full webhook delivery-status payload.

    Returns a ``(updated, unmatched)`` tuple. ``updated`` counts events that
    were applied (including idempotent no-ops on matched messages); 
    ``unmatched`` counts events whose message id matched no stored record (and
    unknown statuses).
    """
    events = _extract_status_events(payload)
    updated = 0
    unmatched = 0
    for event in events:
        applied = apply_status_event(
            db, event["message_id"], event["status"]
        )
        if applied:
            updated += 1
        else:
            unmatched += 1

    # Recompute affected batches after applying events.
    _recompute_affected_batches(db)
    db.commit()
    return updated, unmatched


def _recompute_affected_batches(db: Session) -> None:
    """Recompute batch summary counts/status for any touched parent messages.

    Imported lazily to avoid a circular import at module load time.
    """
    from app.services import message_service

    # Refresh all parent messages that were just committed so the recompute
    # sees fresh state; we recompute all touched batches.
    message_rows = db.query(ParentMessage).all()
    batch_ids = {m.message_batch_id for m in message_rows}
    for batch_id in batch_ids:
        batch = message_service.get_batch(db, batch_id)
        message_service._recompute_batch(db, batch)
