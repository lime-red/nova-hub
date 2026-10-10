"""
backend/services/sysop_accounts.py

Which hub account a provider identity signs in to.

An identity is bound to an account by its subject, the provider's permanent ID
for the person. The first sign-in with a new subject creates a sysop account:
not an admin, owning no BBS, so it sees nothing until an admin makes it the
owner of one. That is the "sysop signs up, admin assigns the owner" flow for
BBSes that predate accounts.

An identity is never bound to an existing account by matching email. Email is
how the provider identifies people, but the hub's own accounts (the admin's
above all) were not created by the provider, and an address match is not proof
that the same person holds both. An existing account is linked deliberately,
through an admin's re-link link.
"""

import re
from datetime import datetime

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import SysopUser
from backend.services import audit
from backend.services.identity_provider import Identity


class SignInRefused(Exception):
    """The identity is genuine but may not sign in. `code` goes to the login page."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _by_email(db: Session, email: str):
    return db.query(SysopUser).filter(func.lower(SysopUser.email) == email.lower()).first()


def _unique_username(db: Session, email: str) -> str:
    base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower())[:40] or "sysop"
    name, n = base, 1
    while db.query(SysopUser.id).filter(SysopUser.username == name).first():
        n += 1
        name = f"{base}{n}"
    return name


def sign_in(db: Session, identity: Identity, request: Request | None = None) -> SysopUser:
    """The account this identity signs in to, creating one on first sign-in. Commits."""
    if not identity.email_verified:
        raise SignInRefused("unverified_email",
                            "Verify your email address with the sign-in provider first.")

    user = db.query(SysopUser).filter(SysopUser.idp_subject == identity.subject).first()
    if user is None:
        if _by_email(db, identity.email):
            raise SignInRefused(
                "email_in_use",
                "A hub account already uses this email address. Ask the hub admin "
                "for a link to connect your sign-in to it.",
            )
        user = SysopUser(
            username=_unique_username(db, identity.email),
            email=identity.email,
            idp_subject=identity.subject,
            full_name=identity.name,
            hashed_password=None,
            is_superuser=False,
            is_active=True,
        )
        db.add(user)
        db.flush()
        audit.record(db, "account.created", actor=user, target=user,
                     detail=f"first sign-in as {identity.email}", request=request)
    elif not user.is_active:
        raise SignInRefused("inactive", "This hub account has been disabled.")
    elif user.email != identity.email:
        # The provider is the authority on the address; follow it, unless that
        # would take another account's.
        other = _by_email(db, identity.email)
        if other is None or other.id == user.id:
            audit.record(db, "account.email_changed", actor=user, target=user,
                         detail=f"{user.email} -> {identity.email}", request=request)
            user.email = identity.email

    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user
