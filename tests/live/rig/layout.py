"""The shape of the test rig: who the nodes are and which leagues they play.

One place to change when the topology changes, so the provisioner, the fixtures,
the hub config and the scenarios cannot drift apart.
"""
from dataclasses import dataclass, field
from pathlib import Path

# Shared, world-readable rig assets. Each node user has its own $HOME/.dosemu tree;
# only the install media, the dosemu conf and the fixture store are shared.
SRV = Path("/srv/novatest")
MEDIA = SRV / "media" / "bre_sj1.zip"
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

    def fido_for(self, index: int) -> str:
        return f"{self.number}:{self.number}/{index}"

    def dirname(self, index: int) -> str:
        """Install directory name for one node in this league.

        MUST stay within DOS 8.3. The games resolve their inbound folder from an
        absolute path in BBS.CFG using 16-bit path handling that cannot traverse a
        longer directory name -- and they fail silently, completing every phase
        while ingesting nothing. "b900n01" is 7 characters; keep it that way.
        """
        name = f"b{self.number}n{index:02d}"
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
LEAGUES = {
    "900B": League("900", "B", (1, 2, 3, 4)),
    "901B": League("901", "B", (1, 2)),
}


def installs():
    """Every (league, node) pair the rig provisions."""
    for league in LEAGUES.values():
        for index in league.members:
            yield league, NODES[index]


def fixture_name(league: League, node: Node) -> str:
    return league.dirname(node.index)
