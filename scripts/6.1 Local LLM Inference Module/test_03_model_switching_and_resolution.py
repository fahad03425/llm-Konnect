"""Test Suite 03: Model Switching, Resolution Hierarchy & Capability Validation.

Module: Module 6.1 — Local LLM Inference Module
Target File: backend/app/core/llm.py (methods: set_active_model, _resolve_model, _match_model, resolve_chat_model, get_status)
Scope:
- Verifies model switching with atomic JSON persistence and thread locking.
- Verifies model capability validation (blocks non-completion/embed-only models).
- Verifies resolution hierarchy (exact tag, :latest alias, quantization suffix, and hardware fallback).
- Verifies language-aware model routing (e.g. Roman Urdu / Urdu script targeting Llama 3.2).
- Verifies connectivity status reporting and offline failure isolation.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from app.core.llm import LLMService


class TestModelSwitchingAndResolution:
    """Verifies model switching, fallback hierarchy, and language routing."""

    def test_switch_active_model_persistence(self, llm_service, tmp_path):
        """Switching active model updates memory state and atomically writes settings JSON."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "mistral:7b"}]}
        mock_client.show.return_value = {"capabilities": ["completion"]}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            switched = llm_service.set_active_model("mistral:7b")
            assert switched == "mistral:7b"
            assert llm_service.model == "mistral:7b"

        # Verify disk persistence
        settings_file = llm_service._model_settings_path()
        assert settings_file.exists()
        persisted_data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert persisted_data.get("active_model") == "mistral:7b"

    def test_switch_model_validation_rejects_non_installed(self, llm_service):
        """Attempting to activate an uninstalled model raises ValueError."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini"}]}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            with pytest.raises(ValueError) as exc_info:
                llm_service.set_active_model("nonexistent-model:99b")
            assert "is not installed" in str(exc_info.value)

    def test_switch_model_validation_rejects_embedding_only_model(self, llm_service):
        """Attempting to activate an embed-only model (e.g. nomic-embed-text) raises ValueError."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "nomic-embed-text:latest"}]}
        mock_client.show.return_value = {"capabilities": ["embedding"]}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            with pytest.raises(ValueError) as exc_info:
                llm_service.set_active_model("nomic-embed-text:latest")
            assert "does not support text completion" in str(exc_info.value)

    def test_switch_model_rejects_empty_name(self, llm_service):
        """Empty string or whitespace-only model names raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            llm_service.set_active_model("   ")
        assert "Model name is required" in str(exc_info.value)

    def test_resolve_exact_match(self, llm_service):
        """Direct match resolves immediately."""
        mock_client = MagicMock()
        installed = ["qwen2.5:7b", "phi4-mini:latest"]
        resolved = llm_service._resolve_model(mock_client, override_model="qwen2.5:7b", installed=installed)
        assert resolved == "qwen2.5:7b"

    def test_resolve_latest_tag_alias(self, llm_service):
        """Untagged name matches installed :latest tag."""
        mock_client = MagicMock()
        installed = ["phi4-mini:latest"]
        resolved = llm_service._resolve_model(mock_client, override_model="phi4-mini", installed=installed)
        assert resolved == "phi4-mini:latest"

    def test_resolve_quantization_suffix_match(self, llm_service):
        """Model with quantization suffix (e.g. -q4_K_M) matches requested base tag."""
        mock_client = MagicMock()
        installed = ["qwen2.5:7b-instruct-q4_K_M"]
        resolved = llm_service._resolve_model(mock_client, override_model="qwen2.5:7b", installed=installed)
        assert resolved == "qwen2.5:7b-instruct-q4_K_M"

    def test_resolve_hardware_fallback_when_default_missing(self, llm_service):
        """When active model is not present, fall back to hardware-recommended model."""
        mock_client = MagicMock()
        llm_service.model = "missing-llama:8b"
        installed = ["phi4-mini:latest", "other:latest"]

        with patch.object(llm_service, "detect_hardware", return_value={"recommended_models": ["phi4-mini"]}):
            resolved = llm_service._resolve_model(mock_client, installed=installed)
            assert resolved == "phi4-mini:latest"

    def test_resolve_chat_model_language_routing_roman_urdu(self, llm_service):
        """Roman Urdu language queries route preferentially to Llama 3.2 3B."""
        mock_client = MagicMock()
        mock_client.list.return_value = {
            "models": [{"name": "phi4-mini:latest"}, {"name": "llama3.2:3b"}]
        }
        llm_service.model = "phi4-mini:latest"

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chat_model = llm_service.resolve_chat_model(language="roman_urdu")
            assert chat_model == "llama3.2:3b"

    def test_resolve_chat_model_standard_english(self, llm_service):
        """English queries use the active model."""
        mock_client = MagicMock()
        mock_client.list.return_value = {
            "models": [{"name": "phi4-mini:latest"}, {"name": "llama3.2:3b"}]
        }
        llm_service.model = "phi4-mini:latest"

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chat_model = llm_service.resolve_chat_model(language="english")
            assert chat_model == "phi4-mini:latest"

    def test_get_status_online_and_offline(self, llm_service):
        """get_status correctly communicates daemon availability and resolved model."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini:latest"}]}

        # Online
        with patch.object(llm_service, "_get_client", return_value=mock_client):
            status = llm_service.get_status()
            assert status["available"] is True
            assert status["resolved_model"] == "phi4-mini:latest"
            assert status["error"] is None

        # Offline
        with patch.object(llm_service, "_get_client", side_effect=ConnectionError("Cannot reach Ollama")):
            status = llm_service.get_status()
            assert status["available"] is False
            assert status["resolved_model"] is None
            assert "Cannot reach Ollama" in status["error"]
