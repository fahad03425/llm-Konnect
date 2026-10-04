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
from app.core.llm import LLMService, llm
from app.core.config import settings


@pytest.fixture(autouse=True)
def preserve_active_model(monkeypatch, tmp_path):
    """Preserve model state across test runs without mutating user files."""
    initial_model = llm.model
    initial_settings_model = settings.llm_model
    test_settings_path = tmp_path / "model_settings.json"
    
    monkeypatch.setattr(llm, "model", initial_model)
    monkeypatch.setattr(settings, "llm_model", initial_settings_model)
    monkeypatch.setattr(LLMService, "_model_settings_path", staticmethod(lambda: test_settings_path))
    
    yield
    
    llm.model = initial_model
    settings.llm_model = initial_settings_model


@pytest.fixture
def test_client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


@pytest.fixture
def llm_service(tmp_path, monkeypatch):
    """Isolated LLMService fixture with temporary model_settings path."""
    test_settings_path = tmp_path / "model_settings.json"
    monkeypatch.setattr(LLMService, "_model_settings_path", staticmethod(lambda: test_settings_path))
    monkeypatch.setattr(settings, "llm_model", "phi4-mini")
    service = LLMService()
    return service
