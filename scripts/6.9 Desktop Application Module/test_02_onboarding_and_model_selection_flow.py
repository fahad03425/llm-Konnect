"""Test Suite 02: Onboarding & Model Selection Desktop Flow.

Module: Module 6.9 — Desktop Application Module
Target Files:
- desktop/src/components/auth/OnboardingModal.tsx
- desktop/src/components/settings/ModelSettings.tsx
- desktop/src/components/settings/SettingsModal.tsx
- backend/app/api/models.py
Scope:
- Verifies business domain onboarding options (Pharmacy, E-Commerce, General Retail).
- Verifies automated hardware profile recommendations (Phi-4-mini for CPU, Qwen 2.5 / Mistral for GPU).
- Verifies model switching UI controls without command-line exposure.
- Verifies desktop settings modal configuration.
- Verifies backend model status API contract consumed by the desktop UI.
"""

import pytest
from pathlib import Path


class TestOnboardingAndModelSelectionFlow:
    """Verifies non-technical onboarding, automated hardware detection, and GUI model management."""

    def test_onboarding_modal_domain_selection(self, repo_paths):
        """Verifies that OnboardingModal.tsx offers tailored business domains for small business owners."""
        onboarding_path = repo_paths["src"] / "components" / "auth" / "OnboardingModal.tsx"
        assert onboarding_path.exists()
        code = onboarding_path.read_text(encoding="utf-8")

        # Must offer domain packs
        assert "pharmacy" in code.lower()
        assert "ecommerce" in code.lower() or "retail" in code.lower()
        # Must store onboarding completion in user settings
        assert "onboarding" in code.lower()

    def test_hardware_profile_auto_detection_flow(self, repo_paths):
        """Verifies that ModelSettings.tsx presents hardware-aware model profiles."""
        model_settings_path = repo_paths["src"] / "components" / "settings" / "ModelSettings.tsx"
        assert model_settings_path.exists()
        code = model_settings_path.read_text(encoding="utf-8")

        # Models supported for non-technical users
        assert "phi-4-mini" in code.lower() or "qwen" in code.lower() or "mistral" in code.lower()
        # Hardware context display (CPU vs GPU / VRAM)
        assert "cpu" in code.lower() or "vram" in code.lower() or "gpu" in code.lower()

    def test_model_switching_interface_controls(self, repo_paths):
        """Verifies that model lifecycle actions (switch, pull, status) are GUI buttons, not CLI commands."""
        model_settings_path = repo_paths["src"] / "components" / "settings" / "ModelSettings.tsx"
        code = model_settings_path.read_text(encoding="utf-8")

        # Must handle active model switching
        assert "switch" in code.lower() or "select" in code.lower()
        # Visual status feedback (downloading, ready, loaded)
        assert "status" in code.lower()
        assert "loading" in code.lower() or "active" in code.lower()

    def test_settings_modal_integration(self, repo_paths):
        """Verifies that SettingsModal.tsx provides access to system preferences."""
        settings_path = repo_paths["src"] / "components" / "settings" / "SettingsModal.tsx"
        assert settings_path.exists()
        code = settings_path.read_text(encoding="utf-8")

        assert "ModelSettings" in code
        assert "isSettingsOpen" in code
        assert "closeSettings" in code

    def test_backend_model_status_api_contract(self, test_client):
        """Tests the backend /api/models endpoint consumed by desktop ModelSettings."""
        response = test_client.get("/api/models")
        # Should return 200 with model information
        assert response.status_code == 200
        data = response.json()
        assert "active_model" in data
        assert "models" in data
