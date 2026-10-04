"""Test Suite 06: Live Ollama Connectivity, Daemon Autostart & Environment Health.

Module: Module 6.1 — Local LLM Inference Module
Target File: backend/app/core/llm.py (methods: find_ollama_executable, is_ollama_alive, ensure_ollama_running)
Scope:
- Verifies detection of local Ollama binary executable across Windows / Unix standard paths.
- Probes live Ollama HTTP daemon connectivity on configured host (http://127.0.0.1:11434).
- Tests non-destructive live model discovery and live inference if daemon is currently running.
- Gracefully handles offline environments without breaking test pipeline.
"""

import pytest
from app.core.llm import LLMService, llm


class TestLiveOllamaConnectivity:
    """Verifies live daemon presence, binary paths, and environment readiness."""

    def test_ollama_executable_discovery(self):
        """find_ollama_executable searches PATH and standard installation folders."""
        exe_path = LLMService.find_ollama_executable()
        # Even if not installed on CI, function must return str path or None gracefully without raising
        assert exe_path is None or isinstance(exe_path, str)

    def test_live_daemon_status_probe(self):
        """is_ollama_alive sends a lightweight ping to the configured host."""
        alive = llm.is_ollama_alive(timeout=0.8)
        assert isinstance(alive, bool)

    def test_live_tags_or_graceful_offline_report(self):
        """If Ollama is live, inspect installed models; otherwise report offline state."""
        if not llm.is_ollama_alive(timeout=0.8):
            pytest.skip("Local Ollama daemon is currently offline at " + llm.host + " (expected in isolated test runs)")
        
        installed = llm.list_installed_models()
        assert isinstance(installed, list)
        print(f"\n[LIVE TEST] Connected to Ollama at {llm.host}. Installed models: {installed}")

    def test_live_inference_smoke_test(self):
        """If Ollama is live and a model is installed, perform a quick 1-turn sanity generation."""
        if not llm.is_ollama_alive(timeout=0.8):
            pytest.skip("Local Ollama daemon is offline; skipping live inference smoke test.")

        installed = llm.list_installed_models()
        if not installed:
            pytest.skip("Ollama is running but has no models installed; skipping live inference.")

        # Prefer standard conversational model over reasoning model for fast smoke ping
        preferred = [m for m in installed if any(k in m for k in ("qwen2.5:1.5b", "qwen2.5:0.5b", "llama3.2:1b", "llama3.2:3b", "gemma3"))]
        target_model = preferred[0] if preferred else installed[0]
        result = llm.generate(
            prompt="Reply with the single word 'READY'",
            system_prompt="Direct answer only. Do not think.",
            model=target_model,
            options={"num_predict": 40, "temperature": 0.0}
        )
        assert isinstance(result, str)
        assert len(result.strip()) > 0
        print(f"\n[LIVE TEST] Model '{target_model}' responded: {result.strip()}")

