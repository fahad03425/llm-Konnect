# Test Suite Report 04: Forecast Ladder, Honest Refusal & Additive Domain KPIs

## Overview
- **Module**: Module 6.6 — Financial Analytics and KPI Engine Module
- **Target Files**: `backend/app/analytics/forecast.py`, `backend/app/analytics/engine.py`, `backend/app/analytics/domains/pharmacy.py`
- **Components**: 3-Tier Forecast Ladder, Volatility Bands, Additive Domain Registration

## Scope & Architectural Verification
This test suite verifies forecasting honesty and domain pack extensibility:
1. **Descriptive Trend Analysis**: Verifies `trend_summary` historical trajectory and growth rate evaluation.
2. **Honest Refusal Constraints**: Enforces the design principle that small business forecasts must never be hallucinated on thin data. When history spans fewer than 4 periods, `forecast_revenue` explicitly refuses and returns `status = "unavailable"` with the exact deficiency stated in `reason`.
3. **Tier 1 Baseline Projections**: When sufficient data exists (6+ periods), computes moving-average projections with dynamic upper and lower volatility bounds ($lower \le projected \le upper$).
4. **Additive Domain Pack Architecture**: Verifies that calling `register(clean_engine, domain="pharmacy")` attaches domain-specific metrics without mutating or polluting the core domain-agnostic KPI registry.
5. **Pharmacy Expiry Analytics**: Verifies banded expiry calculations (`expired_stock_value`, `near_expiry_total`) with reference date injection (`as_of`) and exact batch provenance.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_trend_summary_calculation` | Historical Trajectory | Evaluates growth rate over historical daily windows. |
| `test_forecast_ladder_refuses_when_history_too_short` | Honest Refusal | Refuses forecast with < 4 periods; returns `unavailable`. |
| `test_forecast_ladder_tier1_moving_average_with_bands` | Tier 1 Projections | Generates 3-period forecast with lower/upper volatility bands. |
| `test_additive_domain_pack_kpi_registration` | Additive Extensibility | Registers pharmacy metrics while keeping core registry clean. |
| `test_pharmacy_expiry_kpis_computation` | Expiry Valuations | Accurately values expired (PKR 300) and near-expiry (PKR 1000). |
