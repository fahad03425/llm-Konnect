# Executive Test Verification Report: Module 6.3 (Schema Mapping & Validation)

- **Module**: 6.3 Schema Mapping and Validation Module
- **Execution Date**: 2026-10-04 16:22:11
- **Total Tests**: 24
- **Passed**: 24
- **Failed**: 0
- **Skipped**: 0
- **Total Execution Time**: 3.71 seconds
- **Overall Result**: **SUCCESS / ALL TESTS PASSED**

---

## 1. Identified Module Files Under Verification

1. **[`backend/app/schema/canonical.py`](../../backend/app/schema/canonical.py)**
   - Core canonical fields (`CORE_FIELDS`), row provenance tracking (`source_connector`, `source_row`), and core validation rules.
2. **[`backend/app/schema/mapper.py`](../../backend/app/schema/mapper.py)**
   - Deterministic header mapping pipeline (`suggest_mapping`), string normalization (`_normalize_header_string`), domain synonym resolution.
3. **[`backend/app/schema/normalize.py`](../../backend/app/schema/normalize.py)**
   - Type coercion, currency parsing (`_clean_money`), ambiguous date parsing (`_clean_date`), Urdu digit translation (`_convert_urdu_digits`).
4. **[`backend/app/schema/validate.py`](../../backend/app/schema/validate.py)**
   - Vectorized data quality audit (`validate`), problem tracking with physical row references, automated dataset cleaning (`clean`).
5. **[`backend/app/schema/profile.py`](../../backend/app/schema/profile.py)**
   - Deterministic schema signatures (`source_signature`) and atomic profile persistence supporting the one-time confirmation step.
6. **[`backend/app/api/routes.py`](../../backend/app/api/routes.py)**
   - End-to-end API endpoints (`POST /api/sources/preview`, `POST /api/sources/mapping/confirm`).

---

## 2. Test Suite Breakdown & Coverage

| Test File | Accompanying Documentation | Focus Area |
| :--- | :--- | :--- |
| [`test_01_header_mapping_and_synonyms.py`](./test_01_header_mapping_and_synonyms.py) | [`test_01_header_mapping_and_synonyms_report.md`](./test_01_header_mapping_and_synonyms_report.md) | Header normalization, pharmacy/ecommerce synonyms, missing required fields. |
| [`test_02_type_coercion_and_normalization.py`](./test_02_type_coercion_and_normalization.py) | [`test_02_type_coercion_and_normalization_report.md`](./test_02_type_coercion_and_normalization_report.md) | Financial strings, parentheses negation, day-first dates, Urdu numerals. |
| [`test_03_profile_signature_and_one_time_confirmation.py`](./test_03_profile_signature_and_one_time_confirmation.py) | [`test_03_profile_signature_and_one_time_confirmation_report.md`](./test_03_profile_signature_and_one_time_confirmation_report.md) | Order-invariant hashing, atomic profile save, recurring file auto-reuse. |
| [`test_04_data_validation_and_quality_engine.py`](./test_04_data_validation_and_quality_engine.py) | [`test_04_data_validation_and_quality_engine_report.md`](./test_04_data_validation_and_quality_engine_report.md) | Vectorized validation, problem tracking, quality scoring, automated clean(). |
| [`test_05_api_mapping_confirmation_workflow.py`](./test_05_api_mapping_confirmation_workflow.py) | [`test_05_api_mapping_confirmation_workflow_report.md`](./test_05_api_mapping_confirmation_workflow_report.md) | 3-phase preview → confirm → reuse API workflow, input validation. |

---

## 3. Individual Test Results

| Test File | Test Case | Status | Duration |
| :--- | :--- | :---: | :---: |
| `test_01_header_mapping_and_synonyms.py` | `test_normalize_header_strings` | [PASS] **PASSED** | 0.0005s |
| `test_01_header_mapping_and_synonyms.py` | `test_exact_canonical_core_field_matching` | [PASS] **PASSED** | 0.0007s |
| `test_01_header_mapping_and_synonyms.py` | `test_pharmacy_domain_synonym_matching` | [PASS] **PASSED** | 0.0051s |
| `test_01_header_mapping_and_synonyms.py` | `test_ecommerce_domain_synonym_matching` | [PASS] **PASSED** | 0.0013s |
| `test_01_header_mapping_and_synonyms.py` | `test_missing_required_fields_detection` | [PASS] **PASSED** | 0.0317s |
| `test_01_header_mapping_and_synonyms.py` | `test_unmapped_opaque_columns_remain_for_review` | [PASS] **PASSED** | 0.0023s |
| `test_02_type_coercion_and_normalization.py` | `test_convert_urdu_numerals` | [PASS] **PASSED** | 0.0002s |
| `test_02_type_coercion_and_normalization.py` | `test_clean_money_accounting_conventions` | [PASS] **PASSED** | 0.0004s |
| `test_02_type_coercion_and_normalization.py` | `test_clean_date_variations` | [PASS] **PASSED** | 0.008s |
| `test_02_type_coercion_and_normalization.py` | `test_apply_mapping_full_normalization` | [PASS] **PASSED** | 0.0121s |
| `test_02_type_coercion_and_normalization.py` | `test_apply_mapping_omit_extras_when_flag_false` | [PASS] **PASSED** | 0.0065s |
| `test_03_profile_signature_and_one_time_confirmation.py` | `test_signature_deterministic_and_order_insensitive` | [PASS] **PASSED** | 0.0003s |
| `test_03_profile_signature_and_one_time_confirmation.py` | `test_signature_ignores_unnamed_artifact_columns` | [PASS] **PASSED** | 0.0002s |
| `test_03_profile_signature_and_one_time_confirmation.py` | `test_one_time_confirmation_save_and_lookup_lifecycle` | [PASS] **PASSED** | 0.0047s |
| `test_03_profile_signature_and_one_time_confirmation.py` | `test_list_and_delete_profiles` | [PASS] **PASSED** | 0.0107s |
| `test_03_profile_signature_and_one_time_confirmation.py` | `test_compatible_mapping_subset_validation` | [PASS] **PASSED** | 0.0006s |
| `test_04_data_validation_and_quality_engine.py` | `test_clean_valid_dataframe_passes_with_perfect_score` | [PASS] **PASSED** | 0.0354s |
| `test_04_data_validation_and_quality_engine.py` | `test_detects_completely_empty_rows` | [PASS] **PASSED** | 0.0241s |
| `test_04_data_validation_and_quality_engine.py` | `test_detects_unparseable_conversion_failures` | [PASS] **PASSED** | 0.011s |
| `test_04_data_validation_and_quality_engine.py` | `test_pharmacy_domain_expiry_validation_rules` | [PASS] **PASSED** | 0.0285s |
| `test_04_data_validation_and_quality_engine.py` | `test_automated_cleaning_removes_empty_rows` | [PASS] **PASSED** | 0.0122s |
| `test_05_api_mapping_confirmation_workflow.py` | `test_end_to_end_confirmation_and_reuse_lifecycle` | [PASS] **PASSED** | 0.5419s |
| `test_05_api_mapping_confirmation_workflow.py` | `test_confirm_mapping_missing_file_404` | [PASS] **PASSED** | 0.0079s |
| `test_05_api_mapping_confirmation_workflow.py` | `test_confirm_mapping_invalid_schema_400` | [PASS] **PASSED** | 0.0545s |

---

## 4. Architectural Verification Verdict

Module 6.3 completely fulfills its design requirements:
- **Zero LLM Hallucination**: Schema mapping proposals are 100% deterministic, immediate, and fully reproducible.
- **One-Time Confirmation Step**: Users verify each unique source structure once; recurring daily or monthly files auto-map with zero redundant friction.
- **Regional Localization**: Handles Pakistani financial nuances, day-first dates, and Urdu numerals effortlessly.
