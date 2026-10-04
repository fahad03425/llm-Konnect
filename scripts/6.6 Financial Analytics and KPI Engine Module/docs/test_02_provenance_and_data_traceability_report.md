# Test Suite Report 02: Provenance Tracking & Data Traceability

## Overview
- **Module**: Module 6.6 — Financial Analytics and KPI Engine Module
- **Target Files**: `backend/app/analytics/kpi.py` & `backend/app/analytics/models.py`
- **Components**: Audit Provenance Engine (`Provenance`), Honest Refusal Engine (`unavailable`)

## Scope & Architectural Verification
This test suite verifies auditability and data traceability:
1. **Source Row Attribution**: Verifies that every calculation records the exact line numbers (`source_rows`) from the user's raw file that contributed to the figure (e.g. Rows `[2, 3, 4, 5, 9]` for sales).
2. **Column & Source Tracking**: Ensures `columns_used` and `sources` accurately list the data inputs accessed during vectorized evaluation.
3. **Explicit Assumption Logging**: Confirms that when optional fields like `txn_type` are omitted, the engine logs the assumption ("all rows treated as sales") in `provenance.assumptions` rather than hiding it.
4. **Honest Refusal (`unavailable`)**: Enforces the design rule that an uncomputable metric (e.g. missing `amount` column, or computing `gross_profit` without `cost` data) returns `status = "unavailable"` with an explicit `reason` rather than returning a deceptive zero.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_revenue_provenance_tracks_exact_source_rows` | Exact Row Attribution | Correctly links sales to source rows `[2, 3, 4, 5, 9]`. |
| `test_expense_and_refund_provenance_isolation` | Cash Flow Row Isolation | Expenses isolated to `[6, 7]`; refunds isolated to `[8]`. |
| `test_assumptions_recorded_when_txn_type_is_absent` | Assumption Transparency | Logs audit note when assuming rows are sales. |
| `test_missing_required_column_returns_unavailable_with_reason` | Missing Data Rejection | Returns `unavailable` when `amount` is missing. |
| `test_gross_profit_unavailable_when_cost_is_missing` | COGS Data Integrity | Refuses gross profit calculation when `cost` is absent. |
