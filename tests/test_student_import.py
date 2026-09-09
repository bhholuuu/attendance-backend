"""Tests for the bulk CSV student import (validate + preview + commit)."""

import hashlib

from conftest import make_auth_header

from app.models.student import Student
from app.models.student_import import StudentImport

HEADER = (
    "class_name,division_name,student_name,roll_number,parent_name,"
    "parent_whatsapp_number\n"
)


def _csv(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode("utf-8")


def _token(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _create_class_division(client, header):
    resp = client.post("/api/v1/classes", headers=header, json={"name": "10"})
    assert resp.status_code == 201, resp.text
    class_id = resp.json()["id"]
    resp = client.post(
        f"/api/v1/classes/{class_id}/divisions",
        headers=header,
        json={"name": "A"},
    )
    assert resp.status_code == 201, resp.text
    division_id = resp.json()["id"]
    return class_id, division_id


def _preview(client, header, content):
    return client.post(
        "/api/v1/students/import/preview",
        headers=header,
        files={"file": ("students.csv", content, "text/csv")},
    )


def _commit(client, header, content, token=None, allow_partial=False):
    return client.post(
        "/api/v1/students/import/commit",
        headers=header,
        files={"file": ("students.csv", content, "text/csv")},
        data={
            "preview_token": token or _token(content),
            "allow_partial": "true" if allow_partial else "false",
        },
    )


def test_import_preview_valid_file(client, users, db_session):
    header = make_auth_header(users["admin"])
    _create_class_division(client, header)
    content = _csv(
        "10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210",
        "10,A,Priya Patel,2,Mahesh Patel,+919876543211",
    )
    resp = _preview(client, header, content)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_records"] == 2
    assert body["valid_records"] == 2
    assert body["invalid_records"] == 0
    assert body["errors"] == []
    assert body["preview_token"] == _token(content)
    # Preview never writes.
    assert db_session.query(Student).count() == 0


def test_import_preview_reports_invalid_rows(client, users, db_session):
    header = make_auth_header(users["admin"])
    _create_class_division(client, header)
    content = _csv(
        "10,A,Good Student,1,Parent One,+919876543210",
        "10,A,Missing Class,2,Parent Two,+9300000",          # bad phone
        "11,A,No Such Class,3,Parent Three,+919876543213",   # missing class
        "10,B,No Such Division,4,Parent Four,+919876543214", # missing division
    )
    resp = _preview(client, header, content)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_records"] == 4
    assert body["valid_records"] == 1
    assert body["invalid_records"] == 3
    fields = [e["field"] for e in body["errors"]]
    assert "parent_whatsapp_number" in fields
    assert "class_name" in fields
    assert "division_name" in fields
    for e in body["errors"]:
        assert "row_number" in e and "error" in e


def test_import_preview_requires_admin(client, users):
    header = make_auth_header(users["teacher"])
    content = _csv("10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210")
    resp = _preview(client, header, content)
    assert resp.status_code == 403
    resp = _commit(client, header, content)
    assert resp.status_code == 403


def test_import_commit_creates_students_and_audit(client, users, db_session):
    header = make_auth_header(users["admin"])
    class_id, division_id = _create_class_division(client, header)
    content = _csv(
        "10,A,Rahul Sharma,1,Ramesh Sharma,+91-98765-43210",  # formatting removed
        "10,A,Priya Patel,2,Mahesh Patel,+919876543211",
    )
    preview = _preview(client, header, content)
    assert preview.json()["invalid_records"] == 0
    token = preview.json()["preview_token"]

    resp = _commit(client, header, content, token=token)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["imported_records"] == 2
    assert body["invalid_records"] == 0
    assert body["total_records"] == 2

    students = (
        db_session.query(Student)
        .filter(Student.class_id == class_id, Student.division_id == division_id)
        .order_by(Student.roll_number)
        .all()
    )
    assert len(students) == 2
    assert students[0].name == "Rahul Sharma"
    assert students[0].parent_whatsapp_number == "+919876543210"
    assert students[1].name == "Priya Patel"
    assert students[1].created_by == users["admin"].id

    audits = db_session.query(StudentImport).all()
    assert len(audits) == 1
    assert audits[0].imported_by == users["admin"].id
    assert audits[0].file_name == "students.csv"
    assert audits[0].total_records == 2
    assert audits[0].imported_records == 2
    assert audits[0].invalid_records == 0


def test_import_commit_refused_when_invalid_without_partial(
    client, users, db_session
):
    header = make_auth_header(users["admin"])
    _create_class_division(client, header)
    content = _csv(
        "10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210",
        "10,A,Bad Phone,2,Parent,+not-a-number",
    )
    resp = _commit(client, header, content, allow_partial=False)
    assert resp.status_code == 400, resp.text
    assert db_session.query(Student).count() == 0
    assert db_session.query(StudentImport).count() == 0


def test_import_commit_partial_imports_valid_rows_only(
    client, users, db_session
):
    header = make_auth_header(users["admin"])
    _create_class_division(client, header)
    content = _csv(
        "10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210",
        "10,A,Bad Phone,2,Parent,+not-a-number",
    )
    resp = _commit(client, header, content, allow_partial=True)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["imported_records"] == 1
    assert body["invalid_records"] == 1
    assert body["errors"][0]["field"] == "parent_whatsapp_number"
    assert db_session.query(Student).count() == 1
    assert db_session.query(StudentImport).count() == 1
    assert db_session.query(StudentImport).one().invalid_records == 1


def test_import_commit_wrong_preview_token_rejected(client, users, db_session):
    header = make_auth_header(users["admin"])
    _create_class_division(client, header)
    content = _csv("10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210")
    resp = _commit(client, header, content, token="not-the-right-token")
    assert resp.status_code == 400
    assert db_session.query(Student).count() == 0
    assert db_session.query(StudentImport).count() == 0


def test_import_conflict_with_existing_student_is_not_overwritten(
    client, users, db_session
):
    header = make_auth_header(users["admin"])
    class_id, division_id = _create_class_division(client, header)

    # Pre-existing active student with roll 1 (manual API).
    resp = client.post(
        "/api/v1/students",
        headers=header,
        json={
            "name": "Existing Student",
            "roll_number": "1",
            "parent_name": "Existing Parent",
            "parent_whatsapp_number": "+919876540000",
            "class_id": class_id,
            "division_id": division_id,
        },
    )
    assert resp.status_code == 201, resp.text

    content = _csv(
        "10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210",   # roll conflict
        "10,A,Priya Patel,2,Mahesh Patel,+919876543211",
    )
    preview = _preview(client, header, content)
    body = preview.json()
    assert body["invalid_records"] == 1

    resp = _commit(client, header, content, allow_partial=True)
    assert resp.status_code == 201, resp.text
    assert resp.json()["imported_records"] == 1

    students = (
        db_session.query(Student)
        .filter(Student.class_id == class_id, Student.division_id == division_id)
        .all()
    )
    assert len(students) == 2
    by_roll = {s.roll_number: s for s in students}
    # Original student untouched.
    assert by_roll["1"].name == "Existing Student"
    assert by_roll["1"].parent_whatsapp_number == "+919876540000"
    # New student added.
    assert by_roll["2"].name == "Priya Patel"


def test_import_shared_parent_number_allowed(client, users, db_session):
    header = make_auth_header(users["admin"])
    class_id, division_id = _create_class_division(client, header)
    content = _csv(
        "10,A,Child One,1,Shared Parent,+919876543210",
        "10,A,Child Two,2,Shared Parent,+919876543210",
        "10,A,Child Three,3,Other Parent,+919876543211",
    )
    resp = _commit(client, header, content, allow_partial=True)
    assert resp.status_code == 201, resp.text
    assert resp.json()["imported_records"] == 3
    assert resp.json()["invalid_records"] == 0
    assert db_session.query(Student).count() == 3


def test_import_duplicate_roll_within_file_reported(client, users, db_session):
    header = make_auth_header(users["admin"])
    _create_class_division(client, header)
    content = _csv(
        "10,A,Rahul Sharma,1,Ramesh Sharma,+919876543210",
        "10,A,Dup Student,1,Other Parent,+919876543211",
    )
    resp = _preview(client, header, content)
    assert resp.status_code == 200
    body = resp.json()
    assert body["invalid_records"] == 1
    err = body["errors"][0]
    assert err["field"] == "roll_number"
    assert err["row_number"] == 3  # header=row 1, data rows start at 2


def test_import_structural_errors(client, users):
    header = make_auth_header(users["admin"])
    bad = client.post(
        "/api/v1/students/import/preview",
        headers=header,
        files={"file": ("students.csv", b"name,roll\nfoo,1\n", "text/csv")},
    )
    assert bad.status_code == 400
    empty = client.post(
        "/api/v1/students/import/preview",
        headers=header,
        files={"file": ("students.csv", b"", "text/csv")},
    )
    assert empty.status_code == 400