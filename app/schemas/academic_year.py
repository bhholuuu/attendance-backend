from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AcademicYearCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Year name, e.g. 2026-2027")
    start_date: date
    end_date: date
    is_active: bool = True


class AcademicYearUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None


class AcademicYearResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    start_date: date
    end_date: date
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime