"""What moved between nodes, for an admin to look at.

Deliberately descriptive rather than diagnostic. The hub cannot tell a quiet
league from a broken one -- that depends on how many boards are playing and how
active their players are -- so this reports what happened and leaves the
judgement to someone who knows what their league should look like.

The data comes from the /DETAILED transcript of each processing run, stored per
item as the run finishes. See backend/services/transcript_service.py.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.logging_config import get_logger
from backend.models.database import League, ProcessingRunItem, SysopUser
from backend.schemas.movements import (
    DayCount,
    Movement,
    MovementSummary,
    PairCount,
    TypeCount,
)

logger = get_logger(context="management_movements")

router = APIRouter()

DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 365


def _window(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


def _filtered(db: Session, days, league_id, direction, item_type, node, run_id=None):
    q = db.query(ProcessingRunItem)
    if run_id is not None:
        # One run is a closed set: it either has items or it does not, and the
        # time window is meaningless against it.
        q = q.filter(ProcessingRunItem.processing_run_id == run_id)
    else:
        q = q.filter(ProcessingRunItem.occurred_at >= _window(days))
    if league_id is not None:
        q = q.filter(ProcessingRunItem.league_id == league_id)
    if direction:
        q = q.filter(ProcessingRunItem.direction == direction)
    if item_type:
        q = q.filter(ProcessingRunItem.item_type == item_type)
    if node is not None:
        # "Involving this node", either end -- which is what someone chasing a
        # specific board's traffic actually wants.
        q = q.filter(
            (ProcessingRunItem.src_node == node) | (ProcessingRunItem.dst_node == node)
        )
    return q


@router.get("/", response_model=List[Movement], summary="List Item Movements")
async def list_movements(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS),
    league_id: Optional[int] = Query(None),
    direction: Optional[str] = Query(None, pattern="^(in|out)$"),
    item_type: Optional[str] = Query(None),
    node: Optional[int] = Query(None),
    run_id: Optional[int] = Query(None, description="One run's items; ignores `days`"),
    limit: int = Query(200, ge=1, le=2000),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Individual item movements, most recent first.

    **Query Parameters:**
    - `days`: how far back to look (default 7)
    - `league_id`, `direction` (`in`/`out`), `item_type`, `node`: filters
    - `node` matches either end of the transfer
    - `run_id`: just this run's items, whatever their age
    """
    rows = (
        _filtered(db, days, league_id, direction, item_type, node, run_id)
        .order_by(ProcessingRunItem.occurred_at.desc(), ProcessingRunItem.id.desc())
        .limit(limit)
        .all()
    )

    names = {lg.id: lg.name for lg in db.query(League).all()}
    return [
        Movement(
            id=r.id,
            processing_run_id=r.processing_run_id,
            league_id=r.league_id,
            league_name=names.get(r.league_id),
            occurred_at=r.occurred_at.isoformat() if r.occurred_at else None,
            direction=r.direction,
            item_type=r.item_type,
            src_node=r.src_node,
            dst_node=r.dst_node,
            size_before=r.size_before,
            size_after=r.size_after,
            phase=r.phase,
        )
        for r in rows
    ]


@router.get("/summary", response_model=MovementSummary, summary="Summarise Item Movements")
async def summarise_movements(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS),
    league_id: Optional[int] = Query(None),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The shape of the traffic: by type, by node pair, and by day.

    `quiet_nodes` lists boards this league has exchanged nothing with in the
    window, which is the one thing worth pointing at -- not because it is
    necessarily wrong (a node can be legitimately idle) but because it is the
    question an admin would otherwise have to work out by reading the table.
    """
    since = _window(days)
    base = _filtered(db, days, league_id, None, None, None)

    total = base.count()
    runs = base.with_entities(
        func.count(func.distinct(ProcessingRunItem.processing_run_id))
    ).scalar() or 0

    by_type = [
        TypeCount(direction=d, item_type=t, count=n)
        for d, t, n in base.with_entities(
            ProcessingRunItem.direction,
            ProcessingRunItem.item_type,
            func.count(ProcessingRunItem.id),
        ).group_by(ProcessingRunItem.direction, ProcessingRunItem.item_type)
        .order_by(func.count(ProcessingRunItem.id).desc())
        .all()
    ]

    by_pair = [
        PairCount(src_node=s, dst_node=d, count=n)
        for s, d, n in base.with_entities(
            ProcessingRunItem.src_node,
            ProcessingRunItem.dst_node,
            func.count(ProcessingRunItem.id),
        ).group_by(ProcessingRunItem.src_node, ProcessingRunItem.dst_node)
        .order_by(func.count(ProcessingRunItem.id).desc())
        .all()
    ]

    by_day = [
        DayCount(day=str(day), count=n)
        for day, n in base.with_entities(
            func.date(ProcessingRunItem.occurred_at),
            func.count(ProcessingRunItem.id),
        ).group_by(func.date(ProcessingRunItem.occurred_at))
        .order_by(func.date(ProcessingRunItem.occurred_at))
        .all()
    ]

    seen = {p.src_node for p in by_pair} | {p.dst_node for p in by_pair}
    seen.discard(None)
    known = _known_nodes(db, league_id)
    quiet = sorted(known - seen)

    return MovementSummary(
        since=since.isoformat(),
        total=total,
        runs=runs,
        by_type=by_type,
        by_pair=by_pair,
        by_day=by_day,
        quiet_nodes=quiet,
    )


def _known_nodes(db: Session, league_id: Optional[int]) -> set:
    """Every node this league has ever exchanged an item with.

    Taken from the movement history rather than the membership table on purpose:
    the transcript speaks in the game's own node numbers, and membership is keyed
    by BBS index. Mapping between the two is a separate job, and getting it
    subtly wrong here would mean naming the wrong board as quiet.
    """
    q = db.query(ProcessingRunItem.src_node, ProcessingRunItem.dst_node)
    if league_id is not None:
        q = q.filter(ProcessingRunItem.league_id == league_id)
    nodes = set()
    for src, dst in q.distinct().all():
        nodes.update(n for n in (src, dst) if n is not None)
    return nodes
