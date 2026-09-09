from datetime import date

from conftest import grant_teacher_access, make_auth_header


def _seed_school(db, admin_user):
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


def _seed_holiday(db, admin_user, year, start="2026-09-05", end="2026-09-05"):
    from app.models.calendar_event import CalendarEvent, CalendarEventType

    event = CalendarEvent(
        academic_year_id=year.id, title="School Holiday", event_type=CalendarEventType.HOLIDAY,
        holiday_type="SCHOOL_HOLIDAY", start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end), created_by=admin_user.id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _approve_leave(db, admin_user, student, start="2026-09-02", end="2026-09-02"):
    from app.models.student_leave import LeaveStatus, StudentLeave

    leave = StudentLeave(
        student_id=student.id, start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end), status=LeaveStatus.APPROVED, reason="Medical",
        created_by=admin_user.id,
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


def _save(client, headers, data, day, statuses):
    return client.post(
        "/api/v1/attendance",
        json={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": day,
            "students": [
                {"student_id": sid, "status": status, "remarks": None}
                for sid, status in statuses.items()
            ],
        },
        headers=headers,
    )


def test_prepare_flags_on_leave_students(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    rahul = data["students"]["rahul"]
    _approve_leave(db_session, users["admin"], rahul, start="2026-09-02", end="2026-09-04")
    grant_teacher_access(db_session, users["teacher"], data["class"].id)

    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-09-03",
        },
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_holiday"] is False
    assert body["holiday_title"] is None
    by_roll = {s["roll_number"]: s for s in body["students"]}
    assert by_roll["1"]["on_leave"] is True
    assert by_roll["2"]["on_leave"] is False
    assert by_roll["3"]["on_leave"] is False


def test_prepare_reports_holiday_date(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    year = _seed_year(db_session, users["admin"])
    _seed_holiday(db_session, users["admin"], year, start="2026-09-05", end="2026-09-05")

    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-09-05",
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400
    assert "school holiday" in resp.json()["detail"]


def test_save_on_holiday_rejected(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    year = _seed_year(db_session, users["admin"])
    _seed_holiday(db_session, users["admin"], year, start="2026-09-05", end="2026-09-05")
    headers = make_auth_header(users["admin"])

    resp = _save(client, headers, data, "2026-09-05", {
        data["students"]["rahul"].id: "PRESENT",
        data["students"]["priya"].id: "PRESENT",
    })
    assert resp.status_code == 400
    assert "school holiday" in resp.json()["detail"]


def test_save_outside_active_year_rejected(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    resp = _save(client, make_auth_header(users["admin"]), data, "2025-01-01", {
        data["students"]["rahul"].id: "PRESENT",
        data["students"]["priya"].id: "PRESENT",
    })
    assert resp.status_code == 400
    assert "active academic year" in resp.json()["detail"]


def test_save_future_date_rejected(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    resp = _save(client, make_auth_header(users["admin"]), data, "2030-01-01", {
        data["students"]["rahul"].id: "PRESENT",
        data["students"]["priya"].id: "PRESENT",
    })
    assert resp.status_code == 400
    assert "future date" in resp.json()["detail"]


def test_save_rejects_on_leave_submission(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    rahul = data["students"]["rahul"]
    _approve_leave(db_session, users["admin"], rahul)

    resp = _save(client, make_auth_header(users["admin"]), data, "2026-09-02", {
        rahul.id: "ABSENT",
        data["students"]["priya"].id: "PRESENT",
        data["students"]["aarav"].id: "PRESENT",
    })
    assert resp.status_code == 400
    assert "on approved leave" in resp.json()["detail"]
    assert str(rahul.id) in resp.json()["detail"]


def test_save_omits_on_leave_and_passes_completeness_check(client, db_session, users):
    data = _seed_school(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    rahul = data["students"]["rahul"]
    _approve_leave(db_session, users["admin"], rahul)

    resp = _save(client, make_auth_header(users["admin"]), data, "2026-09-02", {
        data["students"]["priya"].id: "PRESENT",
        data["students"]["aarav"].id: "ABSENT",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total_students"] == 3
    assert body["present_count"] == 1
    assert body["absent_count"] == 1
    recorded_ids = {s["student_id"] for s in body["students"]}
    assert rahul.id not in recorded_ids