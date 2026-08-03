"""
Module 6.3 (Schema Mapping) — normalize.py
Centralizes all type coercion for canonical fields.
Connectors return raw DataFrames; this module owns mapping + coercion.

Offline, no network calls. Encoding-safe (handles Urdu digits).
"""
import pandas as pd
import numpy as np
import re
from typing import Optional, Dict

# ---------------------------------------------------------------------------
# Urdu digit conversion
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------

def _clean_money(val: any) -> Optional[float]:
    """Parse a monetary/numeric value to float. Returns None on failure."""
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)

    val_str = str(val).strip()
    if not val_str:
        return None

    val_str = _convert_urdu_digits(val_str)

    # (100) → -100
    is_negative = False
    if val_str.startswith('(') and val_str.endswith(')'):
        is_negative = True
        val_str = val_str[1:-1]

    # Strip currency symbols
    val_str = re.sub(r'(?i)(rs\.?|pkr|₨)', '', val_str)
    # Strip commas, spaces, trailing /-
    val_str = val_str.replace(',', '').replace(' ', '').replace('/-', '').replace('/=', '')

    try:
        num = float(val_str)
        return -num if is_negative else num
    except ValueError:
        return None


def _clean_date(val: any) -> pd.Timestamp:
    """
    Parse a date value to pd.Timestamp.
    Handles: Timestamp, mm/yy, mm-yyyy, Excel serial numbers, ISO strings,
    Pakistani day-first format, and Urdu digits.
    """
    if pd.isna(val):
        return pd.NaT

    if isinstance(val, pd.Timestamp):
        return val
    if isinstance(val, pd.DatetimeIndex):
        return val[0] if len(val) else pd.NaT

    val_str = _convert_urdu_digits(str(val).strip())
    if not val_str:
        return pd.NaT

    # mm/yy or mm-yyyy → last day of that month (common for expiry dates)
    mmyy_match = re.match(r'^(\d{1,2})[/-](\d{2,4})$', val_str)
    if mmyy_match:
        month = int(mmyy_match.group(1))
        year_str = mmyy_match.group(2)
        year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
        if 1 <= month <= 12:
            try:
                if month == 12:
                    next_month = pd.Timestamp(year=year + 1, month=1, day=1)
                else:
                    next_month = pd.Timestamp(year=year, month=month + 1, day=1)
                return next_month - pd.Timedelta(days=1)
            except ValueError:
                pass

    # Excel serial date (5 digits, e.g. 45000)
    if val_str.isdigit() and len(val_str) == 5:
        try:
            return pd.to_datetime('1899-12-30') + pd.Timedelta(days=int(val_str))
        except Exception:
            pass

    # General parse — dayfirst for Pakistani convention
    try:
        return pd.to_datetime(val_str, dayfirst=True)
    except Exception:
        return pd.NaT


def _clean_text(val: any) -> Optional[str]:
    """Normalize a text value: collapse whitespace, strip, return None if empty."""
    if pd.isna(val):
        return None
    val_str = re.sub(r'\s+', ' ', str(val)).strip()
    return val_str if val_str else None


# ---------------------------------------------------------------------------
# Coercion field sets
# NOTE: money_fields must only contain truly numeric canonical fields.
#       Text-like fields (scheme, rack_location, description, etc.) must NOT
#       be included — they would be silently nulled by _clean_money.
# ---------------------------------------------------------------------------

# Core numeric fields (domain-agnostic)
_MONEY_FIELDS = frozenset({
    "quantity", "unit_price", "amount", "cost", "tax", "discount",
})

# Pharmacy numeric extras — added here to avoid pharmacy vocabulary in core code.
# When a new domain pack adds numeric fields, extend _DOMAIN_MONEY_FIELDS.
_DOMAIN_MONEY_FIELDS: Dict[str, frozenset] = {
    "pharmacy": frozenset({"mrp", "reorder_level"}),
}

# Pack size is numeric but sometimes text ("10×10") — treat as text to be safe.
# scheme is a text label ("3+1", "buy 2 get 1") — treat as text.

_DATE_FIELDS = frozenset({"date", "expiry_date", "mfg_date"})
_SKIP_COERCE = frozenset({"source_connector", "source_row"})


def _get_money_fields(domain: str) -> frozenset:
    return _MONEY_FIELDS | _DOMAIN_MONEY_FIELDS.get(domain, frozenset())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(
    raw: pd.DataFrame,
    mapping: Dict[str, str],
    domain: str = "pharmacy",
    keep_extras: bool = False,
) -> pd.DataFrame:
    """
    Apply schema mapping and coerce dtypes.

    Steps:
      1. Rename mapped source columns to their canonical names.
      2. Optionally rename unmapped columns to _extra.<name>.
      3. Drop anything not in keep_cols.
      4. Coerce dtypes: money → float, date → Timestamp, rest → str.

    Args:
        raw:         Raw DataFrame from a connector (unchanged).
        mapping:     {source_col: canonical_field} — confirmed by user.
        domain:      Active domain name for domain-specific money fields.
        keep_extras: If True, unmapped columns survive as _extra.<name>.

    Returns:
        Canonical DataFrame.
    """
    # Work on a copy; never mutate the raw input
    df = raw.copy()

    # 1. Rename mapped columns
    df = df.rename(columns=mapping)

    # 2. Identify columns to keep
    keep_cols = list(dict.fromkeys(mapping.values()))  # mapped canonical fields, in order

    for tc in ("source_connector", "source_row"):
        if tc in df.columns and tc not in keep_cols:
            keep_cols.append(tc)

    # 3. Handle extras — rename BEFORE column selection
    #    Iterate over ORIGINAL column names (pre-rename) to find unmapped ones.
    if keep_extras:
        for orig_col in raw.columns:
            if orig_col in mapping:
                continue  # already renamed above
            if orig_col in ("source_connector", "source_row"):
                continue
            extra_name = f"_extra.{orig_col}"
            if orig_col in df.columns:
                df = df.rename(columns={orig_col: extra_name})
            keep_cols.append(extra_name)

    # 4. Deduplicate keep_cols, filter to present columns, drop duplicate cols
    df = df.loc[:, ~df.columns.duplicated()]
    keep_cols = [c for c in dict.fromkeys(keep_cols) if c in df.columns]
    df = df[keep_cols].copy()

    # 5. Coerce dtypes
    money_fields = _get_money_fields(domain)
    for col in df.columns:
        if col in _SKIP_COERCE or col.startswith("_extra."):
            continue
        if col in _DATE_FIELDS:
            df[col] = df[col].apply(_clean_date)
        elif col in money_fields:
            df[col] = df[col].apply(_clean_money)
        else:
            df[col] = df[col].apply(_clean_text)

    return df


def apply_mapping(
    raw_df: pd.DataFrame,
    mapping: Dict[str, str],
    domain: str = "pharmacy",
    keep_extras: bool = True,
) -> pd.DataFrame:
    """
    Module 6.3 public name for normalize().
    keep_extras defaults to True — no data is silently lost.
    """
    return normalize(raw_df, mapping, domain, keep_extras)
