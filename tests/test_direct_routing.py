
import pytest
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch, AsyncMock

from backend.models.database import Packet, League, LeagueMembership, Client
from backend.services.processing_service import ProcessingService

# Mock data
HUB_INDEX = "01"
SENDER_INDEX = "02"
RECEIVER_INDEX = "03"
LEAGUE_ID = "555"
GAME_TYPE = "B"
FILENAME = f"555B{SENDER_INDEX}{RECEIVER_INDEX}.001"

@pytest.fixture
def mock_db():
    db = MagicMock()
    # Mock query chain
    db.query.return_value.filter.return_value.first.return_value = None
    return db

@pytest.fixture
def mock_config(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "packets" / "inbound").mkdir(parents=True)
    (data_dir / "packets" / "outbound").mkdir(parents=True)
    (data_dir / "packets" / "processed").mkdir(parents=True)
    (data_dir / "dosemu" / LEAGUE_ID / "bre" / "inbound").mkdir(parents=True)
    (data_dir / "dosemu" / LEAGUE_ID / "bre" / "outbound").mkdir(parents=True)
    
    return {
        "hub": {"bbs_index": HUB_INDEX},
        "server": {"data_dir": str(data_dir)},
        "dosemu": {
            "dosemu_path": "/usr/bin/dosemu",
            "timeout": 60,
            LEAGUE_ID: {
                "bre": {
                    "inbound_folder": str(data_dir / "dosemu" / LEAGUE_ID / "bre" / "inbound"),
                    "outbound_folder": str(data_dir / "dosemu" / LEAGUE_ID / "bre" / "outbound"),
                }
            }
        }
    }

@pytest.mark.asyncio
async def test_process_direct_routing(mock_db, mock_config, tmp_path):
    # Setup
    data_dir = Path(mock_config["server"]["data_dir"])
    inbound_dir = data_dir / "packets" / "inbound"
    outbound_dir = data_dir / "packets" / "outbound"
    
    # Create a test packet file in inbound
    packet_file = inbound_dir / FILENAME
    packet_file.write_text("test packet content")
    
    # Mock DB objects
    packet = MagicMock(spec=Packet)
    packet.filename = FILENAME
    packet.league_id = 1
    packet.source_bbs_index = SENDER_INDEX
    packet.dest_bbs_index = RECEIVER_INDEX
    packet.processed_at = None
    
    league = MagicMock(spec=League)
    league.id = 1
    league.league_id = LEAGUE_ID
    league.game_type = GAME_TYPE
    
    # Mock DB queries
    def query_side_effect(model):
        q = MagicMock()
        if model == League:
            q.filter.return_value.first.return_value = league
        return q
        
    mock_db.query.side_effect = query_side_effect
    
    # Initialize service
    service = ProcessingService(mock_db, mock_config)
    
    # Mock DosemuRunner
    service.dosemu_runner = MagicMock()
    service.dosemu_runner.run_game_process = AsyncMock(return_value={"status": "success"})
    service.ingest_processing_files = AsyncMock() # Skip ingest
    service.collect_outbound_packets = AsyncMock() # Skip collect
    service.scan_outbound_folders = AsyncMock() # Skip scan

    # Run processing
    await service.process_game_batch("BRE", [packet], 1)
    
    # Verify results
    
    # 1. Packet should be archived (this works currently)
    processed_dir = data_dir / "packets" / "processed"
    assert (processed_dir / FILENAME).exists(), "Packet should be archived to processed"
    
    # 2. Packet should be in outbound for the receiver (THIS IS THE BUG)
    assert (outbound_dir / FILENAME).exists(), "Packet should be copied to outbound for direct routing"

    # 3. Packet should (maybe?) be copied to game inbound (this works currently)
    # The test in `process_game_batch` cleans up game inbound, so we can't check it easily 
    # unless we mock the cleanup or check logs. 
    # But checking outbound existence is enough to confirm the fix.

