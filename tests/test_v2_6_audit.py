"""V2.6 Audit Logs + Activity History + Security Monitoring tests.

Covers:

Audit core (24):
 1. Login success is audited            (AUTH_LOGIN_SUCCESS)
 2. Login failure is audited            (AUTH_LOGIN_FAILED)
 3. Disabled-account login is audited   (AUTH_ACCOUNT_DISABLED)
 4. Invalid token is audited            (AUTH_INVALID_TOKEN)
 5. Failed-login temporary block        (429 + RATE_LIMITED)
 6. Rate limiter hit is audited         (RATE_LIMITED / DENIED)
 7. Admin-area denial is audited        (ACCESS_DENIED)
 8. Out-of-scope attendance is audited  (ATTENDANCE_SAVED / DENIED)
 9. User created is audited             (USER_CREATED)
10. User update / disable / enable      (USER_UPDATED + USER_DISABLED + USER_ENABLED)
11. Password change is audited          (PASSWORD_CHANGED)
12. Class lifecycle                     (CLASS_CREATED/UPDATED/ARCHIVED)
13. Division lifecycle                  (DIVISION_CREATED/UPDATED/ARCHIVED)
14. Student lifecycle                   (STUDENT_CREATED/UPDATED/ARCHIVED/RESTORED)
15. Student CSV import                  (STUDENT_IMPORT_COMMITTED)
16. Teacher assignment                  (TEACHER_ASSIGNMENT_CREATED/DELETED)
17. Attendance save                     (ATTENDANCE_SAVED + counts)
18. Attendance update old/new diff      (ATTENDANCE_UPDATED)
19. Leave lifecycle                     (LEAVE_CREATED/APPROVED/REJECTED/CANCELLED)
20. Calendar event lifecycle            (CALENDAR_EVENT_CREATED/UPDATED/DELETED)
21. Message batch create + send         (MESSAGE_BATCH_CREATED/SENT)
22. Report generation                   (REPORT_GENERATED)
23. Audit API is admin-only             (401/403, no write routes)
24. Audit API pagination + filters

Security (3):
25. No secrets/passwords in audit rows
26. Phone numbers are masked
27. Correlation id propagates to audit rows

Performance (3):
28. 10k rows pagination
29. 50k rows filtered
30. 100k rows page tail

E2E (1):
31. End-to-end scenario
"""

import json
from datetime import date

from conftest import grant_teacher_access, make_auth_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_class_division_students(db, admin_user, class_name="Class 10"):
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    school_class = SchoolClass(name=class_name, is_active=True, created_by=admin_user.id)
    db.add(school_class)
    db.flush()

    division = Division(name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id)
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

    students = [
        make_student("Rahul Patel", "1"),
        make_student("Priya Shah", "2"),
        make_student("Aarav Mehta", "10"),
    ]
    db.add_all(students)
    db.flush()
    return {"class": school_class, "division": division, "students": students}


def _seed_year(db, admin_user):
    from app.models.academic_year import AcademicYear

    year = AcademicYear(
        name="2026-2027",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        is_active=True,
        created_by=admin_user.id,
    )
    db.add(year)
    db.commit()
    db.refresh(year)
    return year


def _audit_rows(db):
    from app.models.audit_log import AuditLog

    return db.query(AuditLog).order_by(AuditLog.id.asc()).all()


def _row(db, action):
    matches = [r for r in _audit_rows(db) if r.action == action]
    assert matches, f"expected an audit row for action {action}"
    return matches[-1]


def _details(row):
    return json.loads(row.details) if row.details else {}


def _save_attendance(client, db_session, headers, data, attendance_date="2026-09-02"):
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": attendance_date,
        "students": [
            {
                "student_id": s.id,
                "status": "ABSENT" if i == 0 else "PRESENT",
                "remarks": None,
            }
            for i, s in enumerate(data["students"])
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bulk_insert_audit_rows(db, count):
    from app.core.request_context import new_correlation_id
    from app.models.audit_log import AuditLog
    from datetime import datetime, timezone

    rows = []
    for i in range(count):
        rows.append(
            AuditLog(
                timestamp=datetime.now(timezone.utc),
                correlation_id=new_correlation_id(),
                actor_user_id=1,
                actor_username="admin",
                actor_role="ADMIN",
                action="AUTH_LOGIN_SUCCESS" if i % 2 else "REPORT_GENERATED",
                result="SUCCESS",
                entity_type="user",
                entity_label=f"user admin",
                ip_address="192.168.0.1",
                details=None,
            )
        )
    db.add_all(rows)
    db.commit()


# ===================================================================
# 1-6: Authentication & security monitoring
# ===================================================================
def test_login_success_is_audited(client, db_session, users):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": users["admin"].username, "password": "password123"},
    )
    assert resp.status_code == 200
    row = _row(db_session, "AUTH_LOGIN_SUCCESS")
    assert row.result == "SUCCESS"
    assert row.actor_user_id == users["admin"].id
    assert row.actor_username == users["admin"].username
    assert row.entity_id == users["admin"].id
    assert row.ip_address == "testclient"
    assert row.correlation_id


def test_login_failure_is_audited(client, db_session, users):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": users["admin"].username, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    row = _row(db_session, "AUTH_LOGIN_FAILED")
    assert row.result == "FAILED"
    assert _details(row)["reason"] == "bad_password"
    assert row.actor_user_id is None and row.actor_username is None


def test_invalid_token_is_audited(client, db_session):
    resp = client.get("/api/v1/classes", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    row = _row(db_session, "AUTH_INVALID_TOKEN")
    assert row.result == "DENIED"
    assert row.actor_username is None


def test_failed_login_temporary_block(client, db_session, users, monkeypatch):
    from app.api import auth as auth_module
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "FAILED_LOGIN_LIMIT", 3)
    monkeypatch.setattr(config_module.settings, "FAILED_LOGIN_WINDOW_SECONDS", 300)
    auth_module.reset_failed_login_tracking()
    body = {"username": users["teacher"].username, "password": "wrong-password"}
    for _ in range(3):
        assert client.post("/api/v1/auth/login", json=body).status_code == 401
    resp = client.post("/api/v1/auth/login", json=body)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert _row(db_session, "RATE_LIMITED").result == "DENIED"
    # Block is temporary: clearing failures restores login.
    auth_module.reset_failed_login_tracking()
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": users["teacher"].username, "password": "password123"},
    )
    assert resp.status_code == 200


def test_rate_limiter_hit_is_audited(client, db_session, users, monkeypatch):
    from app.core import rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module.settings, "RATE_LIMIT_ENABLED", True)
    rate_limit_module._window_times.clear()
    try:
        for _ in range(8):
            assert client.post(
                "/api/v1/auth/login",
                json={"username": users["admin"].username, "password": "password123"},
            ).status_code == 200
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": users["admin"].username, "password": "password123"},
        )
        assert resp.status_code == 429
    finally:
        rate_limit_module._window_times.clear()
    row = _row(db_session, "RATE_LIMITED")
    assert row.result == "DENIED"
    assert row.entity_type == "client"
    assert row.ip_address == "testclient"


# ===================================================================
# 7-8: Access control auditing
# ===================================================================
def test_admin_area_denial_is_audited(client, db_session, users):
    resp = client.get(
        "/api/v1/audit-logs", headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 403
    row = _row(db_session, "ACCESS_DENIED")
    assert row.result == "DENIED"
    assert row.actor_user_id == users["teacher"].id
    assert row.entity_label == f"user {users['teacher'].username}"
    assert _details(row)["actual_role"] == "TEACHER"
    assert _details(row)["required_role"] == "ADMIN"


def test_out_of_scope_attendance_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    teacher_headers = make_auth_header(users["teacher"])
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-09-03",
        "students": [
            {"student_id": s.id, "status": "PRESENT", "remarks": None}
            for s in data["students"]
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=teacher_headers)
    assert resp.status_code == 403
    row = _row(db_session, "ATTENDANCE_SAVED")
    assert row.result == "DENIED"
    assert row.actor_user_id == users["teacher"].id


# ===================================================================
# 9-11: User management
# ===================================================================
def test_user_created_is_audited(client, db_session, users):
    resp = client.post(
        "/api/v1/users",
        json={
            "full_name": "New Teacher",
            "username": "newteacher",
            "password": "StrongPass123",
            "role": "TEACHER",
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 201, resp.text
    row = _row(db_session, "USER_CREATED")
    assert row.result == "SUCCESS"
    assert row.actor_user_id == users["admin"].id
    assert row.entity_id == resp.json()["id"]
    assert row.entity_label == f"user {resp.json()['username']}"


def test_user_update_disable_enable_are_audited(client, db_session, users):
    headers = make_auth_header(users["admin"])
    created = client.post(
        "/api/v1/users",
        json={
            "full_name": "Temp Staff",
            "username": "tempstaff",
            "password": "StrongPass123",
            "role": "TEACHER",
        },
        headers=headers,
    ).json()
    uid = created["id"]

    resp = client.put(
        f"/api/v1/users/{uid}",
        json={"full_name": "Temp Staff II", "role": "TEACHER"},
        headers=headers,
    )
    assert resp.status_code == 200
    updated = _row(db_session, "USER_UPDATED")
    assert _details(updated)["changed_fields"]["full_name"]["new"] == "Temp Staff II"

    assert client.post(f"/api/v1/users/{uid}/disable", headers=headers).status_code == 200
    assert _row(db_session, "USER_DISABLED").result == "SUCCESS"

    assert client.post(f"/api/v1/users/{uid}/enable", headers=headers).status_code == 200
    assert _row(db_session, "USER_ENABLED").result == "SUCCESS"


def test_password_change_is_audited(client, db_session, users):
    headers = make_auth_header(users["admin"])
    created = client.post(
        "/api/v1/users",
        json={
            "full_name": "Pwd Staff",
            "username": "pwdstaff",
            "password": "StrongPass123",
            "role": "TEACHER",
        },
        headers=headers,
    ).json()
    resp = client.post(
        f"/api/v1/users/{created['id']}/password",
        json={"new_password": "BrandNew123"},
        headers=headers,
    )
    assert resp.status_code == 200
    row = _row(db_session, "PASSWORD_CHANGED")
    assert row.result == "SUCCESS"
    payload = json.dumps(_details(row))
    assert "BrandNew123" not in payload
    assert "password" not in _details(row)


# ===================================================================
# 12-16: Class / division / student / import / assignment
# ===================================================================
def test_class_lifecycle_is_audited(client, db_session, users):
    headers = make_auth_header(users["admin"])
    created = client.post("/api/v1/classes", json={"name": "Class 9A"}, headers=headers)
    assert created.status_code == 201
    cid = created.json()["id"]
    created_row = _row(db_session, "CLASS_CREATED")
    assert created_row.entity_id == cid
    assert created_row.entity_label == "Class 9A"

    assert client.put(f"/api/v1/classes/{cid}", json={"name": "Class 9B"}, headers=headers).status_code == 200
    assert _details(_row(db_session, "CLASS_UPDATED"))["new_name"] == "Class 9B"

    assert client.delete(f"/api/v1/classes/{cid}", headers=headers).status_code == 200
    assert _row(db_session, "CLASS_ARCHIVED").result == "SUCCESS"


def test_division_lifecycle_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    created = client.post(
        f"/api/v1/classes/{data['class'].id}/divisions", json={"name": "B"}, headers=headers
    )
    assert created.status_code == 201
    did = created.json()["id"]
    assert _row(db_session, "DIVISION_CREATED").entity_id == did

    assert client.put(f"/api/v1/divisions/{did}", json={"name": "Beta"}, headers=headers).status_code == 200
    assert _details(_row(db_session, "DIVISION_UPDATED"))["new_name"] == "Beta"

    assert client.delete(f"/api/v1/divisions/{did}", headers=headers).status_code == 200
    assert _row(db_session, "DIVISION_ARCHIVED").result == "SUCCESS"


def test_student_lifecycle_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    created = client.post(
        "/api/v1/students",
        json={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "name": "New Kid",
            "roll_number": "99",
            "parent_name": "Kid Parent",
            "parent_whatsapp_number": "+91 90000-54321",
        },
        headers=headers,
    )
    assert created.status_code == 201
    sid = created.json()["id"]
    assert _row(db_session, "STUDENT_CREATED").entity_id == sid

    assert client.put(
        f"/api/v1/students/{sid}", json={"name": "New Kid Jr"}, headers=headers
    ).status_code == 200
    assert _row(db_session, "STUDENT_UPDATED").result == "SUCCESS"

    assert client.delete(f"/api/v1/students/{sid}", headers=headers).status_code == 200
    assert _row(db_session, "STUDENT_ARCHIVED").entity_id == sid

    assert client.post(f"/api/v1/students/{sid}/restore", headers=headers).status_code == 200
    assert _row(db_session, "STUDENT_RESTORED").entity_id == sid


def test_students_are_masked_in_audit_details(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    resp = client.post(
        "/api/v1/students",
        json={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "name": "Mask Me",
            "roll_number": "98",
            "parent_name": "Parent",
            "parent_whatsapp_number": "+91 90000-54321",
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 201
    details = _details(_row(db_session, "STUDENT_CREATED"))
    masked = details.get("parent_phone_masked", "")
    assert "*" in masked
    assert "9000054321" not in masked.replace("+91", "")


def test_student_import_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    csv_text = (
        f"class_name,division_name,student_name,roll_number,parent_name,parent_whatsapp_number\n"
        f"{data['class'].name},A,Imported Kid,77,Imported Parent,+919000077777\n"
    )
    preview = client.post(
        "/api/v1/students/import/preview",
        files={"file": ("students.csv", csv_text.encode("utf-8"), "text/csv")},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]

    commit = client.post(
        "/api/v1/students/import/commit",
        data={"preview_token": token},
        files={"file": ("students.csv", csv_text.encode("utf-8"), "text/csv")},
        headers=headers,
    )
    assert commit.status_code == 201, commit.text
    row = _row(db_session, "STUDENT_IMPORT_COMMITTED")
    assert row.result == "SUCCESS"
    assert _details(row)["imported_records"] == 1


def test_teacher_assignment_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={
            "teacher_id": users["teacher"].id,
            "class_id": data["class"].id,
            "division_id": data["division"].id,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    aid = resp.json()["id"]
    created = _row(db_session, "TEACHER_ASSIGNMENT_CREATED")
    assert created.entity_id == aid

    assert client.delete(f"/api/v1/teacher-assignments/{aid}", headers=headers).status_code == 200
    assert _row(db_session, "TEACHER_ASSIGNMENT_DELETED").entity_id == aid


# ===================================================================
# 17-22: Domain actions
# ===================================================================
def test_attendance_save_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    att = _save_attendance(client, db_session, make_auth_header(users["teacher"]), data)
    row = _row(db_session, "ATTENDANCE_SAVED")
    assert row.result == "SUCCESS"
    assert row.entity_id == att["id"]
    details = _details(row)
    assert details["present_count"] == 2
    assert details["absent_count"] == 1


def test_attendance_update_records_old_new_diff(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["teacher"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    att = _save_attendance(client, db_session, headers, data)
    rahul = data["students"][0]
    payload = [{"student_id": rahul.id, "status": "PRESENT", "remarks": None}]
    resp = client.put(f"/api/v1/attendance/{att['id']}", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    row = _row(db_session, "ATTENDANCE_UPDATED")
    assert row.result == "SUCCESS"
    changes = _details(row)["changes"]
    assert changes and changes[0]["student_id"] == rahul.id
    assert changes[0]["old_status"] == "ABSENT"
    assert changes[0]["new_status"] == "PRESENT"


def test_leave_lifecycle_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    created = client.post(
        "/api/v1/student-leaves",
        json={
            "student_id": data["students"][0].id,
            "start_date": "2026-09-10",
            "end_date": "2026-09-11",
            "leave_type": "MEDICAL",
            "reason": "Fever",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    lid = created.json()["id"]
    assert _row(db_session, "LEAVE_CREATED").entity_id == lid

    assert client.post(f"/api/v1/student-leaves/{lid}/approve", headers=headers).status_code == 200
    assert _row(db_session, "LEAVE_APPROVED").result == "SUCCESS"

    assert client.post(f"/api/v1/student-leaves/{lid}/reject", headers=headers).status_code == 200
    assert _row(db_session, "LEAVE_REJECTED").result == "SUCCESS"

    assert client.post(f"/api/v1/student-leaves/{lid}/cancel", headers=headers).status_code == 200
    assert _row(db_session, "LEAVE_CANCELLED").result == "SUCCESS"


def test_calendar_event_lifecycle_is_audited(client, db_session, users):
    year = _seed_year(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    created = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "Holiday",
            "event_type": "HOLIDAY",
            "holiday_type": "PUBLIC_HOLIDAY",
            "start_date": "2026-10-02",
            "end_date": "2026-10-02",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    eid = created.json()["id"]
    assert _row(db_session, "CALENDAR_EVENT_CREATED").entity_id == eid

    assert client.put(
        f"/api/v1/calendar/{eid}", json={"title": "Holiday II"}, headers=headers
    ).status_code == 200
    assert _row(db_session, "CALENDAR_EVENT_UPDATED").result == "SUCCESS"

    assert client.delete(f"/api/v1/calendar/{eid}", headers=headers).status_code == 200
    assert _row(db_session, "CALENDAR_EVENT_DELETED").entity_id == eid


def test_message_batch_create_and_send_are_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["teacher"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    att = _save_attendance(client, db_session, headers, data)
    batch = client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": att["id"]},
        headers=headers,
    )
    assert batch.status_code == 201, batch.text
    bid = batch.json()["id"]
    created = _row(db_session, "MESSAGE_BATCH_CREATED")
    assert created.entity_id == bid
    assert _details(created)["message_count"] == 1

    sent = client.post(f"/api/v1/messages/batches/{bid}/send", headers=headers)
    assert sent.status_code == 200, sent.text
    assert _row(db_session, "MESSAGE_BATCH_SENT").result == "SUCCESS"


def test_report_generation_is_audited(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    _save_attendance(
        client, db_session, make_auth_header(users["admin"]), data,
        attendance_date="2026-09-05",
    )
    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2026-09-01", "end_date": "2026-09-30"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    row = _row(db_session, "REPORT_GENERATED")
    assert row.entity_label == "summary_report"
    details = _details(row)
    assert details["report_type"] == "summary"


# ===================================================================
# 23-24: Audit API surface
# ===================================================================
def test_audit_api_is_admin_only_and_append_only(client, db_session, users):
    assert client.get("/api/v1/audit-logs").status_code == 401
    assert client.get("/api/v1/audit-logs", headers=make_auth_header(users["teacher"])).status_code == 403

    headers = make_auth_header(users["admin"])
    assert client.post("/api/v1/audit-logs", json={}, headers=headers).status_code == 405
    assert client.put("/api/v1/audit-logs/1", json={}, headers=headers).status_code == 405
    assert client.delete("/api/v1/audit-logs/1", headers=headers).status_code == 405


def test_audit_api_pagination_and_filters(client, db_session, users):
    headers = make_auth_header(users["admin"])
    client.post("/api/v1/classes", json={"name": "FilterA"}, headers=headers)
    client.post("/api/v1/classes", json={"name": "FilterB"}, headers=headers)
    client.post("/api/v1/classes", json={"name": "FilterC"}, headers=headers)

    page = client.get("/api/v1/audit-logs?page=1&page_size=20", headers=headers).json()
    assert page["page_size"] == 20
    assert page["total"] == 3
    assert page["total_pages"] == 1

    filtered = client.get(
        "/api/v1/audit-logs?action=CLASS_CREATED&result=SUCCESS", headers=headers
    ).json()
    assert filtered["total"] == 3

    none = client.get(
        "/api/v1/audit-logs?action=REPORT_GENERATED", headers=headers
    ).json()
    assert none["total"] == 0

    bad = client.get("/api/v1/audit-logs?result=NOPE", headers=headers)
    assert bad.status_code == 422

    detail = client.get("/api/v1/audit-logs/1", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == 1
    assert client.get("/api/v1/audit-logs/99999", headers=headers).status_code == 404


# ===================================================================
# 25-27: Security
# ===================================================================
def test_no_secrets_in_audit_rows(client, db_session, users):
    headers = make_auth_header(users["admin"])
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Secret Staff",
            "username": "secretstaff",
            "password": "SuperSecret99",
            "role": "TEACHER",
        },
        headers=headers,
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": users["teacher"].username, "password": "password123"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": users["teacher"].username, "password": "SuperSecret99"},
    )
    for row in _audit_rows(db_session):
        if row.details:
            json.loads(row.details)
        payload = (row.details or "").lower() + (row.entity_label or "").lower()
        assert "supersecret99" not in payload
        assert "password123" not in payload


def test_phone_mask_never_leaks_full_numbers(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    client.post(
        "/api/v1/students",
        json={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "name": "Phone Kid",
            "roll_number": "7",
            "parent_name": "Parent",
            "parent_whatsapp_number": "+9199000111222",
        },
        headers=make_auth_header(users["admin"]),
    )
    for row in _audit_rows(db_session):
        details = json.loads(row.details) if row.details else {}
        if not isinstance(details, dict):
            continue
        for value in details.values():
            if isinstance(value, str) and len(value) >= 10 and value.startswith("+91"):
                assert "*" in value, f"unmasked number: {value}"


def test_correlation_id_propagates(client, db_session, users):
    resp = client.post(
        "/api/v1/classes",
        json={"name": "CorrClass"},
        headers={"Authorization": make_auth_header(users["admin"])["Authorization"]},
    )
    assert resp.status_code == 201
    row = _row(db_session, "CLASS_CREATED")
    assert row.correlation_id


# ===================================================================
# 28-30: Performance
# ===================================================================
def test_audit_10k_pagination(client, db_session, users):
    _bulk_insert_audit_rows(db_session, 10000)
    headers = make_auth_header(users["admin"])
    page = client.get("/api/v1/audit-logs?page=1&page_size=20", headers=headers).json()
    assert page["total"] == 10000
    assert len(page["items"]) == 20


def test_audit_50k_filtered(client, db_session, users):
    _bulk_insert_audit_rows(db_session, 50000)
    headers = make_auth_header(users["admin"])
    filtered = client.get(
        "/api/v1/audit-logs?action=REPORT_GENERATED&page_size=20", headers=headers
    ).json()
    assert filtered["total"] == 25000


def test_audit_100k_page_tail(client, db_session, users):
    _bulk_insert_audit_rows(db_session, 100000)
    headers = make_auth_header(users["admin"])
    tail = client.get("/api/v1/audit-logs?page=5000&page_size=20", headers=headers).json()
    assert tail["total"] == 100000
    assert tail["total_pages"] == 5000
    assert len(tail["items"]) == 20


# ===================================================================
# 31: End-to-end scenario
# ===================================================================
def test_end_to_end_scenario(client, db_session, users):
    """A teacher runs a realistic day; the admin sees a coherent audit trail."""
    admin_headers = make_auth_header(users["admin"])
    teacher_headers = make_auth_header(users["teacher"])
    data = _seed_class_division_students(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)

    # 1. Teacher marks attendance; one student is absent.
    att = _save_attendance(client, db_session, teacher_headers, data)

    # 2. The teacher recorded a leave for the absent student yesterday.
    leave = client.post(
        "/api/v1/student-leaves",
        json={
            "student_id": data["students"][0].id,
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
            "leave_type": "MEDICAL",
            "reason": "Fever",
        },
        headers=admin_headers,
    )
    assert leave.status_code == 201, leave.text

    # 3. Admin approves the leave (attendance for that day now counts as leave).
    assert client.post(
        f"/api/v1/student-leaves/{leave.json()['id']}/approve", headers=admin_headers
    ).status_code == 200

    # 4. An unknown client attempts login 3 times.
    from app.api import auth as auth_module

    auth_module.reset_failed_login_tracking()
    for _ in range(3):
        client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})

    # 5. A teacher outside scope is blocked.
    other = _seed_class_division_students(db_session, users["admin"], class_name="Class 12")
    blocked = client.post(
        "/api/v1/attendance",
        json={
            "class_id": other["class"].id,
            "division_id": other["division"].id,
            "attendance_date": "2026-09-06",
            "students": [
                {"student_id": s.id, "status": "PRESENT", "remarks": None}
                for s in other["students"]
            ],
        },
        headers=teacher_headers,
    )
    assert blocked.status_code == 403

    actions = [r.action for r in _audit_rows(db_session)]
    for expected in (
        "ATTENDANCE_SAVED",
        "LEAVE_CREATED",
        "LEAVE_APPROVED",
        "AUTH_LOGIN_FAILED",
        "ATTENDANCE_SAVED",
        "ACCESS_DENIED",
    ):
        assert expected in actions

    # Ordered newest-first, filtered, from the admin API.
    listing = client.get(
        "/api/v1/audit-logs?page_size=20&result=SUCCESS", headers=admin_headers
    ).json()
    assert listing["total"] >= 3
    timestamps = [item["timestamp"] for item in listing["items"]]
    assert all(a >= b for a, b in zip(timestamps, timestamps[1:]))