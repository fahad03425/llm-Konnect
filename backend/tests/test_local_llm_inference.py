"""Aggressive and comprehensive test suite for Module 6.1 — Local LLM Inference Module.
Tests all capabilities:
1. Hardware detection & profile recommendation across CPU/GPU/VRAM tiers.
2. Model resolution, switching, and fallback hierarchies.
3. Detailed model listing and metadata parsing.
4. Model pulling with streaming NDJSON progress, byte tracking, and error recovery.
5. Model loading (pre-warm) and unloading (memory eviction via keep_alive=0).
6. Real-time loaded model inspection (Ollama ps / VRAM usage).
7. Model-agnostic generation, fast chat, streaming chat, and think-tag sanitization.
8. REST and Streaming API endpoints under /api/models and backward compatibility.
9. Concurrency & thread safety under active model switching.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app.main import app
from app.core.llm import LLMService


@pytest.fixture(autouse=True)
def preserve_active_model(monkeypatch, tmp_path):
    from app.core.llm import llm
    from app.core.config import settings
    monkeypatch.setattr(llm, "model", llm.model)
    monkeypatch.setattr(settings, "llm_model", settings.llm_model)
    monkeypatch.setattr(LLMService, "_model_settings_path", staticmethod(lambda: tmp_path / "model_settings.json"))


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def llm_service(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMService, "_model_settings_path", staticmethod(lambda: tmp_path / "model_settings.json"))
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:1.5b")
    return LLMService()


# =============================================================================
# 1. Hardware Detection & Recommendation Profiles (CPU, Low/Mid/High VRAM)
# =============================================================================
class TestHardwareDetection:
    def test_cpu_only_fallback(self, llm_service):
        """No GPU detected -> CPU lightweight profile with Phi-4-mini recommendation."""
        with patch("torch.cuda.is_available", return_value=False, create=True), \
             patch("subprocess.check_output", side_effect=FileNotFoundError("No nvidia-smi")):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            assert hw["gpu_available"] is False
            assert hw["device_type"] == "cpu"
            assert hw["vram_mb"] == 0
            assert hw["recommended_profile"] == "cpu_lightweight"
            assert "phi4-mini" in hw["recommended_models"]
            assert hw["cpu_cores"] > 0

    def test_budget_gpu_profile(self, llm_service):
        """GPU with 3GB VRAM -> Budget GPU profile."""
        mock_props = MagicMock()
        mock_props.total_memory = 3072 * 1024 * 1024  # 3072 MB

        with patch("torch.cuda.is_available", return_value=True, create=True), \
             patch("torch.cuda.get_device_name", return_value="NVIDIA GTX 1050", create=True), \
             patch("torch.cuda.get_device_properties", return_value=mock_props, create=True):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            assert hw["gpu_available"] is True
            assert hw["vram_mb"] == 3072
            assert hw["device_type"] == "gpu"
            assert hw["recommended_profile"] == "gpu_budget"
            assert "phi4-mini" in hw["recommended_models"] or "qwen2.5:3b" in hw["recommended_models"]

    def test_standard_gpu_profile(self, llm_service):
        """GPU with 8GB VRAM -> Standard GPU profile recommending Qwen 2.5 / Mistral."""
        mock_props = MagicMock()
        mock_props.total_memory = 8192 * 1024 * 1024

        with patch("torch.cuda.is_available", return_value=True, create=True), \
             patch("torch.cuda.get_device_name", return_value="NVIDIA RTX 4070", create=True), \
             patch("torch.cuda.get_device_properties", return_value=mock_props, create=True):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            assert hw["gpu_available"] is True
            assert hw["vram_mb"] == 8192
            assert hw["recommended_profile"] == "gpu_standard"
            assert any("qwen2.5" in m for m in hw["recommended_models"])
            assert any("mistral" in m for m in hw["recommended_models"])

    def test_heavy_gpu_profile(self, llm_service):
        """GPU with 16GB VRAM -> Heavy GPU profile recommending Qwen 2.5 14B+."""
        mock_props = MagicMock()
        mock_props.total_memory = 16384 * 1024 * 1024

        with patch("torch.cuda.is_available", return_value=True, create=True), \
             patch("torch.cuda.get_device_name", return_value="NVIDIA RTX 4090", create=True), \
             patch("torch.cuda.get_device_properties", return_value=mock_props, create=True):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            assert hw["gpu_available"] is True
            assert hw["vram_mb"] == 16384
            assert hw["recommended_profile"] == "gpu_heavy"
            assert "qwen2.5:14b" in hw["recommended_models"]

    def test_nvidia_smi_parser_fallback(self, llm_service):
        """When PyTorch is not available, correctly parse nvidia-smi CLI output."""
        with patch("torch.cuda.is_available", return_value=False, create=True), \
             patch("subprocess.check_output", return_value="Quadro T1000, 4096\n"):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            assert hw["gpu_available"] is True
            assert hw["gpu_name"] == "Quadro T1000"
            assert hw["vram_mb"] == 4096

    def test_hardware_caching(self, llm_service):
        """Ensure repeated calls use cached result unless force_refresh is True."""
        llm_service._cached_hw = {"gpu_available": True, "cached_sentinel": True}
        res = llm_service.detect_hardware(force_refresh=False)
        assert res.get("cached_sentinel") is True


# =============================================================================
# 2. Model Switching, Hierarchy Resolution & Discovery
# =============================================================================
class TestModelSwitchingAndResolution:
    def test_explicit_switch(self, llm_service):
        """Switching active model updates instance and config settings."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "mistral:7b-instruct"}]}
        mock_client.show.return_value = {"capabilities": ["completion"]}
        with patch.object(llm_service, "_get_client", return_value=mock_client):
            active = llm_service.set_active_model("mistral:7b-instruct")
        assert active == "mistral:7b-instruct"
        assert llm_service.model == "mistral:7b-instruct"

    def test_resolve_exact_match(self, llm_service):
        """Exact model string match in installed list."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "mistral:7b-instruct"}]}
        llm_service.model = "mistral:7b-instruct"
        
        resolved = llm_service._resolve_model(mock_client)
        assert resolved == "mistral:7b-instruct"

    def test_resolve_base_name_match(self, llm_service):
        """A size tag accepts the same size with a quantization suffix."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "qwen2.5:7b-q4_K_M"}]}
        llm_service.model = "qwen2.5:7b"
        
        resolved = llm_service._resolve_model(mock_client)
        assert resolved == "qwen2.5:7b-q4_K_M"

    def test_resolve_hardware_preference_fallback(self, llm_service):
        """When active model is missing, resolution picks hardware-appropriate model."""
        mock_client = MagicMock()
        mock_client.list.return_value = {
            "models": [{"name": "other-model:latest"}, {"name": "phi4-mini:latest"}]
        }
        llm_service.model = "non-existent-model"
        # Mock CPU hardware recommendation
        with patch.object(llm_service, "detect_hardware", return_value={"recommended_models": ["phi4-mini"]}):
            resolved = llm_service._resolve_model(mock_client)
            assert resolved == "phi4-mini:latest"

    def test_resolve_offline_error_handling(self, llm_service):
        """Offline resolution exposes the connection failure rather than inventing a model."""
        mock_client = MagicMock()
        mock_client.list.side_effect = ConnectionError("Ollama offline")
        llm_service.model = "phi4-mini"
        
        with pytest.raises(ConnectionError):
            llm_service._resolve_model(mock_client)

    def test_list_installed_models_detailed(self, llm_service):
        """Detailed listing parses family, parameter count, quantization, and sizes."""
        mock_client = MagicMock()
        mock_client.list.return_value = {
            "models": [
                {
                    "model": "phi4-mini:3.8b",
                    "size": 2400000000,
                    "modified_at": "2026-03-01T12:00:00Z",
                    "details": {
                        "family": "phi3",
                        "parameter_size": "3.8B",
                        "quantization_level": "Q4_K_M"
                    }
                }
            ]
        }
        llm_service.model = "phi4-mini:3.8b"

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            items = llm_service.list_installed_models_detailed()
            assert len(items) == 1
            item = items[0]
            assert item["name"] == "phi4-mini:3.8b"
            assert item["size_gb"] > 2.0
            assert item["is_active"] is True
            assert item["details"]["parameter_size"] == "3.8B"


# =============================================================================
# 3. Model Pulling (Progress Streaming, Zero-Division, Network Failure)
# =============================================================================
class TestModelPulling:
    def test_pull_model_sync_success(self, llm_service):
        """Synchronous pull calls Ollama client and returns status dict."""
        mock_client = MagicMock()
        mock_client.pull.return_value = {"status": "success"}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            res = llm_service.pull_model("phi4-mini")
            assert res["status"] == "success"
            assert res["model"] == "phi4-mini"

    def test_pull_model_stream_progress(self, llm_service):
        """Streaming pull yields progress percentages correctly."""
        mock_client = MagicMock()
        mock_client.pull.return_value = [
            {"status": "pulling manifest", "completed": 0, "total": 0},
            {"status": "downloading", "completed": 250, "total": 1000},
            {"status": "downloading", "completed": 1000, "total": 1000},
            {"status": "success", "completed": 1000, "total": 1000},
        ]

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chunks = list(llm_service.pull_model_stream("phi4-mini"))
            assert len(chunks) == 4
            assert chunks[0]["percent"] == 0.0
            assert chunks[1]["percent"] == 25.0
            assert chunks[2]["percent"] == 100.0
            assert chunks[3]["status"] == "success"

    def test_pull_model_stream_network_error(self, llm_service):
        """Streaming pull handles connection drop gracefully without unhandled crash."""
        mock_client = MagicMock()
        mock_client.pull.side_effect = ConnectionResetError("Connection lost")

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chunks = list(llm_service.pull_model_stream("phi4-mini"))
            assert len(chunks) == 1
            assert "error" in chunks[0]["status"]
            assert chunks[0]["percent"] == 0.0


# =============================================================================
# 4. Model Loading & Unloading (Resource / VRAM Management)
# =============================================================================
class TestModelLifecycle:
    def test_load_model_prewarm(self, llm_service):
        """load_model sends 0-token ping with keep_alive duration."""
        mock_client = MagicMock()

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            res = llm_service.load_model("qwen2.5:7b", keep_alive="45m")
            assert res["status"] == "loaded"
            assert res["model"] == "qwen2.5:7b"
            assert res["keep_alive"] == "45m"
            mock_client.generate.assert_called_once_with(
                model="qwen2.5:7b", prompt="", keep_alive="45m"
            )

    def test_unload_model_eviction(self, llm_service):
        """unload_model sends keep_alive=0 to immediately release VRAM/RAM."""
        mock_client = MagicMock()

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            res = llm_service.unload_model("qwen2.5:7b")
            assert res["status"] == "unloaded"
            assert res["model"] == "qwen2.5:7b"
            mock_client.generate.assert_called_once_with(
                model="qwen2.5:7b", prompt="", keep_alive=0
            )

    def test_get_running_models(self, llm_service):
        """get_running_models inspects Ollama ps and calculates VRAM footprint."""
        mock_client = MagicMock()
        mock_client.ps.return_value = {
            "models": [
                {
                    "model": "qwen2.5:7b",
                    "size": 4700000000,
                    "size_vram": 4700000000,
                    "expires_at": "2026-10-01T17:00:00Z"
                }
            ]
        }

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            running = llm_service.get_running_models()
            assert len(running) == 1
            assert running[0]["name"] == "qwen2.5:7b"
            assert running[0]["size_vram_mb"] > 4000

    def test_delete_model(self, llm_service):
        """delete_model issues delete command to Ollama client."""
        mock_client = MagicMock()

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            success = llm_service.delete_model("old-model:latest")
            assert success is True
            mock_client.delete.assert_called_once_with("old-model:latest")


# =============================================================================
# 5. Output Sanitization & Reasoning Block Filtering
# =============================================================================
class TestOutputSanitization:
    def test_clean_think_tags_standard(self, llm_service):
        """Standard closed <think> block removal."""
        raw = "<think>Let me compute total sales: 50 + 50 = 100.</think>The total is 100."
        cleaned = llm_service._clean_output(raw)
        assert cleaned == "The total is 100."

    def test_clean_think_tags_multiline(self, llm_service):
        """Multiline <think> block with internal markdown."""
        raw = "<think>\nStep 1: Check inventory\nStep 2: Aggregate\n</think>\nTotal stock: 50 units."
        cleaned = llm_service._clean_output(raw)
        assert cleaned == "Total stock: 50 units."

    def test_clean_think_tags_unclosed(self, llm_service):
        """Unclosed opening <think> tag."""
        raw = "Valid prefix<think>still thinking..."
        cleaned = llm_service._clean_output(raw)
        assert cleaned == "Valid prefix"

    def test_clean_think_tags_unopened(self, llm_service):
        """Closing </think> tag without opening tag."""
        raw = "random reasoning</think>Final cleaned message."
        cleaned = llm_service._clean_output(raw)
        assert cleaned == "Final cleaned message."

    def test_clean_none_and_empty(self, llm_service):
        """Handling None or empty string inputs gracefully."""
        assert llm_service._clean_output("") == ""
        assert llm_service._clean_output(None) == ""


# =============================================================================
# 6. Model-Agnostic Generation & Streaming Inference
# =============================================================================
class TestInferencePipeline:
    def test_generate_non_streaming(self, llm_service):
        """generate() sends system and user messages and returns sanitized text."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini"}]}
        mock_client.chat.return_value = {
            "message": {"content": "In stock: 20 Panadol."}
        }

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            out = llm_service.generate(
                prompt="How many Panadol?",
                system_prompt="You are an inventory assistant."
            )
            assert out == "In stock: 20 Panadol."

    def test_chat_stream_filters_think_chunks(self, llm_service):
        """chat_stream yields only content outside <think> blocks."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini"}]}
        mock_client.chat.return_value = [
            {"message": {"content": "<think>"}},
            {"message": {"content": "calculating..."}},
            {"message": {"content": "</think>"}},
            {"message": {"content": "Report "}},
            {"message": {"content": "ready."}},
        ]

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            chunks = list(llm_service.chat_stream([{"role": "user", "content": "hello"}]))
            assert chunks == ["Report ", "ready."]


# =============================================================================
# 7. REST & Streaming API Endpoints
# =============================================================================
class TestApiEndpoints:
    def test_get_hardware_endpoint(self, client):
        res = client.get("/api/models/hardware")
        assert res.status_code == 200
        data = res.json()
        assert "hardware" in data
        assert "cpu_cores" in data["hardware"]

    def test_list_models_detailed_endpoint(self, client):
        with patch.object(LLMService, "list_installed_models_detailed", return_value=[{"name": "phi4-mini", "size_gb": 2.4, "is_active": True}]):
            res = client.get("/api/models?detailed=true")
            assert res.status_code == 200
            data = res.json()
            assert data["models"][0]["name"] == "phi4-mini"

    def test_select_model_endpoint(self, client, llm_service):
        with patch.object(LLMService, "_installed_names", return_value=["mistral:7b"]), \
             patch.object(LLMService, "_get_client") as factory:
            factory.return_value.show.return_value = {"capabilities": ["completion"]}
            res = client.post("/api/models/select", json={"model": "mistral:7b"})
        assert res.status_code == 200
        assert res.json()["active_model"] == "mistral:7b"

    def test_load_and_unload_endpoints(self, client):
        with patch.object(LLMService, "load_model", return_value={"status": "loaded", "model": "qwen2.5:7b"}), \
             patch.object(LLMService, "unload_model", return_value={"status": "unloaded", "model": "qwen2.5:7b"}):
            
            # Load
            res_load = client.post("/api/models/load", json={"model": "qwen2.5:7b", "keep_alive": "1h"})
            assert res_load.status_code == 200
            assert res_load.json()["status"] == "loaded"

            # Unload
            res_unload = client.post("/api/models/unload", json={"model": "qwen2.5:7b"})
            assert res_unload.status_code == 200
            assert res_unload.json()["status"] == "unloaded"

    def test_pull_model_streaming_endpoint(self, client):
        def _mock_stream(model_name):
            yield {"model": model_name, "status": "downloading", "percent": 50.0}
            yield {"model": model_name, "status": "success", "percent": 100.0}

        with patch.object(LLMService, "pull_model_stream", side_effect=_mock_stream):
            res = client.post("/api/models/pull", json={"model": "phi4-mini", "stream": True})
            assert res.status_code == 200
            lines = [json.loads(line) for line in res.text.strip().split("\n") if line]
            assert len(lines) == 2
            assert lines[0]["percent"] == 50.0
            assert lines[1]["status"] == "success"

    def test_delete_model_endpoint(self, client):
        with patch.object(LLMService, "delete_model", return_value=True):
            res = client.delete("/api/models/phi4-mini:latest")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"


# =============================================================================
# 8. Thread-Safety & Concurrent Model Switching
# =============================================================================
class TestConcurrencyAndThreadSafety:
    def test_concurrent_model_switching_and_inference(self, llm_service):
        """Ensure concurrent model switches and queries do not deadlock or raise race conditions."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini"}, {"name": "qwen2.5:7b"}]}
        mock_client.chat.return_value = {"message": {"content": "Answer"}}
        mock_client.show.return_value = {"capabilities": ["completion"]}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            def switch_worker(name):
                llm_service.set_active_model(name)
                return llm_service.model

            def infer_worker(query):
                return llm_service.generate(prompt=query)

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = []
                for i in range(10):
                    m = "phi4-mini" if i % 2 == 0 else "qwen2.5:7b"
                    futures.append(executor.submit(switch_worker, m))
                    futures.append(executor.submit(infer_worker, f"Query {i}"))

                results = [f.result() for f in futures]
                assert len(results) == 20
