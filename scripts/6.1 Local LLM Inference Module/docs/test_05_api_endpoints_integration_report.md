# Test Documentation Report: 05 — REST & Streaming API Endpoints and Concurrency Safety

## 1. Test Suite Identification
- **Module ID**: Module 6.1 (Local LLM Inference Module)
- **Test File**: [`test_05_api_endpoints_integration.py`](./test_05_api_endpoints_integration.py)
- **Target Implementation File**: [`backend/app/api/models.py`](../../backend/app/api/models.py) (Lines 1–149)
- **Primary Routes Tested**:
  - `GET /api/models`
  - `GET /api/models/hardware`
  - `GET /api/models/running`
  - `GET /api/models/info/{model_name}`
  - `POST /api/models/select`
  - `POST /api/models/load`
  - `POST /api/models/unload`
  - `POST /api/models/pull`
  - `DELETE /api/models/{model_name}`

---

## 2. Tested Business Requirements & Logic
1. **Model Discovery & Hardware Diagnostics**:
   - `GET /api/models`: Returns list of models, active model, and Ollama connectivity status.
   - `GET /api/models/hardware`: Provides CPU core counts, GPU name, VRAM, and profile recommendations for UI initialization.
   - `GET /api/models/running`: Supplies real-time loaded model memory footprint.
2. **Model Control & VRAM Allocation**:
   - `POST /api/models/select`: Updates system-wide active model. Enforces 400 Bad Request if requested model is uninstalled.
   - `POST /api/models/load`: Accepts `keep_alive` payload to pre-warm model into VRAM.
   - `POST /api/models/unload`: Triggers immediate VRAM cleanup.
3. **Streaming NDJSON Download**:
   - `POST /api/models/pull` with `stream=true`: Returns chunked `application/x-ndjson` stream allowing the frontend progress bar (`ModelSettings.tsx`) to render percent complete smoothly.
4. **Thread-Safety & Concurrent Stress**:
   - Executes 24 interleaved parallel model switches and inferences across 8 worker threads to guarantee atomic updates without race conditions or memory deadlocks.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_get_hardware_endpoint` | Hardware status endpoint | HTTP 200, returns hardware telemetry and active model | **PASS** |
| `test_list_models_basic` | Basic installed models listing | HTTP 200, returns model array | **PASS** |
| `test_list_models_detailed` | Detailed metadata flag query | HTTP 200, includes size in GB, family, parameter count | **PASS** |
| `test_list_running_models_endpoint` | In-memory VRAM model listing | HTTP 200, reports loaded models count and VRAM MB | **PASS** |
| `test_get_model_info_endpoint` | Single model parameter lookup | HTTP 200, returns model parameter manifest | **PASS** |
| `test_get_model_info_not_found` | Lookup missing model info | HTTP 404 Not Found | **PASS** |
| `test_select_model_endpoint_success` | REST model activation switch | HTTP 200, returns updated active_model | **PASS** |
| `test_select_model_endpoint_invalid_model` | Switch to missing model | HTTP 400 Bad Request with descriptive message | **PASS** |
| `test_load_and_unload_endpoints` | API VRAM pre-warm and eviction | HTTP 200 for both `/load` and `/unload` | **PASS** |
| `test_pull_model_streaming_ndjson` | Streaming pull progress endpoint | HTTP 200, `application/x-ndjson` chunked payload | **PASS** |
| `test_delete_model_endpoint` | REST model deletion | HTTP 200, returns deleted confirmation | **PASS** |
| `test_concurrent_switching_and_inference_stress` | High-load parallel switching and inference | 24 tasks succeed across 8 threads with zero lockups | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.1 Local LLM Inference Module/test_05_api_endpoints_integration.py" -v`
- **Assurance**: FastAPI `TestClient` evaluates routing, serialization, schema validation, and streaming generator responses end-to-end.
