# Test Documentation Report: 02 — Model Lifecycle & VRAM Memory Management

## 1. Test Suite Identification
- **Module ID**: Module 6.1 (Local LLM Inference Module)
- **Test File**: [`test_02_model_lifecycle_management.py`](./test_02_model_lifecycle_management.py)
- **Target Implementation File**: [`backend/app/core/llm.py`](../../backend/app/core/llm.py) (Lines 382–424, 480–583)
- **Primary Methods Tested**:
  - `LLMService.pull_model(model_name: str)`
  - `LLMService.pull_model_stream(model_name: str)`
  - `LLMService.load_model(model_name: str, keep_alive: str)`
  - `LLMService.unload_model(model_name: str)`
  - `LLMService.get_running_models()`
  - `LLMService.delete_model(model_name: str)`
  - `LLMService.list_installed_models_detailed()`

---

## 2. Tested Business Requirements & Logic
1. **Model Pulling & Download Tracking**:
   - Synchronous pull triggers internal client download and reports status.
   - Streaming pull yields byte progress updates formatted as dictionaries with `completed`, `total`, and `percent`.
   - Edge cases verified: zero-division safety when `total=0` during manifest handshakes, and network disconnection yielding graceful error dictionaries without process crashing.
2. **Explicit VRAM Pre-warming & Eviction**:
   - `load_model`: Triggers a 0-token ping with user-defined `keep_alive` duration (e.g. `30m`, `45m`), loading weights into GPU VRAM in advance.
   - `unload_model`: Triggers eviction using `keep_alive=0`, freeing VRAM immediately for other applications or larger model loads.
3. **Loaded Model Inspection (`client.ps`)**:
   - Inspects active models in memory, reporting model name, byte footprint, VRAM consumption in MB, and expiration times.
4. **Metadata Discovery**:
   - Extracts architecture details (`family`, `parameter_size`, `quantization_level`), file size in GB, and active flag indicator.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_pull_model_sync_success` | Synchronous model download | Returns success status dictionary | **PASS** |
| `test_pull_model_sync_failure_raises_runtime_error` | Download failure handling | Raises `RuntimeError` with clear diagnostic | **PASS** |
| `test_pull_model_stream_progress_calculation` | Streaming download with incremental chunks | Correctly computes progress (0%, 25%, 100%) | **PASS** |
| `test_pull_model_stream_connection_drop_recovery` | Mid-stream socket disconnection | Yields structured error chunk without crashing | **PASS** |
| `test_load_model_prewarm_into_vram` | Pre-load model weights into VRAM | Passes target model and `keep_alive` duration | **PASS** |
| `test_unload_model_eviction_from_vram` | Immediate memory eviction | Issues ping with `keep_alive=0` | **PASS** |
| `test_get_running_models_vram_telemetry` | Query Ollama `ps` running models | Returns running models with computed VRAM MB | **PASS** |
| `test_delete_model_command` | Deleting model from local disk | Dispatches delete command to daemon | **PASS** |
| `test_list_installed_models_detailed_metadata` | Inspect installed model details | Extracts family, parameter size, and size in GB | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.1 Local LLM Inference Module/test_02_model_lifecycle_management.py" -v`
- **Assurance**: Mocked Ollama client ensures zero unwanted disk writes or bandwidth consumption while validating client protocols.
