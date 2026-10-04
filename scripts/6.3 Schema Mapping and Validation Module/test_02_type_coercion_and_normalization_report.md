# Test Documentation Report: 02 — Type Coercion, Currency Normalization & Date Cleaning

## 1. Test Suite Identification
- **Module ID**: Module 6.3 (Schema Mapping and Validation Module)
- **Test File**: [`test_02_type_coercion_and_normalization.py`](./test_02_type_coercion_and_normalization.py)
- **Target Implementation File**: [`backend/app/schema/normalize.py`](../../backend/app/schema/normalize.py)
- **Primary Functions Tested**:
  - `apply_mapping(df: pd.DataFrame, mapping: Dict[str, str], domain: Optional[str], keep_extras: bool) -> pd.DataFrame`
  - `_clean_money(val: any) -> Optional[float]`
  - `_clean_date(val: any) -> pd.Timestamp`
  - `_convert_urdu_digits(text: str) -> str`

---

## 2. Tested Business Requirements & Logic
1. **Financial & Currency Cleaning (`_clean_money`)**:
   - Accounts for regional Pakistani accounting standards:
     - Parses currency symbols and abbreviations (`Rs.`, `PKR`, `₨`).
     - Removes invoice typography (`/-`, `/=`).
     - Parentheses accounting convention: values formatted as `(1,250)` or `(500.00)` are correctly parsed as negative numbers (`-1250.0`, `-500.0`).
     - Normalizes comma separators and spaces (`"2,400.75"` → `2400.75`).
2. **Ambiguous Date Parsing (`_clean_date`)**:
   - Default assumption in Pakistani and UK accounting is **Day-First** (`DD/MM/YYYY`). Dates like `15/01/2026` or `28-02-2026` parse correctly into January 15 and February 28 respectively.
   - Resolves Excel serial date timestamps (floating-point integers from legacy `.xls` sheets) to ISO timestamps.
   - Gracefully converts unparseable strings or missing timestamps to `pd.NaT`.
3. **Eastern Arabic / Urdu Numeral Transliteration (`_convert_urdu_digits`)**:
   - POS receipts in Pakistan often record quantities and dates in Urdu numerals (`۰۱۲۳۴۵۶۷۸۹`).
   - Replaces Urdu numerals with ASCII standard digits prior to downstream numeric and date conversion.
4. **Canonical Mapping Application (`apply_mapping`)**:
   - Renames raw source columns to standardized canonical fields.
   - Enforces type casting over all canonical numeric, monetary, and date fields.
   - Retains unmapped metadata columns safely under the `_extra.` namespace when `keep_extras=True` to ensure zero data loss.
   - Preserves row-level traceability attributes (`source_connector`, `source_row`).

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_convert_urdu_numerals` | Urdu numerals in prices, dates, strings | Full transliteration to ASCII standard digits | **PASS** |
| `test_clean_money_accounting_conventions` | Currency symbols, `(negatives)`, `/-` | Clean signed floating point representation | **PASS** |
| `test_clean_date_variations` | ISO, Day-First `DD/MM/YYYY`, Excel serials | Accurate `pd.Timestamp` output | **PASS** |
| `test_apply_mapping_full_normalization` | Full pipeline transformation on raw DataFrame | Typed canonical DataFrame + `_extra.` preservation | **PASS** |
| `test_apply_mapping_omit_extras_when_flag_false` | Dropping extra columns when `keep_extras=False` | Canonical columns only, extra columns omitted | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.3 Schema Mapping and Validation Module/test_02_type_coercion_and_normalization.py" -v`
- **Assurance**: Verified with currency string edge cases, negative accounting brackets, and Urdu receipt formats.
