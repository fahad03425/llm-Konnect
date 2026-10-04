"""Test Suite 05: Dynamic Connector Routing & API Layer Integration.

Module: Module 6.2 — Data Connector Module
Target File: backend/app/connectors/base.py (functions: detect_connector, is_network_or_custom_source, source_exists)
Scope:
- Verifies factory routing from file extensions and URI schemes to specific connector classes.
- Verifies custom URL scheme detection (shopify://, tally://, tally+odbc://, http://).
- Verifies rejection of unsupported formats with descriptive errors.
- Verifies integration with FastAPI preview routes and capabilities discovery.
"""

from unittest.mock import patch, MagicMock
import pandas as pd
import pytest
from app.connectors.base import detect_connector, is_network_or_custom_source, source_exists
from app.connectors.csv_excel import CSVConnector, ExcelConnector
from app.connectors.tally import TallyConnector, LocalDBConnector
from app.connectors.shopify import ShopifyConnector


class TestConnectorRoutingAndApiIntegration:
    """Verifies factory routing and API layer integration."""

    def test_detect_connector_file_extensions(self, tmp_path):
        """Routes distinct file extensions to their corresponding connector instances."""
        # CSV / TSV / TXT
        f_csv = tmp_path / "data.csv"
        f_csv.write_text("a,b\n1,2", encoding="utf-8")
        assert isinstance(detect_connector(str(f_csv)), CSVConnector)

        f_tsv = tmp_path / "data.tsv"
        f_tsv.write_text("a\tb\n1\t2", encoding="utf-8")
        assert isinstance(detect_connector(str(f_tsv)), CSVConnector)

        # Excel
        f_xlsx = tmp_path / "data.xlsx"
        pd.DataFrame({"x": [1]}).to_excel(f_xlsx, index=False)
        assert isinstance(detect_connector(str(f_xlsx)), ExcelConnector)

        # XML / Tally
        f_xml = tmp_path / "data.xml"
        f_xml.write_text("<VOUCHER/>", encoding="utf-8")
        assert isinstance(detect_connector(str(f_xml)), TallyConnector)

        # SQLite
        f_db = tmp_path / "data.db"
        f_db.touch()
        assert isinstance(detect_connector(str(f_db)), LocalDBConnector)

    def test_detect_connector_url_schemes(self):
        """Routes custom URI schemes to live network connectors."""
        # Shopify
        shopify_url = "shopify://my-store?access_token=shpat_123&resource=orders"
        conn_shopify = detect_connector(shopify_url)
        assert isinstance(conn_shopify, ShopifyConnector)
        assert conn_shopify.shop_name == "my-store"

        # Tally Live HTTP
        tally_http = "tally://localhost:9000"
        conn_tally_http = detect_connector(tally_http)
        assert isinstance(conn_tally_http, TallyConnector)
        assert conn_tally_http.is_http is True

        # Tally ODBC
        tally_odbc = "tally+odbc://TallyPrimeDSN"
        conn_tally_odbc = detect_connector(tally_odbc)
        assert isinstance(conn_tally_odbc, TallyConnector)
        assert conn_tally_odbc.is_odbc is True

    def test_detect_connector_unsupported_extension_error(self):
        """Unsupported extensions raise descriptive ValueError."""
        with pytest.raises(ValueError) as exc:
            detect_connector("unsupported_archive.zip")
        assert "Unsupported file extension: .zip" in str(exc.value)

    def test_source_exists_and_network_source_validation(self, tmp_path):
        """Verifies existence checks for local paths vs network URLs."""
        # Network schemes always return True without filesystem probing
        assert source_exists("shopify://test-store") is True
        assert source_exists("tally://localhost:9000") is True
        assert source_exists("http://example.com/data.xml") is True
        assert is_network_or_custom_source("shopify://test") is True
        assert is_network_or_custom_source("tally+odbc://dsn") is True

        # Local files check actual existence
        existing_file = tmp_path / "exists.csv"
        existing_file.write_text("a,b\n1,2", encoding="utf-8")
        assert source_exists(str(existing_file)) is True
        assert source_exists(str(tmp_path / "missing.csv")) is False

    def test_api_sheets_discovery_endpoint(self, test_client, tmp_path):
        """POST /api/sources/sheets returns all available sheet names for Excel files."""
        excel_path = tmp_path / "inventory_sheets.xlsx"
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="Medicines", index=False)
            pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="Suppliers", index=False)

        res = test_client.post(f"/api/sources/sheets?file_path={excel_path}")
        assert res.status_code == 200
        data = res.json()
        assert "Medicines" in data["sheets"]
        assert "Suppliers" in data["sheets"]

    def test_api_preview_endpoint(self, test_client, tmp_path):
        """POST /api/sources/preview extracts first n rows with schema signature and connector metadata."""
        csv_file = tmp_path / "pos_preview.csv"
        csv_file.write_text("Item,Price,Stock\nPanadol,20,100\nBrufen,40,50\n", encoding="utf-8")

        res = test_client.post("/api/sources/preview", json={
            "file_path": str(csv_file),
            "n": 1,
            "domain": "pharmacy"
        })
        assert res.status_code == 200
        data = res.json()
        assert "columns" in data
        assert "Item" in data["columns"]
        assert "data" in data
        assert len(data["data"]) == 1
        assert data["connector_description"].startswith("CSV/Text Connector")


