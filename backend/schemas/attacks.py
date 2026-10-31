"""Schemas for the attack-tracing view.

No unit counts in the listing: an admin who also plays must not learn an
attack's strength from the hub. What is there identifies an attack and times its
journey. The one exception is AttackForces, which only an admin can fetch, one
attack at a time, by clicking to reveal it.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel


class AttackHop(BaseModel):
    """One packet that carried the attack, or its result, through the hub."""
    is_result: bool
    packet_id: int
    filename: str
    source_bbs: Optional[int] = None
    dest_bbs: Optional[int] = None
    at_hub: Optional[str] = None
    taken: Optional[str] = None


class AttackJourney(BaseModel):
    attack_id: str
    league_id: int
    league_name: Optional[str] = None
    from_planet: int
    to_planet: int
    attacker: Optional[str] = None          # realm letters; an IB realm has none
    target: Optional[str] = None
    # The realms' names as the hub knew them when it read the packets: who held
    # the slot then, not now. None where it had not heard.
    attacker_name: Optional[str] = None
    target_name: Optional[str] = None
    attack_type: Optional[str] = None
    # Game clocks: the attacker's board for launched, the defender's for resolved.
    launched: Optional[str] = None
    resolved: Optional[str] = None
    # Hub clock.
    attack_at_hub: Optional[str] = None
    attack_delivered: Optional[str] = None
    result_at_hub: Optional[str] = None
    result_delivered: Optional[str] = None
    stage: str
    lost_attack_days: int
    # When the attacker's board writes it off: midnight on its own clock, and
    # that moment on the hub's. The board's offset from the hub is measured.
    mit_due_local: Optional[str] = None
    mit_due: Optional[str] = None
    attacker_clock_minutes: Optional[int] = None
    # Hub clock: the attacker's uploads either side of its rollover into the due date.
    rollover_after: Optional[str] = None
    rollover_before: Optional[str] = None
    mit: Optional[str] = None       # "late" | "possible" | "overdue" | None
    # Set when the stage is "relay not held": the board the hub's missing relay was for.
    unheld_relay_to: Optional[int] = None
    hops: List[AttackHop] = []


class AttackForces(BaseModel):
    """Admin only, on request: what an attack sent and, once resolved, what it cost."""
    attack_id: str
    sent: Dict[str, int]                  # BRE/IB: troopers, jets, tanks, bombers; FE: unit_1-3
    carriers: Optional[int] = None        # BRE: carry the jets; always come home
    resolved: bool                        # False: only the attack has been seen
    success: Optional[bool] = None
    regions_captured: Optional[int] = None
    loss_percent: Optional[float] = None  # the same share of every unit type
    lost: Optional[Dict[str, int]] = None
    returned: Optional[Dict[str, int]] = None
    defenders_destroyed: Optional[int] = None   # defending troopers
    outcome: Optional[str] = None         # IB's verdict, e.g. "protected", "notfound"
    objective: Optional[str] = None       # FE: Capture Land, People or Resources
    gained: Optional[str] = None          # FE: what a win took, e.g. "15 acres"
