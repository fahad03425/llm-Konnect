# Test Documentation Report: 05 — Dynamic Connector Routing & API Layer Integration

## 1. Test Suite Identification
- **Module ID**: Module 6.2 (Data Connector Module)
- **Test File**: [`test_05_connector_routing_and_api_integration.py`](./test_05_connector_routing_and_api_integration.py)
- **Target Implementation File**: [`backend/app/connectors/base.py`](../../backend/app/connectors/base.py) (Lines 36–92) and [`backend/app/api/routes.py`](../../backend/app/api/routes.py)
- **Primary Functions & Endpoints Tested**:
  - `detect_connector(path: str) -> Connector`
  - `source_exists(path_or_url: str) -> bool`
  - `is_network_or_custom_source(path_or_url: str) -> bool`
  - `GET /api/sheets`
  - `POST /api/preview`

---

## 2. Tested Business Requirements & Logic
1. **Dynamic Factory Routing**:
   - Central router `detect_connector` maps disparate inputs to the appropriate handler:
     - Extensions: `.csv`, `.tsv`, `.txt` → `CSVConnector`
     - Extensions: `.xlsx`, `.xls`, `.xlsm` → `ExcelConnector`
     - Extensions: `.xml` or schemes `tally://`, `tally+odbc://` → `TallyConnector`
     - Schemes: `shopify://` → `ShopifyConnector`
     - Extensions: `.sqlite`, `.db` → `LocalDBConnector`
2. **Unsupported Format Guardrails**:
   - Rejects unhandled file extensions (e.g., `.zip`, `.pdf`, `.bin`) with actionable error messages.
3. **Source Verification & Hybrid Existence Checks**:
   - `source_exists` differentiates between local filesystem paths (checking `os.path.exists`) and network URIs (`shopify://`, `tally://`), preventing spurious 404 errors when configuring live cloud or local daemon connectors.
4. **FastAPI Endpoints Integration**:
   - `GET /api/sheets`: Queries multi-sheet capabilities and returns list of sheet names for interactive UI selection.
   - `POST /api/preview`: Integrates connector preview sampling with schema signature computation, returning sample records, column arrays, and connector descriptors for UI verification before final ingestion.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_detect_connector_file_extensions` | Routing `.csv`, `.tsv`, `.xlsx`, `.xml`, `.db` | Correct connector class instances initialized | **PASS** |
| `test_detect_connector_url_schemes` | Routing `shopify://`, `tally://`, `tally+odbc://` | Dispatches to network connectors with parameters | **PASS** |
| `test_detect_connector_unsupported_extension_error` | Unsupported archive extension (`.zip`) | Raises `ValueError` ("Unsupported file extension") | **PASS** |
| `test_source_exists_and_network_source_validation` | Validating network URI vs local file existence | Returns `True` for network; checks disk for local | **PASS** |
| `test_api_sheets_discovery_endpoint` | `GET /api/sheets` endpoint | Returns JSON list of available Excel sheets | **PASS** |
| `test_api_preview_endpoint` | `POST /api/preview` endpoint | Returns preview rows, column list, and connector info | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.2 Data Connector Module/test_05_connector_routing_and_api_integration.py" -v`
- **Assurance**: Tested end-to-end against FastAPI route handlers and underlying connector factories.
