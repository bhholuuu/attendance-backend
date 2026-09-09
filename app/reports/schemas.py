"""Pydantic schemas for the attendance reporting module (V2.2).

These schemas describe the JSON responses for every report endpoint. Services
return plain dicts in exactly this shape; the routers use these models as
``response_model`` so responses are validated and never raw ORM objects.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.attendance import AttendanceStatus, EffectiveAttendanceStatus


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------
class PaginationMeta(BaseModel):
    """Pagination metadata included in any paginated report response."""

    total: int
    page: int = 1
    page_size: int = 50
    total_pages: int = 1


class DailyTrendItem(BaseModel):
    """One row of a date-range daily trend."""

    date: date
    present: int
    absent: int
    percentage: Optional[float] = None


class SessionDailySummaryItem(BaseModel):
    """Attendance summary for a single session (class/division/date)."""

    date: date
    class_name: str
    division_name: str
    total_students: int
    present: int
    absent: int
    percentage: Optional[float] = None
    completion_status: str = "COMPLETED"


# ---------------------------------------------------------------------------
# Student report
# ---------------------------------------------------------------------------
class StudentReportDailyRecord(BaseModel):
    """A single daily attendance record inside a student report.

    ``status`` is the effective status for the day — it already accounts for
    school holidays and approved student leave. ``recorded_status`` preserves
    the raw stored value (None when the day had no session record).
    """

    date: date
    status: EffectiveAttendanceStatus
    recorded_status: Optional[AttendanceStatus] = None
    class_name: str
    division_name: str


class StudentAttendanceReportResponse(BaseModel):
    """Individual student attendance report over a date range."""

    student_id: int
    student_name: str
    roll_number: str
    class_name: str
    division_name: str
    start_date: date
    end_date: date
    total_attendance_days: int
    present_days: int
    absent_days: int
    attendance_percentage: float
    daily_records: List[StudentReportDailyRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Class / Division reports
# ---------------------------------------------------------------------------
class StudentSummaryItem(BaseModel):
    """Per-student rollup inside a class or division report."""

    student_id: int
    roll_number: str
    student_name: str
    present_days: int
    absent_days: int
    attendance_percentage: float


class StudentSummaryPage(BaseModel):
    """Paginated student rollup table."""

    items: List[StudentSummaryItem]
    pagination: PaginationMeta


class ClassAttendanceReportResponse(BaseModel):
    """Class (optionally division-scoped) attendance report."""

    class_name: str
    division_name: Optional[str] = None
    start_date: date
    end_date: date
    total_students: int
    total_attendance_sessions: int
    total_present_records: int
    total_absent_records: int
    average_attendance_percentage: Optional[float] = None
    daily_summary: List[SessionDailySummaryItem]
    student_summary: StudentSummaryPage


class DivisionAttendanceReportResponse(BaseModel):
    """Division-specific attendance report."""

    class_name: str
    division_name: str
    start_date: date
    end_date: date
    total_students: int
    total_attendance_sessions: int
    total_present_records: int
    total_absent_records: int
    average_attendance_percentage: Optional[float] = None
    daily_summary: List[SessionDailySummaryItem]
    student_summary: StudentSummaryPage


# ---------------------------------------------------------------------------
# Daily report
# ---------------------------------------------------------------------------
class DailyAttendanceReportResponse(BaseModel):
    """Report for a single date (school-wide or scoped)."""

    date: date
    items: List[SessionDailySummaryItem]
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Date range summary report
# ---------------------------------------------------------------------------
class AttendanceSummaryReportResponse(BaseModel):
    """Aggregate report for a date range."""

    start_date: date
    end_date: date
    total_attendance_sessions: int
    total_students_marked: int
    present_records: int
    absent_records: int
    average_attendance_percentage: Optional[float] = None
    daily_trend: List[DailyTrendItem]
