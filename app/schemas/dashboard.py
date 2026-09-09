from datetime import date as _date
from typing import Optional

from pydantic import BaseModel

from app.models.user import UserRole


class AttendanceSummary(BaseModel):
    total_expected: int
    completed: int
    pending: int


class StudentsSummary(BaseModel):
    present: int
    absent: int
    on_leave: int = 0


class MessagingSummary(BaseModel):
    pending_batches: int
    failed_messages: int


class AttendanceSummaryResponse(BaseModel):
    date: _date
    role: UserRole
    attendance: AttendanceSummary
    students: StudentsSummary
    messaging: MessagingSummary
    is_holiday: bool = False
    holiday_title: Optional[str] = None


def present_percentage(total_marked: int, present: int) -> Optional[float]:
    """Safe percent from raw counts; None when nothing was marked (no div-by-0)."""
    if total_marked <= 0:
        return None
    return round(present / total_marked * 100, 1)
