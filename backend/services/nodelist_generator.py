"""
backend/services/nodelist_generator.py

Generate BRNODES.<league> / FENODES.<league> nodelist files from the database.

The format mirrors the BRE/FE nodes.dat format:
  <bbs_index>            -- or "<hub_index> HOST <idx> <idx> ..." for the hub
  <bbs_name>
  <fidonet_address>
  <city>
  <state>
  <country>
  (blank line)
  ...

Two things this file gets wrong if you let it: the hub's own entry, and the
HOST line.

The hub is node 1 of every league it runs, but it is not a Client row -- it has
no OAuth credentials and never syncs -- so iterating memberships alone produces
a nodelist with no hub in it. Its name and index come from [hub] config and its
FidoNet address from League.hub_fidonet_address, which is per-league.

Line 1 of the hub's entry carries the routing topology in the game's own
"<index> HOST <targets...>" form. With no route.cfg present -- and none of the
production installs has one -- that line is the only thing telling the game
where mail goes, so a nodelist without it is not a degraded nodelist, it is a
broken one. The targets are exactly the league's member indices. League 013 is
the exception and carries a bare index, which League.hub_routes_mail records so
that generating a nodelist cannot quietly change how a league routes.

Because a partial nodelist is worse than a stale one, generate() refuses to
write anything it cannot construct completely and leaves the previous file in
place.
"""

import os
from pathlib import Path

from sqlalchemy.orm import Session

from backend.logging_config import get_logger
from backend.models.database import League, LeagueMembership, Client

logger = get_logger(context="nodelist_generator")


def _sanitize_nodelist_field(value: str | None) -> str:
    """Return a line-safe representation for nodes.dat fields."""
    if not value:
        return ""
    value = value.replace("\r", " ").replace("\n", " ")
    return "".join(ch for ch in value if ord(ch) >= 32 and ord(ch) != 127).strip()


def _entry(index_line: str, name: str, fidonet: str, city: str, state: str, country: str) -> list[str]:
    """One 6-line nodes.dat entry plus its blank separator."""
    return [index_line, name, fidonet, city, state, country, ""]


class NodelistGenerator:
    """Generate hub-side nodelist files from league membership data."""

    def __init__(self, db: Session, data_dir: str, hub_config: dict | None = None):
        self.db = db
        self.data_dir = Path(data_dir)
        self.hub_config = hub_config or {}

    def generate(self, league_db_id: int) -> Path | None:
        """
        Generate a nodelist file for the given league.

        Returns the path of the written file, or None if the nodelist could not
        be built completely -- no active members with a BBS index, or no
        hub_fidonet_address for the league. Returning None leaves any existing
        file untouched.
        """
        league = self.db.query(League).filter(League.id == league_db_id).first()
        if not league:
            logger.warning(f"generate: league {league_db_id} not found")
            return None

        hub_index = self._hub_index()

        memberships = (
            self.db.query(LeagueMembership)
            .filter(
                LeagueMembership.league_id == league_db_id,
                LeagueMembership.is_active == True,
                LeagueMembership.bbs_index.isnot(None),
            )
            .order_by(LeagueMembership.bbs_index)
            .all()
        )

        # The hub is emitted from config, not from a membership. If someone has
        # also registered it as a client, the config entry wins and the
        # duplicate is dropped rather than written twice.
        members = []
        for membership in memberships:
            if membership.bbs_index == hub_index:
                logger.warning(
                    f"generate: league {league.full_id} has a membership at the hub's "
                    f"own index {hub_index}; ignoring it in favour of [hub] config"
                )
                continue
            members.append(membership)

        if not members:
            logger.info(f"generate: no active members with BBS index for league {league.full_id}")
            return None

        hub_fidonet = _sanitize_nodelist_field(league.hub_fidonet_address)
        if not hub_fidonet:
            logger.error(
                f"generate: league {league.full_id} has no hub_fidonet_address, so the "
                f"hub's own node {hub_index} and its HOST routing line cannot be written. "
                f"Refusing to generate a nodelist without them; any existing file is left "
                f"in place. Set it with PUT /management/api/v1/leagues/{league.id}."
            )
            return None

        hub_name = _sanitize_nodelist_field(self.hub_config.get("bbs_name")) or "Nova Hub"

        # "1 HOST 2 3 4" -- the game's routing directive, not decoration.
        # A league that does not route through the hub gets a bare index, and
        # writing HOST over the top of one would silently re-point its mail.
        targets = " ".join(str(m.bbs_index) for m in members)
        routes_mail = league.hub_routes_mail is not False
        index_line = f"{hub_index} HOST {targets}" if routes_mail else str(hub_index)
        lines = _entry(
            index_line,
            hub_name,
            hub_fidonet,
            _sanitize_nodelist_field(self.hub_config.get("city")),
            _sanitize_nodelist_field(self.hub_config.get("state")),
            _sanitize_nodelist_field(self.hub_config.get("country")),
        )

        for membership in members:
            client = self.db.query(Client).filter(Client.id == membership.client_id).first()
            fallback = f"BBS {membership.bbs_index}"
            raw_bbs_name = client.bbs_name if client else fallback
            lines += _entry(
                str(membership.bbs_index),
                _sanitize_nodelist_field(raw_bbs_name) or fallback,
                _sanitize_nodelist_field(membership.fidonet_address),
                _sanitize_nodelist_field(client.city if client else None),
                _sanitize_nodelist_field(client.state if client else None),
                _sanitize_nodelist_field(client.country if client else None),
            )

        # DOS line endings. Every nodes.dat the games actually use is CRLF --
        # the hand-written production files and the provisioner's output both --
        # and this generator emitted LF, which is the other half of why its
        # output was not a drop-in replacement for the file it overwrote.
        content = "\r\n".join(lines) + "\r\n"

        # Write to nodelists directory
        game_type_str = "bre" if league.game_type == "B" else "fe"
        nodelist_dir = self.data_dir / "nodelists" / game_type_str / league.league_id
        nodelist_dir.mkdir(parents=True, exist_ok=True)

        prefix = "BRNODES" if league.game_type == "B" else "FENODES"
        filename = f"{prefix}.{league.league_id}"
        dest = nodelist_dir / filename

        self._write_atomic(dest, content)
        logger.info(
            f"Generated nodelist {filename} for league {league.full_id} "
            f"(hub entry {index_line!r}; {len(members)} member(s))"
        )
        return dest

    def _hub_index(self) -> int:
        """The hub's node number. [hub] bbs_index is a string like "01"."""
        raw = self.hub_config.get("bbs_index", "01")
        try:
            return int(str(raw), 10)
        except (TypeError, ValueError):
            logger.warning(f"generate: unparseable [hub] bbs_index {raw!r}; assuming 1")
            return 1

    @staticmethod
    def _write_atomic(dest: Path, content: str) -> None:
        """
        Replace dest in one step.

        A game reading a half-written nodes.dat is the kind of fault that shows
        up days later as mis-routed mail, so the new file is built alongside and
        renamed over the old one. Written as bytes so the CRLF endings survive
        whatever the platform would otherwise translate them into.
        """
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(content.encode("utf-8"))
        os.replace(tmp, dest)
