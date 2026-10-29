#!/usr/bin/env python3
"""The nova-client the IB scenarios sync boards with, checked out from gitea.

    /srv/novatest/venv/bin/python -m rig.nova_client [REF]    # default: main

run.sh calls it before pytest, with REF from --client (default main), so the
scenarios play this hub against the client as it stands rather than a copy
that drifts. The checkout is detached and forced, like a --try tree: it is a
copy of the repo, never somewhere to edit. The client runs on the rig's own
interpreter; its requirements (aiohttp, toml, python-dotenv) are nova-hub's
too, and a run says so if one is missing rather than failing inside a sync.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rig.layout import NOVA_CLIENT, NOVA_CLIENT_REPO

NEEDS = ("aiohttp", "toml", "dotenv")


def _git(*args, cwd=NOVA_CLIENT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def checkout(ref: str = "main") -> str:
    if not (NOVA_CLIENT / ".git").is_dir():
        _git("clone", "-q", NOVA_CLIENT_REPO, str(NOVA_CLIENT), cwd=NOVA_CLIENT.parent)
    _git("fetch", "-q", "--force", "--tags", "origin")
    for rev in (f"origin/{ref}", ref):
        found = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{rev}^{{commit}}"],
                               cwd=NOVA_CLIENT, capture_output=True, text=True)
        if found.returncode == 0:
            sha = found.stdout.strip()
            break
    else:
        raise SystemExit(f"nova-client has no branch, tag or commit {ref!r}")
    _git("checkout", "-q", "--force", "--detach", sha)
    _git("clean", "-q", "-fdx")
    return _git("log", "-1", "--format=%h %s")


def main() -> int:
    if not NOVA_CLIENT.is_dir() or not os.access(NOVA_CLIENT, os.W_OK):
        print(f"{NOVA_CLIENT} is missing or not writable; create it once with:\n"
              f"  sudo install -d -o $(id -un) -g $(id -gn) -m 755 {NOVA_CLIENT}", file=sys.stderr)
        return 1
    ref = sys.argv[1] if len(sys.argv) > 1 else "main"
    print(f"nova-client {ref}: {checkout(ref)}")
    missing = [m for m in NEEDS if importlib.util.find_spec(m) is None]
    if missing:
        print(f"the rig interpreter lacks nova-client's {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
