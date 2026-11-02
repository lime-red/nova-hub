"""Where each league's game is, and whether it may run.

The hub runs its own copy of every league's game. Where that copy lives used to
be spelled out per league in config.toml, as [dosemu.<league>.<game>] and
[ib.<league>] sections, each with its own folders and commands. Two sources of
truth that could disagree, and a new league needed a shell, an edit and a
restart.

Now a league's game is a league_games row naming one folder, like B015, inside
`[games] root`. Everything else follows from that folder and the game:

    <root>/B015/              the game (dosemu sees it as C:\\<root's DOS path>\\B015)
    <root>/B015/INBOUND       where the hub puts its packets
    <root>/B015/OUTBOUND      where the game writes packets
    <root>/B015/BULLETIN      the score files
    <root>/I015/              an IB game's data folder; inbound/ and outbound/
                              are what its bbs.cfg names (GameInbound/GameOutbound)

The commands are each game's own constants (games.py), and an IB game's binary
is the image it was made from. league_game() is the only way in: every runner,
the reset job, the nodelist writer and preflight read a league's game through
it. config.toml keeps only facts about the host:

    [games]
    root = "/home/novahub/.dosemu/drive_c/leagues"
    drive_c = "/home/novahub/.dosemu/drive_c"   # what dosemu calls C:
    images = "/home/novahub/nova-data/images"    # default <data_dir>/images

A game runs only when its row is ready and its league is enabled.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.models.database import League
from backend.services.games import DOS_COMPLETION_MARKER, IB_ARGS, Game, game_for_letter

# The file a native (IB) image holds its binary under.
IB_BINARY = "immortal-barons"
DEFAULT_TIMEOUT = 300

# A DOS 8.3 name: the games resolve their inbound folder through 16-bit path
# handling that silently reads nothing behind a longer name.
_DOS_NAME = re.compile(r"^[A-Za-z0-9_\-$~!#%&@^']{1,8}(\.[A-Za-z0-9_\-$~!#%&@^']{0,3})?$")


class GamesConfigError(ValueError):
    """config.toml cannot say where the leagues' games are."""


@dataclass(frozen=True)
class GamesConfig:
    root: Optional[Path]
    drive_c: Optional[Path]
    images: Path


def games_config(config: dict) -> GamesConfig:
    section = (config or {}).get("games") or {}
    data_dir = Path((config or {}).get("server", {}).get("data_dir", "./data"))
    root, drive_c = section.get("root"), section.get("drive_c")
    return GamesConfig(
        root=Path(root) if root else None,
        drive_c=Path(drive_c) if drive_c else None,
        images=Path(section.get("images") or data_dir / "images"),
    )


def legacy_sections(config: dict) -> list[str]:
    """The per-league sections config.toml no longer takes, as written there.

    The hub refuses to start while any is left: two places saying where a
    league's game is is how one ends up reading the wrong folder.
    """
    found = []
    for key, value in ((config or {}).get("dosemu") or {}).items():
        if isinstance(value, dict):
            found += [f"[dosemu.{key}.{code}]" for code, v in value.items() if isinstance(v, dict)]
    for key, value in ((config or {}).get("ib") or {}).items():
        if isinstance(value, dict):
            found.append(f"[ib.{key}]")
    return found


def folder_name(league: League) -> str:
    """B015: the game's letter and the league's number. Four characters, so it
    is always a valid DOS name."""
    return f"{league.game_type.upper()}{league.league_id}"


def is_dos_name(name: str) -> bool:
    return bool(_DOS_NAME.match(name))


def dos_path(cfg: GamesConfig, host: Path) -> str:
    """The DOS path of a host path inside dosemu's C:. Raises GamesConfigError
    if it is outside C: or a component breaks 8.3."""
    if cfg.drive_c is None:
        raise GamesConfigError("[games] drive_c is not set, so no DOS path can be given")
    try:
        rel = Path(host).relative_to(cfg.drive_c)
    except ValueError:
        raise GamesConfigError(f"{host} is not inside [games] drive_c ({cfg.drive_c})")
    bad = [p for p in rel.parts if not is_dos_name(p)]
    if bad:
        raise GamesConfigError(f"{host}: {', '.join(bad)} is not a DOS 8.3 name")
    return "C:\\" + "\\".join(p.upper() for p in rel.parts)


def host_path(cfg: GamesConfig, dos: str) -> Optional[Path]:
    """The host path a DOS path on C: names, matching each component as DOS
    would, ignoring case. None if it is not on C: or C: is not configured."""
    if cfg.drive_c is None:
        return None
    m = re.match(r"^[Cc]:\\?(.*)$", dos.strip())
    if not m:
        return None
    path = cfg.drive_c
    for part in filter(None, m.group(1).split("\\")):
        path = child(path, part)
    return path


def child(folder: Path, name: str) -> Path:
    """`folder/name` as it exists, matched ignoring case; as written if not."""
    try:
        for p in folder.iterdir():
            if p.name.lower() == name.lower():
                return p
    except OSError:
        pass
    return folder / name


def ib_bbs_cfg(folder: Path) -> dict:
    """An IB game's bbs.cfg: one "Key Value" per line."""
    values = {}
    try:
        text = child(folder, "bbs.cfg").read_text(errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        key, _, value = line.strip().partition(" ")
        if key and not key.startswith("#"):
            values[key] = value.strip()
    return values


@dataclass(frozen=True)
class GameInstall:
    """One league's game, resolved: everything a runner needs."""
    league_db_id: int
    full_id: str
    game: Game
    folder: Path
    state: str
    enabled: bool
    inbound: Path
    outbound: Path
    timeout: int
    # DOS games
    dos_path: Optional[str] = None
    scores: Optional[Path] = None
    dosemu_path: str = "/usr/bin/dosemu"
    # IB
    binary: Optional[Path] = None
    hub_board_key: Optional[str] = None
    image_id: Optional[int] = None

    @property
    def runnable(self) -> bool:
        """Ready, and its league enabled: only then may anything run it but a
        reset the admin sent."""
        return self.state == "ready" and self.enabled

    def command(self, what: str) -> str:
        return self.game.command(what)

    @property
    def completion_marker(self) -> Optional[str]:
        return DOS_COMPLETION_MARKER if self.game.dos else None

    @property
    def ib_args(self) -> list[str]:
        return [*IB_ARGS, "-data", str(self.folder)]


def league_game(config: dict, league: League) -> Optional[GameInstall]:
    """The league's game, or None if the hub has none for it (no league_games
    row, or [games] root unset)."""
    row = league.install
    if row is None:
        return None
    cfg = games_config(config)
    if cfg.root is None:
        return None
    game = game_for_letter(league.game_type)
    folder = cfg.root / row.folder
    dosemu = (config or {}).get("dosemu") or {}
    timeout = int(dosemu.get("timeout") or DEFAULT_TIMEOUT)
    common = dict(league_db_id=league.id, full_id=league.full_id, game=game, folder=folder,
                  state=row.state, enabled=bool(league.is_active), timeout=timeout,
                  image_id=row.image_id)
    if game.dos:
        try:
            dos = dos_path(cfg, folder)
        except GamesConfigError:
            dos = None
        return GameInstall(**common, inbound=child(folder, "INBOUND"),
                           outbound=child(folder, "OUTBOUND"), dos_path=dos,
                           scores=child(folder, "BULLETIN"),
                           dosemu_path=dosemu.get("dosemu_path", "/usr/bin/dosemu"))
    bbs = ib_bbs_cfg(folder)
    inbound = Path(bbs.get("GameInbound") or "inbound")
    outbound = Path(bbs.get("GameOutbound") or "outbound")
    binary = cfg.images / row.image.dirname / IB_BINARY if row.image is not None else None
    return GameInstall(**common,
                       inbound=inbound if inbound.is_absolute() else folder / inbound,
                       outbound=outbound if outbound.is_absolute() else folder / outbound,
                       binary=binary, hub_board_key=row.hub_board_key)
