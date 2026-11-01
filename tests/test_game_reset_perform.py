"""Performing a game reset: the editor driver, the job around it, the reset
history, and the API.

The driver runs here against FakeEditor, a small model of RESET's editor built
from the screens captured on the test rig (15 Oct 2026). The real editor is
driven by tests/live/test_game_reset.py.
"""
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import GameReset, League, Packet, ProcessingRun
from backend.services import bre_packet
from backend.services.game_reset import editor, history, perform
from backend.services.game_reset.drafts import validate
from backend.services.game_reset.editor import EditorDriver, EditorError
from backend.services.game_reset.fields import CHOICE, DATE, NUMBER, TOGGLE, defaults, fields_for

TODAY = date(2026, 10, 15)


# ── a model of the editor ─────────────────────────────────────────────────
class FakeEditor:
    """RESET's screens and keys, as seen on the rig. `swap` opens the wrong
    field's help for one row; `ignore` makes a row's edits not stick."""

    TITLE = {"B": "Barren Realms Elite", "F": "Falcon's Eye"}

    def __init__(self, game_type, swap=None, ignore=None):
        self.gt = game_type
        self.rows = {p: [f for f in fields_for(game_type) if f.page == p and f.help_title]
                     for p in (1, 2)}
        self.v = {f.id: f.default for f in fields_for(game_type)}
        self.v["start_date"] = TODAY.isoformat()
        self.state, self.page, self.cursor = "ask", 1, 0
        self.closed, self.typed, self.asked = False, "", []
        self.swap, self.ignore = swap or {}, ignore
        self.keys = []

    # the session interface
    def pump(self, timeout=0):
        pass

    def drain(self, timeout):
        return self.closed

    def close(self, grace=0):
        self.closed = True
        return 0

    def send(self, keys):
        self.keys.append(keys)
        i = 0
        while i < len(keys):
            if keys.startswith("\x1b[", i):
                j = i + 2
                while keys[j] not in "ABCD~":
                    j += 1
                self._key(keys[i:j + 1])
                i = j + 1
            else:
                self._key(keys[i])
                i += 1

    # what's on screen
    def _shown(self, f):
        v = self.v[f.id]
        if f.kind == NUMBER:
            return str(v)
        if f.kind == TOGGLE:
            return "Enabled" if v else "Disabled"
        if f.kind == DATE:
            d = date.fromisoformat(v)
            return f"{d:%m/%d/%Y}  00:00:00"
        if f.id == "sabre":
            return {"N": "None/Disabled", "U": "User Select/Original Setting",
                    "R": "Random", "C": f"Constant Setting Of {self.v['sabre_dial']}"}[v]
        return dict(f.choices)[v]

    def screen_text(self):
        if self.state == "ask":
            return "Barren Realms Elite\n\nDo you wish to reset the Game? (y/N) "
        if self.state == "league":
            return "Is this a league-wide reset? (Y/n) "
        if self.state == "done":
            return ""
        if self.state == "page":
            nav = "[PG-DN]" if self.page == 1 else "[PG-UP]"
            lines = [f"─────══{self.TITLE[self.gt]} Configuration Editor══════──────"]
            for f in self.rows[self.page]:
                label = ("*" if f.interbbs else "") + f.label
                lines.append(f"{label:<30}: {self._shown(f)}")
            lines += [""] * 5
            lines.append(f"* = InterBBS Setting Only   {nav} For More Options   "
                         f"[ESC] to Save & Quit")
            return "\n".join(lines)
        f = self.field
        topic = self.swap.get(f.id, f.help_title)
        lines = ["                    ──═Configuration Help Information═──",
                 f"  ──═{topic}═──", "  Some help about it.", ""]
        if self.state == "num":
            # The real editor puts thousands commas in both: "(0; 50,000)".
            lines.append(f"Enter New Value: ({self.v[f.id]:,}; {f.max:,}) {self.typed}")
        elif self.state == "choice":
            lines += ["Possible Settings: " + "\n                   ".join(
                n for _, n in f.choices), f"New Setting: {self.typed}"]
        elif self.state == "dial":
            lines.append(f"New Setting: Constant.  Enter value > {self.typed}")
        elif self.state == "toggled" and (self.gt, f.id) == ("B", "local_attacks"):
            # BRE's help fills the screen: no message, only the pause.
            lines.append("─»>Paused<«─")
        elif self.state == "toggled":
            lines.append("  This option has been toggled.")
        elif self.state == "date":
            d = date.fromisoformat(self.v[f.id])
            cur = {"Day": (d.day, 31), "Month": (d.month, 12), "Year": (d.year, 2999)}
            lines.append("Please enter when you wish the game to start:")
            for part, val in self.asked:
                lines.append(f"{part} ({cur[part][0]}; {cur[part][1]}) {val}")
            part = self.order[len(self.asked)]
            lines.append(f"{part} ({cur[part][0]}; {cur[part][1]}) {self.typed}")
        return "\n".join(lines)

    # what keys do
    @property
    def field(self):
        return self.rows[self.page][self.cursor]

    def _set(self, fid, value):
        if fid != self.ignore:
            self.v[fid] = value

    def _key(self, k):
        s = self.state
        if s == "ask":
            if k in "Yy":
                self.state = "page"
        elif s == "league":
            if k in "Yy\r":
                self.state, self.closed = "done", True
        elif s == "page":
            n = len(self.rows[self.page])
            if k == "\x1b[B":
                self.cursor = min(self.cursor + 1, n - 1)
            elif k == "\x1b[A":
                self.cursor = max(self.cursor - 1, 0)
            elif k == "\x1b[6~":
                self.page, self.cursor = 2, 0
            elif k == "\x1b[5~":
                self.page, self.cursor = 1, 0
            elif k == "\x1b":
                self.state = "league"
            elif k == "\r":
                f = self.field
                self.typed = ""
                if f.kind == NUMBER:
                    self.state = "num"
                elif f.kind == CHOICE:
                    self.state = "choice"
                elif f.kind == TOGGLE:
                    self._set(f.id, not self.v[f.id])
                    self.state = "toggled"
                else:
                    self.state, self.asked = "date", []
                    self.order = (["Day", "Month", "Year"] if self.gt == "B"
                                  else ["Month", "Day", "Year"])
        elif s == "toggled":
            self.state = "page"
        elif s in ("num", "dial", "date"):
            if k.isdigit():
                self.typed += k
            elif k == "\r":
                self._enter()
        elif s == "choice":
            letters = [l for l, _ in self.field.choices]
            if k == "\r":
                self.state = "page"
            elif k.upper() in letters:
                self._set(self.field.id, k.upper())
                self.state = "dial" if (self.field.id, k.upper()) == ("sabre", "C") else "page"
                self.typed = ""

    def _enter(self):
        f = self.field
        if self.state == "num":
            if self.typed and int(self.typed) > f.max:
                self.typed = str(f.max)          # the clamp: waits for a second Enter
                return
            if self.typed:
                self._set(f.id, int(self.typed))
            self.state = "page"
        elif self.state == "dial":
            self._set("sabre_dial", int(self.typed))
            self.state = "page"
        else:
            self.asked.append((self.order[len(self.asked)], self.typed))
            self.typed = ""
            if len(self.asked) == 3:
                parts = dict(self.asked)
                self._set(f.id, date(int(parts["Year"]), int(parts["Month"]),
                                     int(parts["Day"])).isoformat())
                self.state = "page"


def drive(game_type, changes, **fake):
    wanted, errors = validate(game_type, {**defaults(game_type), **changes}, TODAY)
    assert not errors
    ed = FakeEditor(game_type, **fake)
    log = []
    d = EditorDriver(ed, game_type, wanted, TODAY, log=log.append,
                     step_timeout=0.5, key_gap=0)
    return d, ed, log


# ── the driver ────────────────────────────────────────────────────────────
def test_page_rows_reads_the_rig_screen():
    screen = "\n".join([
        "─────══Barren Realms Elite Configuration Editor══════───────────────────",
        "Attack Rewards                : Medium",
        "Sabre Handling                : Constant Setting Of 7",
        '*Days for "Lost Attacks"      : 7',
        "*Gooie Kablooies              : Enabled",
        "", "* = InterBBS Setting Only   [PG-UP] For More Options   [ESC] to Save & Quit"])
    assert editor.page_rows(screen) == {
        "Attack Rewards": "Medium", "Sabre Handling": "Constant Setting Of 7",
        'Days for "Lost Attacks"': "7", "Gooie Kablooies": "Enabled"}
    assert editor.page_rows("Do you wish to reset the Game? (y/N)") == {}


def test_a_bre_reset_changes_only_what_differs_and_reads_back():
    changes = {"start_date": "2026-11-20", "turns_per_day": 12, "steady_investment": True,
               "buy_military": "L", "attack_rewards": "L", "sabre": "C", "sabre_dial": 7,
               "lost_attack_days": 3, "gooie": False, "dupe_checking": False,
               "initial_land": 4321,  # a prompt with commas: "(0; 50,000)"
               "local_attacks": False}  # toggles with no message
    d, ed, log = drive("B", changes)
    readback = d.run()
    assert ed.closed and ed.state == "done"
    for fid, v in changes.items():
        assert ed.v[fid] == v, fid
    assert readback["sabre"] == "Constant Setting Of 7"
    assert readback["start_date"].startswith("11/20/2026")
    # Untouched fields were never opened.
    opened = [line.split(":")[0] for line in log if ":" in line]
    assert "Tax Rate" not in opened and "Interest Rate" not in opened
    assert log[-1] == "league-wide reset? Y"


def test_an_fe_date_is_asked_month_first():
    d, ed, log = drive("F", {"join_date": "2026-12-31", "start_runes": 50,
                             "local_attacks": False})
    d.run()
    assert ed.v["join_date"] == "2026-12-31"
    parts = [line for line in log if line.startswith("Game Join Date")]
    assert [p.split()[3].rstrip(":") for p in parts] == ["Month", "Day", "Year"]


def test_a_blank_start_date_is_left_to_the_reset():
    d, ed, log = drive("B", {"turns_per_day": 9})
    d.run()
    assert not any(line.startswith("Game Start Date") for line in log)


def test_the_wrong_help_aborts_before_answering():
    d, ed, _ = drive("B", {"tax_rate": 60}, swap={"tax_rate": "Bank Interest Rate"})
    with pytest.raises(EditorError, match="opened 'Bank Interest Rate'"):
        d.run()
    assert d.stage == "editor" and ed.v["tax_rate"] == 50 and not ed.closed


def test_a_clamp_is_caught_not_followed():
    # validate() stops this; the driver must still not type a second Enter.
    d, ed, _ = drive("B", {})
    d.wanted["turns_per_day"] = 25
    with pytest.raises(EditorError, match="Turns Per Day"):
        d.run()
    assert ed.state == "num"


def test_an_edit_that_doesnt_stick_aborts():
    d, ed, _ = drive("B", {"bombings": 9}, ignore="bombings")
    with pytest.raises(EditorError, match="Bombings / Day reads '5'"):
        d.run()


def test_shown_matches_the_editor_wording():
    f = {x.id: x for x in fields_for("B")}
    s = {**defaults("B"), "sabre": "U"}
    assert editor.shown(f["sabre"], s) == "User Select/Original Setting"
    assert editor.shown(f["sabre"], {**s, "sabre": "C", "sabre_dial": 0}) == "Constant Setting Of 0"
    assert editor.shown(f["buy_military"], {**s, "buy_military": "L"}) == "Limited"
    assert editor.matches(f["join_date"], s, "01/01/2999  00:00:00")


# ── the job ───────────────────────────────────────────────────────────────
@pytest.fixture
def game(tmp_path, db_session, db_engine, monkeypatch):
    """A league whose 'game' is a folder of files, and a config pointing at it."""
    folder = tmp_path / "drive_c" / "bre_900"
    (folder / "DATA").mkdir(parents=True)
    (folder / "BRE.EXE").write_bytes(b"MZ" + bytes(range(256)))
    (folder / "DATA" / "GAME.DAT").write_bytes(b"old game" * 100)
    (folder / "DATA" / "PLAYER.DAT").write_bytes(b"players")
    (folder / "INBOUND").mkdir()
    config = {
        "server": {"data_dir": str(tmp_path / "data")},
        "hub": {"bbs_index": "01"},
        "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 300,
                   "900": {"bre": {"game_folder": str(folder),
                                   "game_dos_path": "C:\\BRE_900",
                                   "processing_command": "BRE.EXE PLANETARY /DETAILED"}}},
    }
    league = League(league_id="900", game_type="B", name="900B", is_active=True)
    db_session.add(league)
    db_session.commit()
    Session = sessionmaker(bind=db_engine)
    monkeypatch.setattr(perform, "get_session", lambda: Session())
    return SimpleNamespace(folder=folder, config=config, league=league, db=db_session)


def tree(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def make_draft(g, **settings):
    s, errors = validate("B", {**defaults("B"), **settings}, TODAY)
    assert not errors
    d = GameReset(league_id=g.league.id, status="draft", source="console", settings=s,
                  version=3, created_by="mantis", updated_by="mantis")
    g.db.add(d)
    g.db.commit()
    return d


class FakeProcessing:
    """Stands in for ProcessingService: records the leagues it was asked to run."""
    calls = []
    status = "completed"

    def __init__(self, db, config):
        self.db = db

    async def process_batch(self, also=()):
        FakeProcessing.calls.append(also)
        run = ProcessingRun(status=FakeProcessing.status, error_message=None
                            if FakeProcessing.status == "completed" else "dosemu exited 1")
        self.db.add(run)
        self.db.commit()
        return run


@pytest.fixture
def processing(monkeypatch):
    import backend.services.processing_service as ps
    FakeProcessing.calls, FakeProcessing.status = [], "completed"
    monkeypatch.setattr(ps, "ProcessingService", FakeProcessing)
    return FakeProcessing


def test_start_refuses_what_it_should(game, tmp_path):
    g = game
    with pytest.raises(perform.ResetRefused, match="no draft"):
        perform.start(g.db, g.league, 1, "900B", "mantis", g.config, TODAY)
    make_draft(g)
    with pytest.raises(perform.ResetRefused, match="Type the league code") as e:
        perform.start(g.db, g.league, 3, "900F", "mantis", g.config, TODAY)
    assert e.value.status == 422
    with pytest.raises(perform.ResetRefused, match="saved this draft"):
        perform.start(g.db, g.league, 2, "900b", "mantis", g.config, TODAY)
    (g.folder / "INUSE.FLG").write_text("")
    with pytest.raises(perform.ResetRefused, match="inuse.flg"):
        perform.start(g.db, g.league, 3, "900b", "mantis", g.config, TODAY)
    (g.folder / "INUSE.FLG").unlink()
    no_section = {**g.config, "dosemu": {}}
    with pytest.raises(perform.ResetRefused, match=r"\[dosemu.900.bre\]"):
        perform.start(g.db, g.league, 3, "900b", "mantis", no_section, TODAY)
    reset = perform.start(g.db, g.league, 3, " 900b ", "mantis", g.config, TODAY)
    assert reset.status == "applying" and reset.applied_by == "mantis"
    assert [p["stage"] for p in reset.progress] == ["queued"]
    other = League(league_id="901", game_type="B", name="901B", is_active=True)
    g.db.add(other)
    g.db.commit()
    g.db.add(GameReset(league_id=other.id, status="draft", source="console",
                       settings=reset.settings, version=1))
    g.db.commit()
    with pytest.raises(perform.ResetRefused, match="already under way"):
        perform.start(g.db, other, 1, "901B", "mantis", g.config, TODAY)


@pytest.mark.asyncio
async def test_a_good_reset_saves_and_runs_planetary(game, processing):
    g = game
    make_draft(g, turns_per_day=12)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)
    seen = {}

    def fake_drive(setup, game_type, settings, today, log, transcript):
        seen.update(setup=setup, settings=settings)
        log("typed it all")
        (setup.folder / "DATA" / "GAME.DAT").write_bytes(b"new game")

    await perform.run_job(reset.id, g.config, drive_fn=fake_drive, today=TODAY)
    g.db.expire_all()
    r = g.db.get(GameReset, reset.id)
    assert r.status == "applied" and r.error is None and r.applied_at
    assert [p["stage"] for p in r.progress] == [
        "queued", "lock", "backup", "editor", "saved", "processing", "sent"]
    assert seen["setup"].exe == "BRE.EXE" and seen["setup"].dos_path == "C:\\BRE_900"
    assert seen["settings"]["turns_per_day"] == 12
    assert processing.calls == [(("BRE", "900"),)]
    assert r.processing_run_id is not None
    assert "typed it all" in r.transcript
    assert Path(r.backup_path).exists()
    from backend.services.processing_service import _processing_lock
    assert not _processing_lock.locked()


@pytest.mark.asyncio
async def test_a_failed_reset_restores_the_game_byte_for_byte(game, processing):
    g = game
    make_draft(g, turns_per_day=12)
    before = tree(g.folder)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)

    def wiping_drive(setup, game_type, settings, today, log, transcript):
        # What RESET does at "reset the Game? Y", then a surprise.
        (setup.folder / "DATA" / "GAME.DAT").unlink()
        (setup.folder / "DATA" / "PLAYER.DAT").write_bytes(b"")
        (setup.folder / "inuse.flg").write_text("")
        (setup.folder / "RESET.BAT").write_text("@ECHO OFF")
        raise EditorError("Tax Rate reads '50' after the edit, not '60'")

    await perform.run_job(reset.id, g.config, drive_fn=wiping_drive, today=TODAY)
    g.db.expire_all()
    r = g.db.get(GameReset, reset.id)
    assert tree(g.folder) == before
    assert r.status == "failed" and "Tax Rate reads" in r.error and "put back" in r.error
    assert r.progress[-1]["stage"] == "restored"
    assert processing.calls == []
    draft = g.db.query(GameReset).filter_by(status="draft").one()
    assert draft.settings == r.settings


@pytest.mark.asyncio
async def test_a_failed_restore_says_to_restore_by_hand(game, processing, monkeypatch):
    g = game
    make_draft(g)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)

    def broken_restore(archive, folder):
        raise OSError("disk full")
    monkeypatch.setattr(perform, "restore", broken_restore)

    def failing_drive(*a):
        raise EditorError("timed out")

    await perform.run_job(reset.id, g.config, drive_fn=failing_drive, today=TODAY)
    g.db.expire_all()
    r = g.db.get(GameReset, reset.id)
    assert r.status == "failed" and "by hand" in r.error and r.backup_path in r.error
    assert r.progress[-1]["stage"] == "failed"


@pytest.mark.asyncio
async def test_a_backup_that_fails_leaves_the_game_alone(game, processing, monkeypatch):
    g = game
    make_draft(g)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)

    def broken_backup(*a):
        raise OSError("disk full")
    monkeypatch.setattr(perform, "backup", broken_backup)
    driven = []
    await perform.run_job(reset.id, g.config, drive_fn=lambda *a: driven.append(a),
                          today=TODAY)
    g.db.expire_all()
    r = g.db.get(GameReset, reset.id)
    assert not driven
    assert r.status == "failed" and "before the game was touched: disk full" in r.error
    assert g.db.query(GameReset).filter_by(status="draft").count() == 1
    from backend.services.processing_service import _processing_lock
    assert not _processing_lock.locked()


@pytest.mark.asyncio
async def test_a_failed_processing_run_leaves_the_reset_saved(game, processing):
    g = game
    make_draft(g)
    processing.status = "failed"
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)
    await perform.run_job(reset.id, g.config, drive_fn=lambda *a: None, today=TODAY)
    g.db.expire_all()
    r = g.db.get(GameReset, reset.id)
    assert r.status == "applied"
    assert "processing run after it failed: dosemu exited 1" in r.error
    assert r.progress[-1]["stage"] == "processing"


@pytest.mark.asyncio
async def test_the_lock_is_waited_for_then_given_up(game, processing):
    from backend.services.processing_service import _processing_lock
    g = game
    make_draft(g)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)
    _processing_lock.acquire()
    try:
        await perform.run_job(reset.id, g.config, drive_fn=lambda *a: None,
                              lock_wait=0, today=TODAY)
    finally:
        _processing_lock.release()
    g.db.expire_all()
    r = g.db.get(GameReset, reset.id)
    assert r.status == "failed" and "Nothing was changed" in r.error
    assert g.db.query(GameReset).filter_by(status="draft").count() == 1


# ── Immortal Barons ───────────────────────────────────────────────────────
# Stands in for immortal-barons -league-reset: resets the world to the date and
# writes the order and the rules as two packets, as v0.2.6 does. A `mode` file
# beside config.json makes it misbehave.
FAKE_IB = """#!{python}
import json, sys
from pathlib import Path
on, data = sys.argv[2], Path(sys.argv[4])
mode = (data / "mode").read_text() if (data / "mode").exists() else ""
if sys.argv[1] != "-league-reset" or mode == "frozen":
    print("-league-reset: the league is frozen")
    sys.exit(1)
c = json.loads((data / "config.json").read_text())
if mode == "clamp":
    c["TurnsPerDay"] = 10
c["SlappenheimerHandling"] = c.pop("SabreHandling")
for k in ("DataDir", "IBBS", "IdleTimeoutSecs", "MaxIdleWarnings"):
    c.pop(k)
(data / "world.json").write_text(json.dumps({{"StartedDate": on, "LastMaintDate": on}}))
out = Path({outbound!r})
out.mkdir(exist_ok=True)
(out / "L900-001-0041-000.brp").write_text(json.dumps({{"Reset": {{"OnDate": on}}}}))
(out / "L900-001-0042-000.brp").write_text(json.dumps({{"LeagueConfig": c}}))
print("League reset declared, starting " + on)
"""


@pytest.fixture
def ib(tmp_path, db_session, db_engine, monkeypatch):
    """The hub's IB game: config.json as v0.2.6 writes it, the Coordinator's key,
    a world, and the stand-in binary. Its outbound is outside the data dir, so
    a restore can't be what clears it."""
    from backend.services.ib_packet import config_json
    folder, outbound = tmp_path / "ib" / "900", tmp_path / "ib_outbound"
    folder.mkdir(parents=True)
    outbound.mkdir()
    (folder / "config.json").write_text(json.dumps(
        {"DataDir": str(folder), "IBBS": True, "IdleTimeoutSecs": 300,
         "MaxIdleWarnings": 3, **config_json(defaults("I")), "MoneyCapBillions": 2}, indent=2))
    (folder / "coord.key").write_text("secret")
    (folder / "world.json").write_text('{"StartedDate": "2026-09-01"}')
    binary = tmp_path / "immortal-barons"
    binary.write_text(FAKE_IB.format(python=sys.executable, outbound=str(outbound)))
    binary.chmod(0o755)
    config = {"server": {"data_dir": str(tmp_path / "data")}, "hub": {"bbs_index": "01"},
              "ib": {"900": {"binary": str(binary), "data_dir": str(folder),
                             "outbound_dir": str(outbound)}}}
    league = League(league_id="900", game_type="I", name="900I", is_active=True)
    db_session.add(league)
    db_session.commit()
    Session = sessionmaker(bind=db_engine)
    monkeypatch.setattr(perform, "get_session", lambda: Session())
    return SimpleNamespace(folder=folder, outbound=outbound, binary=binary, config=config,
                           league=league, db=db_session)


def ib_draft(g, **settings):
    s, errors = validate("I", settings, TODAY)
    assert not errors
    g.db.add(GameReset(league_id=g.league.id, status="draft", source="console", settings=s,
                       version=1, created_by="lime", updated_by="lime"))
    g.db.commit()
    return perform.start(g.db, g.league, 1, "900I", "lime", g.config, TODAY)


@pytest.mark.asyncio
async def test_an_ib_reset_writes_the_rules_and_orders_the_reset(ib, processing):
    later = (TODAY + timedelta(days=3)).isoformat()
    reset = ib_draft(ib, turns_per_day=12, start_date=later, sabre="C", sabre_dial=8,
                     min_board_version="0.2.6")
    await perform.run_job(reset.id, ib.config, today=TODAY)
    ib.db.expire_all()
    r = ib.db.get(GameReset, reset.id)
    assert r.status == "applied" and r.error is None, r.error
    assert [p["stage"] for p in r.progress] == [
        "queued", "lock", "backup", "editor", "saved", "processing", "sent"]
    c = json.loads((ib.folder / "config.json").read_text())
    assert (c["TurnsPerDay"], c["GameStartDate"], c["SabreHandling"],
            c["SabreConstantDial"]) == (12, later, 3, 8)
    # The board's own keys, and the one the form doesn't offer, are left alone.
    assert c["IdleTimeoutSecs"] == 300 and c["MoneyCapBillions"] == 2
    assert (ib.folder / "ibversion.cfg").read_text().splitlines()[-1] == "0.2.6"
    assert f"-league-reset {later}" in r.transcript
    assert sorted(p.name for p in ib.outbound.iterdir()) == [
        "L900-001-0041-000.brp", "L900-001-0042-000.brp"]
    assert processing.calls == [(("IB", "900"),)]


@pytest.mark.asyncio
async def test_an_ib_reset_with_no_start_date_starts_today(ib, processing):
    reset = ib_draft(ib)
    await perform.run_job(reset.id, ib.config, today=TODAY)
    ib.db.expire_all()
    r = ib.db.get(GameReset, reset.id)
    assert r.status == "applied", r.error
    assert json.loads((ib.folder / "world.json").read_text())["StartedDate"] == "2026-10-15"
    assert json.loads((ib.folder / "config.json").read_text())["GameStartDate"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,why", [("frozen", "didn't reset"),
                                      ("clamp", "turns_per_day 10 not 12")])
async def test_a_failed_ib_reset_puts_everything_back(ib, processing, mode, why):
    (ib.folder / "mode").write_text(mode)
    before = tree(ib.folder)
    reset = ib_draft(ib, turns_per_day=12)
    await perform.run_job(reset.id, ib.config, today=TODAY)
    ib.db.expire_all()
    r = ib.db.get(GameReset, reset.id)
    assert r.status == "failed" and why in r.error and "put back" in r.error
    assert tree(ib.folder) == before
    assert list(ib.outbound.iterdir()) == []    # nothing reaches the members
    assert processing.calls == []
    assert ib.db.query(GameReset).filter_by(status="draft").one().settings == r.settings


def test_an_ib_reset_needs_the_game_and_the_coordinators_key(ib):
    (ib.folder / "coord.key").unlink()
    with pytest.raises(perform.ResetRefused, match="coord.key"):
        ib_draft(ib)
    (ib.folder / "coord.key").write_text("secret")
    ib.binary.unlink()
    with pytest.raises(perform.ResetRefused, match="isn't there"):
        perform.start(ib.db, ib.league, 1, "900I", "lime", ib.config, TODAY)
    with pytest.raises(perform.ResetRefused, match=r"\[ib.900\]"):
        perform.start(ib.db, ib.league, 1, "900I", "lime", {"ib": {}}, TODAY)


def test_backups_round_trip_and_prune(tmp_path):
    folder = tmp_path / "game"
    (folder / "DATA").mkdir(parents=True)
    (folder / "DATA" / "GAME.DAT").write_bytes(bytes(range(256)) * 10)
    (folder / "bbs.cfg").write_bytes(b"x\r\n")
    before = tree(folder)
    into = tmp_path / "backups"
    archive = perform.backup(folder, into, "20261015-120000")
    (folder / "DATA" / "GAME.DAT").unlink()
    (folder / "new.txt").write_text("junk")
    perform.restore(archive, folder)
    assert tree(folder) == before
    for i in range(7):
        perform.backup(folder, into, f"2026101{i}-000000")
    perform.prune(into)
    assert len(list(into.glob("*.tar.gz"))) == perform.KEEP_BACKUPS
    assert not list(into.glob("*.partial"))


def test_recover_restores_a_reset_stopped_in_the_editor(game):
    g = game
    make_draft(g)
    before = tree(g.folder)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)
    archive = perform.backup(g.folder, g.folder.parent / "bk", "x")
    reset.backup_path = str(archive)
    reset.progress = reset.progress + [{"stage": "lock", "at": "x"},
                                       {"stage": "backup", "at": "x"},
                                       {"stage": "editor", "at": "x"}]
    g.db.commit()
    (g.folder / "DATA" / "GAME.DAT").unlink()
    perform.recover(g.db, g.config)
    assert tree(g.folder) == before
    assert reset.status == "failed" and "put back" in reset.error
    assert reset.progress[-1]["stage"] == "interrupted"


def test_recover_keeps_a_reset_stopped_after_the_save(game):
    g = game
    make_draft(g)
    reset = perform.start(g.db, g.league, 3, "900B", "mantis", g.config, TODAY)
    reset.progress = reset.progress + [{"stage": "saved", "at": "x"}]
    g.db.commit()
    perform.recover(g.db, g.config)
    assert reset.status == "applied" and "next run" in reset.error


# ── history ───────────────────────────────────────────────────────────────
def settings_record(started, protection=20, indiv=1, lost=7):
    return bre_packet.LeagueSettings(started, protection, indiv, lost)


def packet(db, league, name, at):
    p = Packet(filename=name, league_id=league.id, source_bbs_index="01",
               dest_bbs_index="02", sequence_number=1, file_size=1, uploaded_at=at)
    db.add(p)
    db.commit()
    return p


def test_a_game_start_confirms_the_console_reset_before_it(db_session, sample_league):
    db, league = db_session, sample_league
    s, _ = validate("B", {**defaults("B"), "lost_attack_days": 3}, TODAY)
    applied = datetime(2026, 10, 15, 9, 0)
    console = GameReset(league_id=league.id, status="applied", source="console",
                        settings=s, version=1, applied_at=applied)
    db.add(console)
    db.commit()
    start = datetime(2026, 10, 15, 9, 0, 5)
    for i in range(2):     # every member's packet carries the same Game Start
        history.record_game_start(db, league, packet(db, league, f"555B01{i + 2:02d}.001",
                                                     applied + timedelta(minutes=1)),
                                  settings_record(start, lost=3))
    db.commit()
    assert db.query(GameReset).count() == 1
    assert console.game_start == start and console.sent_config["mismatches"] == []


def test_a_game_start_in_a_reused_row_is_dated_by_its_collection(db_session, sample_league):
    """013B on dev, 15 Oct: the Game Start went out in rows first written on
    11 Jan, whose uploaded_at said January, so it confirmed nothing."""
    db, league = db_session, sample_league
    s, _ = validate("B", defaults("B"), TODAY)
    applied = datetime(2026, 10, 15, 22, 29, 35)
    console = GameReset(league_id=league.id, status="applied", source="console",
                        settings=s, version=1, applied_at=applied)
    db.add(console)
    db.commit()
    row = packet(db, league, "555B0103.001", datetime(2026, 1, 11, 3, 21))
    row.processed_at = applied + timedelta(seconds=1)
    history.record_game_start(db, league, row, settings_record(applied))
    assert console.game_start == applied
    assert db.query(GameReset).filter_by(source="observed").count() == 0


def test_a_disagreeing_game_start_is_a_mismatch(db_session, sample_league):
    db, league = db_session, sample_league
    s, _ = validate("B", defaults("B"), TODAY)
    console = GameReset(league_id=league.id, status="applied", source="console",
                        settings=s, version=1, applied_at=datetime(2026, 10, 15, 9))
    db.add(console)
    db.commit()
    history.record_game_start(db, league, packet(db, league, "555B0102.001",
                                                 datetime(2026, 10, 15, 9, 1)),
                              settings_record(datetime(2026, 10, 15, 9), protection=0))
    assert console.sent_config["mismatches"] == ["protection_turns"]


def test_every_decoded_setting_is_checked_not_just_the_first_three(db_session, sample_league):
    """A real Game Start from the rig (capture c: Trade Costs High, among
    others) against a draft that says Medium."""
    import json
    from pathlib import Path
    db, league = db_session, sample_league
    capture = json.loads((Path(__file__).parent / "fixtures" /
                          "bre_configupdates.json").read_text())["c"]
    draft = {**capture["settings"], "trade_costs": "M"}
    console = GameReset(league_id=league.id, status="applied", source="console",
                        settings=draft, version=1, applied_at=datetime(2026, 10, 16, 0, 0))
    db.add(console)
    db.commit()
    rec = bre_packet.Record(bre_packet.GAME_START, 1, 2, True, bytes.fromhex(capture["game_start"]))
    history.record_game_start(db, league, packet(db, league, "555B0102.001",
                                                 datetime(2026, 10, 16, 0, 1)),
                              bre_packet.league_settings(rec))
    assert console.sent_config["mismatches"] == ["trade_costs"]
    assert console.sent_config["gooie"] is False


def test_other_game_starts_are_observed_and_history_runs_newest_first(db_session, sample_league):
    db, league = db_session, sample_league
    old = datetime(2026, 1, 11, 3, 17, 50)
    history.record_game_start(db, league, packet(db, league, "555B0102.001", old),
                              settings_record(old, protection=0))
    s, _ = validate("B", defaults("B"), TODAY)
    new = GameReset(league_id=league.id, status="applied", source="console", settings=s,
                    version=1, applied_at=datetime(2026, 7, 8), game_start=datetime(2026, 7, 8))
    failed = GameReset(league_id=league.id, status="failed", source="console", settings=s,
                       version=1, applied_at=datetime(2026, 7, 7))
    draft = GameReset(league_id=league.id, status="draft", source="console", settings=s,
                      version=1)
    db.add_all([new, failed, draft])
    db.commit()
    rows = history.history(db, league)
    assert [(r.source, r.status) for r, _ in rows] == [
        ("console", "applied"), ("console", "failed"), ("observed", "applied")]
    assert rows[0][1] is None                        # the game being played
    assert rows[2][1] == datetime(2026, 7, 8)        # ended when the next began
    assert rows[2][0].settings == {"protection_turns": 0, "indiv_attacks": 1,
                                   "lost_attack_days": 7}


def test_game_start_records_decode_like_a_configupdate():
    import struct
    payload = struct.pack("<17H", 2026, 10, 15, 9, 0, 5, 0, 0, 20, 0, 0, 0, 1, 0, 0, 0, 7)
    rec = bre_packet.Record(bre_packet.GAME_START, 1, 2, True, payload + bytes(206))
    s = bre_packet.league_settings(rec)
    assert s.game_started_at == datetime(2026, 10, 15, 9, 0, 5)
    assert (s.protection_turns, s.indiv_attacks_per_day, s.lost_attack_days) == (20, 1, 7)


# ── the API ───────────────────────────────────────────────────────────────
from tests.test_game_reset import BASE, api  # noqa: E402,F401  the fixture


def test_perform_over_the_api(api, monkeypatch, tmp_path):
    client, db, lid = api
    folder = tmp_path / "bre_015"
    folder.mkdir()
    (folder / "BRE.EXE").write_bytes(b"MZ")
    config = {"server": {"data_dir": str(tmp_path)}, "dosemu": {"015": {"bre": {
        "game_folder": str(folder), "game_dos_path": "C:\\BRE_015",
        "processing_command": "BRE.EXE PLANETARY"}}}}
    import backend.api.management.game_reset as module
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(_raw=config))
    scheduled = []
    monkeypatch.setattr(perform, "schedule", lambda rid, cfg: scheduled.append(rid))

    r = client.post(f"{BASE}/{lid}/reset/perform", json={"version": 1, "confirm": "015B"})
    assert r.status_code == 404
    saved = client.put(f"{BASE}/{lid}/reset/draft",
                       json={"settings": {"turns_per_day": 0}}).json()["draft"]
    r = client.post(f"{BASE}/{lid}/reset/perform", json={"version": 1, "confirm": "015F"})
    assert r.status_code == 422 and "015B" in r.json()["detail"]
    r = client.post(f"{BASE}/{lid}/reset/perform",
                    json={"version": saved["version"], "confirm": "015b"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "applying" and scheduled == [body["id"]]
    assert body["warnings"][0]["field"] == "turns_per_day"
    assert [s["stage"] for s in body["progress"]] == ["queued"]

    form = client.get(f"{BASE}/{lid}/reset").json()
    assert form["draft"] is None and form["under_way"]["id"] == body["id"]
    r = client.post(f"{BASE}/{lid}/reset/perform", json={"version": 1, "confirm": "015B"})
    assert r.status_code == 404           # the draft became the reset

    rows = client.get(f"{BASE}/{lid}/resets").json()
    assert [x["id"] for x in rows] == [body["id"]]
    one = client.get(f"{BASE}/{lid}/resets/{body['id']}").json()
    assert one["applied_by"] == "mantis" and one["members"] == []
    assert client.get(f"{BASE}/{lid}/resets/999").status_code == 404
    from backend.models.database import AuditEvent
    assert db.query(AuditEvent).filter_by(action="game_reset.performed").count() == 1
