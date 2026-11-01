"""Schemas for drafting, performing and looking back on a game reset. The settings themselves are a plain
mapping keyed by field id; game_reset/fields.py says what each one may hold."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResetChoice(BaseModel):
    letter: str
    name: str


class ResetField(BaseModel):
    """One setting, as the reset editor offers it, with the game's own help."""
    id: str
    label: str
    page: int
    group: str
    kind: str = Field(description="number, choice, toggle, date or text")
    default: Any = None
    min: Optional[int] = None
    max: Optional[int] = None
    choices: List[ResetChoice] = []
    interbbs: bool = False
    advised_min: Optional[int] = Field(
        None, description="Ours, not the game's: below it the form warns but allows")
    advised_reason: Optional[str] = None
    note: Optional[str] = None
    admin_note: Optional[str] = None
    depends_on: Optional[List[Any]] = Field(
        None, description="[field id, value]: only asked when that field has that value")
    help_title: str
    help: str


class ResetWarning(BaseModel):
    field: str
    message: str


class ResetDraft(BaseModel):
    id: int
    version: int
    settings: Dict[str, Any]
    warnings: List[ResetWarning] = []
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None


class ResetApplied(BaseModel):
    id: int
    settings: Dict[str, Any]
    applied_by: Optional[str] = None
    applied_at: Optional[str] = None
    game_start: Optional[str] = None


class ObservedSettings(BaseModel):
    """What the hub last decoded from the league's Configupdate (BRE only, so far)."""
    game_started_at: Optional[str] = None
    protection_turns: Optional[int] = None
    indiv_attacks: Optional[int] = None
    lost_attack_days: Optional[int] = None
    settings: Optional[Dict[str, Any]] = Field(
        None, description="Every reset-editor setting decoded, by field id, in the "
                          "form's values; null until a Configupdate is seen after "
                          "the full decoder")
    seen_at: Optional[str] = None


class ResetStep(BaseModel):
    stage: str = Field(description="queued, lock, backup, editor, saved, processing, "
                                   "sent; or restored, failed, interrupted")
    at: str


class ResetMember(BaseModel):
    """One packet the reset's processing run wrote, and whether its BBS has it."""
    bbs_index: str
    bbs_name: Optional[str] = None
    filename: str
    downloaded_at: Optional[str] = None


class ResetSummary(BaseModel):
    """One row of a league's reset history."""
    id: int
    kind: str = Field("reset", description="reset; or, for an Immortal Barons league, "
                                           "rules (changed without a reset), freeze or thaw")
    status: str = Field(description="applying, applied or failed")
    source: str = Field(description="console, or observed: seen in a Game Start "
                                    "packet, not performed from here")
    settings: Dict[str, Any]
    notes: Optional[str] = None
    applied_by: Optional[str] = None
    applied_at: Optional[str] = None
    game_start: Optional[str] = None
    ended: Optional[str] = Field(None, description="The next reset's game start; "
                                                   "null for the game being played")
    error: Optional[str] = None
    confirmed: Optional[bool] = Field(
        None, description="True once a Game Start agreeing with the settings went "
                          "out; false if it disagreed; null until one is seen")
    mismatches: List[str] = []


class ResetDetail(ResetSummary):
    progress: List[ResetStep] = []
    warnings: List[ResetWarning] = []
    transcript: Optional[str] = None
    backup_path: Optional[str] = None
    processing_run_id: Optional[int] = None
    sent_config: Optional[Dict[str, Any]] = None
    members: List[ResetMember] = []


class ResetPerform(BaseModel):
    version: int = Field(description="The draft version being performed")
    confirm: str = Field(description="The league's code, typed by hand (for example 015B)")


class LeagueOrder(BaseModel):
    """A freeze or thaw of an Immortal Barons league."""
    confirm: str = Field(description="The league's code, typed by hand (for example 015I)")
    message: Optional[str] = Field(None, description="Freeze only: what every caller is "
                                                     "shown while the league is frozen")


class IbBoard(BaseModel):
    """One board as the hub, the league's Coordinator, knows it."""
    name: str = Field(description="The board's name in the game's roster")
    bbs_index: Optional[int] = None
    bbs_name: Optional[str] = Field(None, description="The hub's name for its client")
    last_packet: Optional[str] = Field(None, description="When the hub last accepted a "
                                                         "packet from it (a bounce included)")
    version: Optional[str] = None
    ruleset: Optional[str] = Field(None, description="The rules it last said it plays by")
    other_rules: bool = False
    below_min: bool = False
    protocol_held: bool = Field(False, description="Its packets are held for a protocol "
                                                   "the hub's build cannot read, and it "
                                                   "has not talked past that since")
    held: Dict[str, int] = {}
    round_trip_days: Optional[float] = None
    round_trip_seen: Optional[str] = None
    quiet_since: Optional[str] = Field(None, description="Frozen: its quiet report, when "
                                                         "it last applied real traffic")


class IbHeldPacket(BaseModel):
    file: str
    reason: str = Field(description="protocol, rules, signature or unreadable")
    arrived: Optional[str] = None
    expires: Optional[str] = Field(None, description="When the game deletes it unread")
    from_board: Optional[str] = None
    bbs_index: Optional[int] = None
    type: Optional[str] = None
    protocol: Optional[int] = None
    ruleset: Optional[str] = None
    version: Optional[str] = None


class IbGameState(BaseModel):
    """An Immortal Barons league as the hub, its Coordinator, sees it. The hub
    plays no part in the game; this is what it knows of the boards."""
    league_id: int
    full_id: str
    available: bool = Field(description="False when the hub's Coordinator game has no "
                                        "world yet")
    saved: Optional[str] = None
    started: Optional[str] = None
    ruleset: Optional[str] = None
    min_board_version: Optional[str] = None
    frozen: bool = False
    freeze_serial: int = 0
    freeze_message: Optional[str] = None
    frozen_at: Optional[str] = None
    thawed_at: Optional[str] = None
    quiet: int = Field(0, description="Frozen: boards whose quiet report is in")
    boards: List[IbBoard] = []
    held: List[IbHeldPacket] = []
    under_way: Optional[ResetDetail] = None


class HubTimezone(BaseModel):
    """The hub's own clock, for the form's note on dates. The dates themselves
    are applied by each BBS on its own clock; the hub enforces none."""
    name: str = Field(description="IANA name where the host has one (Etc/UTC), else its abbreviation")
    utc_offset: str = Field(description="Offset from UTC now, as +HH:MM")
    today: str = Field(description="The hub's date now, which the form's date checks use")


class GameResetForm(BaseModel):
    league_id: int
    full_id: str
    game_type: str
    fields: List[ResetField]
    defaults: Dict[str, Any]
    draft: Optional[ResetDraft] = None
    last_applied: Optional[ResetApplied] = None
    observed: Optional[ObservedSettings] = None
    under_way: Optional[ResetDetail] = Field(
        None, description="The league's reset in progress, if one is")
    hub_timezone: HubTimezone


class ResetDraftSave(BaseModel):
    settings: Dict[str, Any]
    notes: Optional[str] = Field(None, max_length=4000)
    version: Optional[int] = Field(
        None, description="The draft version being edited; omit when starting a new draft")
