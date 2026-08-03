import pandas as pd
from typing import List, Dict, Any, Optional, Tuple

from app.schema.domain import Problem, ValidationReport, CleaningSummary, get_domain_pack
from app.schema.canonical import validate_core_dataframe

def validate(
    canonical_df: pd.DataFrame, 
    domain: str = "pharmacy", 
    table_kind: str = "auto"
) -> ValidationReport:
    """
    Module 6.3 (Part B) - Data Validation.
    Executes core domain-agnostic rules and active domain pack rules over a canonical DataFrame.
    
    Returns a structured, serializable ValidationReport containing problems, table stats, and a verdict.
    Does NOT call any LLM or external networks. Deterministic and vectorized.
    """
    if not isinstance(canonical_df, pd.DataFrame):
        raise ValueError("canonical_df must be a pandas DataFrame")

    problems: List[Problem] = []

    # Get active domain pack
    try:
        domain_pack = get_domain_pack(domain)
    except ValueError:
        domain_pack = None

    # 1. Core rules
    core_problems = validate_core_dataframe(canonical_df, table_kind=table_kind)
    problems.extend(core_problems)

    # 2. Domain pack rules
    if domain_pack:
        domain_problems = domain_pack.validate_dataframe(canonical_df)
        problems.extend(domain_problems)

    # 3. Calculate summary statistics
    total_rows = len(canonical_df)
    
    error_row_set = set()
    warning_row_set = set()

    for p in problems:
        if p.severity == "error":
            error_row_set.update(p.row_refs)
        elif p.severity == "warning":
            warning_row_set.update(p.row_refs)

    error_rows = len(error_row_set)
    warning_rows = len(warning_row_set)

    # Calculate null counts per field
    null_counts: Dict[str, int] = {}
    for col in canonical_df.columns:
        if col not in ("source_connector", "source_row") and not str(col).startswith("Unnamed:"):
            s = canonical_df[col]
            null_c = int((s.isna() | (s.astype(str).str.strip() == "") | (s.astype(str) == "nan")).sum())
            null_counts[str(col)] = null_c

    # Determine overall verdict
    # not_usable: empty dataset OR more than 20% of rows have errors
    # usable_with_warnings: any warnings (or up to 20% error rows)
    # usable: no problems at all
    if total_rows == 0:
        verdict = "not_usable"
    elif error_rows > 0 and (error_rows / total_rows) > 0.20:
        verdict = "not_usable"
    elif error_rows > 0 or warning_rows > 0:
        verdict = "usable_with_warnings"
    else:
        verdict = "usable"

    return ValidationReport(
        total_rows=total_rows,
        error_rows=error_rows,
        warning_rows=warning_rows,
        null_counts=null_counts,
        verdict=verdict,
        problems=problems
    )


def clean(
    canonical_df: pd.DataFrame, 
    options: Optional[Dict[str, Any]] = None
) -> Tuple[pd.DataFrame, CleaningSummary]:
    """
    Module 6.3 (Part B) - Optional Opt-In Data Cleaning.
    
    Performs non-destructive normalization (whitespace trimming, dropping fully-empty rows,
    optional deduplication). Never alters numeric money/quantity values or deletes usable rows.
    Returns (cleaned_df, CleaningSummary).
    """
    if not isinstance(canonical_df, pd.DataFrame):
        raise ValueError("canonical_df must be a pandas DataFrame")

    opts = {
        "drop_empty": True,
        "trim_whitespace": True,
        "drop_duplicates": False
    }
    if options:
        opts.update(options)

    df = canonical_df.copy()
    original_rows = len(df)
    details: List[str] = []
    whitespace_trimmed_cells = 0
    empty_rows_dropped = 0
    duplicate_rows_dropped = 0

    # 1. Trim whitespace in string columns
    if opts.get("trim_whitespace"):
        for col in df.columns:
            if col in ("source_connector", "source_row"):
                continue
            if df[col].dtype == object or isinstance(df[col].dtype, pd.StringDtype):
                # Only touch cells that are genuinely non-null strings so we do
                # not convert NaN to the literal string "nan" before comparing.
                is_str_mask = df[col].notna()
                if not is_str_mask.any():
                    continue
                s_str = df.loc[is_str_mask, col].astype(str)
                s_trimmed = s_str.str.strip()
                diff_idx = s_str.index[s_str.values != s_trimmed.values]
                cell_diff = len(diff_idx)
                if cell_diff > 0:
                    whitespace_trimmed_cells += cell_diff
                    df.loc[diff_idx, col] = s_trimmed.loc[diff_idx]

        if whitespace_trimmed_cells > 0:
            details.append(f"Trimmed leading/trailing whitespace in {whitespace_trimmed_cells} cell(s)")

    # 2. Drop fully-empty rows
    if opts.get("drop_empty"):
        data_cols = [
            c for c in df.columns 
            if c not in ("source_connector", "source_row") 
            and not str(c).startswith("Unnamed:")
            and not str(c).startswith("_extra.")
        ]
        if data_cols:
            empty_mask = df[data_cols].apply(
                lambda col: col.isna() | (col.astype(str).str.strip() == "") | (col.astype(str) == "nan")
            ).all(axis=1)
            
            empty_rows_dropped = int(empty_mask.sum())
            if empty_rows_dropped > 0:
                df = df[~empty_mask].copy()
                details.append(f"Dropped {empty_rows_dropped} completely empty row(s)")

    # 3. Drop exact duplicate rows (if requested)
    if opts.get("drop_duplicates"):
        data_cols = [
            c for c in df.columns 
            if c not in ("source_connector", "source_row") 
            and not str(c).startswith("Unnamed:")
            and not str(c).startswith("_extra.")
        ]
        if data_cols:
            before_dup = len(df)
            df = df.drop_duplicates(subset=data_cols, keep="first").copy()
            duplicate_rows_dropped = before_dup - len(df)
            if duplicate_rows_dropped > 0:
                details.append(f"Dropped {duplicate_rows_dropped} exact duplicate row(s)")

    cleaned_rows = len(df)
    rows_dropped = original_rows - cleaned_rows

    summary = CleaningSummary(
        original_rows=original_rows,
        cleaned_rows=cleaned_rows,
        rows_dropped=rows_dropped,
        empty_rows_dropped=empty_rows_dropped,
        duplicate_rows_dropped=duplicate_rows_dropped,
        whitespace_trimmed_cells=whitespace_trimmed_cells,
        details=details
    )

    return df, summary

