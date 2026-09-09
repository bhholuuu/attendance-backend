from conftest import make_auth_header


def _seed_class(db, admin_user, name="Class 10"):
    from app.models.school_class import Division, SchoolClass

    school_class = SchoolClass(name=name, is_active=True, created_by=admin_user.id)
    db.add(school_class)
    db.flush()
    div_a = Division(name="A", is_active=True, class_id=school_class.id, created_by=admin_user.id)
    div_b = Division(name="B", is_active=True, class_id=school_class.id, created_by=admin_user.id)
    db.add_all([div_a, div_b])
    db.flush()
    return {"class": school_class, "div_a": div_a, "div_b": div_b}


def _seed_teacher(db, username="extra_teacher"):
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    t = User(
        full_name="Extra Teacher",
        username=username,
        password_hash=hash_password("password123"),
        role=UserRole.TEACHER,
        is_active=True,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_admin_can_list_teachers(client, db_session, users):
    _seed_teacher(db_session, "another_teacher")
    resp = client.get(
        "/api/v1/teachers", headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 200
    usernames = {t["username"] for t in resp.json()}
    # Both teacher users present; admin not included.
    assert {"teacher", "another_teacher"} <= usernames
    assert "admin" not in usernames


def test_non_admin_cannot_list_teachers(client, users):
    resp = client.get("/api/v1/teachers", headers=make_auth_header(users["teacher"]))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Authorization: admin-only management
# ---------------------------------------------------------------------------
def test_teacher_cannot_manage_assignments(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 403
    resp = client.get(
        "/api/v1/teacher-assignments", headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 403
    resp = client.delete(
        "/api/v1/teacher-assignments/1", headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 403


def test_unauthenticated_cannot_manage_assignments(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Create / list / delete
# ---------------------------------------------------------------------------
def test_admin_creates_and_lists_assignment(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["admin"])

    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["teacher_id"] == users["teacher"].id
    assert body["class_id"] == data["class"].id
    assert body["division_id"] is None

    resp = client.get("/api/v1/teacher-assignments", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    item = resp.json()["items"][0]
    assert item["teacher"]["username"] == users["teacher"].username
    assert item["school_class"]["name"] == "Class 10"


def test_list_assignments_for_a_teacher(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
        headers=headers,
    )
    resp = client.get(
        f"/api/v1/users/{users['teacher'].id}/assignments", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_admin_deletes_assignment(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    created = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
        headers=headers,
    ).json()
    resp = client.delete(
        f"/api/v1/teacher-assignments/{created['id']}", headers=headers
    )
    assert resp.status_code == 200
    resp = client.get("/api/v1/teacher-assignments", headers=headers)
    assert resp.json()["total"] == 0


def test_delete_missing_assignment_404(client, users):
    resp = client.delete(
        "/api/v1/teacher-assignments/9999", headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------
def test_cannot_assign_admin_user(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["admin"].id, "class_id": data["class"].id},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400


def test_cannot_assign_unknown_teacher(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": 9999, "class_id": data["class"].id},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 404


def test_cannot_assign_unknown_class(client, db_session, users):
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": 9999},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 404


def test_division_must_belong_to_class(client, db_session, users):
    class_a = _seed_class(db_session, users["admin"], name="Class A")
    class_b = _seed_class(db_session, users["admin"], name="Class B")
    # Try to assign division B (belongs to class_b) to class A.
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={
            "teacher_id": users["teacher"].id,
            "class_id": class_a["class"].id,
            "division_id": class_b["div_a"].id,
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400


def test_specific_division_assignment(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={
            "teacher_id": users["teacher"].id,
            "class_id": data["class"].id,
            "division_id": data["div_a"].id,
        },
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 201
    assert resp.json()["division_id"] == data["div_a"].id


# ---------------------------------------------------------------------------
# Conflict / precedence rules
# ---------------------------------------------------------------------------
def test_all_then_specific_conflict(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    # All-divisions first.
    assert (
        client.post(
            "/api/v1/teacher-assignments",
            json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
            headers=headers,
        ).status_code
        == 201
    )
    # Adding a specific division is redundant -> 409.
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={
            "teacher_id": users["teacher"].id,
            "class_id": data["class"].id,
            "division_id": data["div_a"].id,
        },
        headers=headers,
    )
    assert resp.status_code == 409


def test_specific_then_all_conflict(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    assert (
        client.post(
            "/api/v1/teacher-assignments",
            json={
                "teacher_id": users["teacher"].id,
                "class_id": data["class"].id,
                "division_id": data["div_a"].id,
            },
            headers=headers,
        ).status_code
        == 201
    )
    # Adding all-divisions conflicts with existing specifics -> 409.
    resp = client.post(
        "/api/v1/teacher-assignments",
        json={"teacher_id": users["teacher"].id, "class_id": data["class"].id},
        headers=headers,
    )
    assert resp.status_code == 409


def test_duplicate_specific_assignment_rejected(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["admin"])
    payload = {
        "teacher_id": users["teacher"].id,
        "class_id": data["class"].id,
        "division_id": data["div_a"].id,
    }
    assert client.post("/api/v1/teacher-assignments", json=payload, headers=headers).status_code == 201
    assert client.post("/api/v1/teacher-assignments", json=payload, headers=headers).status_code == 409


# ---------------------------------------------------------------------------
# Access control enforcement
# ---------------------------------------------------------------------------
def test_unassigned_teacher_denied_class_access(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    headers = make_auth_header(users["teacher"])
    assert client.get(f"/api/v1/classes/{data['class'].id}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/classes/{data['class'].id}/divisions", headers=headers).status_code == 403
    assert client.get(f"/api/v1/divisions/{data['div_a'].id}", headers=headers).status_code == 403
    assert (
        client.get(
            "/api/v1/attendance/prepare",
            params={
                "class_id": data["class"].id,
                "division_id": data["div_a"].id,
                "attendance_date": "2026-09-01",
            },
            headers=headers,
        ).status_code
        == 403
    )


def test_teacher_with_all_divisions_sees_class_and_divisions(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    from conftest import grant_teacher_access

    # All-divisions assignment.
    grant_teacher_access(db_session, users["teacher"], data["class"].id, division_id=None)
    headers = make_auth_header(users["teacher"])

    # Class detail with both divisions.
    resp = client.get(f"/api/v1/classes/{data['class'].id}", headers=headers)
    assert resp.status_code == 200
    assert {d["id"] for d in resp.json()["divisions"]} == {data["div_a"].id, data["div_b"].id}

    # Division list sees both.
    resp = client.get(f"/api/v1/classes/{data['class'].id}/divisions", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # prepare works.
    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["div_a"].id,
            "attendance_date": "2026-09-01",
        },
        headers=headers,
    )
    assert resp.status_code == 200


def test_teacher_with_specific_division_sees_only_it(client, db_session, users):
    data = _seed_class(db_session, users["admin"])
    from conftest import grant_teacher_access

    grant_teacher_access(db_session, users["teacher"], data["class"].id, division_id=data["div_a"].id)
    headers = make_auth_header(users["teacher"])

    resp = client.get(f"/api/v1/classes/{data['class'].id}", headers=headers)
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()["divisions"]] == [data["div_a"].id]

    # The non-assigned division must be denied.
    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["div_b"].id,
            "attendance_date": "2026-09-01",
        },
        headers=headers,
    )
    assert resp.status_code == 403


def test_teacher_denied_other_class_students(client, db_session, users):
    from app.models.student import Student

    data = _seed_class(db_session, users["admin"])
    other = _seed_class(db_session, users["admin"], name="Other Class")
    from conftest import grant_teacher_access

    for c in (data["class"], other["class"]):
        db_session.add(Student(
            class_id=c.id,
            division_id=data["div_a"].id,
            name="Kid",
            roll_number="1",
            parent_name="P",
            parent_whatsapp_number="+919000000001",
            is_active=True,
            created_by=users["admin"].id,
        ))
    db_session.commit()
    # Only assign class "data".
    grant_teacher_access(db_session, users["teacher"], data["class"].id, division_id=None)
    headers = make_auth_header(users["teacher"])

    resp = client.get("/api/v1/students", headers=headers)
    assert resp.status_code == 200
    # Other-class student must not appear.
    students = resp.json()
    assert len(students) == 1
    target = db_session.query(Student).filter(Student.class_id == data["class"].id).first()
    assert students[0]["id"] == target.id


# ---------------------------------------------------------------------------
# GET /teacher-assignments/me (role-aware class scope for the reports UI)
# ---------------------------------------------------------------------------
def test_teacher_sees_own_assignments_me(client, db_session, users):
    from conftest import grant_teacher_access

    data = _seed_class(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id, division_id=None)
    grant_teacher_access(db_session, users["teacher"], data["class"].id, division_id=data["div_a"].id)

    resp = client.get(
        "/api/v1/teacher-assignments/me",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    class_names = {i["school_class"]["name"] for i in body["items"]}
    assert class_names == {"Class 10"}


def test_teacher_me_excludes_other_teachers_assignments(client, db_session, users):
    from conftest import grant_teacher_access

    data = _seed_class(db_session, users["admin"])
    other = _seed_class(db_session, users["admin"], name="Other Class")
    from app.models.user import User, UserRole
    from app.core.security import hash_password

    other_teacher = User(
        full_name="Other Teacher",
        username="otherteacher",
        password_hash=hash_password("password123"),
        role=UserRole.TEACHER,
        is_active=True,
    )
    db_session.add(other_teacher)
    db_session.commit()
    db_session.refresh(other_teacher)
    grant_teacher_access(db_session, users["teacher"], data["class"].id, division_id=None)
    grant_teacher_access(db_session, other_teacher, other["class"].id, division_id=None)

    resp = client.get(
        "/api/v1/teacher-assignments/me",
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["school_class"]["name"] == "Class 10"
    assert body["items"][0]["teacher"]["username"] == "teacher"


def test_admin_me_returns_empty_scope(client, users):
    resp = client.get(
        "/api/v1/teacher-assignments/me", headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
