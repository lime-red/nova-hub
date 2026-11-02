"""Where each league's game is (league_games), what may run it, and the checks
that keep one league's game from touching another league's packets."""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from backend.models.database import GameImage, League, LeagueGame, Packet, ProcessingRun
from backend.services import preflight
from backend.services.league_games import (
    dos_path,
    games_config,
    host_path,
    league_game,
    legacy_sections,
)
from backend.services.processing_service import ProcessingService
from tests.conftest import install_game


def _bbs_cfg(folder: Path, number: str, inbound: str, address: str = "13:10/1"):
    lines = ["Nova Admin", "Nova Hub", address, inbound, inbound.rsplit("\\", 1)[0] + "\\NETMAIL",
             number, "NONE"]
    (folder / "bbs.cfg").write_bytes("".join(f"{l}\r\n" for l in lines).encode())


@pytest.fixture
def hub(db_session, tmp_path):
    """Two BRE leagues and an IB league, each with its game, all well made."""
    config = {"server": {"data_dir": str(tmp_path / "data")}, "hub": {"bbs_index": "01"},
              "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 30}}
    drive_c = tmp_path / "drive_c"
    leagues = {}
    for number, letter in (("900", "B"), ("901", "B"), ("902", "I")):
        lg = League(league_id=number, game_type=letter, name=f"{number}{letter}",
                    is_active=True, hub_fidonet_address="13:10/1")
        db_session.add(lg)
        db_session.commit()
        leagues[lg.full_id] = lg
    binary = tmp_path / "immortal-barons"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    for full_id, lg in leagues.items():
        folder = install_game(db_session, config, lg, drive_c,
                              binary=binary if lg.game_type == "I" else None)
        if lg.game_type == "B":
            _bbs_cfg(folder, lg.league_id, f"C:\\LEAGUES\\{folder.name}\\INBOUND")
            (folder / "BRNODES.DAT").write_bytes(b"1 HOST\r\nNova Hub\r\n13:10/1\r\n\r\n\r\n\r\n\r\n")
        else:
            (folder / "bbs.cfg").write_text(f"BoardID Nova Hub\nLeagueNumber {int(lg.league_id)}\n"
                                            f"GameInbound inbound\nGameOutbound outbound\n")
    return config, leagues, drive_c


# ── where a league's game is ──────────────────────────────────────────────

def test_a_dos_game_is_its_folder_and_the_games_own_commands(hub):
    config, leagues, drive_c = hub
    g = league_game(config, leagues["900B"])
    folder = drive_c / "leagues" / "B900"
    assert (g.folder, g.inbound, g.outbound, g.scores) == (
        folder, folder / "INBOUND", folder / "OUTBOUND", folder / "BULLETIN")
    assert g.dos_path == "C:\\LEAGUES\\B900"
    assert g.command("processing") == "BRE.EXE PLANETARY /DETAILED"
    assert g.completion_marker == "Planetary Maintenance Complete"
    assert g.runnable


def test_folders_are_found_as_dos_finds_them_ignoring_case(hub):
    config, leagues, drive_c = hub
    folder = drive_c / "leagues" / "B900"
    (folder / "INBOUND").rename(folder / "inbound")
    assert league_game(config, leagues["900B"]).inbound == folder / "inbound"
    assert host_path(games_config(config), "c:\\leagues\\b900\\INBOUND") == folder / "inbound"


def test_an_ib_game_runs_its_image_in_its_folder_with_bbs_cfgs_folders(hub, tmp_path):
    config, leagues, drive_c = hub
    folder = drive_c / "leagues" / "I902"
    elsewhere = tmp_path / "ib_out"
    (folder / "bbs.cfg").write_text(f"LeagueNumber 902\nGameOutbound {elsewhere}\n")
    g = league_game(config, leagues["902I"])
    assert (g.inbound, g.outbound) == (folder / "inbound", elsewhere)
    assert g.binary == Path(config["games"]["images"] if "images" in config["games"]
                            else tmp_path / "data" / "images") / "ib-test-902I" / "immortal-barons"
    assert g.ib_args == ["-maint", "-data", str(folder)]


def test_no_row_no_game(hub, db_session):
    config, _, _ = hub
    lone = League(league_id="903", game_type="F", name="903F", is_active=True)
    db_session.add(lone)
    db_session.commit()
    assert league_game(config, lone) is None


def test_a_dos_path_must_be_inside_drive_c_and_8_3(hub, tmp_path):
    config, _, drive_c = hub
    cfg = games_config(config)
    with pytest.raises(ValueError, match="not inside"):
        dos_path(cfg, tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="8.3"):
        dos_path(cfg, drive_c / "leagues" / "bre_900_node02")


def test_the_old_league_sections_are_found_to_be_refused():
    config = {"dosemu": {"dosemu_path": "/usr/bin/dosemu", "015": {"bre": {}, "fe": {}}},
              "ib": {"015": {"binary": "x"}}}
    assert legacy_sections(config) == ["[dosemu.015.bre]", "[dosemu.015.fe]", "[ib.015]"]
    assert legacy_sections({"dosemu": {"timeout": 1}}) == []


def test_the_validator_refuses_a_league_section(tmp_path, db_session):
    import toml
    from backend.services.validator import HubValidator
    path = tmp_path / "config.toml"
    path.write_text(toml.dumps({"dosemu": {"dosemu_path": "x", "015": {"bre": {}}},
                                "games": {"root": str(tmp_path)}}))
    v = HubValidator(str(path), db_session)
    assert not v.validate()
    assert any("[dosemu.015.bre]" in e.message for e in v.errors)


# ── what may run ──────────────────────────────────────────────────────────

def _waiting(db, league, name="900B0201.001"):
    db.add(Packet(filename=name, league_id=league.id, source_bbs_index="02",
                  dest_bbs_index="01", sequence_number=1, file_size=1,
                  uploaded_at=datetime.now()))
    db.commit()


def _service(db, config, monkeypatch, ran):
    service = ProcessingService(db, config)

    async def group(game_type, packets, run_id, league_number=None):
        ran.append((game_type, league_number or packets[0].league_id))
        return {"status": "success", "output": ""}

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(service, "process_game_batch", group)
    monkeypatch.setattr(service, "scan_outbound_folders", nothing)
    return service


@pytest.mark.parametrize("how", ["disabled", "installed", "no game"])
def test_a_game_that_is_not_ready_and_enabled_does_not_run_and_its_packets_wait(
        hub, db_session, monkeypatch, how):
    config, leagues, _ = hub
    lg = leagues["900B"]
    if how == "disabled":
        lg.is_active = False
    elif how == "installed":
        lg.install.state = "installed"
    else:
        db_session.delete(lg.install)
    db_session.commit()
    db_session.refresh(lg)
    _waiting(db_session, lg)
    ran = []
    assert asyncio.run(_service(db_session, config, monkeypatch, ran).process_batch()) is None
    assert ran == []
    assert db_session.query(Packet).one().processed_at is None


def test_a_reset_runs_a_ready_game_in_a_league_not_yet_enabled(hub, db_session, monkeypatch):
    config, leagues, _ = hub
    leagues["900B"].is_active = False
    leagues["901B"].install.state = "installed"
    db_session.commit()
    ran = []
    service = _service(db_session, config, monkeypatch, ran)
    asyncio.run(service.process_batch(also=(("BRE", "900"), ("BRE", "901"))))
    assert ran == [("BRE", "900")]


# ── the run guard ─────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures" / "bre_packets"


def test_another_leagues_packet_in_a_games_outbound_is_quarantined_not_routed(
        hub, db_session, monkeypatch, tmp_path):
    config, leagues, drive_c = hub
    outbound = drive_c / "leagues" / "B900" / "OUTBOUND"
    payload = (FIXTURES / "results_901b0102.002").read_bytes()
    (outbound / "900B0102.001").write_bytes(payload)       # its own
    (outbound / "901B0102.001").write_bytes(payload)       # 901B's
    (outbound / "555B0102.001").write_bytes(payload)       # a league the hub doesn't host
    (outbound / "FENODES.900").write_bytes(b"1\r\n")        # another game's nodelist
    sent = []

    async def dispatch(event, subject, details, cfg):
        sent.append((event, details))

    import backend.services.alert_service as alerts
    monkeypatch.setattr(alerts, "dispatch_event", dispatch)
    leagues_before = db_session.query(League).count()

    service = ProcessingService(db_session, config)
    run = ProcessingRun(status="running")
    db_session.add(run)
    db_session.commit()
    asyncio.run(service.collect_outbound_packets(leagues["900B"], run.id, outbound))

    assert [p.filename for p in db_session.query(Packet)] == ["900B0102.001"]
    assert db_session.query(League).count() == leagues_before, "a league made from a filename"
    assert list(outbound.iterdir()) == []
    quarantine = tmp_path / "data" / "quarantine" / str(run.id)
    assert sorted(p.name for p in quarantine.iterdir()) == [
        "555B0102.001", "901B0102.001", "FENODES.900"]
    assert {q["file"] for q in service.quarantined} == {
        "555B0102.001", "901B0102.001", "FENODES.900"}

    asyncio.run(service.alert_quarantined())
    assert sent and sent[0][0] == "quarantined" and "900B" in sent[0][1]["leagues"]
    assert service.quarantined == []


def test_a_run_that_quarantined_something_is_failed_and_says_so(hub, db_session, monkeypatch):
    config, leagues, drive_c = hub
    _waiting(db_session, leagues["900B"])
    service = ProcessingService(db_session, config)

    async def group(game_type, packets, run_id, league_number=None):
        for p in packets:
            p.processed_at = datetime.now()
        out = drive_c / "leagues" / "B900" / "OUTBOUND"
        (out / "901B0102.001").write_bytes(b"x")
        await service.collect_outbound_packets(leagues["900B"], run_id, out)
        return {"status": "success", "output": ""}

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(service, "process_game_batch", group)
    monkeypatch.setattr(service, "scan_outbound_folders", nothing)
    run = asyncio.run(service.process_batch())
    assert (run.status, run.packets_quarantined) == ("failed", 1)
    assert "901B0102.001 from 900B" in run.error_message


def test_an_ib_game_writing_another_leagues_packet_is_quarantined(hub, db_session):
    from backend.services.providers import provider_for
    config, leagues, drive_c = hub
    out = drive_c / "leagues" / "I902" / "outbound"
    (out / "L903-1-0000000000001-0.brp").write_text("{}")
    service = ProcessingService(db_session, config)
    asyncio.run(provider_for("I").collect_outbound(service, leagues["902I"], 0, out))
    assert list(out.iterdir()) == []
    assert [q["file"] for q in service.quarantined] == ["L903-1-0000000000001-0.brp"]
    assert db_session.query(Packet).count() == 0


# ── preflight ─────────────────────────────────────────────────────────────

def test_well_made_games_pass_and_the_result_is_kept(hub, db_session):
    config, leagues, _ = hub
    assert preflight.run(db_session, config) == {"900B": [], "901B": [], "902I": []}
    row = leagues["900B"].install
    assert row.preflight_ok is True and row.preflight_at is not None
    assert row.preflight_problems == []


def _problems(db, config, league):
    return preflight.run(db, config, [league])[league.full_id]


def test_a_wrong_league_number_fails(hub, db_session):
    config, leagues, drive_c = hub
    _bbs_cfg(drive_c / "leagues" / "B901", "900", "C:\\LEAGUES\\B901\\INBOUND")
    problems = _problems(db_session, config, leagues["901B"])
    assert problems == ["bbs.cfg line 6 says league '900', not 901"]
    assert leagues["901B"].install.preflight_ok is False


def test_an_inbound_line_naming_another_leagues_inbound_fails(hub, db_session):
    config, leagues, drive_c = hub
    _bbs_cfg(drive_c / "leagues" / "B901", "901", "C:\\LEAGUES\\B900\\INBOUND")
    [problem] = _problems(db_session, config, leagues["901B"])
    assert "not this league's INBOUND" in problem


def test_an_inbound_path_that_breaks_8_3_fails(hub, db_session):
    config, leagues, drive_c = hub
    _bbs_cfg(drive_c / "leagues" / "B901", "901", "C:\\LEAGUES\\B901_NODE\\INBOUND")
    assert any("not a DOS 8.3 name" in p for p in _problems(db_session, config, leagues["901B"]))


def test_a_hub_address_that_is_not_the_leagues_fails(hub, db_session):
    config, leagues, drive_c = hub
    _bbs_cfg(drive_c / "leagues" / "B901", "901", "C:\\LEAGUES\\B901\\INBOUND", "13:10/9")
    assert any("line 3" in p for p in _problems(db_session, config, leagues["901B"]))


def test_a_game_whose_folder_is_another_leagues_fails(hub, db_session):
    """A folder can only be two leagues' through a link, since each is named
    for its league; this is the check that holds once the rename has been."""
    config, leagues, drive_c = hub
    root = drive_c / "leagues"
    import shutil
    shutil.rmtree(root / "B901")
    (root / "B901").symlink_to(root / "B900")
    problems = _problems(db_session, config, leagues["901B"])
    assert any("also 900B's folder" in p for p in problems), problems


def test_a_nodes_file_that_disagrees_with_the_memberships_fails(hub, db_session):
    from backend.models.database import Client, LeagueMembership
    config, leagues, _ = hub
    c = Client(client_id="alpha", client_secret="x", bbs_name="Alpha")
    db_session.add(c)
    db_session.flush()
    db_session.add(LeagueMembership(client_id=c.id, league_id=leagues["900B"].id, bbs_index=2,
                                    is_active=True))
    db_session.commit()
    assert any("not found in brnodes.dat" in p.lower()
               for p in _problems(db_session, config, leagues["900B"]))


def test_an_ib_game_with_another_league_number_fails(hub, db_session):
    config, leagues, drive_c = hub
    (drive_c / "leagues" / "I902" / "bbs.cfg").write_text("LeagueNumber 900\n")
    assert _problems(db_session, config, leagues["902I"]) == [
        "bbs.cfg LeagueNumber is '900', not 902"]


def test_an_image_that_does_not_match_its_manifest_fails(hub, db_session):
    config, leagues, _ = hub
    image = leagues["902I"].install.image
    folder = games_config(config).images / image.dirname
    (folder / "manifest.json").write_text(json.dumps({"files": {"immortal-barons": "0" * 64}}))
    assert _problems(db_session, config, leagues["902I"]) == [
        f"image {image.dirname}: immortal-barons does not match its manifest"]


# ── the league page ───────────────────────────────────────────────────────

from tests.test_league_members_api import LEAGUES, _as_sysop, api  # noqa: E402,F401


def test_an_admin_sees_the_leagues_game_and_can_run_preflight(api):
    from backend.core.config import get_config
    client, db = api
    config = get_config()._raw
    lg = League(league_id="950", game_type="B", name="950B", is_active=True,
                hub_fidonet_address="13:10/1")
    db.add(lg)
    db.commit()
    folder = install_game(db, config, lg, Path(config["games"]["drive_c"]), state="installed")

    game = client.get(f"{LEAGUES}/{lg.id}").json()["game"]
    assert (game["folder"], game["path"], game["state"], game["runs"]) == (
        "B950", str(folder), "installed", False)
    assert game["preflight_ok"] is None

    r = client.post(f"{LEAGUES}/{lg.id}/preflight")
    assert r.status_code == 200, r.text
    assert r.json()["preflight_ok"] is False
    assert any("bbs.cfg cannot be read" in p for p in r.json()["preflight_problems"])

    _bbs_cfg(folder, "950", "C:\\LEAGUES\\B950\\INBOUND")
    (folder / "BRNODES.DAT").write_bytes(b"1 HOST\r\nNova Hub\r\n13:10/1\r\n\r\n\r\n\r\n\r\n")
    r = client.post(f"{LEAGUES}/{lg.id}/preflight")
    assert (r.json()["preflight_ok"], r.json()["preflight_problems"]) == (True, [])
    assert client.get(f"{LEAGUES}/{lg.id}").json()["game"]["preflight_ok"] is True


def test_a_member_does_not_see_the_hubs_game(api):
    from backend.core.config import get_config
    client, db = api
    config = get_config()._raw
    lg = League(league_id="951", game_type="B", name="951B", is_active=True)
    db.add(lg)
    db.commit()
    install_game(db, config, lg, Path(config["games"]["drive_c"]))
    _as_sysop()
    r = client.post(f"{LEAGUES}/{lg.id}/preflight")
    assert r.status_code == 403
