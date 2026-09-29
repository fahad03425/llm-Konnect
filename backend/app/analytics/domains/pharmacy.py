"""
Pharmacy expiry analytics — DomainPack KPIs on the Module 6.6 KPI engine.

Answers the flagship pharmacy question: *"how much stock value is expiring soon,
and which batches?"* Expired and near-expiry stock is real money a pharmacist
loses, so these figures must be correct to the rupee and auditable down to the
batch.

Same contract as every other KPI in 6.6:
  * Deterministic pure pandas. The LLM is never involved: this module does not
    import the LLM client. Code computes, the LLM narrates.
  * Offline, CPU-only, no network.
  * Every result is a `KPIResult` with real provenance back to `source_row`.
  * A figure that cannot be computed is `unavailable` WITH A REASON — never a
    silently-wrong 0, never a crash.

Registered onto the engine via `PharmacyDomainPack.register_kpis`, so nothing here
touches engine core and no other domain ever sees these KPIs.

Definitions this module commits to
----------------------------------
**Subject rows.** These KPIs read a *stock-on-hand / inventory* table: rows with an
`expiry_date`, a `quantity`, and a value basis. A row contributes only when all
three are usable and `quantity > 0`.

**Value basis.** Stock value is `quantity x unit_value`. The basis is `cost` by
default — the money actually lost — and `mrp` optionally, for revenue foregone.
The basis in force is stated in every result's formula. If the configured basis
column is absent or entirely empty, the other is used and the substitution is
recorded as an assumption.

**Bucket banding.** Buckets are BANDED, not cumulative, so each item appears in
exactly one bucket and a pharmacist can act on "these expire first". With the
default `[30, 60, 90]`:

    expiring_value_30d  ->  0 <= days_to_expiry <= 30
    expiring_value_60d  -> 31 <= days_to_expiry <= 60
    expiring_value_90d  -> 61 <= days_to_expiry <= 90
    expired_stock_value ->      days_to_expiry <  0
    near_expiry_total   ->  0 <= days_to_expiry <= 90   (cumulative headline)

Boundaries are inclusive at the top of each band: an item at exactly 30 days is in
the 30-day bucket, exactly 60 in the 60-day bucket. An item expiring *today*
(0 days) counts as near-expiry, not expired.

**Reference date.** "Today" is injected via `KPIFilters.as_of` and defaults to the
current date. `datetime.now()` is never read from inside the maths, so tests pin a
date and results are reproducible.

**Exclusions are reported, never silent.** Rows dropped for a missing or unparseable
expiry, a missing quantity, or a missing unit value are counted and reported in
`provenance.assumptions`, so a total is never quietly understated.
"""

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.filters import KPIFilters
from app.analytics.kpi import build_provenance
from app.analytics.models import (
    UNIT_COUNT,
    UNIT_CURRENCY,
    UNIT_PERCENT,
    KPIResult,
    Period,
    unavailable,
)
from app.core.config import settings

_MONEY_ROUND = 2

# Question vocabulary for the chatbot's numeric route (English + Roman-Urdu).
# Lives here, on the domain side, so the chatbot core stays domain-agnostic.
PHARMACY_QUESTION_RULES: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    # 1. Low stock & reorder point predictions / sales velocity:
    (
        (
            "running below", "days supply", "day supply", "day's supply", "days of supply",
            "sales velocity", "velocity", "reorder point", "stockout", "out of stock",
            "low stock", "fast moving", "fast-moving", "kam stock", "khatam hone",
            "stock khatam", "supply running low", "stockout risk", "critical supply",
            "reorder prediction", "reorder alert", "critical drugs", "cardiac drugs",
        ),
        ("low_stock_reorder_predictions", "stockout_risk_count"),
    ),
    # 2. Demand forecasting
    (
        (
            "demand", "how much should i order", "how much to order", "reorder",
            "kitna mangwana", "kitna order", "kitni dawai", "dawai ki demand",
            "stock kitna chahiye",
        ),
        ("product_demand_forecast", "demand_forecast"),
    ),
    # 3. Expired vs Expiring
    (
        ("expired or expiring", "expired and expiring", "expired or near", "expired aur expiring"),
        ("expired_stock_value", "expired_item_count", "near_expiry_total", "near_expiry_item_count"),
    ),
    (
        ("expired", "already expired", "dead stock", "expire ho gaya", "expire ho chuka"),
        ("expired_stock_value", "expired_item_count"),
    ),
    (
        (
            "liquidation", "liquidation suggestions", "liquidation plan", "return to distributor",
            "bundle or discount",
        ),
        ("expiring_medicines_liquidation", "near_expiry_total", "near_expiry_item_count"),
    ),
    (
        (
            "which medicines expire", "expire in the next", "expire in next",
            "expiring in next", "expiring in the next", "medicines expire", "drugs expire",
            "kon si dawai expire", "konsi medicine expire", "short expiry", "near expiry",
            "near-expiry", "expiring", "expire", "expiry", "about to expire", "expire ho raha",
            "expire hone", "khatam ho raha", "khatam hone", "miyad", "meyad",
        ),
        ("near_expiry_total", "expiring_medicines_liquidation", "near_expiry_item_count"),
    ),
    (
        (
            "schedule", "scheduled", "prescription", "rx", "controlled", "narcotic",
        ),
        ("scheduled_transaction_count", "scheduled_units_sold", "scheduled_sales_value"),
    ),
]


# ---------------------------------------------------------------------------
# Shared preparation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StockView:
    """The usable stock rows plus everything derived from them, computed once."""

    ok: bool
    reason: Optional[str] = None
    usable: Optional[pd.Series] = None          # boolean mask of contributing-eligible rows
    days: Optional[pd.Series] = None            # days to expiry vs the reference date
    line_value: Optional[pd.Series] = None      # quantity x unit_value
    unit_value: Optional[pd.Series] = None
    quantity: Optional[pd.Series] = None
    expiry: Optional[pd.Series] = None
    basis: str = "cost"
    reference_date: Optional[pd.Timestamp] = None
    columns_used: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    last_sold_date: Optional[pd.Series] = None
    days_since_sale: Optional[pd.Series] = None


def _resolve_reference_date(filters: KPIFilters) -> Tuple[pd.Timestamp, List[str]]:
    """
    The date expiry is measured against: `filters.as_of`, else today.

    Injected rather than read inside the maths so tests can pin it and a user can
    ask "as of month-end".
    """
    notes: List[str] = []
    if filters.as_of:
        parsed = pd.to_datetime(filters.as_of, errors="coerce")
        if pd.notna(parsed):
            return parsed.normalize(), notes
        notes.append(f"as_of '{filters.as_of}' could not be parsed as a date; used today instead")
    return pd.Timestamp(datetime.date.today()), notes


def _prepare_stock(
    df: pd.DataFrame,
    filters: KPIFilters,
    require_expiry: bool = True,
    require_last_sold: bool = False,
) -> _StockView:
    """
    Validate the frame and derive expiry/value series once for every stock KPI.

    Consumes the ALREADY-NORMALIZED `expiry_date` produced by the schema pipeline
    (which handles mm/yy, mm-yyyy, Excel serials, day-first strings). No date
    formats are re-parsed here; `to_datetime` is a coercion safety net only.
    """
    reference_date, notes = _resolve_reference_date(filters)

    if require_expiry and "expiry_date" not in df.columns:
        return _StockView(
            ok=False,
            reason="canonical 'expiry_date' column is not present, so expiry cannot be assessed",
            reference_date=reference_date,
            notes=tuple(notes),
        )
    if "quantity" not in df.columns:
        return _StockView(
            ok=False,
            reason="canonical 'quantity' column is not present, so stock value cannot be computed",
            reference_date=reference_date,
            notes=tuple(notes),
        )

    work_df = df
    if require_last_sold:
        if "last_sold_date" not in work_df.columns or work_df["last_sold_date"].isna().all():
            try:
                from app.schema.pharmacy import derive_last_sold_date
                work_df = derive_last_sold_date(work_df)
            except Exception:
                pass
        if "last_sold_date" not in work_df.columns or work_df["last_sold_date"].isna().all():
            return _StockView(
                ok=False,
                reason="canonical 'last_sold_date' column is not present and cannot be derived (no transaction date)",
                reference_date=reference_date,
                notes=tuple(notes),
            )

    # --- value basis --------------------------------------------------------
    requested = str(filters.option("value_basis", settings.expiry_value_basis)).strip().casefold()
    if requested not in ("cost", "mrp"):
        notes.append(f"unknown value_basis '{requested}'; used '{settings.expiry_value_basis}' instead")
        requested = str(settings.expiry_value_basis).strip().casefold()
    fallback = "mrp" if requested == "cost" else "cost"

    def _usable_column(name: str) -> Optional[pd.Series]:
        if name not in work_df.columns:
            return None
        series = pd.to_numeric(work_df[name], errors="coerce")
        return series if series.notna().any() else None

    basis = requested
    unit_value = _usable_column(requested)
    if unit_value is None:
        alt = _usable_column(fallback)
        if alt is None:
            return _StockView(
                ok=False,
                reason=(
                    f"no usable value basis: neither '{requested}' nor '{fallback}' is present "
                    f"with any numeric values, so stock cannot be valued"
                ),
                reference_date=reference_date,
                notes=tuple(notes),
            )
        basis = fallback
        unit_value = alt
        notes.append(
            f"configured value basis '{requested}' is unavailable in this data; "
            f"valued at '{fallback}' instead"
        )

    # --- derived series -----------------------------------------------------
    quantity = pd.to_numeric(work_df["quantity"], errors="coerce")
    has_qty = quantity.notna() & (quantity > 0)
    has_value = unit_value.notna()

    columns = ["quantity", basis]

    expiry = None
    days = None
    if require_expiry or "expiry_date" in work_df.columns:
        columns.append("expiry_date")
        expiry = pd.to_datetime(work_df["expiry_date"], errors="coerce") if "expiry_date" in work_df.columns else pd.Series(pd.NaT, index=work_df.index)
        has_expiry = expiry.notna()
        days = (expiry.dt.normalize() - reference_date).dt.days
    else:
        has_expiry = pd.Series(True, index=work_df.index)

    last_sold = None
    days_since_sale = None
    if require_last_sold or "last_sold_date" in work_df.columns:
        columns.append("last_sold_date")
        last_sold = pd.to_datetime(work_df["last_sold_date"], errors="coerce") if "last_sold_date" in work_df.columns else pd.Series(pd.NaT, index=work_df.index)
        has_last_sold = last_sold.notna()
        days_since_sale = (reference_date - last_sold.dt.normalize()).dt.days
    else:
        has_last_sold = pd.Series(True, index=work_df.index)

    usable = has_qty & has_value
    if require_expiry:
        usable = usable & has_expiry
    if require_last_sold:
        usable = usable & has_last_sold

    # --- report exclusions ---
    total = len(work_df)
    if require_expiry:
        missing_expiry = int((~has_expiry).sum())
        if missing_expiry:
            notes.append(
                f"{missing_expiry} of {total} row(s) excluded: expiry date is missing or "
                f"could not be parsed. Their value is NOT included in any expiry figure."
            )
    if require_last_sold:
        missing_last_sold = int((~has_last_sold).sum())
        if missing_last_sold:
            notes.append(
                f"{missing_last_sold} of {total} row(s) excluded: last sold date is missing."
            )

    missing_qty = int((~has_qty).sum())
    if missing_qty:
        notes.append(
            f"{missing_qty} row(s) excluded: quantity is missing, non-numeric, or not greater than zero"
        )
    missing_value = int((has_qty & ~has_value).sum())
    if missing_value:
        notes.append(f"{missing_value} row(s) excluded: no '{basis}' value to price the stock with")

    # Stock-on-hand check: an invoice column means these are transactions, not shelf stock.
    if "invoice_id" in work_df.columns and work_df["invoice_id"].notna().any():
        notes.append(
            "this data carries invoice ids, so it looks like transactions rather than "
            "stock on hand; expiry values describe the rows present, which may not be "
            "current shelf stock"
        )

    for optional in ("batch_no", "product_id"):
        if optional in work_df.columns:
            columns.append(optional)

    return _StockView(
        ok=True,
        usable=usable,
        days=days,
        line_value=quantity * unit_value,
        unit_value=unit_value,
        quantity=quantity,
        expiry=expiry,
        basis=basis,
        reference_date=reference_date,
        columns_used=tuple(columns),
        notes=tuple(notes),
        last_sold_date=last_sold,
        days_since_sale=days_since_sale,
    )


def _bucket_bands() -> List[Tuple[int, int]]:
    """Banded, non-overlapping day ranges from the configured bucket edges."""
    edges = sorted({int(b) for b in settings.expiry_buckets_days if int(b) > 0})
    bands, low = [], 0
    for edge in edges:
        bands.append((low, edge))
        low = edge + 1
    return bands


def _round_money(value) -> float:
    return round(float(value), _MONEY_ROUND)


def _item_breakdown(df: pd.DataFrame, view: _StockView, mask: pd.Series) -> List[Dict[str, Any]]:
    """
    The at-risk items behind a figure: soonest expiry first, then product, then batch.

    This is the table a pharmacist acts on, so it is sorted for action rather than
    by value, and the ordering is fully specified so it never wobbles between runs.
    """
    table = pd.DataFrame(
        {
            "product_id": (
                df.loc[mask, "product_id"].astype(str) if "product_id" in df.columns else "(unknown)"
            ),
            "batch_no": (
                df.loc[mask, "batch_no"].astype(str) if "batch_no" in df.columns else "(unknown)"
            ),
            "expiry_date": view.expiry[mask].dt.strftime("%Y-%m-%d"),
            "days_to_expiry": view.days[mask].astype(int),
            "quantity": view.quantity[mask],
            "unit_value": view.unit_value[mask],
            "line_value": view.line_value[mask],
            "source_row": (
                pd.to_numeric(df.loc[mask, "source_row"], errors="coerce")
                if "source_row" in df.columns
                else pd.Series(pd.NA, index=df.index[mask])
            ),
        }
    ).sort_values(
        ["days_to_expiry", "product_id", "batch_no"], ascending=True, kind="mergesort"
    )

    top_n = int(settings.expiry_breakdown_top_n)
    rows = []
    for _, r in table.head(top_n).iterrows():
        rows.append(
            {
                "product_id": r["product_id"],
                "batch_no": r["batch_no"],
                "expiry_date": r["expiry_date"],
                "days_to_expiry": int(r["days_to_expiry"]),
                "quantity": float(r["quantity"]),
                "unit_value": _round_money(r["unit_value"]),
                "line_value": _round_money(r["line_value"]),
                "source_row": None if pd.isna(r["source_row"]) else int(r["source_row"]),
            }
        )
    return rows


BREAKDOWN_COLUMNS = [
    "product_id", "batch_no", "expiry_date", "days_to_expiry",
    "quantity", "unit_value", "line_value", "source_row",
]


def _period_of(view: _StockView, mask: pd.Series) -> Optional[Period]:
    """Expiry-date span of the contributing rows."""
    dates = view.expiry[mask].dropna()
    if dates.empty:
        return None
    return Period(start=dates.min().strftime("%Y-%m-%d"), end=dates.max().strftime("%Y-%m-%d"))


# ---------------------------------------------------------------------------
# The shared value/count computation
# ---------------------------------------------------------------------------


def _window_mask(view: _StockView, low: Optional[int], high: Optional[int]) -> pd.Series:
    """Rows whose days-to-expiry fall in [low, high]; either bound may be open."""
    mask = view.usable.copy()
    if low is not None:
        mask &= view.days >= low
    if high is not None:
        mask &= view.days <= high
    return mask


def _value_kpi(
    df: pd.DataFrame,
    filters: KPIFilters,
    key: str,
    name: str,
    low: Optional[int],
    high: Optional[int],
    window_text: str,
) -> KPIResult:
    """Total stock value inside a days-to-expiry window, with the item breakdown."""
    view = _prepare_stock(df, filters)
    formula = (
        f"sum(quantity x {view.basis}) over stock rows where {window_text}, "
        f"measured from {view.reference_date.strftime('%Y-%m-%d') if view.reference_date else 'today'}"
    )

    if not view.ok:
        return unavailable(
            key, name, UNIT_CURRENCY, formula, view.reason,
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    mask = _window_mask(view, low, high)
    provenance = build_provenance(df, mask, filters, list(view.columns_used), list(view.notes))

    if not mask.any():
        # The data is fine, there is simply nothing in this window: a real zero.
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula,
            provenance=provenance, breakdown=[], breakdown_columns=BREAKDOWN_COLUMNS,
        )

    return KPIResult(
        key=key,
        name=name,
        value=_round_money(view.line_value[mask].sum()),
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        period=_period_of(view, mask),
        breakdown=_item_breakdown(df, view, mask),
        breakdown_columns=BREAKDOWN_COLUMNS,
    )


def _count_kpi(
    df: pd.DataFrame,
    filters: KPIFilters,
    key: str,
    name: str,
    low: Optional[int],
    high: Optional[int],
    window_text: str,
) -> KPIResult:
    """Distinct product-batch count inside a days-to-expiry window."""
    view = _prepare_stock(df, filters)
    formula = f"count of distinct product/batch pairs among stock rows where {window_text}"

    if not view.ok:
        return unavailable(
            key, name, UNIT_COUNT, formula, view.reason,
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    mask = _window_mask(view, low, high)
    provenance = build_provenance(df, mask, filters, list(view.columns_used), list(view.notes))

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_COUNT, formula=formula, provenance=provenance
        )

    product = df.loc[mask, "product_id"].astype(str) if "product_id" in df.columns else ""
    batch = df.loc[mask, "batch_no"].astype(str) if "batch_no" in df.columns else ""
    pairs = pd.DataFrame({"p": product, "b": batch}) if "product_id" in df.columns else None
    value = float(len(pairs.drop_duplicates())) if pairs is not None else float(int(mask.sum()))

    return KPIResult(
        key=key, name=name, value=value, unit=UNIT_COUNT, formula=formula,
        provenance=provenance, period=_period_of(view, mask),
    )


# ---------------------------------------------------------------------------
# The registered KPIs
# ---------------------------------------------------------------------------


def expired_stock_value(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Value of stock already past its expiry date — dead loss sitting on the shelf."""
    return _value_kpi(
        df, filters, "expired_stock_value", "Expired Stock Value",
        None, -1, "expiry date is already in the past (days_to_expiry < 0)",
    )


def expired_item_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """How many distinct product-batches are already expired."""
    return _count_kpi(
        df, filters, "expired_item_count", "Expired Item Count",
        None, -1, "expiry date is already in the past (days_to_expiry < 0)",
    )


def near_expiry_total(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Headline number: total value expiring within the widest configured bucket.

    Cumulative (unlike the individual buckets) and excludes already-expired stock,
    which `expired_stock_value` reports separately.
    """
    horizon = max(_bucket_bands())[1] if _bucket_bands() else 90
    return _value_kpi(
        df, filters, "near_expiry_total", "Near-Expiry Stock Value",
        0, horizon, f"stock expires within the next {horizon} days (0 <= days_to_expiry <= {horizon})",
    )


def near_expiry_item_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """How many distinct product-batches are near expiry."""
    horizon = max(_bucket_bands())[1] if _bucket_bands() else 90
    return _count_kpi(
        df, filters, "near_expiry_item_count", "Near-Expiry Item Count",
        0, horizon, f"stock expires within the next {horizon} days (0 <= days_to_expiry <= {horizon})",
    )


def expiry_by_manufacturer(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Near-expiry value grouped by manufacturer — which supplier's stock is most at
    risk, which is what a pharmacist needs to negotiate returns.
    """
    key, name = "expiry_by_manufacturer", "Near-Expiry Value by Manufacturer"
    view = _prepare_stock(df, filters)
    horizon = max(_bucket_bands())[1] if _bucket_bands() else 90
    formula = f"sum(quantity x {view.basis}) grouped by manufacturer, for stock expiring within {horizon} days"

    if not view.ok:
        return unavailable(
            key, name, UNIT_CURRENCY, formula, view.reason,
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    group_col = next((c for c in ("manufacturer", "supplier_id") if c in df.columns), None)
    if group_col is None:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "neither 'manufacturer' nor 'supplier_id' is present, so at-risk stock "
            "cannot be attributed to a supplier",
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    mask = _window_mask(view, 0, horizon) & df[group_col].notna()
    provenance = build_provenance(
        df, mask, filters, list(view.columns_used) + [group_col], list(view.notes)
    )
    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula,
            provenance=provenance, breakdown=[],
            breakdown_columns=[group_col, "value", "item_count"],
        )

    grouped = (
        pd.DataFrame({group_col: df.loc[mask, group_col].astype(str), "value": view.line_value[mask]})
        .groupby(group_col, as_index=False, sort=False)
        .agg(value=("value", "sum"), item_count=("value", "size"))
        .sort_values(["value", group_col], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )

    return KPIResult(
        key=key, name=name, value=_round_money(grouped["value"].sum()), unit=UNIT_CURRENCY,
        formula=formula, provenance=provenance, period=_period_of(view, mask),
        breakdown=[
            {group_col: r[group_col], "value": _round_money(r["value"]), "item_count": int(r["item_count"])}
            for _, r in grouped.iterrows()
        ],
        breakdown_columns=[group_col, "value", "item_count"],
    )


def _make_bucket_kpi(low: int, high: int):
    """Build the compute function for one banded bucket."""

    def _bucket(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
        return _value_kpi(
            df, filters, f"expiring_value_{high}d", f"Stock Value Expiring in {low}-{high} Days",
            low, high, f"expiry falls {low} to {high} days away ({low} <= days_to_expiry <= {high})",
        )

    return _bucket


def _is_scheduled(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.lower()
    return s.isin(["yes", "true", "1", "y", "schedule", "sched", "rx", "controlled"]) | s.str.startswith("schedule")


def scheduled_transaction_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """Distinct transactions for scheduled / prescription medicines."""
    from app.analytics.kpi import classify_transactions, _no_rows, _period
    txn = classify_transactions(df)
    formula = "count of distinct invoice_id over sale rows with schedule_flag"
    
    if "schedule_flag" not in df.columns and "prescription_ref" not in df.columns:
        return unavailable(
            "scheduled_transaction_count", "Scheduled Medicine Transactions", UNIT_COUNT, formula,
            "neither 'schedule_flag' nor 'prescription_ref' column is present",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
    
    sched_mask = pd.Series(False, index=df.index)
    if "schedule_flag" in df.columns:
        sched_mask = sched_mask | _is_scheduled(df["schedule_flag"])
    if "prescription_ref" in df.columns:
        sched_mask = sched_mask | (df["prescription_ref"].notna() & (df["prescription_ref"].astype(str).str.strip() != ""))
        
    contributing = txn.sale & sched_mask
    columns = [c for c in ["invoice_id", "schedule_flag", "prescription_ref"] if c in df.columns]
    provenance = build_provenance(df, contributing, filters, columns, list(txn.notes))
    
    if "invoice_id" in df.columns:
        val = float(df.loc[contributing, "invoice_id"].astype(str).nunique())
    else:
        val = float(contributing.sum())
        
    return KPIResult(
        key="scheduled_transaction_count", name="Scheduled Medicine Transactions",
        value=val, unit=UNIT_COUNT, formula=formula, provenance=provenance,
        period=_period(df, contributing)
    )


def scheduled_units_sold(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """Total units sold for scheduled / prescription medicines."""
    from app.analytics.kpi import classify_transactions, _no_rows, _period
    txn = classify_transactions(df)
    formula = "sum of quantity over sale rows with schedule_flag"
    
    if "schedule_flag" not in df.columns and "prescription_ref" not in df.columns:
        return unavailable(
            "scheduled_units_sold", "Scheduled Units Sold", UNIT_COUNT, formula,
            "neither 'schedule_flag' nor 'prescription_ref' column is present",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
        
    sched_mask = pd.Series(False, index=df.index)
    if "schedule_flag" in df.columns:
        sched_mask = sched_mask | _is_scheduled(df["schedule_flag"])
    if "prescription_ref" in df.columns:
        sched_mask = sched_mask | (df["prescription_ref"].notna() & (df["prescription_ref"].astype(str).str.strip() != ""))
        
    qty = pd.to_numeric(df["quantity"], errors="coerce") if "quantity" in df.columns else None
    if qty is None:
        return unavailable(
            "scheduled_units_sold", "Scheduled Units Sold", UNIT_COUNT, formula,
            "canonical 'quantity' column is not present",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
        
    contributing = txn.sale & sched_mask & qty.notna()
    columns = [c for c in ["quantity", "schedule_flag", "prescription_ref"] if c in df.columns]
    provenance = build_provenance(df, contributing, filters, columns, list(txn.notes))
    val = float(qty[contributing].sum()) if contributing.any() else 0.0
    
    return KPIResult(
        key="scheduled_units_sold", name="Scheduled Units Sold",
        value=val, unit=UNIT_COUNT, formula=formula, provenance=provenance,
        period=_period(df, contributing)
    )


def scheduled_sales_value(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """Total sales amount for scheduled / prescription medicines."""
    from app.analytics.kpi import classify_transactions, _amount_series, _no_rows, _period
    txn = classify_transactions(df)
    formula = "sum of amount over sale rows with schedule_flag"
    
    if "schedule_flag" not in df.columns and "prescription_ref" not in df.columns:
        return unavailable(
            "scheduled_sales_value", "Scheduled Sales Value", UNIT_CURRENCY, formula,
            "neither 'schedule_flag' nor 'prescription_ref' column is present",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
        
    amounts, cols, notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            "scheduled_sales_value", "Scheduled Sales Value", UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
        
    sched_mask = pd.Series(False, index=df.index)
    if "schedule_flag" in df.columns:
        sched_mask = sched_mask | _is_scheduled(df["schedule_flag"])
    if "prescription_ref" in df.columns:
        sched_mask = sched_mask | (df["prescription_ref"].notna() & (df["prescription_ref"].astype(str).str.strip() != ""))
        
    contributing = txn.sale & sched_mask & amounts.notna()
    columns = [c for c in cols + ["schedule_flag", "prescription_ref"] if c in df.columns]
    provenance = build_provenance(df, contributing, filters, columns, list(txn.notes) + notes)
    val = float(_round_money(amounts[contributing].sum())) if contributing.any() else 0.0
    
    return KPIResult(
        key="scheduled_sales_value", name="Scheduled Sales Value",
        value=val, unit=UNIT_CURRENCY, formula=formula, provenance=provenance,
        period=_period(df, contributing)
    )


# ---------------------------------------------------------------------------
# Liquidation Strategy & Smart Expiry Engine
# ---------------------------------------------------------------------------


def _liquidation_strategy(days_to_expiry: int, supplier: str = "") -> Tuple[str, int]:
    """
    Actionable liquidation suggestion based on pharmaceutical shelf-life and distributor return policies.
    Returns (suggested_action, suggested_discount_pct).
    """
    clean_supp = str(supplier).strip() if supplier and str(supplier).strip() not in ("nan", "None", "(unknown)", "") else ""
    dist_str = f" to Distributor ({clean_supp})" if clean_supp else " to Distributor"

    if days_to_expiry < 0:
        return "Immediate Quarantine & Disposal (Expired)", 0
    elif days_to_expiry <= 15:
        return "Clearance Sale: Markdown 40-50% to salvage cost before write-off", 50
    elif days_to_expiry <= 30:
        return "Promotional Markdown: Discount 20-30% / Bundle with Fast-Moving OTC", 25
    elif days_to_expiry <= 45:
        return "Prioritize Dispensing & Clinical Push / BOGO Offer", 10
    else:
        return f"Return{dist_str} for Credit Note / Replacement", 0


def expiring_medicines_liquidation(
    df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy"
) -> KPIResult:
    """
    List of medicines expiring within the requested horizon (default 60 days)
    with itemized batch details, at-risk value, and actionable liquidation recommendations.
    """
    key, name = "expiring_medicines_liquidation", "Near-Expiry Medicines & Liquidation Plan"
    view = _prepare_stock(df, filters)

    raw_horizon = filters.option("expiry_days", filters.option("horizon_days", 60))
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        horizon = 60

    formula = (
        f"itemized stock rows expiring within {horizon} days (0 <= days_to_expiry <= {horizon}) "
        f"with liquidation strategies (distributor returns, bundling, promotional discounts)"
    )

    if not view.ok:
        return unavailable(
            key, name, UNIT_CURRENCY, formula, view.reason,
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    mask = _window_mask(view, 0, horizon)
    provenance = build_provenance(df, mask, filters, list(view.columns_used), list(view.notes))

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula,
            provenance=provenance, breakdown=[],
            breakdown_columns=[
                "product_id", "generic_name", "batch_no", "expiry_date", "days_to_expiry",
                "quantity", "unit_value", "line_value", "supplier_id", "suggested_action",
                "suggested_discount_pct", "source_row"
            ],
        )

    # Build rich itemized breakdown
    supp_col = next((c for c in ("supplier_id", "manufacturer", "company") if c in df.columns), None)
    gen_col = next((c for c in ("generic_name", "generic") if c in df.columns), None)

    table = pd.DataFrame(
        {
            "product_id": (
                df.loc[mask, "product_id"].astype(str) if "product_id" in df.columns else "(unknown)"
            ),
            "generic_name": (
                df.loc[mask, gen_col].astype(str) if gen_col else ""
            ),
            "batch_no": (
                df.loc[mask, "batch_no"].astype(str) if "batch_no" in df.columns else "(unknown)"
            ),
            "expiry_date": view.expiry[mask].dt.strftime("%Y-%m-%d"),
            "days_to_expiry": view.days[mask].astype(int),
            "quantity": view.quantity[mask],
            "unit_value": view.unit_value[mask],
            "line_value": view.line_value[mask],
            "supplier_id": (
                df.loc[mask, supp_col].astype(str) if supp_col else ""
            ),
            "source_row": (
                pd.to_numeric(df.loc[mask, "source_row"], errors="coerce")
                if "source_row" in df.columns
                else pd.Series(pd.NA, index=df.index[mask])
            ),
        }
    ).sort_values(
        ["days_to_expiry", "product_id", "batch_no"], ascending=True, kind="mergesort"
    )

    breakdown_rows = []
    for _, r in table.iterrows():
        days_exp = int(r["days_to_expiry"])
        action, disc = _liquidation_strategy(days_exp, r["supplier_id"])
        breakdown_rows.append(
            {
                "product_id": r["product_id"],
                "generic_name": r["generic_name"],
                "batch_no": r["batch_no"],
                "expiry_date": r["expiry_date"],
                "days_to_expiry": days_exp,
                "quantity": float(r["quantity"]),
                "unit_value": _round_money(r["unit_value"]),
                "line_value": _round_money(r["line_value"]),
                "supplier_id": r["supplier_id"],
                "suggested_action": action,
                "suggested_discount_pct": disc,
                "source_row": None if pd.isna(r["source_row"]) else int(r["source_row"]),
            }
        )

    return KPIResult(
        key=key,
        name=name,
        value=_round_money(view.line_value[mask].sum()),
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        period=_period_of(view, mask),
        breakdown=breakdown_rows,
        breakdown_columns=[
            "product_id", "generic_name", "batch_no", "expiry_date", "days_to_expiry",
            "quantity", "unit_value", "line_value", "supplier_id", "suggested_action",
            "suggested_discount_pct", "source_row"
        ],
    )


# ---------------------------------------------------------------------------
# Low-Stock & Sales Velocity Reorder Predictions
# ---------------------------------------------------------------------------


_THERAPEUTIC_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cardiac": (
        "cardiac", "cardio", "cardiovascular", "heart", "bp", "hypertension", "blood pressure",
        "statin", "cholesterol", "beta blocker", "amlodipine", "metoprolol", "rosuvastatin",
        "atorvastatin", "clopidogrel", "plavix", "lipitor", "crestor", "clexane", "enoxaparin",
    ),
    "diabetes": (
        "diabet", "sugar", "insulin", "metformin", "glucophage", "lantus", "glucovance", "mixtard",
    ),
    "antibiotic": (
        "antibiotic", "anti-infective", "infection", "amoxicillin", "augmentin", "ciprofloxacin",
        "ciproxin", "azithromycin", "zithromax", "meropenem", "meronem", "amikacin", "vancomycin",
    ),
    "analgesic": (
        "pain", "analgesic", "anti-inflammatory", "paracetamol", "panadol", "ibuprofen", "brufen",
        "diclofenac", "voltaren",
    ),
    "respiratory": (
        "respiratory", "asthma", "inhaler", "salbutamol", "ventolin", "seretide", "fluticasone",
    ),
    "gastro": (
        "gastro", "antacid", "proton", "omeprazole", "esomeprazole", "nexium",
    ),
    "psychiatric": (
        "psych", "antipsychotic", "mental", "risperidone", "olanzapine",
    ),
}


def _matches_category_keywords(df: pd.DataFrame, category_query: str) -> pd.Series:
    """Matches rows against category column, generic_name, or product name keywords."""
    if not category_query or df.empty:
        return pd.Series(True, index=df.index)

    query_cf = category_query.strip().casefold()
    tokens = [query_cf]
    for key, key_tokens in _THERAPEUTIC_KEYWORDS.items():
        if key in query_cf or any(t in query_cf for t in key_tokens):
            tokens.extend(key_tokens)
    tokens = list(set(tokens))

    mask = pd.Series(False, index=df.index)
    for col in ("category", "generic_name", "product_id", "description"):
        if col in df.columns:
            col_str = df[col].astype(str).str.casefold()
            for tok in tokens:
                mask = mask | col_str.str.contains(tok, na=False, regex=False)
    return mask


def low_stock_reorder_predictions(
    df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy"
) -> KPIResult:
    """
    Predict stockout risks and reorder points based on daily sales velocity (DSV)
    and current inventory stock, flagging items running below target days of supply.
    """
    import math
    from app.analytics.kpi import classify_transactions

    key, name = "low_stock_reorder_predictions", "Low-Stock & Reorder Point Predictions"
    notes: List[str] = []

    if "product_id" not in df.columns:
        return unavailable(
            key, name, UNIT_COUNT, "days of supply and reorder point predictions",
            "canonical 'product_id' column is not present in data",
            build_provenance(df, pd.Series(False, index=df.index), filters, [], []),
        )

    if "quantity" not in df.columns:
        return unavailable(
            key, name, UNIT_COUNT, "days of supply and reorder point predictions",
            "canonical 'quantity' column is not present to calculate sales velocity or stock",
            build_provenance(df, pd.Series(False, index=df.index), filters, [], []),
        )

    # Threshold days (default 3.0 days for critical alert)
    raw_thresh = filters.option("days_supply_threshold", 3.0)
    try:
        threshold_days = float(raw_thresh)
    except (TypeError, ValueError):
        threshold_days = 3.0

    target_buffer_days = 14  # standard replenishment buffer

    # Apply category filter if requested
    cat_mask = pd.Series(True, index=df.index)
    cat_filter = filters.category or filters.option("category")
    if cat_filter:
        cat_mask = _matches_category_keywords(df, str(cat_filter))
        notes.append(f"filtered by category/therapeutic specialty '{cat_filter}'")

    txn = classify_transactions(df)
    qty_num = pd.to_numeric(df["quantity"], errors="coerce")
    is_sales_table = "invoice_id" in df.columns and df["invoice_id"].notna().any()

    # Calculate velocity from sale rows only; inventory/purchase rows must not
    # inflate units sold or distort the observation window.
    dates = pd.to_datetime(df["date"], errors="coerce") if "date" in df.columns else pd.Series(pd.NaT, index=df.index)
    valid_dates = dates[txn.sale].dropna()
    if not valid_dates.empty and is_sales_table:
        min_date, max_date = valid_dates.min(), valid_dates.max()
        span_days = max(1, (max_date - min_date).days + 1)
        period_str = f"spanning {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')} ({span_days} days)"
    else:
        span_days = 0
        period_str = "sales history unavailable"

    # Identify distinct products
    product_rows = df.loc[cat_mask]
    product_groups = product_rows.groupby("product_id", sort=False, dropna=True)
    if product_groups.ngroups == 0:
        prov = build_provenance(df, pd.Series(False, index=df.index), filters, ["product_id", "quantity"], notes)
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_COUNT,
            formula=f"products running below {threshold_days}-day supply based on sales velocity",
            provenance=prov, breakdown=[],
            breakdown_columns=[
                "product_id", "generic_name", "category", "current_stock", "daily_sales_velocity",
                "days_of_supply", "reorder_level", "stockout_risk", "recommended_reorder_qty", "supplier_id"
            ]
        )

    breakdown_rows = []
    critical_count = 0

    supp_col = next((c for c in ("supplier_id", "manufacturer", "company") if c in df.columns), None)
    gen_col = next((c for c in ("generic_name", "generic") if c in df.columns), None)
    cat_col = next((c for c in ("category", "therapeutic_class") if c in df.columns), None)
    reorder_col = next((c for c in ("reorder_level", "min_stock", "reorder") if c in df.columns), None)
    txn_labels = df["txn_type"].astype(str).str.casefold() if "txn_type" in df.columns else pd.Series("", index=df.index)
    inventory_mask = txn_labels.str.contains(r"inventory|stock", regex=True, na=False)

    for prod, p_rows in product_groups:

        generic = str(p_rows[gen_col].dropna().iloc[0]) if gen_col and not p_rows[gen_col].dropna().empty else ""
        category = str(p_rows[cat_col].dropna().iloc[0]) if cat_col and not p_rows[cat_col].dropna().empty else ""
        supplier = str(p_rows[supp_col].dropna().iloc[0]) if supp_col and not p_rows[supp_col].dropna().empty else ""
        
        reorder_lvl = 0.0
        if reorder_col and not p_rows[reorder_col].dropna().empty:
            try:
                reorder_lvl = float(pd.to_numeric(p_rows[reorder_col], errors="coerce").dropna().iloc[0])
            except Exception:
                reorder_lvl = 0.0

        if is_sales_table:
            # Sales velocity is supported only by classified sale rows.
            product_sale_mask = txn.sale.loc[p_rows.index]
            sold_units = float(qty_num.loc[p_rows.index[product_sale_mask]].sum())
            daily_velocity = round(sold_units / span_days, 2) if span_days > 0 else 0.0

            # Current stock comes from a stock snapshot row or an explicit stock field.
            stock_rows = p_rows[inventory_mask.loc[p_rows.index]]
            stock_val: Optional[float] = None
            if not stock_rows.empty:
                stock_qty = qty_num.loc[stock_rows.index]
                dated_rows = stock_rows.loc[dates.loc[stock_rows.index].notna()]
                if not dated_rows.empty:
                    latest_idx = dates.loc[dated_rows.index].idxmax()
                    stock_val = float(stock_qty.loc[latest_idx]) if pd.notna(stock_qty.loc[latest_idx]) else None
                else:
                    last_qty = stock_qty.dropna()
                    stock_val = float(last_qty.iloc[-1]) if not last_qty.empty else None
            stock_series = pd.to_numeric(p_rows["stock"], errors="coerce").dropna() if "stock" in p_rows.columns else pd.Series([], dtype="float64")
            closing_series = pd.to_numeric(p_rows["closing_stock"], errors="coerce").dropna() if "closing_stock" in p_rows.columns else pd.Series([], dtype="float64")
            if stock_val is None and not stock_series.empty:
                stock_val = float(stock_series.iloc[0])
            elif stock_val is None and not closing_series.empty:
                stock_val = float(closing_series.iloc[0])
            # Without observed stock, a zero-days alert is fabricated; omit this SKU.
            if stock_val is None:
                continue
        else:
            # A stock quantity is not a sales velocity. Keep inventory visible,
            # but do not claim days of supply without actual dated sales history.
            product_inventory_mask = inventory_mask.loc[p_rows.index]
            stock_val = float(qty_num.loc[p_rows.index[product_inventory_mask]].sum()) if product_inventory_mask.any() else float(qty_num.loc[p_rows.index].sum())
            daily_velocity = 0.0

        # Calculate Days of Supply
        if daily_velocity > 0:
            days_of_supply = round(stock_val / daily_velocity, 1)
        else:
            days_of_supply = None

        # Assess Risk Level
        if days_of_supply is not None and days_of_supply <= threshold_days:
            risk = "CRITICAL_STOCKOUT_RISK"
            critical_count += 1
        elif (days_of_supply is not None and days_of_supply <= 7.0) or (reorder_lvl > 0 and stock_val <= reorder_lvl):
            risk = "REORDER_RECOMMENDED"
        elif days_of_supply is not None and days_of_supply <= 30.0:
            risk = "HEALTHY"
        elif days_of_supply is not None:
            risk = "OVERSTOCKED"
        else:
            risk = "VELOCITY_UNAVAILABLE"

        # Calculate Recommended Reorder Quantity
        needed_buffer = daily_velocity * target_buffer_days
        reorder_qty = max(0, int(math.ceil(needed_buffer - stock_val)))
        if reorder_lvl > 0 and stock_val <= reorder_lvl:
            reorder_qty = max(reorder_qty, int(math.ceil(reorder_lvl * 2 - stock_val)))

        breakdown_rows.append(
            {
                "product_id": str(prod),
                "generic_name": generic,
                "category": category,
                "current_stock": stock_val,
                "daily_sales_velocity": daily_velocity,
                "days_of_supply": days_of_supply,
                "reorder_level": reorder_lvl,
                "stockout_risk": risk,
                "recommended_reorder_qty": reorder_qty,
                "supplier_id": supplier,
            }
        )

    # Sort breakdown by days_of_supply ascending (critical first)
    breakdown_rows.sort(key=lambda r: (
        r["days_of_supply"] if r["days_of_supply"] is not None else float("inf"),
        -r["daily_sales_velocity"],
        r["product_id"],
    ))

    formula = (
        f"stockout risk analysis across {product_groups.ngroups} product(s) ({period_str}): "
        f"days_of_supply = stock / daily_velocity; count of items with supply <= {threshold_days} days"
    )

    prov = build_provenance(df, cat_mask, filters, ["product_id", "quantity"], notes)
    return KPIResult(
        key=key,
        name=name,
        value=float(critical_count),
        unit=UNIT_COUNT,
        formula=formula,
        provenance=prov,
        breakdown=breakdown_rows,
        breakdown_columns=[
            "product_id", "generic_name", "category", "current_stock", "daily_sales_velocity",
            "days_of_supply", "reorder_level", "stockout_risk", "recommended_reorder_qty", "supplier_id"
        ],
    )


def stockout_risk_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """Distinct medicines running below critical days-of-supply threshold."""
    res = low_stock_reorder_predictions(df, filters, domain)
    return KPIResult(
        key="stockout_risk_count",
        name="Critical Stockout Risk Count",
        value=res.value if res.is_available else 0.0,
        unit=UNIT_COUNT,
        formula="count of products with days_of_supply <= 3 days based on sales velocity",
        provenance=res.provenance,
        breakdown=res.breakdown,
        breakdown_columns=res.breakdown_columns,
    )


# ---------------------------------------------------------------------------
# Supplier Credit (Accounts Payable) KPIs
# ---------------------------------------------------------------------------


def supplier_payable_total(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """
    Sum of outstanding payable amount to suppliers / distributors.
    """
    key, name = "supplier_payable_total", "Total Supplier Payable"
    formula = "sum of supplier_payable_amount across supplier accounts"

    pay_col = next((c for c in ("supplier_payable_amount", "payable_amount", "outstanding_payable", "balance_payable", "payable") if c in df.columns), None)
    if pay_col is None:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "canonical 'supplier_payable_amount' column is not present in data",
            build_provenance(df, pd.Series(False, index=df.index), filters, [], []),
        )

    pay_series = pd.to_numeric(df[pay_col], errors="coerce")
    mask = pay_series.notna() & (pay_series > 0)
    provenance = build_provenance(df, mask, filters, [pay_col], [])

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula, provenance=provenance
        )

    return KPIResult(
        key=key,
        name=name,
        value=_round_money(pay_series[mask].sum()),
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
    )


def supplier_payable_by_supplier(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """
    Outstanding supplier payable breakdown grouped by supplier_id,
    with earliest due date per supplier.
    """
    key, name = "supplier_payable_by_supplier", "Supplier Payable by Supplier"
    formula = "sum of supplier_payable_amount grouped by supplier_id with earliest due date"

    pay_col = next((c for c in ("supplier_payable_amount", "payable_amount", "outstanding_payable", "balance_payable", "payable") if c in df.columns), None)
    supp_col = next((c for c in ("supplier_id", "supplier_name", "supplier", "distributor", "vendor") if c in df.columns), None)

    if pay_col is None or supp_col is None:
        missing = []
        if pay_col is None:
            missing.append("'supplier_payable_amount'")
        if supp_col is None:
            missing.append("'supplier_id'")
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            f"required column(s) {', '.join(missing)} not present in data",
            build_provenance(df, pd.Series(False, index=df.index), filters, [], []),
        )

    due_col = next((c for c in ("supplier_payment_due_date", "payment_due_date", "due_date", "credit_due_date") if c in df.columns), None)
    columns_used = [pay_col, supp_col]
    if due_col:
        columns_used.append(due_col)

    pay_series = pd.to_numeric(df[pay_col], errors="coerce")
    mask = pay_series.notna() & (pay_series > 0) & df[supp_col].notna()
    provenance = build_provenance(df, mask, filters, columns_used, [])

    breakdown_columns = ["supplier_id", "payable_amount", "earliest_due_date", "record_count"]

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula,
            provenance=provenance, breakdown=[], breakdown_columns=breakdown_columns,
        )

    sub = pd.DataFrame({
        "supplier": df.loc[mask, supp_col].astype(str),
        "payable": pay_series[mask],
    })
    if due_col:
        sub["due_date"] = pd.to_datetime(df.loc[mask, due_col], errors="coerce")
    else:
        sub["due_date"] = pd.NaT

    grouped_rows = []
    for s_name, grp in sub.groupby("supplier"):
        tot_pay = float(grp["payable"].sum())
        valid_dates = grp["due_date"].dropna()
        earliest = valid_dates.min().strftime("%Y-%m-%d") if not valid_dates.empty else None
        grouped_rows.append({
            "supplier_id": str(s_name),
            "payable_amount": _round_money(tot_pay),
            "earliest_due_date": earliest,
            "record_count": int(len(grp)),
        })

    grouped_rows.sort(key=lambda r: (-r["payable_amount"], r["supplier_id"]))
    total_val = _round_money(sum(r["payable_amount"] for r in grouped_rows))

    return KPIResult(
        key=key,
        name=name,
        value=total_val,
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        breakdown=grouped_rows,
        breakdown_columns=breakdown_columns,
    )


# ---------------------------------------------------------------------------
# Dead Stock Analysis (Unsold Inventory >= 60 Days)
# ---------------------------------------------------------------------------


def dead_stock_value(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """
    Value of shelf stock items that have not had any sales for >= 60 days
    (measured against reference date).
    """
    key, name = "dead_stock_value", "Dead Stock Value (60+ Days)"
    raw_thresh = filters.option("dead_stock_days", 60)
    try:
        threshold_days = int(raw_thresh)
    except (TypeError, ValueError):
        threshold_days = 60

    formula = (
        f"sum(quantity x cost) for inventory rows where days_since_last_sale >= {threshold_days}, "
        f"measured from reference date"
    )

    view = _prepare_stock(df, filters, require_expiry=False, require_last_sold=True)
    if not view.ok:
        return unavailable(
            key, name, UNIT_CURRENCY, formula, view.reason,
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    mask = view.usable & (view.days_since_sale >= threshold_days)
    provenance = build_provenance(df, mask, filters, list(view.columns_used), list(view.notes))

    breakdown_columns = [
        "product_id", "batch_no", "last_sold_date", "days_since_last_sale",
        "quantity", "unit_value", "line_value", "source_row"
    ]

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula,
            provenance=provenance, breakdown=[], breakdown_columns=breakdown_columns,
        )

    table = pd.DataFrame(
        {
            "product_id": (
                df.loc[mask, "product_id"].astype(str) if "product_id" in df.columns else "(unknown)"
            ),
            "batch_no": (
                df.loc[mask, "batch_no"].astype(str) if "batch_no" in df.columns else "(unknown)"
            ),
            "last_sold_date": view.last_sold_date[mask].dt.strftime("%Y-%m-%d"),
            "days_since_last_sale": view.days_since_sale[mask].astype(int),
            "quantity": view.quantity[mask],
            "unit_value": view.unit_value[mask],
            "line_value": view.line_value[mask],
            "source_row": (
                pd.to_numeric(df.loc[mask, "source_row"], errors="coerce")
                if "source_row" in df.columns
                else pd.Series(pd.NA, index=df.index[mask])
            ),
        }
    ).sort_values(
        ["days_since_last_sale", "product_id"], ascending=[False, True], kind="mergesort"
    )

    breakdown_rows = []
    for _, r in table.iterrows():
        breakdown_rows.append(
            {
                "product_id": r["product_id"],
                "batch_no": r["batch_no"],
                "last_sold_date": r["last_sold_date"],
                "days_since_last_sale": int(r["days_since_last_sale"]),
                "quantity": float(r["quantity"]),
                "unit_value": _round_money(r["unit_value"]),
                "line_value": _round_money(r["line_value"]),
                "source_row": None if pd.isna(r["source_row"]) else int(r["source_row"]),
            }
        )

    return KPIResult(
        key=key,
        name=name,
        value=_round_money(view.line_value[mask].sum()),
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        breakdown=breakdown_rows,
        breakdown_columns=breakdown_columns,
    )


def dead_stock_item_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """
    Count of distinct products or product-batch pairs with no sales for >= 60 days.
    """
    key, name = "dead_stock_item_count", "Dead Stock Item Count (60+ Days)"
    raw_thresh = filters.option("dead_stock_days", 60)
    try:
        threshold_days = int(raw_thresh)
    except (TypeError, ValueError):
        threshold_days = 60

    formula = f"count of distinct products/batches where days_since_last_sale >= {threshold_days}"

    view = _prepare_stock(df, filters, require_expiry=False, require_last_sold=True)
    if not view.ok:
        return unavailable(
            key, name, UNIT_COUNT, formula, view.reason,
            build_provenance(df, pd.Series(False, index=df.index), filters, [], list(view.notes)),
        )

    mask = view.usable & (view.days_since_sale >= threshold_days)
    provenance = build_provenance(df, mask, filters, list(view.columns_used), list(view.notes))

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_COUNT, formula=formula, provenance=provenance
        )

    product = df.loc[mask, "product_id"].astype(str) if "product_id" in df.columns else ""
    batch = df.loc[mask, "batch_no"].astype(str) if "batch_no" in df.columns else ""
    pairs = pd.DataFrame({"p": product, "b": batch}) if "product_id" in df.columns else None
    value = float(len(pairs.drop_duplicates())) if pairs is not None else float(int(mask.sum()))

    return KPIResult(
        key=key, name=name, value=value, unit=UNIT_COUNT, formula=formula, provenance=provenance
    )


# ---------------------------------------------------------------------------
# Gross Margin by Category & Payment Method Mix KPIs
# ---------------------------------------------------------------------------


def gross_margin_by_category(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """
    Gross margin percentage grouped by product category, reusing core COGS matching.
    """
    from app.analytics.kpi import (
        classify_transactions,
        _amount_series,
        _cogs_series,
        _no_rows,
        _round_pct,
        _round_money,
    )

    key, name = "gross_margin_by_category", "Gross Margin by Category"
    formula = "(category_revenue - category_cogs) / category_revenue x 100 grouped by category"

    cat_col = next((c for c in ("category", "therapeutic_class", "type") if c in df.columns), None)
    if cat_col is None:
        return unavailable(
            key, name, UNIT_PERCENT, formula,
            "canonical 'category' column is not present in data",
            build_provenance(df, _no_rows(df), filters, [], []),
        )

    txn = classify_transactions(df)
    amounts, amount_cols, amount_notes = _amount_series(df)
    cogs, cost_cols, cost_notes = _cogs_series(df)

    if amounts is None or cogs is None:
        return unavailable(
            key, name, UNIT_PERCENT, formula,
            "a required column ('amount'/'unit_price'+'quantity', or 'cost') is not present",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    mask = txn.sale & amounts.notna() & cogs.notna() & df[cat_col].notna()
    columns_used = amount_cols + cost_cols + [cat_col]
    provenance = build_provenance(df, mask, filters, columns_used, txn.notes + amount_notes + cost_notes)

    breakdown_columns = ["category", "revenue", "cogs", "profit", "margin_pct", "item_count"]

    if not mask.any():
        return unavailable(
            key, name, UNIT_PERCENT, formula,
            "no sale rows have both an amount and a cost grouped by category",
            provenance,
        )

    sub = pd.DataFrame({
        "category": df.loc[mask, cat_col].astype(str),
        "amount": amounts[mask],
        "cogs": cogs[mask],
    })

    grouped_rows = []
    tot_rev = 0.0
    tot_cost = 0.0

    for cat_name, grp in sub.groupby("category"):
        cat_rev = float(grp["amount"].sum())
        cat_cost = float(grp["cogs"].sum())
        cat_profit = cat_rev - cat_cost
        cat_margin = (cat_profit / cat_rev * 100.0) if cat_rev > 0 else 0.0
        tot_rev += cat_rev
        tot_cost += cat_cost
        grouped_rows.append({
            "category": str(cat_name),
            "revenue": _round_money(cat_rev),
            "cogs": _round_money(cat_cost),
            "profit": _round_money(cat_profit),
            "margin_pct": _round_pct(cat_margin),
            "item_count": int(len(grp)),
        })

    grouped_rows.sort(key=lambda r: (-r["revenue"], r["category"]))
    overall_profit = tot_rev - tot_cost
    overall_margin = (overall_profit / tot_rev * 100.0) if tot_rev > 0 else 0.0

    return KPIResult(
        key=key,
        name=name,
        value=_round_pct(overall_margin),
        unit=UNIT_PERCENT,
        formula=formula,
        provenance=provenance,
        breakdown=grouped_rows,
        breakdown_columns=breakdown_columns,
    )


def payment_method_mix(df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy") -> KPIResult:
    """
    Revenue share and breakdown by payment method / client type.
    """
    from app.analytics.kpi import (
        classify_transactions,
        _amount_series,
        _no_rows,
        _round_pct,
        _round_money,
    )

    key, name = "payment_method_mix", "Payment Method Mix"
    formula = "sum of revenue grouped by payment_method with percentage share"

    pay_col = next((c for c in ("payment_method", "client_type", "payment_type", "mode_of_payment") if c in df.columns), None)
    if pay_col is None:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "neither 'payment_method' nor 'client_type' is present in data",
            build_provenance(df, _no_rows(df), filters, [], []),
        )

    txn = classify_transactions(df)
    amounts, cols, notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    mask = txn.sale & amounts.notna() & df[pay_col].notna()
    columns_used = cols + [pay_col]
    provenance = build_provenance(df, mask, filters, columns_used, txn.notes + notes)

    breakdown_columns = ["payment_method", "revenue", "share_pct", "transaction_count"]

    if not mask.any():
        return KPIResult(
            key=key, name=name, value=0.0, unit=UNIT_CURRENCY, formula=formula,
            provenance=provenance, breakdown=[], breakdown_columns=breakdown_columns,
        )

    sub = pd.DataFrame({
        "method": df.loc[mask, pay_col].astype(str),
        "amount": amounts[mask],
    })

    tot_rev = float(sub["amount"].sum())
    grouped_rows = []

    for m_name, grp in sub.groupby("method"):
        m_rev = float(grp["amount"].sum())
        m_share = (m_rev / tot_rev * 100.0) if tot_rev > 0 else 0.0
        grouped_rows.append({
            "payment_method": str(m_name),
            "revenue": _round_money(m_rev),
            "share_pct": _round_pct(m_share),
            "transaction_count": int(len(grp)),
        })

    grouped_rows.sort(key=lambda r: (-r["revenue"], r["payment_method"]))

    return KPIResult(
        key=key,
        name=name,
        value=_round_money(tot_rev),
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        breakdown=grouped_rows,
        breakdown_columns=breakdown_columns,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(engine, domain: str = "pharmacy") -> None:
    """
    Attach the expiry KPIs to a KPIEngine for `domain`.

    Called by `PharmacyDomainPack.register_kpis`, which the engine invokes lazily
    the first time it does pharmacy work. Idempotent: re-registration replaces,
    so reloading a domain never raises.
    """
    from app.analytics.engine import KPISpec

    specs = [
        KPISpec(
            "near_expiry_total", "Near-Expiry Stock Value", UNIT_CURRENCY,
            "Value of stock expiring within the widest configured bucket, excluding "
            "stock that has already expired.",
            near_expiry_total, domain=domain, tags=("money", "risk", "headline"),
        ),
        KPISpec(
            "expired_stock_value", "Expired Stock Value", UNIT_CURRENCY,
            "Value of stock whose expiry date has already passed.",
            expired_stock_value, domain=domain, tags=("money", "risk", "headline"),
        ),
        KPISpec(
            "near_expiry_item_count", "Near-Expiry Item Count", UNIT_COUNT,
            "Distinct product/batch pairs expiring within the widest configured bucket.",
            near_expiry_item_count, domain=domain, tags=("risk", "volume"),
        ),
        KPISpec(
            "expired_item_count", "Expired Item Count", UNIT_COUNT,
            "Distinct product/batch pairs already expired.",
            expired_item_count, domain=domain, tags=("risk", "volume"),
        ),
        KPISpec(
            "expiry_by_manufacturer", "Near-Expiry Value by Manufacturer", UNIT_CURRENCY,
            "Near-expiry stock value grouped by manufacturer (or supplier).",
            expiry_by_manufacturer, domain=domain, tags=("risk", "breakdown"),
        ),
        KPISpec(
            "scheduled_transaction_count", "Scheduled Medicine Transactions", UNIT_COUNT,
            "Distinct transactions for scheduled or prescription medicines.",
            scheduled_transaction_count, domain=domain, tags=("volume", "compliance"),
        ),
        KPISpec(
            "scheduled_units_sold", "Scheduled Units Sold", UNIT_COUNT,
            "Total units sold for scheduled or prescription medicines.",
            scheduled_units_sold, domain=domain, tags=("volume", "compliance"),
        ),
        KPISpec(
            "scheduled_sales_value", "Scheduled Sales Value", UNIT_CURRENCY,
            "Total sales value for scheduled or prescription medicines.",
            scheduled_sales_value, domain=domain, tags=("money", "compliance"),
        ),
        KPISpec(
            "expiring_medicines_liquidation", "Near-Expiry Medicines & Liquidation Plan", UNIT_CURRENCY,
            "Itemized medicines expiring within requested horizon with liquidation and discount recommendations.",
            expiring_medicines_liquidation, domain=domain, tags=("money", "risk", "actionable"),
        ),
        KPISpec(
            "low_stock_reorder_predictions", "Low-Stock & Reorder Point Predictions", UNIT_COUNT,
            "Predicts stockout risks and reorder quantities based on daily sales velocity and days of supply.",
            low_stock_reorder_predictions, domain=domain, tags=("volume", "velocity", "reorder"),
        ),
        KPISpec(
            "stockout_risk_count", "Critical Stockout Risk Count", UNIT_COUNT,
            "Count of fast-moving products running below critical supply threshold.",
            stockout_risk_count, domain=domain, tags=("volume", "risk"),
        ),
        KPISpec(
            "supplier_payable_total", "Total Supplier Payable", UNIT_CURRENCY,
            "Total outstanding accounts payable to distributors/suppliers.",
            supplier_payable_total, domain=domain, tags=("money", "credit", "payable"),
        ),
        KPISpec(
            "supplier_payable_by_supplier", "Supplier Payable by Supplier", UNIT_CURRENCY,
            "Outstanding supplier payable grouped by supplier with earliest due date.",
            supplier_payable_by_supplier, domain=domain, tags=("money", "credit", "breakdown"),
        ),
        KPISpec(
            "dead_stock_value", "Dead Stock Value (60+ Days)", UNIT_CURRENCY,
            "Value of shelf stock items unsold for 60+ days based on last sale date.",
            dead_stock_value, domain=domain, tags=("money", "inventory", "dead_stock"),
        ),
        KPISpec(
            "dead_stock_item_count", "Dead Stock Item Count (60+ Days)", UNIT_COUNT,
            "Count of distinct inventory items unsold for 60+ days.",
            dead_stock_item_count, domain=domain, tags=("volume", "inventory", "dead_stock"),
        ),
        KPISpec(
            "gross_margin_by_category", "Gross Margin by Category", UNIT_PERCENT,
            "Gross margin percentage grouped by product category.",
            gross_margin_by_category, domain=domain, tags=("margin", "category", "breakdown"),
        ),
        KPISpec(
            "payment_method_mix", "Payment Method Mix", UNIT_CURRENCY,
            "Revenue and percentage share by payment method (cash vs card vs credit).",
            payment_method_mix, domain=domain, tags=("money", "sales", "breakdown"),
        ),
    ]

    for low, high in _bucket_bands():
        specs.append(
            KPISpec(
                f"expiring_value_{high}d", f"Stock Value Expiring in {low}-{high} Days", UNIT_CURRENCY,
                f"Value of stock expiring {low}-{high} days from the reference date "
                f"(banded, so each item falls in exactly one bucket).",
                _make_bucket_kpi(low, high), domain=domain, tags=("money", "risk", "bucket"),
            )
        )

    for spec in specs:
        engine.register(spec, replace=True)

