import pandas as pd
import chardet
import csv
import io
import os
from typing import Optional, List, Dict, Any
from app.connectors.base import Connector

def _sniff_csv_format(file_path: str) -> tuple[str, str, int]:
    """Detects encoding, delimiter, and the likely header row."""
    # 1. Sniff encoding
    with open(file_path, 'rb') as f:
        raw_data = f.read(10000)
    result = chardet.detect(raw_data)
    encoding = result['encoding'] or 'utf-8'

    # 2. Sniff delimiter and header
    with open(file_path, 'r', encoding=encoding, errors='replace') as f:
        lines = f.readlines()
        
    if not lines:
        return encoding, ',', 0
        
    # Find the first row that actually looks like a header (most columns, no fully empty cells)
    # This skips branding/title rows.
    sniffer = csv.Sniffer()
    sample = "".join(lines[:min(20, len(lines))])
    
    try:
        dialect = sniffer.sniff(sample)
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ','
        
    header_idx = 0
    max_cols = 0
    
    # Use csv.reader to correctly handle quoted commas
    reader = csv.reader(lines[:20], delimiter=delimiter, skipinitialspace=True)
    for i, cols in enumerate(reader):
        if not cols:
            continue
        cols = [c.strip() for c in cols]
        # A good header row has multiple columns and few empty strings
        non_empty = len([c for c in cols if c])
        if non_empty > max_cols:
            max_cols = non_empty
            header_idx = i
            
    return encoding, delimiter, header_idx


class CSVConnector(Connector):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.encoding, self.delimiter, self.header_idx = _sniff_csv_format(file_path)

    def fetch(self, **kwargs) -> pd.DataFrame:
        df = pd.read_csv(
            self.file_path, 
            encoding=self.encoding, 
            sep=self.delimiter, 
            skiprows=self.header_idx,
            skip_blank_lines=True,
            skipinitialspace=True,
            on_bad_lines='skip' # ragged rows handling
        )
        df['source_connector'] = "csv"
        # source_row: keep track of original row (1-indexed based on file, roughly)
        df['source_row'] = df.index + self.header_idx + 2 
        return df

    def preview(self, n: int = 5, **kwargs) -> pd.DataFrame:
        df = pd.read_csv(
            self.file_path, 
            encoding=self.encoding, 
            sep=self.delimiter, 
            skiprows=self.header_idx,
            skip_blank_lines=True,
            skipinitialspace=True,
            on_bad_lines='skip',
            nrows=n
        )
        df['source_connector'] = "csv"
        df['source_row'] = df.index + self.header_idx + 2
        return df

    def describe(self) -> str:
        return f"CSV/Text Connector reading from {os.path.basename(self.file_path)}"


def _find_excel_header(df: pd.DataFrame) -> int:
    """Heuristic to find the true header row in an Excel sheet to skip branding."""
    # Look at first 20 rows. True header usually has the most non-null string values.
    max_non_null = 0
    header_idx = 0
    for i in range(min(20, len(df))):
        row = df.iloc[i]
        non_null_strings = row.apply(lambda x: isinstance(x, str) and str(x).strip() != "").sum()
        if non_null_strings > max_non_null:
            max_non_null = non_null_strings
            header_idx = i
    return header_idx

class ExcelConnector(Connector):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def _read_sheet(self, sheet_name: Optional[str] = None, nrows: Optional[int] = None) -> pd.DataFrame:
        # First read a chunk to find header
        preview_df = pd.read_excel(self.file_path, sheet_name=sheet_name or 0, nrows=30, header=None)
        if preview_df.empty:
            return pd.DataFrame()
            
        header_idx = _find_excel_header(preview_df)
        
        # Now read properly
        df = pd.read_excel(self.file_path, sheet_name=sheet_name or 0, skiprows=header_idx, nrows=nrows)
        # Drop columns that are completely unnamed and empty
        df = df.dropna(axis=1, how='all')
        
        df['source_connector'] = "excel"
        df['source_row'] = df.index + header_idx + 2
        return df

    def fetch(self, sheet_name: Optional[str] = None, **kwargs) -> pd.DataFrame:
        return self._read_sheet(sheet_name=sheet_name)

    def preview(self, n: int = 5, sheet_name: Optional[str] = None, **kwargs) -> pd.DataFrame:
        return self._read_sheet(sheet_name=sheet_name, nrows=n)

    def list_sheets(self) -> List[str]:
        xls = pd.ExcelFile(self.file_path)
        return xls.sheet_names

    def capabilities(self) -> Dict[str, Any]:
        return {"multi_sheet": True}

    def describe(self) -> str:
        return f"Excel Connector reading from {os.path.basename(self.file_path)}"
