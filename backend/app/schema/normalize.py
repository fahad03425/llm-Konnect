import pandas as pd
import numpy as np
import re
from typing import Optional, Dict

URDU_DIGITS_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9'
}

def _convert_urdu_digits(text: str) -> str:
    if not isinstance(text, str):
        return text
    for urdu_digit, ascii_digit in URDU_DIGITS_MAP.items():
        text = text.replace(urdu_digit, ascii_digit)
    return text

def _clean_money(val: any) -> Optional[float]:
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    
    val_str = str(val).strip()
    if not val_str:
        return None
        
    val_str = _convert_urdu_digits(val_str)
    
    # Check for negative in parentheses e.g. (100) -> -100
    is_negative = False
    if val_str.startswith('(') and val_str.endswith(')'):
        is_negative = True
        val_str = val_str[1:-1]
    
    # Strip common currency symbols and spaces
    val_str = re.sub(r'(?i)(rs\.?|pkr|₨)', '', val_str)
    # Strip commas, spaces, and common trailing dashes like /-
    val_str = val_str.replace(',', '').replace(' ', '').replace('/-', '').replace('/=', '')
    
    try:
        num = float(val_str)
        return -num if is_negative else num
    except ValueError:
        return None

def _clean_date(val: any) -> pd.Timestamp:
    if pd.isna(val):
        return pd.NaT
        
    if isinstance(val, (pd.Timestamp, pd.DatetimeIndex)):
        return val
        
    val_str = str(val).strip()
    if not val_str:
        return pd.NaT

    # Detect mm/yy or mm-yy or mm/yyyy (common for expiry) without a day
    # if it's purely mm/yy or mm-yyyy, we default to the end of the month
    mmyy_match = re.match(r'^(\d{1,2})[/-](\d{2,4})$', val_str)
    if mmyy_match:
        month = int(mmyy_match.group(1))
        year_str = mmyy_match.group(2)
        year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
        if 1 <= month <= 12:
            try:
                # Get last day of the month
                if month == 12:
                    next_month = pd.Timestamp(year=year+1, month=1, day=1)
                else:
                    next_month = pd.Timestamp(year=year, month=month+1, day=1)
                return next_month - pd.Timedelta(days=1)
            except ValueError:
                pass

    # Detect Excel serial dates (typically ~40000+ for recent dates)
    if val_str.isdigit() and len(val_str) == 5:
        try:
            return pd.to_datetime('1899-12-30') + pd.Timedelta(days=int(val_str))
        except Exception:
            pass

    # Try parsing generally, Pakistani format is dayfirst
    try:
        return pd.to_datetime(val, dayfirst=True)
    except Exception:
        return pd.NaT

def _clean_text(val: any) -> Optional[str]:
    if pd.isna(val):
        return None
    val_str = str(val)
    # Collapse whitespace and trim
    val_str = re.sub(r'\s+', ' ', val_str).strip()
    return val_str if val_str else None

def normalize(raw: pd.DataFrame, mapping: Dict[str, str], domain: str = "pharmacy") -> pd.DataFrame:
    """
    Applies the schema mapping and normalizes the data.
    """
    # 1. Rename columns based on mapping
    df = raw.rename(columns=mapping)
    
    # 2. Drop columns that were not mapped, EXCEPT keep source_connector and source_row if they exist
    keep_cols = list(mapping.values())
    if "source_connector" in df.columns:
        keep_cols.append("source_connector")
    if "source_row" in df.columns:
        keep_cols.append("source_row")
        
    # Only keep columns that are in our df (in case mapping had fields not present in raw)
    keep_cols = [c for c in keep_cols if c in df.columns]
    
    # Keep only the mapped columns + traceability
    # Handle duplicate columns if any mapping collided
    df = df.loc[:, ~df.columns.duplicated()]
    # Deduplicate keep_cols while preserving order
    keep_cols = list(dict.fromkeys(keep_cols))
    df = df[keep_cols].copy()
    
    # 3. Apply normalizations based on column name
    money_fields = {"quantity", "unit_price", "amount", "cost", "tax", "discount", "mrp", "pack_size", "scheme"}
    date_fields = {"date", "expiry_date", "mfg_date"}
    
    for col in df.columns:
        if col in money_fields:
            df[col] = df[col].apply(_clean_money)
        elif col in date_fields:
            df[col] = df[col].apply(_clean_date)
        elif col not in ["source_connector", "source_row"]:
            df[col] = df[col].apply(_clean_text)
            
    return df
