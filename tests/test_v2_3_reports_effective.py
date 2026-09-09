from datetime import date

from conftest import make_auth_header


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


def _save(client, headers, data, day, statuses):
    resp = client.post(
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
    assert resp.status_code == 201, resp.text
    return resp.json()


def _approve_leave(db, admin_user, student, start, end):
    from app.models.student_leave import LeaveStatus, StudentLeave

    leave = StudentLeave(
        student_id=student.id, start_date=start, end_date=end,
        status=LeaveStatus.APPROVED, reason="Medical", created_by=admin_user.id,
    )
    db.add(leave)
    db.commit()
    return leave


def _seed_holiday(db, admin_user, year, start, end):
    from app.models.calendar_event import CalendarEvent, CalendarEventType

    event = CalendarEvent(
        academic_year_id=year.id, title="National Holiday", event_type=CalendarEventType.HOLIDAY,
        holiday_type="PUBLIC_HOLIDAY", start_date=start, end_date=end,
        created_by=admin_user.id,
    )
    db.add(event)
    db.commit()
    return event


def _record_absent_then_leave_and_holiday(client, db_session, users, data):
    """Record 2 days, then approve leave (day1) and add a holiday (day2)."""
    rahul, priya, aarav = (
        data["students"]["rahul"],
        data["students"]["priya"],
        data["students"]["aarav"],
    )
    headers = make_auth_header(users["admin"])
    _save(client, headers, data, "2026-09-02", {
        rahul.id: "ABSENT", priya.id: "PRESENT", aarav.id: "PRESENT",
    })
    _save(client, headers, data, "2026-09-03", {
        rahul.id: "PRESENT", priya.id: "ABSENT", aarav.id: "PRESENT",
    })
    year = _seed_year(db_session, users["admin"])
    _approve_leave(
        db_session, users["admin"], rahul,
        date(2026, 9, 2), date(2026, 9, 2),
    )
    _seed_holiday(
        db_session, users["admin"], year,
        date(2026, 9, 3), date(2026, 9, 3),
    )
    return headers


def test_student_report_excludes_leave_and_holiday(client, db_session, users):
    data = _seed(db_session, users["admin"])
    headers = _record_absent_then_leave_and_holiday(client, db_session, users, data)
    rahul = data["students"]["rahul"]

    resp = client.get(
        f"/api/v1/reports/students/{rahul.id}",
        params={
            "start_date": "2026-09-01",
            "end_date": "2026-09-10",
            "include_daily": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()

    # Rahul: ABSENT on 09-02 (leave) and PRESENT on 09-03 (holiday) -> excluded.
    assert body["present_days"] == 0
    assert body["absent_days"] == 0
    assert body["total_attendance_days"] == 0

    daily = {r["date"]: r for r in body["daily_records"]}
    assert daily["2026-09-02"]["status"] == "APPROVED_LEAVE"
    assert daily["2026-09-02"]["recorded_status"] == "ABSENT"
    assert daily["2026-09-03"]["status"] == "HOLIDAY"
    assert daily["2026-09-03"]["recorded_status"] == "PRESENT"

    # Aarav was PRESENT on 09-02; his 09-03 PRESENT is itself holiday-covered.
    aarav = data["students"]["aarav"]
    resp2 = client.get(
        f"/api/v1/reports/students/{aarav.id}",
        params={"start_date": "2026-09-01", "end_date": "2026-09-10"},
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["present_days"] == 1


def test_class_report_excludes_leave_and_holiday_records(client, db_session, users):
    data = _seed(db_session, users["admin"])
    headers = _record_absent_then_leave_and_holiday(client, db_session, users, data)

    resp = client.get(
        f"/api/v1/reports/classes/{data['class'].id}",
        params={"start_date": "2026-09-01",
                "end_date": "2026-09-10", "page_size": 50},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_attendance_sessions"] == 2
    # Day 1: leave removes 1 absent. Day 2: holiday removes 2 present + 1 absent.
    assert body["total_present_records"] == 2
    assert body["total_absent_records"] == 0

    by_roll = {s["roll_number"]: s for s in body["student_summary"]["items"]}
    assert by_roll["1"]["present_days"] == 0
    assert by_roll["1"]["absent_days"] == 0
    assert by_roll["2"]["present_days"] == 1
    assert by_roll["2"]["absent_days"] == 0
    # Aarav: PRESENT on 09-02; 09-03 PRESENT is holiday-covered.
    assert by_roll["3"]["present_days"] == 1


def test_daily_report_holiday_exclusion(client, db_session, users):
    data = _seed(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    rahul, priya = data["students"]["rahul"], data["students"]["priya"]
    _save(client, headers, data, "2026-09-04", {
        rahul.id: "PRESENT", priya.id: "ABSENT",
        data["students"]["aarav"].id: "PRESENT",
    })
    year = _seed_year(db_session, users["admin"])
    _seed_holiday(db_session, users["admin"], year, date(2026, 9, 4), date(2026, 9, 4))

    resp = client.get(
        "/api/v1/reports/daily",
        params={"date": "2026-09-04"},
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["present"] == 0
    assert items[0]["absent"] == 0
    assert items[0]["completion_status"] == "COMPLETED"


def test_summary_report_excludes_leave_and_holiday(client, db_session, users):
    data = _seed(db_session, users["admin"])
    headers = _record_absent_then_leave_and_holiday(client, db_session, users, data)

    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2026-09-01", "end_date": "2026-09-10"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_students_marked"] == 2
    assert body["present_records"] == 2
    assert body["absent_records"] == 0
    trend = {t["date"]: t for t in body["daily_trend"]}
    assert trend["2026-09-02"]["absent"] == 0  # leave-corrected
    assert trend["2026-09-03"]["present"] == 0  # holiday-corrected
    assert trend["2026-09-03"]["absent"] == 0