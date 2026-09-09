from datetime import date

from conftest import grant_teacher_access, make_auth_header


def _seed(db, admin_user):
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    school_class = SchoolClass(name="Class 10", is_active=True, created_by=admin_user.id)
    db.add(school_class)
    db.flush()
    division = Division(name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id)
    db.add(division)
    db.flush()

    def mk(name, roll):
        return Student(
            class_id=school_class.id, division_id=division.id, name=name,
            roll_number=roll, parent_name=f"{name} Parent",
            parent_whatsapp_number="+919000000000", is_active=True,
            created_by=admin_user.id,
        )

    rahul = mk("Rahul Patel", "1")
    priya = mk("Priya Shah", "2")
    aarav = mk("Aarav Mehta", "3")
    db.add_all([rahul, priya, aarav])
    db.commit()
    return {"class": school_class, "division": division,
            "students": {"rahul": rahul, "priya": priya, "aarav": aarav}}


def _seed_year(db, admin_user):
    from app.models.academic_year import AcademicYear

    year = AcademicYear(
        name="2026-2027", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        is_active=True, created_by=admin_user.id,
    )
    db.add(year)
    db.commit()
    db.refresh(year)
    return year


def _approve_leave(db, admin_user, student, start=date(2026, 9, 2), end=date(2026, 9, 2)):
    from app.models.student_leave import LeaveStatus, StudentLeave

    leave = StudentLeave(
        student_id=student.id, start_date=start, end_date=end,
        status=LeaveStatus.APPROVED, reason="Medical", created_by=admin_user.id,
    )
    db.add(leave)
    db.commit()
    return leave


def _seed_holiday(db, admin_user, year, start=date(2026, 9, 3), end=date(2026, 9, 3)):
    from app.models.calendar_event import CalendarEvent, CalendarEventType

    event = CalendarEvent(
        academic_year_id=year.id, title="National Holiday", event_type=CalendarEventType.HOLIDAY,
        holiday_type="PUBLIC_HOLIDAY", start_date=start, end_date=end,
        created_by=admin_user.id,
    )
    db.add(event)
    db.commit()
    return event


def test_dashboard_on_leave_excluded_from_expected(client, db_session, users):
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    _approve_leave(db_session, users["admin"], data["students"]["rahul"])

    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-02",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_holiday"] is False
    assert body["holiday_title"] is None
    assert body["students"]["on_leave"] == 1
    # 3 students, 1 on leave -> 2 expected.
    assert body["attendance"]["total_expected"] == 2
    assert body["attendance"]["pending"] == 2


def test_dashboard_holiday_info(client, db_session, users):
    _seed(db_session, users["admin"])
    year = _seed_year(db_session, users["admin"])
    _seed_holiday(db_session, users["admin"], year)

    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-03",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_holiday"] is True
    assert body["holiday_title"] == "National Holiday"
    assert body["students"]["on_leave"] == 0
    assert body["attendance"]["total_expected"] == 3


def test_dashboard_teacher_scope_with_on_leave(client, db_session, users):
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    _approve_leave(db_session, users["admin"], data["students"]["rahul"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)

    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-02",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["students"]["on_leave"] == 1
    assert body["attendance"]["total_expected"] == 2


def test_message_batch_rejects_holiday_session(client, db_session, users):
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    year = _seed_year(db_session, users["admin"])
    school_class = SchoolClass(name="Class 11", is_active=True, created_by=users["admin"].id)
    db_session.add(school_class)
    db_session.flush()
    division = Division(name="A", is_active=True, class_id=school_class.id, created_by=users["admin"].id)
    db_session.add(division)
    db_session.flush()
    student = Student(
        class_id=school_class.id, division_id=division.id, name="Student One",
        roll_number="1", parent_name="Parent", parent_whatsapp_number="+919000000000",
        is_active=True, created_by=users["admin"].id,
    )
    db_session.add(student)
    db_session.commit()

    headers = make_auth_header(users["admin"])
    resp = client.post(
        "/api/v1/attendance",
        json={
            "class_id": school_class.id,
            "division_id": division.id,
            "attendance_date": "2026-09-05",
            "students": [{"student_id": student.id, "status": "PRESENT", "remarks": None}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    attendance_id = resp.json()["id"]

    # Make 09-05 a holiday AFTER recording.
    _seed_holiday(db_session, users["admin"], year,
                  start=date(2026, 9, 5), end=date(2026, 9, 5))

    batch = client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": attendance_id},
        headers=headers,
    )
    assert batch.status_code == 400
    assert "school holiday" in batch.json()["detail"]


def test_message_batch_skips_on_leave_records(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    rahul, priya, aarav = (
        data["students"]["rahul"],
        data["students"]["priya"],
        data["students"]["aarav"],
    )

    resp = client.post(
        "/api/v1/attendance",
        json={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-09-02",
            "students": [
                {"student_id": rahul.id, "status": "PRESENT", "remarks": None},
                {"student_id": priya.id, "status": "ABSENT", "remarks": None},
                {"student_id": aarav.id, "status": "PRESENT", "remarks": None},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    attendance_id = resp.json()["id"]

    # Approve leave for Rahul (whose ABSENT-ish record is excused).
    _approve_leave(
        db_session, users["admin"], rahul,
        start=date(2026, 9, 2), end=date(2026, 9, 2),
    )

    batch = client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": attendance_id},
        headers=headers,
    )
    assert batch.status_code == 201, batch.text
    body = batch.json()
    message_ids = {m["student_id"] for m in body["messages"]}
    # V2.5: only ABSENT students get absence notifications; Rahul is on leave
    # (skipped), Aarav is PRESENT (no message).
    assert rahul.id not in message_ids
    assert message_ids == {priya.id}
    assert body["skipped_count"] == 2


def test_message_batch_all_on_leave_rejected(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    rahul, priya, aarav = (
        data["students"]["rahul"],
        data["students"]["priya"],
        data["students"]["aarav"],
    )

    resp = client.post(
        "/api/v1/attendance",
        json={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-09-03",
            "students": [
                {"student_id": rahul.id, "status": "PRESENT", "remarks": None},
                {"student_id": priya.id, "status": "PRESENT", "remarks": None},
                {"student_id": aarav.id, "status": "PRESENT", "remarks": None},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    attendance_id = resp.json()["id"]

    for student in (rahul, priya, aarav):
        _approve_leave(
            db_session, users["admin"], student,
            start=date(2026, 9, 3), end=date(2026, 9, 3),
        )

    batch = client.post(
        "/api/v1/messages/batches",
        json={"attendance_session_id": attendance_id},
        headers=headers,
    )
    assert batch.status_code == 400
    assert "on approved leave" in batch.json()["detail"]