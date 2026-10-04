# Test Documentation Report: 03 — Model Switching, Resolution Hierarchy & Capability Validation

## 1. Test Suite Identification
- **Module ID**: Module 6.1 (Local LLM Inference Module)
- **Test File**: [`test_03_model_switching_and_resolution.py`](./test_03_model_switching_and_resolution.py)
- **Target Implementation File**: [`backend/app/core/llm.py`](../../backend/app/core/llm.py) (Lines 278–337, 425–455)
- **Primary Methods Tested**:
  - `LLMService.set_active_model(model_name: str)`
  - `LLMService._resolve_model(client, override_model, installed)`
  - `LLMService._match_model(target, installed)`
  - `LLMService.resolve_chat_model(language: Optional[str])`
  - `LLMService.get_status()`

---

## 2. Tested Business Requirements & Logic
1. **Dynamic Model Switching**:
   - Model switching updates the runtime active model reference (`self.model` and `settings.llm_model`).
   - Persists state atomically to disk (`model_settings.json`) using a temporary file replacement pattern protected by `self._model_lock` to avoid file corruption.
2. **Capability Validation**:
   - Validates that the requested model supports text completion. Rejects models tagged strictly for embedding (`nomic-embed-text`) or vision-only.
   - Prevents activation of non-installed or empty model strings with helpful user-facing exceptions.
3. **Resolution & Tag Matching Hierarchy**:
   - Matches exact string (`mistral:7b`).
   - Resolves `:latest` tag alias for untagged model inputs (e.g. `phi4-mini` -> `phi4-mini:latest`).
   - Handles quantization and instruct suffixes (e.g. `qwen2.5:7b-instruct-q4_K_M`).
   - Falls back gracefully to hardware-recommended models if the configured default model was uninstalled.
4. **Bilingual / Language Policy Routing**:
   - Automatically routes Roman Urdu or Urdu script prompts to bilingual-specialized models (e.g. `llama3.2:3b`) if installed, while using standard models for English queries.
5. **Connectivity & Status Telemetry**:
   - `get_status()` isolates network connectivity from model availability, returning structured JSON for UI diagnostics.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_switch_active_model_persistence` | Switch model and verify file write | Updates instance state & writes to `model_settings.json` | **PASS** |
| `test_switch_model_validation_rejects_non_installed` | Requesting uninstalled model | Rejects with `ValueError` ("is not installed") | **PASS** |
| `test_switch_model_validation_rejects_embedding_only_model` | Requesting embed-only model | Rejects with `ValueError` ("does not support text completion")| **PASS** |
| `test_switch_model_rejects_empty_name` | Whitespace or empty model string | Rejects with `ValueError` ("Model name is required") | **PASS** |
| `test_resolve_exact_match` | Explicit model string match | Directly resolves exact tag | **PASS** |
| `test_resolve_latest_tag_alias` | Untagged name resolution | Successfully maps to `:latest` | **PASS** |
| `test_resolve_quantization_suffix_match` | Resolving base tag with Q4/Instruct suffix | Matches quantized variant | **PASS** |
| `test_resolve_hardware_fallback_when_default_missing` | Active model absent | Automatically falls back to hardware profile recommendation | **PASS** |
| `test_resolve_chat_model_language_routing_roman_urdu` | Language specified as Roman Urdu | Routes to `llama3.2:3b` | **PASS** |
| `test_resolve_chat_model_standard_english` | Language specified as English | Retains active general model | **PASS** |
| `test_get_status_online_and_offline` | Probe status under online and offline states | Returns structured availability dict without crashing | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.1 Local LLM Inference Module/test_03_model_switching_and_resolution.py" -v`
- **Assurance**: Atomic file replacement and thread-safe lock mechanisms prevent race conditions across parallel requests.
