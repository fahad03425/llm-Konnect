import pandas as pd
import json
from unittest.mock import patch

from app.analytics.tabular_query import answer_tabular_question, is_cross_table_pharmacy_risk_question
from app.ingestion.store import KnowledgeBase
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest
from app.rag.router import classify_route


def sales_ledger():
    return pd.DataFrame([
        {"invoice_id": "400005", "date": "2026-09-01", "product_id": "Alpha 10mg tablet", "quantity": 2, "amount": 20.0, "discount": 1.0, "source_row": 2},
        {"invoice_id": "400005", "date": "2026-09-01", "product_id": "Beta 5mg tablet", "quantity": 1, "amount": 10.0, "discount": 0.0, "source_row": 3},
        {"invoice_id": "400006", "date": "2026-09-02", "product_id": "Alpha 10mg tablet", "quantity": 3, "amount": 30.0, "discount": 2.0, "source_row": 4},
    ])


def test_sales_ledger_returns_explicit_abstention_for_missing_inventory_evidence():
    frame = sales_ledger()
    for question, required in (
        ("What stock is expired and what is its value?", "expiry_date"),
        ("Show all batches of Alpha tablet.", "batch_no"),
        ("What is the current stock of Alpha 10mg tablet?", "inventory"),
        ("How much cash versus credit sales did we make?", "payment_method"),
        ("What was the gross profit margin?", "cost"),
        ("Which supplier had the most purchases?", "purchases"),
    ):
        result = answer_tabular_question(question, frame)
        assert result is not None, question
        assert result["values"]["status"] in {"unsupported_field", "unsupported_record_type"}, question
        assert required in str(result["values"]), question
        assert result["source_rows"] == [], question
        assert "Units Sold" not in result["answer"], question


def test_invoice_grain_ledger_answers_total_units_and_top_products():
    frame = sales_ledger()
    total = answer_tabular_question("How many total units were sold across all products?", frame)
    assert total is not None
    assert total["values"].get("value") == 6


def test_bill_number_lookup_matches_exact_invoice_id():
    frame = sales_ledger()
    result = answer_tabular_question("Which medicines were dispensed in bill number 400005?", frame)
    assert result is not None
    assert result["values"].get("lookup_id") == "400005"
    assert set(result["source_rows"]) == {2, 3}
    assert "Alpha 10mg tablet" in result["answer"]
    assert "Beta 5mg tablet" in result["answer"]
    roman_urdu = answer_tabular_question("Bill number 400005 mein customer ko kaun kaun si dawaiyan di gayi theen?", sales_ledger())
    assert set(roman_urdu["source_rows"]) == {2, 3}
    assert "Alpha 10mg tablet" in roman_urdu["answer"]
    assert "Beta 5mg tablet" in roman_urdu["answer"]


def test_selected_chroma_source_returns_all_encrypted_structured_rows(tmp_path):
    kb = KnowledgeBase(chroma_dir=str(tmp_path), collection_name="full_record_test")
    source = pd.DataFrame([
        {"source_row": 8, "table_name": "tbl_Batches", "product_id": "Panadol 500 mg", "generic_name": "paracetamol", "batch_no": "PN-42", "stock_qty": 24, "rack_location": "A3", "customer_id": "PATIENT-1"},
        {"source_row": 9, "table_name": "tbl_Batches", "product_id": "Panadol Extra", "generic_name": "paracetamol and caffeine", "batch_no": "PX-09", "stock_qty": 3, "rack_location": "B1", "customer_id": "PATIENT-2"},
    ])
    kb.add_dataframe(source, {"source_file": "inventory.csv", "source_connector": "test"}, domain="pharmacy", file_id="inventory-test")

    loaded = kb.get_dataframe(file_ids=["inventory-test"])
    assert len(loaded) == 2
    assert set(loaded["batch_no"]) == {"PN-42", "PX-09"}
    assert set(loaded["rack_location"]) == {"A3", "B1"}
    assert set(loaded["customer_id"]) == {"PATIENT-1", "PATIENT-2"}


def test_named_customer_history_does_not_fall_back_to_whole_file_sales():
    frame = pd.DataFrame([
        {"source_row": 20, "table_name": "tbl_SalesDetails", "customer_id": "PATIENT-1", "date": "2026-09-01", "invoice_id": "INV-1", "product_id": "Panadol"},
        {"source_row": 21, "table_name": "tbl_SalesDetails", "customer_id": "PATIENT-2", "date": "2026-09-02", "invoice_id": "INV-2", "product_id": "Brufen"},
    ])
    result = answer_tabular_question("What did customer Umar Mirza purchase last time?", frame)
    assert result["values"]["status"] == "missing_identity_mapping"
    assert "customer IDs" in result["answer"]
    assert result["source_rows"] == []


def cross_table_pharmacy_data():
    today = pd.Timestamp.today().normalize()
    return pd.DataFrame([
        {"table_name": "tbl_SalesDetails", "source_file": "sales", "source_row": 1, "txn_type": "sale", "invoice_id": "S-1", "date": today - pd.Timedelta(days=8), "product_id": "Alpha 10mg", "quantity": 50, "amount": 5000, "unit_price": 100, "cost": 80, "line_cost": 3000},
        {"table_name": "tbl_SalesDetails", "source_file": "sales", "source_row": 2, "txn_type": "sale", "invoice_id": "S-2", "date": today, "product_id": "Alpha 10mg", "quantity": 40, "amount": 4000, "unit_price": 100, "cost": 80, "line_cost": 2400},
        {"table_name": "tbl_SalesDetails", "source_file": "sales", "source_row": 3, "txn_type": "sale", "invoice_id": "S-3", "date": today, "product_id": "Beta 20mg", "quantity": 2, "amount": 400, "unit_price": 200, "cost": 120, "line_cost": 240},
        {"table_name": "tbl_Batches", "source_file": "inventory", "source_row": 11, "txn_type": "inventory", "product_id": "Alpha 10mg", "quantity": 5, "stock_qty": 5, "reorder_level": 20, "expiry_date": today + pd.Timedelta(days=50), "batch_no": "A-1"},
        {"table_name": "tbl_Batches", "source_file": "inventory", "source_row": 12, "txn_type": "inventory", "product_id": "Beta 20mg", "quantity": 30, "stock_qty": 30, "reorder_level": 8, "expiry_date": today + pd.Timedelta(days=20), "batch_no": "B-1"},
    ])


def test_hybrid_reorder_priority_joins_sales_velocity_to_current_stock():
    question = "Which medicines are selling quickly, are currently below their reorder level, and should be reordered first?"
    result = answer_tabular_question(question, cross_table_pharmacy_data())
    assert result["values"]["status"] == "ok"
    assert result["values"]["products"][0]["product"] == "Alpha 10mg"
    assert result["values"]["products"][0]["daily_sales_velocity"] == 10
    assert result["values"]["products"][0]["days_of_supply"] == 0.5
    assert "| Rank | Medicine | Stock / reorder level | Sold per day | Stock cover |" in result["answer"]
    assert "| 1 | Alpha 10mg | 5 / 20 | 10 | 0.5 days |" in result["answer"]
    assert ("sales", 1) in result["source_rows"] and ("inventory", 11) in result["source_rows"]


def test_hybrid_reorder_uses_requested_date_window_for_velocity_and_sales_rows():
    frame = cross_table_pharmacy_data()
    today = pd.Timestamp.today().normalize()
    older_sale = frame.iloc[0].copy()
    older_sale["source_row"] = 0
    older_sale["invoice_id"] = "S-OLD"
    older_sale["date"] = today - pd.Timedelta(days=40)
    older_sale["quantity"] = 100
    frame = pd.concat([frame, pd.DataFrame([older_sale])], ignore_index=True)
    question = "Which medicines are selling quickly, are currently below their reorder level, and should be reordered first? last 30 days"
    result = answer_tabular_question(question, frame, filters={
        "date_from": (today - pd.Timedelta(days=29)).date().isoformat(),
        "date_to": today.date().isoformat(),
    })
    alpha = result["values"]["products"][0]
    assert result["values"]["sales_window_days"] == 30
    assert result["values"]["sales_date_from"] == (today - pd.Timedelta(days=29)).date().isoformat()
    assert alpha["units_sold"] == 90
    assert alpha["daily_sales_velocity"] == 3
    assert ("sales", 0) not in result["source_rows"]
    assert ("sales", 1) in result["source_rows"]
    assert "sales window: 30 days" in result["answer"]


def test_hybrid_expiry_clearance_uses_sales_velocity_and_batch_stock():
    question = "Which medicines will expire in the next 60 days and are selling too slowly to clear the remaining stock?"
    result = answer_tabular_question(question, cross_table_pharmacy_data())
    assert result["values"]["horizon_days"] == 60
    assert [row["product"] for row in result["values"]["batches"]] == ["Beta 20mg"]
    assert result["values"]["batches"][0]["estimated_units_at_expiry"] > 0
    assert "| Medicine | Batch | Expires | Stock | Sold per day | Estimated left at expiry |" in result["answer"]
    assert ("sales", 3) in result["source_rows"] and ("inventory", 12) in result["source_rows"]


def test_hybrid_profit_and_stockout_risk_uses_line_costs_and_cites_both_sources():
    question = "Which products are my most profitable but are at risk of going out of stock?"
    result = answer_tabular_question(question, cross_table_pharmacy_data())
    assert result["values"]["products"][0]["product"] == "Alpha 10mg"
    assert result["values"]["products"][0]["gross_profit"] == 3600
    assert "| Product | Gross profit | Stock / reorder level | Sold per day | Stock cover |" in result["answer"]
    assert ("sales", 1) in result["source_rows"] and ("inventory", 11) in result["source_rows"]


def test_hybrid_analysis_abstains_without_an_exact_cross_table_product_key():
    frame = cross_table_pharmacy_data()
    frame.loc[frame.table_name.eq("tbl_Batches"), "product_id"] = ["Unrelated-1", "Unrelated-2"]
    result = answer_tabular_question(
        "Which medicines are selling quickly and below their reorder level?", frame
    )
    assert result["values"]["status"] == "missing_join_key"
    assert result["source_rows"] == []


def test_multi_source_chat_uses_hybrid_analytics_and_returns_combined_result():
    chat = RAGChat()
    records = cross_table_pharmacy_data().rename(columns={
        "stock_qty": "_extra.CurrentStock",
        "reorder_level": "_extra.MinimumStockLevel",
    })
    older_sale = records.iloc[0].copy()
    older_sale["source_row"] = 0
    older_sale["invoice_id"] = "S-OLD"
    older_sale["date"] = pd.Timestamp.today().normalize() - pd.Timedelta(days=40)
    records = pd.concat([records, pd.DataFrame([older_sale])], ignore_index=True)
    with patch("app.rag.chat.session_manager") as sessions, \
         patch.object(chat, "_get_records_for_analytics", return_value=(records, [])):
        events = [json.loads(line) for line in chat.ask_stream(ChatRequest(
            question="Which products are my most profitable but are at risk of going out of stock? last 30 days",
            session_id="hybrid-multi-source", domain="pharmacy", file_ids=["inventory", "sales"],
        ))]
    assert events[0]["route"] == "analytics"
    assert "Alpha 10mg" in events[0]["chunk"]
    assert events[0]["computed_values"]["tabular_result"]["value"]["products"][0]["gross_profit"] == 3600
    assert events[0]["computed_values"]["tabular_result"]["value"]["sales_window_days"] == 30
    assert {source["source_file"] for source in events[0]["sources"]} == {"sales", "inventory"}
    assert sessions.append_turn.call_count == 2


def inventory_demand_dataset():
    today = pd.Timestamp.today().normalize()
    rows = []
    products = [
        ("FastBelow", 300, 5, 20),
        ("FewDays", 30, 4, 0),
        ("Runout", 100, 3, 10),
        ("ZeroDemand", 90, 0, 10),
        ("Overstock", 10, 900, 30),
        ("NoRecentSales", None, 700, 20),
        ("IncreaseQty", 300, 50, 30),
    ]
    source_row = 1
    for product, sold, stock, reorder in products:
        if sold is not None:
            rows.append({"table_name": "tbl_SalesDetails", "source_file": "sales", "source_row": source_row,
                         "txn_type": "sale", "invoice_id": f"S-{source_row}", "date": today,
                         "product_id": product, "quantity": sold, "amount": sold * 10,
                         "unit_price": 10, "cost": 5, "line_cost": sold * 5})
            source_row += 1
        rows.append({"table_name": "tbl_Batches", "source_file": "inventory", "source_row": 100 + source_row,
                     "txn_type": "inventory", "product_id": product, "quantity": stock,
                     "stock_qty": stock, "reorder_level": reorder, "expiry_date": today + pd.Timedelta(days=180)})
    return pd.DataFrame(rows)


def test_ten_inventory_demand_questions_route_to_supported_hybrid_analysis():
    today = pd.Timestamp.today().normalize()
    filters = {"date_from": (today - pd.Timedelta(days=29)).date().isoformat(),
               "date_to": today.date().isoformat()}
    questions = [
        ("Which medicines are selling quickly and are currently below their reorder level?", "reorder_priority"),
        ("Which medicines are likely to run out soon based on their recent sales rate?", "runout_risk"),
        ("Which medicines are selling fast and may go out of stock soon?", "runout_risk"),
        ("Which products should I reorder first based on sales velocity and current stock?", "reorder_priority"),
        ("Which products have enough stock for only a few more days at the current sales rate?", "runout_risk"),
        ("Which high-demand medicines are currently out of stock?", "high_demand_out_of_stock"),
        ("Which products are overstocked compared with their actual sales?", "overstock"),
        ("Which products have high inventory but very low sales?", "overstock"),
        ("Which products have not sold recently but are still occupying significant inventory?", "no_recent_sales"),
        ("Which medicines am I repeatedly running low on because of high demand?", "recurring_low_stock"),
        ("Which products should I increase the reorder quantity for based on historical demand?", "reorder_quantity"),
    ]
    frame = inventory_demand_dataset()
    for question, intent in questions:
        assert classify_route(question) == "analytics", question
        assert is_cross_table_pharmacy_risk_question(question), question
        result = answer_tabular_question(question, frame, filters=filters)
        assert result["values"].get("intent", intent) == intent, (question, result)
        assert result["values"]["status"] in {"ok", "insufficient_history"}, (question, result)

    paraphrases = [
        "What should I restock first?",
        "Show products with less than a week's supply left.",
        "Which items have stock but no sales in the last month?",
        "Which products might run out of stock soon based on recent sales?",
        "What medicines are selling fast despite being below minimum stock?",
        "What items need a larger reorder amount based on how much we sell?",
    ]
    for question in paraphrases:
        assert classify_route(question) == "analytics", question
        assert is_cross_table_pharmacy_risk_question(question), question
        assert answer_tabular_question(question, frame, filters=filters)["values"]["status"] in {"ok", "insufficient_history"}


def test_inventory_demand_intents_return_period_filtered_evidence_and_clear_assumptions():
    today = pd.Timestamp.today().normalize()
    frame = inventory_demand_dataset()
    filters = {"date_from": (today - pd.Timedelta(days=29)).date().isoformat(),
               "date_to": today.date().isoformat()}
    assert answer_tabular_question("Which high-demand medicines are currently out of stock?", frame, filters)["values"]["products"][0]["product"] == "ZeroDemand"
    assert {p["product"] for p in answer_tabular_question("Which products are overstocked compared with actual sales?", frame, filters)["values"]["products"]} == {"Overstock", "NoRecentSales"}
    no_sales = answer_tabular_question("Which products have not sold recently but occupy inventory?", frame, filters)
    assert [p["product"] for p in no_sales["values"]["products"]] == ["NoRecentSales"]
    few_days = answer_tabular_question("Which products have enough stock for only a few more days at current sales?", frame, filters)
    assert "FewDays" in {p["product"] for p in few_days["values"]["products"]}
    increase = answer_tabular_question("Which products should I increase the reorder quantity for based on historical demand?", frame, filters)
    assert increase["values"]["replenishment_target_days"] == 30
    assert next(p for p in increase["values"]["products"] if p["product"] == "IncreaseQty")["suggested_replenishment_units"] == 250
    recurring = answer_tabular_question("Which medicines am I repeatedly running low on because of high demand?", frame, filters)
    assert recurring["values"]["status"] == "insufficient_history"


def test_future_stockout_wording_is_not_misread_as_current_zero_stock():
    frame = inventory_demand_dataset()
    inventory_rows = frame.table_name.eq("tbl_Batches")
    frame.loc[inventory_rows, "stock_qty"] = 10000
    frame.loc[inventory_rows, "quantity"] = 10000
    frame.loc[inventory_rows, "reorder_level"] = 0
    question = "Which medicines are selling fast and may go out of stock soon?"
    result = answer_tabular_question(question, frame)
    assert result["values"]["intent"] == "runout_risk"
    assert "Products at risk of running out" in result["answer"]
    assert "zero current stock" not in result["answer"]
    assert result["values"]["closest_products"]


def test_low_remaining_stock_compared_with_sales_rate_is_not_an_out_of_stock_query():
    frame = inventory_demand_dataset()
    question = "Which medicines have low remaining stock compared with how quickly they are selling?"
    contextual_question = (
        "Relevant prior conditions: high demand and out of stock soon. "
        "Current follow-up: " + question
    )
    result = answer_tabular_question(contextual_question, frame)
    assert is_cross_table_pharmacy_risk_question(contextual_question)
    assert result["values"]["intent"] == "runout_risk"
    assert "Products at risk of running out" in result["answer"]
    assert "High-demand products currently out of stock" not in result["answer"]


def test_cross_pos_stock_and_reorder_aliases_work_without_vendor_specific_mapping():
    frame = cross_table_pharmacy_data().rename(columns={
        "stock_qty": "_extra.CurrentStock",
        "reorder_level": "_extra.MinimumStockLevel",
    })
    question = "Which medicines are selling quickly and are currently below their reorder level?"
    result = answer_tabular_question(question, frame)
    assert result["values"]["reorder_threshold_available"] is True
    assert result["values"]["products"][0]["product"] == "Alpha 10mg"


def test_reorder_question_uses_transparent_stock_cover_proxy_when_policy_field_is_absent():
    frame = cross_table_pharmacy_data().drop(columns=["reorder_level"])
    question = "Which medicines are selling quickly and are currently below their reorder level?"
    result = answer_tabular_question(question, frame)
    assert result["values"]["reorder_threshold_available"] is False
    assert result["values"]["proxy_days"] == 30
    assert result["values"]["products"][0]["product"] == "Alpha 10mg"
    assert "shown as a proxy" in result["answer"]
