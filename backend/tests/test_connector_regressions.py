import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.connectors.csv_excel import CSVConnector, ExcelConnector
from app.connectors.tally import TallyConnector
from app.connectors.shopify import ShopifyConnector


def test_malformed_csv_does_not_become_header(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("product,amount\nA,10\nB,20,unexpected\n", encoding="utf-8")
    with pytest.raises(pd.errors.ParserError):
        CSVConnector(str(path)).fetch()


def test_csv_branding_preserves_rows(tmp_path):
    path = tmp_path / "good.csv"
    path.write_text("Business report\nproduct,amount\nA,10\nB,20\n", encoding="utf-8")
    assert CSVConnector(str(path)).fetch()["amount"].tolist() == [10, 20]


def test_invalid_excel_sheet_is_explicit(tmp_path):
    path = tmp_path / "data.xlsx"
    pd.DataFrame({"amount": [10]}).to_excel(path, sheet_name="Sales", index=False)
    with pytest.raises(ValueError, match="Available sheets: Sales"):
        ExcelConnector(str(path)).fetch(sheet_name="Missing")


def test_tally_odbc_dummy_driver_is_readonly_and_closed(monkeypatch):
    cursor = Mock()
    cursor.description = [("$Name",), ("$ClosingBalance",)]
    cursor.fetchall.return_value = [("Dummy ledger", 50)]
    connection = Mock()
    connection.cursor.return_value = cursor
    connect = Mock(return_value=connection)
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=connect))
    frame = TallyConnector("tally+odbc://DummyDSN").fetch()
    assert frame["ClosingBalance"].tolist() == [50]
    assert connect.call_args.kwargs["readonly"] is True
    assert cursor.execute.call_args.args[0].startswith("SELECT")
    connection.close.assert_called_once()
    with pytest.raises(ValueError, match="read-only"):
        TallyConnector("tally+odbc://DummyDSN?query=DELETE%20FROM%20Ledger").fetch()
    assert connect.call_count == 1


def test_foreign_shopify_pagination_never_sends_token(monkeypatch):
    import requests
    request = Mock()
    monkeypatch.setattr(requests, "get", request)
    with pytest.raises(ValueError):
        ShopifyConnector("dummy", "dummy-token")._request("GET", "https://foreign.example/orders.json")
    request.assert_not_called()


def test_encrypted_shopify_credentials_roundtrip(tmp_path, monkeypatch):
    from app.connectors import credentials
    monkeypatch.setattr(credentials, "_directory", lambda: tmp_path)
    monkeypatch.setattr(credentials, "get_vault_key", lambda: b"k" * 32)
    identity = credentials.save_shopify_token("dummy", "secret-dummy-token")
    assert "secret-dummy-token" not in (tmp_path / (identity + ".json")).read_text()
    connector = ShopifyConnector.from_url(f"shopify://dummy?connection_id={identity}&resource=products")
    assert connector.access_token == "secret-dummy-token"
    assert connector.resource == "products"
    with pytest.raises(ValueError, match="does not match"):
        credentials.read_shopify_token(identity, "other")
