from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.attendance import (
    AttendanceSession,
    AttendanceStatus,
    SessionStatus,
    StudentAttendance,
)
from app.models.school_class import Division, SchoolClass
from app.models.student import Student
from app.models.user import User

# Reuse the same natural sort used for student lists so roll numbers appear in
# a sensible order (1, 2, 3, 10, A1, ...).
from app.services.student_service import _natural_sort_key
from app.services.effective_attendance_service import (
    date_in_active_academic_year,
    is_holiday,
    on_leave_student_ids,
)

FUTURE_DATE_MESSAGE = "Attendance cannot be recorded for a future date."
HOLIDAY_MESSAGE = (
    "Attendance cannot be recorded because the selected date is a school holiday."
)
ACADEMIC_YEAR_MESSAGE = (
    "Attendance for the selected date is outside the active academic year."
)
ON_LEAVE_MESSAGE = (
    "Student id {sid} is on approved leave for the selected date and cannot "
    "be marked present or absent."
)


def _http(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def _validate_class_division(
    db: Session, class_id: int, division_id: int
) -> tuple[SchoolClass, Division]:
    """Validate the class and division for attendance operations.

    Ensures the class exists and is active, the division exists and is active,
    and that the division belongs to the selected class. Returns the validated
    (class, division) pair or raises an HTTPException.
    """
    school_class = db.query(SchoolClass).filter(SchoolClass.id == class_id).first()
    if school_class is None:
        raise _http(404, "Class not found")
    if not school_class.is_active:
        raise _http(400, "Cannot take attendance for an archived class")

    division = db.query(Division).filter(Division.id == division_id).first()
    if division is None:
        raise _http(404, "Division not found")
    if division.class_id != class_id:
        raise _http(400, "Division does not belong to the specified class")
    if not division.is_active:
        raise _http(400, "Cannot take attendance for an archived division")

    return school_class, division


def _active_students(db: Session, class_id: int, division_id: int) -> List[Student]:
    """Return active students belonging to the class/division, sorted by roll."""
    students = (
        db.query(Student)
        .filter(
            Student.class_id == class_id,
            Student.division_id == division_id,
            Student.is_active.is_(True),
        )
        .all()
    )
    students.sort(key=lambda s: (_natural_sort_key(s.roll_number), s.name))
    return students


def _holiday_title(db: Session, attendance_date: date) -> Optional[str]:
    from app.services.effective_attendance_service import holiday_title_for

    return holiday_title_for(db, attendance_date)


def _guard_new_recording_date(
    db: Session, class_id: int, division_id: int, attendance_date: date
) -> None:
    """Validate a NEW attendance date: no future dates, no holidays, and the
    date must fall inside the active academic year when one is configured."""
    del class_id, division_id  # class/division already validated by callers
    if attendance_date > date.today():
        raise _http(400, FUTURE_DATE_MESSAGE)
    if is_holiday(db, attendance_date):
        raise _http(400, HOLIDAY_MESSAGE)
    if not date_in_active_academic_year(db, attendance_date):
        raise _http(400, ACADEMIC_YEAR_MESSAGE)


def _student_by_id(students: List[Student]) -> dict[int, Student]:
    return {s.id: s for s in students}


def get_session(db: Session, attendance_id: int) -> AttendanceSession:
    session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.id == attendance_id)
        .first()
    )
    if session is None:
        raise _http(404, "Attendance session not found")
    return session


def _get_existing_session(
    db: Session, class_id: int, division_id: int, attendance_date: date
) -> Optional[AttendanceSession]:
    return (
        db.query(AttendanceSession)
        .filter(
            AttendanceSession.class_id == class_id,
            AttendanceSession.division_id == division_id,
            AttendanceSession.attendance_date == attendance_date,
        )
        .first()
    )


def _records_for_session(
    db: Session, session_id: int
) -> List[StudentAttendance]:
    return (
        db.query(StudentAttendance)
        .filter(StudentAttendance.attendance_session_id == session_id)
        .all()
    )


def _record_id_by_client(
    records: List[StudentAttendance],
) -> dict[str, int]:
    """Map client_record_id -> server record id for a session's records."""
    return {
        r.client_record_id: r.id
        for r in records
        if r.client_record_id is not None
    }


# ---------------------------------------------------------------------------
# Prepare
# --------------------------------------------------------------------------
def prepare_attendance(
    db: Session, *, class_id: int, division_id: int, attendance_date: date
) -> dict:
    """Prepare the data needed to take attendance for a class/division/date.

    - Validates class/division.
    - If an attendance session already exists for the date, return it along
      with each student's recorded status (for editing) WITHOUT applying the
      holiday/future-date guards (historical data stays editable).
    - Otherwise, return all active students defaulted to PRESENT. A holiday or
      future date blocks preparing a brand-new session.
    - Students with APPROVED leave on the date are flagged ``on_leave``.
    """
    school_class, division = _validate_class_division(db, class_id, division_id)
    existing = _get_existing_session(db, class_id, division_id, attendance_date)

    holiday = False
    holiday_title = None
    if existing is None:
        _guard_new_recording_date(db, class_id, division_id, attendance_date)
        holiday = is_holiday(db, attendance_date)
        if holiday:
            holiday_title = _holiday_title(db, attendance_date)

    on_leave = (
        set()
        if existing is not None
        else on_leave_student_ids(db, class_id, division_id, attendance_date)
    )

    students = _active_students(db, class_id, division_id)

    if existing is not None:
        # Retrieve existing statuses keyed by student id.
        status_by_student = {
            r.student_id: r.attendance_status
            for r in _records_for_session(db, existing.id)
        }
        attendees = [
            {
                "student_id": s.id,
                "roll_number": s.roll_number,
                "name": s.name,
                "status": status_by_student.get(s.id, AttendanceStatus.PRESENT),
            }
            for s in students
        ]
        return {
            "existing_session": True,
            "session_id": existing.id,
            "status": existing.status,
            "class_": {"id": school_class.id, "name": school_class.name},
            "division": {"id": division.id, "name": division.name},
            "attendance_date": attendance_date,
            "is_holiday": is_holiday(db, attendance_date),
            "holiday_title": _holiday_title(db, attendance_date),
            "students": attendees,
        }

    attendees = [
        {
            "student_id": s.id,
            "roll_number": s.roll_number,
            "name": s.name,
            "status": AttendanceStatus.PRESENT,
            "on_leave": s.id in on_leave,
        }
        for s in students
    ]
    return {
        "existing_session": False,
        "session_id": None,
        "status": None,
        "class_": {"id": school_class.id, "name": school_class.name},
        "division": {"id": division.id, "name": division.name},
        "attendance_date": attendance_date,
        "is_holiday": holiday,
        "holiday_title": holiday_title,
        "students": attendees,
    }


# ---------------------------------------------------------------------------
# Save
# --------------------------------------------------------------------------
def save_attendance(
    db: Session,
    *,
    class_id: int,
    division_id: int,
    attendance_date: date,
    submissions: List[dict],
    taker: User,
) -> AttendanceSession:
    """Create a complete attendance session and its student records.

    The submitted student list must exactly match the set of active students
    in the class/division at this time (no missing, duplicate, invalid, or
    archived students). Students on APPROVED leave for the date are exempt
    from the submission set and cannot be marked present/absent. A duplicate
    session for the same class/division/date is rejected. Everything is
    committed in a single transaction.
    """
    _validate_class_division(db, class_id, division_id)
    _guard_new_recording_date(db, class_id, division_id, attendance_date)

    existing = _get_existing_session(db, class_id, division_id, attendance_date)
    if existing is not None:
        raise _http(409, "Attendance for this class, division and date already exists")

    active = _active_students(db, class_id, division_id)
    active_by_id = _student_by_id(active)
    on_leave = on_leave_student_ids(db, class_id, division_id, attendance_date)

    # Validate the submission set thoroughly.
    seen = set()
    for sub in submissions:
        sid = sub["student_id"]
        if sid in seen:
            raise _http(
                400, f"Student id {sid} appears more than once in the submission"
            )
        seen.add(sid)
        if sid in on_leave:
            raise _http(400, ON_LEAVE_MESSAGE.format(sid=sid))
        if sid not in active_by_id:
            raise _http(
                400,
                f"Student id {sid} is not an active student of the selected "
                "class and division",
            )

    missing = [
        s.id for s in active if s.id not in seen and s.id not in on_leave
    ]
    if missing:
        raise _http(
            400,
            f"Attendance is incomplete: missing entries for student id(s) {missing}",
        )

    present_count = sum(
        1 for sub in submissions if sub["status"] == AttendanceStatus.PRESENT
    )
    absent_count = len(submissions) - present_count

    session = AttendanceSession(
        class_id=class_id,
        division_id=division_id,
        attendance_date=attendance_date,
        taken_by=taker.id,
        status=SessionStatus.COMPLETED,
        total_students=len(active),
        present_count=present_count,
        absent_count=absent_count,
    )
    db.add(session)
    db.flush()  # assign session.id

    for sub in submissions:
        db.add(
            StudentAttendance(
                attendance_session_id=session.id,
                student_id=sub["student_id"],
                attendance_status=sub["status"],
                remarks=sub.get("remarks"),
            )
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(
            409, "Attendance for this class, division and date already exists"
        )

    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Offline sync (V2.4)
# --------------------------------------------------------------------------
def sync_attendance(
    db: Session,
    *,
    client_session_id: str,
    class_id: int,
    division_id: int,
    attendance_date: date,
    records: List[dict],
    taker: User,
) -> dict:
    """Apply a device-uploaded attendance batch idempotently (V2.4, Part 8).

    Server-AUTHORITATIVE sync rules:
      * The same ``client_session_id`` uploaded again with identical records is
        a no-op ("already_synced") — retries and crash-replays never duplicate.
      * The same ``client_session_id`` uploaded again with DIFFERENT records is
        a CONFLICT: the server copy is preserved, nothing is overwritten.
      * A different ``client_session_id`` for an already recorded class +
        division + date is a CONFLICT (multi-device): the server session is
        authoritative and returned so the device can show manual resolution.
      * Everything else is validated exactly like a fresh web save (future
        date / holiday / academic year / active students / duplicates /
        approved leave), and the batch is committed in ONE transaction.

    Returns a dict with:
      accepted, already_synced, conflict, message, session (ORM),
      items (list of per-record result dicts).
    """
    _validate_class_division(db, class_id, division_id)

    # Normalize pydantic UUID objects to the string form stored in the DB.
    records = [
        dict(r, client_record_id=str(r["client_record_id"])) for r in records
    ]

    # 1) Idempotent replay of an already-applied batch.
    existing_by_client = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.client_session_id == client_session_id)
        .first()
    )
    if existing_by_client is not None:
        return _sync_existing_by_client(db, existing_by_client, records)

    # 2) A session already exists for this class/division/date (created by the
    #    web UI, another device, or a different client id) -> conflict.
    existing = _get_existing_session(db, class_id, division_id, attendance_date)
    if existing is not None:
        server_records = _records_for_session(db, existing.id)
        submitted_ids = [r["client_record_id"] for r in records]
        items = [
            {
                "client_record_id": rid,
                "status": "CONFLICT",
                "record_id": None,
                "reason": "An attendance session already exists for this "
                "class, division and date. The saved copy is shown and kept.",
            }
            for rid in submitted_ids
        ]
        return {
            "accepted": False,
            "already_synced": False,
            "conflict": True,
            "message": "Attendance already exists for this class, division and "
            "date on the server. Your copy was not uploaded.",
            "session": existing,
            "items": items,
        }

    # 3) Validate the batch exactly like a fresh web save.
    _guard_new_recording_date(db, class_id, division_id, attendance_date)

    active = _active_students(db, class_id, division_id)
    active_by_id = _student_by_id(active)
    on_leave = on_leave_student_ids(db, class_id, division_id, attendance_date)

    seen = set()
    seen_client = set()
    for sub in records:
        crid = sub["client_record_id"]
        if crid in seen_client:
            raise _http(
                400, f"Client record id {crid} appears more than once in the request"
            )
        seen_client.add(crid)
        sid = sub["student_id"]
        if sid in seen:
            raise _http(
                400, f"Student id {sid} appears more than once in the request"
            )
        seen.add(sid)
        if sid in on_leave:
            raise _http(400, ON_LEAVE_MESSAGE.format(sid=sid))
        if sid not in active_by_id:
            raise _http(
                400,
                f"Student id {sid} is not an active student of the selected "
                "class and division",
            )

    missing = [s.id for s in active if s.id not in seen and s.id not in on_leave]
    if missing:
        raise _http(
            400,
            f"Attendance is incomplete: missing entries for student id(s) {missing}",
        )

    present_count = sum(
        1 for sub in records if sub["status"] == AttendanceStatus.PRESENT
    )
    absent_count = len(records) - present_count

    session = AttendanceSession(
        class_id=class_id,
        division_id=division_id,
        attendance_date=attendance_date,
        taken_by=taker.id,
        status=SessionStatus.COMPLETED,
        total_students=len(active),
        present_count=present_count,
        absent_count=absent_count,
        client_session_id=client_session_id,
    )
    db.add(session)
    db.flush()

    items = []
    for sub in records:
        record = StudentAttendance(
            attendance_session_id=session.id,
            student_id=sub["student_id"],
            attendance_status=sub["status"],
            remarks=sub.get("remarks"),
            client_record_id=sub["client_record_id"],
        )
        db.add(record)
        db.flush()
        items.append(
            {
                "client_record_id": sub["client_record_id"],
                "status": "ACCEPTED",
                "record_id": record.id,
                "reason": None,
            }
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Extremely unlikely (unique client id + class/division/date guard both
        # pass); treat as a conflict instead of crashing the upload.
        return {
            "accepted": False,
            "already_synced": False,
            "conflict": True,
            "message": "A conflicting attendance session already exists on the "
            "server.",
            "session": _get_existing_session(db, class_id, division_id, attendance_date),
            "items": [],
        }

    db.refresh(session)
    return {
        "accepted": True,
        "already_synced": False,
        "conflict": False,
        "message": "Attendance synced successfully.",
        "session": session,
        "items": items,
    }


def _sync_existing_by_client(
    db: Session,
    existing: AttendanceSession,
    records: List[dict],
) -> dict:
    """Handle a replay of an already-applied client session (V2.4).

    An EXACT replay of the same records (same student, status and remarks per
    client id) is an idempotent "already_synced" success so retries and
    crash-replays never duplicate data. Any difference (student mapping, status
    or remarks) is a CONFLICT: the server copy is preserved and returned so the
    device can present both sides for manual resolution.
    """
    server_records = _records_for_session(db, existing.id)
    server_by_client = {
        r.client_record_id: r for r in server_records if r.client_record_id
    }
    submitted_ids = [r["client_record_id"] for r in records]
    submitted_set = set(submitted_ids)

    def _matches(server_rec: StudentAttendance, sub: dict) -> bool:
        return (
            server_rec.student_id == sub["student_id"]
            and server_rec.attendance_status == sub["status"]
            and (server_rec.remarks or None) == (sub.get("remarks") or None)
        )

    same_ids = submitted_set == set(server_by_client) and bool(submitted_set)
    same_content = same_ids and all(
        _matches(server_by_client[rid], sub)
        for rid, sub in zip(submitted_ids, records)
    )

    if same_content:
        record_id_by_client = _record_id_by_client(server_records)
        items = [
            {
                "client_record_id": rid,
                "status": "ALREADY_SYNCED",
                "record_id": record_id_by_client.get(rid),
                "reason": None,
            }
            for rid in sorted(submitted_set)
        ]
        return {
            "accepted": True,
            "already_synced": True,
            "conflict": False,
            "message": "This attendance session was already synced.",
            "session": existing,
            "items": items,
        }

    submitted_sorted = sorted(submitted_ids)
    items = [
        {
            "client_record_id": rid,
            "status": "CONFLICT",
            "record_id": None,
            "reason": "The server already has this session under a different "
            "set of records. Your offline changes were not uploaded.",
        }
        for rid in submitted_sorted
    ]
    return {
        "accepted": False,
        "already_synced": False,
        "conflict": True,
        "message": "This session was already synced with different records on "
        "the server. Your offline changes were not applied.",
        "session": existing,
        "items": items,
    }
def get_session_with_records(
    db: Session, attendance_id: int
) -> dict:
    """Return full detail (session + student records) for a single session."""
    session = get_session(db, attendance_id)
    records = _records_for_session(db, session.id)
    student_ids = {r.student_id: r for r in records}
    students = (
        db.query(Student)
        .filter(Student.id.in_(student_ids.keys()))
        .all()
        if student_ids
        else []
    )
    student_map = {s.id: s for s in students}

    student_list = []
    for r in records:
        student = student_map.get(r.student_id)
        if student is None:
            # Student may have been removed; still surface the recorded entry.
            name = ""
            roll = ""
        else:
            name = student.name
            roll = student.roll_number
        student_list.append(
            {
                "record_id": r.id,
                "student_id": r.student_id,
                "roll_number": roll,
                "name": name,
                "attendance_status": r.attendance_status,
                "remarks": r.remarks,
            }
        )

    return {
        "id": session.id,
        "class_": {
            "id": session.school_class.id,
            "name": session.school_class.name,
        },
        "division": {"id": session.division.id, "name": session.division.name},
        "attendance_date": session.attendance_date,
        "taken_by": session.taker.full_name,
        "status": session.status,
        "total_students": session.total_students,
        "present_count": session.present_count,
        "absent_count": session.absent_count,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "students": student_list,
    }


def list_sessions(
    db: Session,
    *,
    class_id: Optional[int] = None,
    division_id: Optional[int] = None,
    attendance_date: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    division_map: Optional[dict[int, set[int]]] = None,
) -> List[dict]:
    """Return attendance history summaries, newest first, with optional filters."""
    query = db.query(AttendanceSession)

    if class_id is not None:
        query = query.filter(AttendanceSession.class_id == class_id)
    if division_id is not None:
        query = query.filter(AttendanceSession.division_id == division_id)
    if attendance_date is not None:
        query = query.filter(AttendanceSession.attendance_date == attendance_date)
    if start_date is not None:
        query = query.filter(AttendanceSession.attendance_date >= start_date)
    if end_date is not None:
        query = query.filter(AttendanceSession.attendance_date <= end_date)
    if division_map is not None:
        if not division_map:
            return []
        pair_conditions = [
            (AttendanceSession.class_id == cid)
            & (AttendanceSession.division_id.in_(divs))
            for cid, divs in division_map.items()
        ]
        query = query.filter(or_(*pair_conditions))

    sessions = query.order_by(
        AttendanceSession.attendance_date.desc(),
        AttendanceSession.id.desc(),
    ).all()

    return [
        {
            "id": s.id,
            "attendance_date": s.attendance_date,
            "class_name": s.school_class.name,
            "division_name": s.division.name,
            "total_students": s.total_students,
            "present_count": s.present_count,
            "absent_count": s.absent_count,
            "status": s.status,
            "taken_by": s.taker.full_name,
        }
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------
def update_attendance(
    db: Session,
    session: AttendanceSession,
    *,
    updates: List[dict],
) -> AttendanceSession:
    """Update the status/remarks of student attendance records in a session.

    The session's identity (class, division, date) is never changed. Counts
    are recalculated from the full set of records. Submitted student ids must
    belong to this session and must not contain duplicates. A single
    transaction wraps the entire update.
    """
    records = _records_for_session(db, session.id)
    record_by_student = {r.student_id: r for r in records}
    on_leave = on_leave_student_ids(
        db, session.class_id, session.division_id, session.attendance_date
    )

    seen = set()
    for upd in updates:
        sid = upd["student_id"]
        if sid in seen:
            raise _http(
                400, f"Student id {sid} appears more than once in the update"
            )
        seen.add(sid)
        if sid in on_leave:
            raise _http(400, ON_LEAVE_MESSAGE.format(sid=sid))
        record = record_by_student.get(sid)
        if record is None:
            raise _http(
                400,
                f"Student id {sid} does not belong to this attendance session",
            )
        # V2.5: Include record_id for message cancellation tracking.
        upd["record_id"] = record.id
        record.attendance_status = upd["status"]
        record.remarks = upd.get("remarks")
        # Touch updated_at via onupdate on commit.

    present_count = sum(
        1 for r in records if r.attendance_status == AttendanceStatus.PRESENT
    )
    session.present_count = present_count
    session.absent_count = len(records) - present_count
    session.total_students = len(records)
    session.updated_at = datetime.utcnow()

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(409, "Could not update attendance")

    db.refresh(session)
    return session
