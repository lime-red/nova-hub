"""Run a BRE command on one node's install, as that node's unix user.

Nodes are separate users, so the test process (running as the rig owner) reaches
them through sudo. Everything a scenario needs from a node is here: make it
produce a packet, hand it one, see what it emitted.
"""
import getpass
import subprocess
from pathlib import Path

from rig.layout import DOSEMU_CONF, LEAGUES, LOGS, NODES, VENV_PYTHON, League, Node

# Only INBOUND/OUTBOUND/PLANETARY accept /DETAILED, and it is what puts the
# per-item "Type: <x> <src>-> <dst>" lines in the transcript. Without it the run
# is identical but what moved between nodes is invisible.
DETAILED = {"INBOUND", "OUTBOUND", "PLANETARY"}


def _sudo(node: Node, *args) -> list:
    """Run as the node's own user -- unless we already are it.

    The rig runs as the hub user, which owns the hub's own installs and so needs
    no sudo for them (and is not permitted to sudo to itself).
    """
    if node.user == getpass.getuser():
        return list(args)
    return ["sudo", "-u", node.user, "--", *args]


def run(league: League, node: Node, command: str = "PLANETARY",
        tag: str = "run", timeout: int = 240) -> Path:
    """Run `BRE.EXE <command>` on this node's install. Returns the transcript path.

    `FULL` is deliberately not supported: it is the *player* path, not a
    maintenance command, and headless it blocks forever on "Do you want ANSI
    Graphics? (Y/n)".
    """
    assert command.upper() != "FULL", (
        "FULL is the interactive player path and will hang headless; "
        "use PLANETARY for maintenance"
    )
    install = league.install_path(node)
    flag = " /DETAILED" if command.upper() in DETAILED else ""
    log = LOGS / f"{tag}_{league.dirname(node.index)}_{command.lower()}.log"

    script = (
        "import sys; sys.path.insert(0, '.');"
        "from pathlib import Path;"
        "from rig import dosrun;"
        "from rig.layout import DOSEMU_CONF;"
        f"t = Path({str(install)!r});"
        f"(t / 'RUN.BAT').write_bytes("
        f"  '@ECHO OFF\\r\\nC:\\r\\nCD {league.dos_path(node)}\\r\\n"
        f"BRE.EXE {command.upper()}{flag}\\r\\nEXIT\\r\\n'.encode());"
        # inuse.flg is the game's mutex. An uncleanly killed run leaves one behind
        # and every later run then exits 1 having printed nothing about why.
        "(t / 'inuse.flg').unlink(missing_ok=True);"
        f"rc = dosrun.run(['/usr/bin/dosemu', '-f', str(DOSEMU_CONF), '-K', str(t),"
        f" '-E', 'RUN.BAT'], {str(log)!r}, timeout={timeout});"
        "(t / 'inuse.flg').unlink(missing_ok=True);"
        "print(rc)"
    )
    result = subprocess.run(
        _sudo(node, str(VENV_PYTHON), "-c", script),
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not run {command} on {league.league_id} node {node.index}: "
            f"{result.stderr.strip()}"
        )
    return log


def outbound(league: League, node: Node) -> list:
    return _ls(league, node, "OUTBOUND")


def inbound(league: League, node: Node) -> list:
    return _ls(league, node, "INBOUND")


def _ls(league: League, node: Node, sub: str) -> list:
    install = league.install_path(node)
    result = subprocess.run(
        _sudo(node, "sh", "-c", f"ls -1 {install / sub} 2>/dev/null || true"),
        capture_output=True, text=True, check=True,
    )
    return sorted(n for n in result.stdout.split() if n)


def read(league: League, node: Node, sub: str, filename: str) -> bytes:
    install = league.install_path(node)
    result = subprocess.run(
        _sudo(node, "cat", str(install / sub / filename)),
        capture_output=True, check=True,
    )
    return result.stdout


def take_outbound(league: League, node: Node):
    """Collect and remove everything the node has queued for sending.

    Returns [(filename, payload)] -- the same thing nova-client would upload.
    """
    collected = []
    for name in outbound(league, node):
        collected.append((name, read(league, node, "OUTBOUND", name)))
    install = league.install_path(node)
    subprocess.run(
        _sudo(node, "sh", "-c", f"rm -f {install / 'OUTBOUND'}/*"), check=True
    )
    return collected


def give_inbound(league: League, node: Node, filename: str, payload: bytes):
    """Deliver a packet into a node's inbound, the way a real download would."""
    install = league.install_path(node)
    subprocess.run(
        _sudo(node, "tee", str(install / "INBOUND" / filename)),
        input=payload, capture_output=True, check=True,
    )


def set_route(league: League, node: Node, *lines: str):
    """Write ROUTE.CFG, the game's own routing override.

    With HOST routing in BRNODES.DAT every node sends everything to node 1, so a
    direct node-to-node packet never appears. `ROUTE 3 3` restores node 3 to
    direct (per DOCS/ROUTE.SAM), which is how a scenario gets a genuine
    `900b0203.nnn` rather than a synthesised one.

    A fixture restore removes it, so this only lasts for the current test.
    """
    install = league.install_path(node)
    body = "".join(f"{line}\r\n" for line in lines)
    subprocess.run(
        _sudo(node, "tee", str(install / "ROUTE.CFG")),
        input=body.encode(), capture_output=True, check=True,
    )


def set_inbound_path(league: League, node: Node, dos_path: str):
    """Rewrite line 4 of bbs.cfg -- the DOS path the game reads packets from.

    This is the R-3 lever. Production league fe_015 has a line 4 the game cannot
    traverse, and nothing anywhere reports it: the run completes, every phase
    marker prints, and not one packet is ingested.

    A fixture restore puts the original back.
    """
    install = league.install_path(node)
    result = subprocess.run(
        _sudo(node, "cat", str(install / "bbs.cfg")), capture_output=True, check=True
    )
    lines = result.stdout.decode("latin-1").split("\r\n")
    assert len(lines) > 3, "bbs.cfg is too short to have an inbound line"
    lines[3] = dos_path
    subprocess.run(
        _sudo(node, "tee", str(install / "bbs.cfg")),
        input="\r\n".join(lines).encode("latin-1"),
        capture_output=True, check=True,
    )
