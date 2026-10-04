# Test Report 05: LLM Plain-Language Explainer & REST API Workflow

## Executive Summary
This report validates the plain-language explanation layer (`explainer.py`), the FastAPI transport layer (`/api/anomaly/*`), and the integration into `KPIEngine`.

### Inviolable Architectural Separation
The primary design guarantee of Module 6.7 is:
1. **Mathematical Detection**: Executed **exclusively in Python code** via vectorized Z-Score, IQR, hash collision, and unit economics algorithms. The LLM never computes standard deviations, quartiles, or thresholds.
2. **Plain-Language Narration**: The LLM's **only** role is to summarize an already-flagged item into business English for the store owner.
3. **Deterministic Fallback**: If the local LLM is busy or offline, the system instantly employs rule-based template generation (`generate_template_explanation`) with zero latency and 100% reliability.

---

## Test Cases & Specifications

| Test Case | Component Tested | Validated Invariants | Status |
| :--- | :--- | :--- | :---: |
| `test_template_explanation_generation` | Deterministic Explainer | Generates crisp explanations for duplicate invoices citing matching row numbers `[10, 11]`. | **PASS** |
| `test_spike_template_explanation` | Outlier Explainer | Details standard deviation distance and contextual verification guidance. | **PASS** |
| `test_explain_all_attaches_to_records` | Batch Annotation | Populates `.explanation` on anomaly records in-place without altering statistical figures. | **PASS** |
| `test_api_scan_endpoint` | `POST /api/anomaly/scan` | Runs full anomaly pipeline over HTTP; returns counts by type and severity. | **PASS** |
| `test_api_explain_endpoint` | `POST /api/anomaly/explain` | Generates a single plain-language explanation for an already-flagged record via REST. | **PASS** |
| `test_kpi_engine_anomaly_integration` | `KPIEngine` Specs | Evaluates `anomaly_count` and `anomaly_breakdown` KPIs deterministically. | **PASS** |

---

## Explanation Payload Contract
Every explanation strictly inherits the provenance of the underlying record:
- **Observed value**: Exact figure from the data (e.g. `2,500.00 PKR`).
- **Mathematical justification**: Distance from normal ($Z = 5.2\sigma$, IQR upper bound).
- **Physical source coordinates**: Exact file name and row index to permit immediate ledger cross-checking.
