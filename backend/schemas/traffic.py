"""Schemas for the InterBBS traffic view.

As for attacks: the listing says who sent what kind of thing to whom and how
far it got. What was in it is TrafficDetails, admin only, one event at a time,
on request -- and a Message's text is never decoded at all.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.schemas.attacks import AttackHop


class TrafficEvent(BaseModel):
    key: str
    kind: str                         # the game's own name, e.g. "Terrorist Result"
    role: Optional[str] = None        # "send", "result", "notice"
    from_planet: int
    to_planet: int
    from_letter: Optional[str] = None
    to_letter: Optional[str] = None
    from_name: Optional[str] = None   # as the hub knew them when it read the packet
    to_name: Optional[str] = None
    stamp: Optional[str] = None       # far side's game clock, where it says
    at_hub: Optional[str] = None
    delivered: Optional[str] = None
    stage: str
    revealable: bool                  # whether there is anything an admin may reveal
    hops: List[AttackHop] = []


class TrafficJourney(BaseModel):
    """A send and its result, a Gooie from funding to its end, or one event."""
    key: str                          # its first event's
    league_id: int
    league_name: Optional[str] = None
    kind: str                         # "Terrorist Op", "Trade Deal", "Gooie Kablooie", ...
    from_planet: int
    to_planet: int
    from_letter: Optional[str] = None
    to_letter: Optional[str] = None
    from_name: Optional[str] = None   # as the hub knew them when it read the packet
    to_name: Optional[str] = None
    recipients: Optional[str] = None  # Message: "all planets", or realm letters
    # Message: the recipients' names in the same order, null where not known.
    recipient_names: Optional[List[Optional[str]]] = None
    expects_result: bool
    sent_at_hub: Optional[str] = None
    sent_delivered: Optional[str] = None
    result_kind: Optional[str] = None
    result_at_hub: Optional[str] = None
    result_delivered: Optional[str] = None
    stage: str
    events: List[TrafficEvent] = []


class TrafficDetails(BaseModel):
    key: str
    kind: str
    details: Dict[str, Any]
