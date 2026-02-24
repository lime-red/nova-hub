# tests/test_client_schemas.py - Validation tests for client schemas

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.schemas.clients import ClientCreate, ClientUpdate


def test_client_create_rejects_newlines_in_bbs_name():
    with pytest.raises(ValidationError):
        ClientCreate(bbs_name="Bad\nName", client_id="client-1")


def test_client_create_rejects_control_chars_in_bbs_name():
    with pytest.raises(ValidationError):
        ClientCreate(bbs_name="Bad\x01Name", client_id="client-1")


def test_client_update_accepts_none_bbs_name():
    model = ClientUpdate(bbs_name=None, is_active=True)
    assert model.bbs_name is None
