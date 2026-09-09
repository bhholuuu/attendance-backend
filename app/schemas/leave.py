from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.student_leave import LeaveStatus


class StudentLeaveCreate(BaseModel):
    student_id: int
    start_date: date
    end_date: date
    leave_type: Optional[str] = Field(None, max_length=100)
    reason: Optional[str] = Field(None, max_length=500)


class StudentLeaveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    student_name: str
    roll_number: str
    class_name: str
    division_name: str
    start_date: date
    end_date: date
    leave_type: Optional[str] = None
    reason: Optional[str] = None
    status: LeaveStatus
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    created_by: int
    created_at: datetime
    updated_at: datetime


class StudentLeaveListResponse(BaseModel):
    items: list[StudentLeaveResponse]