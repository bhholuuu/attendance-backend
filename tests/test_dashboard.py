from conftest import grant_teacher_access, make_auth_header


def _seed_school(db, admin_user):
    """Create two classes with one division each and active students."""
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    seeds = {}
    for cls_name, rolls in (("Class 10", [1, 2, 3]), ("Class 11", [1, 2])):
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


def test_dashboard_admin_sees_whole_school(client, db_session, users):
    """Admin summary aggregates all classes; endpoint returns 200 with role=ADMIN."""
    from app.models.student import Student

    seeds = _seed_school(db_session, users["admin"])
    # Directly create a completed attendance session for Class 10 via the API to
    # seed real counts, then assert admin summary counts.
    s10 = seeds["Class 10"]
    students = (
        db_session.query(Student)
        .filter(Student.division_id == s10["division"].id)
        .order_by(Student.roll_number.asc())
        .all()
    )
    payload = {
        "class_id": s10["class"].id,
        "division_id": s10["division"].id,
        "attendance_date": "2026-09-01",
        "students": [
            {"student_id": s.id, "status": "PRESENT", "remarks": None}
            for s in students
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert resp.status_code == 201

    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-01",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "ADMIN"
    assert body["date"] == "2026-09-01"
    # Expected = all active students (Class 10:3 + Class 11:2 = 5).
    assert body["attendance"]["total_expected"] == 5
    # Only Class 10 was marked (3 present).
    assert body["attendance"]["completed"] == 3
    assert body["attendance"]["pending"] == 2
    assert body["students"]["present"] == 3
    assert body["students"]["absent"] == 0
    assert body["messaging"]["pending_batches"] == 0


def test_dashboard_defaults_to_today(client, db_session, users):
    from datetime import date

    _seed_school(db_session, users["admin"])
    resp = client.get(
        "/api/v1/dashboard/attendance-summary",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    assert resp.json()["date"] == date.today().isoformat()


def test_dashboard_invalid_date_rejected(client, users):
    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=not-a-date",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422


def test_dashboard_no_divide_by_zero_when_nothing_marked(client, db_session, users):
    _seed_school(db_session, users["admin"])
    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-01",
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["attendance"]["completed"] == 0
    assert body["attendance"]["pending"] == body["attendance"]["total_expected"]


def test_dashboard_teacher_scoped_to_assigned_class(client, db_session, users):
    from app.models.student import Student

    seeds = _seed_school(db_session, users["admin"])
    # Assign teacher only to Class 10.
    grant_teacher_access(db_session, users["teacher"], seeds["Class 10"]["class"].id, division_id=None)

    s10 = seeds["Class 10"]
    students = (
        db_session.query(Student)
        .filter(Student.division_id == s10["division"].id)
        .all()
    )
    payload = {
        "class_id": s10["class"].id,
        "division_id": s10["division"].id,
        "attendance_date": "2026-09-01",
        "students": [
            {"student_id": s.id, "status": "PRESENT", "remarks": None} for s in students
        ],
    }
    client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))

    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-01",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Scoped to Class 10 only: 3 expected, 3 marked.
    assert body["attendance"]["total_expected"] == 3
    assert body["attendance"]["completed"] == 3
    assert body["attendance"]["pending"] == 0
    assert body["students"]["present"] == 3


def test_dashboard_teacher_no_assignments_returns_zeros(client, db_session, users):
    _seed_school(db_session, users["admin"])
    resp = client.get(
        "/api/v1/dashboard/attendance-summary?date=2026-09-01",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["attendance"]["total_expected"] == 0
    assert body["attendance"]["completed"] == 0
    assert body["attendance"]["pending"] == 0
    assert body["students"]["present"] == 0
    assert body["students"]["absent"] == 0
