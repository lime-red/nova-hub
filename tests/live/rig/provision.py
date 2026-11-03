#!/usr/bin/env python3
"""Build a virgin BRE or Falcon's Eye install for one node in one test league.

Runs *as the node's own unix user* -- each node needs its own ~/.dosemu/drive_c,
and dosemu wants a real home directory. Drive it from the rig owner with:

    sudo -u node02 /srv/novatest/venv/bin/python -m rig.provision 900B 2

Each install is a copy of the game's golden image (rig.golden), made by
make_golden's recipe: the media with the donor's league taken out and GAME/
regenerated. Provisioning stamps it as this node in this league and RESETs it;
virgin game state only comes from `BRE.EXE RESET`, which is interactive and is
driven through sr-harness's DosSession (backend/services/golden.py).
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rig import golden
from rig.layout import DOSEMU_CONF, LEAGUES, LOGS, NODES, League, Node
from backend.services.golden import RECIPES, Dosemu, reset


def _crlf(*lines) -> bytes:
    return "".join(f"{line}\r\n" for line in lines).encode()


def _unpack(league: League, node: Node, target: Path):
    if target.exists():
        shutil.rmtree(target)
    golden.install(league.g, target)


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


def _dos(node: Node, log_dir: Path) -> Dosemu:
    return Dosemu(drive_c=node.home / ".dosemu/drive_c", conf=DOSEMU_CONF, log_dir=log_dir)


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
    reset(target, RECIPES[league.game], _dos(node, log_dir),
          log_dir / f"prov_{league.dirname(node.index)}_reset.log")

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
