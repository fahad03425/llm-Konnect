# Test Documentation Report: 01 — Hardware Detection & Profile Recommendation

## 1. Test Suite Identification
- **Module ID**: Module 6.1 (Local LLM Inference Module)
- **Test File**: [`test_01_hardware_detection_and_profiles.py`](./test_01_hardware_detection_and_profiles.py)
- **Target Implementation File**: [`backend/app/core/llm.py`](../../backend/app/core/llm.py) (Lines 149–274)
- **Primary Method Tested**: `LLMService.detect_hardware(force_refresh: bool)`

---

## 2. Tested Business Requirements & Logic
1. **Dynamic Hardware Probing**:
   - Automated detection of system compute capabilities without hardcoded device assumptions.
   - Hierarchy of probing mechanisms:
     1. PyTorch CUDA (`torch.cuda.is_available()`, `torch.cuda.get_device_properties()`).
     2. Apple Metal Performance Shaders (`torch.backends.mps.is_available()`).
     3. Command-line query via `nvidia-smi` formatted CSV stream.
     4. Windows CIM / PowerShell `Win32_VideoController` adapter query for AMD/Intel/NVIDIA discrete adapters.
2. **Profile Recommendation Mapping**:
   - **CPU Only / Low Resource**: Maps to `cpu_lightweight` profile; recommends `phi4-mini`, `phi3:mini`, `llama3.2:3b`, `qwen2.5:3b`, `qwen2.5:1.5b`.
   - **Budget GPU (< 5.5 GB VRAM)**: Maps to `gpu_budget`; recommends `qwen2.5:3b`, `phi4-mini`, `llama3.2:3b`, `gemma2:2b`.
   - **Standard GPU (5.5 GB to 12 GB VRAM)**: Maps to `gpu_standard`; recommends `qwen2.5:7b`, `mistral:7b`, `llama3.1:8b`, `phi4-mini`.
   - **Heavy GPU (≥ 12 GB VRAM)**: Maps to `gpu_heavy`; recommends `qwen2.5:14b`, `qwen2.5:7b`, `mistral:7b`, `llama3.1:8b`.
3. **Caching & Telemetry**:
   - Hardware profiling is cached in `self._cached_hw` to eliminate subprocess latency during regular operations.
   - Cache invalidation and re-probe is supported via `force_refresh=True`.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_cpu_only_detection_and_phi4_mini_recommendation` | System without GPU or with CLI unavailable | `gpu_available=False`, profile=`cpu_lightweight`, recommends `phi4-mini` | **PASS** |
| `test_budget_gpu_profile` | System with 3 GB VRAM (e.g. GTX 1050) | Profile=`gpu_budget`, recommends `phi4-mini`, `qwen2.5:3b` | **PASS** |
| `test_standard_gpu_profile_qwen_mistral` | System with 8 GB VRAM (e.g. RTX 4070) | Profile=`gpu_standard`, recommends `qwen2.5:7b`, `mistral:7b` | **PASS** |
| `test_heavy_gpu_profile_qwen_14b` | System with 16 GB VRAM (e.g. RTX 4090) | Profile=`gpu_heavy`, recommends `qwen2.5:14b` | **PASS** |
| `test_apple_metal_unified_memory` | macOS system with MPS enabled | Detects Apple Metal unified memory | **PASS** |
| `test_nvidia_smi_cli_fallback_parsing` | Torch absent, `nvidia-smi` CLI query used | Successfully parses CSV memory total and assigns GPU profile | **PASS** |
| `test_hardware_caching_and_force_refresh` | Validates caching and forced invalidation | Cache prevents redundant system calls unless `force_refresh=True` | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.1 Local LLM Inference Module/test_01_hardware_detection_and_profiles.py" -v`
- **Assurance**: All assertions verified with deterministic mock fixtures isolating external device drivers and subprocesses.
