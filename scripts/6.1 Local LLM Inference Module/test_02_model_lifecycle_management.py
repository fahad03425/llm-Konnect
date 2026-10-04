"""Test Suite 02: Model Lifecycle & VRAM Memory Management.

Module: Module 6.1 — Local LLM Inference Module
Target File: backend/app/core/llm.py (methods: pull_model, pull_model_stream, load_model, unload_model, get_running_models, delete_model, list_installed_models_detailed)
Scope:
- Verifies pulling models synchronously and via NDJSON streaming with progress calculation.
- Verifies zero-division protection and network disconnection handling during downloads.
- Verifies explicit memory pre-warming via keep_alive.
- Verifies immediate VRAM/RAM eviction via keep_alive=0.
- Verifies real-time inspection of active loaded models in memory (Ollama ps).
- Verifies deletion of models from disk storage.
"""

from unittest.mock import MagicMock, patch
import pytest
from app.core.llm import LLMService


class TestModelLifecycleManagement:
    """Verifies download, pre-warm, eviction, and memory footprint monitoring."""

    def test_pull_model_sync_success(self, llm_service):
        """pull_model triggers client pull synchronously and reports completion."""
        mock_client = MagicMock()
        mock_client.pull.return_value = {"status": "success"}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            result = llm_service.pull_model("phi4-mini")
            assert result["status"] == "success"
            assert result["model"] == "phi4-mini"
            mock_client.pull.assert_called_once_with(model="phi4-mini", stream=False)

    def test_pull_model_sync_failure_raises_runtime_error(self, llm_service):
        """pull_model raises RuntimeError on underlying client errors."""
        mock_client = MagicMock()
        mock_client.pull.side_effect = Exception("Ollama daemon unreachable")

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            with pytest.raises(RuntimeError) as exc_info:
                llm_service.pull_model("qwen2.5:7b")
            assert "Failed to pull model" in str(exc_info.value)

    def test_pull_model_stream_progress_calculation(self, llm_service):
        """pull_model_stream computes progress percentages across download chunks."""
        mock_client = MagicMock()
        mock_client.pull.return_value = [
            {"status": "pulling manifest", "completed": 0, "total": 0},
            {"status": "downloading", "completed": 500, "total": 2000},
            {"status": "downloading", "completed": 2000, "total": 2000},
            {"status": "verifying sha256 digest", "completed": 2000, "total": 2000},
            {"status": "success", "completed": 2000, "total": 2000},
        ]

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chunks = list(llm_service.pull_model_stream("phi4-mini"))
            assert len(chunks) == 5
            # Zero-division safety
            assert chunks[0]["percent"] == 0.0
            assert chunks[1]["percent"] == 25.0
            assert chunks[2]["percent"] == 100.0
            assert chunks[4]["status"] == "success"

    def test_pull_model_stream_connection_drop_recovery(self, llm_service):
        """pull_model_stream handles socket aborts gracefully yielding error status chunk."""
        mock_client = MagicMock()
        mock_client.pull.side_effect = ConnectionResetError("Remote server closed connection")

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chunks = list(llm_service.pull_model_stream("mistral:7b"))
            assert len(chunks) == 1
            assert "error" in chunks[0]["status"]
            assert chunks[0]["percent"] == 0.0

    def test_load_model_prewarm_into_vram(self, llm_service):
        """load_model executes 0-token ping with keep_alive to lock model into VRAM."""
        mock_client = MagicMock()

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            res = llm_service.load_model("qwen2.5:7b", keep_alive="45m")
            assert res["status"] == "loaded"
            assert res["model"] == "qwen2.5:7b"
            assert res["keep_alive"] == "45m"
            mock_client.generate.assert_called_once_with(
                model="qwen2.5:7b", prompt="", keep_alive="45m"
            )

    def test_unload_model_eviction_from_vram(self, llm_service):
        """unload_model executes ping with keep_alive=0 to immediately release VRAM."""
        mock_client = MagicMock()

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            res = llm_service.unload_model("qwen2.5:7b")
            assert res["status"] == "unloaded"
            assert res["model"] == "qwen2.5:7b"
            mock_client.generate.assert_called_once_with(
                model="qwen2.5:7b", prompt="", keep_alive=0
            )

    def test_get_running_models_vram_telemetry(self, llm_service):
        """get_running_models queries Ollama ps and converts byte footprints to MB."""
        mock_client = MagicMock()
        mock_client.ps.return_value = {
            "models": [
                {
                    "model": "qwen2.5:7b",
                    "size": 4700000000,
                    "size_vram": 4700000000,
                    "expires_at": "2026-10-04T16:00:00Z"
                },
                {
                    "model": "phi4-mini:latest",
                    "size": 2400000000,
                    "size_vram": 0,
                    "expires_at": "2026-10-04T15:30:00Z"
                }
            ]
        }

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            running = llm_service.get_running_models()
            assert len(running) == 2
            assert running[0]["name"] == "qwen2.5:7b"
            assert running[0]["size_vram_mb"] > 4000
            assert running[1]["name"] == "phi4-mini:latest"
            assert running[1]["size_vram_mb"] == 0

    def test_delete_model_command(self, llm_service):
        """delete_model sends deletion command to Ollama daemon."""
        mock_client = MagicMock()

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            success = llm_service.delete_model("obsolete-model:latest")
            assert success is True
            mock_client.delete.assert_called_once_with("obsolete-model:latest")

    def test_list_installed_models_detailed_metadata(self, llm_service):
        """list_installed_models_detailed extracts family, quantization, and size in GB."""
        mock_client = MagicMock()
        mock_client.list.return_value = {
            "models": [
                {
                    "model": "phi4-mini:latest",
                    "size": 2684354560,  # 2.5 GB
                    "modified_at": "2026-10-01T10:00:00Z",
                    "details": {
                        "family": "phi3",
                        "parameter_size": "3.8B",
                        "quantization_level": "Q4_K_M"
                    }
                }
            ]
        }
        llm_service.model = "phi4-mini:latest"

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            items = llm_service.list_installed_models_detailed()
            assert len(items) == 1
            model_info = items[0]
            assert model_info["name"] == "phi4-mini:latest"
            assert model_info["size_gb"] == 2.5
            assert model_info["is_active"] is True
            assert model_info["details"]["family"] == "phi3"
            assert model_info["details"]["quantization_level"] == "Q4_K_M"
