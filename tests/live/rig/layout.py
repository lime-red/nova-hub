"""The shape of the test rig: who the nodes are and which leagues they play.

One place to change when the topology changes, so the provisioner, the fixtures,
the hub config and the scenarios cannot drift apart.
"""
from dataclasses import dataclass, field
from pathlib import Path

# Shared, world-readable rig assets. Each node user has its own $HOME/.dosemu tree;
# only the install media, the dosemu conf and the fixture store are shared.
SRV = Path("/srv/novatest")
MEDIA = SRV / "media" / "bre_sj1.zip"   # kept for callers that predate GAMES
# REDATE.COM hooks int 21h AH=2Ah (GET-DATE) only -- exactly where Turbo Pascal's
# GetDate goes -- so the game sees a faked date and nothing else does. libfaketime
# is not an option: it is not async-signal-safe and deadlocks dosemu2's timer
# handler, even at +0d.
REDATE = SRV / "media" / "REDATE.COM"
DOSEMU_CONF = SRV / "conf" / "vgaterm.conf"
FIXTURES = SRV / "fixtures"
LOGS = SRV / "logs"
REPO = SRV / "nova-hub"
VENV_PYTHON = SRV / "venv" / "bin" / "python"

HUB_INDEX = 1
HUB_PORT = 8900

# BRE prints this when Planetary Maintenance really finished. Asserting on it is
# what replaces the log-size floor, which is not portable across league sizes.
COMPLETION_MARKER = "Planetary Maintenance Complete"


@dataclass(frozen=True)
class Game:
    """What differs between Barren Realms Elite and Falcon's Eye.

    The two are close relatives -- same 7-line CRLF BBS.CFG, same nodes format
    with the "1 HOST 2 3 4" routing line, same PLANETARY /DETAILED maintenance
    command, same completion marker. Everything that actually differs is a name,
    so it lives here rather than as branches through the provisioner.

    The one trap worth naming: FE writes DATA/ in lower case (game.dat, planet.fe)
    where BRE writes upper (GAME.DAT, PLANET.BRE). Under dosemu both appear to DOS
    as upper case, but the rig checks them from Linux, where case is real.
    """
    code: str              # "B" / "F", the letter in a packet filename
    exe: str
    data_exe: str
    nodes_file: str
    media: Path
    media_dir: str         # top-level directory inside the media archive
    dir_prefix: str        # first letter of the install directory name
    required_data: tuple   # must exist in DATA/ once RESET has run
    junk: tuple            # donor state to strip before RESET

    @property
    def maintenance(self) -> str:
        return f"{self.exe} PLANETARY /DETAILED"


GAMES = {
    "B": Game(
        code="B",
        exe="BRE.EXE",
        data_exe="BREDATA.EXE",
        nodes_file="BRNODES.DAT",
        media=SRV / "media" / "bre_sj1.zip",
        media_dir="bre_sj1",
        dir_prefix="b",
        required_data=("GAME.DAT", "IDS.DAT", "PLANET.BRE"),
        # The media ships "ROUTE * 2", which sends a leaf node's mail to itself.
        # No working production install has a route.cfg at all -- routing comes
        # from the HOST form in the nodes file.
        junk=("inuse.flg", "DATA/BRE.LOC", "DATA/bre.loc", "DUPES.TXT", "route.cfg"),
    ),
    "F": Game(
        code="F",
        exe="FE.EXE",
        data_exe="FEDATA.EXE",
        nodes_file="FENODES.DAT",
        media=SRV / "media" / "fe_sj1.zip",
        media_dir="fe_sj1",
        dir_prefix="f",
        # Only game.dat. BRE lays down its whole DATA/ at RESET; FE does not --
        # planet.fe, routes.dat and the rest are created by the *first* PLANETARY
        # run, which is also game-day one and emits real packets (900f0102.001 and
        # siblings) and fenodes.900. So a fixture captured after that first run
        # would not be virgin. Capturing straight after RESET keeps FE's fixture
        # the same shape as BRE's: the world comes into existence on the first run
        # a scenario makes, not during provisioning. ids.dat never appears until a
        # player joins, so it is not a provisioning signal at all.
        required_data=("game.dat",),
        junk=("inuse.flg", "DATA/fe.loc", "DATA/FE.LOC", "DATA/dupes.fe",
              "DUPES.TXT", "route.cfg", "routes.lst", "routes.bad",
              "BBSINFO.LST", "PROBLEMS.LOG", "DOORFILE.SR"),
    ),
}


@dataclass(frozen=True)
class Node:
    index: int
    user: str
    name: str

    @property
    def home(self) -> Path:
        return Path("/home") / self.user

    @property
    def fido(self) -> str:
        # Filled in per-league by League.fido_for(); kept here for symmetry.
        raise NotImplementedError


@dataclass(frozen=True)
class League:
    number: str          # "900"
    game: str            # "B" (BRE) or "F" (FE)
    members: tuple       # node indices, hub first

    @property
    def league_id(self) -> str:
        return f"{self.number}{self.game}"

    @property
    def g(self) -> Game:
        return GAMES[self.game]

    def fido_for(self, index: int) -> str:
        return f"{self.number}:{self.number}/{index}"

    def dirname(self, index: int) -> str:
        """Install directory name for one node in this league.

        MUST stay within DOS 8.3. The games resolve their inbound folder from an
        absolute path in BBS.CFG using 16-bit path handling that cannot traverse a
        longer directory name -- and they fail silently, completing every phase
        while ingesting nothing. "b900n01" is 7 characters; keep it that way.
        """
        name = f"{self.g.dir_prefix}{self.number}n{index:02d}"
        assert len(name) <= 8, f"install dir {name!r} breaks DOS 8.3"
        return name

    def install_path(self, node: "Node") -> Path:
        return node.home / ".dosemu/drive_c/bbs/doors" / self.dirname(node.index)

    def dos_path(self, node: "Node") -> str:
        return "C:\\BBS\\DOORS\\" + self.dirname(node.index).upper()


NODES = {
    1: Node(1, "novahub-t", "Test Hub 01"),
    2: Node(2, "node02", "Test Node 02"),
    3: Node(3, "node03", "Test Node 03"),
    4: Node(4, "node04", "Test Node 04"),
}

# 900B spans all four nodes: 02->03 gives direct routing something to route, and
# 04 stays idle so "queued / never downloaded" has somewhere to happen.
# 901B spans 01+02 only -- enough for B-1, which needs two same-game leagues both
# producing traffic within one processing run.
# 900F is Falcon's Eye across the same four nodes. FE is the game where R-3
# actually bit in production, and it runs on a ~4-day cadence there, so real-world
# feedback on it is far too slow to rely on. It shares node users and the hub with
# 900B, which also makes it a second same-run league for the B-1 check -- this time
# across two *different* games rather than two BRE leagues.
LEAGUES = {
    "900B": League("900", "B", (1, 2, 3, 4)),
    "901B": League("901", "B", (1, 2)),
    "900F": League("900", "F", (1, 2, 3, 4)),
}


def installs():
    """Every (league, node) pair the rig provisions."""
    for league in LEAGUES.values():
        for index in league.members:
            yield league, NODES[index]


def fixture_name(league: League, node: Node) -> str:
    return league.dirname(node.index)
