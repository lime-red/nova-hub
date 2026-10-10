"""
backend/services/relink_links.py

Re-link links: how a provider sign-in gets attached to a hub account that
already exists.

First sign-in normally creates a new account (backend/services/sysop_accounts.py),
and an identity is never matched to an existing account by email. So when an
existing account needs a provider sign-in -- the admin's own, a sysop the
admin created by hand, or a sysop who lost access to their email and signs in
with a new identity -- the admin issues a re-link link for that account and
sends it (over Discord, say). Whoever opens it and signs in has that identity
bound to the account, replacing any identity bound before.

The mechanics are the claim link's: only the token's SHA-256 is stored, the
link expires after 72 hours, it works once, and issuing another supersedes it.
Opening it changes nothing; it is used only when the sign-in it starts
completes.
"""

import secrets
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.models.database import RelinkLink, SysopUser
from backend.services.claim_links import EXPIRED, READY, SUPERSEDED, USED, hash_token

LINK_TTL = timedelta(hours=72)

__all__ = ["READY", "USED", "EXPIRED", "SUPERSEDED", "issue", "find", "latest", "state", "use"]


def state(link: RelinkLink, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    if link.used_at:
        return USED
    if link.superseded_at:
        return SUPERSEDED
    if now >= link.expires_at:
        return EXPIRED
    return READY


def issue(db: Session, user: SysopUser, issued_by: str) -> tuple[str, RelinkLink, int]:
    """A new link for this account. Returns (token, link, how many it superseded).

    Does not commit: the caller adds its audit record and commits both.
    """
    now = datetime.utcnow()
    superseded = db.execute(
        update(RelinkLink)
        .where(
            RelinkLink.user_id == user.id,
            RelinkLink.used_at.is_(None),
            RelinkLink.superseded_at.is_(None),
            RelinkLink.expires_at > now,
        )
        .values(superseded_at=now)
    ).rowcount
    token = secrets.token_urlsafe(32)
    link = RelinkLink(user_id=user.id, token_hash=hash_token(token), issued_by=issued_by,
                      issued_at=now, expires_at=now + LINK_TTL)
    db.add(link)
    db.flush()
    return token, link, superseded


def find(db: Session, token: str) -> RelinkLink | None:
    return db.query(RelinkLink).filter(RelinkLink.token_hash == hash_token(token)).first()


def latest(db: Session, user_id: int) -> RelinkLink | None:
    return (
        db.query(RelinkLink)
        .filter(RelinkLink.user_id == user_id)
        .order_by(RelinkLink.issued_at.desc(), RelinkLink.id.desc())
        .first()
    )


class NotUsable(Exception):
    """The link exists but is used, superseded or expired."""


def use(db: Session, link: RelinkLink, ip: str) -> None:
    """Mark the link used, if it still can be. Does not commit.

    One conditional UPDATE, so two sign-ins racing on one link cannot both win.
    """
    now = datetime.utcnow()
    marked = db.execute(
        update(RelinkLink)
        .where(
            RelinkLink.id == link.id,
            RelinkLink.used_at.is_(None),
            RelinkLink.superseded_at.is_(None),
            RelinkLink.expires_at > now,
        )
        .values(used_at=now, used_ip=ip[:45])
    ).rowcount
    if marked != 1:
        raise NotUsable(link.id)
