"""Alert schemas for Management API"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class AlertBase(BaseModel):
    """Base alert fields"""
    league_id: int
    source_bbs_index: str
    dest_bbs_index: str
    expected_sequence: int
    received_sequence: int
    gap_size: int


class AlertResponse(AlertBase):
    """Alert response"""
    id: int
    league_name: Optional[str] = None
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    is_resolved: bool
    description: Optional[str] = None
    resolution_note: Optional[str] = None

    class Config:
        from_attributes = True


class AlertResolveRequest(BaseModel):
    """Request to resolve an alert"""
    resolution_note: Optional[str] = None


class AlertResolveResponse(BaseModel):
    """Response from resolving an alert"""
    success: bool
    message: str


class AlertListResponse(BaseModel):
    """List of alerts"""
    alerts: List[AlertResponse]
    total: int
    unresolved: int
