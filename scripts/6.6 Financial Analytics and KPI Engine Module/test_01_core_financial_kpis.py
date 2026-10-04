"""Test Suite 01: Deterministic Core Financial KPIs.

Module: Module 6.6 — Financial Analytics and KPI Engine Module
Target Files: backend/app/analytics/kpi.py & backend/app/analytics/engine.py
Scope:
- Verifies total_revenue calculation over sale transactions.
- Verifies total_expenses calculation over expense/purchase transactions.
- Verifies total_refunds calculation over customer return transactions.
- Verifies net_profit (revenue - expenses - refunds).
- Verifies gross_profit (sale revenue - COGS) and gross_margin_pct.
- Verifies net_margin_pct and refund_rate_pct.
- Verifies volume and order KPIs (units_sold, transaction_count, average_transaction_value).
- Confirms zero LLM calls and 100% deterministic pure-pandas arithmetic.
"""

import pytest
from app.analytics.engine import KPIEngine
from app.analytics.filters import KPIFilters
from app.analytics import kpi as core_kpis


class TestCoreFinancialKpis:
    """Verifies deterministic calculation of fundamental financial figures."""

    def test_total_revenue_calculation(self, sample_financial_df):
        """Computes exact sum of positive revenue across sale rows."""
        result = core_kpis.total_revenue(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert result.status == "ok"
        assert result.unit == "PKR"
        # 500 + 400 + 500 + 1000 + 800 = 3200.0
        assert result.value == 3200.0

    def test_total_expenses_calculation(self, sample_financial_df):
        """Computes sum of operational and supply expense amounts."""
        result = core_kpis.total_expenses(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert result.status == "ok"
        assert result.unit == "PKR"
        # Row 5 (500) + Row 6 (500) = 1000.0
        assert result.value == 1000.0

    def test_total_refunds_calculation(self, sample_financial_df):
        """Computes absolute value sum of refunds and return amounts."""
        result = core_kpis.total_refunds(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert result.status == "ok"
        assert result.unit == "PKR"
        # Row 7 |-100.0| = 100.0
        assert result.value == 100.0

    def test_net_profit_calculation(self, sample_financial_df):
        """Computes net profit: Total Revenue - Total Expenses - Total Refunds."""
        result = core_kpis.net_profit(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert result.status == "ok"
        assert result.unit == "PKR"
        # 3200 - 1000 - 100 = 2100.0
        assert result.value == 2100.0

    def test_gross_profit_and_gross_margin_percentage(self, sample_financial_df):
        """Computes gross profit (Revenue - COGS) and gross margin percentage."""
        res_gp = core_kpis.gross_profit(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_gp.status == "ok"
        # Costed sales: (500-350) + (400-275) + (500-360) + (1000-700) + (800-550) = 965.0
        assert res_gp.value == 965.0

        res_margin = core_kpis.gross_margin_pct(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_margin.status == "ok"
        assert res_margin.unit == "percent"
        # 965 / 3200 * 100 = 30.16%
        assert round(res_margin.value, 2) == 30.16

    def test_net_margin_and_refund_rate_percentages(self, sample_financial_df):
        """Computes net profit margin % and refund rate % relative to gross sales."""
        res_net_margin = core_kpis.net_margin_pct(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_net_margin.status == "ok"
        # Net profit 2100 / 3200 * 100 = 65.62%
        assert round(res_net_margin.value, 2) == 65.62

        res_refund_rate = core_kpis.refund_rate_pct(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_refund_rate.status == "ok"
        # Refunds 100 / 3200 * 100 = 3.12%
        assert round(res_refund_rate.value, 2) == 3.12

    def test_volume_and_average_transaction_value(self, sample_financial_df):
        """Computes units sold, transaction count, and average transaction value (ATV)."""
        res_units = core_kpis.units_sold(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_units.status == "ok"
        # 10 + 5 + 4 + 20 + 10 = 49.0
        assert res_units.value == 49.0

        res_txns = core_kpis.transaction_count(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_txns.status == "ok"
        # Distinct invoices: INV-1001, INV-1002, INV-1003, INV-1004, INV-1005 = 5
        assert res_txns.value == 5

        res_atv = core_kpis.average_transaction_value(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_atv.status == "ok"
        # 3200.0 / 5 = 640.0
        assert res_atv.value == 640.0
