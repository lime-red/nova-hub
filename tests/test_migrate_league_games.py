"""migrate_league_games.py on a fake hub laid out as production is: BRE in
drive_c/bbs/doors/bre_013 and Immortal Barons in its own data folder with a
`current` link to its release, both in config.toml sections."""
import json
import subprocess
from pathlib import Path

import pytest
import toml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, GameImage, League, LeagueGame, LeagueMembership, Client
from migrate_league_games import run

CONFIG = """[server]
port = 8000
data_dir = "{data}"

[dosemu]
dosemu_path = "/usr/bin/dosemu"
timeout = 300

# Per-league DOSEMU configuration
# Format: [dosemu.<league_number>.<game_type>]

[dosemu.013.bre]
game_folder = "{drive_c}/bbs/doors/bre_013"
game_dos_path = "C:\\\\bbs\\\\doors\\\\bre_013"
inbound_folder = "{drive_c}/bbs/doors/bre_013/INBOUND"
outbound_folder = "{drive_c}/bbs/doors/bre_013/OUTBOUND"
scores_folder = "{drive_c}/bbs/doors/bre_013/BULLETIN"
processing_command = "BRE.EXE PLANETARY /DETAILED"
# The game's own "I finished" line.
completion_marker = "Planetary Maintenance Complete"
scores_command = "BRE.EXE SCORES"
reset_command = "BRE.EXE RESET"


[database]
path = "{db}"

[identity]
provider = ""
# client_id = "client_..."
# api_key = "sk_..."

[ib.902]
binary = "{ib}/current/immortal-barons"
data_dir = "{ib}/data"
hub_board_key = "{key}"
"""

KEY = "ab" * 32


@pytest.fixture
def host(tmp_path):
    drive_c, ib, data = tmp_path / "drive_c", tmp_path / "ib_902", tmp_path / "data"
    bre = drive_c / "bbs" / "doors" / "bre_013"
    for sub in ("INBOUND", "OUTBOUND", "BULLETIN"):
        (bre / sub).mkdir(parents=True)
    (bre / "bbs.cfg").write_bytes(b"Nova Admin\r\nNova Hub\r\n13:10/1\r\nC:\\bbs\\doors\\bre_013\\inbound"
                                  b"\r\nC:\\bbs\\doors\\bre_013\\netmail\r\n013\r\nNONE\r\n")
    (bre / "BRNODES.DAT").write_bytes(b"1 HOST\r\nNova Hub\r\n13:10/1\r\n\r\n\r\n\r\n\r\n")
    (bre / "BRE.EXE").write_bytes(b"MZ")
    release = ib / "releases" / "v0.2.6"
    release.mkdir(parents=True)
    (release / "immortal-barons").write_text("#!/bin/sh\nexit 0\n")
    (release / "immortal-barons").chmod(0o755)
    (ib / "current").symlink_to(release)
    for sub in ("inbound", "outbound"):
        (ib / "data" / sub).mkdir(parents=True)
    (ib / "data" / "bbs.cfg").write_text(f"BoardID Nova Hub\nLeagueNumber 902\n"
                                         f"GameInbound inbound\nGameOutbound {ib}/data/outbound\n")
    (ib / "data" / "config.json").write_text(json.dumps({"DataDir": str(ib / "data"), "Days": 7},
                                                        indent=2))
    data.mkdir()
    db = data / "nova-hub.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for number, letter in (("013", "B"), ("902", "I")):
        session.add(League(league_id=number, game_type=letter, name=number + letter,
                           is_active=True, hub_fidonet_address="13:10/1"))
    session.commit()
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.format(data=data, drive_c=drive_c, db=db, ib=ib, key=KEY))
    return dict(config=config, drive_c=drive_c, bre=bre, ib=ib, data=data, session=session,
                tmp=tmp_path)


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() and not p.is_symlink()
                                             else None)
            for p in sorted(root.rglob("*")) if "nova-hub.db" not in p.name}


def _migrate(host, **kw):
    return run(host["config"], do_apply=True, check_hub=False, **kw)


def test_a_dry_run_changes_nothing(host, capsys):
    before = _snapshot(host["tmp"])
    assert run(host["config"], do_apply=False, check_hub=False) == 0
    assert _snapshot(host["tmp"]) == before
    out = capsys.readouterr().out
    assert "C:\\bbs\\doors\\bre_013\\inbound  ->  C:\\LEAGUES\\B013\\INBOUND" in out
    assert "image ib-v0.2.6" in out


def test_the_games_move_into_the_database_and_undo_puts_everything_back(host):
    before = _snapshot(host["tmp"])
    assert _migrate(host) == 0

    leagues = host["drive_c"] / "leagues"
    b013, i902 = leagues / "B013", leagues / "I902"
    assert not host["bre"].exists() and not (host["ib"] / "data").exists()
    lines = (b013 / "bbs.cfg").read_bytes().split(b"\r\n")
    assert lines[3:5] == [b"C:\\LEAGUES\\B013\\INBOUND", b"C:\\LEAGUES\\B013\\NETMAIL"]
    assert (b013 / "bbs.cfg.pre-league-games").read_bytes().startswith(b"Nova Admin")
    assert "GameOutbound outbound\n" in (i902 / "bbs.cfg").read_text()
    assert json.loads((i902 / "config.json").read_text()) == {"DataDir": str(i902), "Days": 7}

    config = toml.loads(host["config"].read_text())
    assert config["games"] == {"root": str(leagues), "drive_c": str(host["drive_c"])}
    assert "ib" not in config and "013" not in config["dosemu"]
    text = host["config"].read_text()
    assert "Per-league DOSEMU" not in text
    assert '# api_key = "sk_..."' in text, "a setting's own comments go with the section above"

    db = host["session"]
    db.expire_all()
    rows = {r.league.full_id: r for r in db.query(LeagueGame)}
    assert {k: (r.folder, r.state, r.preflight_ok) for k, r in rows.items()} == {
        "013B": ("B013", "ready", True), "902I": ("I902", "ready", True)}
    assert rows["902I"].hub_board_key == KEY
    image = rows["902I"].image
    assert image.dirname == "ib-v0.2.6"
    assert (host["data"] / "images" / "ib-v0.2.6" / "immortal-barons").read_text().startswith("#!")

    db.close()
    assert subprocess.run(["sh", str(host["tmp"] / "league-games-undo.sh")]).returncode == 0
    (host["tmp"] / "league-games-undo.sh").unlink()
    after = _snapshot(host["tmp"])
    after = {k: v for k, v in after.items() if not k.startswith(("data/images", "drive_c/leagues"))}
    assert after == before
    assert db.query(LeagueGame).count() == 0 and db.query(GameImage).count() == 0


def test_a_section_that_says_something_the_hub_no_longer_does_is_refused(host, capsys):
    text = host["config"].read_text().replace('"BRE.EXE SCORES"', '"BRE.EXE SCORES /ALL"')
    host["config"].write_text(text)
    before = _snapshot(host["tmp"])
    assert _migrate(host) == 1
    assert _snapshot(host["tmp"]) == before
    assert "scores_command is 'BRE.EXE SCORES /ALL'" in capsys.readouterr().err


def test_an_inbound_line_outside_the_game_is_refused(host, capsys):
    cfg = host["bre"] / "bbs.cfg"
    cfg.write_bytes(cfg.read_bytes().replace(b"doors\\bre_013\\inbound", b"mail\\inbound"))
    assert _migrate(host) == 1
    assert "bbs.cfg line 4" in capsys.readouterr().err
    assert host["bre"].exists()


def test_a_failed_preflight_undoes_the_move(host, capsys):
    db = host["session"]
    c = Client(client_id="alpha", client_secret="x", bbs_name="Alpha")
    db.add(c)
    db.flush()
    league = db.query(League).filter_by(league_id="013").one()
    db.add(LeagueMembership(client_id=c.id, league_id=league.id, bbs_index=2, is_active=True))
    db.commit()
    before = _snapshot(host["tmp"])

    assert _migrate(host) == 1
    assert "Preflight failed" in capsys.readouterr().err
    (host["tmp"] / "league-games-undo.sh").unlink()
    after = {k: v for k, v in _snapshot(host["tmp"]).items()
             if not k.startswith(("data/images", "drive_c/leagues"))}
    assert after == before
    db.expire_all()
    assert db.query(LeagueGame).count() == 0


def test_nothing_to_do_without_league_sections(host, capsys):
    host["config"].write_text("[server]\nport = 8000\n")
    assert _migrate(host) == 0
    assert "nothing to move" in capsys.readouterr().out
