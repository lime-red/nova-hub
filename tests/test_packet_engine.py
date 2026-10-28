"""The packet lifecycle's edges (#90).

An upload is refused before it costs anything when it can't be right, and a
retry of one the hub already has changes nothing. A sequence alert is resolved
only by its own packet, in its own time round. Processing never drops a
packet that arrived while it ran, and a dosemu that hangs is killed whole.
"""
import asyncio
import os
import shutil
import sys
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app, service_app
from backend.core import rate_limiter
from backend.core.clock import utcnow
from backend.core.database import get_db
from backend.core.security import get_current_user, get_password_hash
from backend.models.database import (
    Base, Client, League, LeagueMembership, Packet, SequenceAlert, SysopUser,
)
from backend.services import processing_service
from backend.services.providers import sr_games
from backend.services.dosemu_runner import DosemuRunner
from backend.services.game_reset.perform import backup, restore
from backend.services.processing_service import ProcessingService
from backend.services.sequence_validator import SequenceValidator

TOKEN_URL = "/service/api/v1/auth/token"
PACKETS = "/service/api/v1/leagues/555B/packets"
MAX = 4096


@pytest.fixture
def hub(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = Session()
    admin = SysopUser(username="admin", hashed_password="x", is_superuser=True)
    board = Client(client_id="board", client_secret=get_password_hash("s"), bbs_name="Board")
    unplaced = Client(client_id="unplaced", client_secret=get_password_hash("s"), bbs_name="New")
    league = League(league_id="555", game_type="B", name="BRE 555")
    off = League(league_id="556", game_type="B", name="BRE 556", is_active=False)
    db.add_all([admin, board, unplaced, league, off])
    db.commit()
    db.add_all([LeagueMembership(client_id=board.id, league_id=league.id, bbs_index=2),
                LeagueMembership(client_id=board.id, league_id=off.id, bbs_index=2),
                LeagueMembership(client_id=unplaced.id, league_id=league.id, bbs_index=None)])
    db.commit()

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    saved = {a: dict(a.dependency_overrides) for a in (app, management_app, service_app)}
    for a in (app, management_app, service_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = lambda: Session().get(SysopUser, admin.id)
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    from backend.api.service import packets as packets_api

    class FakeConfig:
        class security:
            max_upload_size_bytes = MAX

        def get(self, section, default=None):
            return {"data_dir": str(tmp_path)} if section == "server" else default

    monkeypatch.setattr(packets_api, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(processing_service, "trigger_processing", lambda: None)

    yield TestClient(app, base_url="https://testserver"), db, league
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def _auth(client, who="board"):
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                       "client_id": who, "client_secret": "s"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _put(client, path, content, headers):
    return client.put(path, content=content,
                      headers={**headers, "Content-Type": "application/octet-stream"})


# -- uploads ----------------------------------------------------------------------

def test_an_upload_to_a_league_that_does_not_exist_makes_no_league(hub):
    client, db, _ = hub
    r = _put(client, "/service/api/v1/leagues/777B/packets/777B0201.001", b"x", _auth(client))
    assert r.status_code == 404
    assert db.query(League).filter(League.league_id == "777").count() == 0


def test_an_upload_to_a_disabled_league_is_refused(hub):
    client, db, _ = hub
    r = _put(client, "/service/api/v1/leagues/556B/packets/556B0201.001", b"x", _auth(client))
    assert r.status_code == 403
    assert db.query(Packet).count() == 0


def test_a_content_length_that_is_not_a_number_is_a_bad_request(hub):
    client, *_ = hub
    r = client.put(f"{PACKETS}/555B0201.001", content=b"x",
                   headers={**_auth(client), "Content-Length": "lots"})
    assert r.status_code == 400


def test_a_chunked_upload_past_the_limit_is_refused_as_it_streams(hub):
    client, db, _ = hub
    def body():
        for _ in range(64):
            yield b"\0" * 1024

    r = client.put(f"{PACKETS}/555B0201.001", content=body(), headers=_auth(client))
    assert r.status_code == 413
    assert db.query(Packet).count() == 0


def test_a_retry_of_the_same_packet_is_the_same_packet(hub, tmp_path):
    client, db, _ = hub
    headers = _auth(client)
    first = _put(client, f"{PACKETS}/555B0201.001", b"packet", headers)
    again = _put(client, f"{PACKETS}/555B0201.001", b"packet", headers)
    assert first.status_code == again.status_code == 200
    assert first.json()["packet_id"] == again.json()["packet_id"]
    assert db.query(Packet).count() == 1


def test_new_bytes_under_the_same_name_are_a_new_packet(hub):
    client, db, _ = hub
    headers = _auth(client)
    _put(client, f"{PACKETS}/555B0201.001", b"packet", headers)
    _put(client, f"{PACKETS}/555B0201.001", b"packet, grown", headers)
    assert db.query(Packet).count() == 2


def test_the_same_bytes_a_day_later_are_a_new_packet(hub):
    client, db, _ = hub
    headers = _auth(client)
    _put(client, f"{PACKETS}/555B0201.001", b"packet", headers)
    db.query(Packet).update({Packet.uploaded_at: utcnow() - timedelta(days=2)})
    db.commit()
    _put(client, f"{PACKETS}/555B0201.001", b"packet", headers)
    assert db.query(Packet).count() == 2


def test_an_upload_says_which_client_sent_it(hub):
    client, db, _ = hub
    _put(client, f"{PACKETS}/555B0201.001", b"packet", _auth(client))
    board = db.query(Client).filter(Client.client_id == "board").one()
    assert db.query(Packet).one().source_client_id == board.id


def test_a_member_without_a_board_index_is_refused_not_a_500(hub):
    client, *_ = hub
    headers = _auth(client, "unplaced")
    assert _put(client, f"{PACKETS}/555B0201.001", b"x", headers).status_code == 403
    assert client.get(f"{PACKETS}/555B0102.001", headers=headers).status_code in (403, 404)
    listed = client.get(PACKETS, headers=headers)
    assert listed.status_code == 200 and listed.json()["packets"] == []


# -- sequence alerts ------------------------------------------------------------

def _route(db, league, numbers, days_ago=30):
    start = utcnow() - timedelta(days=days_ago)
    for i, n in enumerate(numbers):
        db.add(Packet(filename=f"555B0201.{n:03d}", league_id=league.id,
                      source_bbs_index="02", dest_bbs_index="01", sequence_number=n,
                      file_size=1, uploaded_at=start + timedelta(minutes=i)))
    db.commit()


def _alert(db, league, number, epoch):
    alert = SequenceAlert(league_id=league.id, source_bbs_index="02", dest_bbs_index="01",
                          expected_sequence=number, received_sequence=number + 1, gap_size=1,
                          sequence_epoch=epoch)
    db.add(alert)
    db.commit()
    return alert


def test_a_gap_after_the_wrap_is_not_filled_by_the_last_time_round(hub):
    _, db, league = hub
    _route(db, league, list(range(1000)) + [0, 1, 3, 4, 5])
    alert = _alert(db, league, 2, epoch=1)

    SequenceValidator(db).auto_resolve_alerts()
    db.refresh(alert)
    assert not alert.is_resolved

    _route(db, league, [2], days_ago=1)            # late, not lost
    SequenceValidator(db).auto_resolve_alerts()
    db.refresh(alert)
    assert alert.is_resolved and alert.resolution_note == "Missing packet received"


def test_resolving_by_hand_is_what_the_validator_sees(hub):
    client, db, league = hub
    _route(db, league, [0, 1, 3, 4])
    alert = _alert(db, league, 2, epoch=0)

    assert client.post(f"/management/api/v1/alerts/{alert.id}/resolve").status_code == 200
    db.refresh(alert)
    assert alert.is_resolved
    SequenceValidator(db).auto_resolve_alerts()
    db.refresh(alert)
    assert alert.resolution_note is None      # left alone, not resolved over the top

    assert client.post(f"/management/api/v1/alerts/{alert.id}/unresolve").status_code == 200
    db.refresh(alert)
    assert not alert.is_resolved and alert.resolved_at is None


# -- processing -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_trigger_during_a_run_runs_again_after_it(monkeypatch):
    runs = []

    async def once():
        runs.append(1)
        if len(runs) == 1:
            processing_service.trigger_processing()     # an upload, mid-run
            await asyncio.sleep(0.01)

    monkeypatch.setattr(processing_service, "_process_once", once)
    monkeypatch.setattr(processing_service, "_rerun", False)
    await processing_service.process_batch()
    await asyncio.sleep(0.01)
    assert len(runs) == 2


class _Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg, *a, **k: self.lines.append((level, msg))


def test_a_stray_file_in_the_games_outbound_is_passed_over(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    game_out = tmp_path / "out"
    game_out.mkdir()
    (game_out / "README.TXT").write_text("not a packet")
    service = ProcessingService(db, {"hub": {"bbs_index": "01"},
                                     "server": {"data_dir": str(tmp_path / "data")},
                                     "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 60}})
    log = _Log()
    monkeypatch.setattr(sr_games, "logger", log)

    asyncio.run(service.collect_outbound_packets("BRE", run_id=None, outbound_dir=game_out))
    assert [lvl for lvl, _ in log.lines if lvl in ("error", "exception")] == []
    assert any("README.TXT" in msg for lvl, msg in log.lines if lvl == "warning")
    assert (game_out / "README.TXT").exists()


@pytest.mark.skipif(shutil.which("script") is None, reason="needs script(1)")
@pytest.mark.asyncio
async def test_a_timed_out_dosemu_is_killed_with_everything_it_started(tmp_path):
    runner = DosemuRunner({"server": {"data_dir": str(tmp_path)},
                           "dosemu": {"dosemu_path": "/bin/true", "timeout": 1}})
    pidfile = tmp_path / "child.pid"
    # _run_command puts this under script(1), which gives it a session and pty
    # of its own. Deaf to the pty's hangup, as dosemu may be, it dies only if
    # it is killed outright, not because script went.
    cmd = ["sh", "-c", f"trap '' HUP; sleep 60 & echo $! > {pidfile}; wait"]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(runner._run_command(cmd, tmp_path / "out.log"), timeout=1)

    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _running(pid):
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("the game's process outlived the timeout")


@pytest.mark.skipif(shutil.which("script") is None, reason="needs script(1)")
@pytest.mark.asyncio
async def test_a_timeout_kills_an_orphan_left_in_dosemus_session(tmp_path):
    runner = DosemuRunner({"server": {"data_dir": str(tmp_path)},
                           "dosemu": {"dosemu_path": "/bin/true", "timeout": 1}})
    pidfile = tmp_path / "orphan.pid"
    # The subshell starts sleep and exits, so sleep is reparented out of the
    # tree under script; only its session still ties it to the run.
    cmd = ["sh", "-c", f"trap '' HUP; (sleep 60 & echo $! > {pidfile}); sleep 60"]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(runner._run_command(cmd, tmp_path / "out.log"), timeout=1)

    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _running(pid):
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("an orphan in dosemu's session outlived the timeout")


def _running(pid: int) -> bool:
    """Alive and not a zombie. In a container with no init to reap orphans
    (CI's), a killed process lingers as a zombie, which kill(pid, 0) still
    finds."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] not in ("Z", "X")


# -- the reset's restore --------------------------------------------------------

def test_a_restore_swaps_the_folder_and_leaves_nothing_beside_it(tmp_path):
    game = tmp_path / "bre_555"
    game.mkdir()
    (game / "GAME.DAT").write_bytes(b"before")
    archive = backup(game, tmp_path / "backups", "s1")
    (game / "GAME.DAT").write_bytes(b"after")
    (game / "NEW.DAT").write_bytes(b"x")
    (tmp_path / ".bre_555.replaced").mkdir()        # left by a restore that died

    restore(archive, game)
    assert (game / "GAME.DAT").read_bytes() == b"before"
    assert not (game / "NEW.DAT").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backups", "bre_555"]


def test_a_restore_will_not_write_outside_the_folder(tmp_path):
    game = tmp_path / "bre_555"
    game.mkdir()
    (game / "GAME.DAT").write_bytes(b"keep")
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_bytes(b"x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escaped")

    with pytest.raises(tarfile.TarError):
        restore(evil, game)
    assert not (tmp_path / "escaped").exists()
    assert (game / "GAME.DAT").read_bytes() == b"keep"
