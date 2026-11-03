"""New League, on the real games: Set Up from a golden image, the first reset
with its canary run, and the mistakes that must be caught.

The happy path sets up a league beside leagues already running and measures
that it did not touch them, rather than assuming it. The mistakes are made on
purpose, each in the place an admin could make it, and each must leave the new
league unable to be enabled and the other leagues untouched.

The new leagues' games are made where the hub keeps its games (drive_c/leagues,
B902 and so on) and removed again after each test.

The rig's own leagues (900B and the rest) are not made this way, and should not
be: their fixtures are virgin games on day one, and a first reset's canary run
is the hub game's day-one maintenance. Made through New League, every scenario's
first-day expectations would move, and a setup that broke would take the whole
suite down instead of failing here. The round below is what proves a league
made this way plays like theirs.
"""
import getpass
import hashlib
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from rig import node as node_rig
from rig.layout import HUB_GAMES, HUB_INDEX, LEAGUES, LOGS, NODES, VENV_PYTHON, League

pytestmark = pytest.mark.asyncio

HUB = NODES[HUB_INDEX]
ROOT = HUB.home / ".dosemu" / "drive_c" / HUB_GAMES
NEW = ("B902", "F903", "I904")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LIVE = Path(__file__).resolve().parent
# A league the hub sets up, with members of its own: boards 02 and 03.
ROUND = League("902", "B", (HUB_INDEX, 2, 3))
MESSAGE = "NEW LEAGUE ROUND ONE"


def tree(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def fresh(hub, monkeypatch):
    """The new leagues' folders gone before and after; the jobs on the hub's
    database."""
    from sqlalchemy.orm import sessionmaker
    from backend.services import league_setup
    from backend.services.game_reset import perform

    def clear():
        for name in NEW:
            for path in (ROOT / name, ROOT / f".{name}.setup"):
                shutil.rmtree(path, ignore_errors=True)

    clear()
    Session = sessionmaker(bind=hub._engine)
    monkeypatch.setattr(league_setup, "get_session", lambda: Session())
    monkeypatch.setattr(perform, "get_session", lambda: Session())
    yield
    clear()


async def set_up(hub, number: str, game: str):
    from backend.models.database import GameReset, League
    from backend.services import league_setup

    order = league_setup.start(hub.db, hub.config, number=number, game_type=game,
                               name=f"New {number}{game}", hub_address=None, image_id=None,
                               username="rig")
    hub.db.commit()
    await league_setup.run_job(order.id, hub.config)
    hub.db.expire_all()
    order = hub.db.get(GameReset, order.id)
    league = hub.db.get(League, order.league_id)
    assert league.install.state == "installed", f"{order.error}\n{order.transcript}"
    return league


async def first_reset(hub, league, changes: dict):
    from backend.models.database import GameReset
    from backend.services.game_reset import perform
    from backend.services.game_reset.drafts import validate
    from backend.services.game_reset.fields import defaults

    settings, errors = validate(league.game_type, {**defaults(league.game_type), **changes},
                                date.today())
    assert not errors, errors
    hub.db.add(GameReset(league_id=league.id, status="draft", source="console",
                         settings=settings, version=1, created_by="rig"))
    hub.db.commit()
    reset = perform.start(hub.db, league, 1, league.full_id, "rig", hub.config, date.today())
    await perform.run_job(reset.id, hub.config)
    hub.db.expire_all()
    return hub.db.get(GameReset, reset.id)


def hold_packet(hub, league_id: str, fixture: str):
    """A real packet of a running league, as the hub keeps the ones it routed:
    what a canary for that league is made from."""
    from backend.models.database import League, Packet
    league = LEAGUES[league_id]
    row = hub.db.query(League).filter_by(league_id=league.number, game_type=league.game).one()
    data = (FIXTURES / fixture).read_bytes()
    name = fixture.split("_")[-1].upper()
    hub.db.add(Packet(filename=name, league_id=row.id, source_bbs_index=name[4:6],
                      dest_bbs_index=name[6:8], sequence_number=int(name[-3:]),
                      file_size=len(data), file_data=data,
                      processed_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    hub.db.commit()
    return name


def hub_install(league_id: str) -> Path:
    return LEAGUES[league_id].install_path(HUB)


# ── the happy path ────────────────────────────────────────────────────────
async def test_a_new_bre_league_beside_two_running_is_set_up_and_made_ready(
        pristine, hub, fresh):
    from backend.models.database import Packet
    from backend.api.management.leagues import _check_can_enable
    from backend.services.processing_service import ProcessingService

    for league_id in ("900B", "901B"):
        pristine(league_id, nodes=[HUB_INDEX])
    hub.seed(["900B", "901B"])
    canary = hold_packet(hub, "901B", "bre_packets/attacks_901b0201.001")
    before = {lid: tree(hub_install(lid)) for lid in ("900B", "901B")}

    league = await set_up(hub, "902", "B")
    folder = ROOT / "B902"
    assert not (folder / "DATA" / "GAME.DAT").exists(), "setup runs no game"
    with pytest.raises(Exception, match="no reset yet"):
        _check_can_enable(league)

    # Installed: a packet for it waits, and nothing runs in it.
    hub.db.add(Packet(filename="902B0201.001", league_id=league.id, source_bbs_index="02",
                      dest_bbs_index="01", sequence_number=1, file_size=1))
    hub.db.commit()
    assert await ProcessingService(hub.db, hub.config).process_batch() is None
    assert not (folder / "DATA" / "GAME.DAT").exists()
    hub.db.query(Packet).filter_by(filename="902B0201.001").delete()
    hub.db.commit()

    r = await first_reset(hub, league, {"turns_per_day": 12})
    assert r.status == "applied", f"{r.error}\n{r.transcript}"
    assert [p["stage"] for p in r.progress][-1] == "ready", r.transcript
    assert f"canary for 901B: {canary}" in r.transcript
    assert "canary run passed" in r.transcript
    hub.db.expire_all()
    league = hub.db.get(type(league), league.id)
    assert league.install.state == "ready" and league.install.preflight_ok
    assert any(p.name.lower() == "game.dat" for p in (folder / "DATA").iterdir())
    assert not (folder / "INBOUND" / canary).exists(), "the canary is removed"
    _check_can_enable(league)

    for lid, files in before.items():
        assert tree(hub_install(lid)) == files, f"{lid}'s game changed"


async def test_a_new_falcons_eye_league_is_set_up_and_made_ready(pristine, hub, fresh):
    pristine("900F", nodes=[HUB_INDEX])
    hub.seed(["900F"])
    canary = hold_packet(hub, "900F", "fe_packets/day1_900f0201.001")
    before = tree(hub_install("900F"))

    league = await set_up(hub, "903", "F")
    r = await first_reset(hub, league, {"turns_per_day": 10})
    assert r.status == "applied", f"{r.error}\n{r.transcript}"
    assert [p["stage"] for p in r.progress][-1] == "ready", r.transcript
    assert f"canary for 900F: {canary}" in r.transcript
    assert tree(hub_install("900F")) == before


async def test_a_new_immortal_barons_league_is_set_up_and_made_ready(hub, fresh):
    from backend.services.game_images import import_ib_image
    from rig.ib_league import builds

    release = builds().get("release")
    if not release:
        pytest.skip("no IB release build on the rig")
    import_ib_image(hub.db, hub.config, Path(release["binary"]), release["ref"])
    hub.db.commit()

    league = await set_up(hub, "904", "I")
    folder = ROOT / "I904"
    assert (folder / "coord.pub").is_file() and league.install.hub_board_key
    r = await first_reset(hub, league, {"turns_per_day": 15})
    assert r.status == "applied", f"{r.error}\n{r.transcript}"
    assert [p["stage"] for p in r.progress][-1] == "ready", r.transcript
    assert "no other league of this game, so no canary" in r.transcript


# ── mistakes made on purpose ──────────────────────────────────────────────
def _bbs_cfg(folder: Path, **lines):
    """Rewrite bbs.cfg lines, numbered from 1."""
    path = folder / "bbs.cfg"
    rows = path.read_bytes().split(b"\r\n")
    for n, value in lines.items():
        rows[int(n[1:]) - 1] = value.encode()
    path.write_bytes(b"\r\n".join(rows))


async def test_a_wrong_league_number_or_inbound_fails_preflight(pristine, hub, fresh):
    from backend.services import preflight
    from backend.api.management.leagues import _check_can_enable

    pristine("900B", nodes=[HUB_INDEX])
    hub.seed(["900B"])
    league = await set_up(hub, "902", "B")
    folder = ROOT / "B902"
    good = (folder / "bbs.cfg").read_bytes()

    _bbs_cfg(folder, l6="900")
    problems = preflight.run(hub.db, hub.config, [league])["902B"]
    assert any("line 6 says league '900'" in p for p in problems), problems

    (folder / "bbs.cfg").write_bytes(good)
    _bbs_cfg(folder, l4=f"{LEAGUES['900B'].dos_path(HUB)}\\INBOUND")
    problems = preflight.run(hub.db, hub.config, [league])["902B"]
    assert any("line 4" in p and "not this league's INBOUND" in p for p in problems), problems

    hub.db.expire_all()
    league.install.state = "ready"   # even ready, a failed preflight blocks it
    hub.db.commit()
    with pytest.raises(Exception, match="passes preflight"):
        _check_can_enable(league)


async def test_the_canary_catches_a_game_that_believes_it_is_another_league(
        pristine, hub, fresh):
    """The case preflight exists for, run anyway: 902B's bbs.cfg and nodes file
    copied from 901B's. Its first run takes 901B's packet, which the canary
    shows."""
    from backend.services import league_setup
    from backend.services.processing_service import ProcessingService

    pristine("901B", nodes=[HUB_INDEX])
    hub.seed(["901B"])
    canary = hold_packet(hub, "901B", "bre_packets/attacks_901b0201.001")
    league = await set_up(hub, "902", "B")
    folder, theirs = ROOT / "B902", hub_install("901B")
    shutil.copyfile(theirs / "brnodes.dat" if (theirs / "brnodes.dat").exists()
                    else theirs / "BRNODES.DAT", folder / "BRNODES.DAT")
    _bbs_cfg(folder, l3=LEAGUES["901B"].fido_for(HUB_INDEX), l6="901")

    # The first reset, done by hand so the misconfigured game gets its run.
    from backend.services.golden import RECIPES, Dosemu, reset
    from rig.layout import DOSEMU_CONF
    reset(folder, RECIPES["B"], Dosemu(drive_c=ROOT.parent, conf=DOSEMU_CONF,
                                       log_dir=hub.data_dir / "logs"),
          hub.data_dir / "logs" / "canary_reset.log")

    lines = []
    placed = league_setup.place_canaries(hub.db, hub.config, league, lines.append)
    assert canary in placed["canaries"]
    run = await ProcessingService(hub.db, hub.config).process_batch(
        also=(("BRE", "902"),), first_reset=True)
    problems = league_setup.check_canaries(hub.config, league, placed, run, lines.append)
    assert any(f"the canary {canary} was" in p for p in problems), "\n".join(lines)


# ── a round, with members ─────────────────────────────────────────────────
def _as(node, *args) -> list:
    return list(args) if node.user == getpass.getuser() else ["sudo", "-u", node.user, "--", *args]


@pytest.fixture
def members(fresh):
    """ROUND's member installs (b902n02, b902n03) gone before and after."""
    def clear():
        for index in ROUND.members[1:]:
            node = NODES[index]
            subprocess.run(_as(node, "rm", "-rf", str(ROUND.install_path(node))), check=True)
    clear()
    yield
    clear()


def install_member(index: int, nodes_file: Path):
    """What a sysop joining does: install the game from the image, stamp it as
    their board, and take the league's nodelist from the hub."""
    node = NODES[index]
    r = subprocess.run(
        _as(node, str(VENV_PYTHON), "-m", "rig.provision", ROUND.league_id, str(index),
            "--members", ",".join(str(i) for i in ROUND.members), "--nodes-file", str(nodes_file)),
        cwd=str(LIVE), capture_output=True, text=True)
    assert r.returncode == 0, f"installing board {index:02d}:\n{r.stdout}\n{r.stderr}"


def send(hub, index: int):
    """The board's outbound, uploaded to the hub."""
    sent = []
    for name, payload in node_rig.take_outbound(ROUND, NODES[index]):
        if name.lower().startswith(ROUND.league_id.lower()):
            hub.deliver(name, payload)
            sent.append(name)
    return sent


def download(hub, index: int):
    """The hub's packets for the board, into its INBOUND."""
    got = []
    for name in hub.spool("outbound"):
        if name.upper().startswith(f"{ROUND.league_id}01{index:02X}"):
            node_rig.give_inbound(ROUND, NODES[index], name,
                                  (hub.data_dir / "packets" / "outbound" / name).read_bytes())
            got.append(name)
    return got


async def test_a_new_league_plays_a_round_with_its_members(pristine, hub, members):
    """New League end to end, as an admin and two sysops would: set up, first
    reset, enable, members join and install from the hub's nodelist. Then node
    02 plays a turn and writes, and node 03 sees both, through the new hub game.
    """
    from rig import player
    from backend.api.management.leagues import _check_can_enable
    from backend.services.applications import regenerate_nodelists
    from backend.services.nodelist_generator import find_nodelist

    pristine("900B", nodes=[HUB_INDEX])
    hub.seed(["900B"])
    neighbour = tree(hub_install("900B"))

    # The admin: Set Up, the first reset, Enable, the members, the nodelist.
    league = await set_up(hub, ROUND.number, ROUND.game)
    r = await first_reset(hub, league, {})
    assert r.status == "applied", f"{r.error}\n{r.transcript}"
    hub.db.expire_all()
    league = hub.db.get(type(league), league.id)
    _check_can_enable(league)
    league.is_active = True
    hub.join(league, ROUND)
    assert regenerate_nodelists(hub.db, [league.id], hub.config) == []
    nodelist = find_nodelist(hub.data_dir, league)
    assert nodelist.read_bytes().startswith(b"1 HOST 2 3\r\n"), nodelist.read_bytes()[:80]

    # The sysops: each installs the game and takes the hub's nodelist. Their
    # node users read it from the rig's shared log drop, not the hub's data dir.
    shared = LOGS / "new_league_902b_nodes.dat"
    shutil.copyfile(nodelist, shared)
    shared.chmod(0o644)
    for index in ROUND.members[1:]:
        install_member(index, shared)

    # Node 02 plays a turn and writes; the hub's new game takes it in.
    played = player.visit(ROUND, NODES[2], realm="Node Two", turns=1,
                          ip_messages=[MESSAGE], tag="newleague")
    assert played["turns_played"] == 1 and played["rc"] == 0
    assert f"{ROUND.league_id.lower()}0201.001" in send(hub, 2)
    await hub.process()
    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0, "the new game left node 02's packet in its INBOUND"
    assert hub.transcript(run).ingested_from(2), hub.transcript(run).items

    # Node 03 downloads what the hub has for it, runs its maintenance, and
    # sees node 02 as a player would.
    assert download(hub, 3), f"nothing for node 03; hub outbound {hub.spool('outbound')}"
    node_rig.run(ROUND, NODES[3], "PLANETARY", tag="newleague3")
    seen = player.visit(ROUND, NODES[3], realm="Node Three", turns=1,
                        read_ip_scores=True, tag="newleague3")
    assert "Node Two" in seen["ip_scores"], seen["ip_scores"][-2000:]
    assert str(played["status_after"]["score"]) in seen["ip_scores"].replace(",", ""), (
        f"node 02 scored {played['status_after']['score']}; node 03 shows\n"
        f"{seen['ip_scores'][-2000:]}")
    assert MESSAGE in seen["turn_text"], seen["turn_text"][:3000]

    # And back: node 03's traffic goes through the hub to node 02.
    assert send(hub, 3), "node 03 sent nothing"
    queued = set(hub.spool("outbound"))
    await hub.process()
    run = hub.last_run()
    assert run.status == "completed" and run.packets_unconsumed == 0, run.error_message
    assert hub.transcript(run).ingested_from(3)
    new = [n for n in hub.spool("outbound") if n not in queued]
    assert any(n.upper().startswith("902B0102") for n in new), (
        f"nothing new for node 02; hub outbound {hub.spool('outbound')}")

    assert tree(hub_install("900B")) == neighbour, "900B's game changed"
