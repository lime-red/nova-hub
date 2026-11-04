"""League schemas for Management API"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.services.games import GAME_LETTERS


class LeagueCreate(BaseModel):
    """Create a new league"""
    # What packet filenames can carry: anything else makes a league no packet
    # can ever be addressed to.
    league_id: str = Field(pattern=r"^\d{3}$")  # 3-digit number, e.g., "555"
    game_type: str  # one letter from backend.services.games, e.g. "B"
    name: str
    description: Optional[str] = None
    dosemu_path: Optional[str] = None
    game_executable: Optional[str] = None
    # The hub's own FidoNet address in this league. Nodelist generation is
    # blocked until it is set -- see NodelistGenerator.
    hub_fidonet_address: Optional[str] = None
    # Whether the hub's nodelist entry carries the game's HOST routing
    # directive. True for every live league.
    hub_routes_mail: Optional[bool] = True
    is_active: Optional[bool] = True

    @field_validator("game_type")
    @classmethod
    def known_game(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 1 or v not in GAME_LETTERS:
            raise ValueError(f"game_type must be one of: {', '.join(GAME_LETTERS)}")
        return v


class LeagueUpdate(BaseModel):
    """Update a league"""
    name: Optional[str] = None
    description: Optional[str] = None
    dosemu_path: Optional[str] = None
    game_executable: Optional[str] = None
    hub_fidonet_address: Optional[str] = None
    hub_routes_mail: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("A league needs a name")
        if len(v) > 100:
            raise ValueError("A league name can be at most 100 characters")
        return v


class LeagueResponse(BaseModel):
    """League response with member count"""
    id: int
    league_id: str
    game_type: str
    full_id: str  # e.g., "555B"
    name: str
    description: Optional[str] = None
    hub_fidonet_address: Optional[str] = None
    hub_routes_mail: Optional[bool] = None
    is_active: bool
    member_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class FtnAddressRef(BaseModel):
    """One of a BBS's FTN addresses"""
    id: int
    address: str


class NodelistInfo(BaseModel):
    """The league's current nodelist file, as served for download"""
    filename: str
    size: int
    modified_at: datetime


class CoordinatorKeyResponse(BaseModel):
    """An Immortal Barons league's Coordinator public key (Service API)"""
    league_id: str      # e.g. "555I"
    public_key: str     # 64 lower-case hex, what -coord-key takes


class MemberResponse(BaseModel):
    """League member response"""
    membership_id: int
    client_id: int
    bbs_name: str
    bbs_index: int
    ftn_address_id: Optional[int] = None
    fidonet_address: Optional[str] = None  # the address's text, for display
    client_ftn_addresses: List[FtnAddressRef] = []  # what an edit may choose from
    client_oauth_id: Optional[str] = None  # The client's OAuth client_id; admins only
    board_key: Optional[str] = None  # Immortal Barons: the board's public signing key
    joined_at: Optional[str] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class LeagueStats(BaseModel):
    """League statistics"""
    total_packets: int = 0
    processed_packets: int = 0
    processing_runs: int = 0


class LeagueGameInfo(BaseModel):
    """The hub's own copy of the league's game (league_games), for an admin"""
    folder: str  # inside [games] root, as B015
    path: Optional[str] = None  # on the hub; None if [games] root is unset
    state: str  # provisioning, installed, ready or failed
    runs: bool  # ready and the league enabled: the schedule runs it
    image: Optional[str] = None  # the image it was made from, as "<version> (<dirname>)"
    preflight_ok: Optional[bool] = None  # None until preflight has run
    preflight_at: Optional[datetime] = None
    preflight_problems: List[str] = []
    # The league's latest setup (New League), if it had one: a reset-history
    # order of kind "setup", with its status and, failed, why.
    setup_id: Optional[int] = None
    setup_status: Optional[str] = None  # applying, applied or failed
    setup_error: Optional[str] = None
    can_enable: bool = False  # ready, and the latest preflight passed


class NewLeague(BaseModel):
    """New League's form: what Set Up makes the league and its game from"""
    league_id: str = Field(min_length=3, max_length=3)  # three digits, unused by any game
    game_type: str = Field(min_length=1, max_length=1)  # B, F or I
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    hub_fidonet_address: Optional[str] = None  # default <n>:<n>/<hub index>
    image_id: Optional[int] = None  # default the game's newest image


class SetUpAgain(BaseModel):
    image_id: Optional[int] = None  # default the image it was set up from


class SetupImage(BaseModel):
    id: int
    dirname: str
    game_type: str
    version: str


class NewLeagueForm(BaseModel):
    """What New League's form needs from the hub"""
    hub_index: int
    images: List[SetupImage]  # newest first
    used_ids: List[str]  # every league in use, as 015B: a number is unique within its game
    problems: List[str] = []  # why Set Up cannot work on this hub, if anything


class SetupStarted(BaseModel):
    league_id: int  # the new league's database id
    full_id: str
    setup_id: int  # its order, in the league's reset history


class LeagueDetailResponse(BaseModel):
    """Detailed league info with members and stats"""
    id: int
    league_id: str
    game_type: str
    full_id: str
    name: str
    description: Optional[str] = None
    hub_fidonet_address: Optional[str] = None
    hub_routes_mail: Optional[bool] = None
    dosemu_path: Optional[str] = None
    game_executable: Optional[str] = None
    is_active: bool
    members: List[MemberResponse] = []
    available_clients: List[Dict[str, Any]] = []
    stats: LeagueStats
    nodelist: Optional[NodelistInfo] = None  # None until one has been generated
    # Immortal Barons: what members record with -coord-key. The problem is for
    # an admin, and names hub paths.
    coordinator_key: Optional[str] = None
    coordinator_key_problem: Optional[str] = None
    game: Optional[LeagueGameInfo] = None  # admins only; None if the hub has no game for it

    model_config = ConfigDict(from_attributes=True)


class AddMemberRequest(BaseModel):
    """Add member to league"""
    client_id: int
    bbs_index: int
    ftn_address_id: int  # one of the client's own addresses


class UpdateMemberRequest(BaseModel):
    """Edit a membership; a field left out is left as it is"""
    bbs_index: Optional[int] = None
    ftn_address_id: Optional[int] = None
    # Immortal Barons leagues only: the 64-hex key -gen-board-key printed, or
    # "" to remove it.
    board_key: Optional[str] = None


class LeagueDeleteRequest(BaseModel):
    """Confirmation required to delete a league"""
    confirmation_name: str  # Must match "{game_name}_{league_id}", e.g. "BRE_014"


class LeagueListResponse(BaseModel):
    """List of leagues"""
    leagues: List[LeagueResponse]
    total: int


class LeagueScoreFile(BaseModel):
    """The newest copy of one of the league's score files, as the hub's game
    last wrote it."""
    filename: str            # e.g. "PLYSCORE.ANS"
    label: str               # what it ranks, e.g. "Players by score"
    generated_at: datetime   # when the hub took it from the game, UTC
    processing_run_id: int   # the run that made it
    file_data_html: Optional[str] = None  # the ANSI as HTML

