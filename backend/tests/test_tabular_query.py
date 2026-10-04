import pandas as pd

from app.analytics.tabular_query import _current_pharmacy_question, answer_cross_dataset_question, answer_tabular_question, is_distinct_entity_count_question, is_ranked_record_count_question


def test_named_entity_filters_use_all_rows_and_keep_citations():
    frame = pd.DataFrame({
        "invoice_id": ["SALE-1", "SALE-2", "SALE-3"],
        "date": ["2026-03-01", "2026-03-02", "2026-04-01"],
        "product_id": ["Drug A", "Drug A", "Drug B"],
        "branch": ["North Pharmacy", "North Pharmacy", "South Pharmacy"],
        "quantity": [2, 5, 9], "amount": [20, 50, 90], "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "total quantity and total sales amount of Drug A at North Pharmacy in March 2026", frame
    )
    assert result["values"]["total_quantity"] == 7
    assert result["values"]["total_amount"] == 70
    assert result["source_rows"] == [1, 2]


def test_threshold_predicate_filters_before_count():
    frame = pd.DataFrame({"invoice_id": ["S1", "S2", "S3"], "discount": [2249, 2250, 2400], "source_row": [1, 2, 3]})
    result = answer_tabular_question("Which sales had a discount of 2250 PKR or more?", frame)
    assert result["values"]["matched_records"] == 2
    assert result["source_rows"] == [2, 3]


def test_named_missing_customer_does_not_return_whole_file_total():
    frame = pd.DataFrame({"customer_id": ["Mariam Sheikh"], "amount": [100], "source_row": [1]})
    result = answer_tabular_question("What did customer Sameer Lakhani purchase and spend?", frame)
    assert result["values"]["matched_records"] == 0
    assert "No records" in result["answer"]


def test_two_location_comparison_uses_same_measure():
    frame = pd.DataFrame({"branch": ["North Pharmacy", "North Pharmacy", "South Pharmacy"], "amount": [10, 15, 40], "source_row": [1, 2, 3]})
    result = answer_tabular_question("Which branch had the greater total sales amount: North Pharmacy or South Pharmacy?", frame)
    assert result["values"]["comparison"] == {"North Pharmacy": 25, "South Pharmacy": 40}


def test_total_stock_question_sums_quantity_and_preserves_row_count():
    frame = pd.DataFrame({"product_id": ["Drug A", "Drug A"], "warehouse": ["North", "North"], "quantity": [300, 545], "source_row": [4, 5]})
    result = answer_tabular_question("What is total stock of Drug A at North warehouse?", frame)
    assert result["values"]["total_quantity"] == 845
    assert "Across 2 records" in result["answer"]


def test_inventory_reorder_comparison_and_percent_do_not_confuse_stock_with_sold_quantity():
    frame = pd.DataFrame({
        "product_code": ["I1", "I2", "I3"], "product_id": ["A", "B", "C"],
        "stock_qty": [5, 10, 20], "reorder_level": [5, 12, 15],
        "category": ["Women’s Health"] * 3, "warehouse": ["North"] * 3,
        "source_row": [1, 2, 3],
    })
    exact = answer_tabular_question("Which Women’s Health items have Stock exactly equal to Reorder_Level?", frame)
    pct = answer_tabular_question("What percentage of inventory records are below their reorder level?", frame)
    assert exact["values"]["matching_records"] == 1
    assert exact["source_rows"] == [1]
    assert pct["values"]["below_reorder"] == 1
    assert pct["values"]["total_records"] == 3


def test_purchase_status_word_in_cost_question_does_not_filter_ledger():
    frame = pd.DataFrame({
        "purchase_order_no": ["P1", "P2", "P3"], "supplier_name": ["Orion Medical Supplies"] * 3,
        "product_id": ["Cold Tablets"] * 3, "quantity": [100, 200, 300],
        "cost": [80, 90, 100], "invoice_total": [8000, 18000, 30000],
        "status": ["Paid", "Pending", "Partially Paid"], "source_row": [1, 2, 3],
    })
    result = answer_tabular_question("What was the average unit cost paid for Cold Tablets to Orion Medical Supplies?", frame)
    assert result["values"]["purchase_count"] == 3
    assert result["values"]["average_unit_cost"] == 90


def test_supplier_comparison_preserves_product_scope():
    frame = pd.DataFrame({
        "purchase_order_no": ["P1", "P2", "P3", "P4"],
        "supplier_name": ["Orion Medical Supplies", "Orion Medical Supplies", "Kohsar Pharma Link", "Kohsar Pharma Link"],
        "product_id": ["Requested", "Other", "Requested", "Other"],
        "quantity": [1, 100, 1, 100], "cost": [10, 100, 8, 90],
        "invoice_total": [10, 10000, 8, 9000], "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question("Compare the average unit cost of Requested between Orion Medical Supplies and Kohsar Pharma Link.", frame)
    comparison = result["values"]["comparison"]
    assert comparison["Orion Medical Supplies"]["average_unit_cost"] == 10
    assert comparison["Kohsar Pharma Link"]["average_unit_cost"] == 8


def test_inventory_combined_predicates_do_not_drop_a_filter_when_intersection_is_empty():
    frame = pd.DataFrame({
        "product_code": ["I1", "I2"], "product_id": ["A", "B"],
        "category": ["Anti-inflammatory", "Oral Care"],
        "supplier_name": ["Other Supplier", "Indus Medico Traders"],
        "stock_qty": [5, 8], "reorder_level": [10, 10],
        "expiry_date": ["2027-01-01", "2027-01-01"], "warehouse": ["North", "North"],
        "source_row": [1, 2],
    })
    result = answer_tabular_question("Which Anti-inflammatory items are supplied by Indus Medico Traders?", frame)
    assert result["values"]["matched_records"] == 0
    assert "No matching" in result["answer"]


def test_missing_supplier_never_falls_through_to_whole_purchase_total():
    frame = pd.DataFrame({
        "purchase_order_no": ["P1"], "supplier_name": ["Noor Drug House"],
        "product_id": ["Nebulizer Machine Compact"], "quantity": [2],
        "cost": [100], "invoice_total": [200], "source_row": [1],
    })
    result = answer_tabular_question("What is the total purchase value from Al-Noor Medical Wholesale?", frame)
    assert result["values"]["matched_records"] == 0
    assert "No matching" in result["answer"]


def test_purchase_totals_by_supplier_use_header_grain_and_route_past_inventory():
    frame = pd.DataFrame({
        "supplier_id": ["11", "11", "13"],
        "invoice_total": [100.0, 100.0, 250.0],
        "_extra.ID": [1, 1, 2],
        "_header_source_file": ["sql://inventory/tbl_14"] * 3,
        "_header_source_row": [4, 4, 9],
        "source_file": ["sql://inventory/tbl_15"] * 3,
        "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "Can you show the purchase totals by supplier from this Asan Pos scope?", frame
    )
    assert result["values"]["total_purchase_value"] == 350.0
    assert result["values"]["purchase_header_count"] == 2
    assert result["values"]["by_supplier"] == {
        "13": {"purchase_headers": 1, "purchase_value": 250.0},
        "11": {"purchase_headers": 1, "purchase_value": 100.0},
    }
    assert result["source_rows"] == [
        ("sql://inventory/tbl_14", 4), ("sql://inventory/tbl_14", 9)
    ]


def test_explicit_table_record_count_cites_only_named_table_rows():
    frame = pd.DataFrame({
        "table_name": ["tbl_15", "tbl_15", "tbl_22"],
        "source_file": ["sql://inventory/tbl_15", "sql://inventory/tbl_15", "sql://sales/tbl_22"],
        "source_row": [1, 2, 1],
        "purchase_order_no": ["1", "1", None],
        "transaction_id": [None, None, "R1"],
    })
    result = answer_cross_dataset_question(
        "How many purchase detail records does tbl_15 contain in this Asan Pos scope?", frame
    )
    assert result["values"] == {"table": "tbl_15", "record_count": 2}
    assert result["source_rows"] == [
        ("sql://inventory/tbl_15", 1), ("sql://inventory/tbl_15", 2)
    ]


def test_purchase_totals_filter_mixed_scope_and_cite_purchase_headers():
    frame = pd.DataFrame({
        "txn_type": ["sale", "expense", "expense"],
        "date": ["2026-09-01", "2026-09-09", "2026-09-15"],
        "purchase_order_no": [None, "P1", "P2"],
        "invoice_total": [9999.0, 100.0, 200.0],
        "quantity": [1, 2, 3], "source_row": [1, 1, 2],
        "source_file": ["sql://sales/tbl_21", "sql://inventory/tbl_15", "sql://inventory/tbl_15"],
        "_header_source_file": [None, "sql://inventory/tbl_14", "sql://inventory/tbl_14"],
        "_header_source_row": [None, 7, 8],
    })
    result = answer_tabular_question("What was the total purchase value in September 2026?", frame)
    assert result["values"]["total_purchase_value"] == 300.0
    assert result["values"]["purchase_count"] == 2
    assert result["source_rows"] == [
        ("sql://inventory/tbl_14", 7), ("sql://inventory/tbl_14", 8)
    ]


def test_sales_total_for_invoices_uses_header_totals_and_header_citations():
    frame = pd.DataFrame({
        "txn_type": ["sale"] * 3,
        "transaction_id": [1, 1, 2], "invoice_id": ["CASH-1", "CASH-1", "CARD-1"],
        "payment_method": ["Cash", "Cash", "Card"],
        "amount": [45.0, 55.0, 200.0], "invoice_total": [100.0, None, 200.0],
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [1, 2, 3],
        "_header_source_file": ["sql://sales/tbl_21"] * 3,
        "_header_source_row": [10, 10, 11],
    })
    result = answer_tabular_question("What total sales amount was recorded for Cash invoices?", frame)
    assert result["values"]["total_sales"] == 100.0
    assert result["values"]["invoice_count"] == 1
    assert result["source_rows"] == [("sql://sales/tbl_21", 10)]


def test_exact_prefixed_batch_lookup_precedes_cross_table_risk_planner(monkeypatch):
    import app.analytics.tabular_query as tabular_query

    question = (
        "A batch labeled PO-BATCH-35-0 is on the inventory side. "
        "How many units are recorded for it, and what expiry date is shown?"
    )
    frame = pd.DataFrame([{
        "batch_no": "PO-BATCH-35-0",
        "product_id": "16",
        "quantity": 20.0,
        "expiry_date": "2028-06-30",
        "source_row": 156,
        "table_name": "tbl_15",
        "source_file": "sql://inventory/tbl_15",
        "file_id": "db_inventory_tbl_15",
    }])
    monkeypatch.setattr(
        tabular_query,
        "_answer_cross_table_pharmacy_risk",
        lambda *_args, **_kwargs: {
            "answer": "unrelated broad expiry report",
            "values": {"status": "ok"},
            "source_rows": [],
        },
    )

    result = tabular_query.answer_tabular_question(question, frame)

    assert result["values"]["lookup_id"] == "PO-BATCH-35-0"
    assert "Quantity: 20.0" in result["answer"]
    assert "Expiry Date: 2028-06-30" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_15", 156)]


def test_exact_record_followup_reports_units_above_reorder_level():
    frame = pd.DataFrame([{
        "batch_no": "PO-BATCH-63-3", "quantity": 20.0,
        "reorder_level": 10.0, "source_row": 286,
        "table_name": "tbl_15", "source_file": "sql://inventory/tbl_15",
    }])
    result = answer_tabular_question(
        "And how many units over its reorder level is that? PO-BATCH-63-3", frame
    )
    assert result["values"]["reorder_difference"] == 10
    assert "10 units above reorder level" in result["answer"]


def test_exact_batch_lookup_includes_requested_unit_price():
    frame = pd.DataFrame([{
        "batch_no": "PO-BATCH-12-4", "quantity": 78.0,
        "expiry_date": "2028-06-30", "unit_price": 491.38,
        "source_row": 55, "table_name": "tbl_15",
    }])
    result = answer_tabular_question(
        "whts the expiry and unit price for batch PO-BATCH-12-4?", frame
    )
    assert "Expiry Date: 2028-06-30" in result["answer"]
    assert "Unit Price: 491.38" in result["answer"]


def test_purchase_batch_quantity_is_not_compared_to_reorder_stock_and_citation_is_table_specific():
    frame = pd.DataFrame([
        {
            "batch_no": "PO-BATCH-49-1", "purchase_order_no": "49",
            "product_id": "76", "quantity": 76.0, "reorder_level": 10.0,
            "expiry_date": "2028-06-30", "unit_price": 337.67,
            "source_row": 220, "table_name": "tbl_15",
            "source_file": "sql://inventory/tbl_15",
        },
        {
            "batch_no": "OTHER", "quantity": 1.0, "source_row": 220,
            "table_name": "tbl_22", "source_file": "sql://sales/tbl_22",
        },
        {
            "batch_no": "OTHER", "quantity": 1.0, "source_row": 220,
            "table_name": "tbl_10", "source_file": "sql://inventory/tbl_10",
        },
    ])
    result = answer_tabular_question("How many units are recorded for batch PO-BATCH-49-1?", frame)

    assert "Quantity: 76.0" in result["answer"]
    assert "Reorder comparison" not in result["answer"]
    assert "reorder_difference" not in result["values"]
    assert result["source_rows"] == [("sql://inventory/tbl_15", 220)]


def test_rec_prefixed_invoice_discount_lookup_is_limited_to_that_invoice():
    frame = pd.DataFrame({
        "invoice_id": [None, "REC-00014", "REC-00014", "REC-00015"],
        "reference_number": ["REC-00014", None, None, None],
        "invoice_discount": [120.0, None, None, 35.0],
        "discount": [None, 60.0, 60.0, None],
        "amount": [1080.0, 540.0, 540.0, 900.0],
        "source_row": [14, 43, 44, 15],
        "table_name": ["tbl_21", "tbl_22", "tbl_22", "tbl_21"],
        "source_file": ["sql://sales/tbl_21", "sql://sales/tbl_22", "sql://sales/tbl_22", "sql://sales/tbl_21"],
    })
    result = answer_tabular_question(
        "Was invoice REC-00014 discounted, and by how much?", frame
    )
    assert result["values"]["lookup_id"] == "REC-00014"
    assert "total recorded discount 120.00" in result["answer"]
    assert result["values"]["invoice_discount"] == 120
    assert result["source_rows"] == [14]


def test_rec_prefixed_invoice_discount_sums_matching_line_discounts():
    frame = pd.DataFrame({
        "invoice_id": ["REC-00014", "REC-00014", "REC-00015"],
        "discount": [60.0, 60.0, 35.0],
        "source_row": [43, 44, 45], "table_name": ["tbl_22"] * 3,
    })
    result = answer_tabular_question(
        "Was invoice REC-00014 discounted, and by how much?", frame
    )
    assert result["values"]["total_discount"] == 120
    assert result["source_rows"] == [43, 44]


def test_inventory_report_uses_inventory_database_group_and_table_row_citations():
    frame = pd.DataFrame({
        "database_name": ["inventory", "inventory", "sales"],
        "table_name": ["tbl_10", "tbl_15", "tbl_22"],
        "quantity": [5, 20, 1], "reorder_level": [10, 10, 0],
        "source_row": [1, 1, 1],
        "source_file": ["sql://inventory/tbl_10", "sql://inventory/tbl_15", "sql://sales/tbl_22"],
    })
    result = answer_tabular_question(
        "Across inventory, what share of records are under the recorded reorder level?", frame
    )
    assert result["values"]["below_reorder"] == 1
    assert result["values"]["total_records"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1)]


def test_explicit_expiry_cutoff_count_uses_expiry_rows_not_sales_velocity():
    frame = pd.DataFrame({
        "database_name": ["inventory"] * 3,
        "table_name": ["tbl_10"] * 3,
        "batch_no": ["L1", "L2", "L3"],
        "expiry_date": ["2026-12-31", "2027-01-01", "2027-02-01"],
        "quantity": [5, 10, 2], "reorder_level": [1, 1, 1],
        "product_id": ["2", "1", "3"],
        "source_row": [1, 2, 3], "source_file": ["sql://inventory/tbl_10"] * 3,
    })
    result = answer_tabular_question(
        "How many inventory batches expire before 1 January 2027?", frame
    )
    assert result["values"]["matching_records"] == 1
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1)]


def test_how_many_units_did_we_sell_in_a_month_uses_selling_verb():
    frame = pd.DataFrame({
        "invoice_id": ["R1", "R1", "R2"],
        "quantity": [2, 5, 100], "amount": [20, 50, 1000],
        "date": ["2026-09-01", "2026-09-30", "2026-08-31"],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_row": [1, 2, 3], "source_file": ["sql://sales/tbl_22"] * 3,
    })
    result = answer_tabular_question("How many units did we sell in September 2026?", frame)
    assert result["values"]["total_quantity_sold"] == 7
    assert result["source_rows"] == [1, 2]


def test_highest_recorded_monthly_sales_revenue_reports_the_top_month():
    frame = pd.DataFrame({
        "invoice_id": ["A", "B", "C"],
        "quantity": [1, 1, 1], "amount": [100, 250, 150],
        "date": ["2026-07-10", "2026-08-10", "2026-09-10"],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_row": [1, 2, 3],
        "source_file": ["sql://sales/tbl_22"] * 3,
    })
    result = answer_tabular_question(
        "Among records currently selected, which calendar month has the highest recorded sales revenue?",
        frame,
    )
    assert result["values"]["highest_revenue_month"] == "2026-08"
    assert result["values"]["sales_revenue"] == 250
    assert result["source_rows"] == [("sql://sales/tbl_22", 2)]


def test_highest_sales_amount_by_payment_method_uses_selected_field():
    frame = pd.DataFrame({
        "invoice_id": ["A", "B", "C", "D"],
        "quantity": [1, 1, 1, 1], "amount": [100, 80, 150, 30],
        "payment_method": ["Cash", "Card", "Cash", "Wallet"],
        "txn_type": ["sale"] * 4, "table_name": ["tbl_22"] * 4,
        "source_row": [1, 2, 3, 4], "source_file": ["sql://sales/tbl_22"] * 4,
    })
    result = answer_tabular_question(
        "Which payment method generated the greatest recorded sales amount?", frame
    )
    assert result["values"]["results"][0] == {"Payment method": "Cash", "sales": 250.0}
    assert result["source_rows"] == [1, 2, 3, 4]


def test_cash_payment_filter_is_applied_with_explicit_month():
    frame = pd.DataFrame({
        "invoice_id": ["A", "B", "C"],
        "quantity": [1, 1, 1], "amount": [100, 250, 150],
        "payment_method": ["Cash", "Card", "Cash"],
        "date": ["2026-09-01", "2026-09-02", "2026-08-31"],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_row": [1, 2, 3], "source_file": ["sql://sales/tbl_22"] * 3,
    })
    result = answer_tabular_question(
        "What was the total sales amount paid in cash during September 2026?", frame
    )
    assert result["values"]["total_sales"] == 100.0
    assert result["source_rows"] == [1]


def test_combined_product_stock_uses_table_specific_citations_without_fake_row_list():
    frame = pd.DataFrame({
        "database_name": ["inventory"] * 2 + ["sales"],
        "table_name": ["tbl_15", "tbl_15", "tbl_22"],
        "product_id": ["76", "76", "76"], "product_code": [None, None, "76"],
        "quantity": [62, 133, 999], "reorder_level": [10, 10, None],
        "expiry_date": ["2028-06-30", "2028-07-30", None],
        "source_file": ["sql://inventory/tbl_15"] * 2 + ["sql://sales/tbl_22"],
        "source_row": [1, 29, 1], "txn_type": [None, None, "sale"],
    })
    result = answer_tabular_question(
        "What is the combined on-hand quantity for product 76 across the selected inventory tables?",
        frame,
    )
    assert result["values"]["total_stock"] == 195
    assert "Records:" not in result["answer"]
    assert result["source_rows"] == [
        ("sql://inventory/tbl_15", 1), ("sql://inventory/tbl_15", 29),
    ]


def test_unit_cost_valuation_discloses_missing_cost_coverage_and_cites_priced_rows():
    frame = pd.DataFrame({
        "database_name": ["inventory", "inventory"],
        "table_name": ["tbl_10", "tbl_15"],
        "quantity": [5, 10], "cost": [20.0, None],
        "reorder_level": [1, 1], "expiry_date": ["2027-01-01", "2027-02-01"],
        "source_file": ["sql://inventory/tbl_10", "sql://inventory/tbl_15"],
        "source_row": [7, 8],
    })
    result = answer_tabular_question("What is the total inventory value at recorded unit cost?", frame)
    assert result["values"] == {
        "status": "partial_data", "inventory_value_subtotal": 100.0,
        "priced_records": 1, "missing_value_records": 1,
    }
    assert "full-scope inventory value is unavailable" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 7)]


def test_earliest_in_stock_batch_answer_includes_the_requested_batch_id():
    frame = pd.DataFrame({
        "database_name": ["inventory", "inventory"],
        "table_name": ["tbl_10", "tbl_10"],
        "batch_no": ["LOT-A", "LOT-B"],
        "product_id": ["P1", "P2"], "product_name": ["Alpha", "Beta"],
        "expiry_date": ["2026-08-08", "2026-08-15"], "quantity": [38, 22],
        "reorder_level": [5, 5], "warehouse": ["A", "B"],
        "source_file": ["sql://inventory/tbl_10"] * 2, "source_row": [4, 9],
    })
    result = answer_tabular_question("Which in-stock batch has the earliest expiry date?", frame)
    assert "batch LOT-A" in result["answer"]
    assert result["values"]["earliest"] == {"batch_no": "LOT-A", "expiry_date": "2026-08-08"}
    assert result["source_rows"] == [("sql://inventory/tbl_10", 4)]


def test_receipt_alias_uses_transaction_id_and_sums_all_sales_lines():
    frame = pd.DataFrame({
        "transaction_id": [31, 31, 32],
        "invoice_id": ["Alpha", "Beta", "Gamma"],
        "amount": [780.0, 1920.0, 5000.0], "quantity": [1, 2, 3],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [89, 90, 91],
    })
    result = answer_tabular_question("What is the total recorded sales amount on invoice REC-00031?", frame)
    assert result["values"]["lookup_id"] == "REC-00031"
    assert result["values"]["total_sales_amount"] == 2700.0
    assert result["values"]["matched_records"] == 2
    assert result["source_rows"] == [("sql://sales/tbl_22", 89), ("sql://sales/tbl_22", 90)]


def test_unique_products_question_counts_products_not_sales_transactions():
    frame = pd.DataFrame({
        "transaction_id": ["1", "1", "2", "3", None, None],
        "product_id": ["Alpha", "Beta", "Alpha", "Gamma", "Stock Item X", "Stock Item Y"],
        "amount": [10, 20, 30, 40, None, None], "quantity": [1, 1, 3, 1, 20, 30],
        "txn_type": ["sale"] * 4 + ["inventory"] * 2, "table_name": ["tbl_22"] * 4 + ["tbl_10"] * 2,
        "source_file": ["sql://sales/tbl_22"] * 4 + ["sql://inventory/tbl_10"] * 2,
        "source_row": [1, 2, 3, 4, 10, 11],
    })
    result = answer_tabular_question("How many unique products appear in the selected sales records?", frame)
    assert result["values"]["distinct_products"] == 3
    assert "3 distinct products" in result["answer"]


def test_distinct_entity_count_precedes_cross_dataset_ranking():
    question = "How many distinct products have at least one recorded sales detail line?"
    frame = pd.DataFrame({
        "transaction_id": ["1", "1", "2", "3", None, None],
        "product_id": ["Alpha", "Beta", "Alpha", "Gamma", "Stock Item X", "Stock Item Y"],
        "amount": [10, 20, 30, 40, None, None], "quantity": [1, 1, 3, 1, 20, 30],
        "txn_type": ["sale"] * 4 + ["inventory"] * 2, "table_name": ["tbl_22"] * 4 + ["tbl_10"] * 2,
        "source_file": ["sql://sales/tbl_22"] * 4 + ["sql://inventory/tbl_10"] * 2,
        "source_row": [1, 2, 3, 4, 10, 11],
    })
    assert is_distinct_entity_count_question(question)
    result = answer_tabular_question(question, frame)
    assert result["values"]["distinct_count"] == 3
    assert "3 distinct" in result["answer"]
    assert not is_distinct_entity_count_question("Which products had the highest sales?")


def test_ranked_receipt_count_uses_count_metric_before_sales_value_ranking():
    frame = pd.DataFrame({
        "payment_method": ["Cash", "Cash", "Cash", "JazzCash", "JazzCash", None, None],
        "transaction_id": ["R1", "R2", "R3", "R4", "R5", None, None],
        "invoice_total": [100, 200, 300, 900, 800, None, None],
        "stock_qty": [None] * 5 + [30, 50], "purchase_order_no": [None] * 5 + ["P1", "P2"],
        "txn_type": ["sale"] * 5 + ["inventory"] * 2,
        "source_file": ["sql://sales/tbl_21"] * 5 + ["sql://inventory/tbl_10"] * 2,
        "source_row": [1, 2, 3, 4, 5, 6, 7],
        "_header_source_file": ["sql://sales/tbl_21"] * 5 + [None, None],
        "_header_source_row": [1, 2, 3, 4, 5, None, None],
    })
    question = "Which payment method was used on the largest number of sales receipts, and what was its receipt count?"
    assert is_ranked_record_count_question(question)
    result = answer_tabular_question(question, frame)
    assert result["values"]["results"] == [{"payment_method": "Cash", "value": 3}]
    assert "Cash: 3" in result["answer"]
    assert result["source_rows"] == [
        ("sql://sales/tbl_21", 1), ("sql://sales/tbl_21", 2), ("sql://sales/tbl_21", 3),
    ]
    assert not is_ranked_record_count_question("How many sales invoices include at least one returned item?")


def test_latest_sales_date_request_does_not_add_line_count_or_detail_noise():
    frame = pd.DataFrame({
        "transaction_id": ["129", "130", "130"],
        "date": ["2026-10-01 16:00:00", "2026-10-02 16:00:00", "2026-10-02 16:00:00"],
        "product_id": ["Drug A", "Drug B", "Drug C"], "quantity": [1, 1, 2],
        "amount": [100, 200, 300], "txn_type": ["sale"] * 3,
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [1, 2, 3],
        "_header_source_file": ["sql://sales/tbl_21"] * 3,
        "_header_source_row": [129, 130, 130],
    })
    result = answer_tabular_question("What is the date of the most recent sales receipt recorded?", frame)
    assert result["answer"] == "The most recent recorded sales receipt date is 2026-10-02."
    assert result["values"]["latest_sales_date"] == "2026-10-02"
    assert result["source_rows"] == [("sql://sales/tbl_21", 130)]


def test_median_quantity_per_sales_line_uses_sales_grain_from_mixed_pos_frame():
    frame = pd.DataFrame({
        "quantity": [1, 3, 5, 100],
        "product_id": ["A", "B", "C", "Stock D"],
        "txn_type": ["sale", "sale", "sale", "inventory"],
        "source_file": ["sql://sales/tbl_22"] * 3 + ["sql://inventory/tbl_10"],
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question("What is the median quantity recorded per individual sales detail line?", frame)
    assert result["values"]["median"] == 3
    assert result["values"]["line_count"] == 3
    assert result["source_rows"] == [("sql://sales/tbl_22", 1), ("sql://sales/tbl_22", 2), ("sql://sales/tbl_22", 3)]


def test_roman_urdu_named_product_units_query_avoids_cross_dataset_row_count():
    from app.language.roman_urdu import normalize_roman_urdu_intent

    question = normalize_roman_urdu_intent("Omep 20mg Capsules ki sales mein kul kitni units record hui hain?")
    frame = pd.DataFrame({
        "transaction_id": ["1", "2", "3", None],
        "product_id": ["Omep 20mg Capsules", "Omep 20mg Capsules", "Other", None],
        "quantity": [2, 3, 9, None], "amount": [460, 690, 99, None],
        "unit_price": [230, 230, 99, None], "txn_type": ["sale", "sale", "sale", "purchase"],
        "stock_qty": [None, None, None, None], "purchase_order_no": [None, None, None, "P1"],
        "cost": [None, None, None, 400],
        "table_name": ["tbl_22"] * 3 + ["tbl_purchase"],
        "source_file": ["sql://sales/tbl_22"] * 3 + ["sql://purchases/tbl_purchase"],
        "source_row": [1, 2, 3, 4],
    })
    assert "how many units" in question
    assert answer_cross_dataset_question(question, frame) is None
    result = answer_tabular_question(question, frame)
    assert result["values"]["total_quantity_sold"] == 5
    assert result["source_rows"] == [("sql://sales/tbl_22", 1), ("sql://sales/tbl_22", 2)]


def test_named_product_quantity_query_declines_when_product_is_absent():
    from app.language.roman_urdu import normalize_roman_urdu_intent

    question = normalize_roman_urdu_intent("Omep 20mg Capsules ki sales mein kul kitni units record hui hain?")
    frame = pd.DataFrame({
        "product_id": ["Panadol 500mg Tab", "Augmentin 625mg Tab"],
        "quantity": [10, 3], "txn_type": ["sale", "sale"],
        "source_file": ["sql://PharmacyPOS/tbl_SalesDetails"] * 2,
        "source_row": [1, 2],
    })
    result = answer_tabular_question(question, frame)
    assert result["values"]["status"] == "no_matching_product"
    assert result["values"]["product_query"] == "omep 20mg capsules"
    assert not result["source_rows"]


def test_named_product_quantity_uses_spreadsheet_product_name_column():
    frame = pd.DataFrame({
        "product_name": ["Medicine A", "Medicine A", "Medicine B"],
        "quantity": [2, 3, 40], "transaction_id": ["R1", "R2", "R3"],
        "source_file": ["sales.xlsx"] * 3, "source_row": [2, 3, 4],
    })
    result = answer_tabular_question("How many units were sold for Medicine A?", frame)
    assert result["values"]["total_quantity_sold"] == 5
    assert result["values"]["product"] == "Medicine A"
    assert result["source_rows"] == [("sales.xlsx", 2), ("sales.xlsx", 3)]


def test_schema_planner_counts_groups_with_exact_sales_detail_lines(monkeypatch):
    from app.analytics import schema_query
    frame = pd.DataFrame({
        "transaction_id": ["R1"] * 3 + ["R2"] * 3 + ["R3"] * 2 + ["R1", None],
        "product_id": ["A", "B", "C", "A", "D", "E", "F", "G", None, "Stock H"],
        "quantity": [1, 1, 2, 1, 1, 2, 3, 1, None, 10],
        "txn_type": ["sale"] * 8 + ["sale", "inventory"],
        "source_file": ["sql://sales/tbl_22"] * 9 + ["sql://inventory/tbl_10"],
        "source_row": list(range(1, 11)),
    })
    monkeypatch.setattr(schema_query, "_decode_plan", lambda _q, _f: {
        "status": "ready", "reason": "", "filters": [
            {"field": "txn_type", "op": "eq", "value": "sale"},
            {"field": "source_file", "op": "contains", "value": "sales"},
        ],
        "group_by": ["transaction_id"], "having": {"aggregate": "count_non_null", "field": "product_id", "op": "eq", "value": 3},
        "measure": {"op": "count_rows", "field": ""}, "limit": 20,
    })
    result = answer_tabular_question("How many receipts have exactly three sales detail lines?", frame)
    assert result["values"].get("result") == 2, result
    assert result["values"]["query_diagnostic"]["validation"] == "ok"
    assert result["answer"].startswith("2 receipts")
    assert len(result["source_rows"]) == 6


def test_requested_payment_breakdown_declines_when_schema_has_no_payment_field():
    frame = pd.DataFrame({
        "invoice_id": ["R1", "R2"], "paid_amount": [100, 200],
        "source_file": ["sql://PharmacyPOS/tbl_SalesHeader"] * 2,
        "source_row": [1, 2],
    })
    result = answer_tabular_question(
        "Which payment method was used on the largest number of sales receipts, and what was its receipt count?",
        frame,
    )
    assert result["values"]["status"] == "missing_required_field"
    assert result["values"]["requested_dimension"] == "payment method"
    assert not result["source_rows"]


def test_returned_line_count_uses_recorded_return_flag_not_transaction_count():
    frame = pd.DataFrame({
        "transaction_id": ["1", "1", "2"], "product_id": ["A", "B", "C"],
        "amount": [10, 20, 30], "quantity": [1, 1, 2],
        "_extra.RETURNED": [0, 0, 0], "txn_type": ["sale"] * 3,
        "table_name": ["tbl_22"] * 3, "source_file": ["sql://sales/tbl_22"] * 3,
        "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "How many sales lines are marked as returned in the selected records?", frame
    )
    assert result["values"]["returned_lines"] == 0
    assert result["values"]["return_field"] == "_extra.RETURNED"
    assert result["source_rows"] == [("sql://sales/tbl_22", 1), ("sql://sales/tbl_22", 2), ("sql://sales/tbl_22", 3)]


def test_returned_invoice_count_precedes_product_ranking_and_deduplicates_lines():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2], "invoice_id": ["A", "A", "B"],
        "product_id": ["Aspirin", "Vitamin C", "Aspirin"],
        "amount": [10, 20, 30], "quantity": [1, 1, 2],
        "_extra.RETURNED": [0, 0, 0], "txn_type": ["sale"] * 3,
        "table_name": ["tbl_22"] * 3, "source_file": ["sql://sales/tbl_22"] * 3,
        "source_row": [1, 2, 3],
    })
    result = answer_tabular_question("How many sales invoices include at least one returned item?", frame)
    assert result["values"]["returned_invoices"] == 0
    assert result["values"]["returned_lines"] == 0
    assert "0 sales invoices" in result["answer"]
    assert result["source_rows"] == [
        ("sql://sales/tbl_22", 1), ("sql://sales/tbl_22", 2), ("sql://sales/tbl_22", 3)
    ]


def test_product_discount_ranking_precedes_overall_discount_sum():
    frame = pd.DataFrame({
        "transaction_id": ["1", "2", "3"], "product_id": ["Alpha", "Beta", "Alpha"],
        "amount": [90, 80, 90], "discount": [10, 40, 15], "quantity": [1, 1, 1],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "Which medicine received the largest total discount across its sales entries?", frame
    )
    assert result["values"]["results"][0] == {"Product": "Beta", "discount": 40.0}


def test_runner_up_follow_up_uses_second_grouped_sales_value():
    frame = pd.DataFrame({
        "transaction_id": ["1", "2", "3"], "product_id": ["Alpha", "Beta", "Gamma"],
        "amount": [100, 80, 60], "quantity": [1, 1, 1],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "Previous analysis question: Rank medicines by recorded sales value. Current follow-up: And what was the runner-up?",
        frame,
    )
    assert result["values"]["results"][0] == {"Product": "Beta", "sales": 80.0}


def test_exact_receipt_payment_method_abstains_when_only_status_is_recorded():
    frame = pd.DataFrame({
        "invoice_id": ["REC-00031"], "transaction_id": [31], "status": [1],
        "amount": [780], "source_file": ["sql://sales/tbl_22"], "source_row": [89],
    })
    result = answer_tabular_question(
        "What payment method did receipt REC-00031 use?", frame
    )
    assert "does not include a payment method" in result["answer"]
    assert result["values"]["status"] == "unsupported_field"
    assert result["source_rows"] == [89]


def test_exact_receipt_combines_requested_header_channel_and_payment_with_header_citation():
    frame = pd.DataFrame([
        {
            "invoice_id": "REC-00001", "transaction_id": "1",
            "payment_method": "JazzCash", "_extra.SHIP_VIA": "Counter Sale",
            "source_file": "sql://sales/tbl_22", "source_row": 1,
            "_header_source_file": "sql://sales/tbl_21", "_header_source_row": 1,
        },
        {
            "invoice_id": "REC-00001", "transaction_id": "1",
            "payment_method": "JazzCash", "_extra.SHIP_VIA": "Counter Sale",
            "source_file": "sql://sales/tbl_22", "source_row": 2,
            "_header_source_file": "sql://sales/tbl_21", "_header_source_row": 1,
        },
    ])
    result = answer_tabular_question(
        "For receipt REC-00001, what payment method was used, and was it marked as a counter sale?", frame
    )

    assert "Payment method: JazzCash" in result["answer"]
    assert "Recorded sale channel: Counter Sale" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 1)]


def test_exact_receipt_customer_balance_returns_header_value():
    frame = pd.DataFrame([{
        "invoice_id": "REC-00002", "customer_balance": 0.0,
        "source_file": "sql://sales/tbl_22", "source_row": 6,
        "_header_source_file": "sql://sales/tbl_21", "_header_source_row": 2,
    }])
    result = answer_tabular_question("REC-00002 customer balance how much was recorded?", frame)
    assert "Customer balance: 0.0" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 2)]


def test_named_payment_method_share_uses_unfiltered_invoice_denominator_and_header_citations():
    frame = pd.DataFrame({
        "invoice_id": ["R1", "R2", "R3", "R3", "R3"],
        "payment_method": ["JazzCash", "Cash", "JazzCash", "JazzCash", "JazzCash"],
        "txn_type": ["sale"] * 5,
        "source_file": ["sql://sales/tbl_22"] * 5,
        "source_row": [1, 2, 3, 4, 5],
        "_header_source_file": ["sql://sales/tbl_21"] * 5,
        "_header_source_row": [1, 2, 3, 3, 4],
    })
    result = answer_tabular_question(
        "Across sales receipt headers, what percentage used JazzCash as the payment method?", frame
    )
    assert result["values"]["numerator"] == 3
    assert result["values"]["denominator"] == 4
    assert abs(result["values"]["percentage"] - 75.0) < 0.0001
    assert result["source_rows"] == [("sql://sales/tbl_21", 1), ("sql://sales/tbl_21", 3), ("sql://sales/tbl_21", 4)]


def test_named_product_discount_yes_no_uses_matching_sales_rows():
    frame = pd.DataFrame({
        "transaction_id": ["1", "2", "3"],
        "product_id": ["Jardiance 10mg Tablets", "Other Drug", "Jardiance 10mg Tablets"],
        "amount": [100, 80, 100], "discount": [0, 5, 0], "quantity": [1, 1, 1],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [26, 27, 28],
    })
    result = answer_tabular_question(
        "Were any sales of Jardiance 10mg Tablets discounted?", frame
    )
    assert result["values"]["total_discount"] == 0
    assert result["values"]["matched_records"] == 2
    assert result["source_rows"] == [26, 28]


def test_exact_receipt_quantity_question_beats_how_much_invoice_total():
    frame = pd.DataFrame({
        "invoice_id": ["REC-00031", "REC-00031", "REC-00031"],
        "transaction_id": [31, 31, 31],
        "product_id": ["Getryl 3mg Tablets", "Pyodine Surgical Scrub", "Zyrtec 10mg Tablets"],
        "quantity": [2, 4, 2], "amount": [780, 1920, 700],
        "txn_type": ["sale"] * 3, "table_name": ["tbl_22"] * 3,
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [89, 90, 91],
    })
    result = answer_tabular_question(
        "What quantity of Getryl 3mg Tablets was on receipt REC-00031?", frame
    )
    assert result["values"]["total_quantity"] == 2
    assert result["values"]["product"] == "Getryl 3mg Tablets"
    assert result["source_rows"] == [89]


def test_inventory_maximum_stock_question_returns_highest_batch_only():
    frame = pd.DataFrame({
        "table_name": ["tbl_10", "tbl_10", "tbl_15"],
        "product_id": ["A", "B", "C"], "batch_no": ["LOT-A", "LOT-B", "LOT-C"],
        "quantity": [20, 349, 100], "reorder_level": [5, 5, 5],
        "expiry_date": ["2027-01-01"] * 3, "warehouse": ["Main"] * 3,
        "source_file": ["sql://inventory/tbl_10", "sql://inventory/tbl_10", "sql://inventory/tbl_15"],
        "source_row": [1, 2, 1],
    })
    result = answer_tabular_question(
        "Which inventory batch currently has the most stock on hand?", frame
    )
    assert result["values"]["highest_stock"] == 349
    assert result["values"]["batches"] == ["LOT-B"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 2)]


def test_below_reorder_count_by_rack_precedes_stock_unit_ranking_and_excludes_purchase_rows():
    rows = []
    for index in range(5):
        rows.append({"table_name": "tbl_10", "database_name": "inventory", "txn_type": "inventory",
                     "warehouse": "Rack-E2", "batch_no": f"LOT-E2-{index}", "quantity": 5, "reorder_level": 10,
                     "source_file": "sql://inventory/tbl_10", "source_row": index + 1})
    for index in range(3):
        rows.append({"table_name": "tbl_10", "database_name": "inventory", "txn_type": "inventory",
                     "warehouse": "Rack-B8", "batch_no": f"LOT-B8-{index}", "quantity": 2, "reorder_level": 10,
                     "source_file": "sql://inventory/tbl_10", "source_row": index + 6})
    rows.append({"table_name": "tbl_15", "database_name": "inventory", "txn_type": "inventory",
                 "warehouse": "Rack-E2", "batch_no": "PO-BATCH-9", "quantity": 1, "reorder_level": 20, "purchase_order_no": "9",
                 "source_file": "sql://inventory/tbl_15", "source_row": 1})
    result = answer_tabular_question(
        "For current stock batches, show counts below their reorder levels by rack and identify the rack with the highest count.",
        pd.DataFrame(rows),
    )
    assert result["values"]["counts_by_location"] == {"Rack-E2": 5, "Rack-B8": 3}
    assert result["values"]["selected_locations"] == ["Rack-E2"]
    assert result["values"]["rank"] == 1
    assert all(source_file == "sql://inventory/tbl_10" for source_file, _ in result["source_rows"])


def test_next_below_reorder_count_followup_preserves_grouping_and_includes_ties():
    prior = "For current stock batches, show counts below their reorder levels by rack and identify the rack with the highest count."
    followup = "And which racks came next, including ties?"
    contextual = f"Previous analysis question: {prior} Current follow-up: {followup}"
    interpreted = _current_pharmacy_question(contextual)
    assert "next distinct count" in interpreted
    assert "counts by rack" in interpreted

    rows = []
    for rack, count, quantity in (("Rack-E2", 5, 5), ("Rack-B8", 3, 2), ("Rack-E8", 3, 2), ("Rack-C4", 2, 1)):
        for index in range(count):
            rows.append({
                "table_name": "tbl_10", "database_name": "inventory", "txn_type": "inventory",
                "warehouse": rack, "batch_no": f"{rack}-{index}", "quantity": quantity,
                "reorder_level": 10, "source_file": "sql://inventory/tbl_10", "source_row": len(rows) + 1,
            })
    result = answer_tabular_question(interpreted, pd.DataFrame(rows))
    assert result["values"]["selected_count"] == 3
    assert result["values"]["selected_locations"] == ["Rack-B8", "Rack-E8"]
    assert result["values"]["rank"] == 2
    assert {row for _, row in result["source_rows"]} == set(range(6, 12))
    assert all(source == "sql://inventory/tbl_10" for source, _ in result["source_rows"])


def test_highest_distinct_product_count_by_current_rack_keeps_location_grouping():
    frame = pd.DataFrame({
        "warehouse": ["Rack-A", "Rack-A", "Rack-B", "Rack-B", "Rack-B", "Rack-C"],
        "product_id": ["P1", "P1", "P2", "P3", "P3", "P4"],
        "quantity": [4, 8, 2, 3, 5, 6],
        "source_file": ["sql://inventory/tbl_10"] * 6,
        "source_row": [1, 2, 3, 4, 5, 6],
    })
    result = answer_tabular_question(
        "Which rack stores the greatest number of distinct products in current inventory?", frame
    )
    assert result["values"]["distinct_products_by_location"] == {"Rack-A": 1, "Rack-B": 2, "Rack-C": 1}
    assert result["values"]["highest_locations"] == ["Rack-B"]
    assert result["values"]["highest_count"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 3), ("sql://inventory/tbl_10", 4)]


def test_at_or_below_reorder_count_includes_equal_stock_rows():
    frame = pd.DataFrame({
        "batch_no": ["BELOW-1", "EQUAL", "ABOVE"],
        "product_id": ["P1", "P2", "P3"],
        "quantity": [4, 5, 6], "reorder_level": [5, 5, 5],
        "source_file": ["sql://inventory/tbl_10"] * 3, "source_row": [1, 2, 3],
    })
    result = answer_tabular_question("How many current inventory batches are at or below their reorder level?", frame)
    assert result["values"]["matching_records"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1), ("sql://inventory/tbl_10", 2)]


def test_exactly_at_their_reorder_point_uses_equality_not_unfiltered_inventory():
    frame = pd.DataFrame({
        "batch_no": ["LOW", "EQUAL", "HIGH"],
        "quantity": [4, 5, 6], "reorder_level": [5, 5, 5],
        "source_file": ["sql://inventory/tbl_10"] * 3, "source_row": [1, 2, 3],
    })
    result = answer_tabular_question("How many current batches are exactly at their reorder point?", frame)
    assert result["values"]["matching_records"] == 1
    assert result["source_rows"] == [("sql://inventory/tbl_10", 2)]


def test_largest_invoice_total_uses_unique_header_grain_not_sales_line_amount():
    frame = pd.DataFrame({
        "invoice_id": ["REC-9", "REC-9", "REC-75"],
        "amount": [4000, 6400, 10400],
        "invoice_total": [10400, 10400, 14445],
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [1, 2, 3],
        "_header_source_file": ["sql://sales/tbl_21"] * 3,
        "_header_source_row": [9, 9, 75],
    })
    result = answer_tabular_question("Which receipt had the largest invoice total, and what was its amount?", frame)
    assert result["values"]["highest_invoice_total"] == 14445
    assert result["values"]["receipt_ids"] == ["REC-75"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 75)]


def test_positive_receipt_balance_count_filters_headers_before_generic_receipt_count():
    frame = pd.DataFrame({
        "invoice_id": ["DUP", "DUP", "DUP", "ZERO"],
        "customer_balance": [25, 25, 0, 0],
        "source_file": ["sql://sales/tbl_22"] * 4, "source_row": [1, 2, 3, 4],
        "_header_source_file": ["sql://sales/tbl_21"] * 4,
        "_header_source_row": [9, 9, 10, 11],
    })
    result = answer_tabular_question("How many receipt headers have a positive customer balance due?", frame)
    assert result["values"]["receipts_with_positive_balance"] == 1
    assert result["values"]["receipt_headers_checked"] == 3
    assert result["source_rows"] == [("sql://sales/tbl_21", 9)]


def test_average_money_display_rounds_decimal_half_cent_up():
    frame = pd.DataFrame({
        "invoice_id": ["A", "B"], "amount": [3446.97, 3446.98],
        "source_file": ["sql://sales/tbl_21"] * 2, "source_row": [1, 2],
    })
    result = answer_tabular_question("What was the average amount per receipt?", frame)
    assert result["values"]["average_transaction_value"] == 3446.975
    assert "Average transaction value: 3,446.98" in result["answer"]


def test_invoice_header_record_count_deduplicates_joined_lines_and_excludes_purchase_headers():
    frame = pd.DataFrame({
        "invoice_id": ["R1", "R1", "R2", "PO1"],
        "invoice_total": [100, 100, 200, 300],
        "source_file": ["sql://sales/tbl_22"] * 3 + ["sql://purchases/tbl_16"],
        "source_row": [1, 2, 3, 1],
        "_header_source_file": ["sql://sales/tbl_21"] * 3 + ["sql://purchases/tbl_11"],
        "_header_source_row": [10, 10, 11, 5],
    })
    result = answer_tabular_question("How many invoice header records are in the selected sales data?", frame)
    assert result["values"]["invoice_header_records"] == 2


def test_receipt_counts_by_pos_terminal_deduplicates_joined_sales_lines():
    frame = pd.DataFrame({
        "invoice_id": ["R-1", "R-1", "R-1", "P-1"],
        "_extra.TERMINAL_ID": ["POS-1", "POS-1", "POS-1", "POS-9"],
        "_header_source_file": ["sql://sales/tbl_21"] * 3 + ["sql://inventory/tbl_14"],
        "_header_source_row": [1, 1, 2, 3],
        "source_row": [10, 11, 12, 13],
    })
    result = answer_tabular_question(
        "Which POS terminal handled the sales receipts, and how many receipts were recorded for it?", frame
    )
    assert result["values"]["receipts_by_terminal"] == [{"terminal": "POS-1", "receipts": 2}]
    assert result["source_rows"] == [("sql://sales/tbl_21", 1), ("sql://sales/tbl_21", 2)]


def test_invoice_discount_count_uses_header_discount_not_sales_line_discount():
    frame = pd.DataFrame({
        "invoice_id": ["R-1", "R-1", "R-2", "R-2"],
        "invoice_discount": [10.0, 10.0, 0.0, 0.0],
        "discount": [0.0, 99.0, 50.0, 0.0],
        "_header_source_file": ["sql://sales/tbl_21"] * 4,
        "_header_source_row": [1, 1, 2, 2],
        "source_row": [10, 11, 12, 13],
    })
    result = answer_tabular_question("How many sales receipt headers have an invoice discount greater than zero?", frame)
    assert result["values"]["sales_headers_with_invoice_discount"] == 1
    assert result["values"]["discount_field"] == "invoice_discount"
    assert result["source_rows"] == [("sql://sales/tbl_21", 1)]


def test_lowest_positive_current_stock_batch_excludes_zero_and_purchase_rows():
    frame = pd.DataFrame({
        "batch_no": ["LOT-A", "LOT-B", "LOT-ZERO", "PO-BATCH"],
        "product_id": ["A", "B", "Z", "P"],
        "quantity": [12, 18, 0, 3],
        "table_name": ["tbl_10", "tbl_10", "tbl_10", "tbl_15"],
        "database_name": ["inventory"] * 4,
        "txn_type": ["inventory"] * 4,
        "purchase_order_no": ["", "", "", "44"],
        "source_file": ["sql://inventory/tbl_10"] * 3 + ["sql://inventory/tbl_15"],
        "source_row": [2, 3, 4, 1],
    })
    result = answer_tabular_question(
        "Which currently stocked batch has the lowest positive on-hand quantity, and how many units are left?", frame
    )
    assert result["values"]["lowest_stock"] == 12
    assert result["values"]["batches"] == ["LOT-A"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 2)]


def test_named_customer_invoice_total_filters_customer_and_deduplicates_headers():
    frame = pd.DataFrame({
        "_extra.CUSTOMER": ["Hina Qureshi", "Hina Qureshi", "Other Customer"],
        "invoice_id": ["R-1", "R-1", "R-2"],
        "invoice_total": [100.0, 100.0, 200.0],
        "_header_source_file": ["sql://sales/tbl_21"] * 3,
        "_header_source_row": [1, 1, 2],
        "source_row": [10, 11, 12],
    })
    result = answer_tabular_question(
        "What is the combined invoice total across sales receipts issued to Hina Qureshi?", frame
    )
    assert result["values"]["customer"] == "Hina Qureshi"
    assert result["values"]["total_sales"] == 100.0
    assert result["values"]["invoice_count"] == 1
    assert result["source_rows"] == [("sql://sales/tbl_21", 1)]


def test_most_frequent_selling_price_is_scoped_to_the_named_product():
    frame = pd.DataFrame({
        "product_id": ["Risek 20mg Capsules"] * 6 + ["Other Medicine"],
        "transaction_id": ["1", "2", "3", "4", "5", "6", "7"],
        "unit_price": [330.0, 330.0, 330.0, 330.0, 330.0, 23.5, 1.0],
        "source_file": ["sql://sales/tbl_22"] * 7,
        "source_row": [42, 153, 167, 291, 329, 50, 51],
    })
    result = answer_tabular_question("What selling price was recorded most often for Risek 20mg Capsules?", frame)
    assert result["values"]["product"] == "Risek 20mg Capsules"
    assert result["values"]["most_common_selling_price"] == [330.0]
    assert result["values"]["frequency"] == 5
    assert result["source_rows"] == [("sql://sales/tbl_22", 42), ("sql://sales/tbl_22", 153), ("sql://sales/tbl_22", 167), ("sql://sales/tbl_22", 291), ("sql://sales/tbl_22", 329)]


def test_highest_unit_cost_names_batches_when_batches_are_requested():
    frame = pd.DataFrame({
        "table_name": ["tbl_10"] * 3,
        "database_name": ["inventory"] * 3,
        "txn_type": ["inventory"] * 3,
        "batch_no": ["LOT-202601-048", "LOT-202602-048", "LOT-202603-048"],
        "product_id": ["Janumet 50/500mg Tablets"] * 3,
        "quantity": [45, 333, 291],
        "cost": [2400.0] * 3,
        "source_file": ["sql://inventory/tbl_10"] * 3,
        "source_row": [95, 96, 97],
    })
    result = answer_tabular_question(
        "Which currently stocked inventory batches share the highest recorded unit cost, and what is that cost?", frame
    )
    assert result["values"]["highest_unit_cost"] == 2400.0
    assert result["values"]["records"] == ["LOT-202601-048", "LOT-202602-048", "LOT-202603-048"]
    assert "LOT-202601-048" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 95), ("sql://inventory/tbl_10", 96), ("sql://inventory/tbl_10", 97)]


def test_before_year_expiry_count_filters_to_positive_current_inventory():
    frame = pd.DataFrame({
        "table_name": ["tbl_10"] * 3 + ["tbl_15"],
        "database_name": ["inventory"] * 4,
        "txn_type": ["inventory"] * 4,
        "batch_no": ["LOT-A", "LOT-B", "LOT-ZERO", "PO-BATCH"],
        "quantity": [5, 8, 0, 99],
        "expiry_date": ["2026-12-31", "2027-01-01", "2026-01-01", "2026-01-01"],
        "purchase_order_no": ["", "", "", "44"],
        "source_file": ["sql://inventory/tbl_10"] * 3 + ["sql://inventory/tbl_15"],
        "source_row": [1, 2, 3, 1],
    })
    result = answer_tabular_question("How many currently stocked inventory batches expire before 2027?", frame)
    assert result["values"]["matching_records"] == 1
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1)]


def test_top_therapeutic_categories_by_sales_detail_line_count_preserves_ties():
    frame = pd.DataFrame({
        "transaction_id": ["1", "2", "3", "4", "5"],
        "category": ["Antidiabetic", "Antidiabetic", "Cardiovascular", "Cardiovascular", "Other"],
        "amount": [10.0, 10.0, 5.0, 5.0, 1000.0],
        "table_name": ["sales_tbl_22"] * 5,
        "source_file": ["sql://sales/tbl_22"] * 5,
        "source_row": [1, 2, 3, 4, 5],
    })
    result = answer_tabular_question(
        "Which therapeutic categories tie for the most sales detail lines, and how many lines does each have?", frame
    )
    assert result["values"]["top_categories_by_line_count"] == [
        {"category": "Antidiabetic", "sales_detail_lines": 2},
        {"category": "Cardiovascular", "sales_detail_lines": 2},
    ]
    assert result["source_rows"] == [("sql://sales/tbl_22", 1), ("sql://sales/tbl_22", 2), ("sql://sales/tbl_22", 3), ("sql://sales/tbl_22", 4)]


def test_positive_stock_location_count_deduplicates_racks_and_excludes_purchase_rows():
    frame = pd.DataFrame({
        "warehouse": ["Rack-A", "Rack-A", "Rack-B", "Rack-C", "Rack-D"],
        "quantity": [2, 8, 0, 4, 99],
        "purchase_order_no": ["", "", "", "", "44"],
        "source_file": ["sql://inventory/tbl_10"] * 4 + ["sql://inventory/tbl_15"],
        "source_row": [1, 2, 3, 4, 1],
    })
    result = answer_tabular_question("How many distinct rack locations currently have positive stock?", frame)
    assert result["values"]["positive_stock_locations"] == 2
    assert result["values"]["locations"] == ["Rack-A", "Rack-C"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1), ("sql://inventory/tbl_10", 4)]


def test_earliest_current_batch_expiry_skips_purchase_linked_rows_and_forecast_path():
    frame = pd.DataFrame([
        {
            "database_name": "inventory", "table_name": "tbl_10", "txn_type": "inventory",
            "batch_no": "LOT-CURRENT", "product_id": "2", "expiry_date": "2026-08-08",
            "quantity": 38.0, "source_row": 4, "source_file": "sql://inventory/tbl_10",
        },
        {
            "database_name": "inventory", "table_name": "tbl_15", "txn_type": "inventory",
            "batch_no": "PO-BATCH-HISTORY", "product_id": "2", "expiry_date": "2026-07-01",
            "quantity": 100.0, "purchase_order_no": "1", "source_row": 1,
            "source_file": "sql://inventory/tbl_15",
        },
    ])
    result = answer_tabular_question(
        "Among current batch records, which batch has the earliest expiry date, and how many units are recorded on it?",
        frame,
    )
    assert result["values"]["earliest"]["batch_no"] == "LOT-CURRENT"
    assert result["values"]["earliest"]["expiry_date"] == "2026-08-08"
    assert result["values"]["earliest"]["quantity"] == 38.0
    assert "quantity 38" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 4)]


def test_expired_current_batch_cutoff_uses_expiry_date_and_reports_count_and_units():
    frame = pd.DataFrame([
        {"database_name": "inventory", "table_name": "tbl_10", "txn_type": "inventory",
         "batch_no": "LOT-A", "expiry_date": "2026-09-01", "quantity": 7,
         "source_row": 1, "source_file": "sql://inventory/tbl_10"},
        {"database_name": "inventory", "table_name": "tbl_10", "txn_type": "inventory",
         "batch_no": "LOT-B", "expiry_date": "2026-10-03", "quantity": 2,
         "source_row": 2, "source_file": "sql://inventory/tbl_10"},
        {"database_name": "inventory", "table_name": "tbl_10", "txn_type": "inventory",
         "batch_no": "LOT-C", "expiry_date": "2026-10-05", "quantity": 9,
         "source_row": 3, "source_file": "sql://inventory/tbl_10"},
        {"database_name": "inventory", "table_name": "tbl_15", "txn_type": "inventory",
         "batch_no": "PO-OLD", "expiry_date": "2026-08-01", "quantity": 100,
         "purchase_order_no": "1", "source_row": 1, "source_file": "sql://inventory/tbl_15"},
    ])
    result = answer_tabular_question(
        "As of 2026-10-04, how many current batches in inventory have already expired while still showing positive stock, and how many units do they total?",
        frame,
    )
    assert result["values"]["expired_batches"] == 2
    assert result["values"]["expired_stock_units"] == 9
    assert result["values"]["as_of"] == "2026-10-04"
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1), ("sql://inventory/tbl_10", 2)]


def test_how_many_units_on_hand_sums_stock_not_batch_count():
    frame = pd.DataFrame({
        "table_name": ["tbl_10", "tbl_10", "tbl_10"],
        "product_id": ["Caflam 50mg Tablets"] * 3,
        "batch_no": ["LOT-A", "LOT-B", "LOT-C"], "quantity": [349, 250, 189],
        "reorder_level": [5] * 3, "expiry_date": ["2027-01-01"] * 3,
        "warehouse": ["Main"] * 3, "source_file": ["sql://inventory/tbl_10"] * 3,
        "source_row": [231, 232, 233],
    })
    result = answer_tabular_question(
        "How many units of Caflam 50mg Tablets are on hand across all its batches?", frame
    )
    assert result["values"]["total_stock"] == 788
    assert "788 units" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 231), ("sql://inventory/tbl_10", 232), ("sql://inventory/tbl_10", 233)]


def test_location_stock_ranking_sums_units_by_rack_not_single_batch():
    frame = pd.DataFrame({
        "table_name": ["tbl_10"] * 5,
        "product_id": ["A", "B", "C", "D", "E"], "batch_no": ["LOT-A", "LOT-B", "LOT-C", "LOT-D", "LOT-E"],
        "quantity": [349, 800, 600, 700, 15465], "reorder_level": [5] * 5,
        "expiry_date": ["2027-01-01"] * 5,
        "warehouse": ["Rack-A", "Rack-B", "Rack-B", "Rack-B", "Main Store"],
        "source_file": ["sql://inventory/tbl_10"] * 5, "source_row": [1, 2, 3, 4, 5],
    })
    result = answer_tabular_question(
        "Which storage rack holds the largest number of units in current inventory?", frame
    )
    assert result["values"]["location"] == "Rack-B"
    assert result["values"]["stock_units"] == 2100
    assert result["source_rows"] == [("sql://inventory/tbl_10", 2), ("sql://inventory/tbl_10", 3), ("sql://inventory/tbl_10", 4)]


def test_inventory_product_prefix_filters_batches_before_expiry_ranking():
    frame = pd.DataFrame({
        "table_name": ["tbl_10"] * 3,
        "product_id": ["Caflam 50mg Tablets (Diclofenac)", "Caflam 50mg Tablets (Diclofenac)", "Panadol Extra 500mg/65mg Tablets"],
        "batch_no": ["LOT-A", "LOT-B", "LOT-C"], "quantity": [10, 12, 50],
        "reorder_level": [5] * 3,
        "expiry_date": ["2027-09-23", "2028-04-26", "2026-08-08"],
        "warehouse": ["Rack-A", "Rack-B", "Rack-C"],
        "source_file": ["sql://inventory/tbl_10"] * 3, "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "Which Caflam 50mg batch expires first, and on what date?", frame
    )
    assert result["values"]["earliest"]["batch_no"] == "LOT-A"
    assert result["values"]["earliest"]["expiry_date"] == "2027-09-23"
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1)]


def test_inventory_named_medicine_abstains_when_only_numeric_product_ids_exist():
    frame = pd.DataFrame({
        "table_name": ["tbl_10", "tbl_10"], "product_id": ["112", "113"],
        "batch_no": ["LOT-112", "LOT-113"], "quantity": [349, 50],
        "reorder_level": [30, 10], "expiry_date": ["2027-09-23", "2026-08-08"],
        "warehouse": ["Rack-A", "Rack-B"], "source_file": ["sql://inventory/tbl_10"] * 2,
        "source_row": [231, 232],
    })
    result = answer_tabular_question(
        "Which Caflam 50mg batch expires first, and on what date?", frame
    )
    assert result["values"]["status"] == "missing_product_mapping"
    assert "no verified name mapping" in result["answer"]
    assert result["source_rows"] == []


def test_inventory_distinct_product_id_count_deduplicates_batch_rows():
    frame = pd.DataFrame({
        "table_name": ["tbl_10"] * 4,
        "product_id": ["112", "112", "113", "114"],
        "batch_no": ["A", "B", "C", "D"],
        "quantity": [3, 4, 5, 6], "reorder_level": [1, 1, 1, 1],
        "expiry_date": ["2027-01-01"] * 4, "warehouse": ["Main"] * 4,
        "source_file": ["sql://inventory/tbl_10"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question(
        "How many distinct product IDs appear in the current inventory batch table?", frame
    )
    assert result["values"]["distinct_products"] == 3
    assert result["values"]["count_field"] == "product_id"
    assert result["values"]["input_records"] == 4
    assert result["values"]["source_table_record_counts"] == {"tbl_10": 4}
    assert result["source_rows"] == [
        ("sql://inventory/tbl_10", 1),
        ("sql://inventory/tbl_10", 3),
        ("sql://inventory/tbl_10", 4),
    ]


def test_current_stock_row_count_excludes_purchase_linked_inventory_rows():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_10", "inventory tbl_10", "inventory tbl_15", "inventory tbl_15"],
        "database_name": ["inventory"] * 4,
        "product_id": ["A", "B", "A", "C"],
        "quantity": [10, 12, 4, 8],
        "reorder_level": [1, 1, 1, 1],
        "purchase_order_no": [None, None, "PO-1", "PO-2"],
        "source_file": ["sql://inventory/tbl_10"] * 2 + ["sql://inventory/tbl_15"] * 2,
        "source_row": [1, 2, 1, 2],
    })
    result = answer_tabular_question(
        "For the current stock table, how many product stock rows are recorded?", frame
    )
    assert result["values"]["current_stock_rows"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1), ("sql://inventory/tbl_10", 2)]


def test_current_stock_rows_below_reorder_level_report_the_qualifier():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_10"] * 3,
        "database_name": ["inventory"] * 3,
        "product_id": ["A", "B", "C"],
        "quantity": [4, 10, 0],
        "reorder_level": [5, 8, 2],
        "source_file": ["sql://inventory/tbl_10"] * 3,
        "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "How many current stock rows are below their recorded reorder level?", frame
    )
    assert result["values"]["below_reorder_level"] == 2
    assert "2 current stock rows are below" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1), ("sql://inventory/tbl_10", 3)]


def test_summed_current_on_hand_excludes_purchase_order_quantities():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_10", "inventory tbl_10", "inventory tbl_15"],
        "database_name": ["inventory"] * 3,
        "product_id": ["A", "B", "A"],
        "quantity": [10, 12, 100],
        "reorder_level": [1, 1, 1],
        "purchase_order_no": [None, None, "PO-1"],
        "source_file": ["sql://inventory/tbl_10"] * 2 + ["sql://inventory/tbl_15"],
        "source_row": [1, 2, 1],
    })
    result = answer_tabular_question(
        "What is the summed on-hand quantity in the current inventory snapshot table tbl_10?", frame
    )
    assert result["values"]["total_stock"] == 22
    assert result["values"]["matching_records"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1), ("sql://inventory/tbl_10", 2)]


def test_distinct_products_below_reorder_deduplicates_batch_rows():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_10"] * 4,
        "database_name": ["inventory"] * 4,
        "product_id": ["A", "A", "B", "C"],
        "batch_no": ["A-1", "A-2", "B-1", "C-1"],
        "quantity": [2, 3, 10, 0],
        "reorder_level": [5, 5, 8, 1],
        "purchase_order_no": [None, None, None, "PO-1"],
        "source_file": ["sql://inventory/tbl_10"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question(
        "How many different product IDs have any current stock batch below its reorder level?", frame
    )
    assert result["values"]["distinct_products_below_reorder"] == 1
    assert result["values"]["qualifying_stock_rows"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 1)]


def test_numeric_product_id_lookup_uses_id_column_when_names_also_exist():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_10"] * 3 + ["inventory tbl_1"],
        "database_name": ["inventory"] * 4,
        "product_id": [112.0, 112.0, 113.0, "Catalog Name"],
        "product_name": ["Product 1", "Product 1", "Product 2", "Catalog Name"],
        "_extra.ID": [None, None, None, 112],
        "batch_no": ["LOT-A", "LOT-B", "LOT-C", None],
        "quantity": [349, 250, 80, None],
        "reorder_level": [30, 30, 20, None],
        "source_file": ["sql://inventory/tbl_10"] * 3 + ["sql://inventory/tbl_1"],
        "source_row": [231, 232, 233, 1],
    })
    result = answer_tabular_question(
        "How many units are on hand for product ID 112 across its current inventory batches?", frame
    )
    assert result["values"]["total_stock"] == 599
    assert result["values"]["matching_records"] == 2
    assert result["source_rows"] == [("sql://inventory/tbl_10", 231), ("sql://inventory/tbl_10", 232)]


def test_numeric_product_id_found_only_in_purchase_rows_abstains_for_on_hand():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_15"],
        "database_name": ["inventory"],
        "product_id": ["112"],
        "batch_no": ["PO-BATCH-112"],
        "quantity": [79],
        "reorder_level": [30],
        "purchase_order_no": ["53"],
        "source_file": ["sql://inventory/tbl_15"],
        "source_row": [239],
    })
    result = answer_tabular_question(
        "How many units are on hand for product ID 112 across its current inventory batches?", frame
    )
    assert result["values"]["status"] == "missing_product_id_mapping"
    assert "can't map those rows to current on-hand stock" in result["answer"]
    assert result["source_rows"] == [("sql://inventory/tbl_15", 239)]


def test_exact_invoice_paid_amount_uses_invoice_header_and_header_citation():
    frame = pd.DataFrame({
        "invoice_id": ["REC-00031", "REC-00031"],
        "transaction_id": [31, 31],
        "amount": [100, 200],
        "invoice_total": [300, 300],
        "paid_amount": [300, 300],
        "source_file": ["sql://sales/tbl_22"] * 2,
        "source_row": [89, 90],
        "_header_source_file": ["sql://sales/tbl_21"] * 2,
        "_header_source_row": [31, 31],
    })
    result = answer_tabular_question("What amount was paid toward invoice REC-00031?", frame)
    assert result["values"]["amount_paid"] == 300
    assert "recorded amount paid 300.00" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 31)]


def test_discounted_sales_line_count_counts_positive_lines_not_receipts():
    frame = pd.DataFrame({
        "transaction_id": ["R1", "R1", "R2", "R3"],
        "product_id": ["A", "B", "C", "D"],
        "discount": [5, 0, 10, None],
        "amount": [95, 40, 90, 100],
        "source_file": ["sql://sales/tbl_22"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question("How many sales detail lines have a non-zero recorded discount?", frame)
    assert result["values"]["discounted_lines"] == 2
    assert "2 sales lines" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_22", 1), ("sql://sales/tbl_22", 3)]


def test_invoice_count_uses_invoice_grain_after_payment_method_filter():
    frame = pd.DataFrame({
        "transaction_id": [1, 2, 3, 4],
        "invoice_id": ["REC-1", "REC-1", "REC-2", "REC-3"],
        "payment_method": ["Cash", "Cash", "Cash", "Card"],
        "amount": [30, 20, 40, 15],
        "source_file": ["tbl_22"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question("How many unique invoices were paid using Cash?", frame)
    assert result["values"]["transaction_count"] == 2
    assert result["values"]["count_field"] == "invoice_id"
    assert "2 distinct invoices" in result["answer"]
    assert result["source_rows"] == [("tbl_22", 1), ("tbl_22", 3)]


def test_average_units_per_invoice_uses_each_invoice_total_in_requested_period():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2, 3],
        "invoice_id": ["I1", "I1", "I2", "I3"],
        "date": ["2026-09-02", "2026-09-02", "2026-09-20", "2026-10-01"],
        "quantity": [2, 3, 9, 10],
        "amount": [20, 30, 90, 100],
        "source_file": ["tbl_22"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question(
        "For September 2026, what was the average number of medicine units per invoice?",
        frame,
        filters={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert result["values"]["average_units_per_document"] == 7
    assert result["values"]["document_count"] == 2
    assert result["values"]["total_units"] == 14
    assert "7.00 units across 2 invoices" in result["answer"]
    assert result["source_rows"] == [1, 2, 3]


def test_total_invoice_tax_deduplicates_joined_sales_lines_and_cites_headers():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2],
        "invoice_id": ["REC-1", "REC-1", "REC-2"],
        "invoice_tax": [12.5, 12.5, 0.0],
        "amount": [50, 30, 40],
        "source_file": ["sql://sales/tbl_22"] * 3,
        "source_row": [1, 2, 3],
        "_header_source_file": ["sql://sales/tbl_21"] * 3,
        "_header_source_row": [10, 10, 11],
    })
    result = answer_tabular_question("How much total invoice tax is recorded on the selected sales headers?", frame)
    assert result["values"]["total_invoice_tax"] == 12.5
    assert result["values"]["invoice_count"] == 2
    assert "12.50" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 10), ("sql://sales/tbl_21", 11)]


def test_exact_invoice_tax_lookup_deduplicates_lines_and_cites_header():
    frame = pd.DataFrame({
        "transaction_id": [72, 72], "invoice_id": ["REC-00072", "REC-00072"],
        "invoice_tax": [0.0, 0.0], "amount": [630.0, 900.0],
        "source_file": ["sql://sales/tbl_22"] * 2, "source_row": [232, 233],
        "_header_source_file": ["sql://sales/tbl_21"] * 2, "_header_source_row": [72, 72],
    })
    result = answer_tabular_question("Was any sales tax recorded on invoice REC-00072?", frame)
    assert result["values"]["invoice_tax"] == 0.0
    assert result["values"]["matched_records"] == 1
    assert result["source_rows"] == [("sql://sales/tbl_21", 72)]


def test_average_positive_invoice_discount_uses_invoice_headers():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2, 3, 4],
        "invoice_id": ["REC-1", "REC-1", "REC-2", "REC-3", "TODAY-REC"],
        "invoice_discount": [120, 120, 30, 0, 45],
        "amount": [50, 40, 20, 60, 35],
        "source_file": ["sql://sales/tbl_22"] * 5,
        "source_row": [1, 2, 3, 4, 5],
        "_header_source_file": ["sql://sales/tbl_21"] * 5,
        "_header_source_row": [10, 10, 11, 12, 13],
    })
    result = answer_tabular_question(
        "Among invoices with a positive invoice-level discount, what was the average discount per invoice?", frame
    )
    assert result["values"]["average_invoice_discount"] == 65
    assert result["values"]["invoice_count"] == 3
    assert result["values"]["total_invoice_discount"] == 195
    assert "65.00" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 10), ("sql://sales/tbl_21", 11), ("sql://sales/tbl_21", 13)]


def test_list_numeric_n_most_sold_products_honors_requested_limit():
    frame = pd.DataFrame({
        "transaction_id": [1, 2, 3, 4],
        "product_id": ["A medicine", "B medicine", "C medicine", "D medicine"],
        "quantity": [10, 8, 6, 4],
        "amount": [100, 80, 60, 40],
        "source_file": ["tbl_22"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question("List the 3 medicines with the most units sold.", frame)
    assert result["values"]["metric"] == "units_sold"
    assert len(result["values"]["results"]) == 3
    assert [row["Product"] for row in result["values"]["results"]] == ["A medicine", "B medicine", "C medicine"]


def test_inventory_distinct_product_count_maps_numeric_ids_to_catalog_names():
    frame = pd.DataFrame({
        "table_name": ["inventory tbl_3", "inventory tbl_3", "inventory tbl_10", "inventory tbl_10", "inventory tbl_15", "inventory tbl_15"],
        "database_name": ["inventory"] * 6,
        "product_id": ["Panadol", "Caflam", "Panadol", "Caflam", "1", "2"],
        "_extra.ID": [1, 2, None, None, None, None],
        "purchase_order_no": [None, None, None, None, "PO-A", "PO-B"],
        "batch_no": [None, None, "A", "B", "C", "D"],
        "quantity": [None, None, 10, 12, 5, 6],
        "reorder_level": [None, None, 1, 1, 1, 1],
        "expiry_date": [None, None, "2027-01-01", "2027-01-01", "2027-01-01", "2027-01-01"],
        "warehouse": [None, None, "Main", "Main", "Main", "Main"],
        "source_file": ["sql://inventory/tbl_3"] * 2 + ["sql://inventory/tbl_10"] * 2 + ["sql://inventory/tbl_15"] * 2,
        "source_row": [1, 2, 1, 2, 1, 2],
    })
    result = answer_tabular_question(
        "How many distinct product IDs appear in the current inventory batch table?", frame
    )
    assert result["values"]["distinct_products"] == 2
    assert result["source_rows"] == [
        ("sql://inventory/tbl_10", 1),
        ("sql://inventory/tbl_10", 2),
    ]


def test_receipt_count_with_more_than_n_distinct_products_groups_by_receipt():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 1, 1, 2, 2, 2, 3, 3, 3, 3, 3],
        "invoice_id": ["REC-1"] * 4 + ["REC-2"] * 3 + ["REC-3"] * 5,
        "product_id": ["A", "B", "C", "D", "A", "B", "C", "A", "B", "C", "D", "E"],
        "quantity": [1] * 12, "amount": [10] * 12,
        "source_file": ["sql://sales/tbl_22"] * 12,
        "source_row": list(range(1, 13)),
    })
    result = answer_tabular_question(
        "How many receipts included more than three different medicines?", frame
    )
    assert result["values"]["qualifying_receipts"] == 2
    assert result["values"]["distinct_product_threshold"] == 3
    assert result["source_rows"] == [
        ("sql://sales/tbl_22", row) for row in (1, 2, 3, 4, 8, 9, 10, 11, 12)
    ]


def test_receipt_counts_by_payment_method_use_payment_header_records():
    frame = pd.DataFrame({
        "table_name": ["tbl_22"] * 3 + ["tbl_21"] * 4,
        "transaction_id": [1, 1, 2, 1, 2, 3, 4],
        "product_id": ["A", "B", "A", None, None, None, None],
        "quantity": [1, 2, 1, None, None, None, None],
        "amount": [10, 20, 10, None, None, None, None],
        "payment_method": [None, None, None, "Cash", "Card", "Cash", "JazzCash"],
        "source_file": ["sql://sales/tbl_22"] * 3 + ["sql://sales/tbl_21"] * 4,
        "source_row": [1, 2, 3, 1, 2, 3, 4],
    })
    result = answer_tabular_question(
        "How many receipts were paid with each payment method?", frame
    )
    assert result["values"]["receipts_by_payment_method"] == [
        {"payment_method": "Cash", "receipts": 2},
        {"payment_method": "Card", "receipts": 1},
        {"payment_method": "JazzCash", "receipts": 1},
    ]
    assert result["source_rows"] == [
        ("sql://sales/tbl_21", row) for row in (1, 2, 3, 4)
    ]


def test_highest_monthly_receipt_count_is_not_monthly_revenue():
    frame = pd.DataFrame({
        "transaction_id": [1, 2, 3, 4, 5, 6],
        "invoice_id": ["A", "A", "B", "C", "D", "D"],
        "date": ["2026-01-05", "2026-01-05", "2026-01-08", "2026-02-03", "2026-02-04", "2026-02-04"],
        "product_id": ["P1", "P2", "P1", "P3", "P4", "P5"],
        "quantity": [1, 1, 1, 1, 1, 1], "amount": [10, 10, 10, 1000, 1000, 1000],
        "source_file": ["sql://sales/tbl_21"] * 6,
        "source_row": [1, 1, 2, 3, 4, 4],
    })
    result = answer_tabular_question(
        "Which calendar month had the most sales receipts, and how many were there?", frame
    )
    assert result["values"]["highest_receipt_count_month"] == "2026-01"
    assert result["values"]["receipt_count"] == 2
    assert result["source_rows"] == [
        ("sql://sales/tbl_21", 1), ("sql://sales/tbl_21", 2)
    ]


def test_highest_daily_invoice_count_deduplicates_lines_and_cites_headers():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2, 3, 3],
        "invoice_id": ["TODAY-REC", "TODAY-REC", "TODAY-REC", "C", "C"],
        "date": ["2026-10-02"] * 3 + ["2026-08-28"] * 2,
        "product_id": ["P1", "P2", "P1", "P3", "P4"],
        "quantity": [1] * 5, "amount": [10] * 5,
        "source_file": ["sql://sales/tbl_22"] * 5, "source_row": [1, 2, 3, 4, 5],
        "_header_source_file": ["sql://sales/tbl_21"] * 5,
        "_header_source_row": [10, 10, 11, 12, 12],
    })
    result = answer_tabular_question(
        "Which date had the highest number of sales invoices, and how many?", frame
    )
    assert result["values"]["highest_receipt_count_day"] == "2026-10-02"
    assert result["values"]["receipt_count"] == 2
    assert result["source_rows"] == [
        ("sql://sales/tbl_21", 10), ("sql://sales/tbl_21", 11)
    ]


def test_outstanding_balance_uses_unique_invoice_header_values():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2], "invoice_id": ["REC-1", "REC-1", "REC-2"],
        "product_id": ["A", "B", "C"], "quantity": [1, 2, 1], "amount": [10, 20, 30],
        "customer_balance": [30, None, 0],
        "_header_source_file": ["sql://sales/tbl_21"] * 3,
        "_header_source_row": [11, 11, 12],
        "source_file": ["sql://sales/tbl_22"] * 3, "source_row": [1, 2, 3],
    })
    result = answer_tabular_question(
        "How much outstanding balance remains across all recorded receipts?", frame
    )
    assert result["values"]["total_outstanding_balance"] == 30
    assert result["values"]["receipts_with_balance"] == 1
    assert result["values"]["receipts_checked"] == 2
    assert result["source_rows"] == [
        ("sql://sales/tbl_21", 11), ("sql://sales/tbl_21", 12)
    ]


def test_category_receipt_frequency_counts_unique_receipts_not_revenue():
    frame = pd.DataFrame({
        "transaction_id": [1, 1, 2, 3, 4, 5],
        "invoice_id": ["R1", "R1", "R2", "R3", "R4", "R5"],
        "category": ["A", "A", "A", "A", "B", "B"],
        "product_id": ["P1", "P2", "P1", "P3", "P4", "P5"],
        "quantity": [1] * 6, "amount": [1, 1, 1, 1, 500, 500],
        "source_file": ["sql://sales/tbl_22"] * 6,
        "source_row": [1, 2, 3, 4, 5, 6],
    })
    result = answer_tabular_question(
        "Which therapeutic category appears on the most unique sales receipts?", frame
    )
    assert result["values"]["highest_receipt_category"] == "A"
    assert result["values"]["receipt_count"] == 3


def test_discounted_receipt_count_deduplicates_discounted_sales_lines():
    frame = pd.DataFrame({
        "transaction_id": ["R1", "R1", "R2", "R3"],
        "discount": [5, 2, 0, 10],
        "amount": [95, 38, 51, 90],
        "source_file": ["tbl_22"] * 4,
        "source_row": [1, 2, 3, 4],
    })
    result = answer_tabular_question(
        "How many distinct receipts included at least one discounted sales line?", frame
    )
    assert result["values"]["receipts_with_discount"] == 2
    assert result["values"]["discounted_lines"] == 3
    assert "2 receipts" in result["answer"]
    assert result["source_rows"] == [
        ("tbl_22", 1),
        ("tbl_22", 4),
    ]


def test_receipt_status_count_filters_headers_and_uses_header_citations():
    frame = pd.DataFrame({
        "invoice_id": ["R1", "R1", "R2", "R3"],
        "transaction_id": [1, 1, 2, 3],
        "status": ["1", "1", "0", "1"],
        "amount": [10, 5, 7, 12],
        "source_row": [1, 2, 3, 4],
        "_header_source_file": ["sql://sales/tbl_21"] * 4,
        "_header_source_row": [11, 11, 12, 13],
    })
    result = answer_tabular_question("How many receipt headers are marked with status 1?", frame)
    assert result["values"]["receipt_count"] == 2
    assert result["values"]["receipt_status"] == "1"
    assert result["source_rows"] == [("sql://sales/tbl_21", 11), ("sql://sales/tbl_21", 13)]
    field_wording = answer_tabular_question("Count receipt headers whose status field is 1.", frame)
    assert field_wording["values"]["receipt_count"] == 2
    assert field_wording["values"]["receipt_status"] == "1"
