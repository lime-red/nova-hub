"""Log each member board's League Status when it changes, so a log alert can see it.

The League Status (game_status) is computed when someone opens a page, and
nothing else reads it. That is how a board can sit in "Hub game has not heard
from it" with nobody told (prod 015I, Nov 2026: The Eclipse's client synced the
league but never ran its maintenance, and nothing errored anywhere). This
evaluates every active league's members on a timer and writes one log line per
change of state: WARNING on going into any state but ok, INFO on coming back.

Only changes are logged, so a state that persists is one line, not one a pass.
What was last seen is kept in memory: a restart logs every board not ok once
more, which is the right reading for someone who missed the first.

The lines all start with "Member status:", which is what the OpenObserve alert
matches. Logging only: the hub's own [alerting] channels are not used, so a
noisy state can be tuned out in the alert without a release.
"""
from pathlib import Path
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from backend.logging_config import get_logger
from backend.models.database import League
from backend.services import game_status

logger = get_logger(context="member_watch")

CHECK_EVERY_SECONDS = 5 * 60

Key = Tuple[int, int]                      # (league id, client id)
Seen = Dict[Key, Tuple[str, Optional[str]]]


def check(db: Session, config: dict, seen: Seen) -> Seen:
    """One pass: log what changed since `seen`, and return what is true now."""
    hub_index = game_status.hub_index_from(config)
    data_dir = Path(config.get("server", {}).get("data_dir", "./data"))
    now: Seen = {}
    for league in db.query(League).filter(League.is_active == True).all():
        for s in game_status.for_league(db, league, hub_index, data_dir, config):
            if s.is_hub:
                continue
            key = (league.id, s.client.id)
            state = (s.state, s.state_detail)
            now[key] = state
            before = seen.get(key)
            if before is not None and before[0] == s.state:
                continue
            index = s.member.bbs_index if s.member else None
            where = (f"{league.league_id}{league.game_type} "
                     f"{f'{index:02X}' if index is not None else '--'} {s.client.client_id}")
            label = game_status.STATES.get(s.state, s.state)
            detail = f" ({s.state_detail})" if s.state_detail else ""
            if s.state != "ok":
                logger.warning(f"Member status: {where} is {s.state}: {label}{detail}")
            elif before is not None:
                logger.info(f"Member status: {where} is ok again (was {before[0]})")
    return now
