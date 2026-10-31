"""Follow each attack through the hub, from launch to result.

BRE's are read by bre_packet and Immortal Barons' by ib_packet; FE's war
records are not mapped yet. The journey and the MIT judgement are the same for
both games but where noted (IB counts its wait differently, and its packets
are dated, not timed).

A Missing In Transit is the attacker's game giving up: no result came back
within the league's "Days for Lost Attacks". From inside the game that is all
anyone can see. The hub carried every hop, though, and each attack carries an ID
its result echoes, so the hub can say exactly how far an attack got:

    launched            the attacker's session (game clock of the attacker's board)
    attack at hub       first packet the hub received it in
    attack delivered    the packet addressed to the target's board was taken
    result at hub       first packet the hub received the result in
    result delivered    the packet addressed back to the attacker was taken

The last stage reached is where it stopped. That is descriptive on purpose -- a
board that has not polled today is not a fault -- but where a result was handed
over after the MIT window, the game will have thrown it away, and saying so is
not a judgement call.

"Taken" means downloaded by the board's client, or, when the board is the hub's
own, processed by the hub's game.

Everything here is best-effort with respect to ingest: decoding a packet must
never be the reason a packet fails to be stored.
"""
import bisect
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.logging_config import get_logger
from backend.models.database import (
    AttackSighting, League, LeagueGameSettings, Packet, TrafficSighting,
)
from backend.services import bre_packet, ib_packet, realm_names

logger = get_logger(context="attack_trace")

DEFAULT_LOST_ATTACK_DAYS = 7    # BRE's default, used until a Configupdate is seen
DEFAULT_LOST_DAYS = {"B": DEFAULT_LOST_ATTACK_DAYS, "I": ib_packet.DEFAULT_LOST_FORCES_DAYS}
NODELIST_PREFIXES = ("BRNODES.", "FENODES.", "IBNODES.")   # stored as packets, but text
# Only the League Coordinator -- the hub's game -- sends these.
HUB_ONLY_TYPES = (bre_packet.CONFIG_UPDATE, bre_packet.GAME_START)


def from_hub(packet: Packet) -> bool:
    """Whether the hub's own game wrote this packet. An upload's source index
    is checked against the uploader's membership, and no member holds the
    hub's, so the filename can be believed."""
    from backend.core.config import get_config
    return packet.source_bbs_index.upper() == get_config().hub.bbs_index.upper()


def trusted(rec: bre_packet.Record, packet: Packet, hub: bool) -> bool:
    """Whether a record is one this packet's sender could have written.

    The hub relays other boards' records, so from the hub any source goes. A
    member only ever sends its own (every BRE upload on dev, Oct 2026), and
    never the coordinator's settings or Game Start: either would let a member
    rewrite what the console shows of the league, or confirm a reset.
    """
    if hub:
        return True
    return rec.type not in HUB_ONLY_TYPES and rec.src == int(packet.source_bbs_index, 16)


def _now() -> datetime:
    return utcnow()


# ── recording ─────────────────────────────────────────────────────────────
def record(db: Session, packet: Packet, content: bytes, commit: bool = True) -> int:
    """Record the attacks, other traffic and league settings in one packet.
    Returns sightings added, of both kinds.

    Idempotent per packet: a filename is reused when its sequence number wraps,
    and the outbound collector then rewrites the same row, so whatever was
    recorded against the row before belongs to a different packet and goes.
    """
    league = packet.league or db.get(League, packet.league_id)
    if league is None:
        return 0
    reader = _READERS.get(league.game_type)
    if reader is None:
        return 0
    if packet.filename.upper().startswith(NODELIST_PREFIXES):
        return 0          # BRNODES.015 is a text nodelist, not a game packet

    db.query(AttackSighting).filter(AttackSighting.packet_id == packet.id).delete()
    db.query(TrafficSighting).filter(TrafficSighting.packet_id == packet.id).delete()
    added = reader(db, league, packet, content)
    if commit:
        db.commit()
    else:
        db.flush()
    return added


def _record_bre(db: Session, league: League, packet: Packet, content: bytes) -> int:
    records = bre_packet.parse(content)
    hub = from_hub(packet)
    usable = []
    for rec in records:
        if not rec.crc_ok:
            # A damaged record decodes to garbage; recording a garbage ID would
            # invent an attack that never happened.
            continue
        if not trusted(rec, packet, hub):
            logger.warning(f"{packet.filename}: ignoring a {rec.name} from {rec.src} "
                           f"that its sender could not have written")
            continue
        usable.append(rec)

    # Realm lists first, so a packet that announces a realm and then acts for
    # it names it.
    seen = packet.processed_at or packet.uploaded_at or utcnow()
    for rec in usable:
        if rec.type == bre_packet.RECON_UPDATE and rec.data and rec.data[0] == rec.src:
            try:
                realms = bre_packet.realms(rec)
            except bre_packet.PacketFormatError:
                continue
            realm_names.remember(db, league.id, rec.src, realms, packet.id, seen)
    names = realm_names.Names(db, league.id)

    added = 0
    for rec in usable:
        if rec.type in (bre_packet.INDIV_ATTACK, bre_packet.ATTACK_RESULT):
            a = bre_packet.attack(rec)
            db.add(AttackSighting(
                attack_id=a.attack_id, is_result=a.is_result,
                packet_id=packet.id, league_id=league.id,
                from_planet=a.from_planet, to_planet=a.to_planet,
                attacker=a.attacker, target=a.target,
                attacker_name=names.name(a.from_planet, a.attacker),
                target_name=names.name(a.to_planet, a.target),
                attack_type=a.attack_type, stamp=a.stamp,
            ))
            added += 1
        elif rec.type == bre_packet.CONFIG_UPDATE:
            _remember_settings(db, league, packet, bre_packet.league_settings(rec))
        elif rec.type == bre_packet.GAME_START:
            # A league-wide reset going out: the league's reset history.
            from backend.services.game_reset.history import record_game_start
            record_game_start(db, league, packet, bre_packet.league_settings(rec))
        elif rec.type in bre_packet.TRAFFIC_TYPES:
            t = bre_packet.traffic(rec)
            if t is None:
                continue        # News that is not about a Gooie
            # A message's recipients are on the planet it is addressed to.
            recipient_names = None
            if t.recipients and t.recipients != [bre_packet.ALL_PLANETS]:
                recipient_names = [names.name(t.to_planet, player=r) for r in t.recipients]
            db.add(TrafficSighting(
                event_key=bre_packet.event_key(rec), packet_id=packet.id,
                league_id=league.id, record_type=t.type, kind=t.name,
                from_planet=t.from_planet, to_planet=t.to_planet,
                from_letter=t.from_letter, to_letter=t.to_letter,
                from_player=t.from_player, to_player=t.to_player,
                from_name=names.name(t.from_planet, t.from_letter, t.from_player),
                to_name=names.name(t.to_planet, t.to_letter, t.to_player),
                recipients=None if t.recipients is None
                else ",".join(str(r) for r in t.recipients),
                recipient_names=recipient_names,
                chain=t.chain, role=t.role, stamp=t.stamp,
            ))
            added += 1
    return added


def _record_ib(db: Session, league: League, packet: Packet, content: bytes) -> int:
    p = ib_packet.parse(content)
    src, dst = _bbs(packet.source_bbs_index), _bbs(packet.dest_bbs_index)
    if src is None or dst is None:
        return 0
    settings, starts = ib_packet.orders(p)
    if (settings or starts) and not from_hub(packet):
        # The games refuse league orders the Coordinator did not sign; the hub
        # takes them only from its own game, which is the Coordinator.
        logger.warning(f"{packet.filename}: ignoring league orders from board {src}")
        settings = starts = None
    if settings is not None:
        _remember_settings(db, league, packet, settings)
    if starts is not None:
        from backend.services.game_reset.history import record_game_start
        s = settings or bre_packet.LeagueSettings(None, None, None, None, {})
        s = bre_packet.LeagueSettings(starts, s.protection_turns, s.indiv_attacks_per_day,
                                      s.lost_attack_days, s.settings)
        record_game_start(db, league, packet, s)
    db.flush()
    row = db.get(LeagueGameSettings, league.id)
    game = ib_packet.game_of(row.game_started_at if row else None)

    added = 0
    for a in ib_packet.strikes(p, src, dst):
        db.add(AttackSighting(
            attack_id=ib_packet.action_id(game, a.origin, a.ident), is_result=a.is_result,
            packet_id=packet.id, league_id=league.id,
            from_planet=a.from_planet, to_planet=a.to_planet,
            attacker_name=a.attacker_name, target_name=a.target_name,
            attack_type=a.attack_type, stamp=a.stamp,
        ))
        added += 1
    for t in ib_packet.traffic(p, src, dst, game):
        db.add(TrafficSighting(
            event_key=t.key, packet_id=packet.id, league_id=league.id,
            record_type=t.record_type, kind=t.kind,
            from_planet=t.from_planet, to_planet=t.to_planet,
            from_name=t.from_name, to_name=t.to_name,
            chain=t.chain, role=t.role, stamp=t.stamp,
        ))
        added += 1
    return added


# FE's packets share BRE's container, but its war records are not mapped yet
# (fe_packet), so an FE league records nothing.
_READERS = {"B": _record_bre, "I": _record_ib}


def record_safely(db: Session, packet: Packet, content: bytes) -> int:
    """`record()` for ingest paths: logs and swallows anything it raises."""
    try:
        return record(db, packet, content)
    except Exception as exc:
        db.rollback()
        logger.warning(f"Could not trace attacks in {packet.filename}: {exc}")
        return 0


def _remember_settings(db, league, packet, s: bre_packet.LeagueSettings):
    # A Configupdate comes from the hub's game; see history.record_game_start.
    seen = packet.processed_at or packet.uploaded_at or utcnow()
    row = db.get(LeagueGameSettings, league.id)
    if row is not None and row.seen_at and row.seen_at > seen:
        return      # an older packet being backfilled; keep the newer settings
    if row is None:
        row = LeagueGameSettings(league_id=league.id)
        db.add(row)
    row.game_started_at = s.game_started_at
    row.protection_turns = s.protection_turns
    row.indiv_attacks_per_day = s.indiv_attacks_per_day
    row.lost_attack_days = s.lost_attack_days
    row.settings = s.settings or None
    row.packet_id = packet.id
    row.seen_at = seen


# ── backfill ──────────────────────────────────────────────────────────────
PACKET_DIRS = ("processed", "outbound", "inbound")
BATCH = 200


def backfill(db: Session, data_dir: Path, redo: bool = False) -> Dict[str, int]:
    """Trace packets stored before this existed, from the files still on disk.

    A file is only used if its SHA-256 matches the row's checksum: filenames are
    reused every thousand packets, so a same-named file may be a later packet.
    """
    counts = {"packets": 0, "traced": 0, "sightings": 0, "missing": 0,
              "mismatched": 0, "failed": 0}
    # A packet already traced has sightings of one kind or the other. One
    # traced before traffic was recorded has only attacks: --redo catches those.
    done = set() if redo else {
        pid for (pid,) in db.query(AttackSighting.packet_id).distinct()
    } | {pid for (pid,) in db.query(TrafficSighting.packet_id).distinct()}
    packets = (
        db.query(Packet).join(League, Packet.league_id == League.id)
        .filter(League.game_type.in_(tuple(_READERS)))
        .order_by(Packet.uploaded_at.asc())
        .all()
    )
    index = _file_index(Path(data_dir))
    for packet in packets:
        if packet.id in done:
            continue
        if packet.filename.upper().startswith(NODELIST_PREFIXES):
            continue
        counts["packets"] += 1
        candidates = _candidates(packet, index.get(packet.filename.upper()))
        if not candidates:
            counts["missing"] += 1
            continue
        content = next((c for c in candidates if not packet.checksum
                        or hashlib.sha256(c).hexdigest() == packet.checksum), None)
        if content is None:
            counts["mismatched"] += 1
            continue
        # One commit per packet costs an fsync each: measured at over ten
        # minutes for production's 21k packets. Batch them, with a savepoint
        # per packet so one that cannot be decoded does not lose the batch.
        savepoint = db.begin_nested()
        try:
            counts["sightings"] += record(db, packet, content, commit=False)
            savepoint.commit()
            counts["traced"] += 1
        except Exception as exc:
            savepoint.rollback()
            counts["failed"] += 1
            logger.warning(f"Could not trace attacks in {packet.filename}: {exc}")
        if counts["packets"] % BATCH == 0:
            db.commit()
    db.commit()
    return counts


def _file_index(data_dir: Path) -> Dict[str, List[Path]]:
    index: Dict[str, List[Path]] = {}
    for sub in PACKET_DIRS:
        folder = data_dir / "packets" / sub
        if folder.is_dir():
            for f in folder.iterdir():
                if f.is_file():
                    index.setdefault(f.name.upper(), []).append(f)
    return index


def _candidates(packet: Packet, paths: Optional[List[Path]]) -> List[bytes]:
    """Every copy of what this row might hold; the checksum decides which is it.

    `file_data` is not always the answer: when an outbound sequence number
    wraps, the collector rewrote the row's checksum but left the old packet's
    bytes in `file_data` -- 1,215 of production's rows. The right bytes are then
    the file on disk. Several dirs may also hold the name (a direct-routed
    packet is both archived and copied to outbound).
    """
    out = [packet.file_data] if packet.file_data else []
    for p in paths or ():
        try:
            out.append(p.read_bytes())
        except OSError:
            continue
    return out


# ── the admin reveal ──────────────────────────────────────────────────────
def forces(db: Session, attack_id: str, data_dir: Optional[Path]):
    """Read an attack's strength, and its cost if the result has been seen,
    back out of a packet the hub stored. Sightings hold no unit counts, so this
    is the only way to them: see the admin reveal in api/management/attacks.py.

    A bre_packet.Forces for a BRE attack, an ib_packet.Forces for an IB one,
    None if no stored packet holds it. A BRE result is preferred, as it echoes
    the attack and adds the outcome; an IB result does not echo, so both are
    read. Only a copy whose checksum matches its row is read (filenames are
    reused).
    """
    rows = (db.query(Packet).join(AttackSighting, AttackSighting.packet_id == Packet.id)
            .filter(AttackSighting.attack_id == attack_id.lower())
            .order_by(AttackSighting.is_result.desc(), Packet.id).all())
    if not rows:
        return None
    league = db.get(League, rows[0].league_id)
    if league is not None and league.game_type == "I":
        ident = int(attack_id.rsplit(".", 1)[-1])
        attack = result = None
        for _, content in stored_contents(rows, data_dir):
            try:
                p = ib_packet.parse(content)
            except ib_packet.PacketFormatError:
                continue
            attack = attack or ib_packet.find_strike(p, ident, False)
            result = result or ib_packet.find_strike(p, ident, True)
        return ib_packet.forces(attack, result)
    for r in stored_records(rows, data_dir):
        if r.type in (bre_packet.INDIV_ATTACK, bre_packet.ATTACK_RESULT) \
                and bre_packet.attack(r).attack_id == attack_id.lower():
            return bre_packet.forces(r)
    return None


def stored_contents(packets: List[Packet], data_dir: Optional[Path]):
    """(packet, bytes) for each of these packets the hub still holds a copy of
    that matches the row's checksum (filenames are reused), in the order given."""
    index = _file_index(Path(data_dir)) if data_dir else {}
    for packet in packets:
        for content in _candidates(packet, index.get(packet.filename.upper())):
            if packet.checksum and hashlib.sha256(content).hexdigest() != packet.checksum:
                continue
            yield packet, content
            break


def stored_records(packets: List[Packet], data_dir: Optional[Path]):
    """Every record of these BRE packets, read back from whichever stored copy
    matches the row's checksum, in the order given."""
    for _, content in stored_contents(packets, data_dir):
        try:
            records = bre_packet.parse(content)
        except bre_packet.PacketFormatError:
            continue
        # A damaged record decodes to garbage, which a reveal would show as fact.
        yield from (r for r in records if r.crc_ok)


# ── the journey ───────────────────────────────────────────────────────────
@dataclass
class Hop:
    is_result: bool
    packet_id: int
    filename: str
    source_bbs: int
    dest_bbs: int
    at_hub: Optional[datetime]
    taken: Optional[datetime]


@dataclass
class Journey:
    attack_id: str
    league_id: int
    from_planet: int
    to_planet: int
    attacker: Optional[str]                # realm letters; IB has none
    target: Optional[str]
    attack_type: Optional[str]
    launched: Optional[datetime]
    resolved: Optional[datetime]           # defender's game clock
    # As the hub knew them when it read the packets (realm_names).
    attacker_name: Optional[str] = None
    target_name: Optional[str] = None
    attack_at_hub: Optional[datetime] = None
    attack_delivered: Optional[datetime] = None
    result_at_hub: Optional[datetime] = None
    result_delivered: Optional[datetime] = None
    lost_attack_days: int = DEFAULT_LOST_ATTACK_DAYS
    game_type: str = "B"
    # The attacker's board clock minus the hub's; None if never measured.
    attacker_clock: Optional[timedelta] = None
    # When the attacker's board rolled over into the due date, bracketed on the
    # hub's clock by its uploads either side of that day's burned sequence number.
    rollover: Optional[Tuple[datetime, datetime]] = None
    # The board whose relay from the hub should be the next hop, when the hub no
    # longer holds any packet it wrote to that board around then (see _unheld).
    unheld_relay_to: Optional[int] = None
    hops: List[Hop] = field(default_factory=list)

    @property
    def stage(self) -> str:
        if self.result_delivered:
            return "result delivered"
        if self.unheld_relay_to is not None:
            return "relay not held"
        if self.result_at_hub:
            return "result awaiting pickup"
        if self.attack_delivered:
            return "awaiting result"
        if self.attack_at_hub:
            return "attack awaiting pickup"
        return "not seen"

    @property
    def mit_due_local(self) -> Optional[datetime]:
        """Midnight on the attacker's clock: the earliest it can give up.

        The game counts in dates from the attack's date, not 24-hour multiples of
        the launch time. It does not give up *at* midnight, though -- see `mit`.
        """
        if not self.launched:
            return None
        day = datetime.combine(self.launched.date(), datetime.min.time())
        if self.game_type == "I":
            # IB gives forces back at the first maintenance once a whole
            # LostForcesDays have passed after the launch day
            # (InFlightStrike.lostForcesDaysLeft); 0 is never.
            if self.lost_attack_days <= 0:
                return None
            return day + timedelta(days=self.lost_attack_days + 1)
        return day + timedelta(days=self.lost_attack_days)

    @property
    def mit_due(self) -> Optional[datetime]:
        """The same moment on the hub's clock, which is what hops are timed by."""
        due = self.mit_due_local
        if due is None:
            return None
        return due - (self.attacker_clock or timedelta(0))

    @property
    def mit(self) -> Optional[str]:
        """Whether the attacker's board wrote this attack off.

        It does so in its first game run of the due date (the "rollover", which
        burns a sequence number) -- unless the result is already in its inbound,
        which that same run imports before it checks for lost attacks. Shown on
        the rig: a result waiting at the first run of the next day is accepted;
        one arriving after a run of that day is "Late ... Packet Deleted". And
        production's boards roll over when their schedule says, not at midnight:
        EOTS at about 00:43 its time, The Eclipse anywhere from minutes to hours
        after.

        An IB board's rollover is its first maintenance of the date, which burns
        nothing the hub can see, and its packets are dated but not timed, so an
        IB result handed over after the due midnight is only ever "possible".

          "late"      handed over after the rollover: discarded
          "possible"  handed over while it was happening, or with no rollover seen
                      yet -- only the attacker's own log can say
          "overdue"   past the due date with no result handed over
          None        in time (possibly after midnight, but before the rollover)
        """
        due = self.mit_due
        if due is None or self.unheld_relay_to is not None:
            return None
        if self.result_delivered is None:
            return "overdue" if _now() >= due else None
        if self.result_delivered < due:
            return None
        if self.rollover is None:
            return "possible"
        opened, closed = self.rollover
        if self.result_delivered <= max(opened, due):
            return None
        return "late" if self.result_delivered >= closed else "possible"


# ── board clocks ──────────────────────────────────────────────────────────
# Boards keep local time and the hub keeps UTC. Production's three boards run at
# +10h, +12h/+13h (New Zealand, with DST) and about +0h. A record's stamp is on
# the sending board's clock and it reaches the hub minutes later, so stamp minus
# arrival is a lower bound on that board's offset -- close for a result, which
# is uploaded as soon as it is resolved; looser for an attack, stamped at the
# start of the player's session. The largest of the observations nearest in time
# (nearest, so a DST change is picked up), rounded to the quarter hour that time
# zones come in, is the offset.
CLOCK_SAMPLES = 6
_QUARTER_HOUR = 900


def _clock_observations(db: Session, league_ids, hub: Optional[int]
                        ) -> Dict[tuple, List[tuple]]:
    q = (db.query(AttackSighting, Packet)
         .join(Packet, AttackSighting.packet_id == Packet.id)
         .filter(AttackSighting.league_id.in_(league_ids),
                 AttackSighting.stamp.isnot(None)))
    obs: Dict[tuple, List[tuple]] = {}
    for s, p in q.all():
        sender = s.to_planet if s.is_result else s.from_planet
        source = _bbs(p.source_bbs_index)
        if source != sender:
            continue                  # relayed: arrival says nothing of its clock
        at_hub = (p.processed_at or p.uploaded_at) if source == hub else p.uploaded_at
        if at_hub:
            obs.setdefault((s.league_id, sender), []).append(
                (at_hub, (s.stamp - at_hub).total_seconds()))
    for v in obs.values():
        v.sort()
    return obs


def _clock_at(obs: List[tuple], when: datetime) -> Optional[timedelta]:
    if not obs:
        return None
    i = bisect.bisect_left(obs, (when,))
    near = sorted(obs[max(0, i - CLOCK_SAMPLES):i + CLOCK_SAMPLES],
                  key=lambda o: abs((o[0] - when).total_seconds()))[:CLOCK_SAMPLES]
    best = max(offset for _, offset in near)
    return timedelta(seconds=round(best / _QUARTER_HOUR) * _QUARTER_HOUR)


# ── rollovers ─────────────────────────────────────────────────────────────
# Once a game day, the first run of the new date burns one sequence number on
# each of a board's routes before writing anything (rig: .009 and .010 consumed
# by two rollovers, the second then writing .011). So on the attacker's route to
# the hub, the uploads either side of the first skipped number after the due
# date bracket the moment it rolled over. Upload rows are a true history --
# each upload gets its own row -- which the hub's own routes were not.
ROLLOVER_SEARCH = timedelta(days=3)


def _rollovers(db: Session, out: List["Journey"], hub: Optional[int]) -> None:
    wanted: Dict[tuple, List[Journey]] = {}
    for j in out:
        if j.mit_due and j.result_delivered and j.result_delivered >= j.mit_due \
                and j.from_planet != hub and hub is not None:
            wanted.setdefault((j.league_id, j.from_planet), []).append(j)
    for (league_id, planet), js in wanted.items():
        lo = min(j.mit_due for j in js) - ROLLOVER_SEARCH
        hi = max(j.mit_due for j in js) + ROLLOVER_SEARCH
        uploads = (db.query(Packet.sequence_number, Packet.uploaded_at)
                   .filter(Packet.league_id == league_id,
                           Packet.source_bbs_index == f"{planet:02X}",
                           Packet.dest_bbs_index == f"{hub:02X}",
                           Packet.uploaded_at >= lo, Packet.uploaded_at <= hi)
                   .order_by(Packet.uploaded_at).all())
        for j in js:
            for (a, at), (b, bt) in zip(uploads, uploads[1:]):
                if bt > j.mit_due and (b - a) % 1000 >= 2:
                    j.rollover = (at, bt)
                    break


# ── history the hub no longer holds ───────────────────────────────────────
# Until 6be2238 the collector reused a row whenever a filename recurred, so every
# packet the hub's game wrote to a board was overwritten a thousand packets
# later, and with it the relay hop of whatever it carried. A journey that stops
# where such a relay should be is then not evidence of anything. It is told apart
# by the route itself: the hub's game writes to a board within minutes of having
# something for it, in the first packet it writes to that board. If the row
# created for that packet was later reused for another -- production's slots
# kept their creation time, months before what they now hold -- the route has
# lost its history there. If it still holds its own packet, as every row will
# from now on, a missing hop is a real miss.
RELAY_WINDOW = timedelta(hours=6)
# A row the collector created is written in the same instant; one written again
# later was reused. (013B, 11 Jan: rows created 03:34-04:38 rewritten at 07:32.)
REWRITTEN_AFTER = timedelta(minutes=5)


def _unheld(db: Session, out: List["Journey"], hub: Optional[int]) -> None:
    if hub is None:
        return
    for j in out:
        if j.result_delivered:
            continue
        if j.result_at_hub:
            since, to = j.result_at_hub, j.from_planet
        elif j.to_planet == hub and j.attack_delivered:
            since, to = j.attack_delivered, j.from_planet   # the hub's own board answered
        elif j.attack_at_hub and not j.attack_delivered:
            since, to = j.attack_at_hub, j.to_planet
        else:
            continue
        if to == hub or _now() - since < RELAY_WINDOW:
            continue
        # The relay goes out in the first packet the hub writes to that board
        # after `since`. Held means that row still holds what it was created
        # with: a reused slot kept its creation time and was written again later.
        first = (db.query(Packet.uploaded_at, Packet.processed_at)
                 .filter(Packet.league_id == j.league_id,
                         Packet.source_bbs_index == f"{hub:02X}",
                         Packet.dest_bbs_index == f"{to:02X}",
                         Packet.uploaded_at >= since,
                         Packet.uploaded_at <= since + RELAY_WINDOW)
                 .order_by(Packet.uploaded_at).first())
        held = first is not None and first.processed_at is not None and \
            first.processed_at - first.uploaded_at < REWRITTEN_AFTER
        if not held:
            j.unheld_relay_to = to


def _bbs(hexstr: str) -> Optional[int]:
    try:
        return int(hexstr, 16)
    except (TypeError, ValueError):
        return None


def journeys(db: Session, hub_index: str, league_id: Optional[int] = None,
             since: Optional[datetime] = None, planet: Optional[int] = None,
             attack_id: Optional[str] = None, limit: int = 500) -> List[Journey]:
    q = db.query(AttackSighting, Packet).join(Packet, AttackSighting.packet_id == Packet.id)
    if league_id is not None:
        q = q.filter(AttackSighting.league_id == league_id)
    if attack_id:
        q = q.filter(AttackSighting.attack_id.startswith(attack_id.lower()))
    if planet is not None:
        q = q.filter((AttackSighting.from_planet == planet)
                     | (AttackSighting.to_planet == planet))
    if since is not None:
        # Window on when the attack was launched, taken from its attack
        # sightings -- a result's stamp is the defender's, and later.
        ids = (db.query(AttackSighting.attack_id)
               .filter(AttackSighting.is_result.is_(False),
                       AttackSighting.stamp >= since))
        q = q.filter(AttackSighting.attack_id.in_(ids))

    games = {lg.id: lg.game_type for lg in db.query(League.id, League.game_type)}
    # BRE has no "never"; IB's 0 is one.
    lost_days = {s.league_id: s.lost_attack_days
                 for s in db.query(LeagueGameSettings).all()
                 if s.lost_attack_days or (s.lost_attack_days == 0
                                           and games.get(s.league_id) == "I")}
    hub = _bbs(hub_index)

    by_id: Dict[str, Journey] = {}
    for s, p in q.all():
        j = by_id.get(s.attack_id)
        if j is None:
            j = by_id[s.attack_id] = Journey(
                attack_id=s.attack_id, league_id=s.league_id,
                from_planet=s.from_planet, to_planet=s.to_planet,
                attacker=s.attacker, target=s.target, attack_type=s.attack_type,
                launched=None, resolved=None,
                game_type=games.get(s.league_id, "B"),
                lost_attack_days=lost_days.get(
                    s.league_id, DEFAULT_LOST_DAYS.get(games.get(s.league_id), 7)),
            )
        dest = _bbs(p.dest_bbs_index)
        # A packet for the hub's own board is taken when the hub's game runs it;
        # any other is taken when that board's client downloads it.
        taken = p.processed_at if dest == hub else p.downloaded_at
        # A packet the hub's own game wrote reached the hub when it was
        # collected. uploaded_at is not that: the collector reuses the row when
        # a sequence number wraps, and uploaded_at keeps the first packet's time.
        source = _bbs(p.source_bbs_index)
        at_hub = (p.processed_at or p.uploaded_at) if source == hub else p.uploaded_at
        j.hops.append(Hop(s.is_result, p.id, p.filename, source, dest, at_hub, taken))
        # The earliest sighting that named them: what they were when launched.
        j.attacker_name = j.attacker_name or s.attacker_name
        j.target_name = j.target_name or s.target_name
        if s.is_result:
            j.resolved = _earliest(j.resolved, s.stamp)
            j.result_at_hub = _earliest(j.result_at_hub, at_hub)
            if dest == s.from_planet and taken:
                j.result_delivered = _earliest(j.result_delivered, taken)
        else:
            j.launched = j.launched or s.stamp
            j.attack_type = j.attack_type or s.attack_type
            j.attack_at_hub = _earliest(j.attack_at_hub, at_hub)
            if dest == s.to_planet and taken:
                j.attack_delivered = _earliest(j.attack_delivered, taken)

    out = list(by_id.values())
    # Clocks, rollovers and lost relays are read from BRE's timed stamps,
    # sequence numbers and reused rows; IB has none of the three.
    bre = [j for j in out if j.game_type == "B"]
    clocks = _clock_observations(db, {j.league_id for j in bre}, hub) if bre else {}
    for j in out:
        j.hops.sort(key=lambda h: (h.at_hub or datetime.min, h.is_result))
        when = j.attack_at_hub or j.launched
        if when and j.game_type == "B":
            j.attacker_clock = _clock_at(clocks.get((j.league_id, j.from_planet)), when)
    _rollovers(db, bre, hub)
    _unheld(db, bre, hub)
    out.sort(key=lambda j: j.launched or j.attack_at_hub or datetime.min, reverse=True)
    return out[:limit]


def _earliest(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)
