"""Pytest configuration and shared fixtures for Module 6.4 test suites."""

import sys
from pathlib import Path
import shutil
import pytest

# Ensure backend root is on sys.path
backend_path = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from app.ingestion.store import KnowledgeBase
from app.ingestion.registry import FileRegistry
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def temp_chroma_dir(tmp_path):
    """Provides an isolated directory for temporary ChromaDB vector storage."""
    chroma_path = tmp_path / "test_chroma"
    chroma_path.mkdir(parents=True, exist_ok=True)
    yield str(chroma_path)
    shutil.rmtree(str(chroma_path), ignore_errors=True)


@pytest.fixture
def temp_registry_db(tmp_path):
    """Provides an isolated SQLite database path for FileRegistry."""
    db_file = tmp_path / "test_file_registry.sqlite3"
    registry = FileRegistry(db_path=str(db_file))
    yield registry


@pytest.fixture
def isolated_kb(temp_chroma_dir):
    """Provides an isolated KnowledgeBase instance using a temporary Chroma directory."""
    kb = KnowledgeBase(
        chroma_dir=temp_chroma_dir,
        collection_name="test_llm_konnect_kb"
    )
    yield kb
    try:
        kb.clear_collection()
    except Exception:
        pass


@pytest.fixture
def test_client():
    """Provides a Starlette/FastAPI TestClient for API endpoint integration tests."""
    with TestClient(app) as client:
        yield client
