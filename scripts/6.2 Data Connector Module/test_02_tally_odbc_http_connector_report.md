# Test Documentation Report: 02 — Tally Prime Direct Local Extraction (XML, HTTP & ODBC)

## 1. Test Suite Identification
- **Module ID**: Module 6.2 (Data Connector Module)
- **Test File**: [`test_02_tally_odbc_http_connector.py`](./test_02_tally_odbc_http_connector.py)
- **Target Implementation File**: [`backend/app/connectors/tally.py`](../../backend/app/connectors/tally.py)
- **Primary Classes & Functions Tested**:
  - `TallyConnector`
  - `parse_tally_xml()`
  - `_parse_tally_number()`
  - `_parse_tally_date()`

---

## 2. Tested Business Requirements & Logic
1. **Direct TallyPrime Extraction**:
   - Eliminates tedious manual re-keying or manual multi-step exports by establishing direct local extraction from running TallyPrime or Tally.ERP 9 instances.
2. **Dual-Mode Connectivity**:
   - **Mode A (Live HTTP/XML)**: Connects to local Tally port (`http://localhost:9000` or `tally://localhost:9000`), dispatching a standard TDL XML `DayBook` export request.
   - **Mode B (Tally ODBC)**: Queries read-only collections using Tally's local DSN driver.
   - **Mode C (File Export)**: Imports standalone `.xml` export envelopes from disk.
3. **Inventory & Pharmacy Batch Awareness**:
   - Traverses `<ALLINVENTORYENTRIES.LIST>` to parse item name, billed quantity, unit rate, and calculated amounts.
   - Extracts nested `<BATCHALLOCATIONS.LIST>` for batch numbers (`BATCHNAME`) and expiration dates (`EXPIRYPERIOD`), critical for pharmaceutical FEFO (First-Expired, First-Out) inventory compliance.
4. **Resilient Data Normalization**:
   - Strips unit suffixes (`"100 Box"`, `"18.50/Box"`) and currency symbols.
   - Converts Tally 8-digit date integers (`20260115`) into ISO `YYYY-MM-DD`.
5. **Operational Diagnostics**:
   - Detects Tally `<LINEERROR>` tags and raises clear user exceptions when a company file is not open in Tally.
   - Provides clear offline instructions (`ConnectionError`) informing the operator how to enable ODBC/HTTP in Tally settings.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_parse_sales_voucher_with_inventory_and_batches` | Parses inventory sales voucher | Extracts items, rates, batches, expiries, amounts | **PASS** |
| `test_parse_accounting_payment_voucher` | Parses non-inventory ledger payment | Extracts ledger expense lines and balancing amounts | **PASS** |
| `test_parse_tally_number_variations` | Sanitizes units, commas, negatives | Returns clean floating point values | **PASS** |
| `test_parse_tally_date_formats` | 8-digit integers & string dates | Returns ISO `YYYY-MM-DD` strings | **PASS** |
| `test_parse_tally_error_envelope` | Tally XML containing `LINEERROR` | Raises descriptive `ValueError` with Tally reason | **PASS** |
| `test_live_http_request_construction_and_fetch` | TDL envelope over HTTP POST | Dispatches valid TDL XML envelope to port 9000 | **PASS** |
| `test_live_http_connection_offline_error` | Tally daemon is offline | Raises `ConnectionError` with port 9000 guidance | **PASS** |
| `test_tally_file_based_extraction` | XML file on disk | Reads and parses file cleanly | **PASS** |
| `test_tally_odbc_query_validation` | Non-SELECT or multiple queries | Rejects invalid queries with `ValueError` | **PASS** |
| `test_tally_capabilities` | Capabilities inspection | Reports `live_http=True`, `supports_batch_and_expiry=True` | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.2 Data Connector Module/test_02_tally_odbc_http_connector.py" -v`
- **Assurance**: Mocked network requests isolate local TCP ports while validating actual TDL XML export payload schemas.
