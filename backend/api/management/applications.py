"""Applications: sysops ask to join or for more leagues; the admin decides.

A sysop's own routes (the leagues open to join, their applications, applying
and withdrawing) take any session: a sysop applies before they run anything
here. The queue and the decisions are admin only. See
backend/services/applications.py.
"""

from typing import List, Optional, Union

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.management.claim import public_url, utc
from backend.core.config import get_config
from backend.core.database import get_db
from backend.core.security import get_current_user, require_admin
from backend.logging_config import get_logger
from backend.models.database import Application, Client, League, LeagueMembership, SysopUser
from backend.schemas.applications import (
    ApplicationResponse,
    ApplicationReview,
    ApproveRequest,
    ApproveResponse,
    GrantedLeague,
    JoinableLeague,
    JoinApplicationRequest,
    LeagueRequest,
    LeagueSuggestion,
    RejectRequest,
    TakenIndex,
)
from backend.services import applications
from backend.services.games import game_for_letter

logger = get_logger(context="applications")

router = APIRouter()


def _league(league: League) -> JoinableLeague:
    try:
        game = game_for_letter(league.game_type).code
    except KeyError:
        game = league.game_type
    return JoinableLeague(id=league.id, full_id=league.full_id, name=league.name, game=game)


def _response(db: Session, application: Application) -> ApplicationResponse:
    leagues = db.query(League).filter(League.id.in_(application.league_ids or [])).all()
    return ApplicationResponse(
        id=application.id,
        kind=application.kind,
        status=application.status,
        applicant=application.applicant,
        email=application.email,
        client_id=application.client_id,
        bbs_name=application.bbs_name,
        sysop_name=application.sysop_name,
        city=application.city,
        state=application.state,
        country=application.country,
        address=application.address,
        os=application.os,
        leagues=[_league(lg) for lg in sorted(leagues, key=lambda lg: lg.full_id)],
        note=application.note,
        created_at=utc(application.created_at),
        reviewed_by=application.reviewed_by,
        reviewed_at=utc(application.reviewed_at),
        decision_note=application.decision_note,
        granted=[GrantedLeague(**g) for g in (application.granted or [])],
    )


def _refused(e: applications.Refused) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


# --- the sysop's side --------------------------------------------------------

@router.get("/leagues", response_model=List[JoinableLeague], summary="Leagues Open to Join")
async def joinable_leagues(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The leagues a sysop can ask to join: names and games only."""
    return [_league(lg) for lg in applications.joinable_leagues(db)]


@router.get("/mine", response_model=List[ApplicationResponse], summary="My Applications")
async def my_applications(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The signed-in user's own applications, newest first."""
    rows = (
        db.query(Application)
        .filter(Application.user_id == current_user.id)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .all()
    )
    return [_response(db, a) for a in rows]


@router.post("", response_model=ApplicationResponse, summary="Apply")
async def apply(
    request: Request,
    body: Union[JoinApplicationRequest, LeagueRequest] = Body(..., discriminator="kind"),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Apply to join with a new BBS (`kind: "join"`), or ask for more leagues for
    a BBS you run (`kind: "leagues"`). 409, with the reason, if it can't be
    accepted: one waiting join per account, one waiting request per BBS.
    """
    try:
        if isinstance(body, JoinApplicationRequest):
            details = body.model_dump(exclude={"kind", "league_ids"})
            application = applications.submit_join(db, current_user, details, body.league_ids,
                                                   request)
        else:
            client = db.get(Client, body.client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="Client not found")
            application = applications.submit_leagues(db, current_user, client, body.league_ids,
                                                      body.note, request)
    except applications.Refused as e:
        raise _refused(e)
    logger.info(f"Application {application.id} ({application.kind}) from {current_user.username}")
    return _response(db, application)


@router.post("/{application_id}/withdraw", response_model=ApplicationResponse,
             summary="Withdraw Application")
async def withdraw(
    application_id: int,
    request: Request,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Withdraw one of your own applications while it is waiting."""
    application = db.get(Application, application_id)
    if application is None or application.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        applications.withdraw(db, current_user, application, request)
    except applications.Refused as e:
        raise _refused(e)
    return _response(db, application)


# --- the admin's side --------------------------------------------------------

@router.get("", response_model=List[ApplicationResponse], summary="Application Queue")
async def list_applications(
    status: Optional[str] = Query("pending", description="pending, approved, rejected, "
                                  "withdrawn, or all"),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Applications, oldest waiting first; decided ones newest first (admin only)."""
    query = db.query(Application)
    if status and status != "all":
        query = query.filter(Application.status == status)
    if status == "pending":
        query = query.order_by(Application.created_at, Application.id)
    else:
        query = query.order_by(Application.created_at.desc(), Application.id.desc())
    return [_response(db, a) for a in query.limit(200).all()]


def _application_or_404(db: Session, application_id: int) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


def _suggest(db: Session, application: Application, league: League) -> LeagueSuggestion:
    members = (
        db.query(LeagueMembership)
        .filter(LeagueMembership.league_id == league.id,
                LeagueMembership.is_active == True,  # noqa: E712
                LeagueMembership.bbs_index.isnot(None))
        .order_by(LeagueMembership.bbs_index)
        .all()
    )
    taken = {m.bbs_index for m in members}
    index, address = None, None
    client = application.client if application.kind == applications.LEAGUES else None
    if client is not None:
        # A BBS keeps its index across leagues, where that index is free here.
        own = [m.bbs_index for m in client.league_memberships if m.is_active and m.bbs_index]
        index = next((i for i in own if i not in taken), None)
        own_addresses = [m.fidonet_address for m in client.league_memberships
                         if m.is_active and m.fidonet_address]
        address = own_addresses[0] if own_addresses else (
            client.ftn_addresses[0].address if client.ftn_addresses else None)
    if index is None:
        index = next(i for i in range(1, 256) if i not in taken and i != _hub_index())
    return LeagueSuggestion(
        league_id=league.id,
        full_id=league.full_id,
        name=league.name,
        hub_address=league.hub_fidonet_address,
        bbs_index=index,
        ftn_address=address,
        taken=[TakenIndex(bbs_index=m.bbs_index, bbs_name=m.client.bbs_name,
                          ftn_address=m.fidonet_address) for m in members],
    )


def _hub_index() -> Optional[int]:
    try:
        return int(get_config().get("hub", {}).get("bbs_index"))
    except (TypeError, ValueError):
        return None


@router.get("/{application_id}", response_model=ApplicationReview, summary="Review Application")
async def review_application(
    application_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One application, with suggested indexes and addresses for approving it (admin only)."""
    application = _application_or_404(db, application_id)
    leagues = db.query(League).filter(League.id.in_(application.league_ids or [])).all()
    client = application.client if application.kind == applications.LEAGUES else None
    return ApplicationReview(
        application=_response(db, application),
        client_id_suggestion=(applications.suggest_client_id(db, application.bbs_name)
                              if application.kind == applications.JOIN else None),
        client_addresses=[a.address for a in client.ftn_addresses] if client else [],
        leagues=[_suggest(db, application, lg) for lg in sorted(leagues, key=lambda lg: lg.full_id)],
    )


@router.post("/{application_id}/approve", response_model=ApproveResponse,
             summary="Approve Application")
async def approve(
    application_id: int,
    body: ApproveRequest,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Approve, granting each league with the BBS index and FTN address given (admin only).

    A join creates the BBS, makes the applicant its owner, and returns a claim
    link bound to the applicant: only they, signed in, can use it, and it
    also waits for them in the console. The leagues' nodelists are
    regenerated; `nodelist_problems` lists any that could not be, which leaves
    the approval standing. 409 with the reason if it can't be approved as given.
    """
    application = _application_or_404(db, application_id)
    config = get_config()
    try:
        result = applications.approve(
            db, current_user, application,
            [applications.Grant(g.league_id, g.bbs_index, g.ftn_address) for g in body.grants],
            client_id=body.client_id, bbs_name=body.bbs_name, note=body.note,
            config=config,
            request=request,
        )
    except applications.Refused as e:
        raise _refused(e)
    logger.info(f"Application {application.id} approved by {current_user.username}: "
                f"{result.client.bbs_name}")
    return ApproveResponse(
        application=_response(db, result.application),
        client_id=result.client.id,
        claim_url=f"{public_url(request)}/claim/{result.claim_token}" if result.claim_token else None,
        claim_expires_at=utc(result.claim_link.expires_at) if result.claim_link else None,
        nodelist_problems=result.nodelist_problems,
    )


@router.post("/{application_id}/reject", response_model=ApplicationResponse,
             summary="Reject Application")
async def reject(
    application_id: int,
    body: RejectRequest,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reject, with a note the sysop sees the next time they sign in (admin only)."""
    application = _application_or_404(db, application_id)
    try:
        applications.reject(db, current_user, application, body.note, request)
    except applications.Refused as e:
        raise _refused(e)
    return _response(db, application)
