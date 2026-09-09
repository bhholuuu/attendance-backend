from datetime import timedelta

import pytest

from conftest import grant_teacher_access, make_auth_header

from app.core.jwt import create_access_token
from app.core.security import hash_password
from app.models.user import User, UserRole


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


def _save_attendance(client, db_session, headers, data, attendance_date="2026-08-31"):
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": attendance_date,
        "students": [
            # V2.5: mark the first student ABSENT so a message batch is valid.
            {
                "student_id": s.id,
                "status": "ABSENT" if i == 0 else "PRESENT",
                "remarks": None,
            }
            for i, s in enumerate(data["students"])
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# PART 3-4: Authentication error paths
# ---------------------------------------------------------------------------
def test_login_rejects_invalid_password(client, users):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": users["admin"].username, "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_rejects_unknown_user(client, users):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "ghost", "password": "password123"},
    )
    assert resp.status_code == 401


def test_login_rejects_inactive_user(client, db_session):
    inactive = User(
        full_name="Fired Teacher",
        username="fired_teacher",
        password_hash=hash_password("password123"),
        role=UserRole.TEACHER,
        is_active=False,
    )
    db_session.add(inactive)
    db_session.commit()

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "fired_teacher", "password": "password123"},
    )
    assert resp.status_code == 403


def test_expired_access_token_is_rejected(client, users):
    expired = create_access_token(
        subject=users["admin"].username,
        role=users["admin"].role.value,
        expires_delta=timedelta(minutes=-5),
    )
    resp = client.get("/api/v1/students", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_garbage_access_token_is_rejected(client):
    resp = client.get("/api/v1/students", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_missing_bearer_scheme_is_rejected(client):
    resp = client.get("/api/v1/students", headers={"Authorization": "Basic abc"})
    assert resp.status_code == 401


def test_auth_me_returns_current_user(client, users):
    resp = client.get("/api/v1/auth/me", headers=make_auth_header(users["teacher"]))
    assert resp.status_code == 200
    assert resp.json()["username"] == users["teacher"].username


def test_login_rate_limit_returns_429(client, users, monkeypatch):
    from app.core import rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module.settings, "RATE_LIMIT_ENABLED", True)
    rate_limit_module._window_times.clear()
    try:
        for _ in range(8):
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": users["admin"].username, "password": "password123"},
            )
            assert resp.status_code == 200
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": users["admin"].username, "password": "password123"},
        )
        assert resp.status_code == 429
        assert resp.headers.get("retry-after") is not None
    finally:
        rate_limit_module._window_times.clear()


# ---------------------------------------------------------------------------
# PART 3: Rate limiter Retry-After header correctness
# ---------------------------------------------------------------------------
def test_rate_limiter_retry_after_uses_matching_window(users, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core import rate_limit as rate_limit_module
    from app.database.connection import Base
    from app.services import audit_service

    app = FastAPI()

    @app.post("/api/v1/messages/batches/{batch_id}/send")
    async def send():
        return {"ok": True}

    app.add_middleware(rate_limit_module.RateLimitMiddleware, retry_suffix="/send")
    monkeypatch.setattr(rate_limit_module.settings, "RETRY_RATE_LIMIT_LIMIT", 2)
    monkeypatch.setattr(rate_limit_module.settings, "RETRY_RATE_LIMIT_WINDOW_SECONDS", 30)
    monkeypatch.setattr(rate_limit_module.settings, "RATE_LIMIT_ENABLED", True)
    rate_limit_module._window_times.clear()
    # V2.6: the middleware audits the RATE_LIMITED event via the session
    # factory; point it at a throwaway DB so no event touches the dev DB.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    throwaway = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    audit_service.set_session_factory(lambda: throwaway)
    try:
        test_client = TestClient(app)
        for _ in range(2):
            assert test_client.post("/api/v1/messages/batches/1/send").status_code == 200
        resp = test_client.post("/api/v1/messages/batches/1/send")
        assert resp.status_code == 429
        assert resp.headers.get("retry-after") == "30"
    finally:
        monkeypatch.setattr(rate_limit_module.settings, "RATE_LIMIT_ENABLED", False)
        monkeypatch.setattr(rate_limit_module.settings, "RETRY_RATE_LIMIT_LIMIT", 20)
        monkeypatch.setattr(rate_limit_module.settings, "RETRY_RATE_LIMIT_WINDOW_SECONDS", 60)
        rate_limit_module._window_times.clear()
        audit_service.reset_session_factory()


# ---------------------------------------------------------------------------
# PART 5: Authorization matrix
# ---------------------------------------------------------------------------
def test_teacher_cannot_create_class(client, users):
    resp = client.post(
        "/api/v1/classes", json={"name": "Chemistry"}, headers=make_auth_header(users["teacher"])
    )
    assert resp.status_code == 403


def test_teacher_cannot_update_or_archive_class(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    teacher_headers = make_auth_header(users["teacher"])
    assert client.put(f"/api/v1/classes/{data['class'].id}", json={"name": "X"},
                      headers=teacher_headers).status_code == 403
    assert client.delete(f"/api/v1/classes/{data['class'].id}", headers=teacher_headers).status_code == 403


def test_teacher_cannot_manage_divisions(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    teacher_headers = make_auth_header(users["teacher"])
    assert client.post(f"/api/v1/classes/{data['class'].id}/divisions", json={"name": "B"},
                       headers=teacher_headers).status_code == 403
    assert client.put(f"/api/v1/divisions/{data['division'].id}", json={"name": "C"},
                      headers=teacher_headers).status_code == 403
    assert client.delete(f"/api/v1/divisions/{data['division'].id}",
                         headers=teacher_headers).status_code == 403


def test_teacher_cannot_create_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "name": "New Kid",
        "roll_number": "99",
        "parent_name": "Kid Parent",
        "parent_whatsapp_number": "+919111111111",
    }
    resp = client.post("/api/v1/students", json=payload,
                       headers=make_auth_header(users["teacher"]))
    assert resp.status_code == 403


def test_teacher_cannot_update_archive_or_restore_student(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    student = data["students"][0]
    teacher_headers = make_auth_header(users["teacher"])
    assert client.put(f"/api/v1/students/{student.id}", json={"name": "Renamed"},
                      headers=teacher_headers).status_code == 403
    assert client.delete(f"/api/v1/students/{student.id}", headers=teacher_headers).status_code == 403
    assert client.post(f"/api/v1/students/{student.id}/restore",
                       headers=teacher_headers).status_code == 403


def test_teacher_can_read_classes_and_students(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    teacher_headers = make_auth_header(users["teacher"])
    assert client.get("/api/v1/classes", headers=teacher_headers).status_code == 200
    assert client.get(f"/api/v1/classes/{data['class'].id}", headers=teacher_headers).status_code == 200
    assert client.get(f"/api/v1/classes/{data['class'].id}/divisions",
                      headers=teacher_headers).status_code == 200
    assert client.get(f"/api/v1/divisions/{data['division'].id}",
                      headers=teacher_headers).status_code == 200
    assert client.get("/api/v1/students", headers=teacher_headers).status_code == 200
    assert client.get(f"/api/v1/students/{data['students'][0].id}",
                      headers=teacher_headers).status_code == 200


def test_teacher_can_take_attendance_and_manage_messages(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    teacher_headers = make_auth_header(users["teacher"])
    att = _save_attendance(client, db_session, teacher_headers, data)
    resp = client.post("/api/v1/messages/batches",
                       json={"attendance_session_id": att["id"]}, headers=teacher_headers)
    assert resp.status_code == 201
    batch_id = resp.json()["id"]
    resp = client.post(f"/api/v1/messages/batches/{batch_id}/send", headers=teacher_headers)
    assert resp.status_code == 200


def test_unauthenticated_mutations_are_rejected(client):
    assert client.post("/api/v1/classes", json={"name": "Math"}).status_code == 401
    assert client.delete("/api/v1/classes/1").status_code == 401
    assert client.post("/api/v1/students", json={}).status_code == 401
    assert client.post("/api/v1/students/1/restore").status_code == 401


# ---------------------------------------------------------------------------
# PART 6: Class management
# ---------------------------------------------------------------------------
def test_class_create_and_duplicate_conflict(client, users):
    admin_headers = make_auth_header(users["admin"])
    resp = client.post("/api/v1/classes", json={"name": "Biology"}, headers=admin_headers)
    assert resp.status_code == 201
    resp = client.post("/api/v1/classes", json={"name": "Biology"}, headers=admin_headers)
    assert resp.status_code == 409


def test_class_blank_name_rejected(client, users):
    resp = client.post("/api/v1/classes", json={"name": "   "},
                       headers=make_auth_header(users["admin"]))
    assert resp.status_code == 422


def test_class_update_rename_and_duplicate(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    class_id = data["class"].id
    resp = client.put(f"/api/v1/classes/{class_id}", json={"name": "Renamed"},
                      headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    resp = client.put(f"/api/v1/classes/{class_id}", json={"name": None},
                      headers=admin_headers)
    assert resp.status_code == 400


def test_archive_class_hides_class_and_divisions(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    class_id = data["class"].id
    resp = client.delete(f"/api/v1/classes/{class_id}", headers=admin_headers)
    assert resp.status_code == 200
    listed = client.get("/api/v1/classes", headers=make_auth_header(users["teacher"])).json()
    assert all(item["id"] != class_id for item in listed)
    detail = client.get(f"/api/v1/classes/{class_id}", headers=admin_headers).json()
    assert detail["divisions"] == []


# ---------------------------------------------------------------------------
# PART 6: Division management
# ---------------------------------------------------------------------------
def test_division_duplicate_in_class_rejected_but_ok_across_classes(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    resp = client.post(f"/api/v1/classes/{data['class'].id}/divisions", json={"name": "A"},
                       headers=admin_headers)
    assert resp.status_code == 409
    new_class = client.post("/api/v1/classes", json={"name": "Class 12"},
                            headers=admin_headers).json()
    resp = client.post(f"/api/v1/classes/{new_class['id']}/divisions", json={"name": "A"},
                       headers=admin_headers)
    assert resp.status_code == 201


def test_division_cannot_be_added_to_archived_class(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    client.delete(f"/api/v1/classes/{data['class'].id}", headers=admin_headers)
    resp = client.post(f"/api/v1/classes/{data['class'].id}/divisions", json={"name": "B"},
                       headers=admin_headers)
    assert resp.status_code == 400


def test_archive_division_hides_it_from_lists(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    resp = client.delete(f"/api/v1/divisions/{data['division'].id}", headers=admin_headers)
    assert resp.status_code == 200
    listed = client.get(f"/api/v1/classes/{data['class'].id}/divisions",
                        headers=admin_headers).json()
    assert listed == []


def test_update_division_blank_name_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    resp = client.put(f"/api/v1/divisions/{data['division'].id}", json={"name": " "},
                      headers=make_auth_header(users["admin"]))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PART 7-8: Student management
# ---------------------------------------------------------------------------
def test_student_create_normalizes_phone_and_returns_class(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "name": "  New Kid  ",
        "roll_number": "  99  ",
        "parent_name": "Kid Parent",
        "parent_whatsapp_number": "+91 90000-11111",
    }
    resp = client.post("/api/v1/students", json=payload,
                       headers=make_auth_header(users["admin"]))
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "New Kid"
    assert body["roll_number"] == "99"
    assert body["parent_whatsapp_number"] == "+919000011111"
    assert body["class"]["id"] == data["class"].id
    assert body["division"]["id"] == data["division"].id


def test_student_create_invalid_phone_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "name": "Kid",
        "roll_number": "98",
        "parent_name": "Parent",
        "parent_whatsapp_number": "09000-11111",
    }
    resp = client.post("/api/v1/students", json=payload,
                       headers=make_auth_header(users["admin"]))
    assert resp.status_code == 422


def test_student_duplicate_roll_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    resp = client.get("/api/v1/students", params={"division_id": data["division"].id},
                      headers=admin_headers)
    existing = resp.json()[0]
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "name": "Clone",
        "roll_number": existing["roll_number"],
        "parent_name": "Parent",
        "parent_whatsapp_number": "+919000000001",
    }
    resp = client.post("/api/v1/students", json=payload, headers=admin_headers)
    assert resp.status_code == 409


def test_student_archived_class_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    client.delete(f"/api/v1/classes/{data['class'].id}", headers=admin_headers)
    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "name": "Kid",
        "roll_number": "98",
        "parent_name": "Parent",
        "parent_whatsapp_number": "+919000000001",
    }
    resp = client.post("/api/v1/students", json=payload, headers=admin_headers)
    assert resp.status_code == 400


def test_student_division_not_in_class_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    other = _seed_class_division_students(db_session, users["admin"], class_name="Class 11")
    payload = {
        "class_id": data["class"].id,
        "division_id": other["division"].id,
        "name": "Kid",
        "roll_number": "98",
        "parent_name": "Parent",
        "parent_whatsapp_number": "+919000000001",
    }
    resp = client.post("/api/v1/students", json=payload,
                       headers=make_auth_header(users["admin"]))
    assert resp.status_code == 400


def test_student_update_move_and_duplicate_conflict(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    first, second = data["students"][0], data["students"][1]
    resp = client.put(f"/api/v1/students/{first.id}",
                      json={"roll_number": second.roll_number}, headers=admin_headers)
    assert resp.status_code == 409
    resp = client.put(f"/api/v1/students/{first.id}",
                      json={"name": "Renamed Kid", "parent_whatsapp_number": "+91 90000-22222"},
                      headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed Kid"
    assert body["parent_whatsapp_number"] == "+919000022222"


def test_student_archive_and_restore_flow(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    student = data["students"][0]
    resp = client.delete(f"/api/v1/students/{student.id}", headers=admin_headers)
    assert resp.status_code == 200
    listed = client.get("/api/v1/students", params={"division_id": data["division"].id},
                        headers=admin_headers).json()
    assert all(item["id"] != student.id for item in listed)
    resp = client.post(f"/api/v1/students/{student.id}/restore", headers=admin_headers)
    assert resp.status_code == 200
    listed = client.get("/api/v1/students", params={"division_id": data["division"].id},
                        headers=admin_headers).json()
    assert any(item["id"] == student.id for item in listed)


def test_student_restore_conflict_with_active_roll(client, db_session, users):
    from app.models.student import Student

    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    student = data["students"][0]
    client.delete(f"/api/v1/students/{student.id}", headers=admin_headers)
    db_session.add(Student(
        class_id=data["class"].id,
        division_id=data["division"].id,
        name="Replacement",
        roll_number=student.roll_number,
        parent_name="Parent",
        parent_whatsapp_number="+919000000002",
        is_active=True,
        created_by=users["admin"].id,
    ))
    db_session.commit()
    resp = client.post(f"/api/v1/students/{student.id}/restore", headers=admin_headers)
    assert resp.status_code == 409


def test_student_restore_into_archived_class_rejected(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    student = data["students"][0]
    client.delete(f"/api/v1/students/{student.id}", headers=admin_headers)
    client.delete(f"/api/v1/classes/{data['class'].id}", headers=admin_headers)
    resp = client.post(f"/api/v1/students/{student.id}/restore", headers=admin_headers)
    assert resp.status_code == 400


def test_student_search_and_archived_flag(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    resp = client.get("/api/v1/students", params={"search": "rahul"},
                      headers=admin_headers).json()
    assert [s["name"] for s in resp] == ["Rahul Patel"]
    student = data["students"][1]
    client.delete(f"/api/v1/students/{student.id}", headers=admin_headers)
    resp = client.get("/api/v1/students", params={"search": "priya"},
                      headers=admin_headers).json()
    assert resp == []


def test_student_list_sorts_naturally(client, db_session, users):
    from app.models.student import Student

    data = _seed_class_division_students(db_session, users["admin"])
    grant_teacher_access(db_session, users["teacher"], data["class"].id)
    for roll in ["3", "A1", "B-2"]:
        db_session.add(Student(
            class_id=data["class"].id,
            division_id=data["division"].id,
            name="Sort Me",
            roll_number=roll,
            parent_name="Parent",
            parent_whatsapp_number="+919000000003",
            is_active=True,
            created_by=users["admin"].id,
        ))
    db_session.commit()
    resp = client.get("/api/v1/students", params={"division_id": data["division"].id},
                      headers=make_auth_header(users["teacher"])).json()
    rolls = [s["roll_number"] for s in resp]
    assert rolls == ["1", "2", "3", "10", "A1", "B-2"]


# ---------------------------------------------------------------------------
# PART 10: Attendance gaps
# ---------------------------------------------------------------------------
def test_prepare_rejects_archived_division(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    client.delete(f"/api/v1/divisions/{data['division'].id}", headers=admin_headers)
    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-09-01",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_prepare_and_save_with_absent_status(client, db_session, users):
    data = _seed_class_division_students(db_session, users["admin"])
    admin_headers = make_auth_header(users["admin"])
    resp = client.get(
        "/api/v1/attendance/prepare",
        params={
            "class_id": data["class"].id,
            "division_id": data["division"].id,
            "attendance_date": "2026-09-02",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    prepared = resp.json()
    assert prepared["existing_session"] is False
    assert [s["status"] for s in prepared["students"]] == ["PRESENT"] * 3

    payload = {
        "class_id": data["class"].id,
        "division_id": data["division"].id,
        "attendance_date": "2026-09-02",
        "students": [
            {"student_id": data["students"][0].id, "status": "PRESENT", "remarks": None},
            {"student_id": data["students"][1].id, "status": "ABSENT", "remarks": "sick"},
            {"student_id": data["students"][2].id, "status": "PRESENT", "remarks": None},
        ],
    }
    resp = client.post("/api/v1/attendance", json=payload, headers=admin_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["present_count"] == 2
    assert body["absent_count"] == 1
    assert body["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# PART 27: Performance smoke test
# ---------------------------------------------------------------------------
def test_list_classes_and_students_scale_smoke(client, db_session, users):
    from app.models.school_class import Division
    from app.models.student import Student

    admin = users["admin"]
    headers = make_auth_header(admin)
    for c in range(1, 11):
        school_class = _seed_class_division_students(
            db_session, admin, class_name=f"Class {c}"
        )
        for name in ("B", "C", "D"):
            db_session.add(Division(
                name=name, is_active=True, class_id=school_class["class"].id, created_by=admin.id
            ))
        for s in school_class["students"]:
            db_session.add(Student(
                class_id=school_class["class"].id,
                division_id=school_class["division"].id,
                name=s.name + " Extra",
                roll_number="0-" + s.roll_number,
                parent_name="Parent",
                parent_whatsapp_number="+919000000004",
                is_active=True,
                created_by=admin.id,
            ))
    db_session.commit()

    resp = client.get("/api/v1/classes", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 10
    for item in resp.json():
        assert len(item["divisions"]) >= 1

    resp = client.get("/api/v1/students", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 60


# ---------------------------------------------------------------------------
# PART 26: Production config guard
# ---------------------------------------------------------------------------
def test_production_fails_fast_on_insecure_config():
    from app.core.config import Settings

    s = Settings(
        APP_ENV="production",
        DATABASE_URL="postgresql://postgres:postgres@localhost:5432/school_attendance",
        JWT_SECRET_KEY="x" * 40,
        CORS_ORIGINS=["http://localhost:3000"],
    )
    with pytest.raises(RuntimeError):
        s.validate_production_config()


def test_production_fails_fast_on_default_jwt():
    from pydantic_core import ValidationError as PydanticValidationError

    from app.core.config import Settings

    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="production",
            JWT_SECRET_KEY="change-this-to-a-random-secret-key-in-production",
            DATABASE_URL="postgresql://user:pass@prod/db",
            CORS_ORIGINS=["https://app.example.com"],
        )


def test_production_accepts_secure_config():
    from app.core.config import Settings

    # V2.7: a secure production config must also enable backup encryption
    # (production backups are always encrypted at rest).
    s = Settings(
        APP_ENV="production",
        JWT_SECRET_KEY="t" * 40,
        DATABASE_URL="postgresql://real:secret@prod.example.com/school",
        CORS_ORIGINS=["https://app.example.com"],
        BACKUP_ENCRYPTION_ENABLED=True,
        BACKUP_ENCRYPTION_KEY="a" * 32,
    )
    s.validate_production_config()