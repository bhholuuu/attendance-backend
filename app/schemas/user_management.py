"""V2.6 user management schemas (admin-only).

A minimal, admin-only user management surface required by the audit logging
spec (USER_CREATED / USER_UPDATED / USER_DISABLED / USER_ENABLED /
PASSWORD_CHANGED events). There is intentionally no public registration.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole


_PHONE_RE = r"^\+?[0-9\s-]{7,20}$"


class UserCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    username: str = Field(
        ..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    password: str = Field(..., min_length=8, max_length=128)
    phone_number: Optional[str] = None
    role: UserRole = UserRole.TEACHER

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        import re

        if not re.match(_PHONE_RE, value.strip()):
            raise ValueError(
                "Phone number must be 7-20 characters and may contain "
                "digits, spaces, dashes and a leading +"
            )
        return value.strip()


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    phone_number: Optional[str] = None
    role: Optional[UserRole] = None

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        import re

        if not re.match(_PHONE_RE, value.strip()):
            raise ValueError(
                "Phone number must be 7-20 characters and may contain "
                "digits, spaces, dashes and a leading +"
            )
        return value.strip()


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


class UserManagementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    username: str
    phone_number: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserManagementListResponse(BaseModel):
    items: list[UserManagementResponse]