#!/usr/bin/env python3
"""Build the whole rig: provision every install, then capture pristine fixtures.

Run once, as the rig owner (who needs passwordless sudo), from the repo on
novatest-hl:

    /srv/novatest/venv/bin/python -m rig.build

Each install is provisioned as its own node user, because each node needs its own
~/.dosemu/drive_c and dosemu is happier with a real home directory. Fixtures are
captured last, from a tree that has never processed a packet.
"""
import argparse
import getpass
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rig import fixtures
from rig.layout import LEAGUES, NODES, REPO, SRV, VENV_PYTHON, installs


def _sudo(user: str, *args) -> list:
    """Run as `user`, without sudo when we already are them.

    The rig's sudoers grant is one-way: novahub-t may become node02..04, but
    nothing may become novahub-t. So driving the build from the hub user -- which
    is how CI and `run.sh` do it -- would fail on the hub's own install for no
    reason other than asking sudo for a change of user that is not a change.
    """
    if user == getpass.getuser():
        return list(args)
    return ["sudo", "-u", user, "--", *args]


def provision_all(only=None):
    for league, node in installs():
        if only and league.league_id not in only:
            continue
        subprocess.run(
            _sudo(node.user, str(VENV_PYTHON), "-m", "rig.provision",
                  league.league_id, str(node.index)),
            cwd=str(REPO / "tests" / "live"),
            check=True,
        )


def capture_all(only=None):
    for league, node in installs():
        if only and league.league_id not in only:
            continue
        # Capture as the owning user so the archive is readable and the install's
        # own inuse.flg can be removed first.
        subprocess.run(
            _sudo(node.user, str(VENV_PYTHON), "-c",
                  "import sys; sys.path.insert(0, '.'); "
                  "from rig import fixtures; from rig.layout import LEAGUES, NODES; "
                  f"print(fixtures.capture(LEAGUES['{league.league_id}'], NODES[{node.index}]))"),
            cwd=str(REPO / "tests" / "live"),
            check=True,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", action="append",
                    help="restrict to one league id, e.g. 900B (repeatable)")
    ap.add_argument("--skip-provision", action="store_true")
    ap.add_argument("--skip-capture", action="store_true")
    a = ap.parse_args()

    if not a.skip_provision:
        provision_all(a.league)
    if not a.skip_capture:
        capture_all(a.league)

    print("\n== fixtures:")
    for f in sorted(Path(SRV / "fixtures").glob("*.tar.zst")):
        print(f"  {f.name}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
