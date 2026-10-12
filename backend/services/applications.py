"""
backend/services/applications.py

Sysops apply; the admin approves. How a BBS joins the hub without anyone
typing its details in by hand.

A signed-in sysop applies, either to join with a new BBS ("join") or, for a
BBS they already own, to be added to more leagues ("leagues"). The admin sees
the queue and decides each one once:

- Approving a join creates the BBS, makes the applicant its owner, assigns
  its FTN addresses, adds a membership per granted league with the BBS index
  and address the admin picked, regenerates those leagues' nodelists, and
  issues a claim link bound to the applicant. The BBS's secret does not exist
  until the sysop claims it.
- Approving a leagues request does the same for the BBS that already exists,
  minus creating it and minus the claim link: its credentials don't change.
- Rejecting records the admin's note, which the sysop sees in the console.

A sysop can withdraw a pending application. One pending join per account and
one pending leagues request per BBS keep the queue honest.

Everything here commits, and records itself in the audit log, in one
transaction: an approval either did all of it or none of it.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from backend.core.security import get_password_hash
from backend.models.database import (
    Application,
    ClaimLink,
    Client,
    ClientOwner,
    FtnAddress,
    League,
    LeagueMembership,
    SysopUser,
)
from backend.services import audit, claim_links
from backend.services.games import MAX_BBS_INDEX

JOIN, LEAGUES = "join", "leagues"
PENDING, APPROVED, REJECTED, WITHDRAWN = "pending", "approved", "rejected", "withdrawn"

FTN_ADDRESS = re.compile(r"^\d{1,5}:\d{1,5}/\d{1,5}(\.\d{1,5})?$")


class Refused(Exception):
    """The request can't be done as asked. The message is for the person asking."""


@dataclass
class Grant:
    league_id: int  # League.id
    bbs_index: int
    ftn_address: str


# --- applying ----------------------------------------------------------------

def joinable_leagues(db: Session) -> list[League]:
    return (
        db.query(League)
        .filter(League.is_active == True)  # noqa: E712
        .order_by(League.game_type, League.league_id)
        .all()
    )


def _check_leagues(db: Session, league_ids: list[int]) -> list[int]:
    league_ids = sorted(set(league_ids))
    if not league_ids:
        raise Refused("Choose at least one league.")
    open_ids = {lg.id for lg in joinable_leagues(db)}
    unknown = [i for i in league_ids if i not in open_ids]
    if unknown:
        raise Refused("One of the leagues asked for is not open to join.")
    return league_ids


def submit_join(db: Session, user: SysopUser, details: dict, league_ids: list[int],
                request: Optional[Request] = None) -> Application:
    """A new BBS asking to join. `details` holds the Application's text fields."""
    if db.query(Application.id).filter(
        Application.user_id == user.id, Application.kind == JOIN, Application.status == PENDING
    ).first():
        raise Refused("You already have an application waiting. Withdraw it to send a new one.")
    bbs_name = (details.get("bbs_name") or "").strip()
    if not bbs_name:
        raise Refused("Give your BBS's name.")
    if db.query(Client.id).filter(Client.bbs_name.ilike(bbs_name)).first():
        raise Refused(
            f"A BBS called {bbs_name} is already on this hub. If it is yours, ask the hub "
            "admin to make you its owner instead."
        )
    application = Application(
        kind=JOIN,
        user_id=user.id,
        applicant=user.username,
        status=PENDING,
        league_ids=_check_leagues(db, league_ids),
        email=user.email,
        **{k: _clean(v) for k, v in details.items() if k != "email"},
    )
    application.bbs_name = bbs_name
    db.add(application)
    db.flush()
    audit.record(db, "application.submitted", actor=user, target=application,
                 detail=f"to join with {bbs_name}", request=request)
    db.commit()
    db.refresh(application)
    return application


def submit_leagues(db: Session, user: SysopUser, client: Client, league_ids: list[int],
                   note: Optional[str], request: Optional[Request] = None) -> Application:
    """A BBS the user owns asking to be added to more leagues."""
    if not db.query(ClientOwner).filter(ClientOwner.client_id == client.id,
                                        ClientOwner.sysop_user_id == user.id).first():
        raise Refused("You can only ask for leagues for a BBS you run.")
    if db.query(Application.id).filter(
        Application.client_id == client.id, Application.kind == LEAGUES,
        Application.status == PENDING,
    ).first():
        raise Refused(f"{client.bbs_name} already has a league request waiting.")
    league_ids = _check_leagues(db, league_ids)
    member_of = {
        m.league_id for m in client.league_memberships if m.is_active
    }
    already = [i for i in league_ids if i in member_of]
    if already:
        raise Refused(f"{client.bbs_name} is already in one of those leagues.")
    application = Application(
        kind=LEAGUES,
        user_id=user.id,
        applicant=user.username,
        client_id=client.id,
        status=PENDING,
        bbs_name=client.bbs_name,
        email=user.email,
        league_ids=league_ids,
        note=_clean(note),
    )
    db.add(application)
    db.flush()
    audit.record(db, "application.submitted", actor=user, target=application,
                 detail=f"leagues for {client.bbs_name}", request=request)
    db.commit()
    db.refresh(application)
    return application


def withdraw(db: Session, user: SysopUser, application: Application,
             request: Optional[Request] = None) -> Application:
    if application.user_id != user.id:
        raise Refused("That isn't your application.")
    if application.status != PENDING:
        raise Refused(f"This application has already been {application.status}.")
    application.status = WITHDRAWN
    application.reviewed_at = datetime.utcnow()
    audit.record(db, "application.withdrawn", actor=user, target=application, request=request)
    db.commit()
    db.refresh(application)
    return application


# --- deciding ----------------------------------------------------------------

def reject(db: Session, admin: SysopUser, application: Application, note: Optional[str],
           request: Optional[Request] = None) -> Application:
    _require_pending(application)
    application.status = REJECTED
    application.reviewed_by = admin.username
    application.reviewed_at = datetime.utcnow()
    application.decision_note = _clean(note)
    audit.record(db, "application.rejected", actor=admin, target=application,
                 detail=application.decision_note, request=request)
    db.commit()
    db.refresh(application)
    return application


@dataclass
class Approval:
    application: Application
    client: Client
    claim_token: Optional[str]  # a join's claim link; None for a leagues request
    claim_link: Optional[ClaimLink]
    nodelist_problems: list[str]


def approve(db: Session, admin: SysopUser, application: Application, grants: list[Grant],
            client_id: Optional[str] = None, bbs_name: Optional[str] = None,
            note: Optional[str] = None, data_dir: Optional[str] = None,
            hub_config: Optional[dict] = None,
            request: Optional[Request] = None) -> Approval:
    """Approve, granting these leagues. Commits everything, or nothing.

    A join creates the BBS: `client_id` is its OAuth client ID (unique; a
    sensible one is suggested from the name) and `bbs_name` may correct the
    name as applied for. A grant need not cover every league asked for -- the
    admin can grant some -- but must grant at least one.
    """
    _require_pending(application)
    if not grants:
        raise Refused("Grant at least one league.")
    if len({g.league_id for g in grants}) != len(grants):
        raise Refused("Each league can be granted once.")

    try:
        if application.kind == JOIN:
            client = _create_client(db, admin, application, client_id, bbs_name)
        else:
            client = application.client
            if client is None:
                raise Refused("The BBS this request was for no longer exists.")

        granted = []
        for grant in grants:
            membership = _grant(db, client, grant)
            granted.append({"league_id": grant.league_id, "league": membership.league.full_id,
                            "bbs_index": grant.bbs_index, "ftn_address": membership.fidonet_address})

        application.status = APPROVED
        application.client = client
        application.reviewed_by = admin.username
        application.reviewed_at = datetime.utcnow()
        application.decision_note = _clean(note)
        application.granted = granted
        db.flush()

        summary = ", ".join(f"{g['league']} as #{g['bbs_index']} ({g['ftn_address']})"
                            for g in granted)
        audit.record(db, "application.approved", actor=admin, target=application,
                     detail=f"{client.bbs_name}: {summary}", request=request)

        token, link = None, None
        if application.kind == JOIN:
            token, link, _ = claim_links.issue(db, client, admin.username, user=application.user,
                                               commit=False)
            audit.record(db, "claim_link.issued", actor=admin, target=client,
                         detail=f"link {link.id} for {application.applicant}, on approval",
                         request=request)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(application)
    problems = regenerate_nodelists(db, [g.league_id for g in grants], data_dir, hub_config)
    return Approval(application, client, token, link, problems)


def _require_pending(application: Application) -> None:
    if application.status != PENDING:
        raise Refused(f"This application has already been {application.status}.")


def suggest_client_id(db: Session, bbs_name: str) -> str:
    """A free OAuth client ID from the BBS's name: "The Eclipse" -> "the_eclipse"."""
    base = re.sub(r"[^a-z0-9]+", "_", bbs_name.lower()).strip("_")[:40] or "bbs"
    name, n = base, 1
    while db.query(Client.id).filter(Client.client_id == name).first():
        n += 1
        name = f"{base}_{n}"
    return name


def _create_client(db: Session, admin: SysopUser, application: Application,
                   client_id: Optional[str], bbs_name: Optional[str]) -> Client:
    bbs_name = (bbs_name or application.bbs_name).strip()
    if not bbs_name:
        raise Refused("The BBS needs a name.")
    if db.query(Client.id).filter(Client.bbs_name.ilike(bbs_name)).first():
        raise Refused(f"A BBS called {bbs_name} is already on this hub.")
    client_id = (client_id or suggest_client_id(db, bbs_name)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", client_id):
        raise Refused("A client ID is 3 to 64 letters, digits, dots, dashes or underscores.")
    if db.query(Client.id).filter(Client.client_id == client_id).first():
        raise Refused(f"Client ID {client_id} is already taken.")

    client = Client(
        client_id=client_id,
        # Nobody knows this secret, so nothing can sign in as the BBS until the
        # applicant claims the link, which replaces it.
        client_secret=get_password_hash(secrets.token_urlsafe(32)),
        bbs_name=bbs_name,
        contact_name=application.sysop_name,
        contact_email=application.email,
        city=application.city,
        state=application.state,
        country=application.country,
        is_active=True,
    )
    db.add(client)
    db.flush()
    if application.user is not None:
        db.add(ClientOwner(sysop_user_id=application.user.id, client_id=client.id,
                           created_by=admin.username))
    audit.record(db, "client.created", actor=admin, target=client,
                 detail=f"client_id {client_id}, from application {application.id}")
    if application.user is not None:
        audit.record(db, "owner.added", actor=admin, target=client,
                     detail=f"{application.user.username}, from application {application.id}")
    return client


def _grant(db: Session, client: Client, grant: Grant) -> LeagueMembership:
    league = db.get(League, grant.league_id)
    if league is None:
        raise Refused("One of the granted leagues no longer exists.")
    if not (1 <= grant.bbs_index <= MAX_BBS_INDEX):
        raise Refused(f"A BBS index is between 1 and {MAX_BBS_INDEX}.")
    holder = (
        db.query(LeagueMembership)
        .filter(LeagueMembership.league_id == league.id,
                LeagueMembership.bbs_index == grant.bbs_index,
                LeagueMembership.is_active == True,  # noqa: E712
                LeagueMembership.client_id != client.id)
        .first()
    )
    if holder:
        raise Refused(f"BBS #{grant.bbs_index} in {league.full_id} is "
                      f"{holder.client.bbs_name}.")

    text = (grant.ftn_address or "").strip()
    if not FTN_ADDRESS.match(text):
        raise Refused(f"{text or 'An empty address'} is not an FTN address (zone:net/node).")
    if text == (league.hub_fidonet_address or "").strip():
        raise Refused(f"{text} is the hub's own address in {league.full_id}.")
    try:
        address = FtnAddress.get_or_create(db, client.id, text)
    except ValueError:
        holder_name = db.query(FtnAddress).filter(FtnAddress.address == text).one().client.bbs_name
        raise Refused(f"{text} belongs to {holder_name}.")
    db.flush()

    membership = (
        db.query(LeagueMembership)
        .filter(LeagueMembership.league_id == league.id, LeagueMembership.client_id == client.id)
        .first()
    )
    if membership is None:
        membership = LeagueMembership(league_id=league.id, client_id=client.id)
        db.add(membership)
    membership.is_active = True
    membership.bbs_index = grant.bbs_index
    membership.ftn_address = address
    membership.league = league
    db.flush()
    return membership


def regenerate_nodelists(db: Session, league_ids: list[int], data_dir: Optional[str],
                         hub_config: Optional[dict]) -> list[str]:
    """Regenerate each league's nodelist. Returns why any could not be.

    After the approval has committed: a league with no hub address can't get a
    nodelist, which the admin needs to hear, but it is no reason to undo the
    approval.
    """
    from backend.services.nodelist_generator import NodelistGenerator

    if data_dir is None:
        return []
    problems = []
    generator = NodelistGenerator(db, data_dir, hub_config or {})
    for league_id in league_ids:
        league = db.get(League, league_id)
        try:
            if generator.generate(league_id) is None:
                problems.append(f"{league.full_id}: no nodelist written; set the league's "
                                "hub address, then generate it from the league page")
        except Exception as e:  # noqa: BLE001 -- the approval stands either way
            problems.append(f"{league.full_id}: nodelist generation failed: {e}")
    return problems


# --- the Verify step ---------------------------------------------------------

STAGES = ["approved", "claimed", "first_token", "first_upload", "active"]
ACTIVE_WITHIN_HOURS = 48


def stage(db: Session, client: Client, now: Optional[datetime] = None) -> str:
    """How far this BBS has got: approved, claimed, first_token, first_upload or active.

    Active means it has uploaded and signed in within the last two days, so a
    node that has gone quiet drops back to first_upload, which is the thing
    an admin wants to notice.
    """
    now = now or datetime.utcnow()
    if client.first_upload_at:
        if client.last_seen and (now - client.last_seen).total_seconds() < ACTIVE_WITHIN_HOURS * 3600:
            return "active"
        return "first_upload"
    if client.first_token_at:
        return "first_token"
    claimed = db.query(ClaimLink.id).filter(
        ClaimLink.client_id == client.id, ClaimLink.used_at.isnot(None)
    ).first()
    return "claimed" if claimed else "approved"


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None
