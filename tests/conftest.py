# tests/conftest.py - Shared fixtures for all test modules

import os
import sys
import tempfile
from pathlib import Path

import pytest
import toml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))


def _test_config() -> str:
    """A config for the app under test, built from config.toml.example.

    main.py reads its config when imported. Without this it read config.toml
    from the working directory: absent on the CI runner, so every test that
    imports the app failed to collect there, and on a dev box whatever that
    box happened to have (a public_url, real data paths) leaked into results.
    """
    root = Path(tempfile.mkdtemp(prefix="nova-hub-tests-"))
    config = toml.load(Path(__file__).parent.parent / "config.toml.example")
    config["server"]["data_dir"] = str(root / "data")
    config["database"]["path"] = str(root / "data" / "nova-hub.db")
    config["games"] = {"root": str(root / "drive_c" / "leagues"), "drive_c": str(root / "drive_c")}
    (root / "data").mkdir()
    path = root / "config.toml"
    path.write_text(toml.dumps(config))
    return str(path)


# Before anything below imports the app. The live rig sets its own.
os.environ.setdefault("NOVA_HUB_CONFIG", _test_config())

from backend.models.database import Base, Client, FtnAddress, League, LeagueMembership, SysopUser


def install_game(db, config: dict, league, drive_c: Path, *, state: str = "ready",
                 binary: Path = None, hub_board_key: str = None) -> Path:
    """Give `league` a hub game the way the hub keeps one: a league_games row,
    and its folder (B555, I902) in drive_c/leagues, which `config` [games]
    is pointed at. For IB, `binary` becomes the image the game runs.
    Returns the folder."""
    from backend.models.database import GameImage, LeagueGame
    from backend.services.league_games import IB_BINARY, folder_name, games_config
    from backend.services.game_images import write_manifest

    root = Path(drive_c) / "leagues"
    config.setdefault("games", {}).update({"root": str(root), "drive_c": str(drive_c)})
    folder = root / folder_name(league)
    image = None
    if binary is not None:
        images = games_config(config).images
        dirname = f"ib-test-{league.full_id}"
        (images / dirname).mkdir(parents=True, exist_ok=True)
        target = images / dirname / IB_BINARY
        if not target.exists():
            target.symlink_to(Path(binary).resolve())
        image = GameImage(game_type=league.game_type, version="test", dirname=dirname,
                          manifest_sha256=write_manifest(images / dirname), source="test")
        db.add(image)
        db.flush()
    db.add(LeagueGame(league_id=league.id, folder=folder.name, state=state,
                      image_id=image.id if image else None, hub_board_key=hub_board_key))
    db.commit()
    db.refresh(league)
    subdirs = ("INBOUND", "OUTBOUND", "BULLETIN") if league.game_type != "I" else (
        "inbound", "outbound")
    for sub in subdirs:
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_league(db_session):
    league = League(
        league_id="555",
        game_type="B",
        name="BRE League 555",
        is_active=True,
    )
    db_session.add(league)
    db_session.commit()
    db_session.refresh(league)
    return league


@pytest.fixture
def sample_client(db_session):
    client = Client(
        client_id="test-client-001",
        client_secret="secret",
        bbs_name="Test BBS",
        is_active=True,
    )
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)
    return client


@pytest.fixture
def sample_membership(db_session, sample_league, sample_client):
    membership = LeagueMembership(
        client_id=sample_client.id,
        league_id=sample_league.id,
        bbs_index=2,
        ftn_address=FtnAddress(client_id=sample_client.id, address="13:10/102"),
        is_active=True,
    )
    db_session.add(membership)
    db_session.commit()
    db_session.refresh(membership)
    return membership
