"""The test hub: its config, its database, and a client that talks to it.

The hub is driven in-process rather than as a subprocess. Processing is an async
service call, not an HTTP endpoint, so a scenario needs to await it and then look
at the same database -- and an in-process TestClient gives both without the
polling and teardown races a real uvicorn would add.
"""
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from rig.layout import (
    COMPLETION_MARKER, DOSEMU_CONF, HUB_INDEX, LEAGUES, NODES, League, Node,
)

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def hub_config(data_dir: Path, term: str = "linux", leagues=None,
               inbound_override=None) -> dict:
    """Config in the shape backend/ expects, pointed at the rig's installs.

    `term="dumb"` is the forced-failure lever: dosemu refuses to start and the
    run must be recorded as failed. `inbound_override` breaks one league's inbound
    path the way production's fe_015 is broken, for the R-3 scenario.
    """
    hub = NODES[HUB_INDEX]
    dosemu = {
        "dosemu_path": "/usr/bin/dosemu",
        "config_dir": str(data_dir / "dosemu_configs"),
        "capture_output": True,
        "timeout": 300,
        "term": term,
    }

    for league_id in (leagues or LEAGUES):
        league = LEAGUES[league_id]
        install = league.install_path(hub)
        inbound = install / "INBOUND"
        if inbound_override and league_id in inbound_override:
            inbound = Path(inbound_override[league_id])
        dosemu.setdefault(league.number, {})[
            "bre" if league.game == "B" else "fe"
        ] = {
            "game_folder": str(install),
            "game_dos_path": league.dos_path(hub),
            "inbound_folder": str(inbound),
            "outbound_folder": str(install / "OUTBOUND"),
            # BRE SCORES writes its .ANS bulletins into BULLETIN/, not the game
            # folder. Point somewhere else and the hub runs the command happily
            # and then ingests nothing at all.
            "scores_folder": str(install / "BULLETIN"),
            "processing_command": "BRE.EXE PLANETARY /DETAILED",
            "scores_command": "BRE.EXE SCORES",
            "completion_marker": COMPLETION_MARKER,
        }

    return {
        "server": {"host": "127.0.0.1", "port": 8900, "data_dir": str(data_dir)},
        "hub": {"bbs_name": hub.name, "bbs_index": f"{HUB_INDEX:02d}"},
        "processing": {"poll_interval": 3600, "retention_days": 30},
        "dosemu": dosemu,
        "database": {"path": str(data_dir / "nova-hub.db")},
        "security": {
            "jwt_secret": "rig-only-secret-not-used-anywhere-real-0123456789",
            "jwt_expiry_hours": 24,
            "max_upload_size_bytes": 10485760,
            "min_password_length": 12,
            "cookie_secure": False,
        },
        "rate_limiting": {"enabled": False},
        "alerting": {"enabled": False},
    }


def make_data_dir(root: Path) -> Path:
    """A fresh hub data dir: empty packet spool, empty dosemu log dir."""
    data = root / "nova-data"
    if data.exists():
        shutil.rmtree(data)
    for sub in ("packets/inbound", "packets/outbound", "packets/processed",
                "logs/dosemu", "dosemu_configs"):
        (data / sub).mkdir(parents=True)
    return data


class TestHub:
    """A hub instance with its own database, wired to the rig's game installs."""

    def __init__(self, data_dir: Path, config: dict):
        self.data_dir = Path(data_dir)
        self.config = config
        self._engine = None
        self._session = None

    # -- database ---------------------------------------------------------

    def start(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models.database import Base

        self._engine = create_engine(
            f"sqlite:///{self.data_dir / 'nova-hub.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self._engine)
        self._session = sessionmaker(bind=self._engine)()
        return self

    def stop(self):
        if self._session:
            self._session.close()
        if self._engine:
            self._engine.dispose()

    @property
    def db(self):
        return self._session

    # -- seeding ----------------------------------------------------------

    def seed(self, league_ids=None):
        """Register the leagues and their member clients.

        Returns {bbs_index: (client_id, client_secret)} for the non-hub nodes, so
        a scenario can authenticate as a real BBS.
        """
        from backend.models.database import Client, League as LeagueRow, LeagueMembership

        creds = {}
        for league_id in (league_ids or LEAGUES):
            league = LEAGUES[league_id]
            row = LeagueRow(
                league_id=league.number,
                game_type=league.game,
                name=f"Rig {league.league_id}",
                is_active=True,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)

            for index in league.members:
                if index == HUB_INDEX:
                    continue
                node = NODES[index]
                client = (
                    self.db.query(Client)
                    .filter(Client.client_id == node.user)
                    .first()
                )
                if client is None:
                    client = Client(
                        client_id=node.user,
                        client_secret=f"{node.user}-secret",
                        bbs_name=node.name,
                        is_active=True,
                    )
                    self.db.add(client)
                    self.db.commit()
                    self.db.refresh(client)
                creds[index] = (client.client_id, f"{node.user}-secret")

                self.db.add(LeagueMembership(
                    client_id=client.id,
                    league_id=row.id,
                    bbs_index=index,
                    fidonet_address=league.fido_for(index),
                    is_active=True,
                ))
            self.db.commit()
        return creds

    # -- actions ----------------------------------------------------------

    async def process(self):
        """Run one processing batch, exactly as the scheduler would."""
        from backend.services.processing_service import ProcessingService

        service = ProcessingService(self.db, self.config)
        await service.process_batch()
        self.db.expire_all()
        return service

    def deliver(self, filename: str, payload: bytes):
        """Put a packet in the hub's inbound spool and register it.

        Bypasses HTTP on purpose: uploading is nova-client's job and is covered by
        the existing API tests. What the rig is here to exercise is what the hub
        does with a packet once it has one.
        """
        from backend.models.database import League as LeagueRow, Packet
        from backend.services.packet_service import parse_packet_filename

        info = parse_packet_filename(filename)
        assert info, f"{filename!r} is not a valid packet name"

        league_row = (
            self.db.query(LeagueRow)
            .filter(
                LeagueRow.league_id == info["league_id"],
                LeagueRow.game_type == info["game_type"],
            )
            .first()
        )
        assert league_row, f"league {info['league_id']}{info['game_type']} not seeded"

        (self.data_dir / "packets" / "inbound" / filename).write_bytes(payload)
        packet = Packet(
            filename=filename,
            league_id=league_row.id,
            source_bbs_index=info["source_bbs_index"],
            dest_bbs_index=info["dest_bbs_index"],
            sequence_number=info["sequence_number"],
            file_size=len(payload),
            file_data=None,
        )
        self.db.add(packet)
        self.db.commit()
        self.db.refresh(packet)
        return packet

    # -- inspection -------------------------------------------------------

    def runs(self):
        from backend.models.database import ProcessingRun
        return self.db.query(ProcessingRun).order_by(ProcessingRun.id).all()

    def last_run(self):
        runs = self.runs()
        return runs[-1] if runs else None

    def spool(self, which: str):
        d = self.data_dir / "packets" / which
        return sorted(p.name for p in d.glob("*")) if d.is_dir() else []

    def game_inbound(self, league_id: str):
        install = LEAGUES[league_id].install_path(NODES[HUB_INDEX])
        return sorted(p.name for p in (install / "INBOUND").glob("*"))

    def game_outbound(self, league_id: str):
        install = LEAGUES[league_id].install_path(NODES[HUB_INDEX])
        return sorted(p.name for p in (install / "OUTBOUND").glob("*"))

    def reconfigure(self, **config_kwargs):
        """Rebuild the config on the same data dir and database.

        Models an operator fixing config and restarting the service: the spool
        and the run history stay exactly as the previous run left them, which is
        the whole point when testing recovery.
        """
        self.config = hub_config(self.data_dir, **config_kwargs)
        return self

    def transcript(self, run=None):
        """The parsed dosemu transcript for a run."""
        from rig.transcript import Transcript

        run = run or self.last_run()
        return Transcript((run.dosemu_log or "").encode("utf-8", "replace"))


@contextmanager
def test_hub(root: Path, **config_kwargs):
    data_dir = make_data_dir(root)
    hub = TestHub(data_dir, hub_config(data_dir, **config_kwargs)).start()
    try:
        yield hub
    finally:
        hub.stop()
