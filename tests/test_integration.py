# tests/test_integration.py

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
from app.database import Base, get_db

# Test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    yield TestClient(app)
    Base.metadata.drop_all(bind=engine)


def test_health_check(client):
    """Test health endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in ["healthy", "degraded"]


def test_login(client):
    """Test login"""
    response = client.post("/login", data={
        "username": "admin",
        "password": "admin"
    })
    assert response.status_code == 200 or response.status_code == 302


def test_packet_upload(client):
    """Test packet upload"""
    # TODO: Create test client and token first
    pass


def test_websocket_connection(client):
    """Test WebSocket connection"""
    with client.websocket_connect("/ws/dashboard") as websocket:
        data = websocket.receive_json()
        assert data["type"] == "initial_stats"
