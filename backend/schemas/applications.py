"""Application schemas: a sysop applying, and the admin's queue"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from backend.services.games import MAX_BBS_INDEX


class JoinableLeague(BaseModel):
    id: int
    full_id: str  # "015B"
    name: str
    game: str  # "BRE"


class JoinApplicationRequest(BaseModel):
    """A new BBS asking to join"""
    kind: Literal["join"] = "join"
    bbs_name: str = Field(..., min_length=1, max_length=100)
    sysop_name: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=50)
    state: Optional[str] = Field(None, max_length=50)
    country: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=200)
    os: Optional[Literal["windows", "linux"]] = None
    league_ids: List[int]
    note: Optional[str] = Field(None, max_length=2000)


class LeagueRequest(BaseModel):
    """A BBS the sysop runs, asking to be added to more leagues"""
    kind: Literal["leagues"] = "leagues"
    client_id: int  # Client.id
    league_ids: List[int]
    note: Optional[str] = Field(None, max_length=2000)


class GrantedLeague(BaseModel):
    league_id: int
    league: str  # "015B"
    bbs_index: int
    ftn_address: Optional[str] = None


class ApplicationResponse(BaseModel):
    id: int
    kind: str
    status: str  # pending | approved | rejected | withdrawn
    applicant: str
    email: Optional[str] = None
    client_id: Optional[int] = None  # the BBS (a leagues request's, or the one approval made)
    bbs_name: str
    sysop_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None
    os: Optional[str] = None
    leagues: List[JoinableLeague]
    note: Optional[str] = None
    created_at: datetime
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    granted: List[GrantedLeague] = []


class TakenIndex(BaseModel):
    bbs_index: int
    bbs_name: str
    ftn_address: Optional[str] = None


class LeagueSuggestion(BaseModel):
    """What the approval dialog needs to fill in one league"""
    league_id: int
    full_id: str
    name: str
    hub_address: Optional[str] = None
    bbs_index: int  # suggested: the BBS's index elsewhere, else the lowest free one
    ftn_address: Optional[str] = None  # suggested: an address the BBS already holds
    taken: List[TakenIndex]  # the league's active members


class ApplicationReview(BaseModel):
    """An application with what the admin needs to approve it"""
    application: ApplicationResponse
    client_id_suggestion: Optional[str] = None  # for a join: a free OAuth client ID
    client_addresses: List[str] = []  # for a leagues request: the BBS's FTN addresses
    leagues: List[LeagueSuggestion]


class Grant(BaseModel):
    league_id: int
    bbs_index: int = Field(..., ge=1, le=MAX_BBS_INDEX)
    ftn_address: str = Field(..., min_length=1, max_length=50)


class ApproveRequest(BaseModel):
    grants: List[Grant]
    client_id: Optional[str] = Field(None, max_length=64)  # join only
    bbs_name: Optional[str] = Field(None, max_length=100)  # join only
    note: Optional[str] = Field(None, max_length=2000)


class ApproveResponse(BaseModel):
    application: ApplicationResponse
    client_id: int  # Client.id
    claim_url: Optional[str] = None  # a join's claim link, bound to the applicant
    claim_expires_at: Optional[datetime] = None
    nodelist_problems: List[str] = []


class RejectRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)
