"""League schemas for Management API"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class LeagueCreate(BaseModel):
    """Create a new league"""
    league_id: str  # 3-digit number, e.g., "555"
    game_type: str  # "B" or "F"
    name: str
    description: Optional[str] = None
    dosemu_path: Optional[str] = None
    game_executable: Optional[str] = None
    is_active: Optional[bool] = True


class LeagueUpdate(BaseModel):
    """Update a league"""
    name: Optional[str] = None
    description: Optional[str] = None
    dosemu_path: Optional[str] = None
    game_executable: Optional[str] = None
    is_active: Optional[bool] = None


class LeagueResponse(BaseModel):
    """League response with member count"""
    id: int
    league_id: str
    game_type: str
    full_id: str  # e.g., "555B"
    name: str
    description: Optional[str] = None
    is_active: bool
    member_count: int = 0

    class Config:
        from_attributes = True


class MemberResponse(BaseModel):
    """League member response"""
    membership_id: int
    client_id: int
    bbs_name: str
    bbs_index: int
    fidonet_address: Optional[str] = None
    client_oauth_id: str  # The client's OAuth client_id
    joined_at: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


class LeagueStats(BaseModel):
    """League statistics"""
    total_packets: int = 0
    processed_packets: int = 0
    processing_runs: int = 0


class LeagueDetailResponse(BaseModel):
    """Detailed league info with members and stats"""
    id: int
    league_id: str
    game_type: str
    full_id: str
    name: str
    description: Optional[str] = None
    dosemu_path: Optional[str] = None
    game_executable: Optional[str] = None
    is_active: bool
    members: List[MemberResponse] = []
    available_clients: List[Dict[str, Any]] = []
    stats: LeagueStats

    class Config:
        from_attributes = True


class AddMemberRequest(BaseModel):
    """Add member to league"""
    client_id: int
    bbs_index: int
    fidonet_address: str


class UpdateBbsIndexRequest(BaseModel):
    """Update BBS index for a member"""
    bbs_index: int


class UpdateFidonetRequest(BaseModel):
    """Update Fidonet address for a member"""
    fidonet_address: str


class LeagueListResponse(BaseModel):
    """List of leagues"""
    leagues: List[LeagueResponse]
    total: int
