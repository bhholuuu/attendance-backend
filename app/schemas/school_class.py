from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClassCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip_and_validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Class name cannot be empty")
        return value


class ClassUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip_and_validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Class name cannot be empty")
        return value


class ClassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DivisionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _strip_and_validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Division name cannot be empty")
        return value


class DivisionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _strip_and_validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Division name cannot be empty")
        return value


class DivisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    class_id: int
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DivisionWithClass(BaseModel):
    """Division details including basic parent class information."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool

    # Nested parent class reference
    school_class: ClassResponse


class ClassDetailResponse(BaseModel):
    """Class details including its (active) divisions."""

    id: int
    name: str
    is_active: bool
    divisions: List[DivisionResponse] = []
