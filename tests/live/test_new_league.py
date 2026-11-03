"""New League, on the real games: Set Up from a golden image, the first reset
with its canary run, and the mistakes that must be caught.

The happy path sets up a league beside leagues already running and measures
that it did not touch them, rather than assuming it. The mistakes are made on
purpose, each in the place an admin could make it, and each must leave the new
league unable to be enabled and the other leagues untouched.

The new leagues' games are made where the hub keeps its games (drive_c/leagues,
B902 and so on) and removed again after each test.
"""
import hashlib
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from rig.layout import HUB_GAMES, HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

HUB = NODES[HUB_INDEX]
ROOT = HUB.home / ".dosemu" / "drive_c" / HUB_GAMES
NEW = ("B902", "F903", "I904")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


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
    r = await first_reset(hub, league, {"turns_per_day": 23})
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
