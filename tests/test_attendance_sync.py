"""V2.4 offline-attendance sync endpoint tests.

Verifies the server-authoritative, idempotent sync behavior:
  * duplicate replays are acknowledged (never duplicated)
  * server-copy mismatches are CONFLICT (never overwritten)
  * every rule is re-validated server-side (assignment, future date, holiday,
    academic year, approved leave, active students, duplicates, missing rows)
  * invalid client ids are rejected
  * the batch commit records the client ids so later retries are idempotent
"""
import uuid
from datetime import date, timedelta

from conftest import grant_teacher_access, make_auth_header


def _seed_class_division_students(db, admin_user):
    """Create a class, division and three active students directly in the DB."""
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

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

    def make_student(name, roll):
        return Student(
            class_id=school_class.id,
            division_id=division.id,
            name=name,
            roll_number=roll,
            parent_name="Parent",
            parent_whatsapp_number="+919000000000",
            is_active=True,
            created_by=admin_user.id,
        )

    rahul = make_student("Rahul Patel", "1")
    priya = make_student("Priya Shah", "2")
    aarav = make_student("Aarav Mehta", "3")
    db.add_all([rahul, priya, aarav])
    db.flush()
    return {
        "class": school_class,
        "division": division,
        "students": {"rahul": rahul, "priya": priya, "aarav": aarav},
    }


def _sync_payload(class_id, division_id, rows, client_session_id=None):
    return {
        "client_session_id": client_session_id or str(uuid.uuid4()),
        "class_id": class_id,
        "division_id": division_id,
        "attendance_date": str(date.today()),
        "records": rows,
    }


def _record(student_id, status="PRESENT", client_record_id=None, remarks=None):
    data = {
        "client_record_id": client_record_id or str(uuid.uuid4()),
        "student_id": student_id,
        "status": status,
        "remarks": remarks,
    }
    return {k: v for k, v in data.items() if v is not None}


def _full_records(data):
    s = data["students"]
    return [
        _record(s["rahul"].id),
        _record(s["priya"].id, "ABSENT"),
        _record(s["aarav"].id),
    ]


# ---------------------------------------------------------------------------
# Core idempotency & conflict semantics
# ---------------------------------------------------------------------------
def test_sync_creates_session_and_records_once(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )

    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["already_synced"] is False
    assert body["conflict"] is False
    assert body["session"] is not None
    assert len(body["items"]) == 3
    assert all(i["status"] == "ACCEPTED" for i in body["items"])
    assert all(i["record_id"] is not None for i in body["items"])

    # Replay the exact same batch -> idempotent no-op, same session id.
    resp2 = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["accepted"] is True
    assert body2["already_synced"] is True
    assert body2["conflict"] is False
    assert body2["session"]["id"] == body["session"]["id"]
    assert all(i["status"] == "ALREADY_SYNCED" for i in body2["items"])

    # Only one set of records exists on the server.
    from app.models.attendance import StudentAttendance

    count = (
        db_session.query(StudentAttendance)
        .filter(StudentAttendance.attendance_session_id == body["session"]["id"])
        .count()
    )
    assert count == 3


def test_sync_same_client_session_with_different_records_is_conflict(
    client, db_session, users
):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    session_id = str(uuid.uuid4())
    original = _full_records(data)
    # Record the original client ids for the replay-with-changes.
    client_ids = [r["client_record_id"] for r in original]
    payload = _sync_payload(
        data["class"].id, data["division"].id, original, session_id
    )
    r1 = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert r1.json()["accepted"] is True

    # Device edits one record offline, then tries to upload.
    modified = [
        _record(data["students"]["rahul"].id, "PRESENT", client_ids[0]),
        _record(data["students"]["priya"].id, "PRESENT", client_ids[1]),
        _record(data["students"]["aarav"].id, "PRESENT", client_ids[2]),
    ]
    resp = client.post(
        "/api/v1/attendance/sync",
        json=_sync_payload(
            data["class"].id, data["division"].id, modified, session_id
        ),
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is False
    assert body["conflict"] is True
    assert body["session"]["id"] == r1.json()["session"]["id"]
    assert all(i["status"] == "CONFLICT" for i in body["items"])

    # Server copy is untouched (Priya still ABSENT).
    from app.models.attendance import StudentAttendance

    priya = data["students"]["priya"]
    server_record = (
        db_session.query(StudentAttendance)
        .filter(
            StudentAttendance.attendance_session_id == body["session"]["id"],
            StudentAttendance.student_id == priya.id,
        )
        .first()
    )
    assert server_record.attendance_status.value == "ABSENT"


def test_sync_existing_class_division_date_different_client_is_conflict(
    client, db_session, users
):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    r1 = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert r1.json()["accepted"] is True

    # A second device records the same class/division/date -> new client id.
    second = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=second, headers=make_auth_header(users["teacher"])
    )
    body = resp.json()
    assert body["accepted"] is False
    assert body["already_synced"] is False
    assert body["conflict"] is True
    assert body["session"]["id"] == r1.json()["session"]["id"]


# ---------------------------------------------------------------------------
# Authorization (server re-validates the CURRENT assignment)
# ---------------------------------------------------------------------------
def test_sync_teacher_without_assignment_forbidden(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 403


def test_sync_after_assignment_removed_forbidden(client, db_session, users):
    from app.models.teacher_assignment import TeacherAssignment

    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)

    # Accept a session while the teacher is assigned.
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    r1 = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert r1.json()["accepted"] is True

    # Admin removes the assignment; a NEW sync (different client id) is refused.
    db_session.query(TeacherAssignment).delete()
    db_session.commit()

    other_date = str(date.today() - timedelta(days=1))
    payload2 = _sync_payload(
        data["class"].id,
        data["division"].id,
        _full_records(data),
    )
    payload2["attendance_date"] = other_date
    resp = client.post(
        "/api/v1/attendance/sync", json=payload2, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 403


def test_admin_can_sync_without_assignment(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


# ---------------------------------------------------------------------------
# Validation (all re-checked from server state, never the device cache)
# ---------------------------------------------------------------------------
def test_sync_invalid_client_session_id_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    payload["client_session_id"] = "not-a-uuid"
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 422


def test_sync_invalid_client_record_id_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    rows = _full_records(data)
    rows[0]["client_record_id"] = "definitely-not-a-uuid"
    payload = _sync_payload(
        data["class"].id, data["division"].id, rows
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 422


def test_sync_rejects_future_date(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    payload["attendance_date"] = str(date.today() + timedelta(days=1))
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"].lower()


def test_sync_rejects_holiday(client, db_session, users):
    from app.models.academic_year import AcademicYear
    from app.models.calendar_event import CalendarEvent, CalendarEventType

    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)

    year = AcademicYear(
        name="2026-2027",
        start_date=date(2026, 1, 1),
        end_date=date(2027, 12, 31),
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(year)
    db_session.flush()
    db_session.add(
        CalendarEvent(
            academic_year_id=year.id,
            title="Public Holiday",
            event_type=CalendarEventType.HOLIDAY,
            start_date=date.today(),
            end_date=date.today(),
            is_recurring=False,
            created_by=users["admin"].id,
        )
    )
    db_session.commit()

    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "holiday" in resp.json()["detail"].lower()


def test_sync_rejects_duplicate_student_in_request(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    rows = _full_records(data)
    aarav = data["students"]["aarav"]
    rows.append(_record(aarav.id, "ABSENT"))
    payload = _sync_payload(
        data["class"].id, data["division"].id, rows
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "more than once" in resp.json()["detail"]


def test_sync_rejects_duplicate_client_record_id_in_request(
    client, db_session, users
):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    rows = _full_records(data)
    rows[1]["client_record_id"] = rows[0]["client_record_id"]
    payload = _sync_payload(
        data["class"].id, data["division"].id, rows
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "more than once" in resp.json()["detail"]


def test_sync_rejects_on_leave_student(client, db_session, users):
    from app.models.student_leave import LeaveStatus, StudentLeave

    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    rahul = data["students"]["rahul"]
    db_session.add(
        StudentLeave(
            student_id=rahul.id,
            start_date=date.today(),
            end_date=date.today(),
            status=LeaveStatus.APPROVED,
            created_by=users["admin"].id,
        )
    )
    db_session.commit()

    payload = _sync_payload(
        data["class"].id, data["division"].id, _full_records(data)
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "approved leave" in resp.json()["detail"].lower()


def test_sync_rejects_student_not_in_class(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    rows = _full_records(data)
    rows[0]["student_id"] = 999999
    payload = _sync_payload(
        data["class"].id, data["division"].id, rows
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "not an active student" in resp.json()["detail"]


def test_sync_rejects_incomplete_submission(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    # Only TWO of the three active students submitted -> incomplete.
    rows = _full_records(data)[:2]
    payload = _sync_payload(
        data["class"].id, data["division"].id, rows
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 400
    assert "incomplete" in resp.json()["detail"].lower()


def test_sync_records_store_client_ids_on_server(client, db_session, users):
    from app.models.attendance import AttendanceSession

    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    client_session_id = str(uuid.uuid4())
    payload = _sync_payload(
        data["class"].id,
        data["division"].id,
        _full_records(data),
        client_session_id,
    )
    resp = client.post(
        "/api/v1/attendance/sync", json=payload, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 200

    session = db_session.query(AttendanceSession).one()
    assert session.client_session_id == client_session_id
    assert all(
        r.client_record_id is not None for r in session.records
    )
    stored_ids = {r.client_record_id for r in session.records}
    assert stored_ids == {r["client_record_id"] for r in payload["records"]}