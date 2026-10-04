"""Test Suite 01: Header Mapping & Synonym Proposal Engine.

Module: Module 6.3 — Schema Mapping and Validation Module
Target File: backend/app/schema/mapper.py (functions: suggest_mapping, _normalize_header_string, _build_synonym_lookup, get_canonical_fields)
Scope:
- Verifies raw header string normalization (camelCase, snake_case, Urdu unicode retention, punctuation removal).
- Verifies domain synonym lookup dictionary construction.
- Verifies exact matching of core canonical fields (date, quantity, amount, etc.).
- Verifies domain-specific synonym matching (e.g. 'Medicine Name' -> product_id, 'MRP' -> unit_price).
- Verifies confidence scoring and identification of missing required canonical fields in MappingProposal.
"""

import pytest
from app.schema.mapper import suggest_mapping, _normalize_header_string, _build_synonym_lookup, get_canonical_fields
from app.schema.domain import get_domain_pack


class TestHeaderMappingAndSynonyms:
    """Verifies deterministic header matching and domain synonym resolution."""

    def test_normalize_header_strings(self):
        """Normalizes varied formatting while preserving Urdu letters and expanding cases."""
        # CamelCase to spaced
        assert _normalize_header_string("MedicineName") == "medicine name"
        assert _normalize_header_string("InvoiceTotalAmount") == "invoice total amount"

        # Snake_case to spaced
        assert _normalize_header_string("unit_cost_price") == "unit cost price"
        assert _normalize_header_string("Customer_ID_No") == "customer id no"

        # Punctuation stripping
        assert _normalize_header_string("Total (PKR)") == "total pkr"
        assert _normalize_header_string("Discount%") == "discount"

        # Unicode / Urdu script preservation
        assert "دوا" in _normalize_header_string("دوا کا نام")
        assert "قیمت" in _normalize_header_string("کل قیمت")

    def test_exact_canonical_core_field_matching(self):
        """Exact canonical field names receive 1.0 confidence."""
        headers = ["date", "product_id", "quantity", "amount", "invoice_id"]
        proposal = suggest_mapping(headers, sample_rows=[])

        assert len(proposal.suggestions) == 5
        sug_map = {s.source_column: s for s in proposal.suggestions}

        assert sug_map["date"].canonical_field == "date"
        assert sug_map["date"].confidence == 1.0
        assert sug_map["product_id"].canonical_field == "product_id"
        assert sug_map["quantity"].canonical_field == "quantity"
        assert sug_map["amount"].canonical_field == "amount"
        assert len(proposal.required_fields_missing) == 0

    def test_pharmacy_domain_synonym_matching(self):
        """Resolves common Pakistani pharmacy POS header variations to canonical fields."""
        pack = get_domain_pack("pharmacy")
        headers = [
            "Bill Date",
            "Medicine Name",
            "Qty Sold",
            "MRP",
            "Net Amount",
            "Bill Number",
            "Batch No",
            "Exp Date"
        ]

        proposal = suggest_mapping(headers, sample_rows=[], domain_pack=pack)
        sug_map = {s.source_column: s for s in proposal.suggestions}

        # Medicine Name -> product_id
        assert sug_map["Medicine Name"].canonical_field == "product_id"
        assert sug_map["Medicine Name"].confidence >= 0.9

        # Qty Sold -> quantity
        assert sug_map["Qty Sold"].canonical_field == "quantity"

        # MRP -> mrp (or unit_price in generic)
        assert sug_map["MRP"].canonical_field in ("mrp", "unit_price")

        # Net Amount -> amount
        assert sug_map["Net Amount"].canonical_field == "amount"

        # Bill Number -> invoice_id
        assert sug_map["Bill Number"].canonical_field == "invoice_id"

        # Batch No -> batch_no
        assert sug_map["Batch No"].canonical_field == "batch_no"

        # Exp Date -> expiry_date
        assert sug_map["Exp Date"].canonical_field == "expiry_date"

    def test_ecommerce_domain_synonym_matching(self):
        """Resolves e-commerce Shopify export headers to canonical fields."""
        pack = get_domain_pack("ecommerce")
        headers = [
            "Order ID",
            "Lineitem name",
            "Lineitem sku",
            "Lineitem quantity",
            "Lineitem price",
            "Total Price"
        ]

        proposal = suggest_mapping(headers, sample_rows=[], domain_pack=pack)
        sug_map = {s.source_column: s for s in proposal.suggestions}

        assert sug_map["Order ID"].canonical_field in ("order_id", "invoice_id")
        assert sug_map["Lineitem name"].canonical_field in ("product_name", "product_id")
        assert sug_map["Lineitem sku"].canonical_field in ("product_sku", "product_id")
        assert sug_map["Lineitem quantity"].canonical_field == "quantity"
        assert sug_map["Lineitem price"].canonical_field in ("sale_amount", "unit_price")

    def test_missing_required_fields_detection(self):
        """Accurately identifies required canonical fields that are missing in the proposal."""
        pack = get_domain_pack("pharmacy")
        # Incomplete headers containing invoice_id and quantity but missing Date and Amount
        incomplete_headers = ["Bill Number", "Sold Qty"]
        proposal = suggest_mapping(incomplete_headers, sample_rows=[], domain_pack=pack)

        assert "date" in proposal.required_fields_missing
        assert "amount" in proposal.required_fields_missing

    def test_unmapped_opaque_columns_remain_for_review(self):
        """Arbitrary non-standard columns with low match confidence remain unmapped."""
        headers = ["date", "amount", "XYZ_Random_Audit_Hash_9921"]
        proposal = suggest_mapping(headers, sample_rows=[])

        sug_map = {s.source_column: s for s in proposal.suggestions}
        opaque_sug = sug_map["XYZ_Random_Audit_Hash_9921"]
        assert opaque_sug.canonical_field is None
        assert opaque_sug.confidence < 0.5
