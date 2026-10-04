import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from app.connectors.shopify import ShopifyConnector


@patch('requests.get')
def test_shopify_ecommerce_orders(mock_requests_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = {
        "orders": [
            {
                "id": 5001,
                "name": "#1001",
                "created_at": "2026-03-15T14:30:00Z",
                "financial_status": "paid",
                "fulfillment_status": "fulfilled",
                "payment_gateway_names": ["shopify_payments", "stripe"],
                "total_discounts": "5.00",
                "customer": {
                    "id": 9901,
                    "first_name": "Sarah",
                    "last_name": "Connor",
                    "email": "sarah@example.com"
                },
                "shipping_address": {
                    "city": "Los Angeles",
                    "country": "United States"
                },
                "shipping_lines": [
                    {"price": "10.00"}
                ],
                "refunds": [
                    {"refund_line_items": [{"subtotal": "0.00"}]}
                ],
                "line_items": [
                    {
                        "name": "Organic Cotton Hoodie",
                        "sku": "HOOD-ORG-BLK-M",
                        "variant_title": "Black / M",
                        "quantity": 2,
                        "price": "60.00",
                        "total_discount": "5.00",
                        "tax_lines": [{"price": "9.20"}]
                    }
                ]
            }
        ]
    }
    mock_requests_get.return_value = mock_response

    conn = ShopifyConnector("my-apparel-store", "shpat_test_token")
    df = conn.fetch("orders")

    assert len(df) == 1
    assert df.loc[0, "order_id"] == "#1001"
    assert df.loc[0, "customer_name"] == "Sarah Connor"
    assert df.loc[0, "customer_email"] == "sarah@example.com"
    assert df.loc[0, "shipping_city"] == "Los Angeles"
    assert df.loc[0, "product_sku"] == "HOOD-ORG-BLK-M"
    assert df.loc[0, "quantity"] == 2.0
    assert df.loc[0, "unit_price"] == 60.0
    assert df.loc[0, "sale_amount"] == 115.0  # (2*60) - 5
    assert df.loc[0, "gross_amount"] == 120.0
    assert df.loc[0, "discount_amount"] == 5.0
    assert df.loc[0, "tax_amount"] == 9.20
    assert df.loc[0, "payment_status"] == "paid"
    assert df.loc[0, "fulfillment_status"] == "fulfilled"


@patch('requests.get')
def test_shopify_ecommerce_products(mock_requests_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = {
        "products": [
            {
                "title": "Ergonomic Desk Chair",
                "vendor": "ComfortSeating",
                "product_type": "Office Furniture",
                "variants": [
                    {
                        "title": "Grey Fabric",
                        "sku": "CHR-ERGO-GRY",
                        "price": "299.00",
                        "compare_at_price": "150.00",
                        "inventory_quantity": 45
                    }
                ]
            }
        ]
    }
    mock_requests_get.return_value = mock_response

    conn = ShopifyConnector("my-furniture-store", "shpat_test_token")
    df = conn.fetch("products")

    assert len(df) == 1
    assert df.loc[0, "product_name"] == "Ergonomic Desk Chair"
    assert df.loc[0, "product_sku"] == "CHR-ERGO-GRY"
    assert df.loc[0, "unit_price"] == 299.0
    assert pd.isna(df.loc[0, "cost_per_item"])
    assert df.loc[0, "compare_at_price"] == 150.0
    assert df.loc[0, "quantity"] == 45


@patch('requests.post')
def test_shopify_ecommerce_reviews(mock_requests_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = {"data": {
        "metaobjects": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {
                "fields": [
                    {"key": "product_title", "value": "Ergonomic Desk Chair"},
                    {"key": "sku", "value": "CHR-ERGO-GRY"},
                    {"key": "author", "value": "Michael Scott"},
                    {"key": "rating", "value": "5"},
                    {"key": "body", "value": "Best office chair I have ever used."}
                ],
                "updatedAt": "2026-03-20T10:00:00Z"
            }
        ]}
    }}
    mock_requests_get.return_value = mock_response

    conn = ShopifyConnector("my-furniture-store", "shpat_test_token")
    df = conn.fetch("reviews")

    assert len(df) == 1
    assert df.loc[0, "product_name"] == "Ergonomic Desk Chair"
    assert df.loc[0, "customer_name"] == "Michael Scott"
    assert df.loc[0, "rating"] == 5.0
    assert "Best office chair" in df.loc[0, "review_text"]
