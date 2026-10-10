"""Where each BRE attack got to: the journey behind a Missing In Transit.

See backend/services/attack_trace.py for how a journey is assembled, and
backend/services/bre_packet.py for how an attack is read out of a packet.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query
from sqlalchemy.orm import Session

from backend.core.config import get_config
from backend.core.database import get_db
from backend.core.security import require_admin
from backend.logging_config import get_logger
from backend.models.database import League, SysopUser
from backend.schemas.attacks import AttackForces, AttackHop, AttackJourney
from backend.services import attack_trace, bre_packet

router = APIRouter()
logger = get_logger(context="management_attacks")

DEFAULT_WINDOW_DAYS = 14
MAX_WINDOW_DAYS = 365
STAGES = ("result delivered", "result awaiting pickup", "awaiting result",
          "attack awaiting pickup")


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat() if t else None


@router.get("/", response_model=List[AttackJourney], summary="List Attack Journeys")
async def list_attacks(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS),
    league_id: Optional[int] = Query(None),
    planet: Optional[int] = Query(None, description="Attacker's or target's planet"),
    attack_id: Optional[str] = Query(None, pattern="^[0-9a-fA-F]{1,16}$",
                                     description="ID or a prefix of it; ignores `days`"),
    stage: Optional[str] = Query(None),
    mit: Optional[str] = Query(None, pattern="^(late|possible|overdue|any)$"),
    limit: int = Query(500, ge=1, le=2000),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Individual attacks, most recently launched first, each with its hops.

    **Query Parameters:**
    - `days`: launched within this many days (default 14)
    - `league_id`, `planet`: filters; `planet` matches either side
    - `attack_id`: find one attack by ID, whatever its age
    - `stage`: only attacks whose journey stopped at this stage
    - `mit`: `late` (result handed over after the attacker's board rolled into the
      due date, so discarded), `possible` (handed over while it rolled over, or no
      rollover seen yet), `overdue` (past the due date, no result delivered), or `any`
    """
    hub_index = get_config().get("hub", {}).get("bbs_index", "01")
    since = None if attack_id else datetime.utcnow() - timedelta(days=days)
    found = attack_trace.journeys(db, hub_index, league_id=league_id, since=since,
                                  planet=planet, attack_id=attack_id, limit=2000)
    if stage:
        found = [j for j in found if j.stage == stage]
    if mit:
        found = [j for j in found if j.mit and (mit == "any" or j.mit == mit)]

    names = {lg.id: lg.name for lg in db.query(League).all()}
    return [
        AttackJourney(
            attack_id=j.attack_id,
            league_id=j.league_id,
            league_name=names.get(j.league_id),
            from_planet=j.from_planet,
            to_planet=j.to_planet,
            attacker=j.attacker,
            target=j.target,
            attack_type=j.attack_type,
            launched=_iso(j.launched),
            resolved=_iso(j.resolved),
            attack_at_hub=_iso(j.attack_at_hub),
            attack_delivered=_iso(j.attack_delivered),
            result_at_hub=_iso(j.result_at_hub),
            result_delivered=_iso(j.result_delivered),
            stage=j.stage,
            lost_attack_days=j.lost_attack_days,
            mit_due_local=_iso(j.mit_due_local),
            mit_due=_iso(j.mit_due),
            attacker_clock_minutes=(None if j.attacker_clock is None
                                    else int(j.attacker_clock.total_seconds() // 60)),
            rollover_after=_iso(j.rollover[0]) if j.rollover else None,
            rollover_before=_iso(j.rollover[1]) if j.rollover else None,
            unheld_relay_to=j.unheld_relay_to,
            mit=j.mit,
            hops=[AttackHop(is_result=h.is_result, packet_id=h.packet_id,
                            filename=h.filename, source_bbs=h.source_bbs,
                            dest_bbs=h.dest_bbs, at_hub=_iso(h.at_hub),
                            taken=_iso(h.taken)) for h in j.hops],
        )
        for j in found[:limit]
    ]


@router.get("/{attack_id}/forces", response_model=AttackForces,
            summary="Reveal an Attack's Forces (admin)")
async def reveal_forces(
    attack_id: str = PathParam(..., pattern="^[0-9a-fA-F]{16}$"),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Forces sent, and once resolved, whether it won, the losses, and regions taken.

    Hidden game state, so admin only, one attack per request, and never part of
    the listing: the UI fetches it only when the admin clicks to reveal. Each
    reveal is logged.
    """
    data_dir = Path(get_config().get("server", {}).get("data_dir", "./data"))
    f = attack_trace.forces(db, attack_id, data_dir)
    if f is None:
        raise HTTPException(status_code=404, detail="No stored packet holds this attack")
    logger.info(f"Attack {attack_id.lower()} forces revealed to {current_user.username}")
    sent = {u: getattr(f, u) for u in bre_packet.UNITS}
    lost = f.lost()
    return AttackForces(
        attack_id=attack_id.lower(),
        sent=sent,
        carriers=f.carriers,
        resolved=lost is not None,
        success=f.success,
        regions_captured=f.regions_captured,
        loss_percent=None if f.loss_fraction is None else round(f.loss_fraction * 100, 1),
        lost=lost,
        returned=None if lost is None else {u: sent[u] - lost[u] for u in sent},
        defenders_destroyed=f.defenders_destroyed,
    )
