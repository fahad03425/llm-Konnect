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


def detect_connector(path: str) -> Connector:
    """
    Route a file path to the appropriate file-based connector.
    """
    ext = os.path.splitext(path)[1].lower()
    
    if ext in ('.csv', '.txt', '.tsv'):
        from app.connectors.csv_excel import CSVConnector
        return CSVConnector(path)
    elif ext in ('.xlsx', '.xls', '.xlsm'):
        from app.connectors.csv_excel import ExcelConnector
        return ExcelConnector(path)
    elif ext in ('.json', '.jsonl'):
        from app.connectors.json import JSONConnector
        return JSONConnector(path)
    elif ext in ('.sqlite', '.db', '.sqlite3'):
        from app.connectors.tally import LocalDBConnector
        return LocalDBConnector(path, db_type="sqlite")
    elif ext in ('.mdb', '.accdb'):
        from app.connectors.tally import LocalDBConnector
        return LocalDBConnector(path, db_type="access")
    else:
        raise ValueError(f"Unsupported file extension: {ext}. Cannot detect appropriate connector.")
