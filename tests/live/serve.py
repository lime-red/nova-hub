#!/usr/bin/env python3
"""Stand up the rig's hub as a real server, so the UI can be looked at.

The scenarios drive the hub in-process through a TestClient, which is right for
testing and useless for looking at. This builds a persistent hub instead --
real database, real data directory, real uvicorn -- populates it by running the
games for actual traffic, and serves the Vue frontend against it.

    tests/live/serve.py --build      # restore fixtures, play a round, then serve
    tests/live/serve.py              # serve what is already there

Everything is leagues 900B and 901B on novatest-hl. It touches no production
host and no production league number, and the admin password is printed at
startup rather than defaulted to something guessable.
"""
import argparse
import asyncio
import os
import shutil
import secrets
import sys
from pathlib import Path

LIVE_DIR = Path(__file__).resolve().parent
REPO = LIVE_DIR.parents[1]
for path in (str(REPO), str(LIVE_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

VIEWER_ROOT = Path(os.environ.get("NOVATEST_VIEWER", "/srv/novatest/viewer"))
DEFAULT_PORT = 8900


def build_config(data_dir: Path, port: int) -> dict:
    from rig.hub import hub_config

    config = hub_config(data_dir)
    # hub_config is built for in-process use; a served instance needs to be
    # reachable and must not start processing games behind the viewer's back.
    config["server"]["host"] = "0.0.0.0"
    config["server"]["port"] = port
    config["processing"]["poll_interval"] = 0
    return config


def populate(data_dir: Path, config: dict):
    """Play a real round so there is something to look at.

    Uses the same rig helpers the scenarios do: restore both installs from
    pristine fixtures, have node 2 produce a genuine packet, hand it to the hub,
    and let the hub's games process it. The movements that appear in the UI are
    then the games' own account of what they exchanged, not a fixture.
    """
    from rig import node as node_rig
    from rig.hub import TestHub
    from rig.layout import LEAGUES, NODES

    league = LEAGUES["900B"]
    node02 = NODES[2]

    hub = TestHub(data_dir, config).start()
    hub.seed(["900B"])

    print("restoring installs from fixtures...")
    # Restoring runs as the owning unix user so file ownership survives the
    # round trip -- the same dance conftest does for the scenarios.
    from conftest import _sudo_restore

    for index in league.members:
        _sudo_restore("900B", index)

    print("node 02: producing a packet...")
    node_rig.run(league, node02, "PLANETARY", tag="viewer")
    produced = dict(node_rig.take_outbound(league, node02))
    if not produced:
        print("  the game produced nothing; the UI will have no movements to show")
    for name, payload in produced.items():
        print(f"  delivering {name} ({len(payload)} bytes)")
        hub.deliver(name, payload)

    print("hub: processing...")
    asyncio.run(hub.process())

    from backend.models.database import ProcessingRunItem
    moved = hub.db.query(ProcessingRunItem).count()
    print(f"  recorded {moved} movement(s)")
    hub.stop()


def write_config(config: dict, where: Path):
    import toml

    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(toml.dumps(config))


def ensure_admin(data_dir: Path) -> str | None:
    """Create an admin user with a fresh random password, or leave one alone."""
    import bcrypt
    import sqlite3
    from datetime import datetime

    db = data_dir / "nova-hub.db"
    conn = sqlite3.connect(db)
    try:
        existing = conn.execute(
            "SELECT COUNT(*) FROM sysop_users WHERE username = 'admin'"
        ).fetchone()[0]
        if existing:
            return None

        password = secrets.token_urlsafe(12)
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO sysop_users (username, hashed_password, is_active, "
            "is_superuser, created_at) VALUES (?, ?, 1, 1, ?)",
            ("admin", hashed, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return password
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true",
                    help="wipe the viewer hub, restore fixtures and play a round")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    data_dir = VIEWER_ROOT / "nova-data"
    config_path = VIEWER_ROOT / "config.toml"

    if args.build and VIEWER_ROOT.exists():
        # Empty it rather than remove it: the directory is typically created by
        # root under /srv and owned by the hub user, so removing the directory
        # itself needs write permission on a parent we do not have.
        for entry in VIEWER_ROOT.iterdir():
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

    if not data_dir.exists():
        from rig.hub import make_data_dir
        VIEWER_ROOT.mkdir(parents=True, exist_ok=True)
        make_data_dir(VIEWER_ROOT)

    config = build_config(data_dir, args.port)
    write_config(config, config_path)

    if args.build:
        populate(data_dir, config)

    password = ensure_admin(data_dir)

    dist = REPO / "frontend" / "dist"
    if not dist.exists():
        print(f"WARNING: no frontend build at {dist} -- the API will serve but the "
              f"UI will not. Build it where node is available and copy it here.")

    print()
    print(f"  serving  http://{_address()}:{args.port}/")
    if password:
        print(f"  login    admin / {password}")
    else:
        print("  login    admin (password unchanged from the last build)")
    print()

    # cwd matters: process_batch() reads "config.toml" relative to it.
    os.chdir(VIEWER_ROOT)

    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=args.port, log_level="info")


def _address() -> str:
    """The tailnet address if there is one, so the printed URL is reachable."""
    import subprocess
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if out:
            return out.splitlines()[0]
    except Exception:
        pass
    import socket
    return socket.gethostname()


if __name__ == "__main__":
    main()
