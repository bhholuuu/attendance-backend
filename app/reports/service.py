"""Attendance reporting service (V2.2).

A single central service that computes all attendance reports efficiently using
SQL aggregation (COUNT / SUM / GROUP BY / JOIN) so we never load whole
attendance tables into Python memory.

Authorization is NOT duplicated here: routers call ``TeacherAccessService``
first (which raises 403 for unassigned areas). The service itself still accepts
a scoping ``division_map`` for TEACHERS so multi-entity reports (daily/summary)
only aggregate over the teacher's assigned (class, division) pairs.

Percentage definitions (matching the project spec):
  * Student percentage   = present_days / (present_days + absent_days) * 100
  * Class/aggregate pct  = total_present / (total_present + total_absent) * 100
Days where attendance was never recorded are not counted as ABSENT.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import case, exists, func, tuple_
from sqlalchemy.orm import Session

from app.models.attendance import (
    AttendanceSession,
    AttendanceStatus,
    EffectiveAttendanceStatus,
    SessionStatus,
    StudentAttendance,
)
from app.models.school_class import Division, SchoolClass
from app.models.student import Student
from app.models.student_leave import LeaveStatus, StudentLeave
from app.models.user import User
from app.services import effective_attendance_service, teacher_access_service

MAX_REPORT_DATE_RANGE_DAYS = 365


class ReportError(HTTPException):
    """Base HTTP error for report validation (422 by default)."""

    def __init__(self, detail: str, status_code: int = status.HTTP_422_UNPROCESSABLE_CONTENT):
        super().__init__(status_code=status_code, detail=detail)


def _percentage(present: int, absent: int) -> Optional[float]:
    """Return percentage with the project's definition; None when no records."""
    marked = present + absent
    if marked <= 0:
        return None
    return round((present / marked) * 100, 2)


def _div_pairs(division_map: dict) -> List[Tuple[int, int]]:
    return [(c, d) for c, divs in division_map.items() for d in divs]


class AttendanceReportService:
    """Encapsulates report generation for student/class/division/daily/summary."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def validate_date_range(
        self, start_date: date, end_date: date, max_days: int = MAX_REPORT_DATE_RANGE_DAYS
    ) -> None:
        if start_date > end_date:
            raise ReportError("start_date must not be after end_date")
        if (end_date - start_date).days > max_days:
            raise ReportError(
                f"Date range exceeds the maximum allowed {max_days} days"
            )

    def _require_class(self, class_id: int) -> SchoolClass:
        school_class = (
            self.db.query(SchoolClass).filter(SchoolClass.id == class_id).first()
        )
        if school_class is None:
            raise ReportError("Class not found", status.HTTP_404_NOT_FOUND)
        return school_class

    def _require_division(
        self, division_id: int, class_id: Optional[int]
    ) -> Division:
        division = self.db.query(Division).filter(Division.id == division_id).first()
        if division is None:
            raise ReportError("Division not found", status.HTTP_404_NOT_FOUND)
        if class_id is not None and division.class_id != class_id:
            raise ReportError("Division does not belong to the specified class")
        return division

    # ------------------------------------------------------------------
    # Scope helpers
    # ------------------------------------------------------------------
    def _school_division_pairs(
        self, class_id: Optional[int], division_id: Optional[int]
    ) -> List[Tuple[int, int]]:
        """All active (class, division) pairs for an ADMIN report."""
        query = self.db.query(Division.class_id, Division.id).filter(
            Division.is_active.is_(True)
        )
        if class_id is not None:
            query = query.filter(Division.class_id == class_id)
        if division_id is not None:
            query = query.filter(Division.id == division_id)
        return [(c, d) for c, d in query.all()]

    def _teacher_pairs_in_scope(
        self,
        user: User,
        class_id: Optional[int],
        division_id: Optional[int],
    ) -> List[Tuple[int, int]]:
        """Assigned (class, division) pairs for a TEACHER, honoring filters."""
        division_map = teacher_access_service.get_allowed_divisions_by_class(
            self.db, user
        )
        pairs = _div_pairs(division_map)
        if class_id is not None:
            pairs = [(c, d) for (c, d) in pairs if c == class_id]
        if division_id is not None:
            pairs = [(c, d) for (c, d) in pairs if d == division_id]
        return pairs

    # ------------------------------------------------------------------
    # Student report
    # ------------------------------------------------------------------
    def get_student_report(
        self,
        user: User,
        student_id: int,
        start_date: date,
        end_date: date,
        include_daily: bool = True,
    ) -> dict:
        self.validate_date_range(start_date, end_date)
        student = self.db.query(Student).filter(Student.id == student_id).first()
        if student is None:
            raise ReportError("Student not found", status.HTTP_404_NOT_FOUND)

        teacher_access_service.require_access_division(
            self.db, user, student.class_id, student.division_id
        )

        holidays = effective_attendance_service.holiday_dates_in_range(
            self.db, start_date, end_date
        )
        leave_dates = effective_attendance_service.approved_leave_dates_by_student(
            self.db, [student_id], start_date, end_date
        ).get(student_id, set())

        daily = self._student_daily_records(student_id, start_date, end_date)
        present_days = 0
        absent_days = 0
        effective_records = []
        for rec in daily:
            day = rec["date"]
            eff = effective_attendance_service.effective_status(
                rec.get("recorded_status"),
                day in holidays,
                day in leave_dates,
            )
            rec["status"] = eff
            effective_records.append(rec)
            if eff == EffectiveAttendanceStatus.PRESENT:
                present_days += 1
            elif eff == EffectiveAttendanceStatus.ABSENT:
                absent_days += 1
        total_days = present_days + absent_days

        result = {
            "student_id": student.id,
            "student_name": student.name,
            "roll_number": student.roll_number,
            "class_name": student.school_class.name,
            "division_name": student.division.name,
            "start_date": start_date,
            "end_date": end_date,
            "total_attendance_days": total_days,
            "present_days": present_days,
            "absent_days": absent_days,
            "attendance_percentage": _percentage(present_days, absent_days) or 0.0,
            "daily_records": [],
        }

        if include_daily:
            result["daily_records"] = effective_records
        return result

    def _student_daily_records(
        self, student_id: int, start_date: date, end_date: date
    ) -> List[dict]:
        rows = (
            self.db.query(
                AttendanceSession.attendance_date,
                StudentAttendance.attendance_status,
                AttendanceSession.class_id,
                AttendanceSession.division_id,
            )
            .join(
                AttendanceSession,
                StudentAttendance.attendance_session_id == AttendanceSession.id,
            )
            .filter(
                StudentAttendance.student_id == student_id,
                AttendanceSession.attendance_date.between(start_date, end_date),
            )
            .order_by(AttendanceSession.attendance_date.asc())
            .all()
        )
        # Bulk-load class/division names once.
        class_ids = {r[2] for r in rows}
        div_ids = {r[3] for r in rows}
        class_map = {
            c.id: c.name
            for c in self.db.query(SchoolClass).filter(SchoolClass.id.in_(class_ids)).all()
        }
        div_map = {
            d.id: d.name
            for d in self.db.query(Division).filter(Division.id.in_(div_ids)).all()
        }
        return [
            {
                "date": d,
                "recorded_status": st,
                "status": st,
                "class_name": class_map.get(cid, ""),
                "division_name": div_map.get(did, ""),
            }
            for d, st, cid, did in rows
        ]

    # ------------------------------------------------------------------
    # Class / division report
    # ------------------------------------------------------------------
    def get_class_report(
        self,
        user: User,
        class_id: int,
        start_date: date,
        end_date: date,
        division_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        self.validate_date_range(start_date, end_date)
        school_class = self._require_class(class_id)
        teacher_access_service.require_access_class(self.db, user, class_id)

        division = None
        if division_id is not None:
            division = self._require_division(division_id, class_id)
            teacher_access_service.require_access_division(
                self.db, user, class_id, division_id
            )

        pairs = self._class_pairs(class_id, division_id)
        holidays, corrections = self._corrections(pairs, start_date, end_date)
        agg = self._aggregate_sessions(
            pairs, start_date, end_date, corrections=corrections
        )

        total_students = self._active_students_count(class_id, division_id)

        # Student rollup table.
        student_rows = self._student_rollup(
            pairs, start_date, end_date, page, page_size, corrections=corrections
        )

        return {
            "class_name": school_class.name,
            "division_name": division.name if division else None,
            "start_date": start_date,
            "end_date": end_date,
            "total_students": total_students,
            "total_attendance_sessions": agg["sessions"],
            "total_present_records": agg["present"],
            "total_absent_records": agg["absent"],
            "average_attendance_percentage": _percentage(
                agg["present"], agg["absent"]
            ),
            "daily_summary": self._session_daily_summary(
                pairs, start_date, end_date, corrections=corrections
            ),
            "student_summary": student_rows,
        }

    def get_division_report(
        self,
        user: User,
        class_id: int,
        division_id: int,
        start_date: date,
        end_date: date,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        self.validate_date_range(start_date, end_date)
        school_class = self._require_class(class_id)
        division = self._require_division(division_id, class_id)
        teacher_access_service.require_access_division(
            self.db, user, class_id, division_id
        )

        pairs = [(class_id, division_id)]
        holidays, corrections = self._corrections(pairs, start_date, end_date)
        agg = self._aggregate_sessions(
            pairs, start_date, end_date, corrections=corrections
        )
        total_students = self._active_students_count(class_id, division_id)
        student_rows = self._student_rollup(
            pairs, start_date, end_date, page, page_size, corrections=corrections
        )

        return {
            "class_name": school_class.name,
            "division_name": division.name,
            "start_date": start_date,
            "end_date": end_date,
            "total_students": total_students,
            "total_attendance_sessions": agg["sessions"],
            "total_present_records": agg["present"],
            "total_absent_records": agg["absent"],
            "average_attendance_percentage": _percentage(
                agg["present"], agg["absent"]
            ),
            "daily_summary": self._session_daily_summary(
                pairs, start_date, end_date, corrections=corrections
            ),
            "student_summary": student_rows,
        }

    # ------------------------------------------------------------------
    # Daily report
    # ------------------------------------------------------------------
    def get_daily_report(
        self,
        user: User,
        report_date: date,
        class_id: Optional[int] = None,
        division_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        if class_id is not None:
            self._require_class(class_id)
            teacher_access_service.require_access_class(self.db, user, class_id)
        if division_id is not None:
            self._require_division(division_id, class_id)
            if class_id is not None:
                teacher_access_service.require_access_division(
                    self.db, user, class_id, division_id
                )

        if teacher_access_service.is_admin(user):
            pairs = self._school_division_pairs(class_id, division_id)
        else:
            pairs = self._teacher_pairs_in_scope(user, class_id, division_id)

        holidays, corrections = self._corrections(pairs, report_date, report_date)
        items = self._daily_rows(pairs, report_date, corrections=corrections)
        return {"date": report_date, **self._paginate(items, page, page_size)}

    # ------------------------------------------------------------------
    # Date range summary report
    # ------------------------------------------------------------------
    def get_summary_report(
        self,
        user: User,
        start_date: date,
        end_date: date,
        class_id: Optional[int] = None,
        division_id: Optional[int] = None,
    ) -> dict:
        self.validate_date_range(start_date, end_date)
        if class_id is not None:
            self._require_class(class_id)
            teacher_access_service.require_access_class(self.db, user, class_id)
        if division_id is not None:
            self._require_division(division_id, class_id)
            if class_id is not None:
                teacher_access_service.require_access_division(
                    self.db, user, class_id, division_id
                )

        is_admin = teacher_access_service.is_admin(user)
        if is_admin:
            pairs = self._school_division_pairs(class_id, division_id)
        else:
            pairs = self._teacher_pairs_in_scope(user, class_id, division_id)

        holidays, corrections = self._corrections(pairs, start_date, end_date)
        agg = self._aggregate_sessions(
            pairs, start_date, end_date, corrections=corrections
        )
        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_attendance_sessions": agg["sessions"],
            "total_students_marked": agg["present"] + agg["absent"],
            "present_records": agg["present"],
            "absent_records": agg["absent"],
            "average_attendance_percentage": _percentage(
                agg["present"], agg["absent"]
            ),
            "daily_trend": self._daily_trend(
                pairs, start_date, end_date, corrections=corrections
            ),
        }

    # ------------------------------------------------------------------
    # Internal aggregation helpers
    # ------------------------------------------------------------------
    def _class_pairs(
        self, class_id: int, division_id: Optional[int]
    ) -> List[Tuple[int, int]]:
        query = self.db.query(Division.class_id, Division.id).filter(
            Division.class_id == class_id,
            Division.is_active.is_(True),
        )
        if division_id is not None:
            query = query.filter(Division.id == division_id)
        return [(c, d) for c, d in query.all()]

    def _active_students_count(
        self, class_id: int, division_id: Optional[int]
    ) -> int:
        query = self.db.query(func.count(Student.id)).filter(
            Student.class_id == class_id,
            Student.is_active.is_(True),
        )
        if division_id is not None:
            query = query.filter(Student.division_id == division_id)
        return int(query.scalar() or 0)

    # ------------------------------------------------------------------
    # V2.3 effective-status corrections
    # ------------------------------------------------------------------
    @staticmethod
    def _approved_leave_exists():
        """SQL EXISTS condition: a record whose student had APPROVED leave
        covering the session's date. Uses EXISTS so overlapping leaves can
        never multiply row counts."""
        return exists().where(
            StudentLeave.student_id == StudentAttendance.student_id,
            StudentLeave.status == LeaveStatus.APPROVED,
            AttendanceSession.attendance_date >= StudentLeave.start_date,
            AttendanceSession.attendance_date <= StudentLeave.end_date,
        )

    def _corrections(
        self,
        pairs: List[Tuple[int, int]],
        start_date: date,
        end_date: date,
    ) -> Tuple[set, dict]:
        """Records that qualify as APPROVED_LEAVE or HOLIDAY.

        Effective status precedence (HOLIDAY > APPROVED_LEAVE > recorded):
        a stored PRESENT/ABSENT record is excluded from present/absent counts
        when the session's date is a holiday OR the student had approved leave
        on that date. Historical rows are never modified.

        Returns ``(holiday_dates, corrections)`` where ``corrections`` maps:
          by_student:   {(student_id, AttendanceStatus): count}
          by_date:      {(date, AttendanceStatus): count}
          by_pair_date: {(class_id, division_id, date, AttendanceStatus): count}
        Leave-covered records on holiday dates are attributed to the holiday
        bucket only (queried with the leave branch excluding holiday dates), so
        nothing is double subtracted.
        """
        holidays = effective_attendance_service.holiday_dates_in_range(
            self.db, start_date, end_date
        )
        corrections = {
            "by_student": defaultdict(int),
            "by_date": defaultdict(int),
            "by_pair_date": defaultdict(int),
        }
        if not pairs:
            return holidays, corrections

        group_by = (
            AttendanceSession.class_id,
            AttendanceSession.division_id,
            AttendanceSession.attendance_date,
            StudentAttendance.student_id,
            StudentAttendance.attendance_status,
        )

        def _consume(query, holidays_to_exclude):
            rows = query.group_by(*group_by).all()
            for cid, did, day, student_id, st, cnt in rows:
                cnt = int(cnt)
                st = AttendanceStatus(st)
                corrections["by_student"][(student_id, st)] += cnt
                corrections["by_date"][(day, st)] += cnt
                corrections["by_pair_date"][(cid, did, day, st)] += cnt

        base = (
            self.db.query(
                AttendanceSession.class_id,
                AttendanceSession.division_id,
                AttendanceSession.attendance_date,
                StudentAttendance.student_id,
                StudentAttendance.attendance_status,
                func.count(StudentAttendance.id),
            )
            .join(
                AttendanceSession,
                StudentAttendance.attendance_session_id == AttendanceSession.id,
            )
            .filter(
                AttendanceSession.attendance_date.between(start_date, end_date),
                tuple_(
                    AttendanceSession.class_id, AttendanceSession.division_id
                ).in_(pairs),
            )
        )

        # Branch 1: approved-leave coverage (skip holiday dates).
        leave_query = base.filter(self._approved_leave_exists())
        if holidays:
            leave_query = leave_query.filter(
                ~AttendanceSession.attendance_date.in_(holidays)
            )
        _consume(leave_query, None)

        # Branch 2: historical records that landed on a holiday.
        if holidays:
            holiday_query = base.filter(
                AttendanceSession.attendance_date.in_(holidays)
            )
            _consume(holiday_query, None)

        return holidays, corrections

    def _aggregate_sessions(
        self,
        pairs: List[Tuple[int, int]],
        start_date: date,
        end_date: date,
        corrections: Optional[dict] = None,
    ) -> dict:
        if not pairs:
            return {"sessions": 0, "present": 0, "absent": 0}
        row = (
            self.db.query(
                func.count(AttendanceSession.id),
                func.coalesce(
                    func.sum(AttendanceSession.present_count), 0
                ),
                func.coalesce(
                    func.sum(AttendanceSession.absent_count), 0
                ),
            )
            .filter(
                AttendanceSession.status == SessionStatus.COMPLETED,
                AttendanceSession.attendance_date.between(start_date, end_date),
                tuple_(
                    AttendanceSession.class_id, AttendanceSession.division_id
                ).in_(pairs),
            )
            .one()
        )
        present = int(row[1])
        absent = int(row[2])
        if corrections:
            by_date = corrections.get("by_date") or {}
            for (day, st), n in by_date.items():
                if st == AttendanceStatus.PRESENT:
                    present -= n
                elif st == AttendanceStatus.ABSENT:
                    absent -= n
        return {"sessions": int(row[0]), "present": present, "absent": absent}

    def _session_daily_summary(
        self,
        pairs: List[Tuple[int, int]],
        start_date: date,
        end_date: date,
        corrections: Optional[dict] = None,
    ) -> List[dict]:
        if not pairs:
            return []
        rows = (
            self.db.query(
                AttendanceSession.attendance_date,
                func.coalesce(func.sum(AttendanceSession.present_count), 0),
                func.coalesce(func.sum(AttendanceSession.absent_count), 0),
                func.coalesce(func.sum(AttendanceSession.total_students), 0),
            )
            .filter(
                AttendanceSession.status == SessionStatus.COMPLETED,
                AttendanceSession.attendance_date.between(start_date, end_date),
                tuple_(
                    AttendanceSession.class_id, AttendanceSession.division_id
                ).in_(pairs),
            )
            .group_by(AttendanceSession.attendance_date)
            .order_by(AttendanceSession.attendance_date.asc())
            .all()
        )
        by_date = (corrections or {}).get("by_date") or {}
        return [
            {
                "date": d,
                "class_name": "",
                "division_name": "",
                "total_students": int(total),
                "present": int(p) - by_date.get((d, AttendanceStatus.PRESENT), 0),
                "absent": int(a) - by_date.get((d, AttendanceStatus.ABSENT), 0),
                "percentage": _percentage(
                    int(p) - by_date.get((d, AttendanceStatus.PRESENT), 0),
                    int(a) - by_date.get((d, AttendanceStatus.ABSENT), 0),
                ),
                "completion_status": "COMPLETED"
                if (int(p) + int(a)) > 0
                else "NOT_TAKEN",
            }
            for d, p, a, total in rows
        ]

    def _daily_rows(
        self,
        pairs: List[Tuple[int, int]],
        report_date: date,
        corrections: Optional[dict] = None,
    ) -> List[dict]:
        if not pairs:
            return []
        by_pair_date = (corrections or {}).get("by_pair_date") or {}
        # One session per (class, division) pair is guaranteed by the unique
        # constraint on class+division+date, so fetch all for the date.
        sessions = (
            self.db.query(AttendanceSession)
            .filter(
                AttendanceSession.attendance_date == report_date,
                tuple_(
                    AttendanceSession.class_id, AttendanceSession.division_id
                ).in_(pairs),
            )
            .all()
        )
        session_by_pair = {(s.class_id, s.division_id): s for s in sessions}

        # Active student counts per pair.
        active_counts = {
            (cid, did): int(cnt)
            for cid, did, cnt in (
                self.db.query(
                    Student.class_id,
                    Student.division_id,
                    func.count(Student.id),
                )
                .filter(
                    Student.is_active.is_(True),
                    tuple_(Student.class_id, Student.division_id).in_(pairs),
                )
                .group_by(Student.class_id, Student.division_id)
                .all()
            )
        }

        # Class/division names.
        class_ids = {c for c, _ in pairs}
        div_ids = {d for _, d in pairs}
        class_map = {
            c.id: c.name
            for c in self.db.query(SchoolClass).filter(SchoolClass.id.in_(class_ids)).all()
        }
        div_map = {
            d.id: d.name
            for d in self.db.query(Division).filter(Division.id.in_(div_ids)).all()
        }

        rows = []
        for cid, did in sorted(pairs, key=lambda p: (class_map.get(p[0], ""), div_map.get(p[1], ""))):
            session = session_by_pair.get((cid, did))
            raw_present = session.present_count if session else 0
            raw_absent = session.absent_count if session else 0
            present = raw_present - by_pair_date.get(
                (cid, did, report_date, AttendanceStatus.PRESENT), 0
            )
            absent = raw_absent - by_pair_date.get(
                (cid, did, report_date, AttendanceStatus.ABSENT), 0
            )
            completion = "COMPLETED" if session is not None else "NOT_TAKEN"
            total_students = (
                session.total_students if session else active_counts.get((cid, did), 0)
            )
            rows.append(
                {
                    "date": report_date,
                    "class_name": class_map.get(cid, ""),
                    "division_name": div_map.get(did, ""),
                    "total_students": int(total_students),
                    "present": int(present),
                    "absent": int(absent),
                    "percentage": _percentage(int(present), int(absent)),
                    "completion_status": completion,
                }
            )
        return rows

    def _daily_trend(
        self,
        pairs: List[Tuple[int, int]],
        start_date: date,
        end_date: date,
        corrections: Optional[dict] = None,
    ) -> List[dict]:
        if not pairs:
            return []
        rows = (
            self.db.query(
                AttendanceSession.attendance_date,
                func.coalesce(func.sum(AttendanceSession.present_count), 0),
                func.coalesce(func.sum(AttendanceSession.absent_count), 0),
            )
            .filter(
                AttendanceSession.status == SessionStatus.COMPLETED,
                AttendanceSession.attendance_date.between(start_date, end_date),
                tuple_(
                    AttendanceSession.class_id, AttendanceSession.division_id
                ).in_(pairs),
            )
            .group_by(AttendanceSession.attendance_date)
            .order_by(AttendanceSession.attendance_date.asc())
            .all()
        )
        by_date = (corrections or {}).get("by_date") or {}
        return [
            {
                "date": d,
                "present": int(p) - by_date.get((d, AttendanceStatus.PRESENT), 0),
                "absent": int(a) - by_date.get((d, AttendanceStatus.ABSENT), 0),
                "percentage": _percentage(
                    int(p) - by_date.get((d, AttendanceStatus.PRESENT), 0),
                    int(a) - by_date.get((d, AttendanceStatus.ABSENT), 0),
                ),
            }
            for d, p, a in rows
        ]

    def _student_rollup(
        self,
        pairs: List[Tuple[int, int]],
        start_date: date,
        end_date: date,
        page: int,
        page_size: int,
        corrections: Optional[dict] = None,
    ) -> dict:
        """Per-student present/absent rollup over the range, paginated."""
        if not pairs:
            return {"items": [], "pagination": {"total": 0, "page": page, "page_size": page_size, "total_pages": 0}}

        base = (
            self.db.query(
                StudentAttendance.student_id,
                func.coalesce(
                    func.sum(
                        case(
                            (
                                StudentAttendance.attendance_status
                                == AttendanceStatus.PRESENT,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                StudentAttendance.attendance_status
                                == AttendanceStatus.ABSENT,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .join(
                AttendanceSession,
                StudentAttendance.attendance_session_id == AttendanceSession.id,
            )
            .filter(
                AttendanceSession.attendance_date.between(start_date, end_date),
                tuple_(
                    AttendanceSession.class_id, AttendanceSession.division_id
                ).in_(pairs),
            )
            .group_by(StudentAttendance.student_id)
        )

        total = base.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        rows = base.order_by(StudentAttendance.student_id).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        student_ids = [r[0] for r in rows]
        students = (
            self.db.query(Student)
            .filter(Student.id.in_(student_ids))
            .all()
            if student_ids
            else []
        )
        student_map = {s.id: s for s in students}

        items = []
        by_student = (corrections or {}).get("by_student") or {}
        for sid, present, absent in rows:
            student = student_map.get(sid)
            p = int(present) - by_student.get((sid, AttendanceStatus.PRESENT), 0)
            a = int(absent) - by_student.get((sid, AttendanceStatus.ABSENT), 0)
            items.append(
                {
                    "student_id": sid,
                    "roll_number": student.roll_number if student else "",
                    "student_name": student.name if student else "",
                    "present_days": p,
                    "absent_days": a,
                    "attendance_percentage": _percentage(p, a) or 0.0,
                }
            )

        # Deterministic order: roll number then name.
        items.sort(key=lambda i: (i["roll_number"], i["student_name"]))
        return {
            "items": items,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
        }

    # ------------------------------------------------------------------
    # Generic pagination for flat lists (daily report rows).
    # ------------------------------------------------------------------
    def _paginate(self, items: List[dict], page: int, page_size: int) -> dict:
        total = len(items)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
        }
