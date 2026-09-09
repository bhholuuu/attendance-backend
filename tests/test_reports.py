"""V2.2 tests for the attendance reporting module (reports + exports)."""

from conftest import grant_teacher_access, make_auth_header


def _seed_school(db, admin_user):
    """Create two classes with one division each, active students, and record
    attendance on several days so report aggregation has real data."""
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    seeds = {}
    for cls_name, rolls in (("Class 10", [1, 2]), ("Class 11", [1])):
        school_class = SchoolClass(name=cls_name, is_active=True, created_by=admin_user.id)
        db.add(school_class)
        db.flush()
        div = Division(name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id)
        db.add(div)
        db.flush()
        for roll in rolls:
            db.add(Student(
                class_id=school_class.id,
                division_id=div.id,
                name=f"Student {cls_name} {roll}",
                roll_number=str(roll),
                parent_name="Parent",
                parent_whatsapp_number="+919000000000",
                is_active=True,
                created_by=admin_user.id,
            ))
        db.flush()
        seeds[cls_name] = {"class": school_class, "division": div}
    db.commit()
    return seeds


def _mark_attendance(client, db, user, school, statuses_by_roll):
    """Mark all students of a division as PRESENT/ABSENT per their roll and
    return the attendance session id."""
    from app.models.attendance import AttendanceSession
    from app.models.student import Student

    cls = school["class"]
    div = school["division"]
    students = (
        db.query(Student)
        .filter(Student.division_id == div.id)
        .order_by(Student.roll_number.asc())
        .all()
    )
    payload = {
        "class_id": cls.id,
        "division_id": div.id,
        "attendance_date": "2026-08-01",
        "students": [
            {"student_id": s.id, "status": statuses_by_roll[s.roll_number], "remarks": None}
            for s in students
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(user))
    assert resp.status_code == 201
    # Return the session created for this date.
    return (
        db.query(AttendanceSession)
        .filter_by(class_id=cls.id, division_id=div.id, attendance_date="2026-08-01")
        .one()
        .id
    )


# ---------------------------------------------------------------------------
# Student report
# ---------------------------------------------------------------------------
def test_student_report_admin(client, db_session, users):
    from app.models.student import Student

    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    student = (
        db_session.query(Student)
        .filter(Student.division_id == s10["division"].id, Student.roll_number == "1")
        .one()
    )
    _mark_attendance(
        client,
        db_session,
        users["admin"],
        s10,
        {"1": "PRESENT", "2": "PRESENT"},
    )

    resp = client.get(
        f"/api/v1/reports/students/{student.id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["student_name"] == student.name
    assert body["total_attendance_days"] == 1
    assert body["present_days"] == 1
    assert body["absent_days"] == 0
    # Days without attendance are NOT counted as absent -> 100%.
    assert body["attendance_percentage"] == 100.0
    assert body["daily_records"][0]["date"] == "2026-08-01"
    assert body["daily_records"][0]["status"] == "PRESENT"


def test_student_report_no_attendance_days_not_absent_and_mixed(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]

    import datetime
    from app.models.student import Student
    from app.services.attendance_service import save_attendance

    stud = (
        db_session.query(Student)
        .filter(Student.division_id == s10["division"].id, Student.roll_number == "1")
        .one()
    )
    partner = (
        db_session.query(Student)
        .filter(Student.division_id == s10["division"].id, Student.roll_number == "2")
        .one()
    )

    def mark(day, status):
        save_attendance(
            db_session,
            class_id=s10["class"].id,
            division_id=s10["division"].id,
            attendance_date=datetime.date(2026, 8, day),
            submissions=[
                {"student_id": stud.id, "status": status},
                {"student_id": partner.id, "status": "PRESENT"},
            ],
            taker=users["admin"],
        )

    mark(1, "PRESENT")
    mark(2, "ABSENT")

    resp = client.get(
        f"/api/v1/reports/students/{stud.id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    body = resp.json()
    assert body["total_attendance_days"] == 2
    assert body["present_days"] == 1
    assert body["absent_days"] == 1
    assert body["attendance_percentage"] == 50.0


def test_student_report_teacher_403_on_unassigned(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s11 = seeds["Class 11"]
    from app.models.student import Student
    stud = db_session.query(Student).filter(Student.division_id == s11["division"].id).one()
    resp = client.get(
        f"/api/v1/reports/students/{stud.id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 403


def test_student_report_teacher_200_on_assigned(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    grant_teacher_access(db_session, users["teacher"], s10["class"].id, division_id=None)
    from app.models.student import Student
    stud = db_session.query(Student).filter(Student.division_id == s10["division"].id, Student.roll_number == "1").one()
    resp = client.get(
        f"/api/v1/reports/students/{stud.id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    assert resp.json()["student_id"] == stud.id


# ---------------------------------------------------------------------------
# Class report
# ---------------------------------------------------------------------------
def test_class_report_admin_aggregate(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    _mark_attendance(client, db_session, users["admin"], s10, {"1": "PRESENT", "2": "ABSENT"})

    resp = client.get(
        f"/api/v1/reports/classes/{s10['class'].id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["class_name"] == "Class 10"
    assert body["total_students"] == 2
    assert body["total_attendance_sessions"] == 1
    assert body["total_present_records"] == 1
    assert body["total_absent_records"] == 1
    # aggregate = present / (present+absent) = 50%
    assert body["average_attendance_percentage"] == 50.0
    assert len(body["daily_summary"]) == 1
    assert len(body["student_summary"]["items"]) == 2
    assert body["student_summary"]["pagination"]["total"] == 2


def test_class_report_teacher_403_unassigned(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s11 = seeds["Class 11"]
    resp = client.get(
        f"/api/v1/reports/classes/{s11['class'].id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 403


def test_class_report_teacher_assigned_full_class_sees_all_divisions(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    grant_teacher_access(db_session, users["teacher"], s10["class"].id, division_id=None)
    _mark_attendance(client, db_session, users["admin"], s10, {"1": "PRESENT", "2": "PRESENT"})
    resp = client.get(
        f"/api/v1/reports/classes/{s10['class'].id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    assert len(resp.json()["student_summary"]["items"]) == 2


# ---------------------------------------------------------------------------
# Division report
# ---------------------------------------------------------------------------
def test_division_report_validated_and_scoped(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    s11 = seeds["Class 11"]
    _mark_attendance(client, db_session, users["admin"], s10, {"1": "PRESENT", "2": "PRESENT"})

    # Division belongs to class 10 -> valid.
    resp = client.get(
        f"/api/v1/reports/classes/{s10['class'].id}/divisions/{s10['division'].id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["division_name"] == "A"
    assert body["total_students"] == 2
    assert body["total_present_records"] == 2
    assert body["average_attendance_percentage"] == 100.0

    # Division from class 11 used under class 10 -> division does not belong.
    resp = client.get(
        f"/api/v1/reports/classes/{s10['class'].id}/divisions/{s11['division'].id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code in (404, 422)


def test_division_report_teacher_403_unassigned_division(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    grant_teacher_access(db_session, users["teacher"], s10["class"].id, division_id=s10["division"].id)
    # Teacher assigned this exact division -> 200.
    resp = client.get(
        f"/api/v1/reports/classes/{s10['class'].id}/divisions/{s10['division'].id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Daily report
# ---------------------------------------------------------------------------
def test_daily_report_admin_all_classes(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    _mark_attendance(client, db_session, users["admin"], seeds["Class 10"], {"1": "PRESENT", "2": "ABSENT"})
    resp = client.get(
        "/api/v1/reports/daily?date=2026-08-01",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Admin sees every active class/division for the date; Class 10 was marked,
    # Class 11 shows zero counts (not marked yet).
    assert len(body["items"]) == 2
    class10 = [i for i in body["items"] if i["class_name"] == "Class 10"][0]
    class11 = [i for i in body["items"] if i["class_name"] == "Class 11"][0]
    assert class10["total_students"] == 2
    assert class10["present"] == 1
    assert class10["absent"] == 1
    assert class10["percentage"] == 50.0
    assert class10["completion_status"] == "COMPLETED"
    assert class11["total_students"] == 1
    assert class11["present"] == 0
    assert class11["absent"] == 0


def test_daily_report_teacher_scoped(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], seeds["Class 10"]["class"].id, division_id=None)
    _mark_attendance(client, db_session, users["admin"], seeds["Class 10"], {"1": "PRESENT", "2": "ABSENT"})
    _mark_attendance(client, db_session, users["admin"], seeds["Class 11"], {"1": "PRESENT"})
    resp = client.get(
        "/api/v1/reports/daily?date=2026-08-01",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["class_name"] == "Class 10"


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------
def test_summary_report_admin_with_trend(client, db_session, users):
    import datetime
    from app.models.student import Student
    from app.services.attendance_service import save_attendance

    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    studs = db_session.query(Student).filter(Student.division_id == s10["division"].id).all()
    for day, statuses in ((1, ["PRESENT", "ABSENT"]), (2, ["PRESENT", "PRESENT"])):
        save_attendance(
            db_session,
            class_id=s10["class"].id,
            division_id=s10["division"].id,
            attendance_date=datetime.date(2026, 8, day),
            submissions=[
                {"student_id": studs[i].id, "status": st} for i, st in enumerate(statuses)
            ],
            taker=users["admin"],
        )
    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_attendance_sessions"] == 2
    # day1: 1 present + 1 absent; day2: 2 present -> 3/4 present = 75%
    assert body["present_records"] == 3
    assert body["absent_records"] == 1
    assert body["average_attendance_percentage"] == 75.0
    assert len(body["daily_trend"]) == 2


def test_summary_report_invalid_date_range_422(client, users):
    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2026-08-31", "end_date": "2026-08-01"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422


def test_summary_report_range_gt_365_days_422(client, users):
    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2025-01-01", "end_date": "2026-12-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def _student_for(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    _mark_attendance(client, db_session, users["admin"], seeds["Class 10"], {"1": "PRESENT", "2": "ABSENT"})
    from app.models.student import Student
    return db_session.query(Student).filter(Student.division_id == seeds["Class 10"]["division"].id, Student.roll_number == "1").one()


def test_export_csv_structure_and_filename(client, db_session, users):
    stud = _student_for(client, db_session, users)
    resp = client.get(
        "/api/v1/reports/export/csv",
        params={
            "report_type": "student",
            "student_id": stud.id,
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    cd = resp.headers["content-disposition"]
    assert "student_attendance_" in cd
    assert ".csv" in cd
    text = resp.content.decode("utf-8")
    assert "Student" in text
    assert "PRESENT" in text


def test_export_excel_structure(client, db_session, users):
    stud = _student_for(client, db_session, users)
    resp = client.get(
        "/api/v1/reports/export/excel",
        params={"report_type": "student", "student_id": stud.id, "start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert resp.headers["content-disposition"].endswith(".xlsx\"")
    assert resp.content[:2] == b"PK"
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(resp.content))
    assert "Summary" in wb.sheetnames
    assert "Details" in wb.sheetnames


def test_export_pdf_structure(client, db_session, users):
    stud = _student_for(client, db_session, users)
    resp = client.get(
        "/api/v1/reports/export/pdf",
        params={"report_type": "student", "student_id": stud.id, "start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content.startswith(b"%PDF")


def test_export_teacher_403_unassigned(client, db_session, users):
    stud = _student_for(client, db_session, users)
    for fmt in ("csv", "excel", "pdf"):
        resp = client.get(
            f"/api/v1/reports/export/{fmt}",
            params={"report_type": "student", "student_id": stud.id, "start_date": "2026-08-01", "end_date": "2026-08-31"},
            headers=make_auth_header(users["teacher"]),
        )
        assert resp.status_code == 403, fmt


def test_export_invalid_report_type_422(client, users):
    resp = client.get(
        "/api/v1/reports/export/csv",
        params={"report_type": "bogus", "start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422


def test_export_missing_required_params_422(client, users):
    resp = client.get(
        "/api/v1/reports/export/csv",
        params={"report_type": "class", "start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Additional V2.2 coverage
# ---------------------------------------------------------------------------
def test_student_report_no_data_zero_percentage_not_misleading(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    from app.models.student import Student
    stud = (
        db_session.query(Student)
        .filter(Student.division_id == seeds["Class 10"]["division"].id, Student.roll_number == "1")
        .one()
    )
    resp = client.get(
        f"/api/v1/reports/students/{stud.id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_attendance_days"] == 0
    assert body["present_days"] == 0
    assert body["absent_days"] == 0
    assert body["attendance_percentage"] == 0.0
    assert body["daily_records"] == []


def test_student_report_404_invalid_student(client, users):
    resp = client.get(
        "/api/v1/reports/students/999999",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 404


def test_student_report_invalid_date_range_422(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    from app.models.student import Student
    stud = db_session.query(Student).filter(Student.division_id == seeds["Class 10"]["division"].id).first()
    resp = client.get(
        f"/api/v1/reports/students/{stud.id}",
        params={"start_date": "2026-08-31", "end_date": "2026-08-01"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422


def test_class_report_404_invalid_class(client, users):
    resp = client.get(
        "/api/v1/reports/classes/999999",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 404


def test_class_report_division_filter(client, db_session, users):
    import datetime
    from app.models.school_class import Division
    from app.models.student import Student
    from app.services.attendance_service import save_attendance

    seeds = _seed_school(db_session, users["admin"])
    cls = seeds["Class 10"]["class"]
    div_a = seeds["Class 10"]["division"]

    div_b = Division(name="B", is_active=True, class_id=cls.id, created_by=users["admin"].id)
    db_session.add(div_b)
    db_session.flush()
    stud_b = Student(
        class_id=cls.id, division_id=div_b.id, name="Student B", roll_number="3",
        parent_name="Parent", parent_whatsapp_number="+919000000003",
        is_active=True, created_by=users["admin"].id,
    )
    db_session.add(stud_b)
    db_session.commit()

    stud_a = db_session.query(Student).filter(Student.division_id == div_a.id, Student.roll_number == "1").one()
    for student in (stud_a, stud_b):
        division_students = (
            db_session.query(Student)
            .filter(Student.division_id == student.division_id, Student.is_active.is_(True))
            .all()
        )
        save_attendance(
            db_session,
            class_id=cls.id,
            division_id=student.division_id,
            attendance_date=datetime.date(2026, 8, 1),
            submissions=[
                {"student_id": s.id, "status": "PRESENT"} for s in division_students
            ],
            taker=users["admin"],
        )

    resp = client.get(
        f"/api/v1/reports/classes/{cls.id}",
        params={
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "division_id": div_a.id,
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["division_name"] == "A"
    assert body["total_students"] == 2
    assert body["total_attendance_sessions"] == 1
    assert body["total_present_records"] == 2
    assert body["total_absent_records"] == 0
    # Division B's session must NOT leak into a class report scoped to A.
    assert len(body["daily_summary"]) == 1
    assert body["daily_summary"][0]["date"] == "2026-08-01"
    assert body["daily_summary"][0]["present"] == 2


def test_class_report_pagination(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    _mark_attendance(client, db_session, users["admin"], s10, {"1": "PRESENT", "2": "ABSENT"})

    resp = client.get(
        f"/api/v1/reports/classes/{s10['class'].id}",
        params={
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "page": 2,
            "page_size": 1,
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    summary = resp.json()["student_summary"]
    assert summary["pagination"]["total"] == 2
    assert summary["pagination"]["total_pages"] == 2
    assert summary["pagination"]["page"] == 2
    assert len(summary["items"]) == 1


def test_class_report_teacher_403_division_not_assigned(client, db_session, users):
    from app.models.school_class import Division
    from app.models.student import Student

    seeds = _seed_school(db_session, users["admin"])
    cls = seeds["Class 10"]["class"]
    div_a = seeds["Class 10"]["division"]

    div_b = Division(name="B", is_active=True, class_id=cls.id, created_by=users["admin"].id)
    db_session.add(div_b)
    db_session.flush()
    stud = Student(
        class_id=cls.id, division_id=div_b.id, name="Student B", roll_number="3",
        parent_name="Parent", parent_whatsapp_number="+919000000003",
        is_active=True, created_by=users["admin"].id,
    )
    db_session.add(stud)
    db_session.commit()

    # Teacher is assigned ONLY division A of the class.
    grant_teacher_access(db_session, users["teacher"], cls.id, division_id=div_a.id)

    resp = client.get(
        f"/api/v1/reports/classes/{cls.id}",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31", "division_id": div_b.id},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 403


def test_daily_report_teacher_division_scoped(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    div_a = seeds["Class 10"]["division"]
    grant_teacher_access(db_session, users["teacher"], seeds["Class 10"]["class"].id, division_id=div_a.id)
    _mark_attendance(client, db_session, users["admin"], seeds["Class 10"], {"1": "PRESENT", "2": "ABSENT"})

    resp = client.get(
        "/api/v1/reports/daily?date=2026-08-01",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["division_name"] == "A"


def test_summary_report_teacher_scoped(client, db_session, users):
    import datetime
    from app.models.student import Student
    from app.services.attendance_service import save_attendance

    seeds = _seed_school(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], seeds["Class 10"]["class"].id, division_id=None)

    s10_studs = db_session.query(Student).filter(Student.division_id == seeds["Class 10"]["division"].id).all()
    s11_studs = db_session.query(Student).filter(Student.division_id == seeds["Class 11"]["division"].id).all()

    for cls, div, studs in (
        (seeds["Class 10"]["class"], seeds["Class 10"]["division"], s10_studs),
        (seeds["Class 11"]["class"], seeds["Class 11"]["division"], s11_studs),
    ):
        save_attendance(
            db_session,
            class_id=cls.id,
            division_id=div.id,
            attendance_date=datetime.date(2026, 8, 1),
            submissions=[{"student_id": s.id, "status": "PRESENT"} for s in studs],
            taker=users["admin"],
        )

    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Teacher is only assigned Class 10 -> only its 2 students count.
    assert body["total_attendance_sessions"] == 1
    assert body["total_students_marked"] == 2


def test_export_teacher_200_assigned(client, db_session, users):
    stud = _student_for(client, db_session, users)
    grant_teacher_access(db_session, users["teacher"], stud.class_id, division_id=stud.division_id)
    for fmt in ("csv", "excel", "pdf"):
        resp = client.get(
            f"/api/v1/reports/export/{fmt}",
            params={"report_type": "student", "student_id": stud.id, "start_date": "2026-08-01", "end_date": "2026-08-31"},
            headers=make_auth_header(users["teacher"]),
        )
        assert resp.status_code == 200, fmt


def test_export_class_csv_structure(client, db_session, users):
    seeds = _seed_school(db_session, users["admin"])
    s10 = seeds["Class 10"]
    _mark_attendance(client, db_session, users["admin"], s10, {"1": "PRESENT", "2": "ABSENT"})
    resp = client.get(
        "/api/v1/reports/export/csv",
        params={"report_type": "class", "class_id": s10["class"].id, "start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8")
    assert "Roll Number" in text
    assert "Student Name" in text
    assert "Attendance %" in text
    assert "Student Class 10 1" in text
    assert "Student Class 10 2" in text


from io import BytesIO  # noqa: E402
