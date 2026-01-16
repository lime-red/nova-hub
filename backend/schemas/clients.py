"""Client schemas for Management API"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class ClientCreate(BaseModel):
    """Create a new client"""
    bbs_name: str
    client_id: str


class ClientUpdate(BaseModel):
    """Update a client"""
    bbs_name: Optional[str] = None
    is_active: Optional[bool] = None


class ClientResponse(BaseModel):
    """Client response with basic stats"""
    id: int
    bbs_name: str
    client_id: str
    is_active: bool
    last_seen: Optional[str] = None
    packets_sent_24h: int = 0
    packets_received_24h: int = 0

    class Config:
        from_attributes = True


class ClientCreatedResponse(BaseModel):
    """Response when creating a new client with secret"""
    id: int
    bbs_name: str
    client_id: str
    client_secret: str  # Plain text, shown only once


class ClientSecretResponse(BaseModel):
    """Response when regenerating client secret"""
    client_id: str
    client_secret: str  # Plain text, shown only once


class ClientStats(BaseModel):
    """Client statistics"""
    last_seen: Optional[str] = None
    total_sent: int = 0
    sent_24h: int = 0
    total_received: int = 0
    received_24h: int = 0


class PacketHistoryItem(BaseModel):
    """A packet in the client history"""
    filename: str
    direction: str  # "received" (hub received from client) or "sent" (hub sent to client)
    league_name: str
    source: str
    dest: str
    timestamp: str
    processed_at: Optional[str] = None
    retrieved_at: Optional[str] = None
    processing_run_id: Optional[int] = None  # Link to processing run


class LeagueMembershipInfo(BaseModel):
    """League membership info for client detail"""
    league_id: int
    league_name: str
    full_id: str  # e.g., "013B"
    bbs_index: int
    fidonet_address: Optional[str] = None


class ClientDetailResponse(BaseModel):
    """Detailed client info with stats and recent packets"""
    id: int
    bbs_name: str
    client_id: str
    is_active: bool
    created_at: Optional[str] = None
    stats: ClientStats
    packets: List[PacketHistoryItem] = []
    league_memberships: List[LeagueMembershipInfo] = []

    class Config:
        from_attributes = True


class MembershipInfo(BaseModel):
    """League membership info"""
    league_id: int
    league_name: str
    league_full_id: str
    bbs_index: Optional[int] = None
    fidonet_address: Optional[str] = None
    joined_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


class ClientListResponse(BaseModel):
    """List of clients"""
    clients: List[ClientResponse]
    total: int
