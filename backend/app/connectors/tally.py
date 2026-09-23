"""Module 1.2 — Data connectors (Tally and Local Databases). Month 1.

Supports:
1. TallyPrime / Tally.ERP 9 via live HTTP/XML endpoint (e.g. http://localhost:9000)
   or direct Tally XML export files (.xml).
2. Local relational databases (SQLite .sqlite/.db and MS Access .mdb/.accdb).
"""

import os
import re
import io
import sqlite3
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List
import pandas as pd

from app.connectors.base import Connector
from app.security.crypto import decrypt_file_to_bytes, is_encrypted_file


def _parse_tally_number(val: Optional[str]) -> float:
    """Extracts numeric float value from Tally strings like '50 Box', '18.00/Box', '-900.00'."""
    if not val:
        return 0.0
    val = val.strip()
    match = re.search(r"[-+]?\d*\.?\d+", val)
    if match:
        try:
            return float(match.group(0))
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _parse_tally_date(val: Optional[str]) -> str:
    """Converts Tally date strings (e.g. '20260105' or '5-Jan-2026') to ISO 'YYYY-MM-DD'."""
    if not val:
        return ""
    val = val.strip()
    # 8-digit YYYYMMDD format common in Tally XML
    if len(val) == 8 and val.isdigit():
        return f"{val[:4]}-{val[4:6]}-{val[6:]}"
    try:
        dt = pd.to_datetime(val, errors="coerce")
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return val


def parse_tally_xml(xml_content: str | bytes) -> pd.DataFrame:
    """
    Parses TallyPrime XML voucher exports into a canonical DataFrame.
    Handles Sales, Purchases, Receipts, inventory entries, batches, and expiries.
    """
    if isinstance(xml_content, str):
        xml_bytes = xml_content.encode("utf-8")
    else:
        xml_bytes = xml_content

    if not xml_bytes or not xml_bytes.strip():
        return pd.DataFrame()

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse Tally XML payload: {e}")

    rows: List[Dict[str, Any]] = []

    # Find all VOUCHER elements regardless of envelope nesting
    vouchers = root.findall(".//VOUCHER")
    if not vouchers and root.tag == "VOUCHER":
        vouchers = [root]

    for v in vouchers:
        raw_date = v.findtext("DATE") or ""
        date_iso = _parse_tally_date(raw_date)

        vch_type = v.findtext("VOUCHERTYPENAME") or v.get("VCHTYPE") or "Sales"
        vch_type_low = vch_type.strip().lower()

        if "sale" in vch_type_low:
            txn_type = "sale"
        elif "purchase" in vch_type_low or "expense" in vch_type_low:
            txn_type = "expense"
        elif "receipt" in vch_type_low:
            txn_type = "receipt"
        elif "payment" in vch_type_low:
            txn_type = "payment"
        else:
            txn_type = vch_type_low

        invoice_id = v.findtext("VOUCHERNUMBER") or v.findtext("REFERENCE") or ""
        party_name = v.findtext("PARTYLEDGERNAME") or v.findtext("PARTYNAME") or ""
        narration = v.findtext("NARRATION") or ""

        # Check for inventory entries (<ALLINVENTORYENTRIES.LIST>)
        inv_entries = v.findall(".//ALLINVENTORYENTRIES.LIST")
        if not inv_entries:
            inv_entries = v.findall(".//INVENTORYENTRIES.LIST")

        if inv_entries:
            for inv in inv_entries:
                item_name = inv.findtext("STOCKITEMNAME") or inv.findtext("ITEMNAME") or "Unknown Item"
                billed_qty = _parse_tally_number(inv.findtext("BILLEDQTY") or inv.findtext("ACTUALQTY"))
                rate = _parse_tally_number(inv.findtext("RATE"))
                raw_amt = _parse_tally_number(inv.findtext("AMOUNT"))
                amount = abs(raw_amt) if raw_amt != 0.0 else round(billed_qty * rate, 2)

                # Batch & expiry information (<BATCHALLOCATIONS.LIST>)
                batch_elem = inv.find(".//BATCHALLOCATIONS.LIST")
                batch_no = ""
                expiry_date = ""
                if batch_elem is not None:
                    batch_no = batch_elem.findtext("BATCHNAME") or ""
                    raw_exp = batch_elem.findtext("EXPIRYPERIOD") or ""
                    expiry_date = _parse_tally_date(raw_exp)

                rows.append({
                    "date": date_iso,
                    "invoice_id": invoice_id,
                    "txn_type": txn_type,
                    "voucher_type": vch_type,
                    "product_id": item_name,
                    "quantity": billed_qty,
                    "rate": rate,
                    "amount": amount,
                    "customer_id": party_name,
                    "batch_no": batch_no,
                    "expiry_date": expiry_date,
                    "narration": narration
                })
        else:
            # Accounting / ledger voucher without line items (<ALLLEDGERENTRIES.LIST>)
            ledger_entries = v.findall(".//ALLLEDGERENTRIES.LIST")
            if ledger_entries:
                for led in ledger_entries:
                    led_name = led.findtext("LEDGERNAME") or ""
                    # Skip the party ledger to record the balancing expense/sales item
                    if led_name.lower() == party_name.lower() and len(ledger_entries) > 1:
                        continue
                    raw_amt = _parse_tally_number(led.findtext("AMOUNT"))
                    amount = abs(raw_amt)
                    rows.append({
                        "date": date_iso,
                        "invoice_id": invoice_id,
                        "txn_type": txn_type,
                        "voucher_type": vch_type,
                        "product_id": led_name or vch_type,
                        "quantity": 1.0,
                        "rate": amount,
                        "amount": amount,
                        "customer_id": party_name,
                        "batch_no": "",
                        "expiry_date": "",
                        "narration": narration
                    })
            else:
                # Single minimal voucher entry
                rows.append({
                    "date": date_iso,
                    "invoice_id": invoice_id,
                    "txn_type": txn_type,
                    "voucher_type": vch_type,
                    "product_id": vch_type,
                    "quantity": 1.0,
                    "rate": 0.0,
                    "amount": 0.0,
                    "customer_id": party_name,
                    "batch_no": "",
                    "expiry_date": "",
                    "narration": narration
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["source_connector"] = "tally"
        df["source_row"] = df.index + 1
    return df


class TallyConnector(Connector):
    """
    Connector for TallyPrime / Tally.ERP 9.
    
    Supports:
    1. Direct Tally XML export files (.xml).
    2. Live HTTP/XML interface (e.g. http://localhost:9000 or tally://localhost:9000).
    """
    def __init__(self, path_or_url: str = "http://localhost:9000", timeout: int = 15):
        self.path_or_url = path_or_url.strip()
        self.timeout = timeout
        self.is_http = self.path_or_url.startswith("http://") or \
                       self.path_or_url.startswith("https://") or \
                       self.path_or_url.startswith("tally://")

    def _get_http_url(self) -> str:
        url = self.path_or_url
        if url.startswith("tally://"):
            url = "http://" + url[len("tally://"):]
        return url

    def _fetch_from_http(self) -> bytes:
        """Sends standard TDL XML export envelope to Tally's local HTTP server."""
        import requests
        url = self._get_http_url()
        tdl_request_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\r\n'
            '<ENVELOPE>\r\n'
            '  <HEADER>\r\n'
            '    <VERSION>1</VERSION>\r\n'
            '    <TALLYREQUEST>Export</TALLYREQUEST>\r\n'
            '    <TYPE>Data</TYPE>\r\n'
            '    <ID>DayBook</ID>\r\n'
            '  </HEADER>\r\n'
            '  <BODY>\r\n'
            '    <DESC>\r\n'
            '      <STATICVARIABLES>\r\n'
            '        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>\r\n'
            '      </STATICVARIABLES>\r\n'
            '    </DESC>\r\n'
            '  </BODY>\r\n'
            '</ENVELOPE>'
        )
        headers = {"Content-Type": "text/xml; charset=utf-8"}
        try:
            resp = requests.post(url, data=tdl_request_xml.encode("utf-8"), headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            raise ConnectionError(
                f"Could not connect to TallyPrime at {url}. "
                f"Ensure Tally is open with ODBC/HTTP listening enabled on port 9000. Error: {e}"
            )

    def fetch(self, **kwargs) -> pd.DataFrame:
        if self.is_http:
            xml_bytes = self._fetch_from_http()
        else:
            if not os.path.exists(self.path_or_url):
                raise FileNotFoundError(f"Tally export file not found: {self.path_or_url}")
            xml_bytes = decrypt_file_to_bytes(self.path_or_url)

        return parse_tally_xml(xml_bytes)

    def preview(self, n: int = 5, **kwargs) -> pd.DataFrame:
        df = self.fetch(**kwargs)
        return df.head(n)

    def describe(self) -> str:
        if self.is_http:
            return f"TallyPrime Live HTTP/XML Connector connecting to {self._get_http_url()}"
        enc_badge = " (Encrypted At Rest)" if is_encrypted_file(self.path_or_url) else ""
        return f"Tally XML Export Connector reading from {os.path.basename(self.path_or_url)}{enc_badge}"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "live_http": self.is_http,
            "export_xml": not self.is_http,
            "supports_batch_and_expiry": True
        }


class LocalDBConnector(Connector):
    """
    Connector for local relational databases (SQLite and MS Access)
    or Tally (via delegation to TallyConnector).
    """
    def __init__(self, path_or_url: str, db_type: str = "sqlite"):
        self.path_or_url = path_or_url
        self.db_type = db_type.lower()
        
    def _get_sqlite_conn(self):
        return sqlite3.connect(self.path_or_url)
        
    def _get_odbc_conn(self):
        try:
            import pyodbc
        except ImportError:
            raise ImportError(
                "pyodbc is required for MS Access / ODBC connections but is not installed. "
                "Please install it using: pip install pyodbc"
            )
            
        # Example connection string for Access
        conn_str = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={self.path_or_url};"
        )
        try:
            return pyodbc.connect(conn_str)
        except pyodbc.Error as e:
            raise RuntimeError(
                f"Failed to connect to MS Access database. Ensure you have the correct Access ODBC drivers installed. Error: {e}"
            )

    def _get_default_sqlite_table(self) -> Optional[str]:
        """Auto-detect the first user table in a SQLite database."""
        try:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                tables = [r[0] for r in cursor.fetchall()]
                return tables[0] if tables else None
        except Exception:
            return None

    def fetch(self, table_or_query: Optional[str] = None, **kwargs) -> pd.DataFrame:
        if self.db_type == "tally":
            tally_conn = TallyConnector(self.path_or_url)
            return tally_conn.fetch(**kwargs)

        if not table_or_query:
            if self.db_type == "sqlite":
                table_or_query = self._get_default_sqlite_table()
                if not table_or_query:
                    return pd.DataFrame()
            else:
                raise ValueError("Must provide 'table_or_query' to fetch from DB")
            
        is_query = table_or_query.strip().lower().startswith("select ")
        query = table_or_query if is_query else f"SELECT * FROM {table_or_query}"
        
        if self.db_type == "sqlite":
            with self._get_sqlite_conn() as conn:
                df = pd.read_sql_query(query, conn)
        elif self.db_type == "access":
            with self._get_odbc_conn() as conn:
                df = pd.read_sql_query(query, conn)
        else:
            raise ValueError(f"Unsupported db_type: {self.db_type}")
            
        df['source_connector'] = self.db_type
        df['source_row'] = df.index + 1
        return df

    def preview(self, n: int = 5, table_or_query: Optional[str] = None, **kwargs) -> pd.DataFrame:
        if self.db_type == "tally":
            tally_conn = TallyConnector(self.path_or_url)
            return tally_conn.preview(n=n, **kwargs)

        if not table_or_query:
            if self.db_type == "sqlite":
                table_or_query = self._get_default_sqlite_table()
                if not table_or_query:
                    return pd.DataFrame()
            else:
                raise ValueError("Must provide 'table_or_query' to preview from DB")
            
        is_query = table_or_query.strip().lower().startswith("select ")
        query = table_or_query if is_query else f"SELECT * FROM {table_or_query}"
        
        if self.db_type == "sqlite":
            query = f"{query} LIMIT {n}"
            with self._get_sqlite_conn() as conn:
                df = pd.read_sql_query(query, conn)
        elif self.db_type == "access":
            if not is_query:
                query = f"SELECT TOP {n} * FROM {table_or_query}"
            with self._get_odbc_conn() as conn:
                df = pd.read_sql_query(query, conn)
                df = df.head(n)
        else:
            raise ValueError(f"Unsupported db_type: {self.db_type}")
            
        df['source_connector'] = self.db_type
        df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"Local DB Connector ({self.db_type}) reading from {self.path_or_url}"
