# Module 6.6: Financial Analytics and KPI Engine Module — Architecture & File Manifest

## Executive Summary
The **Financial Analytics and KPI Engine Module (Module 6.6)** is the single source of numeric truth across the LLM-Konnect platform. It computes financial metrics—such as total revenue, expenses, net profit, gross profit, gross margins, refund rates, and dimensional breakdowns—directly in pure Python and pandas without ever invoking an LLM.

### Fundamental Engineering Principles
1. **100% LLM-Free Arithmetic**: The language model is never involved in computing mathematical figures. The KPI engine executes pure vectorized pandas code; the LLM is only permitted to narrate the deterministic output produced here.
2. **Complete Provenance & Traceability**: Every single `KPIResult` contains a `Provenance` record that tracks the exact filters applied, the count of contributing records, and the physical row indices (`source_rows`) in the user's raw file.
3. **No Masquerading Zeros (Honest Refusal)**: If a metric cannot be computed (e.g. calculating gross margin when cost data is missing, or forecasting with insufficient time history), the engine returns `status = "unavailable"` with an explicit human-readable `reason` rather than returning a misleading zero.
4. **Additive Domain Extensions**: New domain KPIs (e.g. pharmacy inventory expiry risk) register additively with `KPIEngine.register(KPISpec(...))` without requiring modification to core financial logic.
5. **Deterministic Sorting & Idempotency**: All multi-dimensional breakdowns and aggregated time series are sorted explicitly, ensuring byte-identical results on repeated runs.

---

## Module Files & Architectural Mapping

| File Path | Architectural Role | Key Functions / Classes | Responsibilities |
| :--- | :--- | :--- | :--- |
| [`backend/app/analytics/engine.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/engine.py) | **Registry & Orchestrator** | `KPIEngine`, `KPISpec`, `_CORE_SPECS`, `engine` (singleton) | Manages KPI specifications, registers domain packs, orchestrates batch and individual computations (`compute`, `compute_all`), and exposes available metrics. |
| [`backend/app/analytics/kpi.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/kpi.py) | **Core Financial Formulas** | `total_revenue`, `total_expenses`, `total_refunds`, `net_profit`, `gross_profit`, `gross_margin_pct`, `net_margin_pct`, `refund_rate_pct`, `units_sold`, `transaction_count`, `average_transaction_value`, breakdowns | Implements domain-agnostic financial math, transaction classification (`classify_transactions`), and provenance generation. |
| [`backend/app/analytics/models.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/models.py) | **Typed Result Models** | `KPIResult`, `Provenance`, `Period`, `unavailable`, `STATUS_OK`, `STATUS_UNAVAILABLE` | Typed, immutable, JSON-serializable dataclasses encapsulating values, units, formulas, periods, and audit provenance. |
| [`backend/app/analytics/filters.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/filters.py) | **Data Slicing & Pre-filtering** | `KPIFilters`, `apply_filters`, `_ROW_FILTER_FIELDS` | Applies consistent, deterministic slicing (date ranges, months, years, categories, products, suppliers, customers) before KPI computation. |
| [`backend/app/analytics/forecast.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/forecast.py) | **Trend & Forecasting Ladder** | `trend_summary`, `forecast_revenue`, `forecast_units`, `ForecastOutcome` | Implements a 3-tier forecasting ladder (Tier 0 Descriptive Trend, Tier 1 Moving Average, Tier 2 Exponential Smoothing) with honest refusal constraints and volatility bands. |
| [`backend/app/analytics/timeseries.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/timeseries.py) | **Time-Series Utilities** | `build_series`, `growth_rate`, `moving_average`, `window_comparison` | Aggregates daily/weekly/monthly periods, fills timeline gaps with explicit zeros, and calculates window comparisons. |
| [`backend/app/analytics/cache.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/cache.py) | **Performance Caching** | `AnalyticsCache`, `get_cached_table`, `set_cached_table`, `clear_analytics_cache` | Thread-safe in-memory LRU cache for normalized canonical DataFrames to accelerate repetitive analytical queries. |
| [`backend/app/analytics/domains/`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/domains/) | **Domain Packs** | `pharmacy.py`, `ecommerce.py`, `pharmacy_pos.py`, `pharmacy_purchases.py` | Additive domain packs registering specialized metrics (e.g. DRAP compliance, expiry buckets, inventory risk). |
| [`backend/app/api/analytics.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/api/analytics.py) | **REST API Router** | `/api/analytics/kpi`, `/api/analytics/kpis`, `/api/analytics/specs` | Public HTTP transport connecting raw files through connector/mapper pipelines to the `KPIEngine`. |

---

## Detailed Financial Math & Formulas

### 1. Revenue, Expense & Profit
- **Total Revenue**:
  $$\text{Total Revenue} = \sum_{\text{sale rows}} \text{amount}$$
- **Total Expenses**:
  $$\text{Total Expenses} = \sum_{\text{expense rows}} \text{amount}$$
- **Total Refunds**:
  $$\text{Total Refunds} = \sum_{\text{refund rows}} |\text{amount}|$$
- **Net Profit**:
  $$\text{Net Profit} = \text{Total Revenue} - \text{Total Expenses} - \text{Total Refunds}$$
- **Gross Profit**:
  $$\text{Gross Profit} = \sum_{\text{costed sale rows}} (\text{amount} - (\text{cost} \times \text{quantity}))$$

### 2. Financial Margins & Rates
- **Gross Margin %**:
  $$\text{Gross Margin \%} = \frac{\text{Gross Profit}}{\text{Revenue of costed sale rows}} \times 100$$
- **Net Margin %**:
  $$\text{Net Margin \%} = \frac{\text{Net Profit}}{\text{Total Revenue}} \times 100$$
- **Refund Rate %**:
  $$\text{Refund Rate \%} = \frac{\text{Total Refunds}}{\text{Total Revenue}} \times 100$$
- **Average Transaction Value (ATV)**:
  $$\text{ATV} = \frac{\text{Total Revenue}}{\text{Transaction Count}}$$

### 3. Provenance Data Structure
```python
Provenance(
    filters={"month": 3, "year": 2026},
    filter_description="month = 3, year = 2026",
    row_count=5,
    source_rows=[2, 3, 4, 5, 9],
    source_rows_truncated=False,
    sources=["C:/data/financial_ledger_2026.csv"],
    columns_used=["amount", "txn_type", "cost", "quantity"],
    assumptions=["cost derived from catalog cost column"]
)
```

---

## Test Verification Scope

The test suite in this folder validates:
1. **Core Financial Formulas**: Revenue, expenses, refunds, net profit, gross profit, gross margin %, net margin %, refund rate %, transaction count, and average transaction value.
2. **Provenance & Traceability**: Physical source row extraction, column audit tracking, assumption logging, and honest refusal (`unavailable`) behavior.
3. **Filtering & Dimensional Slicing**: Pre-filtering by dates, calendar components, product IDs, and Top-N sorting for revenue/expense breakdowns.
4. **Forecasting Ladder & Domain Extensions**: Trend analysis, moving-average projections, uncertainty bands, refusal thresholds, and additive domain pack registration.
5. **FastAPI Endpoints**: HTTP integration for `/api/analytics/specs`, `/api/analytics/kpi`, and `/api/analytics/kpis`.
