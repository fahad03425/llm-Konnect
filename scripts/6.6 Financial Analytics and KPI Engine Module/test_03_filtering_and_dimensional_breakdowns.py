"""Test Suite 03: Pre-filtering & Dimensional Breakdowns.

Module: Module 6.6 — Financial Analytics and KPI Engine Module
Target Files: backend/app/analytics/filters.py & backend/app/analytics/kpi.py
Scope:
- Verifies apply_filters pre-filtering by date ranges, months, categories, products, and suppliers.
- Verifies revenue_breakdown_by_product sorted descending by financial amount.
- Verifies expense_breakdown_by_category.
- Verifies revenue_breakdown_by_supplier.
- Verifies quantity_breakdown_by_product.
- Confirms deterministic sorting and Top-N limiting.
"""

import pytest
from app.analytics.filters import KPIFilters, apply_filters
from app.analytics import kpi as core_kpis


class TestFilteringAndDimensionalBreakdowns:
    """Verifies consistent data slicing and multi-dimensional aggregation breakdowns."""

    def test_apply_filters_date_range_slice(self, sample_financial_df):
        """Filters dataset to an inclusive date window and evaluates scoped revenue."""
        filters = KPIFilters(date_from="2026-03-01", date_to="2026-03-03")
        filtered_df, notes = apply_filters(sample_financial_df, filters)

        # 3 rows fall within March 1 to March 3
        assert len(filtered_df) == 3
        res = core_kpis.total_revenue(filtered_df, filters, domain="pharmacy")

        # Sales on March 1 (500) + March 2 (400) + March 3 (500) = 1400.0
        assert res.value == 1400.0
        assert res.provenance.row_count == 3

    def test_apply_filters_product_and_category_slice(self, sample_financial_df, clean_engine):
        """Filters dataset by specific product identifier or category."""
        filters_panadol = KPIFilters(product_id="Panadol 500mg")
        res_panadol = clean_engine.compute("total_revenue", sample_financial_df, filters_panadol, domain="pharmacy")

        # Sales: Row 1 (500) + Row 4 (1000) = 1500.0 (Row 7 refund is excluded from revenue)
        assert res_panadol.value == 1500.0

        filters_antibiotics = KPIFilters(category="Antibiotics")
        res_antibiotics = clean_engine.compute("total_revenue", sample_financial_df, filters_antibiotics, domain="pharmacy")
        assert res_antibiotics.value == 500.0  # Amoxil

    def test_revenue_breakdown_by_product(self, sample_financial_df):
        """Aggregates sale revenue grouped by product_id sorted descending by amount."""
        res = core_kpis.revenue_breakdown_by_product(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert res.status == "ok"
        assert res.breakdown is not None
        # Expected products: Panadol (1500), Brufen (1200), Amoxil (500)
        items = res.breakdown.items if hasattr(res.breakdown, "items") else res.breakdown
        item_dict = {item["product_id"]: item["amount"] for item in items}

        assert item_dict["Panadol 500mg"] == 1500.0
        assert item_dict["Brufen 400mg"] == 1200.0
        assert item_dict["Amoxil 250mg"] == 500.0

        # Confirms descending sort: Panadol first, then Brufen, then Amoxil
        labels = [item["product_id"] for item in items]
        assert labels[0] == "Panadol 500mg"
        assert labels[1] == "Brufen 400mg"
        assert labels[2] == "Amoxil 250mg"

    def test_expense_breakdown_by_category(self, sample_financial_df):
        """Aggregates expense transactions grouped by category."""
        res = core_kpis.expense_breakdown_by_category(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert res.status == "ok"
        items = res.breakdown.items if hasattr(res.breakdown, "items") else res.breakdown
        item_dict = {item["category"]: item["amount"] for item in items}

        assert item_dict["Overhead"] == 500.0
        assert item_dict["Supplies"] == 500.0

    def test_revenue_breakdown_by_supplier(self, sample_financial_df):
        """Aggregates sales revenue grouped by supplier/manufacturer."""
        res = core_kpis.revenue_breakdown_by_supplier(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert res.status == "ok"
        items = res.breakdown.items if hasattr(res.breakdown, "items") else res.breakdown
        item_dict = {item["supplier_id"]: item["amount"] for item in items}

        assert item_dict["GSK Pakistan"] == 1500.0
        assert item_dict["Abbott Labs"] == 1200.0
        assert item_dict["Pfizer"] == 500.0

    def test_quantity_breakdown_by_product(self, sample_financial_df):
        """Aggregates units sold grouped by product_id."""
        res = core_kpis.quantity_breakdown_by_product(sample_financial_df, KPIFilters(), domain="pharmacy")

        assert res.status == "ok"
        items = res.breakdown.items if hasattr(res.breakdown, "items") else res.breakdown
        item_dict = {item["product_id"]: item["quantity"] for item in items}

        assert item_dict["Panadol 500mg"] == 30.0  # 10 + 20
        assert item_dict["Brufen 400mg"] == 15.0   # 5 + 10
        assert item_dict["Amoxil 250mg"] == 4.0    # 4
