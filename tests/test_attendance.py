from conftest import grant_teacher_access, make_auth_header


def _seed_class_division_students(db, admin_user):
    """Create a class, a division and active students directly in the DB."""
    from app.models.attendance import AttendanceStatus, SessionStatus
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    school_class = SchoolClass(name="Class 10", is_active=True, created_by=admin_user.id)
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

    rahul = make_student("Rahul Patel", "1")
    priya = make_student("Priya Shah", "2")
    aarav = make_student("Aarav Mehta", "10")
    db.add_all([rahul, priya, aarav])
    db.flush()

    return {
        "class": school_class,
        "division": division,
        "students": {"rahul": rahul, "priya": priya, "aarav": aarav},
        "SessionStatus": SessionStatus,
        "AttendanceStatus": AttendanceStatus,
    }


def _binary_save_payload(class_id, division_id, rows):
    """Build a full save payload from list of (name, status, remarks)."""
    return {
        "class_id": class_id,
        "division_id": division_id,
        "attendance_date": "2026-08-31",
        "students": rows,
    }


# ---------------------------------------------------------------------------
# Prepare
# ---------------------------------------------------------------------------
def test_prepare_returns_active_students_defaulted_present(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    headers = make_auth_header(users["teacher"])

    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-08-31",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["existing_session"] is False
    assert body["class"] == {"id": data["class"].id, "name": "Class 10"}
    assert body["division"] == {"id": data["division"].id, "name": "A"}
    assert body["attendance_date"] == "2026-08-31"
    assert len(body["students"]) == 3
    # Sorted by roll: 1, 2, 10 -> Rahul, Priya, Aarav; all defaulted PRESENT.
    assert [s["roll_number"] for s in body["students"]] == ["1", "2", "10"]
    assert all(s["status"] == "PRESENT" for s in body["students"])
    # Content of one attendee.
    first = body["students"][0]
    assert first["name"] == "Rahul Patel"
    assert set(first.keys()) == {
            "student_id",
            "roll_number",
            "name",
            "status",
            "on_leave",
        }


def test_prepare_excludes_archived_students(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    # Archive one student.
    data["students"]["aarav"].is_active = False
    db_session.commit()

    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-08-31",
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 200
    assert len(resp.json()["students"]) == 2


def test_prepare_returns_existing_session_with_recorded_status(
    client, db_session, users
):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    # Save attendance with one absence.
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "ABSENT", "remarks": "Sick"},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    created = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert created.status_code == 201
    session_id = created.json()["id"]

    # Preparing the same class/division/date returns the existing session.
    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-08-31",
        },
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["existing_session"] is True
    assert body["session_id"] == session_id
    assert body["status"] == "COMPLETED"
    statues = {s["student_id"]: s["status"] for s in body["students"]}
    assert statues[data["students"]["priya"].id] == "ABSENT"
    assert statues[data["students"]["rahul"].id] == "PRESENT"


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
def test_save_attendance_creates_completed_session(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "ABSENT", "remarks": "Sick"},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["teacher"]))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["total_students"] == 3
    assert body["present_count"] == 2
    assert body["absent_count"] == 1
    assert body["taken_by"] == "Teacher Name"
    assert body["class"]["name"] == "Class 10"
    assert body["division"]["name"] == "A"
    assert body["attendance_date"] == "2026-08-31"
    assert len(body["students"]) == 3
    absents = [s for s in body["students"] if s["attendance_status"] == "ABSENT"]
    assert len(absents) == 1
    assert absents[0]["remarks"] == "Sick"


def test_save_rejects_duplicate_session(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    first = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert first.status_code == 201
    second = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert second.status_code == 409


def test_save_rejects_missing_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            # aarav is missing
        ],
    )
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert resp.status_code == 400
    assert "incomplete" in resp.json()["detail"].lower()


def test_save_rejects_duplicate_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert resp.status_code == 400


def test_save_rejects_invalid_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
            {"student_id": 99999, "status": "PRESENT", "remarks": None},
        ],
    )
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert resp.status_code == 400
    assert "active student" in resp.json()["detail"]


def test_save_rejects_archived_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    data["students"]["aarav"].is_active = False
    db_session.commit()
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    resp = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    assert resp.status_code == 400


def test_save_requires_authentication(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    resp = client.post("/api/v1/attendance", json=payload)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Get + List (history)
# ---------------------------------------------------------------------------
def test_get_session_detail(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "ABSENT", "remarks": "Sick"},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    created = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    session_id = created.json()["id"]

    resp = client.get(f"/api/v1/attendance/{session_id}", headers=make_auth_header(users["teacher"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id
    assert body["taken_by"] == "Admin User"
    assert body["present_count"] == 2
    assert body["absent_count"] == 1
    assert len(body["students"]) == 3


def test_get_session_not_found(client, db_session, users):
    resp = client.get("/api/v1/attendance/9999", headers=make_auth_header(users["admin"]))
    assert resp.status_code == 404


def test_list_history_with_date_range(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    headers = make_auth_header(users["admin"])

    def save(date_str, absent=None):
        rows = [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ]
        if absent is not None:
            rows[absent]["status"] = "ABSENT"
        payload = {
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": date_str,
            "students": rows,
        }
        return client.post("/api/v1/attendance", json=payload, headers=headers)

    save("2026-08-10")
    save("2026-08-20", absent=1)
    save("2026-08-31")

    # All sessions, newest first.
    resp = client.get(
        "/api/v1/attendance",
        params={"class_id": data["class"].id, "division_id": data["division"].id},
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["attendance_date"] for i in items] == ["2026-08-31", "2026-08-20", "2026-08-10"]
    assert items[1]["absent_count"] == 1
    assert items[1]["class_name"] == "Class 10"
    assert items[1]["division_name"] == "A"
    assert items[1]["taken_by"] == "Admin User"
    assert items[1]["status"] == "COMPLETED"

    # Date-range filter.
    resp = client.get(
        "/api/v1/attendance",
        params={"start_date": "2026-08-15", "end_date": "2026-08-25"},
        headers=headers,
    )
    assert [i["attendance_date"] for i in resp.json()["items"]] == ["2026-08-20"]

    # Specific-date filter.
    resp = client.get(
        "/api/v1/attendance",
        params={"attendance_date": "2026-08-10"},
        headers=headers,
    )
    assert len(resp.json()["items"]) == 1


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
def test_update_attendance_recalculates_and_preserves_identity(
    client, db_session, users
):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    created = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    session_id = created.json()["id"]
    original = created.json()
    assert original["present_count"] == 3

    # Change two students to absent.
    updates = [
        {"student_id": data["students"]["priya"].id, "status": "ABSENT", "remarks": "Sick"},
        {"student_id": data["students"]["aarav"].id, "status": "ABSENT", "remarks": "Travel"},
    ]
    resp = client.put(f"/api/v1/attendance/{session_id}", json=updates, headers=make_auth_header(users["teacher"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id
    assert body["present_count"] == 1
    assert body["absent_count"] == 2
    assert body["total_students"] == 3
    # Identity unchanged.
    assert body["class"]["name"] == original["class"]["name"]
    assert body["division"]["name"] == original["division"]["name"]
    assert body["attendance_date"] == original["attendance_date"]
    # updated_at should have changed.
    assert body["updated_at"] >= original["updated_at"]


def test_update_rejects_student_not_in_session(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = _binary_save_payload(
        data["class"].id,
        data["division"].id,
        [
            {"student_id": data["students"]["rahul"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["priya"].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"]["aarav"].id, "status": "PRESENT", "remarks": None},
        ],
    )
    created = client.post("/api/v1/attendance", json=payload, headers=make_auth_header(users["admin"]))
    session_id = created.json()["id"]

    resp = client.put(
        f"/api/v1/attendance/{session_id}",
        json=[{"student_id": 99999, "status": "ABSENT", "remarks": None}],
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400


def test_update_not_found(client, db_session, users):
    resp = client.put(
        "/api/v1/attendance/9999",
        json=[{"student_id": 1, "status": "PRESENT", "remarks": None}],
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validation of class/division relations
# ---------------------------------------------------------------------------
def test_prepare_rejects_division_not_in_class(client, db_session, users):
    from app.models.school_class import Division, SchoolClass

    admin = users["admin"]
    c1 = SchoolClass(name="Class 10", is_active=True, created_by=admin.id)
    c2 = SchoolClass(name="Class 9", is_active=True, created_by=admin.id)
    db_session.add_all([c1, c2])
    db_session.flush()
    d1 = Division(name="A", is_active=True, class_id=c1.id, created_by=admin.id)
    db_session.add(d1)
    db_session.commit()

    resp = client.get(
        "/api/v1/attendance/prepare",
        params={"class_id": c2.id, "division_id": d1.id, "attendance_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400
    assert "does not belong" in resp.json()["detail"]


def test_prepare_rejects_archived_class(client, db_session, users):
    from app.models.school_class import Division, SchoolClass

    admin = users["admin"]
    c1 = SchoolClass(name="Class 10", is_active=False, created_by=admin.id)
    db_session.add(c1)
    db_session.flush()
    d1 = Division(name="A", is_active=True, class_id=c1.id, created_by=admin.id)
    db_session.add(d1)
    db_session.commit()

    resp = client.get(
        "/api/v1/attendance/prepare",
        params={"class_id": c1.id, "division_id": d1.id, "attendance_date": "2026-08-31"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400
    assert "archived class" in resp.json()["detail"]
