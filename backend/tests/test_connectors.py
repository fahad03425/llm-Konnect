import pytest
import os
import sqlite3
import pandas as pd
from unittest.mock import patch, MagicMock

from app.connectors.base import detect_connector
from app.connectors.csv_excel import CSVConnector
from app.connectors.json import JSONConnector
from app.connectors.tally import LocalDBConnector
from app.connectors.shopify import ShopifyConnector

# Helper to create a dummy sqlite DB for testing
@pytest.fixture
def sqlite_sample(tmp_path):
    db_path = tmp_path / "sample_pharmacy.sqlite"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE inventory (id INTEGER PRIMARY KEY, product TEXT, qty INTEGER)")
    cursor.execute("INSERT INTO inventory (product, qty) VALUES ('Panadol', 100), ('Brufen', 50)")
    conn.commit()
    conn.close()
    
    # Also save one to data/samples for manual testing
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples")
    os.makedirs(sample_dir, exist_ok=True)
    conn2 = sqlite3.connect(os.path.join(sample_dir, "sample_pharmacy.db"))
    conn2.execute("DROP TABLE IF EXISTS inventory")
    conn2.execute("CREATE TABLE inventory (id INTEGER PRIMARY KEY, product TEXT, qty INTEGER)")
    conn2.execute("INSERT INTO inventory (product, qty) VALUES ('Panadol', 100), ('Brufen', 50)")
    conn2.commit()
    conn2.close()
    
    return str(db_path)

def test_detect_connector():
    sample_csv = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "sample_pharmacy.csv")
    sample_json = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "sample_pharmacy.json")
    assert isinstance(detect_connector(sample_csv), CSVConnector)
    assert isinstance(detect_connector(sample_json), JSONConnector)
    
def test_csv_connector():
    sample_csv = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "sample_pharmacy.csv")
    conn = CSVConnector(sample_csv)
    df = conn.fetch()
    assert len(df) == 4
    assert "Panadol 500mg" in df["product name"].values
    
def test_json_connector():
    sample_json = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "sample_pharmacy.json")
    conn = JSONConnector(sample_json)
    df = conn.fetch()
    assert len(df) == 1
    assert "product_title" in df.columns # flattened

def test_localdb_connector(sqlite_sample):
    conn = LocalDBConnector(sqlite_sample, db_type="sqlite")
    df = conn.fetch(table_or_query="inventory")
    assert len(df) == 2
    assert df.loc[0, "product"] == "Panadol"
    
@patch('requests.get')
def test_shopify_connector(mock_requests_get):
    # Mocking response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = {
        "orders": [
            {
                "name": "#1001",
                "created_at": "2024-01-01T12:00:00Z",
                "line_items": [
                    {"name": "Panadol", "quantity": 2, "price": "50.00"}
                ]
            }
        ]
    }
    mock_requests_get.return_value = mock_response
    
    conn = ShopifyConnector("test-shop", "token")
    df = conn.fetch("orders")
    
    assert len(df) == 1
    assert df.loc[0, "invoice_id"] == "#1001"
    assert df.loc[0, "product_id"] == "Panadol"
    assert df.loc[0, "amount"] == 100.0 # 2 * 50
