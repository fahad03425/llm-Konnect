import datetime
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from app.schema.domain import Problem

# Core canonical fields (domain-agnostic)
CORE_FIELDS = [
    "date", "description", "category", "quantity", "unit_price",
    "amount", "cost", "tax", "discount", "invoice_id", "product_id",
    "customer_id", "supplier_id", "txn_type", "payment_method",
    "source_connector", "source_row"  # Traceability columns
]

# Canonical numeric fields — UNPARSEABLE_NUMBER is raised when coercion produces NaN
# from a non-null original value.  Keep domain-agnostic; domain packs add their own
# numeric fields in normalize._DOMAIN_MONEY_FIELDS without editing this set.
_NUMERIC_CORE_FIELDS = frozenset({
    "quantity", "unit_price", "amount", "cost", "tax", "discount",
})

def _get_row_refs(df: pd.DataFrame, mask: pd.Series) -> List[int]:
    """Helper to extract source_row numbers or 1-based index numbers for matching mask."""
    if "source_row" in df.columns:
        refs = df.loc[mask, "source_row"].tolist()
        # Clean up any NaNs in source_row
        cleaned_refs = []
        for idx, r in zip(df.index[mask], refs):
            if pd.notna(r):
                try:
                    cleaned_refs.append(int(r))
                except (ValueError, TypeError):
                    cleaned_refs.append(int(idx) + 1)
            else:
                cleaned_refs.append(int(idx) + 1)
        return cleaned_refs
    else:
        return (df.index[mask] + 1).tolist()


def validate_core_dataframe(df: pd.DataFrame, table_kind: str = "auto") -> List[Problem]:
    """
    Domain-agnostic core validation rules evaluated over a canonical DataFrame.
    Returns a list of structured Problem objects with row references, counts, and samples.
    """
    problems: List[Problem] = []

    if df.empty:
        return problems

    # Columns to inspect for empty check (exclude traceability and unnamed)
    data_cols = [
        c for c in df.columns 
        if c not in ("source_connector", "source_row") 
        and not str(c).startswith("Unnamed:")
        and not str(c).startswith("_extra.")
    ]

    if not data_cols:
        return problems

    # 1. EMPTY_ROW (error)
    # Row is completely empty if all data columns are null or empty string
    empty_mask = df[data_cols].apply(
        lambda col: col.isna() | (col.astype(str).str.strip() == "") | (col.astype(str) == "nan") | (col.astype(str) == "None")
    ).all(axis=1)

    if empty_mask.any():
        row_refs = _get_row_refs(df, empty_mask)
        problems.append(
            Problem(
                severity="error",
                code="EMPTY_ROW",
                message="Row is completely empty (all canonical data fields are missing)",
                field=None,
                row_refs=row_refs,
                sample=row_refs[:5]
            )
        )

    # Work on non-empty rows for remaining checks
    valid_df = df[~empty_mask].copy()
    if valid_df.empty:
        return problems

    # 2. DUPLICATE_ROW (warning)
    dup_mask_valid = valid_df.duplicated(subset=data_cols, keep="first")
    if dup_mask_valid.any():
        dup_mask_full = pd.Series(False, index=df.index)
        dup_mask_full.loc[valid_df.index[dup_mask_valid]] = True
        row_refs = _get_row_refs(df, dup_mask_full)
        problems.append(
            Problem(
                severity="warning",
                code="DUPLICATE_ROW",
                message="Exact duplicate record detected across canonical data fields",
                field=None,
                row_refs=row_refs,
                sample=row_refs[:5]
            )
        )

    # Determine table kind if auto
    has_date_data = "date" in valid_df.columns and valid_df["date"].notna().any()
    has_prod_data = ("product_id" in valid_df.columns or "description" in valid_df.columns)
    
    if table_kind == "auto":
        if not has_date_data and has_prod_data:
            inferred_kind = "inventory"
        else:
            inferred_kind = "transactions"
    else:
        inferred_kind = table_kind


    # 3. MISSING_REQUIRED_FIELD (error)
    if inferred_kind == "inventory":
        # Inventory needs product_id or description
        has_prod = pd.Series(False, index=valid_df.index)
        if "product_id" in valid_df.columns:
            has_prod = valid_df["product_id"].notna() & (valid_df["product_id"].astype(str).str.strip() != "")
        if "description" in valid_df.columns:
            has_prod = has_prod | (valid_df["description"].notna() & (valid_df["description"].astype(str).str.strip() != ""))
        
        req_missing = ~has_prod
        if req_missing.any():
            mask_full = pd.Series(False, index=df.index)
            mask_full.loc[valid_df.index[req_missing]] = True
            row_refs = _get_row_refs(df, mask_full)
            problems.append(
                Problem(
                    severity="error",
                    code="MISSING_REQUIRED_FIELD",
                    message="Missing required field: inventory row must specify a product name/ID",
                    field="product_id",
                    row_refs=row_refs,
                    sample=row_refs[:5]
                )
            )
    else:
        # Transactions need date and amount
        missing_date = pd.Series(True, index=valid_df.index)
        if "date" in valid_df.columns:
            missing_date = valid_df["date"].isna() | (valid_df["date"].astype(str).str.strip() == "")
            
        missing_amount = pd.Series(True, index=valid_df.index)
        if "amount" in valid_df.columns:
            missing_amount = valid_df["amount"].isna() | (valid_df["amount"].astype(str).str.strip() == "")

        req_missing = missing_date | missing_amount
        if req_missing.any():
            mask_full = pd.Series(False, index=df.index)
            mask_full.loc[valid_df.index[req_missing]] = True
            row_refs = _get_row_refs(df, mask_full)
            problems.append(
                Problem(
                    severity="error",
                    code="MISSING_REQUIRED_FIELD",
                    message="Missing required fields: transaction row must contain both 'date' and 'amount'",
                    field="date" if missing_date.any() else "amount",
                    row_refs=row_refs,
                    sample=row_refs[:5]
                )
            )

    # 4. UNPARSEABLE_NUMBER (warning)
    # A value is "present but unparseable" when:
    #   - the original cell is not null/blank (col.notna() and not whitespace-only)
    #   - AND pd.to_numeric(value, errors='coerce') produces NaN
    # Using pd.to_numeric is correct here: it handles both the raw-string case
    # ("abc" is still "abc" in the column) and any post-coercion NaN detection.
    # We deliberately skip cells that are already NaN/None so they are caught
    # by MISSING_REQUIRED_FIELD instead.
    for num_field in _NUMERIC_CORE_FIELDS:
        if num_field not in valid_df.columns:
            continue
        col = valid_df[num_field]
        # "Present" = the cell is non-null AND its string form is not an empty / sentinel.
        raw_str = col.astype(str).str.strip()
        raw_present = (
            col.notna()
            & raw_str.ne("")
            & raw_str.ne("nan")
            & raw_str.ne("None")
            & raw_str.ne("NaT")
        )
        # Attempt numeric coercion on the original column.
        coerced_num = pd.to_numeric(col, errors="coerce")
        bad_mask = raw_present & coerced_num.isna()
        if bad_mask.any():
            mask_full = pd.Series(False, index=df.index)
            mask_full.loc[valid_df.index[bad_mask]] = True
            row_refs = _get_row_refs(df, mask_full)
            samples = valid_df.loc[bad_mask, num_field].astype(str).tolist()[:5]
            problems.append(
                Problem(
                    severity="warning",
                    code="UNPARSEABLE_NUMBER",
                    message=f"Field '{num_field}' contains a value that could not be parsed as a number",
                    field=num_field,
                    row_refs=row_refs,
                    count=len(row_refs),
                    sample=samples,
                )
            )

    # 5. UNPARSEABLE_DATE (error)
    # Only fire for rows where the original date cell is non-null and non-blank
    # but pd.to_datetime coercion fails (produces NaT).
    # Rows that were simply blank date cells are caught by MISSING_REQUIRED_FIELD.
    if "date" in valid_df.columns:
        date_col = valid_df["date"]
        raw_date_str = date_col.astype(str).str.strip()
        raw_date_present = (
            date_col.notna()
            & raw_date_str.ne("")
            & raw_date_str.ne("nan")
            & raw_date_str.ne("None")
            & raw_date_str.ne("NaT")
        )
        # Attempt date coercion; detect non-parseable non-null values.
        coerced_date = pd.to_datetime(date_col, errors="coerce")
        unparseable_date_mask = raw_date_present & coerced_date.isna()
        if unparseable_date_mask.any() and inferred_kind == "transactions":
            mask_full = pd.Series(False, index=df.index)
            mask_full.loc[valid_df.index[unparseable_date_mask]] = True
            row_refs = _get_row_refs(df, mask_full)
            problems.append(
                Problem(
                    severity="error",
                    code="UNPARSEABLE_DATE",
                    message="Date field contains a value that could not be parsed as a valid date",
                    field="date",
                    row_refs=row_refs,
                    sample=row_refs[:5],
                )
            )

    # 6. INVALID_DATE_RANGE (warning)
    if "date" in valid_df.columns:
        parsed_dates = pd.to_datetime(valid_df["date"], errors="coerce")
        today = pd.Timestamp(datetime.date.today() + datetime.timedelta(days=1))
        old_cutoff = pd.Timestamp("1990-01-01")

        future_mask = parsed_dates.notna() & (parsed_dates > today)
        old_mask = parsed_dates.notna() & (parsed_dates < old_cutoff)
        range_mask = future_mask | old_mask

        if range_mask.any():
            mask_full = pd.Series(False, index=df.index)
            mask_full.loc[valid_df.index[range_mask]] = True
            row_refs = _get_row_refs(df, mask_full)
            samples = parsed_dates[range_mask].dt.strftime("%Y-%m-%d").tolist()[:5]
            problems.append(
                Problem(
                    severity="warning",
                    code="INVALID_DATE_RANGE",
                    message="Transaction date is implausibly far in the future or older than 1990",
                    field="date",
                    row_refs=row_refs,
                    sample=samples,
                )
            )

    # 7. NEGATIVE_QUANTITY (warning)
    if "quantity" in valid_df.columns:
        qty_num = pd.to_numeric(valid_df["quantity"], errors="coerce")
        neg_qty_mask = qty_num.notna() & (qty_num < 0)
        if neg_qty_mask.any():
            mask_full = pd.Series(False, index=df.index)
            mask_full.loc[valid_df.index[neg_qty_mask]] = True
            row_refs = _get_row_refs(df, mask_full)
            samples = qty_num[neg_qty_mask].tolist()[:5]
            problems.append(
                Problem(
                    severity="warning",
                    code="NEGATIVE_QUANTITY",
                    message="Quantity value is negative",
                    field="quantity",
                    row_refs=row_refs,
                    sample=samples,
                )
            )

    # 8. LINE_TOTAL_MISMATCH (warning)
    if all(col in valid_df.columns for col in ("unit_price", "quantity", "amount")):
        up = pd.to_numeric(valid_df["unit_price"], errors="coerce")
        q = pd.to_numeric(valid_df["quantity"], errors="coerce")
        amt = pd.to_numeric(valid_df["amount"], errors="coerce")

        valid_calc = up.notna() & q.notna() & amt.notna()
        if valid_calc.any():
            expected_amt = up * q
            mismatch_mask = valid_calc & ((expected_amt - amt).abs() > 0.5)
            if mismatch_mask.any():
                mask_full = pd.Series(False, index=df.index)
                mask_full.loc[valid_df.index[mismatch_mask]] = True
                row_refs = _get_row_refs(df, mask_full)
                samples = [
                    f"unit_price={u}, qty={qty_val}, amount={a} (expected {e:.2f})"
                    for u, qty_val, a, e in zip(
                        up[mismatch_mask][:5],
                        q[mismatch_mask][:5],
                        amt[mismatch_mask][:5],
                        expected_amt[mismatch_mask][:5],
                    )
                ]
                problems.append(
                    Problem(
                        severity="warning",
                        code="LINE_TOTAL_MISMATCH",
                        message="Calculated line total (unit_price * quantity) differs from reported amount",
                        field="amount",
                        row_refs=row_refs,
                        sample=samples,
                    )
                )

    return problems


def validate_core_rules(row: dict, index: int) -> List[Problem]:
    """
    Legacy row-level wrapper calling validate_core_dataframe for backward compatibility.
    """
    single_df = pd.DataFrame([row])
    return validate_core_dataframe(single_df)

