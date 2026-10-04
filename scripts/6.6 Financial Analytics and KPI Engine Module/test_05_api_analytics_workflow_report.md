# Test Report 05: FastAPI Analytics REST Endpoints & Workflow

## Executive Summary
This report documents the verification of the transport layer (`backend/app/api/analytics.py`) within Module 6.6. The analytics API acts as a thin transport boundary connecting file and database sources through connector and schema normalization pipelines into the pure-Python/Pandas `KPIEngine`.

Crucially, **no Large Language Model (LLM)** is present anywhere along this pipeline. All numbers, breakdowns, forecasts, and confidence intervals are computed deterministically and returned alongside comprehensive audit provenance.

---

## Test Cases & Specifications

| Test Case | Method & Endpoint | Validated Behavior | Result |
| :--- | :--- | :--- | :--- |
| `test_list_kpis_endpoint` | `GET /api/analytics/kpis` | Verifies discovery of all registered core and domain-specific KPI specifications (`total_revenue`, `net_profit`, `refund_rate_pct`, etc.). | **PASS** |
| `test_compute_single_kpi_endpoint` | `POST /api/analytics/kpi/total_revenue` | Computes a single metric on canonical CSV data; verifies exact mathematical output (`300.0`), validation telemetry, and row-level provenance (`source_rows: [0, 1]`). | **PASS** |
| `test_compute_full_kpi_pack_endpoint` | `POST /api/analytics/kpis` | Executes an all-in-one deterministic evaluation across revenue, expenses, refunds, net profit, and units sold. | **PASS** |
| `test_unknown_kpi_returns_404` | `POST /api/analytics/kpi/{unknown}` | Confirms strict API contracts: requesting undefined KPI keys immediately raises an HTTP 404 with helpful error messages. | **PASS** |
| `test_missing_file_returns_404` | `POST /api/analytics/kpi/{key}` | Confirms system resilience: invalid or non-existent file paths return a clean HTTP 404 instead of unhandled internal server exceptions. | **PASS** |
| `test_trend_endpoint` | `POST /api/analytics/trend` | Computes historical aggregation time-series across monthly buckets without predictive hallucinations. | **PASS** |
| `test_forecast_endpoint` | `POST /api/analytics/forecast` | Computes statistical projections with lower and upper confidence volatility bands (`lower <= forecast <= upper`). | **PASS** |

---

## Key Architectural Invariants Verified
1. **Thin Transport Decoupling**:
   - The FastAPI layer handles deserialization, mapping resolution, and caching.
   - All arithmetic logic executes strictly in `app.analytics.engine`, `app.analytics.kpi`, and `app.analytics.forecast`.
2. **Safe Sanitization**:
   - All output values are sanitized against NaN and Infinity before JSON serialization, preventing deserialization errors in frontend clients.
3. **Data Quality Envelope**:
   - Every KPI response optionally envelopes a Schema Validation report (`validation_report`), ensuring executives can assess the data quality underpinning the metrics.
4. **Strict HTTP Status Codes**:
   - 404 for unknown metrics or missing files.
   - 400 for malformed payload structures or schema incompatibilities.
