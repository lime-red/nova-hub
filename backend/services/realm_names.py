"""The names of the realms in a BRE league, as the hub last heard them.

Attacks name realms by planet and letter, and ops, trade deals and messages by
player ID; neither says who that is. Each planet's Recon Update does, slot by
slot (bre_packet.realms), so the hub keeps the latest one and names the realms
in a sighting as it reads the packet. The sighting keeps that name: a letter is
taken by someone else after a reset or a deleted realm, and what an old attack
should show is who held it then.

Nothing is backfilled. A packet read before the hub had heard its planet's
realms has no names, which is the truth of what the hub knew.
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.database import RealmName
from backend.services import bre_packet


def remember(db: Session, league_id: int, planet: int, realms: List[bre_packet.Realm],
             packet_id: Optional[int], seen: datetime) -> None:
    """Take a planet's realm list. A slot it leaves empty is forgotten, so a
    deleted realm's name is not given to the next one. A list older than the
    one already held (a backfill, or a relay arriving late) changes nothing."""
    rows = {r.letter: r for r in db.query(RealmName).filter(
        RealmName.league_id == league_id, RealmName.planet == planet)}
    if any(r.seen_at and r.seen_at > seen for r in rows.values()):
        return
    listed = set()
    for realm in realms:
        listed.add(realm.letter)
        row = rows.get(realm.letter)
        if row is None:
            row = RealmName(league_id=league_id, planet=planet, letter=realm.letter)
            db.add(row)
        row.player_id, row.name = realm.player_id, realm.name[:64]
        row.packet_id, row.seen_at = packet_id, seen
    for letter, row in rows.items():
        if letter not in listed:
            db.delete(row)
    db.flush()


class Names:
    """A league's realm names, read once per packet."""

    def __init__(self, db: Session, league_id: int):
        self._by_letter: Dict[Tuple[int, str], str] = {}
        self._by_player: Dict[Tuple[int, int], str] = {}
        for r in db.query(RealmName).filter(RealmName.league_id == league_id):
            self._by_letter[(r.planet, r.letter)] = r.name
            if r.player_id is not None:
                self._by_player[(r.planet, r.player_id)] = r.name

    def name(self, planet: Optional[int], letter: Optional[str] = None,
             player: Optional[int] = None) -> Optional[str]:
        """By player ID where the record has one, else by letter. None if the
        hub has not heard of that realm.

        An ID the hub has not heard of is not looked up by letter instead: on
        production that was always a realm newer than its planet's last Recon
        Update, whose letter the hub still had down for someone else."""
        if planet is None:
            return None
        if player is not None:
            return self._by_player.get((planet, player))
        if letter:
            return self._by_letter.get((planet, letter))
        return None
