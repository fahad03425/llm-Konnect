"""Opt-in real-engine lifecycle check; never downloads or selects a model."""
import os

import pytest

from app.core.llm import LLMService


@pytest.mark.skipif(os.getenv("KONNECT_TEST_OLLAMA_LIVE") != "1", reason="Opt in with KONNECT_TEST_OLLAMA_LIVE=1")
def test_real_ollama_lifecycle():
    service = LLMService()
    assert service.get_status()["available"], "Start Ollama before running live tests"
    client = service._get_client()
    target = os.getenv("KONNECT_TEST_MODEL", service.model)
    model = service._resolve_model(client, override_model=target)
    running = {item["name"] for item in service.get_running_models()}
    if model in running:
        pytest.skip("Model is already in use; choose an idle model with KONNECT_TEST_MODEL")
    try:
        assert service.load_model(model, keep_alive="1m")["status"] == "loaded"
        assert model in {item["name"] for item in service.get_running_models()}
        answer = service.chat([{"role": "user", "content": "Reply with hello."}],
                              model=model, options={"num_predict": 16})
        assert answer.strip()
    finally:
        service.unload_model(model)
    assert model not in {item["name"] for item in service.get_running_models()}
