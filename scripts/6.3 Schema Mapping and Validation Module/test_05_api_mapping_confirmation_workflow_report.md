# Test Documentation Report: 05 — API Mapping Confirmation Workflow & End-to-End Integration

## 1. Test Suite Identification
- **Module ID**: Module 6.3 (Schema Mapping and Validation Module)
- **Test File**: [`test_05_api_mapping_confirmation_workflow.py`](./test_05_api_mapping_confirmation_workflow.py)
- **Target Implementation File**: [`backend/app/api/routes.py`](../../backend/app/api/routes.py) (Lines 189–235)
- **Primary Routes Tested**:
  - `POST /api/sources/preview`
  - `POST /api/sources/mapping/confirm`

---

## 2. Tested Business Requirements & Logic
1. **Interactive One-Time Confirmation Lifecycle**:
   - **Phase 1: Initial Preview & Fingerprinting**:
     - Client calls `POST /api/sources/preview`.
     - System extracts sample records, detects connector type, computes the deterministic `signature`, and checks for existing saved profiles.
     - For new sources, `saved_profile` is returned as `null` along with a generated `mapping_proposal`.
   - **Phase 2: One-Time User Confirmation Step**:
     - Client submits user-verified mapping to `POST /api/sources/mapping/confirm` with `save_profile: true`.
     - System executes schema validation, normalizes data columns into canonical fields, and atomically writes `<signature>.json` into the profile repository.
   - **Phase 3: Automated Profile Reuse**:
     - When tomorrow's recurring export or another file with identical headers arrives, `POST /api/sources/preview` detects the existing profile.
     - Returns `saved_profile` populated with the confirmed mapping, completely bypassing the manual review step.
2. **Defensive API Guardrails**:
   - Returns HTTP 404 when file path does not exist on disk or network.
   - Returns HTTP 400 when submitted mapping references column names that do not exist in the source dataset.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_end_to_end_confirmation_and_reuse_lifecycle` | 3-phase preview → confirm → reuse workflow | Verified profile creation and subsequent instant reuse | **PASS** |
| `test_confirm_mapping_missing_file_404` | Attempting confirmation on missing file | HTTP 404 Not Found | **PASS** |
| `test_confirm_mapping_invalid_schema_400` | Mapping non-existent source column name | HTTP 400 Bad Request with validation detail | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.3 Schema Mapping and Validation Module/test_05_api_mapping_confirmation_workflow.py" -v`
- **Assurance**: Executed against FastAPI TestClient with temporary file-based storage isolation.
