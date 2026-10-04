# Test Documentation Report: 03 — Shopify Admin API E-Commerce Connector

## 1. Test Suite Identification
- **Module ID**: Module 6.2 (Data Connector Module)
- **Test File**: [`test_03_shopify_admin_api_connector.py`](./test_03_shopify_admin_api_connector.py)
- **Target Implementation File**: [`backend/app/connectors/shopify.py`](../../backend/app/connectors/shopify.py)
- **Primary Class Tested**: `ShopifyConnector`

---

## 2. Tested Business Requirements & Logic
1. **Multi-Resource E-Commerce Extraction**:
   - Seamlessly extracts **Orders**, **Products**, and **Customer Reviews** behind a unified connector interface.
   - Resource routing is controlled via `resource="orders"`, `resource="products"`, or `resource="reviews"`.
2. **Order Line-Item Flattening & Financial Allocation**:
   - Decomposes nested order payloads into discrete item lines while preserving global order metadata.
   - Line-item discount allocation, tax lines, and shipping costs.
   - Granular refund allocation: assigns refunds directly to the affected line items (`line_item_id`) rather than inaccurately distorting all product sales.
   - Computes both generic backward-compatible columns (`invoice_id`, `amount`, `product_id`) and canonical e-commerce fields (`order_id`, `sale_amount`, `gross_amount`, `net_amount`, `discount_amount`, `tax_amount`).
3. **Product Inventory & GraphQL Unit Cost**:
   - Extracts product titles, vendors, categories, and multiple product variants (SKUs, barcodes, compare-at prices, stock levels).
   - Automatically executes batched GraphQL queries against `InventoryItem` nodes to retrieve true unit costs, enabling gross profit margin calculations downstream.
4. **Customer Review Extraction**:
   - Queries Shopify's GraphQL `metaobjects` endpoint to ingest customer review text, numerical star ratings, author names, and product association without requiring external SaaS connectors.
5. **Rate-Limit Resilience & Token Security**:
   - Honors HTTP 429 `Retry-After` headers and pauses execution dynamically before re-attempting requests.
   - Handles GraphQL `THROTTLED` extensions using exponential backoff.
   - Blocks SSRF/token-leakage attacks by strictly verifying that pagination URLs match the configured store hostname.
   - Translates HTTP 401/403 into explicit `PermissionError` alerts.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_store_initialization_and_url_validation` | Subdomain cleaning & invalid URL rejection | Normalizes valid subdomains; rejects paths/blank tokens | **PASS** |
| `test_from_url_factory` | URL scheme constructor `shopify://` | Instantiates connector with extracted parameters | **PASS** |
| `test_fetch_orders_line_items_and_refunds` | Orders extraction with refunds & discounts | Correctly flattens lines, assigns shipping & refunds | **PASS** |
| `test_fetch_products_with_inventory_costs` | Products & GraphQL unit cost lookup | Flattens variants and attaches unit costs from GraphQL | **PASS** |
| `test_fetch_reviews_metaobjects_graphql` | Customer review metaobject extraction | Ingests rating, author, product title, and review text | **PASS** |
| `test_rate_limit_429_retry_handling` | HTTP 429 Rate Limit recovery | Sleeps `Retry-After` duration and succeeds on retry | **PASS** |
| `test_authorization_error_401_raises_permission_error` | Invalid token or expired scopes | Raises `PermissionError` with guidance | **PASS** |
| `test_pagination_origin_mismatch_security_check` | Link header pointing to external host | Aborts request to prevent token leakage | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.2 Data Connector Module/test_03_shopify_admin_api_connector.py" -v`
- **Assurance**: Mocked responses replicate real Shopify Admin REST & GraphQL payloads and pagination structures.
