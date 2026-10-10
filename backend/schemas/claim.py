"""Claim link schemas: the admin's side and the sysop's public page"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ClaimLinkIssued(BaseModel):
    """A new link, returned once to the admin who issued it"""
    url: str
    expires_at: datetime
    superseded: int  # outstanding links for this BBS that this one replaced


class ClaimLinkStatus(BaseModel):
    """The latest link issued for a BBS, as the admin sees it (never the token)"""
    state: str  # ready | used | superseded | expired
    issued_by: Optional[str] = None
    issued_at: datetime
    expires_at: datetime
    used_at: Optional[datetime] = None
    used_ip: Optional[str] = None


class ClaimPageStatus(BaseModel):
    """What the claim page shows before anything is claimed"""
    bbs_name: str
    state: str  # ready | used | superseded | expired
    expires_at: datetime
    used_at: Optional[datetime] = None
    used_ip: Optional[str] = None


class ClaimedLeague(BaseModel):
    game: str  # "BRE"
    number: str  # "015"
    bbs_index: int


class ClaimResult(BaseModel):
    """The credentials, returned once, by the claim itself"""
    bbs_name: str
    hub_url: str
    client_id: str
    client_secret: str
    leagues: List[ClaimedLeague]
    config_toml: str  # Linux: the Python client
    config_psd1: str  # Windows: the PowerShell client


class RelinkPageStatus(BaseModel):
    """What the re-link page shows: which account the link connects a sign-in to"""
    username: str
    state: str  # ready | used | superseded | expired
    expires_at: datetime
    has_sign_in: bool  # the account already has one, which using the link replaces
