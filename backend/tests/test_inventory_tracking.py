"""
Smart Inventory & Expiry Tracking tests.

Tests:
1. Near-Expiry Alerts & Liquidation Suggestions (expiring_medicines_liquidation)
2. Low-Stock & Reorder Point Predictions (low_stock_reorder_predictions)
3. Router intent classification and filter extraction for smart inventory queries
4. Non-disruptive domain registration and provenance tracking
"""

import pandas as pd
import pytest

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.analytics.models import STATUS_OK
from app.analytics.seam import AnalyticsRouter, select_kpi_keys
from app.rag.router import RouteType, classify_route, extract_filters
import app.schema  # registers the pharmacy domain pack

AS_OF = "2026-01-01"


@pytest.fixture
def pharma_inventory_df() -> pd.DataFrame:
    """
    Hand-crafted pharmacy inventory dataset with known expiry dates,
    stock on hand, reorder levels, trade prices, and suppliers.
    Reference date: 2026-01-01.
    """
    return pd.DataFrame(
        [
            # product_id, generic_name, category, batch_no, expiry_date, quantity, cost, mrp, supplier_id, reorder_level
            (1, "Panadol 500mg Tab", "Paracetamol", "analgesic", "B101", "2026-01-10", 10.0, 14.0, 20.0, "GSK Pakistan", 50.0),      # 9 days -> clearance (<=15d)
            (2, "Augmentin 625mg Tab", "Amoxicillin", "antibiotic", "B102", "2026-01-25", 20.0, 180.0, 250.0, "GSK Pakistan", 20.0),   # 24 days -> markdown (15-30d)
            (3, "Brufen 400mg Tab", "Ibuprofen", "analgesic", "B103", "2026-02-10", 30.0, 9.0, 15.0, "Abbott Labs", 100.0),          # 40 days -> push/bundle (30-45d)
            (4, "Lipitor 20mg Tab", "Atorvastatin", "cardiac", "B104", "2026-02-25", 15.0, 135.0, 140.0, "Pfizer", 25.0),             # 55 days -> return to distributor (>45d)
            (5, "Crestor 10mg Tab", "Rosuvastatin", "cardiac", "B105", "2026-03-01", 5.0, 120.0, 160.0, "AstraZeneca", 20.0),         # 59 days -> return to distributor (>45d)
            (6, "Metoprolol 50mg Tab", "Metoprolol", "cardiac", "B106", "2026-04-15", 50.0, 18.0, 25.0, "Novartis", 40.0),           # 104 days -> beyond 60d
            (7, "Amlodipine 5mg Tab", "Amlodipine", "cardiac", "B107", "2026-06-01", 100.0, 6.0, 10.0, "Pfizer", 60.0),              # 151 days -> beyond 60d
            (8, "Expired Panadol", "Paracetamol", "analgesic", "B100", "2025-12-01", 12.0, 14.0, 20.0, "GSK Pakistan", 10.0),         # -31 days -> already expired
        ],
        columns=[
            "source_row", "product_id", "generic_name", "category", "batch_no",
            "expiry_date", "quantity", "cost", "mrp", "supplier_id", "reorder_level",
        ],
    ).assign(source_connector="csv")


@pytest.fixture
def pharma_sales_df() -> pd.DataFrame:
    """
    30-day sales transaction history for fast-moving drugs (2026-01-01 to 2026-01-30).
    """
    rows = []
    # Lipitor: 90 units sold over 30 days -> 3.0 units/day velocity. Current stock: 5 units -> 1.67 days supply (CRITICAL < 3d)
    for d in range(1, 31):
        rows.append((f"INV-{1000+d}", f"2026-01-{d:02d}", "Lipitor 20mg Tab", "Atorvastatin", "cardiac", 3.0, 165.0, "Pfizer", 5.0, 25.0))

    # Crestor: 60 units sold over 30 days -> 2.0 units/day velocity. Current stock: 4 units -> 2.0 days supply (CRITICAL < 3d)
    for d in range(1, 31):
        rows.append((f"INV-{2000+d}", f"2026-01-{d:02d}", "Crestor 10mg Tab", "Rosuvastatin", "cardiac", 2.0, 145.0, "AstraZeneca", 4.0, 20.0))

    # Amlodipine: 30 units sold over 30 days -> 1.0 unit/day velocity. Current stock: 50 units -> 50.0 days supply (HEALTHY / OVERSTOCKED)
    for d in range(1, 31):
        rows.append((f"INV-{3000+d}", f"2026-01-{d:02d}", "Amlodipine 5mg Tab", "Amlodipine", "cardiac", 1.0, 8.0, "Pfizer", 50.0, 60.0))

    # Augmentin (Antibiotic): 60 units sold over 30 days -> 2.0 units/day. Current stock: 2 units -> 1.0 day supply (CRITICAL < 3d)
    for d in range(1, 31):
        rows.append((f"INV-{4000+d}", f"2026-01-{d:02d}", "Augmentin 625mg Tab", "Amoxicillin", "antibiotic", 2.0, 220.0, "GSK Pakistan", 2.0, 20.0))

    return pd.DataFrame(
        rows,
        columns=[
            "invoice_id", "date", "product_id", "generic_name", "category",
            "quantity", "unit_price", "supplier_id", "stock", "reorder_level",
        ],
    ).assign(source_connector="csv", source_row=lambda df: df.index + 2)


# ===========================================================================
# 1. Near-Expiry & Liquidation Tests
# ===========================================================================


def test_near_expiry_60_days_liquidation(pharma_inventory_df):
    filters = KPIFilters(as_of=AS_OF, options={"expiry_days": 60})
    res = engine.compute("expiring_medicines_liquidation", pharma_inventory_df, filters, domain="pharmacy")

    assert res.status == STATUS_OK
    assert res.breakdown is not None
    # In next 60 days: Panadol (9d), Augmentin (24d), Brufen (40d), Lipitor (55d), Crestor (59d)
    # Total items = 5. Excludes expired Panadol (-31d) and beyond 60d (Metoprolol 104d, Amlodipine 151d).
    assert len(res.breakdown) == 5

    # Check soonest expiry first
    first = res.breakdown[0]
    assert first["product_id"] == "Panadol 500mg Tab"
    assert first["days_to_expiry"] == 9
    assert first["suggested_discount_pct"] == 50
    assert "Clearance" in first["suggested_action"]

    # Check 15-30 day tier (Augmentin)
    second = res.breakdown[1]
    assert second["product_id"] == "Augmentin 625mg Tab"
    assert second["days_to_expiry"] == 24
    assert second["suggested_discount_pct"] == 25
    assert "Promotional Markdown" in second["suggested_action"]

    # Check 30-45 day tier (Brufen)
    third = res.breakdown[2]
    assert third["product_id"] == "Brufen 400mg Tab"
    assert third["days_to_expiry"] == 40
    assert "Prioritize Dispensing" in third["suggested_action"]

    # Check >45 day tier (Lipitor, Crestor -> Return to distributor)
    fourth = res.breakdown[3]
    assert fourth["product_id"] == "Lipitor 20mg Tab"
    assert fourth["days_to_expiry"] == 55
    assert "Return to Distributor (Pfizer)" in fourth["suggested_action"]
    assert fourth["suggested_discount_pct"] == 0

    fifth = res.breakdown[4]
    assert fifth["product_id"] == "Crestor 10mg Tab"
    assert fifth["days_to_expiry"] == 59
    assert "Return to Distributor (AstraZeneca)" in fifth["suggested_action"]


def test_near_expiry_custom_horizon_30_days(pharma_inventory_df):
    filters = KPIFilters(as_of=AS_OF, options={"expiry_days": 30})
    res = engine.compute("expiring_medicines_liquidation", pharma_inventory_df, filters, domain="pharmacy")

    assert res.status == STATUS_OK
    assert len(res.breakdown) == 2
    prods = [r["product_id"] for r in res.breakdown]
    assert "Panadol 500mg Tab" in prods
    assert "Augmentin 625mg Tab" in prods


# ===========================================================================
# 2. Low-Stock & Sales Velocity Reorder Point Tests
# ===========================================================================


def test_cardiac_low_stock_3_day_supply(pharma_sales_df):
    filters = KPIFilters(category="cardiac", options={"days_supply_threshold": 3.0})
    res = engine.compute("low_stock_reorder_predictions", pharma_sales_df, filters, domain="pharmacy")

    assert res.status == STATUS_OK
    # 2 cardiac drugs below 3-day supply: Lipitor (1.7d) and Crestor (2.0d). Amlodipine is 50.0d.
    assert res.value == 2.0
    assert res.breakdown is not None
    assert len(res.breakdown) == 3

    lipitor = next(r for r in res.breakdown if "Lipitor" in r["product_id"])
    assert lipitor["daily_sales_velocity"] == 3.0
    assert lipitor["days_of_supply"] == 1.7
    assert lipitor["stockout_risk"] == "CRITICAL_STOCKOUT_RISK"
    assert lipitor["recommended_reorder_qty"] > 0

    crestor = next(r for r in res.breakdown if "Crestor" in r["product_id"])
    assert crestor["daily_sales_velocity"] == 2.0
    assert crestor["days_of_supply"] == 2.0
    assert crestor["stockout_risk"] == "CRITICAL_STOCKOUT_RISK"

    amlodipine = next(r for r in res.breakdown if "Amlodipine" in r["product_id"])
    assert amlodipine["daily_sales_velocity"] == 1.0
    assert amlodipine["days_of_supply"] == 50.0
    assert amlodipine["stockout_risk"] != "CRITICAL_STOCKOUT_RISK"


def test_antibiotic_low_stock_filter(pharma_sales_df):
    filters = KPIFilters(category="antibiotic", options={"days_supply_threshold": 3.0})
    res = engine.compute("low_stock_reorder_predictions", pharma_sales_df, filters, domain="pharmacy")

    assert res.status == STATUS_OK
    assert len(res.breakdown) == 1
    assert "Augmentin" in res.breakdown[0]["product_id"]
    assert res.breakdown[0]["days_of_supply"] == 1.0
    assert res.breakdown[0]["stockout_risk"] == "CRITICAL_STOCKOUT_RISK"


# ===========================================================================
# 3. Router Intent & Filter Extraction Integration Tests
# ===========================================================================


def test_classify_route_for_smart_inventory_queries():
    # 1. Near expiry query
    q1 = "Which medicines expire in the next 60 days?"
    assert classify_route(q1) == RouteType.ANALYTICS

    # 2. Fast-moving cardiac drugs running below 3-day supply
    q2 = "Which fast-moving cardiac drugs are running below a 3-day supply based on this month's sales velocity?"
    assert classify_route(q2) == RouteType.ANALYTICS

    # 3. Reorder point and stockout queries
    q3 = "Show medicines with critical stockout risk"
    assert classify_route(q3) == RouteType.ANALYTICS

    # 4. Roman-Urdu query
    q4 = "Agle 60 din me konsi medicine expire hone wali hai?"
    assert classify_route(q4) == RouteType.ANALYTICS

    q5 = "Kam stock wali dawaiyan konsi hain?"
    assert classify_route(q5) == RouteType.ANALYTICS


def test_extract_filters_for_smart_inventory():
    # Expiry extraction
    f1 = extract_filters("Which medicines expire in the next 60 days?", domain="pharmacy")
    assert f1.get("options", {}).get("expiry_days") == 60

    # Cardiac category & 3-day supply threshold extraction
    f2 = extract_filters(
        "Which fast-moving cardiac drugs are running below a 3-day supply based on this month's sales velocity?",
        domain="pharmacy"
    )
    assert f2.get("category") == "cardiac"
    assert f2.get("options", {}).get("days_supply_threshold") == 3.0


def test_select_kpi_keys_for_smart_inventory():
    keys1 = select_kpi_keys("Which medicines expire in the next 60 days?", domain="pharmacy")
    assert "expiring_medicines_liquidation" in keys1

    keys2 = select_kpi_keys("Which fast-moving cardiac drugs are running below a 3-day supply?", domain="pharmacy")
    assert "low_stock_reorder_predictions" in keys2


def test_analytics_router_end_to_end(pharma_inventory_df):
    router = AnalyticsRouter()
    records = pharma_inventory_df.to_dict(orient="records")
    filters = {"as_of": AS_OF, "options": {"expiry_days": 60}}

    computed, source_rows = router.compute(
        "Which medicines expire in the next 60 days?",
        filters,
        records,
        domain="pharmacy"
    )

    assert computed is not None
    assert "expiring_medicines_liquidation" in computed
    exp_kpi = computed["expiring_medicines_liquidation"]
    assert exp_kpi["status"] == STATUS_OK
    assert "breakdown" in exp_kpi
    assert len(exp_kpi["breakdown"]) == 5
    assert len(source_rows) > 0
