import pandas as pd
import pytest
from app.schema.domain import registry, get_domain_pack
from app.schema.mapper import suggest_mapping
from app.schema.normalize import apply_mapping
from app.schema.ecommerce import EcommerceDomainPack


def test_ecommerce_pack_registered():
    pack = get_domain_pack("ecommerce")
    assert pack is not None
    assert pack.name == "ecommerce"
    assert "sale_amount" in pack.extra_fields
    assert "product_sku" in pack.extra_fields
    assert "refund_amount" in pack.extra_fields
    assert "rating" in pack.extra_fields


def test_ecommerce_schema_mapping_shopify_headers():
    pack = get_domain_pack("ecommerce")
    
    # Real Shopify export style column names
    raw_df = pd.DataFrame({
        "Order ID": ["#1001", "#1002"],
        "Lineitem sku": ["SKU-001", "SKU-002"],
        "Lineitem name": ["Wireless Earbuds", "Phone Case"],
        "Lineitem quantity": [1, 2],
        "Lineitem price": [49.99, 15.00],
        "Customer Email": ["john@example.com", "jane@example.com"],
        "Shipping City": ["Seattle", "Austin"],
        "Financial Status": ["paid", "paid"]
    })
    
    proposal = suggest_mapping(
        columns=list(raw_df.columns),
        sample_rows=raw_df.to_dict(orient="records"),
        domain_pack=pack
    )
    mapping = {
        s.source_column: s.canonical_field
        for s in proposal.suggestions
        if s.canonical_field is not None
    }
    
    mapped_df = apply_mapping(raw_df, mapping, domain="ecommerce")
    
    assert "order_id" in mapped_df.columns
    assert "product_sku" in mapped_df.columns
    assert "product_name" in mapped_df.columns
    assert "quantity" in mapped_df.columns
    assert "customer_email" in mapped_df.columns
    assert "shipping_city" in mapped_df.columns
    assert "payment_status" in mapped_df.columns


def test_ecommerce_row_to_text_generation():
    pack = get_domain_pack("ecommerce")
    row = {
        "order_id": "1055",
        "order_date": "2026-04-10",
        "product_name": "Premium Leather Wallet",
        "product_sku": "WAL-LTH-BLK",
        "category": "Accessories",
        "quantity": 1,
        "sale_amount": 75.0,
        "customer_name": "Alex Smith",
        "rating": 5,
        "review_text": "Top quality leather, fast shipping!"
    }
    
    text = pack.row_to_text(row)
    assert "Order: #1055" in text
    assert "Product: Premium Leather Wallet" in text
    assert "SKU: WAL-LTH-BLK" in text
    assert "Total: $75.0" in text
    assert "Customer: Alex Smith" in text
    assert "Rating: ★5" in text
    assert "Review: Top quality leather, fast shipping!" in text
