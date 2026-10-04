"""Real loopback HTTP connections; no Shopify store or Tally installation needed."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.connectors.shopify import ShopifyConnector
from app.connectors.tally import TallyConnector
from app.connectors.base import detect_connector


TALLY_XML = b'''<ENVELOPE><VOUCHER VCHTYPE="Sales"><DATE>20261001</DATE>
<VOUCHERNUMBER>INV-1</VOUCHERNUMBER><PARTYLEDGERNAME>Dummy Customer</PARTYLEDGERNAME>
<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Dummy Product</STOCKITEMNAME>
<BILLEDQTY>2 Box</BILLEDQTY><RATE>25</RATE><AMOUNT>50</AMOUNT>
</ALLINVENTORYENTRIES.LIST></VOUCHER></ENVELOPE>'''


@pytest.fixture
def dummy_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, payload, status=200, headers=None):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/xml" if isinstance(payload, bytes) else "application/json")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            received.append(("GET", self.path, self.headers.get("X-Shopify-Access-Token")))
            token = self.headers.get("X-Shopify-Access-Token")
            if token == "bad-token":
                return self.send({"error": "Unauthorized"}, 401)
            if token == "throttled":
                return self.send({"error": "Rate limited"}, 429, {"Retry-After": "0"})
            path = urlsplit(self.path)
            if path.path.endswith("products.json"):
                second = "page_info" in parse_qs(path.query)
                item = {"id": 11 if second else 10, "title": "Product B" if second else "Product A",
                        "variants": [{"id": 31, "inventory_item_id": 20, "sku": "SKU-B" if second else "SKU-A",
                                      "price": "50", "compare_at_price": "75", "inventory_quantity": 8}]}
                link = {} if second else {"Link": f'<http://127.0.0.1:{self.server.server_port}/admin/api/2026-07/products.json?page_info=next>; rel="next"'}
                return self.send({"products": [item]}, headers=link)
            if path.path.endswith("orders.json"):
                return self.send({"orders": [{"id": 1, "name": "INV-1", "created_at": "2026-10-01",
                    "currency": "PKR", "financial_status": "paid", "shipping_lines": [{"price": "10"}],
                    "refunds": [{"refund_line_items": [{"line_item_id": 101, "subtotal": "15"}],
                                 "transactions": [{"kind": "refund", "status": "success", "amount": "15"}]}],
                    "line_items": [{"id": 101, "title": "Product A", "sku": "SKU-A", "quantity": 2, "price": "25"},
                                   {"id": 102, "title": "Product B", "sku": "SKU-B", "quantity": 1, "price": "40"}]}]})
            return self.send({"error": "Not found"}, 404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(("POST", self.path, self.headers.get("X-Shopify-Access-Token")))
            if self.path == "/tally":
                assert b"DayBook" in body
                return self.send(TALLY_XML)
            if self.path == "/tally-error":
                return self.send(b"<ENVELOPE><LINEERROR>Company is not loaded</LINEERROR></ENVELOPE>")
            query = json.loads(body)
            if self.headers.get("X-Shopify-Access-Token") == "no-reviews":
                return self.send({"errors": [{"message": "Access denied: read_metaobjects required"}]})
            if "Costs" in query["query"]:
                return self.send({"data": {"nodes": [{"legacyResourceId": "20", "unitCost": {"amount": "18", "currencyCode": "PKR"}}]}})
            if "Reviews" in query["query"]:
                after = query["variables"].get("after")
                fields = [{"key": "product_title", "value": "Product A"}, {"key": "body", "value": "Dummy review"}]
                if after:
                    fields.append({"key": "rating", "value": "4"})
                return self.send({"data": {"metaobjects": {"nodes": [{"fields": fields, "updatedAt": "2026-10-01"}],
                    "pageInfo": {"hasNextPage": not bool(after), "endCursor": "review-next" if not after else "done"}}}})
            return self.send({"errors": [{"message": "Unknown dummy query"}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def local_shopify(dummy_server, monkeypatch):
    origin, received = dummy_server
    original = ShopifyConnector.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        # Override transport only in tests; production stays on the Shopify HTTPS host.
        self.base_url = origin + "/admin/api/2026-07"

    monkeypatch.setattr(ShopifyConnector, "__init__", initialize)
    return received


def test_dummy_shopify_products_pagination_and_real_cost(local_shopify):
    connector = detect_connector("shopify://dummy?access_token=dummy-token&resource=products")
    frame = connector.fetch()
    assert frame["product_name"].tolist() == ["Product A", "Product B"]
    assert frame["cost_per_item"].tolist() == [18, 18]
    assert frame["compare_at_price"].tolist() == [75, 75]
    assert all(token == "dummy-token" for _, _, token in local_shopify)
    assert not any("orders.json" in path for _, path, _ in local_shopify)


def test_dummy_shopify_refunds_and_shipping_are_not_duplicated(local_shopify):
    frame = ShopifyConnector("dummy", "dummy-token").fetch("orders")
    assert frame["refund_amount"].tolist() == [15, 0]
    assert frame["net_amount"].tolist() == [35, 40]
    assert frame["shipping_amount"].sum() == 10
    assert frame["refund_amount"].sum() == 15
    assert "60 days" in frame.attrs["connector_warnings"][0]


def test_dummy_shopify_reviews_graphql_and_missing_rating(local_shopify):
    frame = ShopifyConnector("dummy", "dummy-token", resource="reviews").fetch()
    assert len(frame) == 2
    assert pd.isna(frame.iloc[0]["rating"])
    assert frame.iloc[1]["rating"] == 4
    assert all(method == "POST" and path.endswith("graphql.json") for method, path, _ in local_shopify)


def test_dummy_shopify_auth_and_review_scope_fail_explicitly(local_shopify):
    with pytest.raises(PermissionError, match="token"):
        ShopifyConnector("dummy", "bad-token").fetch()
    with pytest.raises(RuntimeError, match="read_metaobjects"):
        ShopifyConnector("dummy", "no-reviews").fetch("reviews")


def test_dummy_shopify_rate_limit_is_bounded(local_shopify):
    with pytest.raises(RuntimeError, match="rate limit"):
        ShopifyConnector("dummy", "throttled", max_retries=2).fetch()
    assert len(local_shopify) == 3


def test_dummy_tally_real_http_and_export_error(dummy_server):
    origin, received = dummy_server
    frame = TallyConnector(origin + "/tally").fetch()
    assert frame.iloc[0]["amount"] == 50
    assert frame.iloc[0]["quantity"] == 2
    assert received[0][0] == "POST"
    with pytest.raises(ValueError, match="Company is not loaded"):
        TallyConnector(origin + "/tally-error").fetch()


@pytest.fixture
def isolated_api(tmp_path, monkeypatch):
    from app.main import app
    from app.api import routes, kb
    from app.core.config import settings
    from app.ingestion.registry import FileRegistry
    registry = FileRegistry(str(tmp_path / "registry.db"))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    from app.connectors import credentials
    monkeypatch.setattr(credentials, "get_vault_key", lambda: b"k" * 32)
    monkeypatch.setattr(routes, "file_registry", registry)
    monkeypatch.setattr(kb, "file_registry", registry)
    store = Mock()
    store.add_dataframe.return_value = SimpleNamespace(total_chunks=2, model_dump=lambda: {"total_chunks": 2})
    monkeypatch.setattr(kb, "_kb", store)
    return TestClient(app), registry, store


@pytest.mark.parametrize("kind", ["shopify", "tally"])
def test_network_source_entire_import_wizard(kind, isolated_api, local_shopify, dummy_server):
    client, registry, store = isolated_api
    source = ("shopify://dummy?access_token=dummy-token&resource=orders" if kind == "shopify"
              else dummy_server[0] + "/tally")
    if kind == "shopify":
        connected = client.post("/api/sources/shopify/connect", json={
            "shop_name": "dummy", "access_token": "dummy-token", "resource": "orders"})
        assert connected.status_code == 200, connected.text
        source = connected.json()["file_path"]
        assert "dummy-token" not in source
        assert "access_token" not in source
    request = {"file_path": source, "domain": "ecommerce", "save_profile": False}
    preview = client.post("/api/sources/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]
    mapping = {"date": "date", "invoice_id": "invoice_id", "product_id": "product_id",
               "amount": "amount", "quantity": "quantity", "txn_type": "txn_type"}
    request["mapping"] = mapping
    for endpoint in ("mapping/confirm", "normalize", "validate", "clean"):
        response = client.post("/api/sources/" + endpoint, json=request)
        assert response.status_code == 200, response.text
    response = client.post("/api/kb/ingest", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["total_chunks"] == 2
    assert registry.get_file_by_path(source).status == "active"
    assert store.add_dataframe.call_args.args[0]["amount"].sum() == (90 if kind == "shopify" else 50)


def test_network_preview_keeps_product_selection(isolated_api, local_shopify):
    client, _, _ = isolated_api
    response = client.post("/api/sources/preview", json={
        "file_path": "shopify://dummy?access_token=dummy-token&resource=products", "domain": "ecommerce"})
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["product_name"] == "Product A"
    assert not any("orders.json" in path for _, path, _ in local_shopify)
