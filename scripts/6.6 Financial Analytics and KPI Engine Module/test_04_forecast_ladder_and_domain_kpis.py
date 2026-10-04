"""Test Suite 04: Forecast Ladder, Honest Refusal & Additive Domain KPIs.

Module: Module 6.6 — Financial Analytics and KPI Engine Module
Target Files: backend/app/analytics/forecast.py, backend/app/analytics/engine.py, backend/app/analytics/domains/pharmacy.py
Scope:
- Verifies descriptive trend analysis (revenue_trend).
- Verifies honest refusal of forecasting when history is too short (< 4 periods).
- Verifies Tier 1 moving-average forecast with upper and lower volatility bands.
- Verifies additive domain KPI registration (registering pharmacy expiry KPIs on KPIEngine).
- Verifies domain isolation (core engine specs remain free of domain nouns).
"""

import pandas as pd
import pytest
from app.analytics import forecast as forecast_kpis
from app.analytics.engine import KPIEngine
from app.analytics.filters import KPIFilters
from app.analytics.domains.pharmacy import register as register_pharmacy_kpis


class TestForecastLadderAndDomainKpis:
    """Verifies forecasting tiers, refusal constraints, and additive domain pack registration."""

    def test_trend_summary_calculation(self):
        """Computes past historical growth rate and trend direction over daily sales."""
        df_trend = pd.DataFrame({
            "date": [
                pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-02"),
                pd.Timestamp("2026-03-03"), pd.Timestamp("2026-03-04"),
                pd.Timestamp("2026-03-05"), pd.Timestamp("2026-03-06"),
            ],
            "amount": [100.0, 120.0, 150.0, 180.0, 200.0, 250.0],
            "txn_type": ["sale"] * 6,
            "source_row": list(range(1, 7)),
            "source_connector": ["CSVConnector"] * 6
        })

        res = forecast_kpis.revenue_trend(df_trend, KPIFilters(options={"granularity": "daily"}), domain="pharmacy")

        assert res.status == "ok"
        assert res.unit == "percent"
        # Positive revenue growth over the 6 daily windows
        assert res.value is not None
        assert res.series is not None

    def test_forecast_ladder_refuses_when_history_too_short(self):
        """Refuses to produce a forecast when dataset has insufficient time history (< 4 periods)."""
        df_short = pd.DataFrame({
            "date": [pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-02")],
            "amount": [100.0, 150.0],
            "txn_type": ["sale", "sale"],
            "source_row": [1, 2],
            "source_connector": ["CSVConnector", "CSVConnector"]
        })

        res = forecast_kpis.revenue_forecast(df_short, KPIFilters(options={"granularity": "daily"}), domain="pharmacy")

        # Crucial principle: Honest refusal over false certainty
        assert res.status == "unavailable"
        assert res.value is None
        assert "not enough history" in res.reason.lower() or "period" in res.reason.lower()

    def test_forecast_ladder_tier1_moving_average_with_bands(self):
        """Produces Tier 1 baseline forecast with lower and upper volatility uncertainty bounds."""
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        df_history = pd.DataFrame({
            "date": dates,
            "amount": [100.0, 110.0, 105.0, 115.0, 120.0, 118.0, 125.0, 130.0, 128.0, 135.0],
            "txn_type": ["sale"] * 10,
            "source_row": list(range(1, 11)),
            "source_connector": ["CSVConnector"] * 10
        })

        res = forecast_kpis.revenue_forecast(
            df_history,
            KPIFilters(options={"granularity": "daily", "horizon": 3}),
            domain="pharmacy"
        )

        assert res.status == "ok"
        assert res.value is not None
        assert res.forecast is not None
        # Must produce forecasted periods
        items = res.forecast
        assert len(items) == 3

        # Validate uncertainty bounds: lower <= estimate <= upper
        for period in items:
            estimate = period["value"]
            lower = period["lower"]
            upper = period["upper"]
            assert lower <= estimate <= upper

    def test_additive_domain_pack_kpi_registration(self, clean_engine):
        """Attaches domain-specific KPIs onto clean engine without mutating core agnostic registry."""
        core_keys = [s.key for s in clean_engine.list_kpis(domain=None)]
        assert "near_expiry_total" not in core_keys
        assert "total_revenue" in core_keys

        # Register pharmacy domain pack
        register_pharmacy_kpis(clean_engine, domain="pharmacy")

        pharmacy_keys = [s.key for s in clean_engine.list_kpis(domain="pharmacy")]
        assert "near_expiry_total" in pharmacy_keys
        assert "expired_stock_value" in pharmacy_keys
        assert "total_revenue" in pharmacy_keys

    def test_pharmacy_expiry_kpis_computation(self, clean_engine):
        """Computes domain-specific near-expiry and expired stock valuations with provenance."""
        register_pharmacy_kpis(clean_engine, domain="pharmacy")

        # Ref date pinned to 2026-03-01
        inv_df = pd.DataFrame({
            "product_id": ["Panadol (Expired)", "Brufen (Near Expiry)", "Amoxil (Fresh)"],
            "expiry_date": [
                pd.Timestamp("2026-01-15"),  # Expired (-45 days)
                pd.Timestamp("2026-03-20"),  # Near expiry (+19 days, in 30d bucket)
                pd.Timestamp("2027-06-01"),  # Fresh (> 90 days)
            ],
            "quantity": [10.0, 20.0, 50.0],
            "cost": [30.0, 50.0, 80.0],
            "source_row": [101, 102, 103],
            "source_connector": ["CSVConnector"] * 3
        })

        filters = KPIFilters(as_of="2026-03-01")

        # Expired stock value
        res_expired = clean_engine.compute("expired_stock_value", inv_df, filters=filters, domain="pharmacy")
        assert res_expired.status == "ok"
        # 10 units * 30.0 cost = 300.0
        assert res_expired.value == 300.0
        assert 101 in res_expired.provenance.source_rows

        # Near-expiry total (within 90 days, not expired)
        res_near = clean_engine.compute("near_expiry_total", inv_df, filters=filters, domain="pharmacy")
        assert res_near.status == "ok"
        # 20 units * 50.0 cost = 1000.0
        assert res_near.value == 1000.0
        assert 102 in res_near.provenance.source_rows
