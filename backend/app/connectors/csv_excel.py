import pandas as pd
import chardet
import csv
import io
import os
from typing import Optional, List, Dict, Any, Tuple
from app.connectors.base import Connector
from app.security.crypto import decrypt_file_to_bytes, is_encrypted_file


def _get_file_bytes(file_path: str) -> bytes:
    """Read file bytes, decrypting in-memory if encrypted at rest."""
    return decrypt_file_to_bytes(file_path)


def _sniff_csv_format_from_bytes(raw_bytes: bytes) -> Tuple[str, str, int, str]:
    """Detects encoding, delimiter, likely header row, and decoded text from in-memory bytes."""
    # 1. Sniff encoding
    sample_bytes = raw_bytes[:10000]
    result = chardet.detect(sample_bytes)
    encoding = result['encoding'] or 'utf-8'

    # 2. Decode text in memory (never written to disk)
    try:
        text = raw_bytes.decode(encoding, errors='replace')
    except Exception:
        encoding = 'utf-8'
        text = raw_bytes.decode('utf-8', errors='replace')

    lines = text.splitlines(keepends=True)
    if not lines:
        return encoding, ',', 0, text

    # Find the first row that looks like a header
    sniffer = csv.Sniffer()
    sample_str = "".join(lines[:min(20, len(lines))])

    try:
        dialect = sniffer.sniff(sample_str)
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ','
    if len(delimiter) != 1 or delimiter in "\r\n":
        delimiter = ','

    header_idx = 0
    max_cols = 0
    candidates = []

    reader = csv.reader(lines[:20], delimiter=delimiter, skipinitialspace=True)
    for i, cols in enumerate(reader):
        if not cols:
            continue
        cols = [c.strip() for c in cols]
        non_empty = len([c for c in cols if c])
        def is_numeric(value):
            try:
                float(value.replace(',', ''))
                return True
            except ValueError:
                return False
        if non_empty >= 2 and not any(is_numeric(c) for c in cols if c):
            candidates.append((non_empty, i))
        if non_empty > max_cols:
            max_cols = non_empty
            header_idx = i

    if candidates:
        header_idx = max(candidates, key=lambda candidate: candidate[0])[1]

    return encoding, delimiter, header_idx, text


class CSVConnector(Connector):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._bytes = _get_file_bytes(file_path)
        self.encoding, self.delimiter, self.header_idx, self._text = _sniff_csv_format_from_bytes(self._bytes)

    def _read_csv(self, nrows: Optional[int] = None) -> pd.DataFrame:
        options = dict(
            encoding=self.encoding,
            sep=self.delimiter,
            skiprows=self.header_idx,
            skip_blank_lines=True,
            skipinitialspace=True,
            on_bad_lines='error',
        )
        try:
            return pd.read_csv(io.StringIO(self._text), nrows=nrows, **options)
        except pd.errors.ParserError:
            # Some exports leave commas unquoted in a trailing notes/review
            # field. Recover only when the schema explicitly identifies that
            # final column as free text; all other malformed rows remain errors.
            lines = self._text.splitlines()
            header = next(csv.reader(lines[self.header_idx:self.header_idx + 1], delimiter=self.delimiter), [])
            if not header or not any(
                token in str(header[-1]).strip().casefold()
                for token in ("review", "comment", "description", "notes", "remarks", "message", "memo", "feedback")
            ):
                raise
            expected_columns = len(header)

            def repair_trailing_text(fields):
                if len(fields) <= expected_columns:
                    return fields
                return fields[:expected_columns - 1] + [self.delimiter.join(fields[expected_columns - 1:])]

            return pd.read_csv(
                io.StringIO(self._text),
                encoding=self.encoding,
                sep=self.delimiter,
                skiprows=self.header_idx,
                skip_blank_lines=True,
                skipinitialspace=False,
                engine="python",
                on_bad_lines=repair_trailing_text,
                nrows=nrows,
            )

    def fetch(self, **kwargs) -> pd.DataFrame:
        df = self._read_csv()
        df['source_connector'] = "csv"
        df['source_row'] = df.index + self.header_idx + 2
        return df

    def preview(self, n: int = 5, **kwargs) -> pd.DataFrame:
        df = self._read_csv(nrows=n)
        df['source_connector'] = "csv"
        df['source_row'] = df.index + self.header_idx + 2
        return df

    def describe(self) -> str:
        enc_badge = " (Encrypted At Rest)" if is_encrypted_file(self.file_path) else ""
        return f"CSV/Text Connector reading from {os.path.basename(self.file_path)}{enc_badge}"

    def total_rows(self, **kwargs) -> int:
        try:
            lines = self._text.count('\n')
            return max(0, lines - self.header_idx)
        except Exception:
            return 0


def _find_excel_header(df: pd.DataFrame) -> int:
    """Heuristic to find the true header row in an Excel sheet to skip branding."""
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
        self._bytes = _get_file_bytes(file_path)

    def _get_stream(self) -> io.BytesIO:
        """Returns a fresh BytesIO stream of the decrypted in-memory bytes."""
        return io.BytesIO(self._bytes)

    def _read_sheet(self, sheet_name: Optional[str] = None, nrows: Optional[int] = None) -> pd.DataFrame:
        target_sheet = sheet_name or 0
        if sheet_name:
            sheets = self.list_sheets()
            if sheet_name not in sheets:
                raise ValueError(f"Excel sheet '{sheet_name}' does not exist. Available sheets: {', '.join(sheets)}")

        # First read a chunk to find header
        preview_df = pd.read_excel(self._get_stream(), sheet_name=target_sheet, nrows=30, header=None)
        if preview_df.empty:
            return pd.DataFrame()

        header_idx = _find_excel_header(preview_df)

        # Now read properly
        df = pd.read_excel(self._get_stream(), sheet_name=target_sheet, skiprows=header_idx, nrows=nrows)
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
        xls = pd.ExcelFile(self._get_stream())
        return xls.sheet_names

    def capabilities(self) -> Dict[str, Any]:
        return {"multi_sheet": True}

    def describe(self) -> str:
        enc_badge = " (Encrypted At Rest)" if is_encrypted_file(self.file_path) else ""
        return f"Excel Connector reading from {os.path.basename(self.file_path)}{enc_badge}"

    def total_rows(self, sheet_name: Optional[str] = None, **kwargs) -> int:
        try:
            target_sheet = sheet_name or 0
            if sheet_name:
                sheets = self.list_sheets()
                if sheet_name not in sheets:
                    raise ValueError(f"Excel sheet '{sheet_name}' does not exist. Available sheets: {', '.join(sheets)}")
            df = pd.read_excel(self._get_stream(), sheet_name=target_sheet, usecols=[0])
            return len(df)
        except Exception:
            return 0
