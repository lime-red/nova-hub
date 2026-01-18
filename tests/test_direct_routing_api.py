import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pathlib import Path
import os
from datetime import datetime

from main import app
from backend.core.database import get_db, init_database
from backend.models.database import Client, League, LeagueMembership, Packet, Base
from backend.core.security import create_access_token

@pytest.fixture
def api_client(tmp_path):
    db_file = tmp_path / "test_api.db"
    db_url = f"sqlite:///{db_file}"
    engine = init_database(db_url)
    
    Base.metadata.create_all(bind=engine)
    
    from backend.core.database import get_session
    db = get_session()
    
    # Create a test client
    hub_client = Client(client_id="test_bbs", client_secret="secret", bbs_name="Test BBS")
    db.add(hub_client)
    db.commit()
    db.refresh(hub_client)
    
    # Create a league
    league = League(league_id="555", game_type="B", name="BRE League 555", is_active=True)
    db.add(league)
    db.commit()
    db.refresh(league)
    
    # Create membership for BBS 03
    membership = LeagueMembership(client_id=hub_client.id, league_id=league.id, bbs_index=3, is_active=True)
    db.add(membership)
    db.commit()
    
    token = create_access_token(data={"sub": hub_client.client_id})
    
    yield TestClient(app), token, hub_client, league
    
    db.close()

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
        
        # Add packet record to DB
        from backend.core.database import get_session
        db = get_session()
        packet = Packet(
            filename=filename,
            league_id=league.id,
            source_bbs_index="02",
            dest_bbs_index="03",
            sequence_number=1,
            file_data=content,
            file_size=len(content),
            checksum="hash",
            is_processed=True,
            uploaded_at=datetime.utcnow()
        )
        db.add(packet)
        db.commit()
        
        # Try to download
        response = client.get(
            f"/service/api/v1/leagues/555B/packets/{filename}",
            headers={"Authorization": f"Bearer {token}"}
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
        
        # Add packet record to DB
        from backend.core.database import get_session
        db = get_session()
        packet = Packet(
            filename=filename,
            league_id=league.id,
            source_bbs_index="02",
            dest_bbs_index="04",
            sequence_number=1,
            file_data=content,
            file_size=len(content),
            checksum="hash",
            is_processed=True,
            uploaded_at=datetime.utcnow()
        )
        db.add(packet)
        db.commit()
        
        # Try to download
        response = client.get(
            f"/service/api/v1/leagues/555B/packets/{filename}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 403
        db.close()
        
    finally:
        config.server.data_dir = old_data_dir
        config._raw["server"] = old_raw_server