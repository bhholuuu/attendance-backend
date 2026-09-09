from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.calendar_event import CalendarEventType, HolidayType


class CalendarEventCreate(BaseModel):
    academic_year_id: int
    title: str = Field(min_length=1, max_length=200)
    event_type: CalendarEventType = CalendarEventType.HOLIDAY
    holiday_type: Optional[HolidayType] = None
    start_date: date
    end_date: date
    description: Optional[str] = Field(None, max_length=500)
    is_recurring: bool = False


class CalendarEventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    event_type: Optional[CalendarEventType] = None
    holiday_type: Optional[HolidayType] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    description: Optional[str] = Field(None, max_length=500)
    is_recurring: Optional[bool] = None


class CalendarEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    academic_year_id: int
    title: str
    event_type: CalendarEventType
    holiday_type: Optional[HolidayType] = None
    start_date: date
    end_date: date
    description: Optional[str] = None
    is_recurring: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class CalendarEventListResponse(BaseModel):
    items: list[CalendarEventResponse]