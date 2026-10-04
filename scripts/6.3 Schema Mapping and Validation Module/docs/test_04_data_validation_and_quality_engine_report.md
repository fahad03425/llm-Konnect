# Test Documentation Report: 04 — Vectorized Data Quality & Validation Engine

## 1. Test Suite Identification
- **Module ID**: Module 6.3 (Schema Mapping and Validation Module)
- **Test File**: [`test_04_data_validation_and_quality_engine.py`](./test_04_data_validation_and_quality_engine.py)
- **Target Implementation Files**: [`backend/app/schema/validate.py`](../../backend/app/schema/validate.py) and [`backend/app/schema/canonical.py`](../../backend/app/schema/canonical.py)
- **Primary Functions Tested**:
  - `validate(canonical_df: pd.DataFrame, domain: Optional[str], table_kind: str) -> ValidationReport`
  - `clean(canonical_df: pd.DataFrame, domain: Optional[str]) -> Tuple[pd.DataFrame, CleaningSummary]`
  - `validate_core_dataframe(df: pd.DataFrame, table_kind: str) -> List[Problem]`

---

## 2. Tested Business Requirements & Logic
1. **Vectorized Quality Auditing**:
   - Evaluates incoming tabular records using high-performance vectorized Pandas masks rather than slow iterative loops.
   - Evaluates both core domain-agnostic rules and active domain pack rules (e.g. pharmacy expired medicines, inventory thresholds).
2. **Deterministic Quality Scoring**:
   - Emits a Pydantic `ValidationReport` containing:
     - `is_valid`: Boolean flag indicating if any blocking errors exist.
     - `quality_score`: Normalized numerical score (0 to 100) reflecting data cleanliness.
     - `error_count` & `warning_count`: Aggregated problem frequencies.
     - `problems`: Structured list of `Problem` objects detailing problem codes, affected fields, user messages, and sample values.
3. **Physical Row Traceability**:
   - When a validation rule fails, the report pinpoints the exact `source_row` corresponding to the original spreadsheet row number, allowing non-technical operators to correct the specific line in their POS export.
4. **Automated Cleaning Pipeline (`clean`)**:
   - Removes empty rows and repairs minor structural anomalies, producing a sanitized DataFrame and a transparent `CleaningSummary` documenting all actions taken.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_clean_valid_dataframe_passes_with_perfect_score` | High-quality canonical records | `is_valid=True`, 0 errors, quality score ≥ 95 | **PASS** |
| `test_detects_completely_empty_rows` | Datasets with blank rows | Flags `EMPTY_ROW` problem and tracks exact `source_row` | **PASS** |
| `test_detects_unparseable_conversion_failures` | Coercion failures attached to attributes | Surfaces `UNPARSEABLE_DATE` and `UNPARSEABLE_NUMBER` | **PASS** |
| `test_pharmacy_domain_expiry_validation_rules` | Medicines sold after expiration date | Pharmacy domain rule flags expired product sale | **PASS** |
| `test_automated_cleaning_removes_empty_rows` | Purging corrupt empty rows | Drops blank row and records `CleaningSummary` action | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.3 Schema Mapping and Validation Module/test_04_data_validation_and_quality_engine.py" -v`
- **Assurance**: Verified across domain-agnostic structural defects and specialized pharmaceutical inventory rules.
