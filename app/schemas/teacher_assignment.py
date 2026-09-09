from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.auth import UserSummary
from app.schemas.school_class import ClassResponse, DivisionResponse


class TeacherAssignmentCreate(BaseModel):
    teacher_id: int
    class_id: int
    # None => ALL active divisions of the class.
    division_id: Optional[int] = None

    @field_validator("teacher_id", "class_id")
    @classmethod
    def _positive_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("id must be a positive integer")
        return value


class TeacherAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    teacher_id: int
    class_id: int
    division_id: Optional[int] = None
    created_by: int
    created_at: datetime
    updated_at: datetime


class TeacherAssignmentFull(BaseModel):
    """Assignment with nested teacher/class/division for an admin management UI."""

    id: int
    teacher_id: int
    class_id: int
    division_id: Optional[int] = None
    created_at: datetime
    teacher: UserSummary
    school_class: ClassResponse
    division: Optional[DivisionResponse] = None


class AssignmentListResponse(BaseModel):
    items: list[TeacherAssignmentResponse]
    total: int


class AssignmentFullListResponse(BaseModel):
    items: list[TeacherAssignmentFull]
    total: int
