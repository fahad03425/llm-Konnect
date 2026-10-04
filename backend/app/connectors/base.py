from abc import ABC, abstractmethod
import pandas as pd
from typing import Any, Dict
import os

class Connector(ABC):
    """
    Abstract base class for all data connectors.
    Part of the pharmacy-first launch. Offline unless specified otherwise.
    """
    
    @abstractmethod
    def fetch(self, **kwargs) -> pd.DataFrame:
        """Fetch all data. Returns a raw pandas DataFrame."""
        pass
        
    @abstractmethod
    def preview(self, n: int = 5, **kwargs) -> pd.DataFrame:
        """Fetch a preview of data (first n rows). Returns a raw pandas DataFrame."""
        pass
        
    @abstractmethod
    def describe(self) -> str:
        """Returns a string describing the connector."""
        pass

    def capabilities(self) -> Dict[str, Any]:
        """Return a dict describing what this connector supports (e.g. multi-sheet, auth)."""
        return {}

    def total_rows(self, **kwargs) -> int:
        """Return total row count of the data source if available, or 0."""
        return 0


def detect_connector(path: str) -> Connector:
    """
    Route a file path to the appropriate file-based connector.
    """
    ext = os.path.splitext(path)[1].lower()
    
    if ext in ('.csv', '.txt', '.tsv'):
        from app.connectors.csv_excel import CSVConnector
        return CSVConnector(path)
    elif ext in ('.xlsx', '.xls', '.xlsm', '.xlx'):
        from app.connectors.csv_excel import ExcelConnector
        return ExcelConnector(path)
    elif ext in ('.json', '.jsonl'):
        from app.connectors.json import JSONConnector
        return JSONConnector(path)
    elif ext in ('.sqlite', '.db', '.sqlite3', '.stardb'):
        from app.connectors.tally import LocalDBConnector
        return LocalDBConnector(path, db_type="sqlite")
    elif ext == '.mdf':
        from app.connectors.sql import SQLConnector
        return SQLConnector(path, db_type="mssql")
    elif ext in ('.mdb', '.accdb'):
        from app.connectors.tally import LocalDBConnector
        return LocalDBConnector(path, db_type="access")
    elif path.startswith("shopify://"):
        from app.connectors.shopify import ShopifyConnector
        return ShopifyConnector.from_url(path)
    elif ext == '.xml' or path.startswith("tally://") or path.startswith("tally+odbc://") or path.startswith("http://") or path.startswith("https://"):
        from app.connectors.tally import TallyConnector
        return TallyConnector(path)
    else:
        raise ValueError(f"Unsupported file extension: {ext}. Cannot detect appropriate connector.")


def is_network_or_custom_source(path_or_url: str) -> bool:
    """Checks if path is an HTTP, Tally, Shopify, SQL, or custom scheme source."""
    if not path_or_url or not isinstance(path_or_url, str):
        return False
    return (
        path_or_url.startswith("http://")
        or path_or_url.startswith("https://")
        or path_or_url.startswith("tally://")
        or path_or_url.startswith("tally+odbc://")
        or path_or_url.startswith("shopify://")
        or path_or_url.startswith("sql://")
        or path_or_url.startswith("db://")
    )


def source_exists(path_or_url: str) -> bool:
    """Checks if a local file exists or if a network/custom URL is structurally valid."""
    if not path_or_url or not isinstance(path_or_url, str):
        return False
    if is_network_or_custom_source(path_or_url):
        return True
    return os.path.exists(path_or_url)
