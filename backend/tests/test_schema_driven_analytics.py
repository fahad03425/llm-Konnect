import pandas as pd

from app.analytics.tabular_query import answer_tabular_question
from app.rag.router import RouteType, classify_route
from app.schema.domain import get_domain_pack
from app.schema.mapper import suggest_mapping
from app.schema.normalize import apply_mapping


def mapped_sample():
    raw = pd.DataFrame([
        {"Transaction_UUID": "T-1", "Product_ID": "SKU-01", "Product_Name": "Alpha 10mg Tablet", "Therapeutic_Class": "Class A", "Manufacturer": "Pharma A", "Pharmacy_Branch": "North", "Quantity_Sold": 2, "Total_Transaction_Value_USD": 20.0, "Unit_Cost_Price_USD": 4.0, "Maximum_Retail_Price_USD": 10.0, "Payment_Method": "Cash Checkout", "Prescription_Required": "Yes - RX"},
        {"Transaction_UUID": "T-2", "Product_ID": "SKU-01", "Product_Name": "Alpha 10mg Tablet", "Therapeutic_Class": "Class A", "Manufacturer": "Pharma A", "Pharmacy_Branch": "North", "Quantity_Sold": 1, "Total_Transaction_Value_USD": 10.0, "Unit_Cost_Price_USD": 4.0, "Maximum_Retail_Price_USD": 10.0, "Payment_Method": "Insurance", "Prescription_Required": "Yes - RX"},
        {"Transaction_UUID": "T-3", "Product_ID": "SKU-02", "Product_Name": "Beta 20mg Capsule", "Therapeutic_Class": "Class B", "Manufacturer": "Pharma B", "Pharmacy_Branch": "South", "Quantity_Sold": 8, "Total_Transaction_Value_USD": 80.0, "Unit_Cost_Price_USD": 2.0, "Maximum_Retail_Price_USD": 10.0, "Payment_Method": "Cash Checkout", "Prescription_Required": "No - OTC Sale"},
    ])
    proposal = suggest_mapping(list(raw.columns), raw.head(5).to_dict("records"), get_domain_pack("pharmacy"))
    mapping = {s.source_column: s.canonical_field for s in proposal.suggestions if s.canonical_field}
    return apply_mapping(raw, mapping, domain="pharmacy", keep_extras=True)


def test_compound_headers_keep_id_name_and_measure_columns_distinct():
    frame = mapped_sample()
    assert frame.product_id.tolist() == ["Alpha 10mg Tablet", "Alpha 10mg Tablet", "Beta 20mg Capsule"]
    assert frame.product_code.tolist() == ["SKU-01", "SKU-01", "SKU-02"]
    assert frame.amount.sum() == 110.0
    assert frame.quantity.sum() == 11
    assert frame.category.tolist() == ["Class A", "Class A", "Class B"]


def test_unseen_export_aliases_map_to_same_canonical_analytics_schema():
    raw = pd.DataFrame([
        {"SKU": "A-1", "Medicine_Name": "Alpha 10mg Tablet", "Therapeutic_Class": "Class A", "Manufacturer": "Pharma A", "Branch_Name": "North", "Units_Sold": 2, "Sales_Value_USD": 20.0},
        {"SKU": "A-1", "Medicine_Name": "Alpha 10mg Tablet", "Therapeutic_Class": "Class A", "Manufacturer": "Pharma A", "Branch_Name": "North", "Units_Sold": 1, "Sales_Value_USD": 10.0},
        {"SKU": "B-2", "Medicine_Name": "Beta 20mg Capsule", "Therapeutic_Class": "Class B", "Manufacturer": "Pharma B", "Branch_Name": "South", "Units_Sold": 8, "Sales_Value_USD": 80.0},
    ])
    proposal = suggest_mapping(list(raw.columns), raw.to_dict("records"), get_domain_pack("pharmacy"))
    mapping = {s.source_column: s.canonical_field for s in proposal.suggestions if s.canonical_field}
    frame = apply_mapping(raw, mapping, domain="pharmacy", keep_extras=True)
    assert frame.product_id.tolist() == ["Alpha 10mg Tablet", "Alpha 10mg Tablet", "Beta 20mg Capsule"]
    assert frame.product_code.tolist() == ["A-1", "A-1", "B-2"]
    result = answer_tabular_question("Which therapeutic class generates the most revenue?", frame)
    assert result["values"]["results"][0]["category"] == "Class B"
    assert result["values"]["results"][0]["value"] == 80.0


def test_grouped_metrics_use_requested_measure_and_full_frame():
    frame = mapped_sample()
    result = answer_tabular_question("Which therapeutic class generates the most revenue?", frame)
    assert result["values"]["measure"] == "sales revenue"
    assert result["values"]["results"][0]["category"] == "Class B"
    assert result["values"]["results"][0]["value"] == 80.0


def test_transaction_count_and_average_value_are_not_confused_with_units():
    frame = mapped_sample()
    count = answer_tabular_question("How many transactions are in the dataset?", frame)
    average = answer_tabular_question("What is the average transaction value?", frame)
    assert count["values"]["value"] == 3
    assert average["values"]["value"] == (20.0 + 10.0 + 80.0) / 3


def test_named_company_total_does_not_turn_into_per_product_output():
    frame = mapped_sample()
    result = answer_tabular_question("How much revenue came from Pharma A products?", frame)
    assert result["values"]["value"] == 30.0
    assert "by product" not in result["answer"].casefold()


def test_nested_request_groups_by_the_parent_after_each():
    frame = mapped_sample()
    result = answer_tabular_question("Which therapeutic class performs best at each branch?", frame)
    assert "Sales Revenue by branch and category" in result["answer"]
    assert "North: Class A" in result["answer"]
    assert "South: Class B" in result["answer"]


def test_prescription_filter_and_cash_insurance_comparison_are_bounded():
    frame = mapped_sample()
    rx = answer_tabular_question("How much revenue from prescription-required medicines?", frame)
    compare = answer_tabular_question("Compare cash and insurance sales.", frame)
    assert rx["values"]["value"] == 30.0
    assert len(compare["values"]["results"]) == 2
    assert {x["payment_method"] for x in compare["values"]["results"]} == {"Cash Checkout", "Insurance"}


def test_unqualified_pharmacy_metric_language_routes_to_structured_analytics():
    for question in (
        "Which pharmacist handled the most transactions?",
        "What is the cost price of Alpha 10mg Tablet?",
        "Which products are stored on Rack-A-Shelf-1?",
        "How many batches are associated with a particular medicine?",
    ):
        assert classify_route(question) == RouteType.ANALYTICS
