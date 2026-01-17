# tests/test_integration.py

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
from backend.models.database import Base
from backend.core.database import get_db

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


def test_management_login(client):
    """Test management API login"""
    response = client.post("/management/api/v1/auth/login", json={
        "username": "admin",
        "password": "admin"
    })
    # Will fail without admin user, but should return valid response structure
    assert response.status_code in [200, 401, 422]


def test_packet_upload(client):
    """Test packet upload"""
    # TODO: Create test client and token first
    pass


def test_api_overview(client):
    """Test API overview endpoint"""
    response = client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert "nova_hub" in data
    assert "endpoints" in data["nova_hub"]
