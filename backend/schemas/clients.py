"""Client schemas for Management API"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_location(value: Optional[str]) -> Optional[str]:
    """City/state/country become whole lines in a nodes.dat entry.

    A newline here would shift every following field up a line and silently
    re-point the rest of the nodelist, so it is rejected at the edge rather
    than stripped later by the generator.
    """
    if value is None:
        return None
    if any(ch in value for ch in ("\n", "\r")):
        raise ValueError("location fields must not contain newline characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("location fields must not contain control characters")
    return value.strip()


def _validate_bbs_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if any(ch in value for ch in ("\n", "\r")):
        raise ValueError("bbs_name must not contain newline characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("bbs_name must not contain control characters")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("bbs_name must not be empty")
    return trimmed


class ClientCreate(BaseModel):
    """Create a new client"""
    bbs_name: str
    client_id: str
    # Nodelist location lines. Optional -- an empty line is valid in nodes.dat.
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

    @field_validator("bbs_name")
    @classmethod
    def validate_bbs_name(cls, value: str) -> str:
        validated = _validate_bbs_name(value)
        if validated is None:
            raise ValueError("bbs_name must not be empty")
        return validated

    @field_validator("city", "state", "country")
    @classmethod
    def validate_location(cls, value: Optional[str]) -> Optional[str]:
        return _validate_location(value)


class ClientUpdate(BaseModel):
    """Update a client"""
    bbs_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("bbs_name")
    @classmethod
    def validate_bbs_name(cls, value: Optional[str]) -> Optional[str]:
        return _validate_bbs_name(value)

    @field_validator("city", "state", "country")
    @classmethod
    def validate_location(cls, value: Optional[str]) -> Optional[str]:
        return _validate_location(value)


class ClientResponse(BaseModel):
    """Client response with basic stats"""
    id: int
    bbs_name: str
    client_id: str
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    is_active: bool
    last_seen: Optional[str] = None
    packets_sent_24h: int = 0
    packets_received_24h: int = 0
    # Each league whose League Status is not OK, e.g. "015F: Never polled this league"
    league_issues: List[str] = []

    model_config = ConfigDict(from_attributes=True)


class ClientCreatedResponse(BaseModel):
    """Response when creating a new client with secret"""
    id: int
    bbs_name: str
    client_id: str
    client_secret: str  # Plain text, shown only once


class RegenerateSecretRequest(BaseModel):
    """How long the replaced secret keeps working"""
    overlap_hours: int = Field(0, ge=0, le=72, description=(
        "Hours the old secret keeps working beside the new one, so the node isn't cut "
        "off before its config is edited. 0 stops it now, and ends the access tokens "
        "already issued."))


class ClientSecretResponse(BaseModel):
    """Response when regenerating client secret"""
    client_id: str
    client_secret: str  # Plain text, shown only once
    old_secret_until: Optional[datetime] = None  # None: the old secret has stopped


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
    direction: str  # "received" | "queued" (awaiting download) | "sent" (downloaded)
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
    nodelist_filename: Optional[str] = None  # None until the league has a nodelist


class FtnAddressInfo(BaseModel):
    """An FTN address the BBS holds, and the leagues using it"""
    id: int
    address: str
    leagues: List[str] = []  # full ids, e.g. ["014B", "015F"]


class FtnAddressRequest(BaseModel):
    """Assign or renumber an FTN address (admin only)"""
    address: str


class OwnerInfo(BaseModel):
    """A sysop who owns a BBS. email is filled in for admins only."""
    user_id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None


class OwnerRequest(BaseModel):
    """Make a user an owner of a BBS"""
    user_id: int


class ClientDetailResponse(BaseModel):
    """Detailed client info with stats and recent packets"""
    id: int
    bbs_name: str
    client_id: str
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None
    stats: ClientStats
    packets: List[PacketHistoryItem] = []
    league_memberships: List[LeagueMembershipInfo] = []
    ftn_addresses: List[FtnAddressInfo] = []
    owners: List[OwnerInfo] = []
    # Onboarding's Verify step: approved, claimed, first_token, first_upload or active
    stage: str = "approved"
    first_token_at: Optional[datetime] = None
    first_upload_at: Optional[datetime] = None
    # A claim link bound to the viewer is waiting for them to collect
    claim_waiting: bool = False
    # A rotation kept the replaced secret working until then
    old_secret_until: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MembershipInfo(BaseModel):
    """League membership info"""
    league_id: int
    league_name: str
    league_full_id: str
    bbs_index: Optional[int] = None
    fidonet_address: Optional[str] = None
    joined_at: datetime
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ClientListResponse(BaseModel):
    """List of clients"""
    clients: List[ClientResponse]
    total: int
