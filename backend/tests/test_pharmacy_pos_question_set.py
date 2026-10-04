"""Routing contract for the pharmacy-owner POS analytics benchmark."""
from pathlib import Path
import re
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from app.analytics.filters import KPIFilters
from app.analytics.domains.pharmacy_pos import analyze_pos_question
from app.api.analytics import _build_canonical_database
import app.api.analytics as analytics_api
from app.analytics.domains import analyze_specialized_question
from app.rag.router import RouteType, classify_route, extract_filters
from app.schema.domain import get_domain_pack
from app.schema.mapper import map_headers
import app.schema.pharmacy  # noqa: F401


QUESTION_SET = Path(__file__).parent / "fixtures" / "pharmacy_pos_owner_questions.txt"


def _questions():
    text = QUESTION_SET.read_text(encoding="utf-8")
    return [match.group(1).strip() for line in text.splitlines()
            if (match := re.match(r"\s*\d+\.\s*(.+)", line))]


def test_all_owner_benchmark_questions_route_to_analytics():
    questions = _questions()
    assert len(questions) == 100
    wrong = [(i, question, classify_route(question))
             for i, question in enumerate(questions, start=1)
             if classify_route(question) != RouteType.ANALYTICS]
    assert wrong == []


def test_pos_analytics_route_survives_paraphrases_and_common_typos():
    variants = [
        "How much did we sell yestarday?",
        "Forecast next month's revenue, please",
        "Which medicines are running low?",
        "Show stock under 10 units",
        "Which supplier delivers fastest?",
        "When did I last pay for Brufen 400mg?",
        "kitni sale hui pichlay 7 din mein?",
    ]
    assert [classify_route(question) for question in variants] == [RouteType.ANALYTICS] * len(variants)
    assert extract_filters("How much did I sell yestarday?", "pharmacy")["date_to"]
    assert extract_filters("How much did I sell in the last 7 days?", "pharmacy")["relative_days"] == 7


def test_exact_bill_and_named_customer_questions_route_to_record_retrieval():
    assert classify_route("What medicines were sold in Bill No 400001?") == RouteType.RAG
    assert classify_route("Bill No 400001 mein kon konsi dawaiyan bechi gayi hain?") == RouteType.RAG
    assert classify_route("What was the net payable for invoice 400001?") == RouteType.ANALYTICS
    assert classify_route("Did Umar Mirza make any purchases, and what did he buy?") == RouteType.RAG


def test_bonus_and_discount_lists_are_deterministic_full_table_analytics():
    frame = pd.DataFrame([
        {"product_id": "Rigix 10mg", "bonus_quantity": 3, "discount_pct": 20,
         "invoice_id": "I-1", "source_row": 2},
        {"product_id": "Tegral Cream", "bonus_quantity": 2, "discount_pct": 20,
         "invoice_id": "I-2", "source_row": 3},
        {"product_id": "Panadol 500mg", "bonus_quantity": 0, "discount_pct": 0,
         "invoice_id": "I-3", "source_row": 4},
    ])
    bonus = analyze_specialized_question("Were there any bonus quantities?", frame, KPIFilters(), "pharmacy")
    discount = analyze_specialized_question("Which products received a 20% discount?", frame, KPIFilters(), "pharmacy")
    assert bonus.value == 5
    assert {row["product"] for row in bonus.breakdown} == {"Rigix 10mg", "Tegral Cream"}
    assert bonus.provenance.source_rows == [2, 3]
    assert discount.value == 2
    assert {row["product"] for row in discount.breakdown} == {"Rigix 10mg", "Tegral Cream"}
    assert discount.provenance.source_rows == [2, 3]


def test_flat_sales_invoice_lookup_uses_header_total_once_and_abstains_if_missing():
    frame = pd.DataFrame([
        {"invoice_id": "INV-123", "product_id": "Drug A", "quantity": 1, "amount": 100,
         "invoice_total": 250, "discount_pct": 10, "source_row": 2},
        {"invoice_id": "INV-123", "product_id": "Drug B", "quantity": 2, "amount": 150,
         "invoice_total": 250, "discount_pct": 0, "source_row": 3},
    ])
    result = analyze_specialized_question("Find details of invoice INV-123, including total amount and discount",
                                          frame, KPIFilters(), "pharmacy")
    missing = analyze_specialized_question("What was the total amount for invoice INV-999?",
                                           frame, KPIFilters(), "pharmacy")
    assert result.value == 250
    assert len(result.breakdown) == 2
    assert result.provenance.source_rows == [2, 3]
    assert not missing.is_available
    assert "invoice inv-999 was not found" in missing.reason.casefold()


def test_exact_invoice_net_payable_uses_selected_sales_header_value():
    frame = pd.DataFrame([
        {"invoice_id": "495449", "table_name": "tbl_SalesHeader", "net_payable": 798.0,
         "amount": 0.0, "source_row": 449},
        {"invoice_id": "495450", "table_name": "tbl_SalesHeader", "net_payable": 123.0,
         "amount": 0.0, "source_row": 450},
    ])
    result = analyze_specialized_question("What was the net payable for invoice 495449?",
                                          frame, KPIFilters(), "pharmacy")
    assert result.value == 798.0
    assert result.name == "Net payable"
    assert result.provenance.source_rows == [449]
    assert result.breakdown == [{"Net payable": 798.0}]


def test_canonical_database_resolves_unambiguous_product_code_for_stock_rows():
    # Realistic POS schemas often use numeric product_id on sales and a SKU on
    # batches. A stock row should join only when sales establish a unique alias.
    raw = pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "product_id": "P-17", "product_code": "SKU-17",
         "quantity": 2, "amount": 200},
        {"table_name": "tbl_Batches", "product_id": None, "product_code": "sku-17",
         "quantity": 8, "expiry_date": "2027-02-01"},
        {"table_name": "tbl_SalesDetails", "product_id": "P-18", "product_code": "SKU-X",
         "quantity": 1, "amount": 100},
        {"table_name": "tbl_SalesDetails", "product_id": "P-19", "product_code": "SKU-X",
         "quantity": 1, "amount": 100},
        {"table_name": "tbl_Batches", "product_id": None, "product_code": "SKU-X",
         "quantity": 50, "expiry_date": "2027-02-01"},
    ])
    canonical = _build_canonical_database(raw)
    stock = canonical[canonical["table_name"] == "tbl_Batches"].set_index("product_code")
    assert stock.loc["sku-17", "product_id"] == "P-17"
    assert pd.isna(stock.loc["SKU-X", "product_id"])


def test_canonical_database_skips_non_unique_header_join_to_prevent_fanout():
    raw = pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "transaction_id": "TX-1", "product_id": "Drug A",
         "quantity": 2, "amount": 200, "source_row": 11},
        {"table_name": "tbl_SalesDetails", "transaction_id": "TX-2", "product_id": "Drug B",
         "quantity": 1, "amount": 50, "source_row": 12},
        # Bad export has duplicate transaction header keys. A regular merge
        # would double TX-1 sales and make revenue/provenance wrong.
        {"table_name": "tbl_SalesHeader", "transaction_id": "TX-1", "date": "2026-09-30",
         "customer_id": "C-1", "source_row": 101},
        {"table_name": "tbl_SalesHeader", "transaction_id": "TX-1", "date": "2026-09-30",
         "customer_id": "C-1", "source_row": 102},
        {"table_name": "tbl_SalesHeader", "transaction_id": "TX-2", "date": "2026-09-30",
         "customer_id": "C-2", "source_row": 103},
    ])

    canonical = _build_canonical_database(raw)
    sales = canonical[canonical["txn_type"] == "sale"]
    assert len(sales) == 2
    assert sales["amount"].sum() == 250
    assert sales["source_row"].tolist() == [11, 12]
    assert sales["_join_warning"].str.contains("no unique shared transaction key").all()


def test_canonical_header_join_keeps_invoice_totals_at_header_grain():
    raw = pd.DataFrame([
        {"table_name": "tbl_PurchaseDetails", "source_row": 10, "transaction_id": "PO-1",
         "product_id": "Drug A", "quantity": 1, "amount": 100},
        {"table_name": "tbl_PurchaseDetails", "source_row": 11, "transaction_id": "PO-1",
         "product_id": "Drug B", "quantity": 2, "amount": 50},
        {"table_name": "tbl_PurchaseHeader", "source_row": 90, "transaction_id": "PO-1",
         "date": "2026-09-29", "supplier_name": "Acme Pharma", "net_payable": 250},
    ])
    canonical = _build_canonical_database(raw)
    details = canonical[canonical["table_name"] == "tbl_PurchaseDetails"]
    assert len(details) == 2
    assert details["amount"].sum() == 150
    assert details["net_payable"].sum() == 250
    assert details["net_payable"].notna().sum() == 1
    assert details["date"].dropna().unique().tolist() == ["2026-09-29"]


def test_canonical_sales_header_join_preserves_invoice_tax_once():
    raw = pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "source_file": "sql://sales/tbl_SalesDetails", "source_row": 10,
         "transaction_id": "INV-1", "product_id": "Drug A", "quantity": 1, "amount": 100},
        {"table_name": "tbl_SalesDetails", "source_file": "sql://sales/tbl_SalesDetails", "source_row": 11,
         "transaction_id": "INV-1", "product_id": "Drug B", "quantity": 2, "amount": 50},
        {"table_name": "tbl_SalesHeader", "source_file": "sql://sales/tbl_SalesHeader", "source_row": 90,
         "transaction_id": "INV-1", "invoice_tax": 7.5, "invoice_discount": 25.0, "date": "2026-09-29"},
    ])
    canonical = _build_canonical_database(raw)
    details = canonical[canonical["table_name"] == "tbl_SalesDetails"]

    assert len(details) == 2
    assert pd.to_numeric(details["invoice_tax"], errors="coerce").sum() == 7.5
    assert details["invoice_tax"].notna().sum() == 1
    assert pd.to_numeric(details["invoice_discount"], errors="coerce").sum() == 25.0
    assert details["invoice_discount"].notna().sum() == 1
    assert details["_header_source_row"].dropna().unique().tolist() == [90]


def test_whole_database_analytics_refuses_a_registry_index_gap(monkeypatch):
    records = [
        SimpleNamespace(file_id="indexed", group_name="IncompletePOS", status="active",
                        chunk_count=1, table_name="tbl_SalesDetails"),
        SimpleNamespace(file_id="missing", group_name="IncompletePOS", status="active",
                        chunk_count=2, table_name="tbl_Batches"),
    ]
    class FakeCollection:
        def get(self, **kwargs):
            return {"metadatas": [{"file_id": "indexed", "group_name": "IncompletePOS"}], "documents": [""]}
    class FakeKB:
        def _get_chroma(self):
            return FakeCollection()
    monkeypatch.setattr(analytics_api, "KnowledgeBase", FakeKB)
    monkeypatch.setattr(analytics_api.file_registry, "list_files", lambda: records)
    analytics_api._clear_cache()
    with pytest.raises(HTTPException) as exc:
        analytics_api._load_canonical(analytics_api.KPIRequest(file_path="db://IncompletePOS", domain="pharmacy"))
    assert exc.value.status_code == 409
    assert "1 active source table(s) have no indexed rows" in exc.value.detail


OWNER_ADVICE_QUESTIONS = [
    "How can I increase my sales?",
    "Which medicines should I promote to increase my revenue?",
    "Which products should I restock to avoid losing sales?",
    "Which slow-moving medicines should I stop purchasing?",
    "How can I reduce losses from medicines that are about to expire?",
    "Which products should I focus on selling this month to increase profit?",
    "Which medicines are overstocked, and what should I do about them?",
    "Which high-demand medicines am I not keeping enough stock of?",
    "Based on my sales history, what changes should I make to my inventory?",
    "What are the biggest problems currently affecting my pharmacy's sales and profitability, and what should I do about them?",
]


def test_owner_advice_questions_use_full_dataset_analytics_not_top_k_rag():
    assert [classify_route(question) for question in OWNER_ADVICE_QUESTIONS] == [RouteType.ANALYTICS] * 10


def test_owner_advice_answers_return_evidence_or_explicit_data_limitations():
    results = [_answer(question) for question in OWNER_ADVICE_QUESTIONS]
    assert all(result.name != "Sales revenue" for result in results)
    assert results[0].breakdown[0]["product"] == "Brufen 400mg"
    assert results[1].breakdown[0]["margin_pct"] == 50.0
    assert results[0].breakdown[1]["stock_units"] is None  # missing inventory is not treated as zero stock
    # The fixture has no products meeting these thresholds. Return a computed
    # zero rather than a fabricated product or an ambiguous unavailable answer.
    assert results[2].is_available and results[2].value == 0
    assert results[3].is_available and results[3].value == 0
    assert results[4].name == "Near-expiry stock actions"
    assert results[4].breakdown[0]["product"] == "Brufen 400mg"
    assert results[5].name == "Products to prioritize for profitable sales"
    assert results[5].breakdown[0]["product"] == "Brufen 400mg"
    assert results[6].is_available and results[6].value == 0
    assert results[7].is_available and results[7].value == 0
    assert results[8].name == "Sales and inventory actions"
    assert results[9].name == "Priority pharmacy risks and actions"


def test_pos_table_headers_map_to_stock_time_and_status_fields():
    mapped = map_headers(
        ["StockUnits", "CurrentStockUnits", "MinStockLevel", "TimeOfSale", "IsCancelled"],
        get_domain_pack("pharmacy"),
    )
    assert mapped == {
        "StockUnits": "quantity",
        "CurrentStockUnits": "quantity",
        "MinStockLevel": "reorder_level",
        "TimeOfSale": "time_of_day",
        "IsCancelled": "is_cancelled",
    }


def _pos_frame():
    return pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "date": "2026-09-28",
         "invoice_id": "S-1", "product_id": "Brufen 400mg", "quantity": 2,
         "amount": 200.0, "unit_price": 100.0, "cost": 50.0, "generic_name": "ibuprofen"},
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "date": "2026-09-28",
         "invoice_id": "S-1", "product_id": "Panadol 500mg", "quantity": 1,
         "amount": 50.0, "unit_price": 50.0, "cost": 20.0, "generic_name": "paracetamol"},
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "date": "2026-09-27",
         "invoice_id": "S-2", "product_id": "Brufen 400mg", "quantity": 3,
         "amount": 300.0, "unit_price": 100.0, "cost": 50.0, "generic_name": "ibuprofen"},
        {"table_name": "tbl_SalesDetails", "txn_type": "sale", "date": "2026-08-20",
         "invoice_id": "S-3", "product_id": "Brufen 400mg", "quantity": 4,
         "amount": 400.0, "unit_price": 100.0, "cost": 50.0, "generic_name": "ibuprofen"},
        {"table_name": "tbl_PurchaseDetails", "txn_type": "purchase_detail", "date": "2026-09-28",
         "transaction_id": "P-1", "invoice_id": "P-1", "product_id": "Brufen 400mg", "quantity": 20, "amount": 1_000.0,
         "supplier_name": "Acme Pharma"},
        {"table_name": "tbl_PurchaseHeader", "txn_type": "purchase_header", "date": "2026-09-28",
         "transaction_id": "P-1", "invoice_id": "P-1", "net_payable": 1_100.0, "supplier_name": "Acme Pharma"},
        {"table_name": "tbl_PurchaseDetails", "txn_type": "purchase_detail", "date": "2026-08-20",
         "transaction_id": "P-2", "invoice_id": "P-2", "product_id": "Brufen 400mg", "quantity": 30, "amount": 1_200.0,
         "supplier_name": "Acme Pharma"},
        {"table_name": "tbl_PurchaseHeader", "txn_type": "purchase_header", "date": "2026-08-20",
         "transaction_id": "P-2", "invoice_id": "P-2", "net_payable": 1_300.0, "supplier_name": "Acme Pharma"},
        {"table_name": "tbl_Batches", "txn_type": "inventory", "date": "2026-09-28",
         "product_id": "Brufen 400mg", "quantity": 7, "cost": 50.0, "mrp": 100.0,
         "expiry_date": "2026-10-15", "batch_no": "B-1"},
        {"table_name": "tbl_Products", "txn_type": "product_master", "date": "2026-09-28",
         "product_id": "Brufen 400mg", "quantity": 10, "cost": 50.0, "mrp": 100.0},
        {"table_name": "tbl_Suppliers", "supplier_name": "Acme Pharma"},
        {"table_name": "tbl_Suppliers", "supplier_name": "Old Pharma"},
    ])


def _answer(question, filters=None):
    return analyze_pos_question(_pos_frame(), question, filters or KPIFilters())


def test_pos_metrics_use_their_native_row_grain():
    assert _answer("What are my total sales?").value == 550.0
    assert _answer("How many sales invoices were generated?").value == 2
    assert _answer("What were my total purchases this month?", KPIFilters(date_from="2026-09-01", date_to="2026-09-30")).value == 1100.0
    assert _answer("How much stock did I purchase?", KPIFilters(date_from="2026-09-01", date_to="2026-09-30")).value == 20.0
    assert _answer("How many total medicine units do I currently have?").value == 10.0


def test_pos_sales_honor_calendar_month_and_year_filters():
    assert _answer("What were September 2026 sales?", KPIFilters(month=9, year=2026)).value == 550.0
    assert _answer("What were August 2026 sales?", KPIFilters(month=8, year=2026)).value == 400.0


def test_exact_bill_lookup_does_not_fall_through_to_highest_invoice_metric():
    frame = _pos_frame().copy()
    frame.loc[frame["table_name"] == "tbl_SalesDetails", "invoice_id"] = ["B-101", "B-101", "B-102"]
    result = analyze_pos_question(frame, "What medicines were sold in Bill No B-101?", KPIFilters())
    assert result.name == "Invoice details"
    assert result.value == 250.0
    assert {row["product"] for row in result.breakdown} == {"Brufen 400mg", "Panadol 500mg"}


def test_missing_exact_invoice_abstains_instead_of_returning_an_unrelated_total():
    result = _answer("What was the total amount on invoice 999999?")
    assert not result.is_available
    assert "invoice 999999 was not found" in result.reason


def test_unavailable_sales_tax_is_not_fabricated():
    result = _answer("How much GST/tax was collected from sales this month?")
    assert not result.is_available


def test_named_product_and_generic_salt_metrics_are_computed():
    assert _answer("How many units of Brufen 400mg have I sold?").value == 5.0
    result = _answer("Which generic salt sells the most?")
    assert result.is_available
    assert result.value == 5.0


def test_suppliers_without_recent_purchases_uses_supplier_master():
    result = _answer("Which suppliers haven't supplied anything recently?")
    assert result.is_available
    assert result.breakdown == [{"supplier": "Old Pharma"}]


def test_purchase_price_uses_dated_lines_and_recent_purchase_filter():
    recent = _answer("How much stock did I purchase in the last 30 days?",
                     KPIFilters(date_from="2026-08-31", date_to="2026-09-30"))
    assert recent.value == 20.0
    last_paid = _answer("What price did I last pay for Brufen 400mg?")
    assert last_paid.value == 50.0
    increased = _answer("Which products have experienced the largest increase in purchase price?")
    assert increased.is_available
    assert increased.breakdown[0]["product_id"] == "Brufen 400mg"
    assert increased.breakdown[0]["change"] == 10.0
    invoice = _answer("What was my largest purchase invoice this month?",
                      KPIFilters(date_from="2026-09-01", date_to="2026-09-30"))
    assert invoice.name == "Largest purchase invoice"
    assert invoice.value == 1100.0


def test_supplier_specific_queries_filter_supplier_rows():
    amount = _answer("How much have I purchased from Acme Pharma?")
    assert amount.value == 2400.0
    medicines = _answer("Which medicines do I purchase from Acme Pharma?")
    assert medicines.breakdown == [{"product_id": "Brufen 400mg", "quantity": 50.0}]
    last_date = _answer("When was my last purchase from Acme Pharma?")
    assert last_date.value == "2026-09-28"


def test_combined_stock_and_profit_questions_do_not_fall_through_to_sales_only():
    tied_up = _answer("Which high-stock medicines have poor sales and are tying up my cash?")
    assert tied_up.is_available
    assert tied_up.breakdown[0]["product_id"] == "Brufen 400mg"

    low_margin = _answer("Which products have high sales but low margins?")
    assert low_margin.is_available
    assert low_margin.breakdown[0]["product_id"] == "Brufen 400mg"

    decision = _answer("Based on current stock, recent sales, expiry dates, and purchasing history, which medicines should I restock, avoid purchasing, or prioritize selling?")
    assert decision.is_available
    assert decision.breakdown[0]["action"] == "Prioritize selling"


def test_low_stock_uses_sales_velocity_when_reorder_points_are_missing():
    frame = _pos_frame()
    frame.loc[frame.txn_type == "product_master", "quantity"] = 1
    result = analyze_pos_question(frame, "Which medicines are low in stock?", KPIFilters())
    assert result.is_available
    assert result.breakdown[0]["product_id"] == "Brufen 400mg"


def test_database_assembly_fills_empty_purchase_detail_header_fields():
    raw = pd.DataFrame([
        {"table_name": "tbl_PurchaseDetails", "transaction_id": "P-1", "date": None,
         "supplier_name": None, "invoice_id": None, "product_id": "Brufen 400mg",
         "quantity": 2, "amount": 100.0},
        {"table_name": "tbl_PurchaseHeader", "transaction_id": "P-1", "date": "2026-09-28",
         "supplier_name": "Acme Pharma", "invoice_id": "INV-1", "net_payable": 110.0},
        {"table_name": "tbl_SalesDetails", "invoice_id": "S-1", "date": None,
         "product_id": "Brufen 400mg", "quantity": 1, "amount": 100.0},
        {"table_name": "tbl_SalesHeader", "invoice_id": "S-1", "date": "2026-09-28",
         "payment_method": "Cash"},
        {"table_name": "tbl_Batches", "product_id": "Brufen 400mg", "quantity": 3},
    ])
    assembled = _build_canonical_database(raw)
    purchase = assembled.loc[assembled.txn_type == "purchase_detail"].iloc[0]
    sale = assembled.loc[assembled.txn_type == "sale"].iloc[0]
    assert str(purchase.date)[:10] == "2026-09-28"
    assert purchase.supplier_name == "Acme Pharma"
    assert purchase.invoice_id == "INV-1"
    assert sale.date == "2026-09-28"
    assert sale.payment_method == "Cash"
