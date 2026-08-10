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
    KPIResult,
    Period,
    unavailable,
)
from app.core.config import settings

_MONEY_ROUND = 2

# Question vocabulary for the chatbot's numeric route (English + Roman-Urdu).
# Lives here, on the domain side, so the chatbot core stays domain-agnostic.
PHARMACY_QUESTION_RULES: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    # Per-item demand framing: in a pharmacy, "how much will I need" is almost
    # always about one medicine, so these route to the per-product forecast first.
    # The generic forecast machinery lives in core; only this vocabulary is local.
    (
        (
            "demand", "how much should i order", "how much to order", "reorder",
            "kitna mangwana", "kitna order", "kitni dawai", "dawai ki demand",
            "stock kitna chahiye",
        ),
        ("product_demand_forecast", "demand_forecast"),
    ),
    (
        ("expired", "already expired", "dead stock", "expire ho gaya", "expire ho chuka"),
        ("expired_stock_value", "expired_item_count"),
    ),
    (
        (
            "expiring", "expire", "expiry", "near expiry", "near-expiry", "short expiry",
            "about to expire", "expire ho raha", "expire hone", "khatam ho raha",
            "khatam hone", "miyad", "meyad",
        ),
        ("near_expiry_total", "expiring_value_30d", "near_expiry_item_count"),
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


def _prepare_stock(df: pd.DataFrame, filters: KPIFilters) -> _StockView:
    """
    Validate the frame and derive expiry/value series once for every expiry KPI.

    Consumes the ALREADY-NORMALIZED `expiry_date` produced by the schema pipeline
    (which handles mm/yy, mm-yyyy, Excel serials, day-first strings). No date
    formats are re-parsed here; `to_datetime` is a coercion safety net only.
    """
    reference_date, notes = _resolve_reference_date(filters)

    if "expiry_date" not in df.columns:
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

    # --- value basis --------------------------------------------------------
    requested = str(filters.option("value_basis", settings.expiry_value_basis)).strip().casefold()
    if requested not in ("cost", "mrp"):
        notes.append(f"unknown value_basis '{requested}'; used '{settings.expiry_value_basis}' instead")
        requested = str(settings.expiry_value_basis).strip().casefold()
    fallback = "mrp" if requested == "cost" else "cost"

    def _usable_column(name: str) -> Optional[pd.Series]:
        if name not in df.columns:
            return None
        series = pd.to_numeric(df[name], errors="coerce")
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
    expiry = pd.to_datetime(df["expiry_date"], errors="coerce")
    quantity = pd.to_numeric(df["quantity"], errors="coerce")

    has_expiry = expiry.notna()
    has_qty = quantity.notna() & (quantity > 0)
    has_value = unit_value.notna()
    usable = has_expiry & has_qty & has_value

    # --- report every exclusion --------------------------------------------
    total = len(df)
    missing_expiry = int((~has_expiry).sum())
    if missing_expiry:
        notes.append(
            f"{missing_expiry} of {total} row(s) excluded: expiry date is missing or "
            f"could not be parsed. Their value is NOT included in any expiry figure."
        )
    missing_qty = int((has_expiry & ~has_qty).sum())
    if missing_qty:
        notes.append(
            f"{missing_qty} row(s) excluded: quantity is missing, non-numeric, or not greater than zero"
        )
    missing_value = int((has_expiry & has_qty & ~has_value).sum())
    if missing_value:
        notes.append(f"{missing_value} row(s) excluded: no '{basis}' value to price the stock with")

    # Stock-on-hand check: an invoice column means these are transactions, not shelf stock.
    if "invoice_id" in df.columns and df["invoice_id"].notna().any():
        notes.append(
            "this data carries invoice ids, so it looks like transactions rather than "
            "stock on hand; expiry values describe the rows present, which may not be "
            "current shelf stock"
        )

    days = (expiry.dt.normalize() - reference_date).dt.days

    columns = ["expiry_date", "quantity", basis]
    for optional in ("batch_no", "product_id"):
        if optional in df.columns:
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
