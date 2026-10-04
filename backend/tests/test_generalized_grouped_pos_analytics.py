import pandas as pd
import importlib
import os
import sqlite3
from datetime import date, timedelta

from app.analytics.tabular_query import (
    _apply_explicit_date_filters,
    _answer_sales_question,
    answer_tabular_question,
)
from app.analytics.pharmacy_intelligence import answer_pharmacy_business_question
from app.analytics.seam import AnalyticsRouter
from app.rag.router import classify_route


def _build_database_frame(raw, monkeypatch, tmp_path):
    connect = sqlite3.connect

    def connect_without_repo_side_effects(database, *args, **kwargs):
        if os.path.basename(str(database)).casefold() == "file_registry.sqlite3":
            return connect(str(tmp_path / "file_registry.sqlite3"), *args, **kwargs)
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect_without_repo_side_effects)
    registry = importlib.import_module("app.ingestion.registry")
    monkeypatch.setattr(registry, "PROJECT_ROOT", str(tmp_path))
    analytics = importlib.import_module("app.api.analytics")
    return analytics._build_canonical_database(raw)


def test_opaque_catalog_primary_key_resolves_inventory_foreign_key(monkeypatch, tmp_path):
    raw = pd.DataFrame([
        {"table_name": "tbl_products", "product_id": "Alpha 10mg", "product_code": "SKU-A", "_extra.ID": "17", "txn_type": "catalog"},
        {"table_name": "tbl_10", "product_id": "17", "quantity": 4, "reorder_level": 12, "expiry_date": "2027-01-01", "batch_no": "A1", "txn_type": "inventory"},
        {"table_name": "tbl_SalesDetails", "product_id": "Alpha 10mg", "quantity": 30, "amount": 300, "unit_price": 10, "cost": 5, "date": "2026-09-01", "transaction_id": "T1", "txn_type": "sale"},
    ])
    result = _build_database_frame(raw, monkeypatch, tmp_path)
    stock = result.loc[result.txn_type.eq("inventory")]
    assert stock.product_id.tolist() == ["Alpha 10mg"]
    answer = answer_tabular_question(
        "Which medicines are selling quickly and are currently below their reorder level?",
        result,
    )
    assert answer["values"]["status"] == "ok"
    assert "Alpha 10mg" in answer["answer"]


def test_payment_method_grouping_carries_header_provenance(monkeypatch, tmp_path):
    raw = pd.DataFrame([
        {"table_name": "sales_header", "transaction_id": "T1", "invoice_id": "R1", "payment_method": "Cash", "source_file": "sql://sales/tbl_21", "source_row": 11, "file_id": "db_sales_tbl_21"},
        {"table_name": "sales_header", "transaction_id": "T2", "invoice_id": "R2", "payment_method": "Card", "source_file": "sql://sales/tbl_21", "source_row": 12, "file_id": "db_sales_tbl_21"},
        {"table_name": "sales_detail", "transaction_id": "T1", "product_id": "A", "quantity": 1, "amount": 10, "source_file": "sql://sales/tbl_22", "source_row": 1, "file_id": "db_sales_tbl_22"},
        {"table_name": "sales_detail", "transaction_id": "T2", "product_id": "B", "quantity": 1, "amount": 20, "source_file": "sql://sales/tbl_22", "source_row": 2, "file_id": "db_sales_tbl_22"},
    ])
    joined = _build_database_frame(raw, monkeypatch, tmp_path)

    result = answer_tabular_question(
        "How many receipts were paid with each payment method?", joined
    )

    assert result["values"]["receipts_by_payment_method"] == [
        {"payment_method": "Card", "receipts": 1},
        {"payment_method": "Cash", "receipts": 1},
    ]
    assert result["source_rows"] == [
        ("sql://sales/tbl_21", 11), ("sql://sales/tbl_21", 12)
    ]


def test_explicit_date_filter_applies_to_transactions_and_preserves_stock_snapshot():
    frame = pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "transaction_id": "A", "date": "2026-08-01", "amount": 100, "quantity": 1, "source_row": 1},
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "transaction_id": "B", "date": "2026-09-15", "amount": 200, "quantity": 2, "source_row": 2},
        {"table_name": "tbl_10", "txn_type": "inventory", "date": None, "stock_qty": 8, "quantity": 8, "source_row": 3},
        {"table_name": "tbl_15", "txn_type": "expense", "date": "2026-08-10", "amount": 500, "quantity": 5, "source_row": 4},
    ])
    filtered = _apply_explicit_date_filters(
        "What were my total sales?", frame,
        {"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert set(filtered.source_row) == {2, 3, 4}
    result = answer_tabular_question(
        "What were my total sales?", frame,
        filters={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert "200" in result["answer"]


def test_profit_question_abstains_instead_of_assuming_a_margin():
    frame = pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "product_id": "P", "date": "2026-09-30", "quantity": 10, "amount": 1000, "unit_price": 100},
        {"table_name": "tbl_10", "txn_type": "inventory", "product_id": "P", "quantity": 5, "stock_qty": 5, "reorder_level": 10},
    ])
    result = answer_pharmacy_business_question(
        "Which products generate the most profit but have insufficient inventory?", frame,
    )
    assert result["values"]["status"] == "unsupported_field"
    assert "assumed margin" in result["answer"]


def test_missing_batch_expiry_dates_return_a_clear_unavailable_result():
    frame = pd.DataFrame([
        {"table_name": "tbl_10", "txn_type": "inventory", "product_id": "Alpha", "quantity": 4, "stock_qty": 4, "batch_no": "A1", "expiry_date": "not a date"},
    ])
    result = answer_tabular_question("Which batch of Alpha should be sold first?", frame)
    assert result["values"]["status"] == "unsupported_field"
    assert result["values"]["required_field"] == "expiry_date"


def test_understock_and_storage_location_questions_route_to_analytics():
    assert classify_route("Which products are understocked?") == "analytics"
    assert classify_route("Where is Omep 20mg Capsules stored?") == "analytics"


def test_sales_grouping_uses_requested_dimension_and_metric_in_mixed_pos_frame():
    frame = pd.DataFrame([
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "A", "product_id": "Alpha", "category": "A", "quantity": 3, "amount": 30, "date": "2026-09-01"},
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "B", "product_id": "Beta", "category": "B", "quantity": 8, "amount": 80, "date": "2026-09-02"},
        {"txn_type": "inventory", "table_name": "tbl_Inventory", "transaction_id": None, "product_id": "Gamma", "category": "A", "quantity": 500, "amount": 9000, "date": None},
        {"txn_type": "expense", "table_name": "tbl_PurchaseDetails", "transaction_id": "P1", "product_id": "Delta", "category": "B", "quantity": 900, "amount": 99000, "date": "2026-09-01"},
    ])
    top = _answer_sales_question("Which product sold the most units?", frame)
    assert top["values"]["results"][0] == {"Product": "Beta", "units_sold": 8.0}
    category = _answer_sales_question("Which category generates the most revenue?", frame)
    assert category["values"]["results"][0] == {"Category": "B", "sales": 80.0}


def test_sales_transaction_count_and_average_are_invoice_grain():
    frame = pd.DataFrame([
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "A", "product_id": "Alpha", "quantity": 1, "amount": 30},
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "A", "product_id": "Beta", "quantity": 1, "amount": 20},
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "B", "product_id": "Beta", "quantity": 1, "amount": 50},
    ])
    count = _answer_sales_question("How many transactions have been made?", frame)
    avg = _answer_sales_question("What is my average transaction value?", frame)
    assert count["values"]["transaction_count"] == 2
    assert avg["values"]["average_transaction_value"] == 50


def test_sales_profit_and_price_ranks_use_recorded_cost_and_price():
    frame = pd.DataFrame([
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "A", "product_id": "Alpha", "quantity": 2, "amount": 30, "line_cost": 20, "unit_price": 15},
        {"txn_type": "sale", "table_name": "tbl_SalesDetails", "transaction_id": "B", "product_id": "Beta", "quantity": 1, "amount": 50, "line_cost": 45, "unit_price": 50},
        {"txn_type": "inventory", "table_name": "tbl_Inventory", "product_id": "Gamma", "quantity": 200, "amount": 9000, "line_cost": 8000, "unit_price": 100},
    ])
    profit = _answer_sales_question("What is my estimated gross profit?", frame)
    highest = _answer_sales_question("Which product generated the highest gross profit?", frame)
    cheapest = _answer_sales_question("Which product has the lowest selling price?", frame)
    assert profit["values"]["gross_profit"] == 15
    assert highest["values"]["results"][0] == {"product": "Alpha", "gross_profit": 10.0}
    assert cheapest["values"]["selling_price"] == 15

def test_mixed_pos_simple_sales_query_hands_off_to_domain_analytics():
    day = (date.today() - timedelta(days=3)).isoformat()
    question = "What were my sale 3 days ago?"
    filters = {"date_from": day, "date_to": day}
    frame = pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "date": day,
         "amount": 275.0, "quantity": 2, "source_row": 12},
        {"table_name": "tbl_InventoryBatches", "txn_type": "inventory",
         "date": None, "amount": 9000.0, "quantity": 60, "source_row": 8},
    ])

    # The generic planner has no invoice key to calculate from, so it must defer
    # instead of emitting a refusal that prevents the registered POS planner.
    assert answer_tabular_question(question, frame, filters=filters) is None

    computed, source_rows = AnalyticsRouter().compute(
        question, filters=filters, kb_records=frame, domain="pharmacy"
    )
    result = computed["pharmacy_pos_analysis"]
    assert result["status"] == "ok"
    assert result["value"] == 275.0
    assert source_rows == [12]

