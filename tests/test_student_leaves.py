from datetime import date

from conftest import grant_teacher_access, make_auth_header


def _seed(db, admin_user):
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    school_class = SchoolClass(name="Class 10", is_active=True, created_by=admin_user.id)
    db.add(school_class)
    db.flush()
    div_a = Division(name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id)
    db.add(div_a)
    db.flush()
    div_b = Division(name="B", is_active=True, class_id=school_class.id, created_by=admin_user.id)
    db.add(div_b)
    db.flush()

    rahul = Student(
        class_id=school_class.id, division_id=div_a.id, name="Rahul Patel",
        roll_number="1", parent_name="P", parent_whatsapp_number="+919000000000",
        is_active=True, created_by=admin_user.id,
    )
    priya = Student(
        class_id=school_class.id, division_id=div_a.id, name="Priya Shah",
        roll_number="2", parent_name="P", parent_whatsapp_number="+919000000000",
        is_active=True, created_by=admin_user.id,
    )
    aarav = Student(
        class_id=school_class.id, division_id=div_b.id, name="Aarav Mehta",
        roll_number="10", parent_name="P", parent_whatsapp_number="+919000000000",
        is_active=True, created_by=admin_user.id,
    )
    db.add_all([rahul, priya, aarav])
    db.commit()
    return {
        "class": school_class,
        "division_a": div_a,
        "division_b": div_b,
        "students": {"rahul": rahul, "priya": priya, "aarav": aarav},
    }


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


def _create_leave(client, headers, student_id, start="2026-09-02", end="2026-09-04"):
    return client.post(
        "/api/v1/student-leaves",
        json={
            "student_id": student_id,
            "start_date": start,
            "end_date": end,
            "leave_type": "MEDICAL",
            "reason": "Fever",
        },
        headers=headers,
    )


def test_leave_create_and_lifecycle(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])

    created = _create_leave(client, headers, data["students"]["rahul"].id)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "PENDING"
    assert body["student_name"] == "Rahul Patel"
    assert body["class_name"] == "Class 10"
    assert body["division_name"] == "A"

    approved = client.post(
        f"/api/v1/student-leaves/{body['id']}/approve", headers=headers
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    listed = client.get("/api/v1/student-leaves", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    rejected = client.post(
        f"/api/v1/student-leaves/{body['id']}/reject", headers=headers
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    cancelled = client.post(
        f"/api/v1/student-leaves/{body['id']}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_leave_overlap_approved_rejected(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    sid = data["students"]["priya"].id

    first = _create_leave(client, headers, sid, start="2026-09-02", end="2026-09-04")
    assert first.status_code == 201
    first_id = first.json()["id"]

    # While PENDING, an overlapping second request is allowed.
    second = _create_leave(client, headers, sid, start="2026-09-03", end="2026-09-05")
    assert second.status_code == 201
    second_id = second.json()["id"]

    # Approving the first is fine (only second is PENDING).
    assert (
        client.post(f"/api/v1/student-leaves/{first_id}/approve", headers=headers).status_code
        == 200
    )
    # Approving the overlapping second must now be rejected.
    blocked = client.post(
        f"/api/v1/student-leaves/{second_id}/approve", headers=headers
    )
    assert blocked.status_code == 400
    assert "overlap" in blocked.json()["detail"].lower()


def test_leave_cancelled_cannot_be_approved(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    sid = data["students"]["rahul"].id

    created = _create_leave(client, headers, sid).json()
    assert (
        client.post(
            f"/api/v1/student-leaves/{created['id']}/cancel", headers=headers
        ).status_code
        == 200
    )
    blocked = client.post(
        f"/api/v1/student-leaves/{created['id']}/approve", headers=headers
    )
    assert blocked.status_code == 400


def test_leave_outside_active_year_rejected_when_year_exists(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    resp = _create_leave(
        client, headers, data["students"]["rahul"].id, start="2025-06-01", end="2025-06-02"
    )
    assert resp.status_code == 400
    assert "academic year" in resp.json()["detail"].lower()


def test_leave_outside_active_year_allowed_legacy_mode(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    resp = _create_leave(
        client, headers, data["students"]["rahul"].id, start="2025-06-01", end="2025-06-02"
    )
    assert resp.status_code == 201


def test_leave_archived_student_rejected(client, db_session, users):
    headers = make_auth_header(users["admin"])
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    data["students"]["aarav"].is_active = False
    db_session.commit()

    resp = _create_leave(client, headers, data["students"]["aarav"].id)
    assert resp.status_code == 400
    assert "archived" in resp.json()["detail"].lower()


def test_leave_teacher_scoping(client, db_session, users):
    data = _seed(db_session, users["admin"])
    _seed_year(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])

    rahul_leave = _create_leave(client, admin_headers, data["students"]["rahul"].id).json()
    aarav_leave = _create_leave(client, admin_headers, data["students"]["aarav"].id).json()

    # Teacher is assigned only to division A.
    grant_teacher_access(
        db_session, users["teacher"], data["class"].id, data["division_a"].id
    )
    teacher_headers = make_auth_header(users["teacher"])

    listed = client.get("/api/v1/student-leaves", headers=teacher_headers)
    assert listed.status_code == 200
    ids = [i["id"] for i in listed.json()["items"]]
    assert rahul_leave["id"] in ids
    assert aarav_leave["id"] not in ids

    visible = client.get(
        f"/api/v1/student-leaves/{rahul_leave['id']}", headers=teacher_headers
    )
    assert visible.status_code == 200

    hidden = client.get(
        f"/api/v1/student-leaves/{aarav_leave['id']}", headers=teacher_headers
    )
    assert hidden.status_code == 403

    # A teacher may never create a leave.
    resp = client.post(
        "/api/v1/student-leaves",
        json={
            "student_id": data["students"]["rahul"].id,
            "start_date": "2026-09-10",
            "end_date": "2026-09-11",
        },
        headers=teacher_headers,
    )
    assert resp.status_code == 403