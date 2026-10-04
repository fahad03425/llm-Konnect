import pandas as pd

from app.analytics import schema_query


def test_schema_plan_counts_groups_with_exact_dynamic_row_count(monkeypatch):
    frame = pd.DataFrame({
        "receipt_ref": ["A", "A", "A", "B", "B", "C"],
        "item_sku": ["x", "y", "z", "x", "y", "z"],
        "table_name": ["sales_detail"] * 6,
        "source_row": [10, 11, 12, 13, 14, 15],
    })
    monkeypatch.setattr(schema_query, "_decode_plan", lambda _q, _f: {
        "status": "ready", "reason": "", "filters": [], "group_by": ["receipt_ref"],
        "having": {"aggregate": "count_rows", "op": "eq", "value": 2},
        "measure": {"op": "count_rows", "field": ""}, "limit": 20,
    })
    result = schema_query.answer_schema_query("How many receipts have exactly two detail lines?", frame)
    assert result["values"]["result"] == 1
    assert result["values"]["query_diagnostic"]["validation"] == "ok"
    assert result["source_rows"] == [13, 14]


def test_schema_plan_uses_source_file_and_field_names_from_selected_csv_like_frame(monkeypatch):
    frame = pd.DataFrame({
        "Invoice Ref": ["A", "A", "B"], "Line SKU": ["x", "y", "x"],
        "source_file": ["sales-lines.csv"] * 3, "source_row": [2, 3, 4],
    })
    monkeypatch.setattr(schema_query, "_decode_plan", lambda _q, _f: {
        "status": "ready", "reason": "", "filters": [], "group_by": ["Invoice Ref"],
        "having": {"aggregate": "count_rows", "op": "eq", "value": 2},
        "measure": {"op": "count_rows", "field": ""}, "limit": 20,
    })
    result = schema_query.answer_schema_query("Count invoices with exactly two lines", frame)
    assert result["values"]["result"] == 1
    assert result["source_rows"] == [("sales-lines.csv", 2), ("sales-lines.csv", 3)]


def test_schema_plan_rejects_unknown_fields_without_calculating(monkeypatch):
    frame = pd.DataFrame({"invoice_ref": ["A"], "source_row": [1]})
    monkeypatch.setattr(schema_query, "_decode_plan", lambda _q, _f: {
        "status": "ready", "reason": "", "filters": [], "group_by": ["made_up_column"],
        "having": None, "measure": {"op": "count_rows", "field": ""}, "limit": 20,
    })
    result = schema_query.answer_schema_query("How many receipts have exactly one made-up detail line?", frame)
    assert result["values"]["status"] == "unsupported"
    assert result["values"]["query_diagnostic"]["validation"] == "unknown_field"


def test_schema_repair_recovers_missing_group_key_and_counts_populated_detail_field(monkeypatch):
    frame = pd.DataFrame({
        "invoice_ref": ["A", "A", "A", "B", "B"],
        "product_sku": ["x", "y", None, "x", "y"],
        "source_row": [1, 2, 3, 4, 5],
    })
    monkeypatch.setattr(schema_query, "_decode_plan", lambda _q, _f: {
        "status": "ready", "reason": "", "filters": [], "group_by": [],
        "having": {"aggregate": "count_rows", "op": "eq", "value": 2},
        "measure": {"op": "count_rows", "field": ""}, "limit": 20,
    })
    result = schema_query.answer_schema_query("How many invoices have exactly two detail lines?", frame)
    assert result["values"]["result"] == 2
    assert result["values"]["query_diagnostic"]["schema_repair"] == "detail_rows_counted_by_populated_field"
    assert result["source_rows"] == [1, 2, 4, 5]
