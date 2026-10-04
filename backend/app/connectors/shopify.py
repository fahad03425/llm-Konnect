import pandas as pd
import time
import os
import re
import urllib.parse
from typing import Optional, List, Dict, Any
from app.connectors.base import Connector


class ShopifyConnector(Connector):
    """
    Shopify Connector for LLM-Konnect.
    Supports multi-resource extraction (orders, line items, products, variants,
    inventory, customers, reviews) with rate-limit handling and schema alignment.
    """

    def __init__(self, shop_name: str, access_token: str, api_version: str = "2026-07", *, resource: str = "orders", timeout: float = 30, max_retries: int = 3, review_type: str = "review"):
        """
        :param shop_name: The shop name (e.g. 'my-store' from my-store.myshopify.com)
        :param access_token: Admin API access token (shpat_...)
        :param api_version: Shopify Admin API version
        """
        self.shop_name = shop_name.replace(".myshopify.com", "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.shop_name):
            raise ValueError("Enter a Shopify store subdomain, e.g. my-store, without a URL path.")
        self.access_token = access_token.strip()
        if not self.access_token:
            raise ValueError("Shopify Admin API access token is required.")
        if not re.fullmatch(r"\d{4}-(01|04|07|10)", api_version):
            raise ValueError("Shopify API version must be YYYY-MM with a quarterly release month.")
        self.api_version = api_version
        self.resource = resource.lower().strip()
        if self.resource not in ("orders", "products", "reviews", "customers"):
            raise ValueError("Resource must be orders, products, reviews, or customers")
        self.timeout = timeout
        self.max_retries = max_retries
        self.review_type = review_type
        self.warnings = []
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
        if query_params.get("connection_id"):
            from app.connectors.credentials import read_shopify_token
            access_token = read_shopify_token(query_params["connection_id"][0], shop_name)
        api_version = query_params.get("api_version", ["2026-07"])[0]
        return cls(shop_name=shop_name, access_token=access_token, api_version=api_version,
                   resource=query_params.get("resource", ["orders"])[0],
                   review_type=query_params.get("review_type", ["review"])[0])

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json"
        }

    def _request(self, method: str, url: str, **kwargs):
        # Do not forward a store token to pagination links on another origin.
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(self.base_url).netloc:
            raise ValueError("Shopify pagination returned a different host")
        for attempt in range(self.max_retries + 1):
            response = getattr(self.requests, method)(url, headers=self._get_headers(),
                                                      timeout=self.timeout, allow_redirects=False, **kwargs)
            if response.status_code in (401, 403):
                raise PermissionError("Shopify rejected the token or required access scopes. Check token and app permissions.")
            if response.status_code == 429:
                if attempt == self.max_retries:
                    raise RuntimeError("Shopify rate limit persists. Please retry later.")
                try:
                    delay = min(30, max(0, float(response.headers.get("Retry-After", 2))))
                except (ValueError, TypeError):
                    delay = 2
                time.sleep(delay)
                continue
            if 300 <= response.status_code < 400:
                raise RuntimeError("Shopify returned an unexpected redirect")
            response.raise_for_status()
            version = response.headers.get("X-Shopify-API-Version")
            if version and version != self.api_version:
                self._warn(f"Shopify served API version {version} instead of {self.api_version}.")
            return response
        raise RuntimeError("Shopify request failed")

    def _warn(self, message: str):
        if message not in self.warnings:
            self.warnings.append(message)

    def _graphql(self, query: str, variables: Optional[Dict] = None) -> Dict:
        for attempt in range(self.max_retries + 1):
            result = self._request("post", f"{self.base_url}/graphql.json",
                                   json={"query": query, "variables": variables or {}}).json()
            errors = result.get("errors") or []
            if errors:
                throttled = all(error.get("extensions", {}).get("code") == "THROTTLED" for error in errors)
                if throttled and attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise RuntimeError("Shopify GraphQL request failed: " + "; ".join(error.get("message", "Unknown error") for error in errors))
            if "data" not in result:
                raise RuntimeError("Shopify GraphQL returned no data")
            return result["data"]
        raise RuntimeError("Shopify GraphQL rate limit persists")

    def _paginate_request(self, endpoint: str, limit: int = 50, max_pages: Optional[int] = None) -> List[Dict]:
        url = f"{self.base_url}/{endpoint}"
        params = {"limit": limit}
        results = []
        pages_fetched = 0
        seen = set()
        resource_key = endpoint.split("?", 1)[0].split("/")[-1].removesuffix(".json")
        while url and (max_pages is None or pages_fetched < max_pages):
            if url in seen:
                raise RuntimeError("Shopify pagination repeated a page")
            seen.add(url)
            response = self._request("get", url, params=params)
            data = response.json()
            items = data.get(resource_key)
            if not isinstance(items, list):
                raise RuntimeError(f"Shopify returned no '{resource_key}' list")
            results.extend(items)
            pages_fetched += 1
            url = None
            params = {}
            for link in response.headers.get("Link", "").split(","):
                if 'rel="next"' in link:
                    url = link[link.find("<") + 1:link.find(">")]
                    break
        return results

    def _inventory_costs(self, products) -> Dict[str, Optional[float]]:
        ids = list(dict.fromkeys(str(variant["inventory_item_id"])
                   for product in products for variant in product.get("variants", [])
                   if variant.get("inventory_item_id")))
        costs = {}
        for offset in range(0, len(ids), 100):
            data = self._graphql(
                "query Costs($ids: [ID!]!) { nodes(ids: $ids) { ... on InventoryItem { legacyResourceId unitCost { amount currencyCode } } } }",
                {"ids": [f"gid://shopify/InventoryItem/{identity}" for identity in ids[offset:offset + 100]]})
            for item in data.get("nodes", []):
                if item:
                    value = item.get("unitCost")
                    costs[str(item["legacyResourceId"])] = float(value["amount"]) if value else None
        return costs

    def _fetch_orders(self, max_pages: Optional[int] = None) -> pd.DataFrame:
        """
        Fetch Shopify orders and flatten line items, customer details, refunds, and discounts.
        Maintains backward compatibility with generic/pharmacy columns (invoice_id, amount, product_id)
        while populating canonical e-commerce columns.
        """
        orders_raw = self._paginate_request("orders.json?status=any", max_pages=max_pages)
        self._warn("Order history is limited to the last 60 days unless the token has read_all_orders access.")
        
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
            line_refunds = {}
            for refund in refunds:
                for entry in refund.get("refund_line_items", []):
                    identity = entry.get("line_item_id") or (entry.get("line_item") or {}).get("id")
                    amount = float(entry.get("subtotal", 0) or 0)
                    total_refund += amount
                    if identity is not None:
                        line_refunds[str(identity)] = line_refunds.get(str(identity), 0) + amount
            transaction_refund = sum(float(tx.get("amount", 0) or 0)
                                     for refund in refunds for tx in refund.get("transactions", [])
                                     if tx.get("kind") == "refund" and tx.get("status") == "success")
            if total_refund and not line_refunds:
                self._warn("A refund has no line-item identifiers; product-level allocation is unavailable.")
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
                "refund_amount": 0.0,
                "order_refund_amount": transaction_refund or total_refund,
                "order_shipping_amount": total_shipping,
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
                row["refund_amount"] = total_refund
                row["net_amount"] = row["sale_amount"] - total_refund
                flattened.append(row)
                continue

            for index, item in enumerate(line_items):
                row = base_info.copy()
                row["shopify_line_item_id"] = item.get("id")
                row["shipping_amount"] = total_shipping if index == 0 else 0.0
                row["refund_amount"] = line_refunds.get(str(item.get("id")), 0.0)
                row["unallocated_order_refund_amount"] = max(0.0, (transaction_refund or total_refund) - sum(line_refunds.values())) if index == 0 else 0.0
                item_name = item.get("name") or item.get("title")
                row["product_id"] = item_name
                row["product_name"] = item_name
                row["product_sku"] = item.get("sku") or item_name
                row["variant_title"] = item.get("variant_title")
                
                qty = float(item["quantity"]) if item.get("quantity") is not None else 1.0
                unit_price = float(item.get("price", 0.0) or 0.0)
                allocations = item.get("discount_allocations") or []
                line_discount = sum(float(entry.get("amount", 0) or 0) for entry in allocations) if allocations else float(item.get("total_discount", 0.0) or 0.0)
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
                row["net_amount"] = line_amount - row["refund_amount"]
                
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
        try:
            inventory_costs = self._inventory_costs(products_raw)
        except (PermissionError, RuntimeError) as exc:
            inventory_costs = {}
            self._warn(f"Product costs unavailable: {exc}")
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
                except (PermissionError, RuntimeError) as exc:
                    self._warn(f"Product metafields unavailable: {exc}")
            
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
                cost_val = inventory_costs.get(str(variant.get("inventory_item_id")))
                row["compare_at_price"] = float(variant["compare_at_price"]) if variant.get("compare_at_price") else None
                
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
        """Read a configured Shopify review metaobject type; external review apps need their own export."""
        flattened = []
        cursor = None
        pages = 0
        while max_pages is None or pages < max_pages:
            data = self._graphql(
                "query Reviews($type: String!, $after: String) { metaobjects(type: $type, first: 50, after: $after) { nodes { updatedAt fields { key value } } pageInfo { hasNextPage endCursor } } }",
                {"type": self.review_type, "after": cursor})
            connection = data.get("metaobjects")
            if not isinstance(connection, dict):
                raise RuntimeError("Review metaobjects unavailable. Check read_metaobjects scope and configured review type.")
            for obj in connection.get("nodes", []):
                fields = {field.get("key"): field.get("value") for field in obj.get("fields", [])}
                rating = fields.get("rating")
                try:
                    rating = float(rating) if rating is not None and rating != "" else None
                except (ValueError, TypeError):
                    self._warn("A review has an invalid rating; it was retained as unknown.")
                    rating = None
                flattened.append({
                    "product_name": fields.get("product_title") or fields.get("product"),
                    "product_sku": fields.get("sku"),
                    "customer_name": fields.get("author") or fields.get("customer_name"),
                    "rating": rating,
                    "review_text": fields.get("body") or fields.get("comment") or fields.get("text"),
                    "date": obj.get("updatedAt")})
            pages += 1
            page_info = connection.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("Review pagination returned an invalid cursor")
            cursor = next_cursor
        if not flattened:
            self._warn("No review metaobjects found. Confirm the review type or import your review app's CSV export.")
        return pd.DataFrame(flattened, columns=["product_name", "product_sku", "customer_name", "rating", "review_text", "date"])

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
        resource: Optional[str] = None,
        pull_metafields: bool = False,
        metafield_mapping: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        Fetch data from Shopify.
        :param resource: 'orders', 'products', 'reviews', or 'customers'
        """
        self.warnings = []
        resource_lower = (resource or self.resource).lower().strip()
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
        df.attrs["connector_warnings"] = self.warnings.copy()
        return df

    def preview(
        self,
        n: int = 5,
        resource: Optional[str] = None,
        pull_metafields: bool = False,
        metafield_mapping: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        self.warnings = []
        resource_lower = (resource or self.resource).lower().strip()
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
        df.attrs["connector_warnings"] = self.warnings.copy()
        return df

    def describe(self) -> str:
        return f"Shopify Connector reading from {self.shop_name}"
