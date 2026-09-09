import hashlib
import hmac
import json

from conftest import make_auth_header


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def _auth_headers(secret: str, body: bytes) -> dict:
    return {"X-Hub-Signature-256": _sign(secret, body)}


def _seed_and_send(client, db, admin_user, monkeypatch):
    """Create a class/division/students, save attendance, build + send a batch.

    Returns the provider_message_id assigned by the mock provider and the
    batch id. Also sets the webhook secrets on settings for the request.
    """
    from app.core import config
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    monkeypatch.setattr(config.settings, "WHATSAPP_APP_SECRET", "webhook-app-secret")
    monkeypatch.setattr(
        config.settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "test-verify-token"
    )

    school_class = SchoolClass(
        name="Class 10", is_active=True, created_by=admin_user.id
    )
    db.add(school_class)
    db.flush()
    division = Division(
        name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id
    )
    db.add(division)
    db.flush()
    student = Student(
        class_id=school_class.id,
        division_id=division.id,
        name="Rahul Patel",
        roll_number="1",
        parent_name="Rahul's Parent",
        parent_whatsapp_number="+919000000000",
        is_active=True,
        created_by=admin_user.id,
    )
    db.add(student)
    db.flush()

    headers = make_auth_header(admin_user)
    resp = client.post(
        "/api/v1/attendance",
        json={
            "class_id": school_class.id,
            "division_id": division.id,
            "attendance_date": "2026-08-31",
            "students": [
                # V2.5: only ABSENT students produce absence notifications.
                {"student_id": student.id, "status": "ABSENT", "remarks": None}
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    att = resp.json()

    resp = client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": att["id"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    resp = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    message = body["messages"][0]
    assert message["delivery_status"] == "SENT"
    return body, message["provider_message_id"]


def _webhook_payload(message_id: str, status: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": message_id,
                                    "status": status,
                                    "timestamp": "1700000000",
                                    "recipient_id": "919000000000",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# GET verification handshake
# ---------------------------------------------------------------------------
def test_webhook_get_verification_success(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "test-verify-token"
    )
    resp = client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "challenge-123",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "challenge-123"


def test_webhook_get_verification_failure(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "test-verify-token"
    )
    resp = client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "challenge-123",
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST signature validation
# ---------------------------------------------------------------------------
def test_webhook_rejects_invalid_signature(client, monkeypatch):
    body = json.dumps(_webhook_payload("wamid.ABC", "delivered")).encode("utf-8")
    resp = client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=invalid"},
    )
    assert resp.status_code == 403


def test_webhook_rejects_missing_signature(client, monkeypatch):
    body = json.dumps(_webhook_payload("wamid.ABC", "delivered")).encode("utf-8")
    resp = client.post("/api/v1/webhooks/whatsapp", content=body)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Monotonic status progression + idempotency
# ---------------------------------------------------------------------------
def test_webhook_advances_status_monotonically(client, db_session, users, monkeypatch):
    _, message_id = _seed_and_send(client, db_session, users["admin"], monkeypatch)

    # sent (already SENT) -> delivered -> read
    for status in ("sent", "delivered", "read"):
        body = json.dumps(_webhook_payload(message_id, status)).encode("utf-8")
        resp = client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers=_auth_headers("webhook-app-secret", body),
        )
        assert resp.status_code == 200, resp.text

    from app.models.message import MessageDeliveryStatus, ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.provider_message_id == message_id
    ).first()
    assert msg.delivery_status == MessageDeliveryStatus.READ


def test_webhook_never_downgrades_read(client, db_session, users, monkeypatch):
    _, message_id = _seed_and_send(client, db_session, users["admin"], monkeypatch)

    # Deliver in reverse order: read first, then delivered -> must stay READ.
    for status in ("read", "delivered", "sent"):
        body = json.dumps(_webhook_payload(message_id, status)).encode("utf-8")
        resp = client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers=_auth_headers("webhook-app-secret", body),
        )
        assert resp.status_code == 200, resp.text

    from app.models.message import MessageDeliveryStatus, ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.provider_message_id == message_id
    ).first()
    assert msg.delivery_status == MessageDeliveryStatus.READ


def test_webhook_is_idempotent_on_duplicate(client, db_session, users, monkeypatch):
    _, message_id = _seed_and_send(client, db_session, users["admin"], monkeypatch)

    body = json.dumps(_webhook_payload(message_id, "delivered")).encode("utf-8")
    for _ in range(2):
        resp = client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers=_auth_headers("webhook-app-secret", body),
        )
        assert resp.status_code == 200, resp.text

    from app.models.message import MessageDeliveryStatus, ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.provider_message_id == message_id
    ).first()
    assert msg.delivery_status == MessageDeliveryStatus.DELIVERED
    # No conflict or error on repeat delivery.


def test_webhook_failed_status_marks_failed(client, db_session, users, monkeypatch):
    _, message_id = _seed_and_send(client, db_session, users["admin"], monkeypatch)

    body = json.dumps(_webhook_payload(message_id, "failed")).encode("utf-8")
    resp = client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers=_auth_headers("webhook-app-secret", body),
    )
    assert resp.status_code == 200, resp.text

    from app.models.message import MessageDeliveryStatus, ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.provider_message_id == message_id
    ).first()
    assert msg.delivery_status == MessageDeliveryStatus.FAILED


def test_webhook_failed_does_not_downgrade_read(client, db_session, users, monkeypatch):
    _, message_id = _seed_and_send(client, db_session, users["admin"], monkeypatch)

    for status in ("read", "failed"):
        body = json.dumps(_webhook_payload(message_id, status)).encode("utf-8")
        resp = client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers=_auth_headers("webhook-app-secret", body),
        )
        assert resp.status_code == 200, resp.text

    from app.models.message import MessageDeliveryStatus, ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.provider_message_id == message_id
    ).first()
    assert msg.delivery_status == MessageDeliveryStatus.READ


def test_webhook_failed_does_not_downgrade_delivered(client, db_session, users, monkeypatch):
    _, message_id = _seed_and_send(client, db_session, users["admin"], monkeypatch)

    for status in ("delivered", "failed"):
        body = json.dumps(_webhook_payload(message_id, status)).encode("utf-8")
        resp = client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers=_auth_headers("webhook-app-secret", body),
        )
        assert resp.status_code == 200, resp.text

    from app.models.message import MessageDeliveryStatus, ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.provider_message_id == message_id
    ).first()
    assert msg.delivery_status == MessageDeliveryStatus.DELIVERED


def test_webhook_unknown_message_id_is_ignored(client, db_session, users, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "WHATSAPP_APP_SECRET", "webhook-app-secret")
    body = json.dumps(_webhook_payload("wamid.NOT_FOUND", "delivered")).encode("utf-8")
    resp = client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers=_auth_headers("webhook-app-secret", body),
    )
    assert resp.status_code == 200, resp.text