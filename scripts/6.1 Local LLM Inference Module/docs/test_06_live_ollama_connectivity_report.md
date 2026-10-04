# Test Documentation Report: 06 — Live Ollama Connectivity, Autostart & Environment Health

## 1. Test Suite Identification
- **Module ID**: Module 6.1 (Local LLM Inference Module)
- **Test File**: [`test_06_live_ollama_connectivity.py`](./test_06_live_ollama_connectivity.py)
- **Target Implementation File**: [`backend/app/core/llm.py`](../../backend/app/core/llm.py) (Lines 49–145)
- **Primary Methods Tested**:
  - `LLMService.find_ollama_executable()`
  - `LLMService.is_ollama_alive(timeout: float)`
  - `LLMService.ensure_ollama_running(wait_timeout: float)`

---

## 2. Tested Business Requirements & Logic
1. **Binary Auto-Discovery**:
   - Searches `PATH` via `shutil.which`.
   - On Windows, automatically inspects candidate directories:
     - `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`
     - `%ProgramFiles%\Ollama\ollama.exe`
     - `%ProgramFiles(x86)%\Ollama\ollama.exe`
     - `%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe`
   - On Unix/macOS, inspects `/usr/local/bin/ollama`, `/usr/bin/ollama`, and `~/.ollama/bin/ollama`.
2. **Daemon Health Probe**:
   - Executes lightweight HTTP `GET /api/tags` request with 800ms timeout to avoid application blocking.
3. **Live End-to-End Smoke Test**:
   - If Ollama daemon is active, performs discovery of installed models.
   - If models are present, executes a live 1-turn generation prompt (`Reply with the single word 'READY'`).
   - If Ollama is offline (such as in headless CI or disconnected environments), test gracefully skips with informative diagnostic messaging, preventing false negative build failures.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_ollama_executable_discovery` | Probes system for Ollama executable | Returns valid binary path string or None safely | **PASS** |
| `test_live_daemon_status_probe` | Lightweight HTTP daemon ping | Returns boolean flag without unhandled timeout | **PASS** |
| `test_live_tags_or_graceful_offline_report` | List models if server is active | Queries active models or skips gracefully if offline | **PASS / SKIP** |
| `test_live_inference_smoke_test` | End-to-end 1-turn inference verification | Verifies live response generation if model available | **PASS / SKIP** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.1 Local LLM Inference Module/test_06_live_ollama_connectivity.py" -v`
- **Assurance**: Live probe safeguards production and staging pipelines while allowing instant validation on local developer workstations.
