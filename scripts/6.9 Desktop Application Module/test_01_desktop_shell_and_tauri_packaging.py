"""Test Suite 01: Desktop Application Shell & Tauri Packaging.

Module: Module 6.9 — Desktop Application Module
Target Files:
- desktop/src-tauri/tauri.conf.json
- desktop/package.json
- desktop/src/Shell.tsx & WindowTitleBar.tsx
- desktop/src/main.tsx (GlobalErrorBoundary)
- desktop/dist (Production Build Validation)
Scope:
- Verifies Tauri v2 desktop window specifications (1280x800, min 900x600, decorations: false).
- Verifies package.json dependencies and bundling configuration.
- Verifies production dist artifacts (index.html, JS chunks, CSS assets).
- Verifies custom WindowTitleBar IPC window controls (minimize, maximize, close).
- Verifies GlobalErrorBoundary desktop crash protection.
"""

import json
from pathlib import Path
import pytest


class TestDesktopShellAndTauriPackaging:
    """Verifies Tauri window configuration, build artifacts, and desktop window shell controls."""

    def test_tauri_window_specifications(self, tauri_config):
        """Verifies desktop window dimensions, resizability, and frameless decorations."""
        assert "app" in tauri_config
        windows = tauri_config["app"]["windows"]
        assert len(windows) >= 1
        main_win = windows[0]

        assert main_win["title"] == "LLM-Konnect"
        assert main_win["width"] == 1280
        assert main_win["height"] == 800
        assert main_win["minWidth"] == 900
        assert main_win["minHeight"] == 600
        assert main_win["resizable"] is True
        # Custom titlebar requires decorations to be disabled
        assert main_win["decorations"] is False

    def test_tauri_build_configuration(self, tauri_config):
        """Verifies frontend build paths and local development URLs."""
        build = tauri_config["build"]
        assert build["frontendDist"] == "../dist"
        assert build["devUrl"] == "http://localhost:5173"
        assert "npm run build" in build["beforeBuildCommand"]

    def test_package_json_dependencies(self, package_json):
        """Verifies essential desktop frontend libraries: React 19, Tauri API, Recharts, Router."""
        deps = package_json.get("dependencies", {})
        dev_deps = package_json.get("devDependencies", {})

        # Core runtime requirements
        assert "@tauri-apps/api" in deps
        assert "react" in deps
        assert "react-dom" in deps
        assert "react-router-dom" in deps
        assert "recharts" in deps
        assert "lucide-react" in deps

        # Build tools
        assert "@tauri-apps/cli" in dev_deps
        assert "vite" in dev_deps
        assert "typescript" in dev_deps

    def test_production_dist_artifacts(self, built_dist_assets):
        """Verifies that production build output exists and is non-empty."""
        assert built_dist_assets["dist_exists"] is True
        assert built_dist_assets["index_exists"] is True

        index_file = built_dist_assets["index_html"]
        content = index_file.read_text(encoding="utf-8")
        assert "<!doctype html>" in content.lower()
        assert 'id="root"' in content

        # Assets bundle check
        assets = built_dist_assets["assets"]
        assert len(assets) >= 2  # At least 1 JS chunk and 1 CSS asset
        extensions = {a.suffix for a in assets}
        assert ".js" in extensions
        assert ".css" in extensions

    def test_custom_window_title_bar_ipc(self, repo_paths):
        """Verifies that WindowTitleBar.tsx integrates Tauri window controls without native OS chrome."""
        title_bar_path = repo_paths["src"] / "components" / "WindowTitleBar.tsx"
        assert title_bar_path.exists()
        code = title_bar_path.read_text(encoding="utf-8")

        # Must import Tauri window API
        assert "@tauri-apps/api" in code or "window" in code
        # Must implement minimize, maximize, and close interactions
        assert "minimize" in code
        assert "maximize" in code or "toggleMaximize" in code
        assert "close" in code

    def test_global_error_boundary_resilience(self, repo_paths):
        """Verifies that main.tsx wraps the desktop application in a crash-resilient boundary."""
        main_path = repo_paths["src"] / "main.tsx"
        assert main_path.exists()
        code = main_path.read_text(encoding="utf-8")

        assert "GlobalErrorBoundary" in code
        assert "componentDidCatch" in code or "getDerivedStateFromError" in code
        # Provides recovery options for business users
        assert "Clear Wizard Cache" in code or "Reload Window" in code
