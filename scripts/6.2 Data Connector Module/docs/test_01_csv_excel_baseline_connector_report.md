# Test Documentation Report: 01 — CSV and Excel Universal Baseline Connectors

## 1. Test Suite Identification
- **Module ID**: Module 6.2 (Data Connector Module)
- **Test File**: [`test_01_csv_excel_baseline_connector.py`](./test_01_csv_excel_baseline_connector.py)
- **Target Implementation File**: [`backend/app/connectors/csv_excel.py`](../../backend/app/connectors/csv_excel.py)
- **Primary Classes Tested**:
  - `CSVConnector`
  - `ExcelConnector`
  - Helper functions: `_sniff_csv_format_from_bytes()`, `_find_excel_header()`

---

## 2. Tested Business Requirements & Logic
1. **Universal Tabular Baseline**:
   - Files exported from pharmacy POS systems, ERP software, or legacy spreadsheets are imported seamlessly regardless of file layout.
   - Automatic character encoding detection via `chardet` (e.g. UTF-8, Windows-1252, ISO-8859-1).
   - In-memory decoding ensuring cleartext data is never written to temporary files during processing.
2. **Dynamic Delimiter Sniffing**:
   - Evaluates comma (`,`), semicolon (`;`), tab (`\t`), and pipe (`|`) without requiring manual user selection.
3. **Preamble / Header Boundary Detection**:
   - Real-world POS reports frequently output branding titles, shop addresses, or export dates in lines 1–3 before the actual column headers.
   - Sniffer scans the first 20 lines to pinpoint the first true header row by identifying the maximum non-empty, non-numeric column candidate.
4. **Malformed Trailing Text Recovery**:
   - When users input unescaped commas into trailing free-text columns (such as `notes`, `comments`, `remarks`), standard CSV engines fail with `ParserError`.
   - `CSVConnector` detects trailing text columns and dynamically repairs rows using Python engine callbacks without dropping records.
5. **Excel Multi-Sheet Navigation**:
   - `ExcelConnector` supports multi-sheet workbooks (`capabilities()["multi_sheet"] = True`).
   - Discovers all sheet names (`list_sheets()`), extracts default (first) or named sheets, and surfaces clear error messages when an unknown sheet is requested.
6. **Provenance Tracking**:
   - Automatically injects `source_connector` (`"csv"`, `"excel"`) and `source_row` (adjusted for header offsets) for downstream auditability.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_standard_csv_fetch_and_provenance` | Standard CSV ingestion | Column extraction + `source_connector="csv"`, `source_row` | **PASS** |
| `test_delimiter_sniffing_semicolon_and_tab` | Delimiter detection (`;` and `\t`) | Sniffs correct delimiter and parses columns | **PASS** |
| `test_header_detection_skips_metadata_preamble` | CSV with company title header rows | Skips preamble; correctly identifies line 4 as header | **PASS** |
| `test_unquoted_trailing_notes_repair` | Unquoted commas in trailing Notes column | Repairs row without raising parser error | **PASS** |
| `test_preview_limits_row_count` | `preview(n=5)` on 100-row file | Returns exactly 5 rows, reports accurate total | **PASS** |
| `test_excel_fetch_default_sheet` | Default sheet extraction on `.xlsx` | Returns first sheet records with row provenance | **PASS** |
| `test_excel_multi_sheet_discovery_and_selection` | Multi-sheet listing and extraction | Lists all sheets and loads secondary sheet | **PASS** |
| `test_excel_invalid_sheet_raises_informative_value_error` | Requesting invalid sheet name | Raises `ValueError` listing available sheets | **PASS** |
| `test_excel_preview` | `preview(n=1)` on Excel workbook | Returns single row preview | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.2 Data Connector Module/test_01_csv_excel_baseline_connector.py" -v`
- **Assurance**: Verified with synthetic CSV and OpenPyXL workbooks testing boundary conditions and encoding sniffing.
