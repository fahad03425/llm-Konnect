import pandas as pd
import time
import os
from typing import Optional, List, Dict, Any
from app.connectors.base import Connector


class ShopifyConnector(Connector):
    """
    Shopify Connector for LLM-Konnect.
    Supports multi-resource extraction (orders, line items, products, variants,
    inventory, customers, reviews) with rate-limit handling and schema alignment.
    """

    def __init__(self, shop_name: str, access_token: str, api_version: str = "2025-01"):
        """
        :param shop_name: The shop name (e.g. 'my-store' from my-store.myshopify.com)
        :param access_token: Admin API access token (shpat_...)
        :param api_version: Shopify Admin API version
        """
        self.shop_name = shop_name.replace(".myshopify.com", "").strip()
        self.access_token = access_token.strip()
        self.api_version = api_version
        self.base_url = f"https://{self.shop_name}.myshopify.com/admin/api/{self.api_version}"
        
        try:
            import requests
            self.requests = requests
        except ImportError:
            raise ImportError(
                "The 'requests' package is required for the Shopify connector. "
                "Please install it using: pip install requests"
            )

    @classmethod
    def from_url(cls, url: str) -> "ShopifyConnector":
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        shop_name = parsed.netloc or parsed.path.lstrip("/")
        query_params = urllib.parse.parse_qs(parsed.query)
        access_token = query_params.get("access_token", [""])[0]
        api_version = query_params.get("api_version", ["2025-01"])[0]
        return cls(shop_name=shop_name, access_token=access_token, api_version=api_version)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json"
        }

    def _paginate_request(self, endpoint: str, limit: int = 50, max_pages: Optional[int] = None) -> List[Dict]:
        """Fetch all pages of a Shopify resource, handling 429 rate limits."""
        url = f"{self.base_url}/{endpoint}"
        separator = "&" if "?" in endpoint else "?"
        params = {"limit": limit}
        results = []
        pages_fetched = 0
        
        while url:
            if max_pages and pages_fetched >= max_pages:
                break
                
            response = self.requests.get(url, headers=self._get_headers(), params=params)
            
            if response.status_code == 429:
                # Rate limit hit, back off
                retry_after = float(response.headers.get("Retry-After", 2.0))
                time.sleep(retry_after)
                continue
                
            response.raise_for_status()
            data = response.json()
            
            # Find the primary list in the response dict
            for key, val in data.items():
                if isinstance(val, list):
                    results.extend(val)
                    break
                    
            pages_fetched += 1
            
            # Check Link header for pagination
            link_header = response.headers.get("Link")
            url = None
            params = {}  # params are included in the link header url
            if link_header:
                links = link_header.split(',')
                for link in links:
                    if 'rel="next"' in link:
                        url = link[link.find("<")+1:link.find(">")]
                        break
                        
        return results

    def _fetch_orders(self, max_pages: Optional[int] = None) -> pd.DataFrame:
        """
        Fetch Shopify orders and flatten line items, customer details, refunds, and discounts.
        Maintains backward compatibility with generic/pharmacy columns (invoice_id, amount, product_id)
        while populating canonical e-commerce columns.
        """
        orders_raw = self._paginate_request("orders.json?status=any", max_pages=max_pages)
        
        flattened = []
        for order in orders_raw:
            customer = order.get("customer") or {}
            shipping_addr = order.get("shipping_address") or order.get("billing_address") or {}
            
            cust_first = customer.get("first_name", "") or ""
            cust_last = customer.get("last_name", "") or ""
            cust_name = f"{cust_first} {cust_last}".strip() or None
            
            # Total refund amount across order refunds
            refunds = order.get("refunds") or []
            total_refund = 0.0
            for r in refunds:
                for r_line in r.get("refund_line_items", []):
                    total_refund += float(r_line.get("subtotal", 0.0) or 0.0)
                for tx in r.get("transactions", []):
                    if tx.get("kind") == "refund" and tx.get("status") == "success":
                        pass

            gateways = order.get("payment_gateway_names", []) or []
            gateway_str = ", ".join(gateways) if isinstance(gateways, list) else str(gateways)
            
            total_shipping = 0.0
            for ship_line in order.get("shipping_lines", []):
                total_shipping += float(ship_line.get("price", 0.0) or 0.0)

            base_info = {
                # Legacy / Generic compatibility
                "invoice_id": order.get("name") or str(order.get("id")),
                "date": order.get("created_at"),
                "customer_id": str(customer.get("id")) if customer.get("id") else None,
                "payment_method": gateway_str,
                "txn_type": "sale",
                "discount_order": order.get("total_discounts"),
                
                # E-Commerce Canonical Fields
                "order_id": order.get("name") or str(order.get("id")),
                "order_date": order.get("created_at"),
                "customer_name": cust_name,
                "customer_email": customer.get("email") or order.get("email"),
                "shipping_city": shipping_addr.get("city"),
                "shipping_country": shipping_addr.get("country"),
                "payment_gateway": gateway_str,
                "payment_status": order.get("financial_status"),
                "fulfillment_status": order.get("fulfillment_status") or "unfulfilled",
                "shipping_amount": total_shipping,
                "refund_amount": total_refund,
                "order_currency": order.get("currency", "USD")
            }
            
            line_items = order.get("line_items", [])
            if not line_items:
                # If an order has no line items, output one order-level row
                row = base_info.copy()
                row["product_id"] = "Order Total"
                row["product_name"] = "Order Total"
                row["quantity"] = 1
                row["unit_price"] = float(order.get("total_price", 0.0) or 0.0)
                row["amount"] = float(order.get("total_price", 0.0) or 0.0)
                row["sale_amount"] = row["amount"]
                row["discount"] = float(order.get("total_discounts", 0.0) or 0.0)
                row["discount_amount"] = row["discount"]
                row["tax"] = float(order.get("total_tax", 0.0) or 0.0)
                row["tax_amount"] = row["tax"]
                flattened.append(row)
                continue

            for item in line_items:
                row = base_info.copy()
                item_name = item.get("name") or item.get("title")
                row["product_id"] = item_name
                row["product_name"] = item_name
                row["product_sku"] = item.get("sku") or item_name
                row["variant_title"] = item.get("variant_title")
                
                qty = float(item.get("quantity", 1) or 1)
                unit_price = float(item.get("price", 0.0) or 0.0)
                line_discount = float(item.get("total_discount", 0.0) or 0.0)
                line_tax = sum([float(t.get("price", 0)) for t in item.get("tax_lines", [])])
                
                row["quantity"] = qty
                row["unit_price"] = unit_price
                row["discount"] = line_discount
                row["discount_amount"] = line_discount
                row["tax"] = line_tax
                row["tax_amount"] = line_tax
                
                line_amount = (qty * unit_price) - line_discount
                row["amount"] = line_amount
                row["sale_amount"] = line_amount
                row["gross_amount"] = qty * unit_price
                row["net_amount"] = line_amount - (total_refund / max(len(line_items), 1))
                
                flattened.append(row)
                
        df = pd.DataFrame(flattened)
        return df

    def _fetch_products(
        self,
        max_pages: Optional[int] = None,
        pull_metafields: bool = False,
        metafield_mapping: Optional[Dict[str, str]] = None
    ) -> pd.DataFrame:
        """
        Fetch Shopify products, variants, inventory levels, and unit costs.
        """
        products_raw = self._paginate_request("products.json", max_pages=max_pages)
        
        flattened = []
        for prod in products_raw:
            base_info = {
                "product_id": prod.get("title"),
                "product_name": prod.get("title"),
                "manufacturer": prod.get("vendor"),
                "vendor": prod.get("vendor"),
                "category": prod.get("product_type"),
                "product_type": prod.get("product_type"),
                "tags": prod.get("tags"),
                "status": prod.get("status", "active")
            }
            
            meta_dict = {}
            if pull_metafields and metafield_mapping:
                product_id = prod.get("id")
                try:
                    meta_raw = self._paginate_request(f"products/{product_id}/metafields.json", max_pages=1)
                    for m in meta_raw:
                        key = f"{m.get('namespace')}.{m.get('key')}"
                        if key in metafield_mapping:
                            meta_dict[metafield_mapping[key]] = m.get("value")
                except Exception:
                    pass
            
            variants = prod.get("variants", [])
            if not variants:
                row = base_info.copy()
                row["product_sku"] = None
                row["quantity"] = 0
                row["unit_price"] = 0.0
                row.update(meta_dict)
                flattened.append(row)
                continue

            for variant in variants:
                row = base_info.copy()
                var_title = variant.get("title")
                if var_title and var_title != "Default Title":
                    row["product_id"] = f"{prod.get('title')} - {var_title}"
                    row["variant_title"] = var_title
                else:
                    row["variant_title"] = None
                    
                row["product_sku"] = variant.get("sku") or variant.get("barcode") or row["product_id"]
                row["barcode"] = variant.get("barcode")
                
                price_val = float(variant.get("price", 0.0) or 0.0)
                cost_val = float(variant.get("compare_at_price", 0.0) or 0.0) if variant.get("compare_at_price") else None
                
                row["unit_price"] = price_val
                row["quantity"] = variant.get("inventory_quantity", 0)
                row["stock_qty"] = variant.get("inventory_quantity", 0)
                row["cost"] = cost_val
                row["cost_per_item"] = cost_val
                
                row.update(meta_dict)
                flattened.append(row)
                
        df = pd.DataFrame(flattened)
        return df

    def _fetch_reviews(self, max_pages: Optional[int] = None) -> pd.DataFrame:
        """
        Fetch reviews or customer feedback metaobjects/ratings from Shopify.
        """
        # Try fetching reviews metaobjects or metafields
        try:
            metaobjects_raw = self._paginate_request("metaobjects.json?type=review", max_pages=max_pages)
        except Exception:
            metaobjects_raw = []

        flattened = []
        for obj in metaobjects_raw:
            fields = {f.get("key"): f.get("value") for f in obj.get("fields", [])}
            flattened.append({
                "product_name": fields.get("product_title") or fields.get("product"),
                "product_sku": fields.get("sku"),
                "customer_name": fields.get("author") or fields.get("customer_name"),
                "rating": float(fields.get("rating", 5.0) or 5.0),
                "review_text": fields.get("body") or fields.get("comment") or fields.get("text"),
                "date": obj.get("updated_at") or obj.get("created_at")
            })

        if not flattened:
            # Return empty DataFrame with expected schema
            return pd.DataFrame(columns=["product_name", "product_sku", "customer_name", "rating", "review_text", "date"])

        return pd.DataFrame(flattened)

    def _fetch_customers(self, max_pages: Optional[int] = None) -> pd.DataFrame:
        """
        Fetch customer directory with order counts and total spent.
        """
        customers_raw = self._paginate_request("customers.json", max_pages=max_pages)
        flattened = []
        for cust in customers_raw:
            first = cust.get("first_name") or ""
            last = cust.get("last_name") or ""
            flattened.append({
                "customer_id": str(cust.get("id")),
                "customer_name": f"{first} {last}".strip() or None,
                "customer_email": cust.get("email"),
                "orders_count": cust.get("orders_count", 0),
                "total_spent": float(cust.get("total_spent", 0.0) or 0.0),
                "city": (cust.get("default_address") or {}).get("city"),
                "country": (cust.get("default_address") or {}).get("country"),
                "created_at": cust.get("created_at")
            })
        return pd.DataFrame(flattened)

    def fetch(
        self,
        resource: str = "orders",
        pull_metafields: bool = False,
        metafield_mapping: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        Fetch data from Shopify.
        :param resource: 'orders', 'products', 'reviews', or 'customers'
        """
        resource_lower = resource.lower().strip()
        if resource_lower == "orders":
            df = self._fetch_orders()
        elif resource_lower == "products":
            df = self._fetch_products(pull_metafields=pull_metafields, metafield_mapping=metafield_mapping)
        elif resource_lower == "reviews":
            df = self._fetch_reviews()
        elif resource_lower == "customers":
            df = self._fetch_customers()
        else:
            raise ValueError("Resource must be 'orders', 'products', 'reviews', or 'customers'")
            
        if not df.empty:
            df['source_connector'] = "shopify"
            df['source_row'] = df.index + 1
        return df

    def preview(
        self,
        n: int = 5,
        resource: str = "orders",
        pull_metafields: bool = False,
        metafield_mapping: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        resource_lower = resource.lower().strip()
        if resource_lower == "orders":
            df = self._fetch_orders(max_pages=1)
        elif resource_lower == "products":
            df = self._fetch_products(max_pages=1, pull_metafields=pull_metafields, metafield_mapping=metafield_mapping)
        elif resource_lower == "reviews":
            df = self._fetch_reviews(max_pages=1)
        elif resource_lower == "customers":
            df = self._fetch_customers(max_pages=1)
        else:
            raise ValueError("Resource must be 'orders', 'products', 'reviews', or 'customers'")
            
        if not df.empty:
            df = df.head(n).copy()
            df['source_connector'] = "shopify"
            df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"Shopify Connector reading from {self.shop_name}"
