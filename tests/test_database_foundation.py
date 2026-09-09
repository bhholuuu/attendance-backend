"""Database foundation tests.

Verifies the persistence layer behaves correctly: CRUD for every core entity,
unique-constraint enforcement, foreign-key behavior, migration availability,
admin settings, and the database-aware health endpoint.

Uses the in-memory SQLite test database (``db_session`` fixture) which mirrors
the PostgreSQL schema via the shared SQLAlchemy ``Base.metadata``.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.models.academic_year import AcademicYear
from app.models.admin_settings import AdminSetting
from app.models.attendance import (
    AttendanceSession,
    AttendanceStatus,
    SessionStatus,
    StudentAttendance,
)
from app.models.calendar_event import CalendarEvent, CalendarEventType, HolidayType
from app.models.message import (
    MessageBatch,
    MessageBatchStatus,
    ParentMessage,
    MessageDeliveryStatus,
)
from app.models.school_class import Division, SchoolClass
from app.models.student import Student
from app.models.student_leave import LeaveStatus, StudentLeave
from app.models.teacher_assignment import TeacherAssignment
from app.models.user import User, UserRole


# ---------------------------------------------------------------------------
# Users & auth lookup
# ---------------------------------------------------------------------------
def test_user_creation_and_auth_lookup(db_session):
    from app.core.security import hash_password, verify_password

    user = User(
        full_name="Database User",
        username="dbuser",
        password_hash=hash_password("password123"),
        role=UserRole.TEACHER,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.id is not None
    assert user.last_login_at is None
    assert verify_password("password123", user.password_hash)

    found = db_session.execute(
        select(User).where(User.username == "dbuser")
    ).scalar_one()
    assert found.id == user.id
    assert found.role == UserRole.TEACHER


def test_user_login_records_last_login_at(db_session, users, client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert resp.status_code == 200

    refreshed = db_session.execute(
        select(User).where(User.username == "admin")
    ).scalar_one()
    assert refreshed.last_login_at is not None


def test_duplicate_username_rejected(db_session):
    from app.core.security import hash_password

    args = dict(
        full_name="X",
        username="dupuser",
        password_hash=hash_password("password123"),
        role=UserRole.TEACHER,
        is_active=True,
    )
    db_session.add(User(**args))
    db_session.commit()

    db_session.add(User(**args))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Classes & divisions
# ---------------------------------------------------------------------------
def test_class_and_division_creation(db_session, users):
    cls = SchoolClass(
        name="Class VII", is_active=True, created_by=users["admin"].id
    )
    db_session.add(cls)
    db_session.commit()
    db_session.refresh(cls)

    div = Division(
        class_id=cls.id, name="A", is_active=True, created_by=users["admin"].id
    )
    db_session.add(div)
    db_session.commit()
    db_session.refresh(div)

    assert div.class_id == cls.id
    assert len(cls.divisions) == 1


def test_duplicate_class_name_rejected(db_session, users):
    db_session.add(
        SchoolClass(name="Class VII", is_active=True, created_by=users["admin"].id)
    )
    db_session.commit()

    db_session.add(
        SchoolClass(name="Class VII", is_active=True, created_by=users["admin"].id)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Teacher assignments
# ---------------------------------------------------------------------------
def test_teacher_assignment_creation(db_session, users):
    cls = SchoolClass(
        name="Class VIII", is_active=True, created_by=users["admin"].id
    )
    db_session.add(cls)
    db_session.commit()
    db_session.refresh(cls)

    assignment = TeacherAssignment(
        teacher_id=users["teacher"].id,
        class_id=cls.id,
        division_id=None,
        created_by=users["admin"].id,
    )
    db_session.add(assignment)
    db_session.commit()
    db_session.refresh(assignment)

    assert assignment.teacher_id == users["teacher"].id
    assert assignment.teacher.username == "teacher"


def test_duplicate_teacher_assignment_rejected(db_session, users):
    cls = SchoolClass(
        name="Class IX", is_active=True, created_by=users["admin"].id
    )
    db_session.add(cls)
    db_session.commit()
    db_session.refresh(cls)
    div = Division(
        class_id=cls.id, name="A", is_active=True, created_by=users["admin"].id
    )
    db_session.add(div)
    db_session.commit()
    db_session.refresh(div)

    # A specific division (not NULL) is used so the composite unique index
    # actually fires; SQL treats NULL division as distinct, so NULL rows can
    # only be deduplicated at the service layer (not here).
    args = dict(
        teacher_id=users["teacher"].id,
        class_id=cls.id,
        division_id=div.id,
        created_by=users["admin"].id,
    )
    db_session.add(TeacherAssignment(**args))
    db_session.commit()

    db_session.add(TeacherAssignment(**args))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
def _make_class_division(db, admin_id):
    cls = SchoolClass(name="Class X", is_active=True, created_by=admin_id)
    db.add(cls)
    db.commit()
    db.refresh(cls)
    div = Division(class_id=cls.id, name="A", is_active=True, created_by=admin_id)
    db.add(div)
    db.commit()
    db.refresh(div)
    return cls, div


def test_student_creation(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)

    student = Student(
        class_id=cls.id,
        division_id=div.id,
        name="Ravi Kumar",
        roll_number="7",
        parent_name="Suresh Kumar",
        parent_whatsapp_number="+911234567890",
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)

    assert student.id is not None
    assert student.division.name == "A"
    assert student.school_class.name == "Class X"


def test_duplicate_roll_number_rejected(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)
    common = dict(
        class_id=cls.id,
        division_id=div.id,
        name="Student",
        parent_name="Parent",
        parent_whatsapp_number="+919876543210",
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(Student(roll_number="42", **common))
    db_session.commit()

    db_session.add(Student(roll_number="42", **common))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_key_violation_rejected(db_session, users):
    """SQLite disables FK enforcement by default; enable it on a dedicated
    engine so this test proves the schema's foreign keys reject orphans."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    fk_session = sessionmaker(bind=engine)()
    try:
        with pytest.raises(IntegrityError):
            fk_session.add(
                Student(
                    class_id=99999,
                    division_id=99999,
                    name="Ghost",
                    roll_number="1",
                    parent_name="P",
                    parent_whatsapp_number="+911",
                    is_active=True,
                    created_by=99999,
                )
            )
            fk_session.commit()
        fk_session.rollback()
    finally:
        fk_session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Attendance sessions & records
# ---------------------------------------------------------------------------
def test_attendance_session_creation(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)

    session = AttendanceSession(
        class_id=cls.id,
        division_id=div.id,
        attendance_date=date(2026, 9, 1),
        taken_by=users["teacher"].id,
        status=SessionStatus.COMPLETED,
        total_students=1,
        present_count=1,
        absent_count=0,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    assert session.id is not None
    assert session.taker.username == "teacher"


def test_duplicate_attendance_session_rejected(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)
    common = dict(
        class_id=cls.id,
        division_id=div.id,
        attendance_date=date(2026, 9, 1),
        taken_by=users["teacher"].id,
        status=SessionStatus.COMPLETED,
        total_students=1,
        present_count=1,
        absent_count=0,
    )
    db_session.add(AttendanceSession(**common))
    db_session.commit()

    db_session.add(AttendanceSession(**common))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_attendance_record_creation_and_duplicate_rejected(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)
    student = Student(
        class_id=cls.id,
        division_id=div.id,
        name="Anita",
        roll_number="3",
        parent_name="P",
        parent_whatsapp_number="+911",
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)

    session = AttendanceSession(
        class_id=cls.id,
        division_id=div.id,
        attendance_date=date(2026, 9, 2),
        taken_by=users["teacher"].id,
        status=SessionStatus.COMPLETED,
        total_students=1,
        present_count=1,
        absent_count=0,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    record = StudentAttendance(
        attendance_session_id=session.id,
        student_id=student.id,
        attendance_status=AttendanceStatus.PRESENT,
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)

    assert record.student.id == student.id
    assert record.session.id == session.id

    db_session.add(
        StudentAttendance(
            attendance_session_id=session.id,
            student_id=student.id,
            attendance_status=AttendanceStatus.ABSENT,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------
def test_student_leave_creation_and_approval(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)
    student = Student(
        class_id=cls.id,
        division_id=div.id,
        name="Bunty",
        roll_number="9",
        parent_name="P",
        parent_whatsapp_number="+911",
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)

    leave = StudentLeave(
        student_id=student.id,
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 4),
        leave_type="sick",
        reason="fever",
        status=LeaveStatus.PENDING,
        created_by=users["admin"].id,
    )
    db_session.add(leave)
    db_session.commit()
    db_session.refresh(leave)

    leave.status = LeaveStatus.APPROVED
    leave.approved_by = users["admin"].id
    leave.approved_at = datetime.utcnow()
    db_session.commit()
    db_session.refresh(leave)

    assert leave.status == LeaveStatus.APPROVED
    assert leave.approver.username == "admin"
    assert leave.student.name == "Bunty"


# ---------------------------------------------------------------------------
# Academic calendar & holidays
# ---------------------------------------------------------------------------
def test_academic_year_and_holiday_creation(db_session, users):
    year = AcademicYear(
        name="2026-2027",
        start_date=date(2026, 4, 1),
        end_date=date(2027, 3, 31),
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(year)
    db_session.commit()
    db_session.refresh(year)

    holiday = CalendarEvent(
        academic_year_id=year.id,
        title="Independence Day",
        event_type=CalendarEventType.HOLIDAY,
        holiday_type=HolidayType.PUBLIC_HOLIDAY,
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 15),
        is_recurring=True,
        created_by=users["admin"].id,
    )
    db_session.add(holiday)
    db_session.commit()
    db_session.refresh(holiday)

    assert holiday.academic_year.name == "2026-2027"
    assert holiday.event_type == CalendarEventType.HOLIDAY


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
def test_message_batch_and_parent_message_creation(db_session, users):
    cls, div = _make_class_division(db_session, users["admin"].id)
    student = Student(
        class_id=cls.id,
        division_id=div.id,
        name="Chhaya",
        roll_number="11",
        parent_name="P",
        parent_whatsapp_number="+911",
        is_active=True,
        created_by=users["admin"].id,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)

    session = AttendanceSession(
        class_id=cls.id,
        division_id=div.id,
        attendance_date=date(2026, 9, 5),
        taken_by=users["teacher"].id,
        status=SessionStatus.COMPLETED,
        total_students=1,
        present_count=0,
        absent_count=1,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    batch = MessageBatch(
        attendance_session_id=session.id,
        class_id=cls.id,
        division_id=div.id,
        attendance_date=session.attendance_date,
        status=MessageBatchStatus.PENDING,
        total_messages=1,
        sent_count=0,
        pending_count=1,
        failed_count=0,
        created_by=users["admin"].id,
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    msg = ParentMessage(
        message_batch_id=batch.id,
        student_id=student.id,
        parent_name=student.parent_name,
        parent_whatsapp_number=student.parent_whatsapp_number,
        attendance_status=AttendanceStatus.ABSENT,
        message_content="Your child was absent.",
        delivery_status=MessageDeliveryStatus.PENDING,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert msg.batch.id == batch.id
    assert batch.messages[0].id == msg.id


# ---------------------------------------------------------------------------
# Admin settings
# ---------------------------------------------------------------------------
def test_admin_settings_crud(db_session):
    setting = AdminSetting(
        key="school.contact_number",
        value="+911234567890",
        description="School front desk number.",
    )
    db_session.add(setting)
    db_session.commit()
    db_session.refresh(setting)

    assert setting.id is not None

    setting.value = "+919876543210"
    db_session.commit()
    db_session.refresh(setting)
    assert setting.value == "+919876543210"

    db_session.delete(setting)
    db_session.commit()
    assert (
        db_session.execute(select(AdminSetting)).scalars().all() == []
    )


def test_duplicate_admin_setting_key_rejected(db_session):
    db_session.add(AdminSetting(key="a.b", value="1"))
    db_session.commit()

    db_session.add(AdminSetting(key="a.b", value="2"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Migration & schema
# ---------------------------------------------------------------------------
def test_migration_files_present_and_chained():
    """Every Alembic revision must chain correctly to a single head."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly one head, found {heads}"

    revisions = list(script.walk_revisions())
    ids = [r.revision for r in revisions]
    for expected in (
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
    ):
        assert expected in ids, f"Migration {expected} missing"


def test_schema_includes_all_required_tables(db_session):
    """All server tables must exist in the metadata and be creatable."""
    expected_tables = {
        "users",
        "classes",
        "divisions",
        "students",
        "attendance_sessions",
        "student_attendance",
        "teacher_assignments",
        "message_batches",
        "parent_messages",
        "refresh_tokens",
        "student_imports",
        "academic_years",
        "calendar_events",
        "student_leaves",
        "admin_settings",
        "audit_logs",
    }
    inspector = inspect(db_session.get_bind())
    actual = set(inspector.get_table_names())
    assert expected_tables.issubset(actual), (
        f"Missing tables: {expected_tables - actual}"
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
def test_health_endpoint_reports_database(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] in {"connected", "unavailable"}
    assert body["status"] in {"healthy", "degraded"}


def test_ready_endpoint_reports_database(client):
    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json()["database"] in {"connected", "unavailable"}
