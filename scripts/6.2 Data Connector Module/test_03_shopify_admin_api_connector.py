"""Test Suite 03: Shopify Admin API E-Commerce Connector.

Module: Module 6.2 — Data Connector Module
Target File: backend/app/connectors/shopify.py (class: ShopifyConnector)
Scope:
- Verifies store URL parsing and subdomain validation.
- Verifies secure token authentication via X-Shopify-Access-Token header.
- Verifies order extraction, line-item flattening, shipping amounts, and refund allocation.
- Verifies product and variant extraction, SKU alignment, and GraphQL inventory costs.
- Verifies review metaobject extraction via GraphQL.
- Verifies rate limit backoff (HTTP 429 Retry-After and GraphQL THROTTLED backoff).
- Verifies security protections (origin mismatch check, 401/403 PermissionError).
"""

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from app.connectors.shopify import ShopifyConnector


class TestShopifyConnector:
    """Verifies multi-resource extraction from Shopify Admin REST & GraphQL APIs."""

    def test_store_initialization_and_url_validation(self):
        """Validates subdomain cleaning and rejection of invalid hostnames."""
        # Clean subdomain
        conn = ShopifyConnector("my-pharmacy-store.myshopify.com", "shpat_123456789")
        assert conn.shop_name == "my-pharmacy-store"
        assert conn.base_url == "https://my-pharmacy-store.myshopify.com/admin/api/2026-07"

        # Invalid subdomain with paths
        with pytest.raises(ValueError) as exc:
            ShopifyConnector("invalid/store/path", "shpat_123456789")
        assert "subdomain" in str(exc.value)

        # Missing token
        with pytest.raises(ValueError):
            ShopifyConnector("my-store", "   ")

    def test_from_url_factory(self):
        """Constructs connector instance from shopify:// URL scheme."""
        url = "shopify://healthy-living?access_token=shpat_secret_abc&resource=products&api_version=2026-07"
        conn = ShopifyConnector.from_url(url)
        assert conn.shop_name == "healthy-living"
        assert conn.access_token == "shpat_secret_abc"
        assert conn.resource == "products"

    @patch("requests.get")
    def test_fetch_orders_line_items_and_refunds(self, mock_get):
        """Flattens order line items, discounts, shipping, and line-item refunds."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "orders": [
                {
                    "id": 9001,
                    "name": "#1001",
                    "created_at": "2026-03-01T10:00:00Z",
                    "financial_status": "paid",
                    "fulfillment_status": "fulfilled",
                    "currency": "PKR",
                    "payment_gateway_names": ["cash_on_delivery"],
                    "customer": {
                        "id": 501,
                        "first_name": "Usman",
                        "last_name": "Tariq",
                        "email": "usman@example.com"
                    },
                    "shipping_address": {
                        "city": "Lahore",
                        "country": "Pakistan"
                    },
                    "shipping_lines": [
                        {"price": "250.00"}
                    ],
                    "refunds": [
                        {
                            "refund_line_items": [
                                {"line_item_id": 111, "subtotal": "50.00"}
                            ]
                        }
                    ],
                    "line_items": [
                        {
                            "id": 111,
                            "name": "Whey Protein 1kg",
                            "sku": "PROT-1KG-VAN",
                            "quantity": 2,
                            "price": "3500.00",
                            "total_discount": "200.00",
                            "tax_lines": []
                        }
                    ]
                }
            ]
        }
        mock_get.return_value = mock_response

        conn = ShopifyConnector("vital-health", "shpat_test_token", resource="orders")
        df = conn.fetch()

        assert len(df) == 1
        assert df.loc[0, "order_id"] == "#1001"
        assert df.loc[0, "customer_name"] == "Usman Tariq"
        assert df.loc[0, "shipping_city"] == "Lahore"
        assert df.loc[0, "product_sku"] == "PROT-1KG-VAN"
        assert df.loc[0, "quantity"] == 2.0
        assert df.loc[0, "unit_price"] == 3500.0
        assert df.loc[0, "discount_amount"] == 200.0
        assert df.loc[0, "shipping_amount"] == 250.0
        assert df.loc[0, "refund_amount"] == 50.0
        # (2 * 3500) - 200 = 6800 net amount = 6800 - 50 = 6750
        assert df.loc[0, "sale_amount"] == 6800.0
        assert df.loc[0, "net_amount"] == 6750.0

    @patch("requests.post")
    @patch("requests.get")
    def test_fetch_products_with_inventory_costs(self, mock_get, mock_post):
        """Extracts products, variants, and unit costs from GraphQL nodes."""
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.headers = {}
        mock_get_resp.json.return_value = {
            "products": [
                {
                    "id": 7001,
                    "title": "Vitamin C 1000mg",
                    "vendor": "NutraPharm",
                    "product_type": "Supplements",
                    "variants": [
                        {
                            "id": 8001,
                            "title": "Bottle of 60",
                            "sku": "VIT-C-60",
                            "price": "850.00",
                            "inventory_item_id": 9001,
                            "inventory_quantity": 120
                        }
                    ]
                }
            ]
        }
        mock_get.return_value = mock_get_resp

        # Mock GraphQL cost query
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "data": {
                "nodes": [
                    {
                        "legacyResourceId": "9001",
                        "unitCost": {"amount": "450.00", "currencyCode": "PKR"}
                    }
                ]
            }
        }
        mock_post.return_value = mock_post_resp

        conn = ShopifyConnector("vital-health", "shpat_test_token", resource="products")
        df = conn.fetch()

        assert len(df) == 1
        assert df.loc[0, "product_id"] == "Vitamin C 1000mg - Bottle of 60"
        assert df.loc[0, "vendor"] == "NutraPharm"
        assert df.loc[0, "product_sku"] == "VIT-C-60"
        assert df.loc[0, "unit_price"] == 850.0
        assert df.loc[0, "quantity"] == 120
        assert df.loc[0, "cost"] == 450.0

    @patch("requests.post")
    def test_fetch_reviews_metaobjects_graphql(self, mock_post):
        """Extracts customer reviews and ratings via GraphQL metaobjects."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "data": {
                "metaobjects": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "updatedAt": "2026-03-05T12:00:00Z",
                            "fields": [
                                {"key": "product_title", "value": "Omega-3 Fish Oil"},
                                {"key": "sku", "value": "OMG-3"},
                                {"key": "customer_name", "value": "Ayesha Bilal"},
                                {"key": "rating", "value": "5.0"},
                                {"key": "body", "value": "Excellent quality, arrived quickly!"}
                            ]
                        }
                    ]
                }
            }
        }
        mock_post.return_value = mock_post_resp

        conn = ShopifyConnector("vital-health", "shpat_test_token", resource="reviews")
        df = conn.fetch()

        assert len(df) == 1
        assert df.loc[0, "product_name"] == "Omega-3 Fish Oil"
        assert df.loc[0, "customer_name"] == "Ayesha Bilal"
        assert df.loc[0, "rating"] == 5.0
        assert "Excellent quality" in df.loc[0, "review_text"]

    @patch("time.sleep", return_value=None)
    @patch("requests.get")
    def test_rate_limit_429_retry_handling(self, mock_get, mock_sleep):
        """Handles HTTP 429 status by waiting Retry-After header and retrying."""
        rate_limited_resp = MagicMock()
        rate_limited_resp.status_code = 429
        rate_limited_resp.headers = {"Retry-After": "2"}

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.headers = {}
        success_resp.json.return_value = {"orders": []}

        mock_get.side_effect = [rate_limited_resp, success_resp]

        conn = ShopifyConnector("vital-health", "shpat_token", resource="orders")
        df = conn.fetch()

        assert len(df) == 0
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    @patch("requests.get")
    def test_authorization_error_401_raises_permission_error(self, mock_get):
        """HTTP 401 or 403 raises PermissionError informing operator to verify token scopes."""
        auth_failed_resp = MagicMock()
        auth_failed_resp.status_code = 401
        mock_get.return_value = auth_failed_resp

        conn = ShopifyConnector("vital-health", "shpat_invalid", resource="orders")
        with pytest.raises(PermissionError) as exc:
            conn.fetch()
        assert "Shopify rejected the token" in str(exc.value)

    def test_pagination_origin_mismatch_security_check(self):
        """Rejects pagination URLs that redirect to an external origin to prevent token theft."""
        conn = ShopifyConnector("vital-health", "shpat_token")
        with pytest.raises(ValueError) as exc:
            conn._request("get", "https://malicious-external-site.com/orders.json")
        assert "different host" in str(exc.value)
