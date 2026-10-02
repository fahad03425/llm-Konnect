import pandas as pd

from app.analytics.tabular_query import answer_product_catalog_question, answer_tabular_question
from app.schema.domain import get_domain_pack
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping


def _catalog():
    raw = pd.DataFrame([
        {"Name": "Zestril", "Company": "ICI Pakistan Limited", "Price_before": "Rs 202", "Discount": "10% Off", "Price_After": "PKR 182", "Pack_Size": "1x14's", "Availability": "Available"},
        {"Name": "Zestril", "Company": "ICI Pakistan Limited", "Price_before": "Rs 388", "Discount": "10% Off", "Price_After": "PKR 350", "Pack_Size": "1x14's", "Availability": "Available"},
        {"Name": "Zestril", "Company": "ICI Pakistan Limited", "Price_before": "Rs 748", "Discount": "10% Off", "Price_After": "PKR 673", "Pack_Size": "1x14's", "Availability": "Sold Out"},
        {"Name": "Test Medicine", "Company": "Other Pharma", "Price_before": "Rs 600", "Discount": None, "Price_After": "PKR 600", "Pack_Size": "2x10", "Availability": "Available"},
    ])
    mapping = map_headers(list(raw.columns), get_domain_pack("pharmacy"))
    return apply_mapping(raw, mapping, domain="pharmacy", keep_extras=True).assign(source_row=[2, 3, 4, 5])


def test_pricing_catalog_columns_map_to_distinct_canonical_fields():
    frame = _catalog()

    assert frame.original_price.tolist() == [202.0, 388.0, 748.0, 600.0]
    assert frame.discounted_price.tolist() == [182.0, 350.0, 673.0, 600.0]
    assert frame.discount_pct.iloc[:3].tolist() == [10.0, 10.0, 10.0]
    assert pd.isna(frame.discount_pct.iloc[3])
    assert frame.availability.tolist() == ["Available", "Available", "Sold Out", "Available"]
    assert "quantity" not in frame


def test_product_price_lookup_returns_all_price_points_and_citations():
    result = answer_tabular_question("What is the price of Zestril?", _catalog())

    assert "PKR 182" in result["answer"]
    assert "PKR 350" in result["answer"]
    assert "PKR 673" in result["answer"]
    assert result["source_rows"] == [2, 3, 4]


def test_catalog_filters_price_and_availability_without_losing_provenance():
    result = answer_product_catalog_question("Show available products under PKR 500", _catalog())

    assert result["values"]["matching_records"] == 2
    assert "Zestril" in result["answer"]
    assert result["source_rows"] == [2, 3]


def test_catalog_missing_deictic_product_requests_clarification():
    result = answer_product_catalog_question("Show me the details of this medicine", _catalog())

    assert result["values"]["status"] == "needs_product_context"
    assert result["source_rows"] == []


def test_catalog_discounted_price_mean_is_not_mistaken_for_discount_rate_mean():
    result = answer_product_catalog_question("What is the average price after discount?", _catalog())

    assert "average discounted product price" in result["answer"].casefold()
    assert "PKR 451.25" in result["answer"]


def test_catalog_repair_swaps_only_unambiguous_misplaced_availability_values():
    raw = pd.DataFrame([
        {"Name": "A", "Company": "X", "Price_before": "Rs 100", "Discount": "10% Off", "Price_After": "Rs 90", "Pack_Size": "Add to cart", "Availability": "1x10"},
        {"Name": "B", "Company": "Y", "Price_before": "Rs 100", "Discount": "10% Off", "Price_After": "Rs 90", "Pack_Size": "2x10", "Availability": "Sold Out"},
    ])
    frame = apply_mapping(raw, map_headers(list(raw.columns), get_domain_pack("pharmacy")), domain="pharmacy", keep_extras=True)

    assert frame.loc[0, "pack_size"] == "1x10"
    assert frame.loc[0, "availability"] == "Add to cart"
    assert frame.loc[1, "pack_size"] == "2x10"
    assert frame.loc[1, "availability"] == "Sold Out"
    frame["source_row"] = [2, 3]
    result = answer_product_catalog_question("What percentage is available?", frame)
    assert result["values"]["available_records"] == 1


def test_percentage_available_uses_whole_catalog_not_top_company_table():
    result = answer_product_catalog_question("What percentage of products are currently available?", _catalog())

    assert result["values"]["available_records"] == 3
    assert result["values"]["available_distinct_products"] == 2
    assert "3 of 4 product records" in result["answer"]
    assert "2 of 2 distinct product names" in result["answer"]


def test_highest_product_price_recognizes_intervening_product_word_and_cites_tie():
    result = answer_product_catalog_question("What is the highest product price?", _catalog())

    assert "PKR 673" in result["answer"]
    assert result["source_rows"] == [4]


def test_significant_price_gap_returns_ranked_savings_not_overall_mean():
    result = answer_product_catalog_question("Which products have a large difference between original and discounted price?", _catalog())

    assert result["values"]["ranking_only"] is True
    assert "Largest listed" in result["answer"]


def test_missing_specific_company_is_clarified_before_global_cheapest_query():
    result = answer_product_catalog_question("What are the cheapest products from a specific company?", _catalog())

    assert result["values"]["status"] == "needs_company_context"


def test_catalog_row_text_renders_percent_after_the_value():
    pack = get_domain_pack("pharmacy")

    text = pack.row_to_text({"product_id": "Zestril", "discount_pct": 10.0})

    assert "Discount: 10.0%" in text
    assert "Discount: % 10.0" not in text


def test_which_company_aggregate_is_not_misread_as_missing_company_entity():
    frame = _catalog()
    result = answer_product_catalog_question("Which company has the most available products?", frame)

    assert result["values"].get("status") != "needs_company_context"
    assert "Company catalog comparison" in result["answer"]


def test_manufacturer_lookup_for_named_product_does_not_request_company_name():
    result = answer_product_catalog_question("Which company manufactures Zestril?", _catalog())

    assert "ICI Pakistan Limited" in result["answer"]
    assert result["values"].get("status") != "needs_company_context"


def test_top_ten_extrema_respects_count_in_natural_word_order():
    result = answer_product_catalog_question("Show the 10 most expensive products", _catalog())

    assert len(result["values"]["ranked_products"]) == 4
    assert result["values"]["tie_count"] == 4


def test_average_price_by_company_is_not_collapsed_to_global_mean():
    result = answer_product_catalog_question("Which company has the highest average product price?", _catalog())

    assert result["values"].get("average_original_price") is None
    assert "Test Medicine" not in result["answer"]


def test_single_typo_in_catalog_name_is_corrected_transparently():
    result = answer_product_catalog_question("What's the price of Zestil?", _catalog())

    assert result["values"]["fuzzy_name_match"] is True
    assert result["values"]["product"] == "Zestril"
    assert "interpreted the product name as Zestril" in result["answer"]


def test_strength_specific_question_abstains_when_source_has_no_strength_field():
    result = answer_product_catalog_question("What is the price of Zestril 10 mg?", _catalog())

    assert result["values"]["status"] == "unsupported_strength"
    assert "does not identify a 10 mg strength" in result["answer"]


def test_explicit_two_product_comparison_keeps_both_entities_and_citations():
    result = answer_product_catalog_question("Compare the prices of Zestril vs Test Medicine", _catalog())

    assert "Zestril:" in result["answer"]
    assert "Test Medicine:" in result["answer"]
    assert set(result["source_rows"]) == {2, 3, 4, 5}


def test_affordable_uses_and_discloses_catalog_average_when_no_cutoff_is_given():
    result = answer_product_catalog_question("What affordable medicines are currently available?", _catalog())

    assert result["values"]["relative_price_threshold"] == 451.25
    assert "catalog mean discounted price" in result["answer"]
    assert "Test Medicine" not in result["answer"]
