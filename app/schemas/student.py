from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.phone import normalize_phone_number


class StudentCreate(BaseModel):
    class_id: int
    division_id: int
    name: str = Field(..., min_length=1, max_length=100)
    roll_number: str = Field(..., min_length=1, max_length=30)
    parent_name: str = Field(..., min_length=1, max_length=100)
    parent_whatsapp_number: str = Field(..., min_length=1, max_length=20)

    @field_validator("name", "parent_name")
    @classmethod
    def _strip_required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value

    @field_validator("roll_number")
    @classmethod
    def _strip_roll_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Roll number cannot be empty")
        return value

    @field_validator("parent_whatsapp_number")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        return normalize_phone_number(value)


class StudentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    roll_number: Optional[str] = Field(None, min_length=1, max_length=30)
    parent_name: Optional[str] = Field(None, min_length=1, max_length=100)
    parent_whatsapp_number: Optional[str] = Field(None, min_length=1, max_length=20)
    class_id: Optional[int] = None
    division_id: Optional[int] = None

    @field_validator("name", "parent_name")
    @classmethod
    def _strip_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value

    @field_validator("roll_number")
    @classmethod
    def _strip_optional_roll(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Roll number cannot be empty")
        return value

    @field_validator("parent_whatsapp_number")
    @classmethod
    def _validate_optional_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return normalize_phone_number(value)


class StudentClassBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class StudentDivisionBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    name: str
    roll_number: str
    parent_name: str
    parent_whatsapp_number: str
    class_: StudentClassBrief = Field(alias="class")
    division: StudentDivisionBrief
    is_active: bool
    created_at: datetime
    updated_at: datetime


class StudentRestoreResponse(BaseModel):
    message: str
    id: int
