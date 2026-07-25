import pandas as pd
import time
import os
from typing import Optional, List, Dict, Any
from app.connectors.base import Connector

class ShopifyConnector(Connector):
    def __init__(self, shop_name: str, access_token: str, api_version: str = "2025-01"):
        """
        :param shop_name: The shop name (e.g. 'my-pharmacy' from my-pharmacy.myshopify.com)
        :param access_token: Admin API access token
        """
        self.shop_name = shop_name
        self.access_token = access_token
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
            
    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json"
        }

    def _paginate_request(self, endpoint: str, limit: int = 50, max_pages: Optional[int] = None) -> List[Dict]:
        """Fetch all pages of a Shopify resource, handling 429 rate limits."""
        url = f"{self.base_url}/{endpoint}"
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
            
            # The key is usually the resource name (e.g. 'orders', 'products')
            # Let's find the first list in the response dict
            for key, val in data.items():
                if isinstance(val, list):
                    results.extend(val)
                    break
                    
            pages_fetched += 1
            
            # Check Link header for pagination
            link_header = response.headers.get("Link")
            url = None
            params = {} # params are included in the link header url
            if link_header:
                # e.g. <https://.../orders.json?page_info=...>; rel="next"
                links = link_header.split(',')
                for link in links:
                    if 'rel="next"' in link:
                        url = link[link.find("<")+1:link.find(">")]
                        break
                        
        return results

    def _fetch_orders(self, max_pages: Optional[int] = None) -> pd.DataFrame:
        orders_raw = self._paginate_request("orders.json?status=any", max_pages=max_pages)
        
        flattened = []
        for order in orders_raw:
            base_info = {
                "invoice_id": order.get("name"),
                "date": order.get("created_at"),
                "customer_id": order.get("customer", {}).get("id") if order.get("customer") else None,
                "payment_method": ", ".join(order.get("payment_gateway_names", [])),
                "txn_type": "sale",
                "discount_order": order.get("total_discounts")
            }
            
            for item in order.get("line_items", []):
                row = base_info.copy()
                row["product_id"] = item.get("name")
                row["quantity"] = item.get("quantity")
                row["unit_price"] = item.get("price")
                
                # discount per line item can be calculated if needed, just bringing total discount now
                row["discount"] = item.get("total_discount") 
                row["tax"] = sum([float(t.get("price", 0)) for t in item.get("tax_lines", [])])
                
                discount_val = row.get("discount") or 0
                row["amount"] = float(row["quantity"]) * float(row["unit_price"]) - float(discount_val)
                
                flattened.append(row)
                
        df = pd.DataFrame(flattened)
        return df

    def _fetch_products(self, max_pages: Optional[int] = None, pull_metafields: bool = False, metafield_mapping: Optional[Dict[str, str]] = None) -> pd.DataFrame:
        products_raw = self._paginate_request("products.json", max_pages=max_pages)
        
        flattened = []
        for prod in products_raw:
            base_info = {
                "product_id": prod.get("title"),
                "manufacturer": prod.get("vendor"),
                "category": prod.get("product_type"),
            }
            
            # Fetch metafields if requested
            meta_dict = {}
            if pull_metafields and metafield_mapping:
                # Note: fetching metafields per product is very slow (N+1 queries). 
                # This is a naive implementation; in a real high-volume app, Bulk API is better.
                product_id = prod.get("id")
                try:
                    meta_raw = self._paginate_request(f"products/{product_id}/metafields.json", max_pages=1)
                    for m in meta_raw:
                        key = f"{m.get('namespace')}.{m.get('key')}"
                        if key in metafield_mapping:
                            meta_dict[metafield_mapping[key]] = m.get("value")
                except Exception:
                    pass
            
            for variant in prod.get("variants", []):
                row = base_info.copy()
                # If product has multiple variants, append variant title to distinguish (e.g. "Panadol - 100mg")
                if variant.get("title") != "Default Title":
                    row["product_id"] = f"{row['product_id']} - {variant.get('title')}"
                    
                row["barcode"] = variant.get("barcode")
                row["unit_price"] = variant.get("price")
                row["quantity"] = variant.get("inventory_quantity")
                row["cost"] = variant.get("compare_at_price") # sometimes used as MRP or cost depending on store
                
                # Update with metafields
                row.update(meta_dict)
                
                flattened.append(row)
                
        df = pd.DataFrame(flattened)
        return df

    def fetch(self, resource: str = "orders", pull_metafields: bool = False, metafield_mapping: Optional[Dict[str, str]] = None, **kwargs) -> pd.DataFrame:
        """
        Fetch data from Shopify.
        :param resource: 'orders' or 'products'
        """
        if resource == "orders":
            df = self._fetch_orders()
        elif resource == "products":
            df = self._fetch_products(pull_metafields=pull_metafields, metafield_mapping=metafield_mapping)
        else:
            raise ValueError("Resource must be 'orders' or 'products'")
            
        if not df.empty:
            df['source_connector'] = "shopify"
            df['source_row'] = df.index + 1
        return df

    def preview(self, n: int = 5, resource: str = "orders", pull_metafields: bool = False, metafield_mapping: Optional[Dict[str, str]] = None, **kwargs) -> pd.DataFrame:
        if resource == "orders":
            df = self._fetch_orders(max_pages=1)
        elif resource == "products":
            df = self._fetch_products(max_pages=1, pull_metafields=pull_metafields, metafield_mapping=metafield_mapping)
        else:
            raise ValueError("Resource must be 'orders' or 'products'")
            
        if not df.empty:
            df = df.head(n).copy()
            df['source_connector'] = "shopify"
            df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"Shopify Connector reading from {self.shop_name}"
