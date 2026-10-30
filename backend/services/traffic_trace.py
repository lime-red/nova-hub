"""The rest of the InterBBS traffic, followed through the hub like attacks are.

An op, trade deal, spy report or message is one record that the hub may carry
twice -- in from its board, and out again in the packet its own game writes for
the target -- and the bytes do not change, so their hash is one *event*. What
the listing says is who sent what kind of thing to whom, and how far it got;
what was in it stays in the packet (see bre_packet.traffic_details).

Events are then strung into *journeys*, the way an attack is followed to its
result. Nothing here carries an ID, so they are joined on what a result echoes
of its send (bre_packet's `chain`):

- an op and its result, a trade deal and its "arrived" report: each result goes
  with the latest earlier send of its chain that has no result yet. A Send Spy
  is answered by a Spy Report.
- a Gooie: the News items telling its target it was funded and when it will
  arrive, its arrival, the daily results, and the News of its kill. One Gooie
  per planet at a time, so a chain (sender -> target planet) is one Gooie until
  it is destroyed or GOOIE_LIFETIME has passed.
- anything else -- a message, a Spy Guy -- is a journey of one.

Player IDs are not letters, and a Message names its recipients only by ID. The
letters are learned from records that carry both (an op, a trade deal, a
message's sender); a recipient never seen that way shows as "?".
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.database import Packet, TrafficSighting
from backend.services import bre_packet
from backend.services.attack_trace import Hop, _bbs, _earliest

# Funded, 72 hours to arrive, up to 5 days on the target; with room to spare.
GOOIE_LIFETIME = timedelta(days=12)


@dataclass
class Event:
    key: str
    league_id: int
    record_type: int
    kind: str
    from_planet: int
    to_planet: int
    from_letter: Optional[str] = None
    to_letter: Optional[str] = None
    # Realm names as the hub knew them when it read the packet (realm_names).
    from_name: Optional[str] = None
    to_name: Optional[str] = None
    recipients: Optional[str] = None     # Message: "all planets", or letters
    recipient_names: Optional[List[Optional[str]]] = None
    stamp: Optional[datetime] = None
    at_hub: Optional[datetime] = None
    delivered: Optional[datetime] = None
    chain: Optional[str] = None
    role: Optional[str] = None
    hops: List[Hop] = field(default_factory=list)
    _recipient_ids: Optional[List[int]] = None

    @property
    def stage(self) -> str:
        if self.delivered:
            return "delivered"
        return "awaiting pickup" if self.at_hub else "not seen"


@dataclass
class Journey:
    """A send and what came of it, or a single event that expects nothing back."""
    events: List[Event]
    expects_result: bool

    @property
    def send(self) -> Optional[Event]:
        return next((e for e in self.events if e.role == bre_packet.SEND), None)

    @property
    def results(self) -> List[Event]:
        return [e for e in self.events if e.role == bre_packet.RESULT]

    @property
    def first(self) -> Event:
        return self.events[0]

    @property
    def is_gooie(self) -> bool:
        return (self.first.chain or "").startswith("gooie:")

    @property
    def kind(self) -> str:
        if self.is_gooie:
            return "Gooie Kablooie"
        return (self.send or self.first).kind

    @property
    def route(self) -> Event:
        """The event whose from/to read as the journey's: its send, or else a
        result turned round."""
        if self.send or not self.results:
            return self.send or self.first
        r = self.results[0]
        return Event(r.key, r.league_id, r.record_type, r.kind,
                     from_planet=r.to_planet, to_planet=r.from_planet,
                     from_letter=r.to_letter, to_letter=r.from_letter,
                     from_name=r.to_name, to_name=r.from_name)

    @property
    def started(self) -> Optional[datetime]:
        return self.first.at_hub

    @property
    def result_at_hub(self) -> Optional[datetime]:
        return min((r.at_hub for r in self.results if r.at_hub), default=None)

    @property
    def result_delivered(self) -> Optional[datetime]:
        return min((r.delivered for r in self.results if r.delivered), default=None)

    @property
    def stage(self) -> str:
        if self.is_gooie:
            kinds = {e.kind for e in self.events}
            if "Gooie Destroyed" in kinds:
                return "destroyed"
            if self.results:
                return "active"
            if self.send:
                return "arrived" if self.send.delivered else "launched"
            return "warned" if "Gooie Warning" in kinds else "funded"
        if not self.expects_result:
            return self.first.stage
        if self.result_delivered:
            return "result delivered"
        if self.result_at_hub:
            return "result awaiting pickup"
        return "awaiting result" if self.send and self.send.delivered else "awaiting pickup"


def events(db: Session, hub_index: str, league_id: Optional[int] = None,
           since: Optional[datetime] = None, planet: Optional[int] = None,
           record_type: Optional[int] = None) -> List[Event]:
    q = db.query(TrafficSighting, Packet).join(Packet, TrafficSighting.packet_id == Packet.id)
    if league_id is not None:
        q = q.filter(TrafficSighting.league_id == league_id)
    if planet is not None:
        q = q.filter((TrafficSighting.from_planet == planet)
                     | (TrafficSighting.to_planet == planet))
    if record_type is not None:
        q = q.filter(TrafficSighting.record_type == record_type)
    if since is not None:
        q = q.filter(Packet.uploaded_at >= since)
    hub = _bbs(hub_index)

    by_key: Dict[str, Event] = {}
    for s, p in q.all():
        e = by_key.get(s.event_key)
        if e is None:
            e = by_key[s.event_key] = Event(
                key=s.event_key, league_id=s.league_id, record_type=s.record_type,
                kind=s.kind, from_planet=s.from_planet, to_planet=s.to_planet,
                from_letter=s.from_letter, to_letter=s.to_letter,
                from_name=s.from_name, to_name=s.to_name,
                recipient_names=s.recipient_names,
                stamp=s.stamp, chain=s.chain, role=s.role,
                _recipient_ids=None if s.recipients is None
                else [int(r) for r in s.recipients.split(",") if r],
            )
        source, dest = _bbs(p.source_bbs_index), _bbs(p.dest_bbs_index)
        # As for attacks: the hub's own board takes a packet when the hub's game
        # runs it, and a packet the hub's game wrote reached the hub when it was
        # collected.
        taken = p.processed_at if dest == hub else p.downloaded_at
        at_hub = (p.processed_at or p.uploaded_at) if source == hub else p.uploaded_at
        e.hops.append(Hop(False, p.id, p.filename, source, dest, at_hub, taken))
        e.at_hub = _earliest(e.at_hub, at_hub)
        if dest == s.to_planet and taken:
            e.delivered = _earliest(e.delivered, taken)

    out = list(by_key.values())
    for e in out:
        e.hops.sort(key=lambda h: h.at_hub or datetime.min)
    _name_recipients(db, out)
    out.sort(key=lambda e: e.at_hub or datetime.min)
    return out


def journeys(found: List[Event]) -> List[Journey]:
    """String events into journeys (see the module docstring), newest first."""
    out: List[Journey] = []
    chains: Dict[Tuple[int, str], List[Event]] = {}
    for e in found:                         # already oldest first
        if e.chain:
            chains.setdefault((e.league_id, e.chain), []).append(e)
        else:
            out.append(Journey([e], expects_result=False))

    for (_, chain), group in chains.items():
        if chain.startswith("gooie:"):
            out.extend(_gooies(group))
        else:
            out.extend(_pairs(group))
    out.sort(key=lambda j: j.started or datetime.min, reverse=True)
    return out


def _pairs(group: List[Event]) -> List[Journey]:
    open_sends: List[Journey] = []
    out: List[Journey] = []
    for e in group:
        if e.role == bre_packet.SEND:
            j = Journey([e], expects_result=True)
            open_sends.append(j)
            out.append(j)
        elif e.role == bre_packet.RESULT:
            if open_sends:
                j = open_sends.pop()        # the latest send still waiting
                j.events.append(e)
            else:
                out.append(Journey([e], expects_result=True))   # its send is older
        else:
            out.append(Journey([e], expects_result=False))
    return out


def _gooies(group: List[Event]) -> List[Journey]:
    out: List[Journey] = []
    current: Optional[Journey] = None
    for e in group:
        start = current.started if current else None
        if (current is None or current.stage == "destroyed"
                or (start and e.at_hub and e.at_hub - start > GOOIE_LIFETIME)):
            current = Journey([], expects_result=True)
            out.append(current)
        current.events.append(e)
    return out


def _name_recipients(db: Session, out: List[Event]) -> None:
    messages = [e for e in out if e._recipient_ids is not None]
    if not messages:
        return
    letters: Dict[Tuple[int, int, int], str] = {}
    leagues = {e.league_id for e in messages}
    for s in db.query(TrafficSighting).filter(TrafficSighting.league_id.in_(leagues)):
        if s.from_player is not None and s.from_letter:
            letters[(s.league_id, s.from_planet, s.from_player)] = s.from_letter
        if s.to_player is not None and s.to_letter:
            letters[(s.league_id, s.to_planet, s.to_player)] = s.to_letter
    for e in messages:
        ids = e._recipient_ids
        if ids == [bre_packet.ALL_PLANETS]:
            e.recipients = "all planets"
        else:
            e.recipients = "".join(letters.get((e.league_id, e.to_planet, i), "?")
                                   for i in ids)
