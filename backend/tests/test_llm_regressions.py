from unittest.mock import Mock

import pytest

from app.core.llm import LLMService
from app.core.config import settings


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMService, "_model_settings_path", staticmethod(lambda: tmp_path / "model.json"))
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:1.5b")
    return LLMService()


def client_with(*names):
    client = Mock()
    client.list.return_value = {"models": [{"name": name} for name in names]}
    client.show.return_value = {"capabilities": ["completion"]}
    return client


def test_explicit_size_never_substituted(service):
    with pytest.raises(ValueError, match="not installed"):
        service._resolve_model(client_with("qwen2.5:14b"), "qwen2.5:7b")


def test_budget_preference_respects_size(service, monkeypatch):
    service.model = "missing"
    monkeypatch.setattr(service, "detect_hardware", lambda: {"recommended_models": ["qwen2.5:3b"]})
    assert service._resolve_model(client_with("qwen2.5:14b", "qwen2.5:3b")) == "qwen2.5:3b"
    with pytest.raises(ValueError):
        service._resolve_model(client_with("qwen2.5:14b"))


def test_failed_selection_preserves_saved_model(service, monkeypatch):
    monkeypatch.setattr(service, "_get_client", lambda: client_with("qwen2.5:1.5b"))
    service.set_active_model("qwen2.5:1.5b")
    original = service._model_settings_path().read_bytes()
    with pytest.raises(ValueError):
        service.set_active_model("missing")
    assert service.model == "qwen2.5:1.5b"
    assert service._model_settings_path().read_bytes() == original
    assert LLMService().model == service.model


def test_embedding_model_rejected(service, monkeypatch):
    client = client_with("embed:latest")
    client.show.return_value = {"capabilities": ["embedding"]}
    monkeypatch.setattr(service, "_get_client", lambda: client)
    with pytest.raises(ValueError, match="completion"):
        service.set_active_model("embed")


def test_offline_not_reported_as_installed(service, monkeypatch):
    client = client_with()
    client.list.side_effect = ConnectionError("offline")
    monkeypatch.setattr(service, "_get_client", lambda: client)
    with pytest.raises(RuntimeError, match="offline"):
        service.list_installed_models()
    assert service.get_status()["available"] is False


def test_online_without_models_is_still_available(service, monkeypatch):
    monkeypatch.setattr(service, "_get_client", lambda: client_with())
    assert service.get_status() == {"available": True, "resolved_model": None, "error": None}


def test_language_preference_is_optional(service, monkeypatch):
    monkeypatch.setattr(service, "_get_client", lambda: client_with("qwen2.5:1.5b"))
    assert service.resolve_chat_model("roman_urdu") == "qwen2.5:1.5b"
    monkeypatch.setattr(service, "_get_client", lambda: client_with("qwen2.5:1.5b", "llama3.2:3b"))
    assert service.resolve_chat_model("urdu_script") == "llama3.2:3b"


@pytest.mark.parametrize("text,expected", [
    ("<think>secret</think>answer", "answer"),
    ("prefix<think>secret</think>answer", "prefixanswer"),
    ("a<think>x</think>b<think>y</think>c", "abc"),
    ("ordinary < text", "ordinary < text"),
    ("prefix<think>unfinished", "prefix"),
])
def test_stream_at_every_boundary(text, expected):
    for split in range(len(text) + 1):
        assert "".join(LLMService._filter_stream([text[:split], text[split:]])) == expected
    assert "".join(LLMService._filter_stream(iter(text))) == expected


def test_multi_gpu_detection(service, monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr("subprocess.check_output", lambda *args, **kwargs: "GPU A, 4096\nGPU B, 8192\n")
    hardware = service.detect_hardware(force_refresh=True)
    assert hardware["gpu_name"] == "GPU B"
    assert hardware["vram_mb"] == 8192


def test_windows_non_cuda_gpu(service, monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr("app.core.llm.sys.platform", "win32")

    def command(args, **kwargs):
        if args[0] == "powershell":
            return '{"Name":"AMD Radeon","AdapterRAM":4294967296}'
        raise FileNotFoundError("No NVIDIA tools")

    monkeypatch.setattr("subprocess.check_output", command)
    hardware = service.detect_hardware(force_refresh=True)
    assert hardware["gpu_available"]
    assert hardware["gpu_name"] == "AMD Radeon"
    assert hardware["recommended_profile"] == "gpu_budget"


def test_failed_running_inspection_is_not_an_empty_success(service, monkeypatch):
    client = client_with()
    client.ps.side_effect = ConnectionError("offline")
    monkeypatch.setattr(service, "_get_client", lambda: client)
    with pytest.raises(RuntimeError, match="offline"):
        service.get_running_models()


@pytest.mark.parametrize("url", ["/api/chat/models", "/api/models", "/api/models?detailed=true"])
def test_offline_model_poll_is_a_status_response(url, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.llm import llm

    def unavailable():
        raise RuntimeError("Ollama connection refused")

    monkeypatch.setattr(llm, "list_installed_models", unavailable)
    monkeypatch.setattr(llm, "list_installed_models_detailed", unavailable)
    monkeypatch.setattr(llm, "detect_hardware", lambda: {"device_type": "cpu"})
    response = TestClient(app).get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["models"] == []
    assert data["active_model"] == llm.model
    assert data["ollama"]["available"] is False
    assert "connection refused" in data["ollama"]["error"]
