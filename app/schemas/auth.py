from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.user import UserRole


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserSummary"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    username: str
    role: UserRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    username: str
    phone_number: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenPayload(BaseModel):
    sub: Optional[str] = None
    role: Optional[UserRole] = None
