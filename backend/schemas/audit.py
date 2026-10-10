"""Audit log schemas"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AuditEventInfo(BaseModel):
    """One audit log entry. actor is None for the hub itself or a signed-out visitor."""
    id: int
    at: datetime
    actor: Optional[str] = None
    actor_user_id: Optional[int] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    target: Optional[str] = None
    detail: Optional[str] = None
    ip: Optional[str] = None
