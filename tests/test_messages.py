from conftest import grant_teacher_access, make_auth_header


def _seed_class_division_students(db, admin_user, whatsapp="+919000000000"):
    """Create a class, a division and active students directly in the DB."""
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    school_class = SchoolClass(name="Class 10", is_active=True, created_by=admin_user.id)
    db.add(school_class)
    db.flush()

    division = Division(name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id)
    db.add(division)
    db.flush()

    def make_student(name, roll, parent_name="Parent", parent_number=whatsapp):
        return Student(
            class_id=school_class.id,
            division_id=division.id,
            name=name,
            roll_number=roll,
            parent_name=parent_name,
            parent_whatsapp_number=parent_number,
            is_active=True,
            created_by=admin_user.id,
        )

    rahul = make_student("Rahul Patel", "1", parent_name="Rahul's Parent")
    priya = make_student("Priya Shah", "2", parent_name="Priya's Parent")
    aarav = make_student("Aarav Mehta", "10", parent_name="Aarav's Parent")
    db.add_all([rahul, priya, aarav])
    db.flush()

    return {
        "class": school_class,
        "division": division,
        "students": {"rahul": rahul, "priya": priya, "aarav": aarav},
    }


def _save_attendance(client, db_session, headers, data, statuses=None):
    """Save a COMPLETED attendance session via the API.

    V2.5: Default statuses make all students ABSENT so that batch creation
    produces one message per student (absence notifications for all). Tests
    that need mixed statuses pass an explicit statuses dict.
    """
    statuses = statuses or {
        "rahul": "ABSENT",
        "priya": "ABSENT",
        "aarav": "ABSENT",
    }
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-08-31",
        "students": [
            {"student_id": data["students"][k].id, "status": v, "remarks": None}
            for k, v in statuses.items()
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def _create_batch(client, headers, attendance_id):
    resp = client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": attendance_id},
        headers=headers,
    )
    return resp


# ---------------------------------------------------------------------------
# Create batch
# ---------------------------------------------------------------------------
def test_create_batch_requires_auth(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, make_auth_header(users["admin"]), data)
    resp = client.post(
        "/api/v1/messages/batches", json={"attendance_session_id": att["id"]}
    )
    assert resp.status_code == 401


def test_create_batch_creates_one_message_per_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    att = _save_attendance(client, db_session, make_auth_header(users["admin"]), data)
    headers = make_auth_header(users["teacher"])

    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["attendance_session_id"] == att["id"]
    assert body["class_name"] == "Class 10"
    assert body["division_name"] == "A"
    assert body["status"] == "PENDING"
    assert body["total_messages"] == 3
    assert body["sent_count"] == 0
    assert body["pending_count"] == 3
    assert body["failed_count"] == 0
    assert len(body["messages"]) == 3

    by_student = {m["student_id"]: m for m in body["messages"]}
    assert set(by_student.keys()) == {
        data["students"]["rahul"].id,
        data["students"]["priya"].id,
        data["students"]["aarav"].id,
    }

    rahul_msg = by_student[data["students"]["rahul"].id]
    assert rahul_msg["delivery_status"] == "PENDING"
    assert rahul_msg["parent_name"] == "Rahul's Parent"
    assert rahul_msg["parent_whatsapp_number"] == "+919000000000"
    # V2.5: messages are absence notifications, so the snapshot is ABSENT.
    assert rahul_msg["attendance_status"] == "ABSENT"
    # Privacy: content for Rahul mentions only Rahul.
    assert "Rahul Patel" in rahul_msg["message_content"]
    assert "Priya" not in rahul_msg["message_content"]
    assert "Aarav" not in rahul_msg["message_content"]

    priya_msg = by_student[data["students"]["priya"].id]
    assert priya_msg["attendance_status"] == "ABSENT"


def test_create_batch_rejects_duplicate(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)

    first = _create_batch(client, headers, att["id"])
    assert first.status_code == 201
    second = _create_batch(client, headers, att["id"])
    assert second.status_code == 409


def test_create_batch_rejects_missing_session(client, db_session, users):
    resp = _create_batch(client, make_auth_header(users["admin"]), 99999)
    assert resp.status_code == 404


def test_create_batch_rejects_non_completed_session(client, db_session, users):
    from app.models.attendance import AttendanceSession, SessionStatus

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)

    session = db_session.get(AttendanceSession, att["id"])
    session.status = SessionStatus.DRAFT
    db_session.commit()

    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 400


def test_create_batch_marks_missing_whatsapp_as_failed(client, db_session, users):
    data = _seed_class_division_students(
        db_session, users["admin"], whatsapp="not-a-number"
    )
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)

    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["total_messages"] == 3
    assert body["failed_count"] == 3
    assert body["pending_count"] == 0
    for m in body["messages"]:
        assert m["delivery_status"] == "FAILED"
        assert "Parent WhatsApp number unavailable or invalid." == m["error_message"]


# ---------------------------------------------------------------------------
# Get / list / pending / sent
# ---------------------------------------------------------------------------
def test_get_batch_detail(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()

    resp = client.get(f"/api/v1/messages/batches/{created['id']}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert len(body["messages"]) == 3
    # Student name + roll surfaced in detail.
    assert {m["student_name"] for m in body["messages"]} == {
        "Rahul Patel",
        "Priya Shah",
        "Aarav Mehta",
    }


def test_list_batches_and_filters(client, db_session, users):
    from app.models.message import MessageBatch

    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    resp = client.get("/api/v1/messages/batches", headers=headers)
    assert resp.status_code == 200
    assert [b["id"] for b in resp.json()["items"]] == [batch_id]

    # Filter by class.
    resp = client.get(
        "/api/v1/messages/batches",
        params={"class_id": data["class"].id},
        headers=headers,
    )
    assert [b["id"] for b in resp.json()["items"]] == [batch_id]

    # Filter by attendance date.
    resp = client.get(
        "/api/v1/messages/batches",
        params={"attendance_date": "2026-08-31"},
        headers=headers,
    )
    assert [b["id"] for b in resp.json()["items"]] == [batch_id]

    # Status filter that matches nothing.
    batch = db_session.get(MessageBatch, batch_id)
    batch.status = "COMPLETED"
    db_session.commit()
    resp = client.get(
        "/api/v1/messages/batches", params={"status": "PENDING"}, headers=headers
    )
    assert resp.json()["items"] == []


def test_pending_and_sent_endpoints(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Initially PENDING -> appears in /pending, not /sent.
    pending = client.get("/api/v1/messages/pending", headers=headers).json()
    assert [b["id"] for b in pending["items"]] == [batch_id]
    sent = client.get("/api/v1/messages/sent", headers=headers).json()
    assert sent["items"] == []

    # Send -> COMPLETED -> appears in /sent, not /pending.
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)
    pending = client.get("/api/v1/messages/pending", headers=headers).json()
    assert pending["items"] == []
    sent = client.get("/api/v1/messages/sent", headers=headers).json()
    assert [b["id"] for b in sent["items"]] == [batch_id]


# ---------------------------------------------------------------------------
# Send / retry
# ---------------------------------------------------------------------------
def test_send_batch_marks_all_sent_and_completed(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    resp = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["sent_count"] == 3
    assert body["pending_count"] == 0
    assert body["failed_count"] == 0
    for m in body["messages"]:
        assert m["delivery_status"] == "SENT"
        assert m["sent_at"] is not None
        assert m["attempt_count"] == 1
        assert m["provider_message_id"].startswith("mock-")


def test_send_batch_is_idempotent(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    first = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers).json()
    assert first["status"] == "COMPLETED"

    # Second send must not resend SENT messages.
    second = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers).json()
    assert second["status"] == "COMPLETED"
    assert second["sent_count"] == 3
    assert all(m["attempt_count"] == 1 for m in second["messages"])


def test_send_batch_partial_failure(client, db_session, users):
    from app.models.message import ParentMessage

    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]
    assert created["pending_count"] == 3

    # Force one message to FAILED (a transient failure) so the batch has a mix
    # of SENT and FAILED after sending -> PARTIAL_FAILED.
    message = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.message_batch_id == batch_id)
        .first()
    )
    message.delivery_status = "FAILED"
    message.error_message = "transient"
    db_session.commit()

    resp = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)
    body = resp.json()
    assert body["status"] == "PARTIAL_FAILED"
    assert body["sent_count"] == 2
    assert body["failed_count"] == 1
    assert body["pending_count"] == 0

    # The FAILED (still-valid-numbered) message can be retried.
    retried = client.post(
        f"/api/v1/messages/batches/{batch_id}/retry-failed", headers=headers
    ).json()
    assert retried["status"] == "COMPLETED"
    assert retried["failed_count"] == 0
    assert retried["sent_count"] == 3


def test_retry_failed_batch_resends_only_failed(client, db_session, users):
    from app.models.message import MessageDeliveryStatus, ParentMessage

    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Send all -> COMPLETED.
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    # Manually force one valid-numbered message back to FAILED to simulate a
    # transient provider failure.
    message = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.message_batch_id == batch_id)
        .first()
    )
    message.delivery_status = MessageDeliveryStatus.FAILED
    message.error_message = "simulated transient failure"
    db_session.commit()
    message_id = message.id

    resp = client.post(
        f"/api/v1/messages/batches/{batch_id}/retry-failed", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["failed_count"] == 0

    retried = next(m for m in body["messages"] if m["id"] == message_id)
    assert retried["delivery_status"] == "SENT"
    assert retried["attempt_count"] == 2


def test_retry_individual_message(client, db_session, users):
    from app.models.message import ParentMessage

    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    message = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.message_batch_id == batch_id)
        .first()
    )
    message.delivery_status = "FAILED"
    message.error_message = "transient"
    db_session.commit()

    resp = client.post(f"/api/v1/messages/{message.id}/retry", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    retried = next(m for m in body["messages"] if m["id"] == message.id)
    assert retried["delivery_status"] == "SENT"
    assert retried["attempt_count"] == 1

    # Retrying a SENT message is rejected.
    resp2 = client.post(f"/api/v1/messages/{message.id}/retry", headers=headers)
    assert resp2.status_code == 400


def test_retry_individual_message_requires_auth(client, db_session, users):
    resp = client.post("/api/v1/messages/1/retry")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Max attempts enforcement
# ---------------------------------------------------------------------------
def test_retry_batch_rejects_when_max_attempts_reached(client, db_session, users, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "MESSAGE_MAX_ATTEMPTS", "2")
    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # First send (attempt 1 for all messages).
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    # Force one to FAILED with attempt_count=2 (already at max).
    from app.models.message import ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    msg.delivery_status = "FAILED"
    msg.attempt_count = 2
    msg.error_message = "transient"
    db_session.commit()

    resp = client.post(
        f"/api/v1/messages/batches/{batch_id}/retry-failed", headers=headers
    )
    assert resp.status_code == 400
    assert "maximum" in resp.json()["detail"]


def test_retry_individual_rejects_when_max_attempts_reached(client, db_session, users, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "MESSAGE_MAX_ATTEMPTS", "1")
    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Send (attempt 1).
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    from app.models.message import ParentMessage

    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    msg.delivery_status = "FAILED"
    msg.attempt_count = 1
    msg.error_message = "transient"
    db_session.commit()

    resp = client.post(f"/api/v1/messages/{msg.id}/retry", headers=headers)
    assert resp.status_code == 400
    assert "maximum" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DELIVERED / READ batch counting
# ---------------------------------------------------------------------------
def test_batch_counts_delivered_and_read_as_sent(client, db_session, users):
    from app.models.message import MessageDeliveryStatus, ParentMessage

    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Send all -> COMPLETED.
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    messages = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.message_batch_id == batch_id)
        .all()
    )
    # Mark one SENT, one DELIVERED, one READ.
    messages[0].delivery_status = MessageDeliveryStatus.SENT
    messages[1].delivery_status = MessageDeliveryStatus.DELIVERED
    messages[2].delivery_status = MessageDeliveryStatus.READ
    db_session.commit()

    resp = client.get(f"/api/v1/messages/batches/{batch_id}", headers=headers)
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["sent_count"] == 3
    assert body["failed_count"] == 0
    assert body["pending_count"] == 0


# ---------------------------------------------------------------------------
# Privacy: one student per request (provider receives only one recipient)
# ---------------------------------------------------------------------------
def test_send_always_passes_single_recipient_to_provider(client, db_session, users):
    """Each message send passes exactly one phone number to the provider."""
    from app.providers.base import MessagePayload

    class RecordingProvider:
        def __init__(self):
            self.calls = []

        def send(self, *, payload: MessagePayload):
            self.calls.append(payload)
            from app.providers.base import MessageSendResult
            import hashlib

            digest = hashlib.sha1(payload.to_number.encode()).hexdigest()
            return MessageSendResult(
                success=True,
                provider_message_id=f"rec-{digest[:12]}",
            )

    provider = RecordingProvider()
    headers = make_auth_header(users["admin"])
    data = _seed_class_division_students(db_session, users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    from app.services import message_service

    message_service.send_batch(db_session, batch_id=batch_id, provider=provider)

    assert len(provider.calls) == 3
    for call in provider.calls:
        assert isinstance(call.to_number, str)
        assert "+" in call.to_number
        # One scalar recipient per request, never a list/group of students.
        assert not isinstance(call.to_number, (list, tuple, set))


# ---------------------------------------------------------------------------
# Phone normalization via E.164 utility
# ---------------------------------------------------------------------------
def test_phone_normalization_accepts_valid_e164():
    from app.core.phone_normalization import is_valid_e164, normalize_e164

    assert normalize_e164("+919876543210") == "+919876543210"
    assert normalize_e164("+1 (555) 123-4567") == "+15551234567"
    assert is_valid_e164("+919876543210") is True
    assert is_valid_e164("+15551234567") is True


def test_phone_normalization_rejects_invalid():
    from app.core.phone_normalization import is_valid_e164, normalize_e164
    import pytest

    assert is_valid_e164("") is False
    assert is_valid_e164("12345") is False
    assert is_valid_e164(None) is False
    with pytest.raises(ValueError):
        normalize_e164("12345")
    with pytest.raises(ValueError):
        normalize_e164("not-a-number")


def test_phone_normalization_preserves_country_code():
    from app.core.phone_normalization import normalize_e164

    # Without '+' gets rejected (no silent country code guessing).
    assert normalize_e164("+447911123456").startswith("+44")
    assert normalize_e164("+919876543210").startswith("+91")
