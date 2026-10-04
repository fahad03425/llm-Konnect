"""Test Suite 02: Provenance Tracking & Data Traceability.

Module: Module 6.6 — Financial Analytics and KPI Engine Module
Target Files: backend/app/analytics/kpi.py & backend/app/analytics/models.py
Scope:
- Verifies that every KPIResult carries an auditable Provenance object.
- Verifies exact physical source_rows tracking for contributing dataset records.
- Verifies tracking of columns_used, sources, and active filter descriptions.
- Verifies explicit assumption disclosure (e.g., absent txn_type treated as sales).
- Verifies honest refusal (status='unavailable' with reason) instead of fake zero when data is missing.
"""

import pandas as pd
import pytest
from app.analytics.filters import KPIFilters
from app.analytics import kpi as core_kpis


class TestProvenanceAndDataTraceability:
    """Verifies audit provenance, source row tracking, and honest refusal behavior."""

    def test_revenue_provenance_tracks_exact_source_rows(self, sample_financial_df):
        """Total revenue provenance captures exact contributing source row indices."""
        res = core_kpis.total_revenue(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert res.provenance is not None
        prov = res.provenance
        # Sale rows in sample_financial_df are at source_row: 2, 3, 4, 5, 9
        assert prov.row_count == 5
        assert prov.source_rows == [2, 3, 4, 5, 9]
        assert "amount" in prov.columns_used
        assert "C:/data/financial_ledger_2026.csv" in prov.sources

    def test_expense_and_refund_provenance_isolation(self, sample_financial_df):
        """Expense and refund provenance strictly isolates their respective contributing rows."""
        res_exp = core_kpis.total_expenses(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_exp.provenance.row_count == 2
        assert res_exp.provenance.source_rows == [6, 7]

        res_ref = core_kpis.total_refunds(sample_financial_df, KPIFilters(), domain="pharmacy")
        assert res_ref.provenance.row_count == 1
        assert res_ref.provenance.source_rows == [8]

    def test_assumptions_recorded_when_txn_type_is_absent(self):
        """Documents explicit assumption when canonical txn_type column is missing."""
        df_no_txn_type = pd.DataFrame({
            "amount": [150.0, 250.0],
            "source_row": [10, 11],
            "source_connector": ["CSVConnector", "CSVConnector"]
        })

        res = core_kpis.total_revenue(df_no_txn_type, KPIFilters(), domain="pharmacy")

        assert res.status == "ok"
        assert res.value == 400.0
        # Check that the assumption is explicitly auditable in provenance
        assert any("txn_type" in a.lower() for a in res.provenance.assumptions)

    def test_missing_required_column_returns_unavailable_with_reason(self):
        """Returns status='unavailable' with reason rather than a misleading zero when column is absent."""
        df_missing_amount = pd.DataFrame({
            "product_id": ["Panadol", "Brufen"],
            "source_row": [1, 2]
        })

        res = core_kpis.total_revenue(df_missing_amount, KPIFilters(), domain="pharmacy")

        # Crucial principle: Never masquerade missing data as 0.0
        assert res.status == "unavailable"
        assert res.value is None
        assert "amount" in res.reason.lower()

    def test_gross_profit_unavailable_when_cost_is_missing(self):
        """Gross profit returns unavailable when neither cost nor unit_cost is present in dataset."""
        df_no_cost = pd.DataFrame({
            "amount": [500.0],
            "quantity": [10.0],
            "txn_type": ["sale"],
            "source_row": [2]
        })

        res = core_kpis.gross_profit(df_no_cost, KPIFilters(), domain="pharmacy")

        assert res.status == "unavailable"
        assert res.value is None
        assert "cost" in res.reason.lower()
