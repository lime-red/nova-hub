"""Fetching image archives from `[games] image_url`.

Golden images carry the games' registration keys, so where they are kept is
never publicly readable: our hubs fetch from a Nexus repo that wants a login.
The login lives in a netrc-format file named by `[games] image_credentials`,
which the hub refuses unless only its own user can read it, and never logs. A
hub with no image_url takes images by upload or drop-in instead.

A fetch only downloads: the archive lands in `[games] images` and is imported,
and checked against its manifest, like any other.
"""

import netrc
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from backend.services.game_images import ARCHIVE, INCOMING, MAX_UNPACKED, PREFIX
from backend.services.league_games import GamesConfig

NAME = re.compile(rf"^({'|'.join(PREFIX.values())})-[A-Za-z0-9][A-Za-z0-9._-]{{0,40}}"
                  + re.escape(ARCHIVE) + "$")
TIMEOUT = 600


class FetchError(ValueError):
    """A fetch that cannot be made, or failed, and why. Never holds the password."""


def _netrc_problems(path: Path) -> list[str]:
    try:
        st = path.stat()
    except OSError as e:
        return [f"[games] image_credentials {path} cannot be read ({e.strerror})"]
    problems = []
    if not stat.S_ISREG(st.st_mode):
        problems.append(f"[games] image_credentials {path} is not a file")
    if st.st_mode & 0o077:
        problems.append(f"[games] image_credentials {path} can be read by others; chmod 600 it")
    if st.st_uid != os.getuid():
        problems.append(f"[games] image_credentials {path} is not owned by the hub's user")
    return problems


def config_problems(cfg: GamesConfig) -> list[str]:
    """What is wrong with image_url and image_credentials, for the validator."""
    problems = []
    if cfg.image_credentials and not cfg.image_url:
        problems.append("[games] image_credentials is set but image_url is not")
    if not cfg.image_url:
        return problems
    url = urlparse(cfg.image_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        return problems + [f"[games] image_url {cfg.image_url!r} is not an http(s) URL"]
    if url.username or url.password:
        problems.append("[games] image_url holds a login; put it in image_credentials instead")
    if cfg.image_credentials:
        if url.scheme != "https":
            problems.append("[games] image_url is not https, so the login would cross the "
                            "network in the clear")
        found = _netrc_problems(cfg.image_credentials)
        if not found:
            try:
                if netrc.netrc(str(cfg.image_credentials)).authenticators(url.hostname) is None:
                    found.append(f"[games] image_credentials has no machine {url.hostname}")
            except (netrc.NetrcParseError, OSError) as e:
                found.append(f"[games] image_credentials is not a netrc file (line "
                             f"{getattr(e, 'lineno', '?')})")
        problems += found
    return problems


def _login(cfg: GamesConfig) -> dict:
    """The login for image_url's host, from image_credentials. Whether it may
    be used over plain http is the validator's call (config_problems)."""
    if not cfg.image_credentials:
        return {}
    problems = _netrc_problems(cfg.image_credentials)
    if problems:
        raise FetchError("; ".join(problems))
    host = urlparse(cfg.image_url).hostname
    try:
        found = netrc.netrc(str(cfg.image_credentials)).authenticators(host)
    except (netrc.NetrcParseError, OSError):
        found = None
    if found is None:
        raise FetchError(f"[games] image_credentials has no machine {host}")
    login, _, password = found
    return {"Authorization": aiohttp.encode_basic_auth(login, password or "")}


async def fetch(cfg: GamesConfig, name: str) -> Path:
    """Download image archive `name` from image_url into `[games] images`,
    under a temporary name. Returns its path; the caller imports and removes it."""
    if not cfg.image_url:
        raise FetchError("this hub has no [games] image_url to fetch from")
    if not NAME.match(name or ""):
        raise FetchError(f"{name!r} is not an image archive name, as bre-sj1-r2{ARCHIVE}")
    url = cfg.image_url.rstrip("/") + "/" + name
    headers = _login(cfg)
    cfg.images.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=INCOMING, suffix=".part", dir=cfg.images)
    path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as out:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # No redirects: a login is for this host only.
                async with session.get(url, headers=headers, allow_redirects=False) as response:
                    if response.status != 200:
                        raise FetchError(f"{url} answered {response.status} {response.reason}")
                    size = 0
                    async for chunk in response.content.iter_chunked(1 << 16):
                        size += len(chunk)
                        if size > MAX_UNPACKED:
                            raise FetchError(f"{url} is larger than any image ({size} bytes so far)")
                        out.write(chunk)
    except (aiohttp.ClientError, TimeoutError) as e:
        path.unlink(missing_ok=True)
        raise FetchError(f"could not fetch {url}: {type(e).__name__} {e}")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
