"""Checking a reset's settings, and keeping each league's one draft.

validate() is the gate everything after it trusts: the driver will type these
values into the game's editor, and an over-max number or a letter the field
doesn't take would throw it out of step. So it rejects rather than clamps, and
returns every problem at once for the form to show beside its field.
"""
import re
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.models.database import GameReset, League
from backend.services.game_reset.fields import (
    CHOICE, DATE, NUMBER, TEXT, TOGGLE, defaults, fields_for,
)

# The editor takes a year up to 2999 (Join Date's "no cut-off").
MAX_YEAR = 2999
# IB's required version: a release number, which IB itself prints with a "v".
VERSION = re.compile(r"\d+(\.\d+){0,3}")


class DraftConflict(Exception):
    """The draft was saved by someone else since this edit began."""


def _date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def validate(game_type: str, settings: dict, today: date) -> tuple[dict, list]:
    """Return (normalised settings, [(field id, message), ...]).

    A field left out takes the game's default. An unknown field is an error.
    `sabre_dial` is kept only when Sabre is Constant.
    """
    fields = fields_for(game_type)
    known = {f.id for f in fields}
    errors = [(k, "Not a setting this game has.") for k in settings if k not in known]
    values = {**defaults(game_type), **{k: v for k, v in settings.items() if k in known}}

    out = {}
    for f in fields:
        v = values[f.id]
        if f.depends_on and values.get(f.depends_on[0]) != f.depends_on[1]:
            continue
        if f.kind == NUMBER:
            if isinstance(v, bool) or not isinstance(v, int):
                errors.append((f.id, "Must be a whole number."))
                continue
            if v < f.min:
                errors.append((f.id, f"At least {f.min}."))
                continue
            if v > f.max:
                errors.append((f.id, f"At most {f.max}: the game's own limit."))
                continue
        elif f.kind == CHOICE:
            letters = [letter for letter, _ in f.choices]
            if v not in letters:
                errors.append((f.id, f"One of {', '.join(letters)}."))
                continue
        elif f.kind == TOGGLE:
            if not isinstance(v, bool):
                errors.append((f.id, "Must be on or off."))
                continue
        elif f.kind == DATE:
            if v is None and f.default is None:
                out[f.id] = None
                continue
            try:
                d = _date(v)
            except (TypeError, ValueError):
                errors.append((f.id, "Must be a date (YYYY-MM-DD)."))
                continue
            if d < today:
                errors.append((f.id, "Can't be in the past."))
                continue
            if d.year > MAX_YEAR:
                errors.append((f.id, f"The game takes years up to {MAX_YEAR}."))
                continue
            v = d.isoformat()
        elif f.kind == TEXT:
            if v is None:
                v = ""
            if not isinstance(v, str):
                errors.append((f.id, "Must be text."))
                continue
            v = v.strip()
            v = v[1:] if v[:1] in ("v", "V") else v
            if v and not VERSION.fullmatch(v):
                errors.append((f.id, "A version like 0.2.6, or blank."))
                continue
        out[f.id] = v

    start = out.get("start_date") or today.isoformat()
    if out.get("join_date") and out["join_date"] < start:
        errors.append(("join_date", "Can't be before the game starts."))
    return out, errors


def warnings(game_type: str, settings: dict) -> list:
    """[(field id, message), ...] for values below our advised minimums.

    Allowed, unlike errors: a league may mean it. The form shows them, and
    performing a reset will list them for a second look.
    """
    out = []
    for f in fields_for(game_type):
        v = settings.get(f.id)
        if f.advised_min is not None and isinstance(v, int) and v < f.advised_min:
            out.append((f.id, f"Below the usual minimum of {f.advised_min}: "
                              f"{f.advised_reason}"))
    return out


def current_draft(db: Session, league: League) -> Optional[GameReset]:
    return (db.query(GameReset)
            .filter(GameReset.league_id == league.id, GameReset.status == "draft")
            .first())


def last_applied(db: Session, league: League) -> Optional[GameReset]:
    """The last reset performed from the console. An observed one (history.py)
    has no start date setting, and older rows only three settings; what the
    league runs with now is the form's `observed`, its latest Configupdate."""
    return (db.query(GameReset)
            .filter(GameReset.league_id == league.id, GameReset.status == "applied",
                    GameReset.source == "console")
            .order_by(GameReset.applied_at.desc())
            .first())


def save_draft(db: Session, league: League, settings: dict, notes: Optional[str],
               base_version: Optional[int], username: str) -> GameReset:
    """Create or update the league's draft. The caller has validated `settings`
    and commits.

    `base_version` is the version the editor loaded (None when it started a new
    draft). Anything else means someone saved in between, and saving would
    silently overwrite their changes.
    """
    draft = current_draft(db, league)
    now = utcnow()
    if draft is None:
        if base_version is not None:
            raise DraftConflict("The draft you were editing has been discarded "
                                "or performed.")
        draft = GameReset(league_id=league.id, status="draft", source="console",
                          settings=settings, notes=notes, version=1,
                          created_by=username, created_at=now,
                          updated_by=username, updated_at=now)
        db.add(draft)
        return draft
    if base_version != draft.version:
        raise DraftConflict(f"{draft.updated_by} saved this draft since you opened it.")
    draft.settings = settings
    draft.notes = notes
    draft.version += 1
    draft.updated_by = username
    draft.updated_at = now
    return draft
