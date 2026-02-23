"""
backend/services/nodelist_generator.py

Generate BRNODES.<league> / FENODES.<league> nodelist files from the database.

The format mirrors the BRE/FE nodes.dat format:
  <bbs_index>
  <bbs_name>
  <fidonet_address>
  <city>
  <state>
  <country>
  (blank line)
  ...

Fields not stored in the database (city, state, country) are written as empty
lines so that the existing NodesFileParser continues to work.
"""

from pathlib import Path

from sqlalchemy.orm import Session

from backend.logging_config import get_logger
from backend.models.database import League, LeagueMembership, Client

logger = get_logger(context="nodelist_generator")


class NodelistGenerator:
    """Generate hub-side nodelist files from league membership data."""

    def __init__(self, db: Session, data_dir: str):
        self.db = db
        self.data_dir = Path(data_dir)

    def generate(self, league_db_id: int) -> Path | None:
        """
        Generate a nodelist file for the given league.

        Returns the path of the written file, or None if the league has no
        active members with a BBS index assigned.
        """
        league = self.db.query(League).filter(League.id == league_db_id).first()
        if not league:
            logger.warning(f"generate: league {league_db_id} not found")
            return None

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

        if not memberships:
            logger.info(f"generate: no active members with BBS index for league {league.full_id}")
            return None

        # Build nodelist content
        lines = []
        for membership in memberships:
            client = self.db.query(Client).filter(Client.id == membership.client_id).first()
            bbs_name = client.bbs_name if client else f"BBS {membership.bbs_index}"
            fidonet = membership.fidonet_address or ""

            lines.append(str(membership.bbs_index))
            lines.append(bbs_name)
            lines.append(fidonet)
            lines.append("")  # city
            lines.append("")  # state
            lines.append("")  # country
            lines.append("")  # blank separator between entries

        content = "\n".join(lines) + "\n"

        # Write to nodelists directory
        game_type_str = "bre" if league.game_type == "B" else "fe"
        nodelist_dir = self.data_dir / "nodelists" / game_type_str / league.league_id
        nodelist_dir.mkdir(parents=True, exist_ok=True)

        prefix = "BRNODES" if league.game_type == "B" else "FENODES"
        filename = f"{prefix}.{league.league_id}"
        dest = nodelist_dir / filename

        dest.write_text(content, encoding="utf-8")
        logger.info(
            f"Generated nodelist {filename} for league {league.full_id} "
            f"({len(memberships)} member(s))"
        )
        return dest
