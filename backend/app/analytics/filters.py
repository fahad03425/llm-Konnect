"""
Module 6.6 (KPI Engine) — filters.py

Consistent, deterministic filtering applied ONCE before any KPI is computed, so
every figure in a pack covers exactly the same slice and the slice is recorded in
each result's provenance.

Deterministic, LLM-free, offline. Domain-agnostic: no domain vocabulary here.
"""

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Canonical column each filter field targets.
_FIELD_COLUMNS = {
    "category": "category",
    "product_id": "product_id",
    "supplier_id": "supplier_id",
    "customer_id": "customer_id",
    "txn_type": "txn_type",
}


@dataclass(frozen=True)
class KPIFilters:
    """
    A slice of the canonical DataFrame.

    All fields are optional; an all-None instance means "every row". Date filters
    are inclusive on both ends. `month`/`year` are convenience filters (the chatbot
    seam extracts a month from questions like "total sales in January").
    """

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    category: Optional[str] = None
    product_id: Optional[str] = None
    supplier_id: Optional[str] = None
    customer_id: Optional[str] = None
    txn_type: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "KPIFilters":
        """Build from a dict, ignoring unknown keys (the 6.5 router emits extras)."""
        if not data:
            return cls()
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if data.get(f) is not None}
        return cls(**known)

    def is_empty(self) -> bool:
        return all(v is None for v in asdict(self).values())

    def as_dict(self) -> Dict[str, Any]:
        """Only the fields that are actually set, in stable field order."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    def describe(self) -> str:
        """Human-readable rendering, stable ordering — goes into provenance."""
        active = self.as_dict()
        if not active:
            return "no filter (all rows)"
        return ", ".join(f"{k} = {v}" for k, v in active.items())


def apply_filters(
    df: pd.DataFrame, filters: KPIFilters
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Apply `filters` to a canonical DataFrame.

    Returns:
        (filtered_df, notes) — `notes` records any filter that could NOT be applied
        because the canonical column is absent. Those notes travel into provenance
        as assumptions so a figure is never silently computed over the wrong slice.
    """
    notes: List[str] = []
    if df.empty or filters.is_empty():
        return df, notes

    mask = pd.Series(True, index=df.index)

    # --- Date-based filters -------------------------------------------------
    date_wanted = any(
        v is not None for v in (filters.date_from, filters.date_to, filters.month, filters.year)
    )
    if date_wanted:
        if "date" not in df.columns:
            notes.append(
                "date filter requested but the canonical 'date' column is absent; "
                "date filter was NOT applied"
            )
        else:
            dates = pd.to_datetime(df["date"], errors="coerce")
            if filters.date_from is not None:
                start = pd.to_datetime(filters.date_from, errors="coerce")
                if pd.isna(start):
                    notes.append(f"date_from '{filters.date_from}' is unparseable; ignored")
                else:
                    mask &= dates.notna() & (dates >= start)
            if filters.date_to is not None:
                end = pd.to_datetime(filters.date_to, errors="coerce")
                if pd.isna(end):
                    notes.append(f"date_to '{filters.date_to}' is unparseable; ignored")
                else:
                    # Inclusive of the whole end day.
                    mask &= dates.notna() & (dates <= end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
            if filters.month is not None:
                mask &= dates.notna() & (dates.dt.month == int(filters.month))
            if filters.year is not None:
                mask &= dates.notna() & (dates.dt.year == int(filters.year))

    # --- Exact-match filters ------------------------------------------------
    for field_name, column in _FIELD_COLUMNS.items():
        wanted = getattr(filters, field_name)
        if wanted is None:
            continue
        if column not in df.columns:
            notes.append(
                f"{field_name} filter requested but the canonical '{column}' column "
                f"is absent; that filter was NOT applied"
            )
            continue
        col = df[column].astype(str).str.strip().str.casefold()
        mask &= col == str(wanted).strip().casefold()

    return df.loc[mask], notes
