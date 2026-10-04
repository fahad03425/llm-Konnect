"""Pytest configuration and shared fixtures for Module 6.9 Desktop Application tests."""

import sys
import json
from pathlib import Path
import pytest

# Ensure backend root is on sys.path
scripts_dir = Path(__file__).resolve().parent
repo_root = scripts_dir.parent.parent
backend_path = repo_root / "backend"
desktop_path = repo_root / "desktop"

if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def repo_paths():
    """Provides validated repository and desktop application directory paths."""
    return {
        "repo_root": repo_root,
        "desktop": desktop_path,
        "src": desktop_path / "src",
        "src_tauri": desktop_path / "src-tauri",
        "dist": desktop_path / "dist",
        "backend": backend_path,
    }


@pytest.fixture
def tauri_config(repo_paths):
    """Loads and parses the official Tauri application configuration (tauri.conf.json)."""
    conf_path = repo_paths["src_tauri"] / "tauri.conf.json"
    assert conf_path.exists(), f"Missing Tauri config at {conf_path}"
    with open(conf_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def package_json(repo_paths):
    """Loads and parses desktop package.json manifest."""
    pkg_path = repo_paths["desktop"] / "package.json"
    assert pkg_path.exists(), f"Missing package.json at {pkg_path}"
    with open(pkg_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def built_dist_assets(repo_paths):
    """Verifies and provides production bundle assets from desktop/dist."""
    dist_dir = repo_paths["dist"]
    index_html = dist_dir / "index.html"
    assets_dir = dist_dir / "assets"
    return {
        "dist_exists": dist_dir.exists(),
        "index_html": index_html,
        "index_exists": index_html.exists(),
        "assets_dir": assets_dir,
        "assets": list(assets_dir.glob("*")) if assets_dir.exists() else [],
    }


@pytest.fixture
def test_client():
    """FastAPI TestClient simulating desktop HTTP bridge to local background engine."""
    with TestClient(app) as client:
        yield client
