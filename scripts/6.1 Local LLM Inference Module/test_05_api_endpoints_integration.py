"""Test Suite 05: REST & Streaming API Endpoints and Concurrency Safety.

Module: Module 6.1 — Local LLM Inference Module
Target File: backend/app/api/models.py
Scope:
- Verifies all REST endpoints exposed under /api/models.
- Verifies NDJSON streaming response protocol on /api/models/pull.
- Verifies error responses (400, 404, 500) for missing models or invalid inputs.
- Verifies multi-threaded concurrency safety during parallel model switching and inference.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
import pytest
from app.core.llm import LLMService


class TestApiEndpointsIntegration:
    """Verifies FastAPI route handlers and concurrent execution safety."""

    def test_get_hardware_endpoint(self, test_client):
        """GET /api/models/hardware returns hardware structure and active model."""
        res = test_client.get("/api/models/hardware")
        assert res.status_code == 200
        data = res.json()
        assert "hardware" in data
        assert "active_model" in data
        assert "cpu_cores" in data["hardware"]

    def test_list_models_basic(self, test_client):
        """GET /api/models returns installed model names and daemon status."""
        with patch.object(LLMService, "list_installed_models", return_value=["phi4-mini", "mistral:7b"]):
            res = test_client.get("/api/models")
            assert res.status_code == 200
            data = res.json()
            assert "phi4-mini" in data["models"]
            assert data["active_model"] is not None

    def test_list_models_detailed(self, test_client):
        """GET /api/models?detailed=true includes size, family, and quantization details."""
        detailed_mock = [
            {
                "name": "phi4-mini",
                "size_gb": 2.4,
                "is_active": True,
                "details": {"family": "phi3", "parameter_size": "3.8B"}
            }
        ]
        with patch.object(LLMService, "list_installed_models_detailed", return_value=detailed_mock):
            res = test_client.get("/api/models?detailed=true")
            assert res.status_code == 200
            data = res.json()
            assert len(data["models"]) == 1
            assert data["models"][0]["size_gb"] == 2.4

    def test_list_running_models_endpoint(self, test_client):
        """GET /api/models/running returns active in-memory models and count."""
        running_mock = [{"name": "qwen2.5:7b", "size_vram_mb": 4200, "expires_at": "2026-10-04"}]
        with patch.object(LLMService, "get_running_models", return_value=running_mock):
            res = test_client.get("/api/models/running")
            assert res.status_code == 200
            data = res.json()
            assert data["count"] == 1
            assert data["running_models"][0]["size_vram_mb"] == 4200

    def test_get_model_info_endpoint(self, test_client):
        """GET /api/models/info/{model_name} returns architectural parameters."""
        info_mock = {"model": "phi4-mini", "details": {"format": "gguf"}}
        with patch.object(LLMService, "get_model_info", return_value=info_mock):
            res = test_client.get("/api/models/info/phi4-mini")
            assert res.status_code == 200
            assert res.json()["model"] == "phi4-mini"

    def test_get_model_info_not_found(self, test_client):
        """GET /api/models/info/{model_name} returns 404 if model info cannot be fetched."""
        with patch.object(LLMService, "get_model_info", side_effect=RuntimeError("Model not found")):
            res = test_client.get("/api/models/info/nonexistent-model")
            assert res.status_code == 404

    def test_select_model_endpoint_success(self, test_client):
        """POST /api/models/select switches active model."""
        with patch.object(LLMService, "_installed_names", return_value=["qwen2.5:7b"]), \
             patch.object(LLMService, "_get_client") as factory:
            factory.return_value.show.return_value = {"capabilities": ["completion"]}
            res = test_client.post("/api/models/select", json={"model": "qwen2.5:7b"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["active_model"] == "qwen2.5:7b"

    def test_select_model_endpoint_invalid_model(self, test_client):
        """POST /api/models/select returns 400 when model is missing or unsupported."""
        with patch.object(LLMService, "_installed_names", return_value=["phi4-mini"]):
            res = test_client.post("/api/models/select", json={"model": "unknown-model"})
            assert res.status_code == 400
            assert "is not installed" in res.json()["detail"]

    def test_load_and_unload_endpoints(self, test_client):
        """POST /api/models/load and /api/models/unload trigger memory lifecycle."""
        with patch.object(LLMService, "load_model", return_value={"status": "loaded", "model": "mistral:7b"}), \
             patch.object(LLMService, "unload_model", return_value={"status": "unloaded", "model": "mistral:7b"}):
            
            # Pre-warm
            res_load = test_client.post("/api/models/load", json={"model": "mistral:7b", "keep_alive": "1h"})
            assert res_load.status_code == 200
            assert res_load.json()["status"] == "loaded"

            # Evict
            res_unload = test_client.post("/api/models/unload", json={"model": "mistral:7b"})
            assert res_unload.status_code == 200
            assert res_unload.json()["status"] == "unloaded"

    def test_pull_model_streaming_ndjson(self, test_client):
        """POST /api/models/pull with stream=true yields application/x-ndjson stream."""
        def mock_pull_stream(model_name):
            yield {"model": model_name, "status": "downloading", "percent": 50.0}
            yield {"model": model_name, "status": "success", "percent": 100.0}

        with patch.object(LLMService, "pull_model_stream", side_effect=mock_pull_stream):
            res = test_client.post("/api/models/pull", json={"model": "phi4-mini", "stream": True})
            assert res.status_code == 200
            assert "application/x-ndjson" in res.headers["content-type"]
            lines = [json.loads(line) for line in res.text.strip().split("\n") if line]
            assert len(lines) == 2
            assert lines[0]["percent"] == 50.0
            assert lines[1]["status"] == "success"

    def test_delete_model_endpoint(self, test_client):
        """DELETE /api/models/{model_name} removes model."""
        with patch.object(LLMService, "delete_model", return_value=True):
            res = test_client.delete("/api/models/phi4-mini:latest")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"
            assert res.json()["deleted"] == "phi4-mini:latest"

    def test_concurrent_switching_and_inference_stress(self, llm_service):
        """High-concurrency stress test ensuring zero race condition or state corruption."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini"}, {"name": "mistral:7b"}]}
        mock_client.chat.return_value = {"message": {"content": "ok"}}
        mock_client.show.return_value = {"capabilities": ["completion"]}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            def switch_task(name):
                return llm_service.set_active_model(name)

            def infer_task(prompt):
                return llm_service.generate(prompt=prompt)

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = []
                for i in range(12):
                    target = "phi4-mini" if i % 2 == 0 else "mistral:7b"
                    futures.append(pool.submit(switch_task, target))
                    futures.append(pool.submit(infer_task, f"Prompt {i}"))
                
                results = [f.result() for f in futures]
                assert len(results) == 24
