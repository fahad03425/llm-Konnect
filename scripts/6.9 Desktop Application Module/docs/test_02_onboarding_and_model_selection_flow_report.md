# Test Report 02: Onboarding & Model Selection Desktop Flow

## Executive Summary
This report validates the first-time user onboarding journey and the model management interface of Module 6.9. 

A primary tenet of the LLM-Konnect platform is **zero command-line exposure**: non-technical users such as retail store operators or pharmacists should never be prompted to enter commands like `ollama run` or edit environment files. The desktop interface automates hardware detection and recommends models accordingly.

---

## Test Cases & Specifications

| Test Case | Target Component | Validated User Experience | Status |
| :--- | :--- | :--- | :---: |
| `test_onboarding_modal_domain_selection` | `OnboardingModal.tsx` | Guided first-run wizard presenting domain presets (Pharmacy, E-Commerce, General). | **PASS** |
| `test_hardware_profile_auto_detection_flow` | `ModelSettings.tsx` | Displays auto-detected hardware profile (CPU vs GPU/VRAM) and matched model recommendations. | **PASS** |
| `test_model_switching_interface_controls` | `ModelSettings.tsx` Actions | One-click model switching with real-time download and activation progress indicators. | **PASS** |
| `test_settings_modal_integration` | `SettingsModal.tsx` | Clean modal dialog providing global preferences and hardware configuration. | **PASS** |
| `test_backend_model_status_api_contract` | `GET /api/models/status` | Confirms HTTP API contract powering the desktop UI's model status indicators. | **PASS** |

---

## Hardware-to-Model Mapping Logic
The desktop UI reflects the following automated recommendations:
- **CPU-Only Hardware**: Defaults to **Phi-4-mini (3.8B)** for fast, responsive offline execution without GPU requirement.
- **GPU-Available Hardware (>= 8GB VRAM)**: Defaults to **Qwen 2.5 (7B)** or **Mistral (7B)** for rich multi-step reasoning and accelerated vector extraction.
- **Model Switching**: Executed behind a model-agnostic internal interface (`LocalLLM`), seamlessly swapping active models without application reboots.
