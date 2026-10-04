"""
Module 6.3 (Schema Mapping) — normalize.py
Centralizes all type coercion for canonical fields.
Connectors return raw DataFrames; this module owns mapping + coercion.

Offline, no network calls. Encoding-safe (handles Urdu digits).
"""
import pandas as pd
import numpy as np
import re
import warnings
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

    # ISO format YYYY-MM-DD or YYYY/MM/DD
    if re.match(r'^\d{4}[/-]\d{1,2}[/-]\d{1,2}', val_str):
        try:
            return pd.to_datetime(val_str, yearfirst=True, dayfirst=False)
        except Exception:
            pass

    # Date with delimiters: determine if month-first (e.g. 09-20-2026 where 20 > 12) or day-first
    dmy_match = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', val_str)
    if dmy_match:
        first, second = int(dmy_match.group(1)), int(dmy_match.group(2))
        is_day_first = False if (first <= 12 < second) else True
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return pd.to_datetime(val_str, dayfirst=is_day_first)
        except Exception:
            pass

    # General parse fallback
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return pd.to_datetime(val_str, dayfirst=True)
    except Exception:
        return pd.NaT


def _clean_text(val: any) -> Optional[str]:
    """Normalize a text value: collapse whitespace, strip, return None if empty."""
    if pd.isna(val):
        return None
    val_str = re.sub(r'\s+', ' ', str(val)).strip()
    return val_str if val_str else None


def _clean_percent(val: any) -> Optional[float]:
    """Parse percentages such as ``10% Off`` without losing the percent value."""
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    text = _convert_urdu_digits(str(val)).strip()
    text = re.sub(r"(?i)\boff\b", "", text).replace("%", "").strip()
    return _clean_money(text)


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
    "pharmacy": frozenset({
        "mrp", "reorder_level", "reorder_quantity", "safety_stock_qty", "bonus_quantity", "net_payable", "tax_amount",
        "original_price", "discounted_price", "line_cost", "sub_items",
        "discount_amount", "tax_pct", "margin_pct", "discount_pct", "total_qty",
        "total_bonus", "total_items", "total_pack", "line_discount_amount",
        "line_tax_amount", "invoice_discount", "invoice_tax", "carriage_charges",
        "other_charges", "invoice_total", "paid_amount", "customer_balance",
        "previous_balance", "invoice_tax_pct", "invoice_discount_pct", "sales_subtotal",
    }),
    "ecommerce": frozenset({
        "sale_amount", "gross_amount", "net_amount", "discount_amount",
        "refund_amount", "shipping_amount", "tax_amount", "cost_per_item",
        "rating"
    }),
}

# Pack size is numeric but sometimes text ("10×10") — treat as text to be safe.
# scheme is a text label ("3+1", "buy 2 get 1") — treat as text.

_DATE_FIELDS = frozenset({"date", "expiry_date", "mfg_date", "customer_due_date", "supplier_payment_due_date", "last_sold_date"})
_PERCENT_FIELDS = frozenset({"discount_pct", "tax_pct", "margin_pct", "invoice_tax_pct", "invoice_discount_pct"})
_SKIP_COERCE = frozenset({"source_connector", "source_row"})


def _get_money_fields(domain: str) -> frozenset:
    return _MONEY_FIELDS | _DOMAIN_MONEY_FIELDS.get(domain, frozenset())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_mapping(raw: pd.DataFrame, mapping: Dict[str, str], domain: Optional[str] = None) -> None:
    from app.schema.mapper import get_canonical_fields
    from app.schema.domain import get_domain_pack
    pack = get_domain_pack(domain) if domain else None
    allowed = set(get_canonical_fields(pack))
    targets = set()
    for source, target in mapping.items():
        if source not in raw.columns:
            raise ValueError(f"Mapping source column '{source}' does not exist")
        if target not in allowed:
            raise ValueError(f"Unknown canonical field '{target}'")
        if target in targets:
            raise ValueError(f"Multiple columns map to '{target}'. Choose one source column.")
        targets.add(target)
    if raw.columns.duplicated().any():
        raise ValueError("Source has duplicate column names; rename them before mapping")
    for target in targets:
        if target in raw.columns and target not in mapping:
            raise ValueError(f"Mapping would overwrite unmapped column '{target}'. Map or remove the conflict.")


def _clean_ecommerce_date(value):
    """E-commerce date handling; pharmacy's established parser stays unchanged."""
    if pd.isna(value):
        return pd.NaT
    try:
        number = float(value)
        if 20000 <= number <= 100000:
            return pd.Timestamp('1899-12-30') + pd.Timedelta(days=number)
    except (ValueError, TypeError):
        pass
    parsed = _clean_date(value)
    if pd.notna(parsed) and parsed.tzinfo is not None:
        parsed = parsed.tz_convert('UTC').tz_localize(None)
    return parsed

def normalize(
    raw: pd.DataFrame,
    mapping: Dict[str, str],
    domain: Optional[str] = None,
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
    validate_mapping(raw, mapping, domain)
    df = raw.copy()

    # 1. Rename mapped columns
    df = df.rename(columns=mapping)

    # 2. Identify columns to keep
    keep_cols = list(dict.fromkeys(mapping.values()))  # mapped canonical fields, in order

    for tc in ("source_connector", "source_row"):
        if tc in df.columns and tc not in keep_cols:
            keep_cols.append(tc)

    # Preserve original reference keys even when a user maps them to another
    # canonical name. Database reconciliation must use the actual primary key.
    if keep_extras:
        from app.schema.mapper import _normalize_header_string
        for source in mapping:
            if re.search(r"\b(?:id|code|number|no)\b", _normalize_header_string(source)):
                extra = f"_extra.{source}"
                if extra not in df.columns:
                    df[extra] = raw[source]
                keep_cols.append(extra)

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
    failures = []
    for col in df.columns:
        if col in _SKIP_COERCE or col.startswith("_extra."):
            continue
        original = df[col].copy()
        is_date = col in _DATE_FIELDS or (domain == 'ecommerce' and col == 'order_date')
        if is_date:
            parser = _clean_ecommerce_date if domain == 'ecommerce' else _clean_date
            df[col] = df[col].apply(parser)
        elif col in _PERCENT_FIELDS:
            df[col] = df[col].apply(_clean_percent)
        elif col in money_fields:
            df[col] = df[col].apply(_clean_money)
        else:
            df[col] = df[col].apply(_clean_text)
        if is_date or col in money_fields or col in _PERCENT_FIELDS:
            present = original.notna() & original.astype(str).str.strip().ne('')
            for position in np.flatnonzero((present & df[col].isna()).to_numpy()):
                reference = raw.iloc[position]['source_row'] if 'source_row' in raw.columns else position + 1
                try:
                    reference = int(reference)
                except (TypeError, ValueError, OverflowError):
                    reference = position + 1
                failures.append({'field': col, 'source_row': reference, 'value': str(original.iloc[position]),
                                 'code': 'UNPARSEABLE_DATE' if is_date else 'UNPARSEABLE_NUMBER'})
    df.attrs['conversion_failures'] = failures
    if domain == 'ecommerce':
        # Keep domain names and add core aliases for shared RAG date filters/KPIs.
        for alias, core in [('order_date', 'date'), ('sale_amount', 'amount')]:
            if alias in df and core not in df:
                df[core] = df[alias]

    # Some pharmaceutical price-list exports contain row-level column shifts:
    # the Availability status is stored in Pack_Size and the pack description
    # in Availability. Repair only unambiguous pairs, and only for the known
    # catalog schema (paired before/after prices + discount percentage). This
    # preserves genuine free-text statuses and never guesses from pack syntax.
    if (
        domain == "pharmacy"
        and {"pack_size", "availability", "original_price", "discounted_price", "discount_pct"}.issubset(df.columns)
    ):
        stock_statuses = {
            "available", "in stock", "low stock", "out of stock", "sold out",
            "unavailable", "not available", "discontinued", "add to cart",
        }
        pack_is_status = df["pack_size"].fillna("").astype(str).str.strip().str.casefold().isin(stock_statuses)
        availability_is_status = df["availability"].fillna("").astype(str).str.strip().str.casefold().isin(stock_statuses)
        swapped = pack_is_status & ~availability_is_status & df["availability"].notna()
        if swapped.any():
            original_pack = df.loc[swapped, "pack_size"].copy()
            df.loc[swapped, "pack_size"] = df.loc[swapped, "availability"]
            df.loc[swapped, "availability"] = original_pack

    # Some purchase exports put the settlement mode (Cash/Credit) in a column
    # called Transaction_Type. Preserve it as payment_method and infer the
    # transaction direction only when the row also has supplier purchase fields.
    if domain == "pharmacy" and "txn_type" in df.columns:
        values = set(df["txn_type"].dropna().astype(str).str.casefold().str.strip())
        payment_labels = {"cash", "credit", "debit", "card", "online", "bank transfer", "cheque", "check"}
        if values and values.issubset(payment_labels):
            if "payment_method" not in df.columns:
                df["payment_method"] = df["txn_type"]
            is_purchase_export = (
                "supplier_id" in df.columns
                and any(col in df.columns for col in ("net_payable", "cost", "purchase_order_no"))
            )
            df["txn_type"] = "Purchase" if is_purchase_export else "Sale"

    if "quantity" not in df.columns and "stock_qty" in df.columns:
        df["quantity"] = pd.to_numeric(df["stock_qty"], errors="coerce")

    # A single-table export often calls its only transaction value "Total
    # Amount". Keep the explicit invoice_total field, and expose it as amount
    # only when there is no separate line-level amount to avoid double-counting
    # invoice totals alongside their detail rows.
    if domain == "pharmacy" and "amount" not in df.columns and "invoice_total" in df.columns:
        df["amount"] = df["invoice_total"]

    return df


def apply_mapping(
    raw_df: pd.DataFrame,
    mapping: Dict[str, str],
    domain: Optional[str] = None,
    keep_extras: bool = True,
) -> pd.DataFrame:
    """
    Module 6.3 public name for normalize().
    keep_extras defaults to True — no data is silently lost.
    """
    return normalize(raw_df, mapping, domain, keep_extras)
