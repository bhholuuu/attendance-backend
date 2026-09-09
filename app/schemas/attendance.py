from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.attendance import AttendanceStatus, SessionStatus


class AttendanceRecordInput(BaseModel):
    """A single student's attendance entry submitted when saving a session."""

    student_id: int
    status: AttendanceStatus
    remarks: Optional[str] = Field(None, max_length=500)


class AttendanceSaveRequest(BaseModel):
    class_id: int
    division_id: int
    attendance_date: date
    students: List[AttendanceRecordInput] = Field(min_length=1)


class AttendanceUpdateItem(BaseModel):
    """A single student's updated attendance status/remarks for a session."""

    student_id: int
    status: AttendanceStatus
    remarks: Optional[str] = Field(None, max_length=500)


class ClassBrief(BaseModel):
    id: int
    name: str


class DivisionBrief(BaseModel):
    id: int
    name: str


class AttendeeBrief(BaseModel):
    """A student shown in attendance-taking/preparation data."""

    student_id: int
    roll_number: str
    name: str
    status: AttendanceStatus
    on_leave: bool = False


class AttendancePrepareResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    existing_session: bool
    session_id: Optional[int] = None
    status: Optional[SessionStatus] = None
    class_: ClassBrief = Field(alias="class")
    division: DivisionBrief
    attendance_date: date
    is_holiday: bool = False
    holiday_title: Optional[str] = None
    students: List[AttendeeBrief]


class StudentAttendanceRecord(BaseModel):
    """A student attendance record within a session detail response."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    record_id: int
    student_id: int
    roll_number: str
    name: str
    attendance_status: AttendanceStatus
    remarks: Optional[str] = None


class AttendanceSessionDetail(BaseModel):
    """Full detail of a single attendance session."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    class_: ClassBrief = Field(alias="class")
    division: DivisionBrief
    attendance_date: date
    taken_by: str
    status: SessionStatus
    total_students: int
    present_count: int
    absent_count: int
    created_at: datetime
    updated_at: datetime
    students: List[StudentAttendanceRecord]


class AttendanceSessionSummary(BaseModel):
    """Summary row used by attendance history/list endpoints."""

    id: int
    attendance_date: date
    class_name: str
    division_name: str
    total_students: int
    present_count: int
    absent_count: int
    status: SessionStatus
    taken_by: str


class AttendanceListResponse(BaseModel):
    items: List[AttendanceSessionSummary]


class AttendanceSyncRecordInput(BaseModel):
    """A single record submitted by a device in an offline-sync batch (V2.4)."""

    client_record_id: UUID
    student_id: int
    status: AttendanceStatus
    remarks: Optional[str] = Field(None, max_length=500)


class AttendanceSyncRequest(BaseModel):
    """The entire payload a device uploads for one attendance session (V2.4).

    The session's identity (class, division, date plus the stable
    ``client_session_id``) travels with the records so the server can apply the
    batch atomically and detect duplicates/conflicts idempotently.
    """

    client_session_id: UUID
    class_id: int
    division_id: int
    attendance_date: date
    records: List[AttendanceSyncRecordInput] = Field(min_length=1)


class AttendanceSyncItemResult(BaseModel):
    """Per-record result of a sync batch (V2.4)."""

    client_record_id: UUID
    # 'ACCEPTED' | 'ALREADY_SYNCED' | 'CONFLICT' | 'REJECTED'
    status: str
    # Server record id once accepted (null on conflict/rejection).
    record_id: Optional[int] = None
    reason: Optional[str] = None


class AttendanceSyncResponse(BaseModel):
    """Result of applying an offline-attendance sync batch (V2.4).

    The server is authoritative. A conflict does not auto-overwrite: the server
    session travels back (``session``) so the device can present both sides and
    let the user resolve manually.
    """

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool
    already_synced: bool = False
    conflict: bool = False
    message: str
    session: Optional[AttendanceSessionDetail] = None
    items: List[AttendanceSyncItemResult] = []
