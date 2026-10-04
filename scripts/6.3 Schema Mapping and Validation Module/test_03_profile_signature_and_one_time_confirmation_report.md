# Test Documentation Report: 03 — Profile Signature Generation & One-Time Confirmation Pattern

## 1. Test Suite Identification
- **Module ID**: Module 6.3 (Schema Mapping and Validation Module)
- **Test File**: [`test_03_profile_signature_and_one_time_confirmation.py`](./test_03_profile_signature_and_one_time_confirmation.py)
- **Target Implementation File**: [`backend/app/schema/profile.py`](../../backend/app/schema/profile.py)
- **Primary Functions Tested**:
  - `source_signature(columns, connector_type, domain, source) -> str`
  - `save_profile(signature: str, mapping: Dict[str, str], label: str)`
  - `find_profile(signature: str) -> Optional[Dict]`
  - `list_profiles() -> List[Dict]`
  - `delete_profile(signature: str) -> bool`
  - `compatible_mapping(mapping, columns) -> Dict[str, str]`

---

## 2. Tested Business Requirements & Logic
1. **The One-Time Confirmation Paradigm**:
   - In businesses with recurring data sources (daily POS CSV dumps, monthly Tally export workbooks), users should **never** have to re-map the same file columns twice.
   - On the very first upload of an unfamiliar file format, the system flags the source as unconfirmed and presents the mapping proposal.
   - Once the user clicks "Confirm", the mapping is atomically written to `data/storage/mapping_profiles/<signature>.json`.
   - On every subsequent upload of that source format, the system computes the signature, finds the saved profile, and automatically ingests the dataset with zero manual confirmation.
2. **Stable Source Fingerprinting (`source_signature`)**:
   - Column order and capitalization vary between export software versions.
   - `source_signature` normalizes, lowercases, strips whitespace, and sorts the source column list before MD5 hashing.
   - Automatically drops spreadsheet trailing artifacts (`Unnamed: 4`, `Unnamed: 5`) so empty columns do not break signature matching.
3. **Atomic File Persistence**:
   - Saves profiles via temporary file write and replacement (`tempfile.mkstemp` + `os.replace`), preventing partial writes or file corruption if a server reload occurs during save.
4. **Schema Evolution Compatibility (`compatible_mapping`)**:
   - If a source drops an optional column in a subsequent month, `compatible_mapping` filters out nonexistent keys, allowing existing profiles to gracefully map without breaking.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_signature_deterministic_and_order_insensitive` | Hashing with inverted order and casing | Exact identical MD5 hash signature | **PASS** |
| `test_signature_ignores_unnamed_artifact_columns` | Eliminating `Unnamed:` trailing columns | Clean signature matching the non-empty columns | **PASS** |
| `test_one_time_confirmation_save_and_lookup_lifecycle` | Initial missing check → save → instant retrieval | Returns None first, then returns confirmed profile | **PASS** |
| `test_list_and_delete_profiles` | Profile inventory and deletion | Lists both profiles; cleanly deletes target | **PASS** |
| `test_compatible_mapping_subset_validation` | Source dropped an optional column | Filters out dropped column from active mapping | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.3 Schema Mapping and Validation Module/test_03_profile_signature_and_one_time_confirmation.py" -v`
- **Assurance**: Verified with temporary profile directories isolated from live production configuration.
