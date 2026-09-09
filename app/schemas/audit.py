"""V2.6 audit log schemas (admin-only read endpoints)."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    correlation_id: str
    actor_user_id: Optional[int] = None
    actor_username: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    result: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    entity_label: Optional[str] = None
    ip_address: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int