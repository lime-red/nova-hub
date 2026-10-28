"""Immortal Barons through the hub (#100): fan-out, collisions, the native run,
the roster, and what the SR-game machinery must leave alone.

The hub's game is a shell script standing in for immortal-barons: it "applies"
every *.brp in its inbound (records the name and deletes it, as the game does)
and publishes whatever the test left in to_send/ into its outbound.
"""

import asyncio
import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app, service_app
from backend.core.config import get_config
from backend.core.database import get_db
from backend.core.security import create_service_token, get_current_user, require_admin
from backend.models.database import Base, Client, League, LeagueMembership, Packet, SysopUser
from backend.services import processing_service
from backend.services.claim_links import LeagueEntry, render_config_psd1, render_config_toml
from backend.services.processing_service import ProcessingService
from backend.services.providers import provider_for
from backend.services.providers.immortal_barons import ImmortalBaronsProvider, valid_board_key
from backend.services.retention_service import RetentionService
from backend.services.sequence_validator import SequenceValidator

KEY_C = "4e1b9c07a3f25d81c6b40e9fa27d3358e1c0b7429dd6a85f3b1e64c0927af8d3"
KEY_HUB = "a" * 64
IB = provider_for("I")

FAKE_GAME = """#!/bin/sh
# immortal-barons -maint -data DIR
DATA="$3"
for f in "$DATA"/inbound/*; do
  case "$f" in *.brp|*.BRP) basename "$f" >> "$DATA/applied.txt"; rm "$f";; esac
done
for f in "$DATA"/to_send/*; do
  [ -f "$f" ] && mv "$f" "$DATA/outbound/"
done
echo "planetary run done"
[ -f "$DATA/exit_code" ] && exit "$(cat "$DATA/exit_code")"
exit 0
"""


def ib_name(src: int, seq: int, dst: int, league: str = "555") -> str:
    """The game's own name for a packet: L<league>-<src>-<seq, 13 digits>-<dst>.brp, base 36."""
    def b36(n: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = ""
        while True:
            n, r = divmod(n, 36)
            out = digits[r] + out
            if n == 0:
                return out
    return f"L{league}-{b36(src)}-{b36(seq).rjust(13, '0')}-{b36(dst)}.brp"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A hub with league 555I: node 1 is the hub, members 2 (A), 3 (B) and 4 (C)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'hub.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    admin = SysopUser(username="admin", hashed_password="x", is_superuser=True)
    league = League(league_id="555", game_type="I", name="IB 555", is_active=True,
                    hub_fidonet_address="21:1/100")
    db.add_all([admin, league])
    db.flush()
    boards = {}
    for index, label in ((2, "A"), (3, "B"), (4, "C")):
        client = Client(client_id=f"board_{label}", client_secret="x", bbs_name=f"Board {label}",
                        is_active=True)
        db.add(client)
        db.flush()
        db.add(LeagueMembership(client_id=client.id, league_id=league.id, bbs_index=index,
                                is_active=True, board_key=KEY_C if label == "C" else None))
        boards[label] = client
    db.commit()

    game = tmp_path / "ib555"
    for sub in ("inbound", "outbound", "to_send"):
        (game / sub).mkdir(parents=True)
    script = tmp_path / "immortal-barons"
    script.write_text(FAKE_GAME)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    data = tmp_path / "data"

    config = {
        "hub": {"bbs_index": "01", "bbs_name": "Nova Hub", "city": "Austin"},
        "server": {"data_dir": str(data)},
        "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 60},
        "ib": {"555": {"binary": str(script), "data_dir": str(game), "hub_board_key": KEY_HUB}},
    }

    def override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    saved = {a: dict(a.dependency_overrides) for a in (app, management_app, service_app)}
    for a in (app, management_app, service_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = lambda: Session().get(SysopUser, admin.id)
        a.dependency_overrides[require_admin] = lambda: Session().get(SysopUser, admin.id)

    real = get_config()
    saved_raw = {k: real._raw.get(k) for k in ("server", "hub")}
    real._raw["server"] = dict(real._raw.get("server") or {}, data_dir=str(data))
    real._raw["hub"] = dict(real._raw.get("hub") or {}, bbs_index="01")
    monkeypatch.setattr(processing_service, "trigger_processing", lambda: None)

    yield {
        "http": TestClient(app, base_url="https://testserver"),
        "db": db, "Session": Session, "league": league, "boards": boards,
        "config": config, "game": game, "data": data,
    }

    db.close()
    engine.dispose()
    for k, v in saved_raw.items():
        if v is None:
            real._raw.pop(k, None)
        else:
            real._raw[k] = v
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def upload(hub, board: str, filename: str, body: bytes):
    token = create_service_token(hub["boards"][board].client_id)
    return hub["http"].put(f"/service/api/v1/leagues/555I/packets/{filename}", content=body,
                           headers={"Authorization": f"Bearer {token}"})


def download(hub, board: str, filename: str):
    token = create_service_token(hub["boards"][board].client_id)
    return hub["http"].get(f"/service/api/v1/leagues/555I/packets/{filename}",
                           headers={"Authorization": f"Bearer {token}"})


def listed(hub, board: str) -> set[str]:
    token = create_service_token(hub["boards"][board].client_id)
    r = hub["http"].get("/service/api/v1/leagues/555I/packets",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return {p["filename"] for p in r.json()["packets"]}


def dests(hub, filename: str) -> list[str]:
    db = hub["Session"]()
    try:
        return sorted(p.dest_bbs_index for p in
                      db.query(Packet).filter(Packet.filename == filename.upper()).all())
    finally:
        db.close()


def run_batch(hub):
    """One processing batch; the run's (status, error_message)."""
    db = hub["Session"]()
    try:
        run = asyncio.run(ProcessingService(db, hub["config"]).process_batch())
        return run.status, run.error_message
    finally:
        db.close()


# -- routing ------------------------------------------------------------------

def test_a_broadcast_is_a_row_for_the_hub_and_every_member_but_its_sender(hub):
    broadcast = ib_name(2, 1, 0)
    assert upload(hub, "A", broadcast, b'{"Seq": 1}').status_code == 200
    assert dests(hub, broadcast) == ["01", "03", "04"]


def test_a_packet_for_one_board_is_one_row(hub):
    direct = ib_name(2, 2, 3)
    assert upload(hub, "A", direct, b'{"Seq": 2}').status_code == 200
    assert dests(hub, direct) == ["03"]


def test_the_same_bytes_again_are_the_same_packet(hub):
    broadcast = ib_name(2, 1, 0)
    first = upload(hub, "A", broadcast, b'{"Seq": 1}').json()
    second = upload(hub, "A", broadcast, b'{"Seq": 1}').json()
    assert first["packet_id"] == second["packet_id"]
    assert dests(hub, broadcast) == ["01", "03", "04"]


def test_different_bytes_under_a_known_name_are_refused_not_replaced(hub):
    direct = ib_name(2, 2, 3)
    assert upload(hub, "A", direct, b'{"Seq": 2}').status_code == 200
    r = upload(hub, "A", direct, b'{"Seq": 2, "forged": true}')
    assert r.status_code == 409
    assert (hub["data"] / "packets" / "inbound" / direct.upper()).read_bytes() == b'{"Seq": 2}'


def test_a_name_without_a_node_number_is_refused(hub):
    # A board with no roster yet names its packets by digest; the hub cannot route them.
    assert upload(hub, "A", "L555-ABCDEFGHIJKLMNOPQRSTUVWXYZ.brp", b"{}").status_code == 400


# -- one batch ----------------------------------------------------------------

def test_a_batch_gives_the_hub_its_own_mail_and_the_members_theirs(hub):
    broadcast, direct = ib_name(2, 1, 0), ib_name(2, 2, 3)
    upload(hub, "A", broadcast, b'{"Seq": 1}')
    upload(hub, "A", direct, b'{"Seq": 2}')
    # What the hub's game sends back this run: a broadcast of its own
    (hub["game"] / "to_send" / ib_name(1, 5, 0)).write_bytes(b'{"Seq": 5}')

    status, error = run_batch(hub)
    assert status == "completed", error

    # The hub's game was handed the broadcast, and not the mail between A and B.
    assert (hub["game"] / "applied.txt").read_text().split() == [broadcast.upper()]
    # Everything that left the hub is in its outbound, and the uploads are archived.
    outbound = hub["data"] / "packets" / "outbound"
    assert {broadcast.upper(), direct.upper(), ib_name(1, 5, 0).upper()} <= {
        p.name for p in outbound.iterdir()}
    assert not any((hub["data"] / "packets" / "inbound").iterdir())
    assert (hub["data"] / "packets" / "processed" / direct.upper()).is_file()

    # The hub's broadcast went to every member.
    assert dests(hub, ib_name(1, 5, 0)) == ["02", "03", "04"]

    # Each board sees, and can fetch, exactly its own.
    assert {broadcast.upper(), direct.upper(), ib_name(1, 5, 0).upper()} <= listed(hub, "B")
    assert download(hub, "B", broadcast.upper()).content == b'{"Seq": 1}'
    assert download(hub, "C", broadcast.upper()).status_code == 200
    assert download(hub, "B", direct.upper()).status_code == 200
    assert download(hub, "C", direct.upper()).status_code == 403
    assert download(hub, "A", broadcast.upper()).status_code == 403  # its own
    assert download(hub, "A", ib_name(1, 5, 0).upper()).status_code == 200


def test_a_run_that_meets_a_new_fault_still_delivers_and_says_it_failed(hub):
    # The game exits 1 for a transport fault it has not reported before, after
    # applying the rest and writing its outbox.
    upload(hub, "A", ib_name(2, 1, 0), b'{"Seq": 1}')
    (hub["game"] / "to_send" / ib_name(1, 6, 2)).write_bytes(b'{"Seq": 6}')
    (hub["game"] / "exit_code").write_text("1")

    status, error = run_batch(hub)
    assert status == "failed"
    assert "exited 1" in error
    assert dests(hub, ib_name(1, 6, 2)) == ["02"]
    db = hub["Session"]()
    try:
        assert all(p.processed_at for p in db.query(Packet).filter(
            Packet.filename == ib_name(2, 1, 0).upper()))
    finally:
        db.close()


def test_the_hubs_game_never_overwrites_a_different_packet(hub):
    name = ib_name(1, 7, 3).upper()
    outbound = hub["data"] / "packets" / "outbound"
    outbound.mkdir(parents=True)
    (outbound / name).write_bytes(b"the first")
    (hub["game"] / "to_send" / ib_name(1, 7, 3)).write_bytes(b"something else")

    run_batch_with_nothing = asyncio.run(
        IB.collect_outbound(ProcessingService(hub["db"], hub["config"]), hub["league"], 0,
                            hub["game"] / "to_send"))
    assert run_batch_with_nothing is None
    assert (outbound / name).read_bytes() == b"the first"
    assert (hub["game"] / "to_send" / ib_name(1, 7, 3)).is_file()
    assert dests(hub, name) == []


def test_temp_files_and_renamed_duplicates_are_not_packets(hub):
    out = hub["game"] / "outbound"
    (out / f".{ib_name(1, 8, 2)}-123.tmp").write_bytes(b"half")
    (out / ib_name(1, 9, 2).replace(".brp", ".br1")).write_bytes(b"dupe")
    asyncio.run(IB.collect_outbound(ProcessingService(hub["db"], hub["config"]),
                                    hub["league"], 0, out))
    assert hub["db"].query(Packet).count() == 0


def test_a_league_with_no_ib_section_fails_the_run_and_says_why(hub):
    hub["config"]["ib"] = {}
    upload(hub, "A", ib_name(2, 1, 0), b'{"Seq": 1}')
    status, error = run_batch(hub)
    assert status == "failed"
    assert "[ib.555] needs binary and data_dir" in error


# -- sequence numbers ------------------------------------------------------------

def test_ib_routes_are_not_examined_for_gaps(hub):
    for seq in (1, 2, 9, 40):  # a sender's counter, spread over destinations
        upload(hub, "A", ib_name(2, seq, 3), f'{{"Seq": {seq}}}'.encode())
    db = hub["Session"]()
    try:
        assert SequenceValidator(db, hub_index="01")._routes() == []
    finally:
        db.close()


# -- the roster ------------------------------------------------------------------

def test_the_roster_is_a_mesh_with_the_hub_as_node_1_and_the_keys_on_line_7(hub):
    content, problems = IB.roster(hub["db"], hub["config"], hub["league"])
    assert problems == []
    entries = [e.split("\r\n") for e in content.strip("\r\n").split("\r\n\r\n")]
    assert [e[0] for e in entries] == ["1", "2", "3", "4"]  # no HOST lines
    assert entries[0][:3] == ["1", "Nova Hub", "21:1/100"]
    assert entries[0][6] == KEY_HUB
    assert entries[1] == ["2", "Board A", "-", "-", "-", "-"]
    assert entries[3][6] == KEY_C
    assert "HOST" not in content


def test_the_roster_needs_the_hub_at_node_1(hub):
    hub["config"]["hub"]["bbs_index"] = "05"
    content, problems = IB.roster(hub["db"], hub["config"], hub["league"])
    assert content is None
    assert "node 1" in problems[0]


def test_the_roster_goes_into_the_hubs_game_and_out_to_the_members(hub):
    published, problems = IB.generate_nodelist(hub["db"], hub["config"], hub["league"])
    assert problems == []
    assert published.name == "IBNODES.555"
    assert (hub["game"] / "ibnodes.dat").read_bytes() == published.read_bytes()
    assert dests(hub, "IBNODES.555") == ["02", "03", "04"]
    assert download(hub, "B", "IBNODES.555").content == published.read_bytes()

    # Unchanged, it is not queued again.
    db = hub["Session"]()
    try:
        db.query(Packet).filter(Packet.filename == "IBNODES.555").update(
            {"is_downloaded": True})
        db.commit()
    finally:
        db.close()
    IB.generate_nodelist(hub["db"], hub["config"], hub["league"])
    db = hub["Session"]()
    try:
        assert all(p.is_downloaded for p in
                   db.query(Packet).filter(Packet.filename == "IBNODES.555"))
    finally:
        db.close()


def test_a_board_key_is_64_hex_or_nothing():
    assert valid_board_key(KEY_C.upper()) == KEY_C
    assert valid_board_key("") is None
    assert valid_board_key(None) is None
    with pytest.raises(ValueError):
        valid_board_key("not a key")


def test_an_admin_sets_a_boards_key_and_only_in_an_ib_league(hub):
    db = hub["Session"]()
    try:
        m = db.query(LeagueMembership).filter(LeagueMembership.bbs_index == 2).one()
        league_id, membership_id = m.league_id, m.id
    finally:
        db.close()
    url = f"/management/api/v1/leagues/{league_id}/members/{membership_id}"
    r = hub["http"].patch(url, json={"board_key": "nope"})
    assert r.status_code == 400
    r = hub["http"].patch(url, json={"board_key": KEY_C.upper()})
    assert r.status_code == 200, r.text
    assert r.json()["board_key"] == KEY_C
    r = hub["http"].patch(url, json={"board_key": ""})
    assert r.json()["board_key"] is None

    db = hub["Session"]()
    try:
        bre = League(league_id="556", game_type="B", name="BRE 556")
        db.add(bre)
        db.flush()
        m = LeagueMembership(client_id=hub["boards"]["A"].id, league_id=bre.id, bbs_index=2)
        db.add(m)
        db.commit()
        url = f"/management/api/v1/leagues/{bre.id}/members/{m.id}"
    finally:
        db.close()
    assert hub["http"].patch(url, json={"board_key": KEY_C}).status_code == 400


def test_an_ib_league_has_no_reset_editor(hub):
    r = hub["http"].get(f"/management/api/v1/leagues/{hub['league'].id}/reset")
    assert r.status_code == 404


# -- client config ---------------------------------------------------------------

def test_a_members_config_runs_the_game_natively():
    entry = LeagueEntry("IB", "555", 3, "I")
    client = Client(client_id="board_b", bbs_name="Board B")
    config = tomllib.loads(render_config_toml(client, "s3cret", "https://hub", [entry]))
    league = config["leagues"]["IB"]["555"]
    assert league["runner"] == "native"
    assert league["bbs_index"] == 3
    assert league["maintenance_args"].startswith("-maint -data ")
    assert "dosemu" not in league["game_folder"]

    psd1 = render_config_psd1(client, None, "https://hub", [entry])
    assert "immortal-barons.exe" in psd1 and "PLANETARY" not in psd1


def test_an_sr_entry_without_a_letter_still_finds_its_provider():
    assert LeagueEntry("BRE", "015", 2).provider is provider_for("B")


# -- retention -------------------------------------------------------------------

def _aged(path: Path, data: bytes, days: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old = path.stat().st_mtime - days * 86400
    os.utime(path, (old, old))


def test_retention_prunes_old_ib_packets_but_not_one_still_waiting(hub):
    data = hub["data"]
    processed = data / "packets" / "processed" / ib_name(2, 1, 0).upper()
    delivered = data / "packets" / "outbound" / ib_name(2, 2, 3).upper()
    waiting = data / "packets" / "outbound" / ib_name(2, 3, 4).upper()
    recent = data / "packets" / "processed" / ib_name(2, 4, 0).upper()
    bre = data / "packets" / "processed" / "555B0201.001"
    for path in (processed, delivered, waiting, bre):
        _aged(path, b"x", days=90)
    _aged(recent, b"x", days=1)

    db = hub["Session"]()
    try:
        from backend.core.clock import utcnow
        db.add_all([
            Packet(filename=delivered.name, league_id=hub["league"].id, source_bbs_index="02",
                   dest_bbs_index="03", sequence_number=2, file_size=1,
                   downloaded_at=utcnow()),
            Packet(filename=waiting.name, league_id=hub["league"].id, source_bbs_index="02",
                   dest_bbs_index="04", sequence_number=3, file_size=1),
        ])
        db.commit()
        deleted, _ = RetentionService(db, 30, data_dir=data).purge_packet_files()
    finally:
        db.close()

    assert deleted == 2
    assert not processed.exists() and not delivered.exists()
    assert waiting.exists() and recent.exists()
    assert bre.exists()  # a wrapping name bounds itself; never touched


def test_native_run_logs_age_out_with_dosemus(tmp_path):
    native = tmp_path / "logs" / "native" / "555_ib_20260101_000000.log"
    _aged(native, b"log", days=90)
    deleted, _ = RetentionService(None, 30, data_dir=tmp_path).purge_logs()
    assert deleted == 1 and not native.exists()


def test_the_provider_flags_say_what_the_sr_machinery_skips():
    assert isinstance(IB, ImmortalBaronsProvider)
    assert (IB.checks_sequences, IB.reuses_names, IB.reads_transcripts) == (False, False, False)
    sr = provider_for("B")
    assert (sr.checks_sequences, sr.reuses_names, sr.reads_transcripts) == (True, True, True)
