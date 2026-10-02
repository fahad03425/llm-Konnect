import pandas as pd

from app.analytics.tabular_query import answer_tabular_question


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
