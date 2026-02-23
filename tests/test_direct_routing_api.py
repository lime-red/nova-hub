import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, service_app, management_app
from backend.core.database import get_db
from backend.models.database import Base, Client, League, LeagueMembership, Packet
from backend.core.security import create_service_token


@pytest.fixture
def api_client(tmp_path):
    """
    Set up an isolated in-memory DB with DI override, then tear it down.
    The override temporarily replaces whatever test_integration.py set at module level.
    The global SessionLocal is also redirected so get_session() calls in test bodies
    see the same data the API sees.
    """
    import backend.core.database as _db_mod

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Redirect global SessionLocal so get_session() uses the test DB too
    saved_engine = _db_mod.engine
    saved_session_local = _db_mod.SessionLocal
    _db_mod.engine = test_engine
    _db_mod.SessionLocal = TestSession

    # Save and replace DI overrides (test_integration.py sets these at module level)
    saved_overrides = {
        app: app.dependency_overrides.get(get_db),
        service_app: service_app.dependency_overrides.get(get_db),
        management_app: management_app.dependency_overrides.get(get_db),
    }
    app.dependency_overrides[get_db] = override_get_db
    service_app.dependency_overrides[get_db] = override_get_db
    management_app.dependency_overrides[get_db] = override_get_db

    db = TestSession()
    hub_client = Client(
        client_id="test_bbs",
        client_secret="secret_hash",
        bbs_name="Test BBS",
        is_active=True,
    )
    db.add(hub_client)
    db.commit()
    db.refresh(hub_client)

    league = League(league_id="555", game_type="B", name="BRE League 555", is_active=True)
    db.add(league)
    db.commit()
    db.refresh(league)

    membership = LeagueMembership(
        client_id=hub_client.id,
        league_id=league.id,
        bbs_index=3,
        is_active=True,
    )
    db.add(membership)
    db.commit()

    # Use HTTPS base URL so Secure cookies are forwarded.
    # Use create_service_token so the "type": "service" claim is present.
    token = create_service_token(hub_client.client_id)

    yield TestClient(app, base_url="https://testserver"), token, hub_client, league

    db.close()
    Base.metadata.drop_all(bind=test_engine)

    # Restore global SessionLocal and engine
    _db_mod.engine = saved_engine
    _db_mod.SessionLocal = saved_session_local

    # Restore DI overrides
    for fa_app, saved in saved_overrides.items():
        if saved is None:
            fa_app.dependency_overrides.pop(get_db, None)
        else:
            fa_app.dependency_overrides[get_db] = saved


def test_download_direct_routed_packet(api_client, tmp_path):
    client, token, hub_client, league = api_client

    # Set up data dir for the test
    from backend.core.config import get_config
    config = get_config()
    old_data_dir = config.server.data_dir
    old_raw_server = config._raw.get("server", {}).copy()

    config.server.data_dir = str(tmp_path)
    if "server" not in config._raw:
        config._raw["server"] = {}
    config._raw["server"]["data_dir"] = str(tmp_path)

    try:
        outbound_dir = tmp_path / "packets" / "outbound"
        outbound_dir.mkdir(parents=True, exist_ok=True)

        filename = "555B0203.001"
        content = b"direct routed content"
        packet_file = outbound_dir / filename
        packet_file.write_bytes(content)

        # Add packet record via the DI-overridden DB
        from backend.core.database import get_session
        db = get_session()
        packet = Packet(
            filename=filename,
            league_id=league.id,
            source_bbs_index="02",
            dest_bbs_index="03",
            sequence_number=1,
            file_size=len(content),
            checksum="hash",
            is_processed=True,
            uploaded_at=datetime.utcnow(),
        )
        db.add(packet)
        db.commit()

        # Try to download
        response = client.get(
            f"/service/api/v1/leagues/555B/packets/{filename}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.content == content

        # Verify it's marked as downloaded
        db.refresh(packet)
        assert packet.is_downloaded is True
        db.close()

    finally:
        config.server.data_dir = old_data_dir
        config._raw["server"] = old_raw_server


def test_download_unauthorized_packet(api_client, tmp_path):
    client, token, hub_client, league = api_client

    # Set up data dir for the test
    from backend.core.config import get_config
    config = get_config()
    old_data_dir = config.server.data_dir
    old_raw_server = config._raw.get("server", {}).copy()

    config.server.data_dir = str(tmp_path)
    if "server" not in config._raw:
        config._raw["server"] = {}
    config._raw["server"]["data_dir"] = str(tmp_path)

    try:
        outbound_dir = tmp_path / "packets" / "outbound"
        outbound_dir.mkdir(parents=True, exist_ok=True)

        # Packet for BBS 04, but client is BBS 03
        filename = "555B0204.001"
        content = b"private content"
        packet_file = outbound_dir / filename
        packet_file.write_bytes(content)

        # Add packet record via the DI-overridden DB
        from backend.core.database import get_session
        db = get_session()
        packet = Packet(
            filename=filename,
            league_id=league.id,
            source_bbs_index="02",
            dest_bbs_index="04",
            sequence_number=1,
            file_size=len(content),
            checksum="hash",
            is_processed=True,
            uploaded_at=datetime.utcnow(),
        )
        db.add(packet)
        db.commit()

        # Try to download — should be 403 (wrong dest BBS)
        response = client.get(
            f"/service/api/v1/leagues/555B/packets/{filename}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
        db.close()

    finally:
        config.server.data_dir = old_data_dir
        config._raw["server"] = old_raw_server
