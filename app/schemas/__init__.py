from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    TokenPayload,
    UserOut,
    UserSummary,
)
from app.schemas.school_class import (
    ClassCreate,
    ClassUpdate,
    ClassResponse,
    ClassDetailResponse,
    DivisionCreate,
    DivisionUpdate,
    DivisionResponse,
    DivisionWithClass,
)
from app.schemas.student import (
    StudentCreate,
    StudentUpdate,
    StudentResponse,
    StudentClassBrief,
    StudentDivisionBrief,
    StudentRestoreResponse,
)
from app.schemas.teacher_assignment import (
    TeacherAssignmentCreate,
    TeacherAssignmentResponse,
    TeacherAssignmentFull,
    AssignmentListResponse,
    AssignmentFullListResponse,
)
from app.schemas.dashboard import (
    AttendanceSummary,
    StudentsSummary,
    MessagingSummary,
    AttendanceSummaryResponse,
)

__all__ = [
    "LoginRequest",
    "TokenResponse",
    "TokenPayload",
    "UserOut",
    "UserSummary",
    "ClassCreate",
    "ClassUpdate",
    "ClassResponse",
    "ClassDetailResponse",
    "DivisionCreate",
    "DivisionUpdate",
    "DivisionResponse",
    "DivisionWithClass",
    "StudentCreate",
    "StudentUpdate",
    "StudentResponse",
    "StudentClassBrief",
    "StudentDivisionBrief",
    "StudentRestoreResponse",
    "TeacherAssignmentCreate",
    "TeacherAssignmentResponse",
    "TeacherAssignmentFull",
    "AssignmentListResponse",
    "AssignmentFullListResponse",
    "AttendanceSummary",
    "StudentsSummary",
    "MessagingSummary",
    "AttendanceSummaryResponse",
]
