import os
import sys
from pathlib import Path
import pytest

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings


@pytest.fixture
def test_client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


@pytest.fixture
def temp_vault_dir(tmp_path, monkeypatch):
    """Isolate vault credentials and storage in a temporary directory."""
    temp_storage = tmp_path / "storage"
    temp_storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "storage_dir", str(temp_storage))
    return temp_storage
