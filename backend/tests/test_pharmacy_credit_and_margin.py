"""
test_pharmacy_credit_and_margin.py — Test suite for supplier credit, dead stock, category margin, and payment mix.

Covers:
  1. Supplier payable totals and grouping with earliest due date.
  2. Dead stock value and count detection (inventory unsold for >= 60 days).
  3. Gross margin by category with hand-computed arithmetic checks.
  4. Payment method mix (revenue share by payment method).
  5. Graceful 'unavailable' degradation when optional columns are missing.
  6. Integration with KPIEngine registration.
"""

import pandas as pd
import pytest

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.analytics.models import STATUS_OK, STATUS_UNAVAILABLE, UNIT_CURRENCY, UNIT_PERCENT, UNIT_COUNT
from app.analytics.domains.pharmacy import (
    supplier_payable_total,
    supplier_payable_by_supplier,
    dead_stock_value,
    dead_stock_item_count,
    gross_margin_by_category,
    payment_method_mix,
)
import app.schema  # ensure pharmacy domain pack is registered


AS_OF = "2026-01-01"
FILTERS = KPIFilters(as_of=AS_OF)


# ---------------------------------------------------------------------------
# 1. Supplier Payable Tests
# ---------------------------------------------------------------------------


class TestSupplierPayable:
    def test_supplier_payable_totals_and_grouping(self):
        """Supplier payables correctly sum and group by supplier with earliest due date."""
        df = pd.DataFrame([
            {"supplier_id": "OBS Pharma", "supplier_payable_amount": 30000, "supplier_payment_due_date": "2026-02-15"},
            {"supplier_id": "OBS Pharma", "supplier_payable_amount": 15000, "supplier_payment_due_date": "2026-01-20"},
            {"supplier_id": "Getz Pharma", "supplier_payable_amount": 25000, "supplier_payment_due_date": "2026-01-10"},
            {"supplier_id": "Searle", "supplier_payable_amount": 10000, "supplier_payment_due_date": "2026-03-01"},
        ])

        # 1. Total payable
        res_total = supplier_payable_total(df, FILTERS)
        assert res_total.status == STATUS_OK
        assert res_total.value == 80000.0
        assert res_total.unit == UNIT_CURRENCY

        # 2. By supplier breakdown
        res_by_supp = supplier_payable_by_supplier(df, FILTERS)
        assert res_by_supp.status == STATUS_OK
        assert res_by_supp.value == 80000.0
        assert len(res_by_supp.breakdown) == 3

        # Breakdown is sorted by payable amount descending
        b = res_by_supp.breakdown
        assert b[0]["supplier_id"] == "OBS Pharma"
        assert b[0]["payable_amount"] == 45000.0
        assert b[0]["earliest_due_date"] == "2026-01-20"  # earlier of Jan 20 and Feb 15
        assert b[0]["record_count"] == 2

        assert b[1]["supplier_id"] == "Getz Pharma"
        assert b[1]["payable_amount"] == 25000.0
        assert b[1]["earliest_due_date"] == "2026-01-10"

        assert b[2]["supplier_id"] == "Searle"
        assert b[2]["payable_amount"] == 10000.0
        assert b[2]["earliest_due_date"] == "2026-03-01"

    def test_supplier_payable_unavailable_when_column_missing(self):
        """When supplier_payable_amount is missing, returns unavailable rather than crashing."""
        df = pd.DataFrame([
            {"product_id": "Panadol", "quantity": 10, "amount": 500}
        ])
        res_total = supplier_payable_total(df, FILTERS)
        assert res_total.status == STATUS_UNAVAILABLE
        assert "supplier_payable_amount" in res_total.reason

        res_by_supp = supplier_payable_by_supplier(df, FILTERS)
        assert res_by_supp.status == STATUS_UNAVAILABLE

    def test_supplier_payable_zero_when_no_payables(self):
        """When column is present but all entries are zero, returns 0.0 with OK status."""
        df = pd.DataFrame([
            {"supplier_id": "OBS", "supplier_payable_amount": 0.0}
        ])
        res = supplier_payable_total(df, FILTERS)
        assert res.status == STATUS_OK
        assert res.value == 0.0


# ---------------------------------------------------------------------------
# 2. Dead Stock Detection Tests
# ---------------------------------------------------------------------------


class TestDeadStockDetection:
    def test_dead_stock_value_and_count_with_last_sold_date(self):
        """Stock unsold for >= 60 days measured from reference date 2026-01-01."""
        df = pd.DataFrame([
            # Prod A: last sold 2025-09-01 (122 days ago >= 60d) -> dead stock: 10 * 50 = 500
            {"product_id": "Augmentin", "quantity": 10, "cost": 50, "last_sold_date": "2025-09-01", "batch_no": "B1"},
            # Prod B: last sold 2025-10-01 (92 days ago >= 60d) -> dead stock: 5 * 100 = 500
            {"product_id": "Klaricid", "quantity": 5, "cost": 100, "last_sold_date": "2025-10-01", "batch_no": "B2"},
            # Prod C: last sold 2025-12-15 (17 days ago < 60d) -> active: 20 * 25 = 500
            {"product_id": "Panadol", "quantity": 20, "cost": 25, "last_sold_date": "2025-12-15", "batch_no": "B3"},
        ])

        res_val = dead_stock_value(df, FILTERS)
        assert res_val.status == STATUS_OK
        assert res_val.value == 1000.0  # 500 + 500
        assert res_val.unit == UNIT_CURRENCY
        assert len(res_val.breakdown) == 2

        # Check breakdown
        b_prods = {r["product_id"]: r for r in res_val.breakdown}
        assert "Augmentin" in b_prods
        assert b_prods["Augmentin"]["days_since_last_sale"] == 122
        assert b_prods["Augmentin"]["line_value"] == 500.0

        assert "Klaricid" in b_prods
        assert b_prods["Klaricid"]["days_since_last_sale"] == 92
        assert b_prods["Klaricid"]["line_value"] == 500.0

        # Count KPI
        res_cnt = dead_stock_item_count(df, FILTERS)
        assert res_cnt.status == STATUS_OK
        assert res_cnt.value == 2.0
        assert res_cnt.unit == UNIT_COUNT

    def test_dead_stock_derived_from_transaction_dates(self):
        """When last_sold_date is not present, derives it from sale transactions."""
        df = pd.DataFrame([
            # Inventory items with sale history: Lipitor last sale 2025-09-01 (122d >= 60d), Nexium last sale 2025-12-25 (7d < 60d)
            {"product_id": "Lipitor", "quantity": 4, "cost": 100, "date": "2025-09-01", "txn_type": "sale"},
            {"product_id": "Nexium", "quantity": 10, "cost": 20, "date": "2025-12-25", "txn_type": "sale"},
        ])
        res_val = dead_stock_value(df, FILTERS)
        assert res_val.status == STATUS_OK
        assert res_val.value == 400.0

    def test_dead_stock_unavailable_when_unresolvable(self):
        """When neither last_sold_date nor transaction date is present, returns unavailable."""
        df = pd.DataFrame([
            {"product_id": "Panadol", "quantity": 10, "cost": 20}
        ])
        res = dead_stock_value(df, FILTERS)
        assert res.status == STATUS_UNAVAILABLE
        assert "last_sold_date" in res.reason


# ---------------------------------------------------------------------------
# 3. Gross Margin by Category Tests
# ---------------------------------------------------------------------------


class TestGrossMarginByCategory:
    def test_category_margin_hand_computed(self):
        """
        Hand-computed check:
          Antibiotics:
            sale 1: rev = 1000, cost = 600 -> profit = 400
            sale 2: rev = 500, cost = 300 -> profit = 200
            Total: rev = 1500, cost = 900, profit = 600 -> margin = 40.0%
          Analgesics:
            sale 3: rev = 500, cost = 400 -> profit = 100, margin = 20.0%
          Overall:
            Total rev = 2000, cost = 1300, profit = 700 -> overall margin = 35.0%
        """
        df = pd.DataFrame([
            {"category": "Antibiotics", "amount": 1000, "cost": 600, "quantity": 1, "txn_type": "sale"},
            {"category": "Antibiotics", "amount": 500, "cost": 300, "quantity": 1, "txn_type": "sale"},
            {"category": "Analgesics", "amount": 500, "cost": 400, "quantity": 1, "txn_type": "sale"},
        ])

        res = gross_margin_by_category(df, FILTERS)
        assert res.status == STATUS_OK
        assert res.value == 35.0
        assert res.unit == UNIT_PERCENT
        assert len(res.breakdown) == 2

        b = {r["category"]: r for r in res.breakdown}
        assert b["Antibiotics"]["revenue"] == 1500.0
        assert b["Antibiotics"]["cogs"] == 900.0
        assert b["Antibiotics"]["profit"] == 600.0
        assert b["Antibiotics"]["margin_pct"] == 40.0

        assert b["Analgesics"]["revenue"] == 500.0
        assert b["Analgesics"]["cogs"] == 400.0
        assert b["Analgesics"]["profit"] == 100.0
        assert b["Analgesics"]["margin_pct"] == 20.0

    def test_category_margin_unavailable_when_missing_columns(self):
        """Returns unavailable if category or cost columns are missing."""
        df_no_cat = pd.DataFrame([{"amount": 100, "cost": 60, "quantity": 1, "txn_type": "sale"}])
        assert gross_margin_by_category(df_no_cat, FILTERS).status == STATUS_UNAVAILABLE

        df_no_cost = pd.DataFrame([{"category": "Cardiac", "amount": 100, "quantity": 1, "txn_type": "sale"}])
        assert gross_margin_by_category(df_no_cost, FILTERS).status == STATUS_UNAVAILABLE


# ---------------------------------------------------------------------------
# 4. Payment Method Mix Tests
# ---------------------------------------------------------------------------


class TestPaymentMethodMix:
    def test_payment_method_mix_calculation(self):
        """Revenue share by payment method: Cash 50%, Card 30%, Credit 20%."""
        df = pd.DataFrame([
            {"payment_method": "Cash", "amount": 5000, "txn_type": "sale"},
            {"payment_method": "Card", "amount": 3000, "txn_type": "sale"},
            {"payment_method": "Credit", "amount": 2000, "txn_type": "sale"},
        ])

        res = payment_method_mix(df, FILTERS)
        assert res.status == STATUS_OK
        assert res.value == 10000.0
        assert res.unit == UNIT_CURRENCY
        assert len(res.breakdown) == 3

        b = {r["payment_method"]: r for r in res.breakdown}
        assert b["Cash"]["revenue"] == 5000.0
        assert b["Cash"]["share_pct"] == 50.0

        assert b["Card"]["revenue"] == 3000.0
        assert b["Card"]["share_pct"] == 30.0

        assert b["Credit"]["revenue"] == 2000.0
        assert b["Credit"]["share_pct"] == 20.0

    def test_payment_method_mix_unavailable_when_missing(self):
        """Returns unavailable if payment method column is missing."""
        df = pd.DataFrame([{"amount": 100, "txn_type": "sale"}])
        assert payment_method_mix(df, FILTERS).status == STATUS_UNAVAILABLE


# ---------------------------------------------------------------------------
# 5. Engine Registration Integration
# ---------------------------------------------------------------------------


class TestEngineRegistration:
    def test_all_new_kpis_registered_in_engine(self):
        """Confirm all new pharmacy KPIs are reachable via engine.compute_all."""
        df = pd.DataFrame([
            {
                "product_id": "Panadol",
                "category": "Analgesics",
                "amount": 1000,
                "cost": 60,
                "quantity": 10,
                "txn_type": "sale",
                "payment_method": "Cash",
                "supplier_id": "GSK",
                "supplier_payable_amount": 5000,
                "supplier_payment_due_date": "2026-02-01",
                "last_sold_date": "2025-08-01",
            }
        ])

        results = engine.compute_all(df, FILTERS, domain="pharmacy")

        assert "supplier_payable_total" in results
        assert results["supplier_payable_total"].value == 5000.0

        assert "supplier_payable_by_supplier" in results
        assert results["supplier_payable_by_supplier"].value == 5000.0

        assert "dead_stock_value" in results
        assert results["dead_stock_value"].value == 600.0  # 10 * 60

        assert "dead_stock_item_count" in results
        assert results["dead_stock_item_count"].value == 1.0

        assert "gross_margin_by_category" in results
        assert results["gross_margin_by_category"].value == 40.0

        assert "payment_method_mix" in results
        assert results["payment_method_mix"].value == 1000.0
