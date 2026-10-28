"""
backend/services/claim_links.py

Claim links: how a BBS gets its client credentials without anyone sending a
secret over chat.

An admin issues a link from the BBS's page. The link carries a random token;
the database keeps only the token's SHA-256. Nothing secret exists yet. When
the sysop opens the link and presses Claim, a new client secret is generated,
its hash replaces the old one, and the plaintext goes back in that one response
-- with a config file already filled in -- and is never stored or shown again.

So a link is only worth something until it is used, it is used at most once,
and a sysop who finds it already claimed knows someone else got there first.
Issuing a new link supersedes any outstanding one for the same BBS.

Opening the link (GET) only reports its state. Claiming is a separate POST, so
the preview fetch a chat app makes when the link is pasted cannot use it up.

A link may be bound to an account. Then only that account, signed in, can
claim it, so a link that is forwarded or read over someone's shoulder is
useless to anyone else. Approving an application issues one bound to the
applicant. An unbound link -- possession is the credential -- is for a BBS
whose sysop has no account.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.models.database import ClaimLink, Client, LeagueMembership, SysopUser
from backend.services import client_secrets
from backend.services.games import game_for_code, game_for_letter

LINK_TTL = timedelta(hours=72)

READY, USED, EXPIRED, SUPERSEDED = "ready", "used", "expired", "superseded"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def state(link: ClaimLink, now: datetime | None = None) -> str:
    """ready, used, superseded or expired -- in that order of precedence."""
    now = now or utcnow()
    if link.used_at:
        return USED
    if link.superseded_at:
        return SUPERSEDED
    if now >= link.expires_at:
        return EXPIRED
    return READY


def issue(db: Session, client: Client, issued_by: str, user: SysopUser | None = None,
          commit: bool = True) -> tuple[str, ClaimLink, int]:
    """A new link for this BBS, bound to `user` if given.

    Returns (token, link, how many it superseded). The token is returned once,
    here; only its hash is kept. Commits unless told not to.
    """
    now = utcnow()
    superseded = db.execute(
        update(ClaimLink)
        .where(
            ClaimLink.client_id == client.id,
            ClaimLink.used_at.is_(None),
            ClaimLink.superseded_at.is_(None),
            ClaimLink.expires_at > now,
        )
        .values(superseded_at=now)
    ).rowcount

    token = secrets.token_urlsafe(32)
    link = ClaimLink(
        client_id=client.id,
        token_hash=hash_token(token),
        issued_by=issued_by,
        issued_at=now,
        expires_at=now + LINK_TTL,
        user_id=user.id if user else None,
    )
    db.add(link)
    if commit:
        db.commit()
        db.refresh(link)
    else:
        db.flush()
    return token, link, superseded


def ready_for(db: Session, client_id: int, user: SysopUser) -> ClaimLink | None:
    """A link for this BBS that is bound to this user and still claimable."""
    now = utcnow()
    return (
        db.query(ClaimLink)
        .filter(
            ClaimLink.client_id == client_id,
            ClaimLink.user_id == user.id,
            ClaimLink.used_at.is_(None),
            ClaimLink.superseded_at.is_(None),
            ClaimLink.expires_at > now,
        )
        .order_by(ClaimLink.issued_at.desc())
        .first()
    )


def find(db: Session, token: str) -> ClaimLink | None:
    return db.query(ClaimLink).filter(ClaimLink.token_hash == hash_token(token)).first()


def latest(db: Session, client_id: int) -> ClaimLink | None:
    return (
        db.query(ClaimLink)
        .filter(ClaimLink.client_id == client_id)
        .order_by(ClaimLink.issued_at.desc(), ClaimLink.id.desc())
        .first()
    )


class NotClaimable(Exception):
    """The link exists but is used, superseded or expired."""


def claim(db: Session, link: ClaimLink, ip: str) -> str:
    """Use the link: mark it used and give its BBS a new secret. Commits.

    Returns the new secret in plaintext -- the only time it exists as such.
    Raises NotClaimable if the link was not ready. The used/unused check and
    the mark are one conditional UPDATE, so two simultaneous claims cannot both
    succeed: SQLite serialises the writes and the second matches no row.
    """
    now = utcnow()
    marked = db.execute(
        update(ClaimLink)
        .where(
            ClaimLink.id == link.id,
            ClaimLink.used_at.is_(None),
            ClaimLink.superseded_at.is_(None),
            ClaimLink.expires_at > now,
        )
        .values(used_at=now, used_ip=ip[:45])
    ).rowcount
    if marked != 1:
        db.rollback()
        raise NotClaimable(link.id)

    client = db.get(Client, link.client_id)
    secret = client_secrets.rotate(client)
    db.commit()
    db.refresh(link)
    return secret


# -- the config files a claim hands back --------------------------------------

@dataclass
class LeagueEntry:
    code: str  # "BRE"
    number: str  # "015"
    bbs_index: int
    letter: str = ""  # "B", the league's game; found from code when left out

    @property
    def provider(self):
        from backend.services.providers import provider_for
        return provider_for(self.letter or game_for_code(self.code).letter)

    @property
    def folder(self) -> str:
        # Eight characters at most: BRE ignores an inbound directory behind a
        # path component that is not a valid DOS 8.3 name.
        return f"{self.code}{self.number}"


def league_entries(db: Session, client: Client) -> list[LeagueEntry]:
    """The BBS's active memberships, as the config files list them."""
    entries = []
    memberships = (
        db.query(LeagueMembership)
        .filter(LeagueMembership.client_id == client.id, LeagueMembership.is_active == True)  # noqa: E712
        .all()
    )
    for m in memberships:
        try:
            game = game_for_letter(m.league.game_type)
        except KeyError:
            continue  # a league of an unknown game has nothing a client can run
        entries.append(LeagueEntry(game.code, m.league.league_id, m.bbs_index, game.letter))
    return sorted(entries, key=lambda e: (e.code, e.number))


PLACEHOLDER_NOTE = (
    "Paths marked CHANGE-ME are placeholders: only you know where each game is "
    "installed. Replace every one, then run the validator before the first sync. "
    "The connection test needs none of them."
)

# A sample config downloaded later has no secret to put in it: the hub keeps
# only its hash. The client_id is still filled in.
SECRET_PLACEHOLDER = "CHANGE-ME"
NO_SECRET_NOTE = (
    "This sample has no client secret: the hub does not keep it. Put in the one "
    "you saved when you claimed your credentials, or ask the hub admin for a new "
    "claim link. Until then even the connection test fails."
)


def _toml_str(value: str) -> str:
    """A TOML basic string. Control characters are escaped: TOML refuses them raw."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _comment_text(value: str) -> str:
    """Text safe inside a config file's comment: one line, and nothing that closes a <# #> block."""
    return "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in value).replace("#>", "# >")


def _psd1_str(value: str) -> str:
    # PowerShell also ends a single-quoted string at the typographic quotes.
    for q in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        value = value.replace(q, q + q)
    return "'" + value + "'"


def render_config_toml(client: Client, secret: str | None, hub_url: str, leagues: list[LeagueEntry]) -> str:
    """config.toml for the Python client on Linux, under dosemu.

    With no secret it is a sample: the secret is a placeholder and says so.
    """
    lines = [
        f"# Nova Client configuration for {_comment_text(client.bbs_name)}, issued by the hub.",
        "#",
        "# This file holds your client secret. Keep it readable only by the user",
        "# that runs the client (chmod 600 config.toml).",
        "#",
        f"# {PLACEHOLDER_NOTE}",
    ]
    if secret is None:
        lines += ["#", f"# {NO_SECRET_NOTE}"]
    lines += [
        "",
        "[hub]",
        f"url = {_toml_str(hub_url)}",
        f"client_id = {_toml_str(client.client_id)}",
        f"client_secret = {_toml_str(secret or SECRET_PLACEHOLDER)}",
        "",
        "[bbs]",
        "# Must match the name recorded against your BBS index in each game's nodes file.",
        f"name = {_toml_str(client.bbs_name)}",
        "",
        "[sync]",
        'sent_action = "archive"',
        'archive_dir = "./sent"',
        "max_retries = 3",
        "retry_delay = 5",
        "timeout_seconds = 120",
        'metrics_file = "./metrics.json"',
        "",
        "[daemon]",
        "enabled = true",
        "sync_interval = 120",
        "maintenance_interval = 600",
        "maintenance_timeout = 300",
        "run_maintenance_on_download = true",
        'dosemu_path = "/usr/bin/dosemu"',
        'dosemu_config_dir = "./dosemu_configs"',
        'dosemu_term = "linux"',
        'log_dir = "./logs"',
    ]
    if not leagues:
        lines += ["", "# No league memberships yet. The hub admin will tell you your", "# league and BBS index."]
    for e in leagues:
        lines += [""] + e.provider.config_toml_league(e)
    return "\n".join(lines) + "\n"


def render_config_psd1(client: Client, secret: str | None, hub_url: str, leagues: list[LeagueEntry]) -> str:
    """config.psd1 for the PowerShell client on Windows. No secret: a sample, as above."""
    lines = [
        "<#",
        f"    Nova Client configuration for {_comment_text(client.bbs_name)}, issued by the hub.",
        "",
        "    This file holds your client secret. Keep it where only the account",
        "    that runs the client can read it.",
        "",
        f"    {PLACEHOLDER_NOTE}",
    ]
    if secret is None:
        lines += ["", f"    {NO_SECRET_NOTE}"]
    lines += [
        "#>",
        "@{",
        "    Hub = @{",
        f"        Url          = {_psd1_str(hub_url)}",
        f"        ClientId     = {_psd1_str(client.client_id)}",
        f"        ClientSecret = {_psd1_str(secret or SECRET_PLACEHOLDER)}",
        "    }",
        "",
        "    Bbs = @{",
        "        # Must match the name recorded against your BBS index in each game's nodes file.",
        f"        Name = {_psd1_str(client.bbs_name)}",
        "    }",
        "",
        "    Sync = @{",
        "        SentAction        = 'archive'",
        "        ArchiveDir        = '.\\sent'",
        "        MaxRetries        = 3",
        "        RetryDelaySeconds = 5",
        "        TimeoutSeconds    = 120",
        "        MetricsFile       = '.\\metrics.json'",
        "        NodelistCheck     = 'bootstrap'",
        "    }",
        "",
        "    Daemon = @{",
        "        SyncIntervalSeconds        = 120",
        "        MaintenanceIntervalSeconds = 600",
        "        MaintenanceTimeoutSeconds  = 300",
        "        RunMaintenanceOnDownload   = $true",
        "    }",
        "",
        "    Leagues = @(",
    ]
    if not leagues:
        lines += ["        # No league memberships yet. The hub admin will tell you your", "        # league and BBS index."]
    for e in leagues:
        lines += e.provider.config_psd1_league(e)
    lines += ["    )", "}"]
    return "\r\n".join(lines) + "\r\n"
