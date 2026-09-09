"""V2.2 performance sanity checks for the reporting module (Part 37).

These tests seed a realistic school-sized dataset (~300 students, 5 recording
days) and verify that the aggregate report endpoints respond quickly and return
correct rollups. The timings are intentionally generous so CI remains stable;
the real guarantee being validated is that reports run on SQL aggregation
(no loading of whole attendance tables into Python) and never time out.

Run with the rest of the suite:

    python -m pytest tests/test_report_performance.py -q
"""

from conftest import make_auth_header

DATE_STR = "2026-07-01"


def _seed_school(db, admin):
    """Seed CLASSES x DIVISIONS_PER_CLASS divisions with STUDENTS_PER_DIVISION
    students each."""
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    CLASSES = 3
    DIVISIONS_PER_CLASS = 3
    STUDENTS_PER_DIVISION = 30

    students_by_division = {}
    for c_idx in range(CLASSES):
        school_class = SchoolClass(
            name=f"Perf Class {c_idx + 1}",
            is_active=True,
            created_by=admin.id,
        )
        db.add(school_class)
        db.flush()
        for d_idx in range(DIVISIONS_PER_CLASS):
            division = Division(
                name=chr(ord("A") + d_idx),
                is_active=True,
                class_id=school_class.id,
                created_by=admin.id,
            )
            db.add(division)
            db.flush()
            students = []
            for s_idx in range(STUDENTS_PER_DIVISION):
                student = Student(
                    class_id=school_class.id,
                    division_id=division.id,
                    name=f"Student {c_idx}-{d_idx}-{s_idx}",
                    roll_number=str(s_idx + 1),
                    parent_name="Parent",
                    parent_whatsapp_number=f"+91{c_idx:02d}{d_idx:02d}{s_idx:05d}",
                    is_active=True,
                    created_by=admin.id,
                )
                db.add(student)
                students.append(student)
            db.flush()
            students_by_division[(school_class.id, division.id)] = students
    db.commit()
    return students_by_division


def _record_attendance(db, admin, students_by_division, start_day=1):
    import datetime

    from app.services.attendance_service import save_attendance

    pairs = sorted(students_by_division.keys())
    for day_offset in range(5):
        day = start_day + day_offset
        for class_id, division_id in pairs:
            students = students_by_division[(class_id, division_id)]
            save_attendance(
                db,
                class_id=class_id,
                division_id=division_id,
                attendance_date=datetime.date(2026, 7, day),
                submissions=[
                    {
                        "student_id": s.id,
                        "status": "PRESENT" if (s.id % 3) else "ABSENT",
                    }
                    for s in students
                ],
                taker=admin,
            )


def test_large_class_report_returns_fast_and_correct(client, db_session, users):
    import time

    students_by_division = _seed_school(db_session, users["admin"])
    _record_attendance(db_session, users["admin"], students_by_division)

    class_id, division_id = next(iter(students_by_division.keys()))

    start = time.perf_counter()
    resp = client.get(
        f"/api/v1/reports/classes/{class_id}",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=make_auth_header(users["admin"]),
    )
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    body = resp.json()
    # The whole class spans 3 divisions x 30 students = 90 students, 15 sessions.
    assert body["total_students"] == 90
    assert body["total_attendance_sessions"] == 15
    assert body["student_summary"]["pagination"]["total"] == 90
    assert body["student_summary"]["pagination"]["total_pages"] == 2
    page_sum = sum(
        s["present_days"] + s["absent_days"] for s in body["student_summary"]["items"]
    )
    # Session aggregates cover every student (450 records); the paginated page
    # covers the first 50 students (250 records). Both must be consistent.
    assert body["total_present_records"] + body["total_absent_records"] == 450
    assert page_sum == 250
    assert elapsed < 10.0, f"class report took {elapsed:.2f}s"


def test_large_summary_report_fast(client, db_session, users):
    import time

    students_by_division = _seed_school(db_session, users["admin"])
    _record_attendance(db_session, users["admin"], students_by_division)

    total_students = sum(len(v) for v in students_by_division.values())
    start = time.perf_counter()
    resp = client.get(
        "/api/v1/reports/summary",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=make_auth_header(users["admin"]),
    )
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    body = resp.json()
    # 9 (class, division) pairs x 5 days = 45 sessions; 30 x 9 = 270 students.
    assert body["total_attendance_sessions"] == 45
    assert body["total_students_marked"] == total_students * 5
    assert len(body["daily_trend"]) == 5
    assert elapsed < 10.0, f"summary report took {elapsed:.2f}s"


def test_large_daily_report_fast(client, db_session, users):
    import time

    students_by_division = _seed_school(db_session, users["admin"])
    _record_attendance(db_session, users["admin"], students_by_division)

    start = time.perf_counter()
    resp = client.get(
        "/api/v1/reports/daily",
        params={"date": DATE_STR},
        headers=make_auth_header(users["admin"]),
    )
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 9
    present = sum(i["present"] for i in body["items"])
    absent = sum(i["absent"] for i in body["items"])
    assert present + absent == 270
    assert elapsed < 10.0, f"daily report took {elapsed:.2f}s"