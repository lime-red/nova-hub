"""An Immortal Barons league on the rig, end to end.

The BRE and FE scenarios drive the hub in-process and hand it packets directly,
because what they test is what the hub does with a packet. For IB the path in
between is as much the subject: a board's first sync is what brings it the
Coordinator's key and the roster, and the hub routes by the destination in the
name with no game in the way. So here every piece is the real one:

- the hub is a real server (uvicorn on a free port), from this tree, on a fresh
  database, and processes uploads on its own as production does;
- the hub's game is the League Coordinator, node 1, in its own data directory;
- each board is a data directory of its own, set up the way a sysop joins a
  league (-ibbs-reset, bbs.cfg, -gen-board-key), with no roster and no
  Coordinator key until its client fetches them;
- each board syncs with nova-client's own client.py, from the checkout
  rig.nova_client keeps, under the board's own credentials.

What a scenario still does itself is run the game: `board.maint()` is the
-maint the client's daemon would run on its schedule, so the order of events is
the test's to choose.

IB is native, so none of this needs dosemu, a fixture or a node user: every
board belongs to the rig user and lives under the test's tmp_path.
"""
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import bcrypt
import toml

from rig.hub import REPO, TestHub, hub_config, make_data_dir
from rig.layout import HUB_INDEX, IB_CURRENT, NODES, NOVA_CLIENT, VENV_PYTHON

LEAGUE = "900"
KEY = re.compile(r"\b[0-9a-f]{64}\b")
# A rule the Coordinator sets that no default has, so a member that adopted the
# league's rules can be told from one still on its own.
TURNS_PER_DAY = 23


@dataclass(frozen=True)
class Build:
    """One entry of current.json: the IB the scenarios play."""
    kind: str
    ref: str
    commit: str
    binary: Path
    version: str

    @property
    def id(self) -> str:
        return f"{self.kind}-{self.ref if self.kind == 'release' else self.commit[:7]}"


def builds() -> dict:
    """current.json as rig.ib_builds left it: kind -> entry (or {} if never run)."""
    try:
        return json.loads(IB_CURRENT.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def ib(binary: Path, data: Path, *args, timeout: int = 120,
       clock: str = None) -> subprocess.CompletedProcess:
    """Run the game on a data directory, headless: on the real clock, or shifted by
    `clock` (IB_CLOCK_OFFSET, a Go duration such as 3600s)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("IB_")}
    if clock:
        env["IB_CLOCK_OFFSET"] = clock
    return subprocess.run([str(binary), *args, "-data", str(data)], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout)


def _bbs_cfg(name: str) -> str:
    return (f"BoardID {name}\nBBSName {name}\nLeagueNumber {int(LEAGUE)}\n"
            f"GameInbound inbound\nGameOutbound outbound\n")


def _new_game(binary: Path, data: Path, name: str) -> str:
    """-ibbs-reset, bbs.cfg and a board key, as a sysop sets a board up. Returns the
    board's public key."""
    data.mkdir(parents=True)
    reset = ib(binary, data, "-ibbs-reset", "-board-id", name)
    assert reset.returncode == 0, f"-ibbs-reset failed for {name}:\n{reset.stdout}{reset.stderr}"
    (data / "bbs.cfg").write_text(_bbs_cfg(name))
    made = ib(binary, data, "-gen-board-key")
    found = KEY.search(made.stdout + made.stderr)
    assert found, f"-gen-board-key printed no key for {name}:\n{made.stdout}{made.stderr}"
    return found.group(0)


class Board:
    """A member board: its game's data directory and its client's config."""

    def __init__(self, league: "IbLeague", index: int):
        self.league = league
        self.index = index
        self.node = NODES[index]
        self.name = self.node.name
        self.root = league.root / f"board{index:02d}"
        self.data = self.root / "data"
        self.config = self.root / "config.toml"
        self.secret = f"{self.node.user}-ib-secret"
        self.key = _new_game(league.build.binary, self.data, self.name)
        self.last_sync = None

    def write_config(self) -> None:
        data = str(self.data)
        self.config.write_text(toml.dumps({
            "hub": {"url": self.league.url, "client_id": self.node.user,
                    "client_secret": self.secret},
            "bbs": {"name": self.name},
            "sync": {"sent_action": "archive", "archive_dir": str(self.root / "sent"),
                     "metrics_file": str(self.root / "metrics.json"),
                     "max_retries": 1, "retry_delay": 1, "timeout_seconds": 60},
            "leagues": {"IB": {LEAGUE: {
                "enabled": True, "bbs_index": self.index, "runner": "native",
                "game_folder": data, "inbound_dir": f"{data}/inbound",
                "outbound_dir": f"{data}/outbound",
                "game_command": str(self.league.build.binary),
                "maintenance_args": f"-maint -data {data}",
            }}},
        }))

    # -- what the client's daemon does --------------------------------------------

    def sync(self, check: bool = True) -> subprocess.CompletedProcess:
        """One nova-client sync, then wait for the hub to finish what it started."""
        result = subprocess.run(
            [str(VENV_PYTHON), "client.py", "--config", str(self.config), "--verbose"],
            cwd=str(NOVA_CLIENT / "python"), capture_output=True, text=True, timeout=180,
        )
        self.last_sync = result.stdout + result.stderr
        if check:
            assert result.returncode == 0, (
                f"board {self.index:02d}'s sync exited {result.returncode}:\n{self.last_sync}")
        self.league.wait_idle()
        return result

    def maint(self, clock: str = None) -> subprocess.CompletedProcess:
        return ib(self.league.build.binary, self.data, "-maint", clock=clock)

    # -- what the board holds ------------------------------------------------------

    def file(self, name: str) -> Path:
        return self.data / name

    def outbound(self) -> list:
        return sorted(p.name for p in (self.data / "outbound").glob("*"))

    def inbound(self) -> list:
        return sorted(p.name for p in (self.data / "inbound").glob("*"))

    def rules(self) -> dict:
        return json.loads((self.data / "config.json").read_text())

    def world(self) -> dict:
        return json.loads((self.data / "world.json").read_text())

    def turns_left(self) -> dict:
        """Realm name -> turns left, from the save. A world just reset has none,
        and saves them as null."""
        return {e["Name"]: e["TurnsLeft"] for e in self.world()["Empires"] or []}

    def ledger(self) -> dict:
        path = self.data / "packet-ledger.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def log_mark(self) -> int:
        return len(self.planetary_log())

    def log_since(self, mark: int) -> str:
        return self.planetary_log()[mark:]

    def faults_since(self, mark: int) -> list:
        """planetary.log lines since `mark` that report something held or refused."""
        return [line for line in self.log_since(mark).splitlines()
                if "held" in line or "refused" in line.lower()]

    def planetary_log(self) -> str:
        path = self.data / "planetary.log"
        return path.read_text(errors="replace") if path.exists() else ""

    def roster_boards(self) -> list:
        """The node numbers in this board's ibnodes.dat, as the game would read them."""
        return roster_numbers(self.data / "ibnodes.dat")

    def league_check(self) -> str:
        result = ib(self.league.build.binary, self.data, "-league-check")
        return result.stdout + result.stderr


class IbLeague:
    """League 900I: the hub and its game, and the member boards."""

    def __init__(self, root: Path, build: Build):
        self.root = Path(root)
        self.build = build
        self.boards = {}
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.data_dir = make_data_dir(self.root)
        self.game = self.root / "hub-game"
        self.hub_name = NODES[HUB_INDEX].name
        self._server = None
        self._hub = None
        self.league_row_id = None

    # -- set up -----------------------------------------------------------------

    def start(self, members=(2, 3)) -> "IbLeague":
        binary = self.build.binary
        hub_key = _new_game(binary, self.game, self.hub_name)
        made = ib(binary, self.game, "-gen-coord-key")
        assert KEY.search(made.stdout + made.stderr), f"-gen-coord-key:\n{made.stdout}{made.stderr}"
        rules = json.loads((self.game / "config.json").read_text())
        rules["TurnsPerDay"] = TURNS_PER_DAY
        (self.game / "config.json").write_text(json.dumps(rules, indent=2))

        self.config = hub_config(self.data_dir)
        # The rig's dosemu leagues have no business here; only the defaults stay.
        self.config["dosemu"] = {k: v for k, v in self.config["dosemu"].items()
                                 if not isinstance(v, dict)}
        self.config["server"].update(port=self.port, public_url=self.url)
        # Long enough that one client's several uploads make one run.
        self.config["processing"]["settle_seconds"] = 1
        self.config["ib"] = {LEAGUE: {"binary": str(binary), "data_dir": str(self.game),
                                      "hub_board_key": hub_key}}
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(toml.dumps(self.config))

        self._hub = TestHub(self.data_dir, self.config).start()
        from backend.models.database import League
        row = League(league_id=LEAGUE, game_type="I", name="Rig 900I", is_active=True,
                     hub_fidonet_address=f"{LEAGUE}:{LEAGUE}/{HUB_INDEX}", hub_routes_mail=False)
        self._hub.db.add(row)
        self._hub.db.commit()
        self.league_row_id = row.id

        for index in members:
            self._join(index)
        self.regenerate()
        self._serve()
        for board in self.boards.values():
            board.write_config()
        return self

    def _join(self, index: int) -> "Board":
        """What approving a board does: a client, a membership with its key."""
        from backend.models.database import Client, FtnAddress, LeagueMembership

        board = Board(self, index)
        db = self._hub.db
        client = Client(client_id=board.node.user, bbs_name=board.name, is_active=True,
                        client_secret=bcrypt.hashpw(board.secret.encode(),
                                                    bcrypt.gensalt()).decode())
        db.add(client)
        db.commit()
        db.add(LeagueMembership(
            client_id=client.id, league_id=self.league_row_id, bbs_index=index,
            ftn_address=FtnAddress.get_or_create(db, client.id, f"{LEAGUE}:{LEAGUE}/{index}"),
            board_key=board.key, is_active=True))
        db.commit()
        self.boards[index] = board
        return board

    def add_board(self, index: int) -> "Board":
        """A board joining a league that is already running."""
        board = self._join(index)
        self.regenerate()
        board.write_config()
        return board

    def regenerate(self) -> list:
        """Generate the roster, as the admin UI's Generate Nodelist does."""
        from backend.models.database import League
        from backend.services.providers import provider_for

        db = self._hub.db
        league = db.get(League, self.league_row_id)
        _, problems = provider_for("I").generate_nodelist(db, self.config, league)
        db.commit()
        return problems

    def _serve(self) -> None:
        env = dict(os.environ, NOVA_HUB_CONFIG=str(self.config_path), PYTHONPATH=str(REPO))
        self._log = open(self.root / "hub.log", "ab")
        self._server = subprocess.Popen(
            [str(VENV_PYTHON), "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=str(REPO), env=env, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self._server.poll() is not None:
                raise RuntimeError(f"the hub exited {self._server.returncode}:\n{self.hub_log()}")
            try:
                with urllib.request.urlopen(f"{self.url}/service/docs", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError(f"the hub did not answer on {self.url}:\n{self.hub_log()}")

    def stop(self) -> None:
        if self._server and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._server.kill()
        if self._server:
            self._log.close()
        if self._hub:
            self._hub.stop()

    # -- running it -----------------------------------------------------------------

    def settle(self, max_rounds: int = 5) -> int:
        """Exchange rounds until the league is quiet: returns how many it took.

        A round is, per board, a sync, a -maint and a sync. Until a board plays the
        league's rules, the Coordinator and the other members hold what it sends,
        and it is told so ("... refused our packet: your packets are being held
        here"). Those notices trail the adoption by a round or so, because they
        answer packets already in flight. A round with none on any board, and no
        failed hub run, is the steady state; measured on the rig 2026-10-29, a new
        two-board league reaches it on round 4 on v0.2.6 and round 3 on trunk.

        Quiet is not enough on its own: a board that goes first in a round can be
        quiet and still a roster behind, because the broadcast that carries the
        new one comes from a hub run later in the same round. So the league is
        settled when a round is quiet and every board holds the hub's roster and
        plays its rules.
        """
        hub_roster = roster_numbers(self.game / "ibnodes.dat")
        hub_rules = json.loads((self.game / "config.json").read_text())["TurnsPerDay"]
        for rounds in range(1, max_rounds + 1):
            marks = {i: b.log_mark() for i, b in self.boards.items()}
            first_run = len(self.runs())
            for board in self.boards.values():
                board.sync()
                board.maint()
                board.sync()
            noisy = {i: b.faults_since(marks[i]) for i, b in self.boards.items()}
            failed = [r for r in self.runs()[first_run:] if r["status"] != "completed"]
            behind = {i: (b.roster_boards(), b.rules().get("TurnsPerDay"))
                      for i, b in self.boards.items()
                      if b.roster_boards() != hub_roster or b.rules().get("TurnsPerDay") != hub_rules}
            if not any(noisy.values()) and not failed and not behind:
                return rounds
        raise AssertionError(
            f"league 900I still not quiet after {max_rounds} rounds:\n"
            + "\n".join(f"board {i:02d}: {lines}" for i, lines in noisy.items() if lines)
            + (f"\nhub runs: {failed}" if failed else "")
            + (f"\nbehind the hub's roster {hub_roster} or rules: {behind}" if behind else ""))

    def wait_idle(self, timeout: float = 120) -> None:
        """Until every packet the hub holds has been processed and no run is going.

        An upload is a row with no processed_at until the run that takes it; the run
        starts after settle_seconds and may go round again, but whatever it is
        waiting for is a row with no processed_at, so a look that sees none pending
        and none running is quiet. Two in a row, as a guard against reading
        between a run's last commit and its status.
        """
        deadline = time.monotonic() + timeout
        quiet = 0
        while time.monotonic() < deadline:
            pending, running = self._query(
                "SELECT (SELECT COUNT(*) FROM packets WHERE processed_at IS NULL),"
                " (SELECT COUNT(*) FROM processing_runs WHERE status = 'running')")[0]
            quiet = quiet + 1 if pending == 0 and running == 0 else 0
            if quiet >= 2:
                return
            time.sleep(0.3)
        raise TimeoutError(f"the hub still had work after {timeout}s:\n{self.hub_log()[-4000:]}")

    # -- what the hub holds -----------------------------------------------------------

    def _query(self, sql: str, *args) -> list:
        conn = sqlite3.connect(f"file:{self.data_dir / 'nova-hub.db'}?mode=ro", uri=True,
                               timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def packets(self) -> list:
        return [dict(r) for r in self._query(
            "SELECT filename, source_bbs_index, dest_bbs_index, sequence_number, checksum,"
            " processed_at, downloaded_at FROM packets ORDER BY id")]

    def runs(self) -> list:
        return [dict(r) for r in self._query(
            "SELECT id, status, error_message FROM processing_runs ORDER BY id")]

    def spool(self, which: str) -> Path:
        return self.data_dir / "packets" / which

    def hub_log(self) -> str:
        path = self.root / "hub.log"
        return path.read_text(errors="replace") if path.exists() else ""

    def game_file(self, name: str) -> Path:
        return self.game / name


def roster_numbers(path: Path) -> list:
    """The node numbers in an ibnodes.dat: the first word of each blank-line block."""
    if not path.exists():
        return []
    entries = path.read_text().replace("\r\n", "\n").strip().split("\n\n")
    return sorted(int(e.split("\n")[0].split()[0]) for e in entries if e.strip())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def parse_name(filename: str):
    """(league, source, sequence, destination) from L900-2-000000000000a-3.brp."""
    m = re.fullmatch(r"L(\d{3})-([0-9a-z]+)-([0-9a-z]{13})-([0-9a-z]+)\.brp", filename,
                     re.IGNORECASE)
    if not m:
        return None
    return m.group(1), int(m.group(2), 36), int(m.group(3), 36), int(m.group(4), 36)
