"""Test Suite 02: Tally Prime Direct Local Extraction (XML, HTTP, and ODBC).

Module: Module 6.2 — Data Connector Module
Target File: backend/app/connectors/tally.py (classes: TallyConnector, functions: parse_tally_xml, _parse_tally_number, _parse_tally_date)
Scope:
- Verifies Tally XML voucher parsing across Sales, Purchases, Receipts, and Payments.
- Verifies granular extraction of inventory items, batch numbers, and expiry dates.
- Verifies accounting-only vouchers (without inventory lines).
- Verifies Tally numeric and date string sanitization (e.g. '50 Box', '18.00/Box', YYYYMMDD).
- Verifies live HTTP TDL export request construction and headers to port 9000.
- Verifies offline connection error reporting when Tally server is not running.
- Verifies Tally ODBC DSN query validation and read-only constraints.
"""

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from app.connectors.tally import TallyConnector, parse_tally_xml, _parse_tally_number, _parse_tally_date


SAMPLE_TALLY_SALES_XML = """<?xml version="1.0" encoding="utf-8"?>
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
          <VOUCHERNUMBER>INV-9021</VOUCHERNUMBER>
          <PARTYLEDGERNAME>Ali Medico Store</PARTYLEDGERNAME>
          <NARRATION>Standard monthly supply</NARRATION>
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Panadol 500mg Tablet</STOCKITEMNAME>
            <BILLEDQTY>100 Box</BILLEDQTY>
            <RATE>18.50/Box</RATE>
            <AMOUNT>-1850.00</AMOUNT>
            <BATCHALLOCATIONS.LIST>
              <BATCHNAME>B-9024</BATCHNAME>
              <EXPIRYPERIOD>20271231</EXPIRYPERIOD>
            </BATCHALLOCATIONS.LIST>
          </ALLINVENTORYENTRIES.LIST>
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Augmentin 625mg</STOCKITEMNAME>
            <BILLEDQTY>50 Box</BILLEDQTY>
            <RATE>140.00/Box</RATE>
            <AMOUNT>-7000.00</AMOUNT>
            <BATCHALLOCATIONS.LIST>
              <BATCHNAME>AUG-112</BATCHNAME>
              <EXPIRYPERIOD>20260815</EXPIRYPERIOD>
            </BATCHALLOCATIONS.LIST>
          </ALLINVENTORYENTRIES.LIST>
        </VOUCHER>
      </TALLYMESSAGE>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>
"""

SAMPLE_TALLY_EXPENSE_XML = """<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
  <BODY>
    <DATA>
      <VOUCHER VCHTYPE="Payment">
        <DATE>20260201</DATE>
        <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
        <VOUCHERNUMBER>PAY-104</VOUCHERNUMBER>
        <PARTYLEDGERNAME>City Bank Account</PARTYLEDGERNAME>
        <NARRATION>Monthly Electricity Bill</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>City Bank Account</LEDGERNAME>
          <AMOUNT>15000.00</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Electricity Expense</LEDGERNAME>
          <AMOUNT>-15000.00</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </DATA>
  </BODY>
</ENVELOPE>
"""


class TestTallyXMLParsing:
    """Verifies parsing of TallyPrime XML payloads."""

    def test_parse_sales_voucher_with_inventory_and_batches(self):
        """Extracts products, rates, quantities, batch numbers, and expiry dates."""
        df = parse_tally_xml(SAMPLE_TALLY_SALES_XML)

        assert len(df) == 2
        assert df.loc[0, "invoice_id"] == "INV-9021"
        assert df.loc[0, "date"] == "2026-01-15"
        assert df.loc[0, "txn_type"] == "sale"
        assert df.loc[0, "customer_id"] == "Ali Medico Store"
        assert df.loc[0, "product_id"] == "Panadol 500mg Tablet"
        assert df.loc[0, "quantity"] == 100.0
        assert df.loc[0, "rate"] == 18.50
        assert df.loc[0, "amount"] == 1850.0
        assert df.loc[0, "batch_no"] == "B-9024"
        assert df.loc[0, "expiry_date"] == "2027-12-31"

        # Second inventory item
        assert df.loc[1, "product_id"] == "Augmentin 625mg"
        assert df.loc[1, "batch_no"] == "AUG-112"
        assert df.loc[1, "expiry_date"] == "2026-08-15"
        assert df.loc[1, "amount"] == 7000.0

    def test_parse_accounting_payment_voucher(self):
        """Extracts non-inventory financial ledger entries."""
        df = parse_tally_xml(SAMPLE_TALLY_EXPENSE_XML)

        assert len(df) == 1
        assert df.loc[0, "invoice_id"] == "PAY-104"
        assert df.loc[0, "date"] == "2026-02-01"
        assert df.loc[0, "txn_type"] == "payment"
        assert df.loc[0, "product_id"] == "Electricity Expense"
        assert df.loc[0, "amount"] == 15000.0

    def test_parse_tally_number_variations(self):
        """Handles negative amounts, packaging units, and comma separators."""
        assert _parse_tally_number("100 Box") == 100.0
        assert _parse_tally_number("18.50/Box") == 18.50
        assert _parse_tally_number("-1,850.00") == -1850.00
        assert _parse_tally_number("Rs. 4,500.50") == 4500.50
        assert _parse_tally_number(None) == 0.0
        assert _parse_tally_number("") == 0.0

    def test_parse_tally_date_formats(self):
        """Handles 8-digit YYYYMMDD and standard date strings."""
        assert _parse_tally_date("20260115") == "2026-01-15"
        assert _parse_tally_date("20271231") == "2027-12-31"
        assert _parse_tally_date("15-Jan-2026") == "2026-01-15"
        assert _parse_tally_date("") == ""

    def test_parse_tally_error_envelope(self):
        """Raises ValueError when Tally export payload reports a LINEERROR."""
        error_xml = "<ENVELOPE><BODY><LINEERROR>Company not open</LINEERROR></BODY></ENVELOPE>"
        with pytest.raises(ValueError) as exc:
            parse_tally_xml(error_xml)
        assert "Tally export failed" in str(exc.value)
        assert "Company not open" in str(exc.value)


class TestTallyLiveConnector:
    """Verifies Tally HTTP XML endpoint and ODBC queries."""

    @patch("requests.post")
    def test_live_http_request_construction_and_fetch(self, mock_post):
        """Sends proper TDL export envelope to Tally HTTP endpoint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = SAMPLE_TALLY_SALES_XML.encode("utf-8")
        mock_post.return_value = mock_resp

        connector = TallyConnector("http://localhost:9000")
        assert connector.is_http is True
        df = connector.fetch()

        assert len(df) == 2
        assert mock_post.called
        call_url, call_kwargs = mock_post.call_args
        assert call_url[0] == "http://localhost:9000"
        assert "DayBook" in call_kwargs["data"].decode("utf-8")
        assert call_kwargs["headers"]["Content-Type"] == "text/xml; charset=utf-8"

    @patch("requests.post")
    def test_live_http_connection_offline_error(self, mock_post):
        """Surfaces clear connection error when TallyPrime is offline."""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        connector = TallyConnector("tally://localhost:9000")
        with pytest.raises(ConnectionError) as exc:
            connector.fetch()
        assert "Could not connect to TallyPrime" in str(exc.value)
        assert "Ensure Tally is open with ODBC/HTTP listening enabled" in str(exc.value)

    def test_tally_file_based_extraction(self, tmp_path):
        """Direct extraction from exported Tally XML file on disk."""
        xml_file = tmp_path / "tally_export.xml"
        xml_file.write_text(SAMPLE_TALLY_SALES_XML, encoding="utf-8")

        connector = TallyConnector(str(xml_file))
        assert connector.is_http is False
        df = connector.fetch()

        assert len(df) == 2
        assert df.loc[0, "product_id"] == "Panadol 500mg Tablet"

    def test_tally_odbc_query_validation(self):
        """Rejects non-SELECT or multiple SQL statements on Tally ODBC."""
        with patch.dict("sys.modules", {"pyodbc": MagicMock()}):
            conn = TallyConnector("tally+odbc://TallyPrimeDSN?query=DROP TABLE Ledger")
            with pytest.raises(ValueError) as exc:
                conn.fetch()
            assert "read-only SELECT query" in str(exc.value)


    def test_tally_capabilities(self):
        """Verifies reported connector capabilities."""
        http_conn = TallyConnector("http://localhost:9000")
        caps = http_conn.capabilities()
        assert caps["live_http"] is True
        assert caps["supports_batch_and_expiry"] is True
