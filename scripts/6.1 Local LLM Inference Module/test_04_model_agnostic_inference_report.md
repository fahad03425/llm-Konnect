# Test Documentation Report: 04 — Model-Agnostic Inference Core & Output Sanitization

## 1. Test Suite Identification
- **Module ID**: Module 6.1 (Local LLM Inference Module)
- **Test File**: [`test_04_model_agnostic_inference.py`](./test_04_model_agnostic_inference.py)
- **Target Implementation File**: [`backend/app/core/llm.py`](../../backend/app/core/llm.py) (Lines 339–380, 585–706)
- **Primary Methods Tested**:
  - `LLMService.generate(prompt, system_prompt, keep_alive, options, model)`
  - `LLMService.chat(messages, keep_alive, options, model)`
  - `LLMService.chat_stream(messages, keep_alive, options, model)`
  - `LLMService._clean_output(text: str)`
  - `LLMService._filter_stream(tokens)`
  - `LLMService._get_default_options(custom_opts)`
  - `LLMService._log_inference_timing(operation, model, response)`

---

## 2. Tested Business Requirements & Logic
1. **Model-Agnostic Contract**:
   - Upstream callers in RAG pipelines, KPI engines, and report generators interact with a uniform API (`generate`, `chat`, `chat_stream`) without model-specific prompt engineering or vendor wrappers.
   - Per-request model overrides (`model="mistral:7b"`) permit specialized query routing while maintaining interface consistency.
2. **Output Sanitization & Reasoning Filter**:
   - Modern reasoning models (e.g., DeepSeek R1, QwQ) emit chain-of-thought tokens wrapped in `<think>...</think>`.
   - The module sanitizes completed text via `_clean_output()` and streams tokens without intermediate thought dumps via `_filter_stream()`.
   - Edge cases tested: multi-line tags, unclosed opening tags, lone closing tags, and stripping internal debug artifacts.
3. **Telemetry & Inference Timings**:
   - Automatically parses Ollama duration metrics (nanoseconds converted to fractional seconds): `total_duration`, `load_duration`, `prompt_eval_duration`, `eval_duration`, `prompt_eval_count`, `eval_count`.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_generate_single_turn_inference` | Single prompt + system instructions | Returns sanitized completion, dispatches correct schema | **PASS** |
| `test_generate_with_model_override` | Request with explicit `model` override | Dispatches inference to specified model override | **PASS** |
| `test_chat_multi_turn_inference` | Multi-turn message history preservation | Preserves complete dialogue role history | **PASS** |
| `test_chat_stream_filters_think_tags` | Streaming tokens with `<think>` blocks | Streams clean tokens while suppressing internal reasoning tokens | **PASS** |
| `test_clean_output_reasoning_edge_cases` | Edge case formatting in raw model outputs | Accurately removes tags, unclosed blocks, and debug artifacts | **PASS** |
| `test_default_options_injection` | Configuration defaults merging | Injects CPU thread counts, context window, and temperature | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.1 Local LLM Inference Module/test_04_model_agnostic_inference.py" -v`
- **Assurance**: Live token streaming generators and buffer logic validated against chunk fragmentation and boundary split scenarios.
