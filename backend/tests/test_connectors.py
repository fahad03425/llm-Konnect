import pytest
import os
import sqlite3
import pandas as pd
from unittest.mock import patch, MagicMock

from app.connectors.base import detect_connector
from app.connectors.csv_excel import CSVConnector
from app.connectors.json import JSONConnector
from app.connectors.tally import LocalDBConnector, TallyConnector, parse_tally_xml
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
    assert len(df) == 30
    assert any("Panadol" in str(x) for x in df["Medicine Name"].values)
    
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


SAMPLE_TALLY_XML = """<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <TALLYMESSAGE>
        <VOUCHER VCHTYPE="Sales" ACTION="Create">
          <DATE>20260115</DATE>
          <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
          <VOUCHERNUMBER>INV-9001</VOUCHERNUMBER>
          <PARTYLEDGERNAME>Ali Medical Store</PARTYLEDGERNAME>
          <NARRATION>Regular pharmacy sales delivery</NARRATION>
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Panadol 500mg Tab</STOCKITEMNAME>
            <RATE>18.00/Box</RATE>
            <ACTUALQTY> 50 Box</ACTUALQTY>
            <BILLEDQTY> 50 Box</BILLEDQTY>
            <AMOUNT>-900.00</AMOUNT>
            <BATCHALLOCATIONS.LIST>
              <BATCHNAME>BAT-2026A</BATCHNAME>
              <EXPIRYPERIOD>20270630</EXPIRYPERIOD>
            </BATCHALLOCATIONS.LIST>
          </ALLINVENTORYENTRIES.LIST>
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Augmentin 625mg Tab</STOCKITEMNAME>
            <RATE>220.00/Box</RATE>
            <ACTUALQTY> 10 Box</ACTUALQTY>
            <BILLEDQTY> 10 Box</BILLEDQTY>
            <AMOUNT>-2200.00</AMOUNT>
            <BATCHALLOCATIONS.LIST>
              <BATCHNAME>BAT-8802</BATCHNAME>
              <EXPIRYPERIOD>20261231</EXPIRYPERIOD>
            </BATCHALLOCATIONS.LIST>
          </ALLINVENTORYENTRIES.LIST>
        </VOUCHER>
        <VOUCHER VCHTYPE="Payment" ACTION="Create">
          <DATE>20260116</DATE>
          <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
          <VOUCHERNUMBER>PAY-1002</VOUCHERNUMBER>
          <PARTYLEDGERNAME>MedLine Distributors</PARTYLEDGERNAME>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Supplier Payment</LEDGERNAME>
            <AMOUNT>5000.00</AMOUNT>
          </ALLLEDGERENTRIES.LIST>
        </VOUCHER>
      </TALLYMESSAGE>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""


def test_tally_connector_from_xml_file(tmp_path):
    """Verify TallyConnector parses real Tally XML export files with inventory & batches."""
    xml_file = tmp_path / "tally_export.xml"
    xml_file.write_text(SAMPLE_TALLY_XML, encoding="utf-8")

    conn = TallyConnector(str(xml_file))
    df = conn.fetch()

    assert len(df) == 3
    # First row: Panadol sale
    row1 = df.iloc[0]
    assert row1["invoice_id"] == "INV-9001"
    assert row1["date"] == "2026-01-15"
    assert row1["txn_type"] == "sale"
    assert row1["product_id"] == "Panadol 500mg Tab"
    assert row1["quantity"] == 50.0
    assert row1["rate"] == 18.0
    assert row1["amount"] == 900.0
    assert row1["customer_id"] == "Ali Medical Store"
    assert row1["batch_no"] == "BAT-2026A"
    assert row1["expiry_date"] == "2027-06-30"
    assert row1["source_connector"] == "tally"

    # Second row: Augmentin sale
    row2 = df.iloc[1]
    assert row2["product_id"] == "Augmentin 625mg Tab"
    assert row2["quantity"] == 10.0
    assert row2["rate"] == 220.0
    assert row2["amount"] == 2200.0

    # Third row: Payment voucher
    row3 = df.iloc[2]
    assert row3["invoice_id"] == "PAY-1002"
    assert row3["txn_type"] == "payment"
    assert row3["amount"] == 5000.0


@patch("requests.post")
def test_tally_connector_from_http_live(mock_post):
    """Verify TallyConnector queries live TallyPrime HTTP server on port 9000."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = SAMPLE_TALLY_XML.encode("utf-8")
    mock_post.return_value = mock_resp

    conn = TallyConnector("http://localhost:9000")
    df = conn.fetch()

    assert len(df) == 3
    assert df.iloc[0]["product_id"] == "Panadol 500mg Tab"
    assert mock_post.called
    assert "localhost:9000" in mock_post.call_args[0][0]


def test_localdb_connector_tally_delegation(tmp_path):
    """Verify LocalDBConnector delegates to TallyConnector without NotImplementedError."""
    xml_file = tmp_path / "tally_export.xml"
    xml_file.write_text(SAMPLE_TALLY_XML, encoding="utf-8")

    # Before fix, db_type="tally" raised NotImplementedError
    conn = LocalDBConnector(str(xml_file), db_type="tally")
    df = conn.fetch()
    assert len(df) == 3
    assert df.iloc[0]["invoice_id"] == "INV-9001"

    preview_df = conn.preview(n=1)
    assert len(preview_df) == 1


def test_detect_connector_tally():
    """Verify detect_connector properly routes .xml and tally:// endpoints."""
    assert isinstance(detect_connector("daybook.xml"), TallyConnector)
    assert isinstance(detect_connector("tally://localhost:9000"), TallyConnector)
    assert isinstance(detect_connector("http://192.168.1.100:9000"), TallyConnector)

