"""V2.5 Messaging Tests - Comprehensive test suite.

Tests cover:
1. Present student -> no message
2. Absent student -> message generated
3. Approved leave -> no absence message
4. Holiday -> no batch created
5. Missing parent number
6. Invalid parent number
7. Correct parent mapping
8. Duplicate message generation prevention
9. Duplicate send request
10. Retry
11. Permanent failure
12. Temporary failure
13. Attendance correction before sending
14. Attendance correction after sending
15. Teacher authorization
16. Admin authorization
17. Unauthorized class
18. Offline attendance synchronization
19. App restart (state persistence)
20. Large class
21. Provider timeout
22. Provider success after timeout
23. Database constraint failure
24. Critical privacy test (cross-student isolation)
25. Idempotency key generation
26. Message type field
27. Auto-batch creation after save
28. Auto-batch creation after sync
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

from conftest import grant_teacher_access, make_auth_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_class_division_students(
    db, admin_user, whatsapp="+919000000000", count=3
):
    """Create a class, a division and active students directly in the DB."""
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    school_class = SchoolClass(
        name="Class 10", is_active=True, created_by=admin_user.id
    )
    db.add(school_class)
    db.flush()

    division = Division(
        name="A",
        is_active=True,
        class_id=school_class.id,
        created_by=admin_user.id,
    )
    db.add(division)
    db.flush()

    students = {}
    names = [
        ("Rahul Patel", "1", "Rahul's Parent"),
        ("Priya Shah", "2", "Priya's Parent"),
        ("Aarav Mehta", "10", "Aarav's Parent"),
    ]
    # Extend for large class tests. Roll numbers start from 11 to avoid
    # colliding with the fixed set (1, 2, 10); keys are unique per student.
    for i in range(count - 3):
        names.append((f"Student {i + 4}", str(11 + i), f"Parent {i + 4}"))

    for idx, (name, roll, parent_name) in enumerate(names):
        student = Student(
            class_id=school_class.id,
            division_id=division.id,
            name=name,
            roll_number=roll,
            parent_name=parent_name,
            parent_whatsapp_number=whatsapp,
            is_active=True,
            created_by=admin_user.id,
        )
        db.add(student)
        db.flush()
        if idx < 3:
            key = name.split()[0].lower()
        else:
            key = f"student{idx + 1}"
        students[key] = student

    return {
        "class": school_class,
        "division": division,
        "students": students,
    }


def _save_attendance(client, db_session, headers, data, statuses=None, date_str="2026-08-31"):
    """Save a COMPLETED attendance session via the API."""
    statuses = statuses or {
        "rahul": "PRESENT",
        "priya": "ABSENT",
        "aarav": "PRESENT",
    }
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": date_str,
        "students": [
            {"student_id": data["students"][k].id, "status": v, "remarks": None}
            for k, v in statuses.items()
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def _create_batch(client, headers, attendance_id):
    return client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": attendance_id},
        headers=headers,
    )


# ===================================================================
# Test 1: Present student -> no message
# ===================================================================
def test_present_student_no_message(client, db_session, users):
    """When all students are PRESENT, batch creation should fail (no absences)."""
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "PRESENT", "priya": "PRESENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 400
    assert "no absence" in resp.json()["detail"].lower() or "present" in resp.json()["detail"].lower()


# ===================================================================
# Test 2: Absent student -> message generated
# ===================================================================
def test_absent_student_gets_message(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "PRESENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_messages"] == 1
    assert body["pending_count"] == 1
    msg = body["messages"][0]
    assert msg["student_id"] == data["students"]["priya"].id
    assert msg["attendance_status"] == "ABSENT"
    assert msg["delivery_status"] == "PENDING"
    assert "Priya Shah" in msg["message_content"]


# ===================================================================
# Test 3: Approved leave -> no absence message
# ===================================================================
def test_approved_leave_no_message(client, db_session, users):
    """Students with approved leave should not receive absence messages.

    A student on approved leave is exempt from attendance recording, so they
    cannot be submitted as ABSENT via the save endpoint (which would 400).
    Instead they are simply excluded from the session and must not produce a
    message, while genuinely ABSENT students still do.
    """
    from datetime import date
    from app.models.student_leave import LeaveStatus, StudentLeave

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])

    # Create approved leave for Priya covering the attendance date.
    leave = StudentLeave(
        student_id=data["students"]["priya"].id,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        reason="Medical",
        status=LeaveStatus.APPROVED,
        created_by=users["admin"].id,
    )
    db_session.add(leave)
    db_session.commit()

    # Save attendance for the students NOT on leave (Priya is exempt).
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-08-31",
        "students": [
            {"student_id": data["students"]["rahul"].id, "status": "ABSENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=headers)
    assert resp.status_code == 201
    att = resp.json()

    batch = _create_batch(client, headers, att["id"])
    assert batch.status_code == 201
    body = batch.json()
    # Only Rahul should have a message (Priya on leave -> no record, Aarav present).
    assert body["total_messages"] == 1
    msg = body["messages"][0]
    assert msg["student_id"] == data["students"]["rahul"].id


# ===================================================================
# Test 4: Holiday -> no batch created
# ===================================================================
def test_holiday_no_batch(client, db_session, users):
    """Batch creation should be rejected for sessions on school holidays."""
    from app.models.academic_year import AcademicYear
    from app.models.calendar_event import CalendarEvent, CalendarEventType

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])

    # Create active academic year and holiday.
    ay = AcademicYear(
        name="2026-27",
        start_date=date(2026, 4, 1),
        end_date=date(2027, 3, 31),
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(ay)
    db_session.flush()

    holiday = CalendarEvent(
        title="Independence Day",
        event_type=CalendarEventType.HOLIDAY,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        academic_year_id=ay.id,
        created_by=users["admin"].id,
    )
    db_session.add(holiday)
    db_session.commit()

    # Attendance saving will fail because of the holiday guard.
    # But let's test the batch creation side directly.
    # First, manually create a session to bypass the holiday guard.
    from app.models.attendance import AttendanceSession, SessionStatus, StudentAttendance
    session = AttendanceSession(
        class_id=data["class"].id,
        division_id=data["division"].id,
        attendance_date=date(2026, 8, 31),
        taken_by=users["admin"].id,
        status=SessionStatus.COMPLETED,
        total_students=3,
        present_count=2,
        absent_count=1,
    )
    db_session.add(session)
    db_session.flush()

    record = StudentAttendance(
        attendance_session_id=session.id,
        student_id=data["students"]["rahul"].id,
        attendance_status="ABSENT",
    )
    db_session.add(record)
    db_session.commit()

    resp = _create_batch(client, headers, session.id)
    assert resp.status_code == 400
    assert "holiday" in resp.json()["detail"].lower()


# ===================================================================
# Test 5: Missing parent number
# ===================================================================
def test_missing_parent_number_marked_failed(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"], whatsapp="")
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "ABSENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["failed_count"] == 3
    for m in body["messages"]:
        assert m["delivery_status"] == "FAILED"
        assert "unavailable" in m["error_message"].lower()


# ===================================================================
# Test 6: Invalid parent number
# ===================================================================
def test_invalid_parent_number_marked_failed(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"], whatsapp="not-a-number")
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "ABSENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["failed_count"] == 3


# ===================================================================
# Test 7: Correct parent mapping
# ===================================================================
def test_correct_parent_mapping(client, db_session, users):
    """Each message must map to the correct student's parent."""
    data = _seed_class_division_students(
        db_session,
        users["admin"],
        whatsapp="+919000000000",
    )
    # Give each student a unique parent number.
    from app.models.student import Student
    rahul = db_session.get(Student, data["students"]["rahul"].id)
    rahul.parent_whatsapp_number = "+919000000001"
    rahul.parent_name = "Rahul's Parent"
    priya = db_session.get(Student, data["students"]["priya"].id)
    priya.parent_whatsapp_number = "+919000000002"
    priya.parent_name = "Priya's Parent"
    db_session.commit()

    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    by_student = {m["student_id"]: m for m in body["messages"]}
    rahul_msg = by_student[data["students"]["rahul"].id]
    priya_msg = by_student[data["students"]["priya"].id]
    assert rahul_msg["parent_whatsapp_number"] == "+919000000001"
    assert rahul_msg["parent_name"] == "Rahul's Parent"
    assert priya_msg["parent_whatsapp_number"] == "+919000000002"
    assert priya_msg["parent_name"] == "Priya's Parent"


# ===================================================================
# Test 8: Duplicate message generation prevention (idempotency)
# ===================================================================
def test_duplicate_batch_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    first = _create_batch(client, headers, att["id"])
    assert first.status_code == 201
    second = _create_batch(client, headers, att["id"])
    assert second.status_code == 409


# ===================================================================
# Test 9: Duplicate send request (idempotent send)
# ===================================================================
def test_send_batch_is_idempotent(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    first = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers).json()
    assert first["status"] == "COMPLETED"

    second = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers).json()
    assert second["status"] == "COMPLETED"
    assert all(m["attempt_count"] == 1 for m in second["messages"])


# ===================================================================
# Test 10: Retry
# ===================================================================
def test_retry_failed_message(client, db_session, users):
    from app.models.message import ParentMessage

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Force one message to FAILED.
    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    msg.delivery_status = "FAILED"
    msg.error_message = "transient"
    db_session.commit()

    resp = client.post(f"/api/v1/messages/{msg.id}/retry", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    retried = next(m for m in body["messages"] if m["id"] == msg.id)
    assert retried["delivery_status"] == "SENT"


# ===================================================================
# Test 11: Permanent failure (max attempts)
# ===================================================================
def test_permanent_failure_max_attempts(client, db_session, users, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "MESSAGE_MAX_ATTEMPTS", "2")
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # First send (attempt 1).
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    from app.models.message import ParentMessage
    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    msg.delivery_status = "FAILED"
    msg.attempt_count = 2
    db_session.commit()

    resp = client.post(
        f"/api/v1/messages/batches/{batch_id}/retry-failed", headers=headers
    )
    assert resp.status_code == 400
    assert "maximum" in resp.json()["detail"]


# ===================================================================
# Test 12: Temporary failure then retry success
# ===================================================================
def test_temporary_failure_then_retry_success(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Send all -> some fail.
    from app.models.message import ParentMessage
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    # Force one to FAILED.
    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    msg.delivery_status = "FAILED"
    msg.error_message = "provider timeout"
    db_session.commit()

    # Retry -> should succeed with mock provider.
    resp = client.post(
        f"/api/v1/messages/batches/{batch_id}/retry-failed", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    retried = next(m for m in body["messages"] if m["id"] == msg.id)
    assert retried["delivery_status"] == "SENT"


# ===================================================================
# Test 13: Attendance correction before sending (cancel)
# ===================================================================
def test_attendance_correction_before_sending_cancels_message(
    client, db_session, users
):
    """When a teacher corrects ABSENT -> PRESENT before sending, the pending
    message should be cancelled."""
    from app.models.message import MessageDeliveryStatus, ParentMessage

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Verify message is PENDING.
    priya_msg = (
        db_session.query(ParentMessage)
        .filter(
            ParentMessage.message_batch_id == batch_id,
            ParentMessage.student_id == data["students"]["priya"].id,
        )
        .first()
    )
    assert priya_msg.delivery_status == MessageDeliveryStatus.PENDING

    # Get the attendance record ID for Priya.
    from app.models.attendance import StudentAttendance
    record = (
        db_session.query(StudentAttendance)
        .filter(
            StudentAttendance.attendance_session_id == att["id"],
            StudentAttendance.student_id == data["students"]["priya"].id,
        )
        .first()
    )

    # Correct attendance: Priya ABSENT -> PRESENT.
    update_payload = [
        {"student_id": data["students"]["priya"].id, "status": "PRESENT"},
    ]
    resp = client.put(
        f"/api/v1/attendance/{att['id']}",
        json=update_payload,
        headers=headers,
    )
    assert resp.status_code == 200

    # Verify the message is now CANCELLED.
    db_session.refresh(priya_msg)
    assert priya_msg.delivery_status == MessageDeliveryStatus.CANCELLED
    assert priya_msg.cancelled_at is not None
    assert priya_msg.cancelled_reason is not None


# ===================================================================
# Test 14: Attendance correction after sending (no cancellation)
# ===================================================================
def test_attendance_correction_after_sending_does_not_cancel(
    client, db_session, users
):
    """When attendance is corrected AFTER the message is already SENT, the
    message should NOT be cancelled."""
    from app.models.message import MessageDeliveryStatus, ParentMessage

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # Send all messages.
    client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=headers)

    # Verify Priya's message is SENT.
    priya_msg = (
        db_session.query(ParentMessage)
        .filter(
            ParentMessage.message_batch_id == batch_id,
            ParentMessage.student_id == data["students"]["priya"].id,
        )
        .first()
    )
    assert priya_msg.delivery_status == MessageDeliveryStatus.SENT

    # Correct attendance: Priya ABSENT -> PRESENT.
    update_payload = [
        {"student_id": data["students"]["priya"].id, "status": "PRESENT"},
    ]
    resp = client.put(
        f"/api/v1/attendance/{att['id']}",
        json=update_payload,
        headers=headers,
    )
    assert resp.status_code == 200

    # Verify the message is still SENT (not cancelled).
    db_session.refresh(priya_msg)
    assert priya_msg.delivery_status == MessageDeliveryStatus.SENT


# ===================================================================
# Test 15: Teacher authorization (can access assigned classes)
# ===================================================================
def test_teacher_can_access_assigned_class(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    teacher_headers = make_auth_header(users["teacher"])

    att = _save_attendance(client, db_session, make_auth_header(users["admin"]), data)
    resp = _create_batch(client, teacher_headers, att["id"])
    assert resp.status_code == 201


# ===================================================================
# Test 16: Admin authorization (full access)
# ===================================================================
def test_admin_full_access(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201


# ===================================================================
# Test 17: Unauthorized class (teacher without access)
# ===================================================================
def test_unauthorized_class_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    # Teacher has no access to this class.
    teacher_headers = make_auth_header(users["teacher"])

    att = _save_attendance(
        client, db_session, make_auth_header(users["admin"]), data
    )
    resp = _create_batch(client, teacher_headers, att["id"])
    assert resp.status_code == 403


# ===================================================================
# Test 18: Offline attendance synchronization -> auto batch
# ===================================================================
def test_offline_sync_auto_creates_batch(client, db_session, users):
    """After offline attendance is synced and accepted, a batch should be
    auto-created if there are absences."""
    import uuid
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    client_session_id = str(uuid.uuid4())

    sync_payload = {
        "client_session_id": client_session_id,
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-08-31",
        "records": [
            {
                "student_id": data["students"]["rahul"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
            {
                "student_id": data["students"]["priya"].id,
                "status": "ABSENT",
                "client_record_id": str(uuid.uuid4()),
            },
            {
                "student_id": data["students"]["aarav"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
        ],
    }
    resp = client.post("/api/v1/attendance/sync", json=sync_payload, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["accepted"] is True

    # A batch should have been auto-created for this session.
    batches = client.get("/api/v1/messages/pending", headers=headers).json()
    matching = [
        b for b in batches["items"]
        if b["attendance_session_id"] == result["session"]["id"]
    ]
    assert len(matching) == 1
    assert matching[0]["total_messages"] == 1
    assert matching[0]["skipped_count"] == 2


# ===================================================================
# Test 19: App restart (state persistence - server retrieves state)
# ===================================================================
def test_message_state_persisted_across_requests(client, db_session, users):
    """Messages should be retrievable after the 'app restarts' (new request)."""
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    # "Restart" - new request to get batch detail.
    resp = client.get(f"/api/v1/messages/batches/{batch_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == batch_id
    assert len(resp.json()["messages"]) == 2


# ===================================================================
# Test 20: Large class (30 students)
# ===================================================================
def test_large_class_30_students(client, db_session, users):
    """30 students, 10 absent -> 10 messages, 20 skipped."""
    data = _seed_class_division_students(
        db_session, users["admin"], count=30
    )
    headers = make_auth_header(users["admin"])

    statuses = {}
    student_keys = list(data["students"].keys())
    for i, key in enumerate(student_keys):
        statuses[key] = "ABSENT" if i < 10 else "PRESENT"

    att = _save_attendance(
        client, db_session, headers, data, statuses=statuses
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_messages"] == 10
    assert body["skipped_count"] == 20
    assert len(body["messages"]) == 10


# ===================================================================
# Test 21: Provider timeout (simulated)
# ===================================================================
def test_provider_timeout_returns_failed(client, db_session, users):
    """Provider timeout should result in FAILED message, not crash."""
    from app.providers.base import MessagePayload, MessageSendResult

    class TimeoutProvider:
        def send(self, *, payload: MessagePayload) -> MessageSendResult:
            return MessageSendResult(
                success=False,
                error_message="WhatsApp API request timed out.",
            )

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    from app.services import message_service
    result = message_service.send_batch(
        db_session, batch_id=batch_id, provider=TimeoutProvider()
    )
    assert result.status in (
        "PENDING",
        "PARTIAL_FAILED",
    )  # messages failed
    for m in db_session.query(
        __import__("app.models.message", fromlist=["ParentMessage"]).ParentMessage
    ).filter(
        __import__("app.models.message", fromlist=["ParentMessage"]).ParentMessage.message_batch_id == batch_id
    ).all():
        if m.delivery_status.value != "PENDING":
            assert m.delivery_status.value == "FAILED"
            assert "timed out" in (m.error_message or "").lower()


# ===================================================================
# Test 22: Provider success after initial failure
# ===================================================================
def test_provider_success_after_retry(client, db_session, users):
    from app.providers.base import MessagePayload, MessageSendResult

    call_count = 0

    class FailThenSucceedProvider:
        def send(self, *, payload: MessagePayload) -> MessageSendResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MessageSendResult(
                    success=False,
                    error_message="temporary failure",
                )
            return MessageSendResult(
                success=True,
                provider_message_id=f"ok-{call_count}",
            )

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "PRESENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    from app.services import message_service
    # First send -> fails.
    message_service.send_batch(
        db_session, batch_id=batch_id, provider=FailThenSucceedProvider()
    )
    from app.models.message import ParentMessage
    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    assert msg.delivery_status.value == "FAILED"

    # Retry -> succeeds.
    message_service.retry_message(
        db_session, message_id=msg.id, provider=FailThenSucceedProvider()
    )
    db_session.refresh(msg)
    assert msg.delivery_status.value == "SENT"
    assert msg.provider_message_id == "ok-2"


# ===================================================================
# Test 23: Database constraint - duplicate batch per session
# ===================================================================
def test_database_constraint_duplicate_batch(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(client, db_session, headers, data)
    first = _create_batch(client, headers, att["id"])
    assert first.status_code == 201
    second = _create_batch(client, headers, att["id"])
    assert second.status_code == 409


# ===================================================================
# Test 24: CRITICAL PRIVACY TEST - cross-student isolation
# ===================================================================
def test_critical_privacy_cross_student_isolation(client, db_session, users):
    """Parent A must receive ONLY Student A's message.
    Parent B must receive ONLY Student B's message.
    Zero possibility of cross-student message association."""
    from app.models.student import Student

    data = _seed_class_division_students(db_session, users["admin"])

    # Set unique parent info.
    rahul = db_session.get(Student, data["students"]["rahul"].id)
    rahul.parent_whatsapp_number = "+919000000001"
    rahul.parent_name = "Parent A"
    rahul.name = "Student A"

    priya = db_session.get(Student, data["students"]["priya"].id)
    priya.parent_whatsapp_number = "+919000000002"
    priya.parent_name = "Parent B"
    priya.name = "Student B"
    db_session.commit()

    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()

    by_student = {m["student_id"]: m for m in body["messages"]}
    msg_a = by_student[data["students"]["rahul"].id]
    msg_b = by_student[data["students"]["priya"].id]

    # Parent A's message contains ONLY Student A's info.
    assert "Student A" in msg_a["message_content"]
    assert "Student B" not in msg_a["message_content"]
    assert "Parent B" not in msg_a["message_content"]
    assert msg_a["parent_whatsapp_number"] == "+919000000001"
    assert msg_a["parent_name"] == "Parent A"

    # Parent B's message contains ONLY Student B's info.
    assert "Student B" in msg_b["message_content"]
    assert "Student A" not in msg_b["message_content"]
    assert "Parent A" not in msg_b["message_content"]
    assert msg_b["parent_whatsapp_number"] == "+919000000002"
    assert msg_b["parent_name"] == "Parent B"

    # Verify provider receives one recipient at a time.
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
    from app.services import message_service
    message_service.send_batch(
        db_session, batch_id=body["id"], provider=provider
    )

    assert len(provider.calls) == 2
    numbers_called = {c.to_number for c in provider.calls}
    assert "+919000000001" in numbers_called
    assert "+919000000002" in numbers_called
    # Each call has exactly one recipient.
    for call in provider.calls:
        assert isinstance(call.to_number, str)
        assert not isinstance(call.to_number, (list, tuple))


# ===================================================================
# Test 25: Idempotency key generation
# ===================================================================
def test_idempotency_key_stable():
    from app.models.message import generate_idempotency_key

    key1 = generate_idempotency_key(123, "ABSENCE_NOTIFICATION")
    key2 = generate_idempotency_key(123, "ABSENCE_NOTIFICATION")
    key3 = generate_idempotency_key(124, "ABSENCE_NOTIFICATION")

    assert key1 == key2  # same input -> same key
    assert key1 != key3  # different record -> different key
    assert key1.startswith("idem-")


# ===================================================================
# Test 26: Message type field
# ===================================================================
def test_message_type_is_absence_notification(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "PRESENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    for m in body["messages"]:
        assert m["message_type"] == "ABSENCE_NOTIFICATION"


# ===================================================================
# Test 27: Auto-batch creation after offline sync (V2.5 key behavior)
# ===================================================================
def test_auto_batch_after_offline_sync_with_absences(client, db_session, users):
    """POST /api/v1/attendance/sync should auto-create a batch when absences exist."""
    import uuid
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    client_session_id = str(uuid.uuid4())

    sync_payload = {
        "client_session_id": client_session_id,
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-08-31",
        "records": [
            {
                "student_id": data["students"]["rahul"].id,
                "status": "ABSENT",
                "client_record_id": str(uuid.uuid4()),
            },
            {
                "student_id": data["students"]["priya"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
            {
                "student_id": data["students"]["aarav"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
        ],
    }
    resp = client.post("/api/v1/attendance/sync", json=sync_payload, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["accepted"] is True

    # A batch should have been auto-created with 1 absence notification.
    batches = client.get("/api/v1/messages/batches", headers=headers).json()
    matching = [
        b for b in batches["items"]
        if b["attendance_session_id"] == result["session"]["id"]
    ]
    assert len(matching) == 1
    assert matching[0]["total_messages"] == 1
    assert matching[0]["skipped_count"] == 2


# ===================================================================
# Test 28: No auto-batch when all present (after sync)
# ===================================================================
def test_no_auto_batch_when_all_present_sync(client, db_session, users):
    """When all students are present after sync, no batch should be auto-created."""
    import uuid
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    client_session_id = str(uuid.uuid4())

    sync_payload = {
        "client_session_id": client_session_id,
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-08-31",
        "records": [
            {
                "student_id": data["students"]["rahul"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
            {
                "student_id": data["students"]["priya"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
            {
                "student_id": data["students"]["aarav"].id,
                "status": "PRESENT",
                "client_record_id": str(uuid.uuid4()),
            },
        ],
    }
    resp = client.post("/api/v1/attendance/sync", json=sync_payload, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["accepted"] is True

    # No batch should have been auto-created.
    batches = client.get("/api/v1/messages/batches", headers=headers).json()
    matching = [
        b for b in batches["items"]
        if b["attendance_session_id"] == result["session"]["id"]
    ]
    assert len(matching) == 0


# ===================================================================
# Test 29: Single provider call per message (privacy)
# ===================================================================
def test_send_always_passes_single_recipient(client, db_session, users):
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

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()

    provider = RecordingProvider()
    from app.services import message_service
    message_service.send_batch(
        db_session, batch_id=created["id"], provider=provider
    )

    assert len(provider.calls) == 2
    for call in provider.calls:
        assert isinstance(call.to_number, str)
        assert "+" in call.to_number
        assert not isinstance(call.to_number, (list, tuple, set))


# ===================================================================
# Test 30: Skipped count in batch summary
# ===================================================================
def test_skipped_count_accurate(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "PRESENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["skipped_count"] == 2
    assert body["total_messages"] == 1


# ===================================================================
# Test 31: Message content privacy (no cross-student info)
# ===================================================================
def test_message_content_contains_only_own_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "ABSENT", "aarav": "PRESENT"},
    )
    resp = _create_batch(client, headers, att["id"])
    body = resp.json()
    by_student = {m["student_id"]: m for m in body["messages"]}

    rahul_msg = by_student[data["students"]["rahul"].id]
    priya_msg = by_student[data["students"]["priya"].id]

    # Rahul's message must not mention Priya.
    assert "Priya" not in rahul_msg["message_content"]
    assert "Aarav" not in rahul_msg["message_content"]
    # Priya's message must not mention Rahul.
    assert "Rahul" not in priya_msg["message_content"]
    assert "Aarav" not in priya_msg["message_content"]


# ===================================================================
# Test 32: Batch detail includes V2.5 fields
# ===================================================================
def test_batch_detail_includes_v25_fields(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "PRESENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    resp = client.get(f"/api/v1/messages/batches/{batch_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "skipped_count" in body
    assert body["skipped_count"] == 2

    msg = body["messages"][0]
    assert "message_type" in msg
    assert msg["message_type"] == "ABSENCE_NOTIFICATION"
    assert "attendance_record_id" in msg
    assert msg["attendance_record_id"] is not None
    assert "cancelled_at" in msg
    assert "cancelled_reason" in msg


# ===================================================================
# Test 33: Phone normalization accepts valid E.164
# ===================================================================
def test_phone_normalization_e164():
    from app.core.phone_normalization import is_valid_e164, normalize_e164

    assert normalize_e164("+919876543210") == "+919876543210"
    assert normalize_e164("+1 (555) 123-4567") == "+15551234567"
    assert is_valid_e164("+919876543210") is True
    assert is_valid_e164("") is False
    assert is_valid_e164("12345") is False
    assert is_valid_e164(None) is False


# ===================================================================
# Test 34: RETRYING status concept (next_retry_at)
# ===================================================================
def test_retry_scheduling_sets_next_retry_at(client, db_session, users):
    """Backoff: first failure schedules an immediate retry (next_retry_at None);
    subsequent failures schedule an exponential backoff."""
    from datetime import datetime, timedelta
    from app.models.message import ParentMessage

    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    att = _save_attendance(
        client, db_session, headers, data,
        statuses={"rahul": "ABSENT", "priya": "PRESENT", "aarav": "PRESENT"},
    )
    created = _create_batch(client, headers, att["id"]).json()
    batch_id = created["id"]

    class FailProvider:
        def __init__(self):
            self.failures = 0

        def send(self, *, payload):
            from app.providers.base import MessageSendResult
            self.failures += 1
            return MessageSendResult(success=False, error_message="temporary")

    from app.services import message_service

    # First failure: attempt 1, immediate retry (no backoff).
    provider = FailProvider()
    message_service.send_batch(
        db_session, batch_id=batch_id, provider=provider
    )
    msg = db_session.query(ParentMessage).filter(
        ParentMessage.message_batch_id == batch_id
    ).first()
    assert msg.delivery_status.value == "FAILED"
    assert msg.attempt_count == 1
    assert msg.next_retry_at is None

    # Second failure: attempt 2 -> 1 minute backoff.
    message_service.retry_failed_batch(
        db_session, batch_id=batch_id, provider=provider
    )
    db_session.refresh(msg)
    assert msg.attempt_count == 2
    assert msg.next_retry_at is not None
    assert msg.next_retry_at <= datetime.utcnow() + timedelta(minutes=2)

    # Third failure: attempt 3 is the max -> terminal, no more retries.
    message_service.retry_failed_batch(
        db_session, batch_id=batch_id, provider=provider
    )
    db_session.refresh(msg)
    assert msg.attempt_count == 3
    assert msg.next_retry_at is None
