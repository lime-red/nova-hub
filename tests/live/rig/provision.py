#!/usr/bin/env python3
"""Build a virgin BRE or Falcon's Eye install for one node in one test league.

Runs *as the node's own unix user* -- each node needs its own ~/.dosemu/drive_c,
and dosemu wants a real home directory. Drive it from the rig owner with:

    sudo -u node02 /srv/novatest/venv/bin/python -m rig.provision 900B 2

Provisioning is not the pure file-copy the design doc assumed. `BREDATA -Y`
regenerates GAME/ (help text, events, news templates) but not DATA/; virgin game
state only comes from `BRE.EXE RESET`, which is interactive and is driven here
through dosdrive.py.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rig import dosrun
from rig.dosdrive import DosSession, expand
from rig.layout import DOSEMU_CONF, LEAGUES, LOGS, NODES, League, Node


def _crlf(*lines) -> bytes:
    return "".join(f"{line}\r\n" for line in lines).encode()


def _unpack(league: League, node: Node, target: Path):
    game = league.g
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["unzip", "-q", str(game.media), "-d", tmp], check=True)
        shutil.move(str(Path(tmp) / game.media_dir), str(target))

    # Donor game state is meaningless for a new league; RESET rebuilds it.
    for junk in game.junk:
        (target / junk).unlink(missing_ok=True)
    # FE ships a populated BULLETIN/ in the donor tree; the scores collector would
    # pick those up as this run's output.
    for stale in (target / "BULLETIN").glob("*"):
        if stale.is_file():
            stale.unlink()
    for sub in ("INBOUND", "OUTBOUND", "BACKUP"):
        for f in (target / sub).glob("*"):
            if f.is_file():
                f.unlink()
    for sub in ("INBOUND", "OUTBOUND", "NETMAIL", "BACKUP"):
        (target / sub).mkdir(exist_ok=True)


def _write_config(league: League, node: Node, target: Path):
    dos = league.dos_path(node)

    # bbs.cfg is 7 CRLF lines. Line 4 must point at this install's own INBOUND;
    # a stale or untraversable path there is silently fatal -- the game "succeeds"
    # having ingested nothing at all. That is production bug R-3.
    (target / "bbs.cfg").write_bytes(_crlf(
        "Test Sysop",
        node.name,
        league.fido_for(node.index),
        f"{dos}\\INBOUND",
        f"{dos}\\NETMAIL",
        league.number,
        "NONE",
    ))

    # BRNODES.DAT / FENODES.DAT: node 1 is the league host and HOSTs the rest.
    hosted = " ".join(str(i) for i in league.members if i != 1)
    lines = [f"1 HOST {hosted}".strip()]
    for index in league.members:
        peer = NODES[index]
        if index != 1:
            lines.append(str(index))
        lines += [peer.name, league.fido_for(index), "Brisbane", "QLD", "AUS", ""]
    (target / league.g.nodes_file).write_bytes(_crlf(*lines))


def _batch(target: Path, name: str, dos_path: str, command: str):
    (target / name).write_bytes(_crlf("@ECHO OFF", "C:", f"CD {dos_path}",
                                      command, "EXIT"))


def _dosemu(target: Path, batch: str):
    return ["/usr/bin/dosemu", "-f", str(DOSEMU_CONF), "-K", str(target),
            "-E", batch]


def _reset(league: League, node: Node, target: Path, log_dir: Path):
    """Drive `<GAME>.EXE RESET` to a virgin game.

    The prompt sequence is neither fixed nor in the order the flow implies:
    confirm, then the *Configuration Editor* opens, and only after ESCaping out of
    it does the league-wide question appear -- and only on the node whose address
    matches the HOST entry. So state the destination and answer what turns up.
    """
    _batch(target, "RESET.BAT", league.dos_path(node), f"{league.g.exe} RESET")
    (target / "inuse.flg").unlink(missing_ok=True)

    session = DosSession(
        _dosemu(target, "RESET.BAT"),
        transcript=str(log_dir / f"prov_{league.dirname(node.index)}_reset.log"),
    )
    try:
        if not session.until(r"Configuration Editor",
                             [(r"reset the Game", "Y")], timeout=180):
            raise RuntimeError(
                f"RESET never reached the config editor for {target}\n"
                f"--- screen ---\n{session.screen_text()}"
            )
        # dosemu treats a lone ESC as the start of an escape sequence and swallows
        # it; repeated presses get one through. Mechanises the human workaround.
        session.mash(expand("<ESC>"), "Configuration Editor", max_presses=80)
        # Host node only, and only after the editor closes.
        if session.wait_for(r"league-wide reset", timeout=60):
            session.send("n")
        session.pump(20)
    finally:
        session.close()

    (target / "inuse.flg").unlink(missing_ok=True)


def _verify(league: League, node: Node, target: Path):
    """Fail the build rather than capture a fixture that is quietly broken."""
    problems = []

    data = target / "DATA"
    for required in league.g.required_data:
        # FE writes DATA/ lower case, BRE upper. Match case-insensitively so the
        # check describes the game rather than one game's habits.
        if not any(f.name.lower() == required.lower() for f in data.iterdir()):
            problems.append(f"DATA/{required} missing - RESET did not complete")

    cfg = (target / "bbs.cfg").read_bytes().decode("latin-1").split("\r\n")
    inbound_dos = cfg[3] if len(cfg) > 3 else ""
    if not inbound_dos.upper().endswith("\\INBOUND"):
        problems.append(f"bbs.cfg line 4 is {inbound_dos!r}, not this install's INBOUND")
    if not (target / "INBOUND").is_dir():
        problems.append("INBOUND directory does not exist")

    # The 8.3 guard. A longer component here does not error -- it silently ingests
    # nothing -- so it has to be caught at build time.
    for part in inbound_dos.split("\\"):
        if not part or part.endswith(":"):
            continue
        stem, _, ext = part.partition(".")
        if len(stem) > 8 or len(ext) > 3:
            problems.append(
                f"bbs.cfg inbound path component {part!r} breaks DOS 8.3; "
                f"the game will silently ingest nothing"
            )

    if (target / "inuse.flg").exists():
        problems.append("inuse.flg left behind - the game did not exit cleanly")

    if problems:
        raise RuntimeError(
            f"{league.league_id} node {node.index:02d} ({target}) failed verification:\n  "
            + "\n  ".join(problems)
        )


def provision(league: League, node: Node, log_dir: Path = LOGS) -> Path:
    target = league.install_path(node)
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"== provisioning {target} ({league.league_id} node {node.index:02d})")

    _unpack(league, node, target)
    _write_config(league, node, target)

    # BREDATA/FEDATA regenerates GAME/, not DATA/.
    _batch(target, "GAMEDATA.BAT", league.dos_path(node),
           f"{league.g.data_exe.replace('.EXE', '')} -Y")
    dosrun.run(_dosemu(target, "GAMEDATA.BAT"),
               log_dir / f"prov_{league.dirname(node.index)}_gamedata.log",
               timeout=120)

    _reset(league, node, target, log_dir)

    # RESET deletes stray .BAT files from the game directory, so the run batch has
    # to be written afterwards or it vanishes.
    _batch(target, "PROCESS.BAT", league.dos_path(node), league.g.maintenance)

    _verify(league, node, target)
    print(f"== provisioned {target}: {' '.join(sorted(p.name for p in (target / 'DATA').iterdir()))}")
    return target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("league_id", help='e.g. "900B"')
    ap.add_argument("node_index", type=int)
    a = ap.parse_args()

    league = LEAGUES[a.league_id]
    node = NODES[a.node_index]
    if os.getenv("USER") not in (node.user, None):
        print(f"warning: provisioning {node.user}'s install as {os.getenv('USER')}",
              file=sys.stderr)
    provision(league, node)


if __name__ == "__main__":
    main()
