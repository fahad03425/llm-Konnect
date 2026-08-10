"""
Module 6.6 (KPI Engine) — timeseries.py

Turning a canonical transaction frame into a clean, regular time series, plus the
purely DESCRIPTIVE trend statistics built on it (moving average, growth rate,
rising/declining movers).

Nothing here predicts anything — these are facts about the past. Forecasting lives
in `forecast.py` and builds on this module.

Deterministic, LLM-free, offline, domain-agnostic. Reuses the existing sale-row
classification and amount handling from `kpi.py` rather than duplicating them.

Missing periods
---------------
A calendar period with no transactions is materialised with a value of 0 (a month
with no sales genuinely earned nothing), but ONLY between the first and last
observed period — the series is never extended past the data. Each point records
whether it was `observed` or zero-filled, and the ratio of observed to total
periods is reported so a caller can tell a dense history from a gappy one. That
ratio is what stops a series like "January, February, then a six-month hole,
then September" being averaged into a confident nonsense forecast.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.filters import KPIFilters
from app.analytics.kpi import _amount_series, classify_transactions
from app.core.config import settings

# granularity -> (pandas period alias, human label)
GRANULARITY_FREQ: Dict[str, str] = {"daily": "D", "weekly": "W", "monthly": "M"}

# The metrics a series can be built over.
METRIC_REVENUE = "revenue"
METRIC_UNITS = "units"


@dataclass(frozen=True)
class TimeSeries:
    """A regular, gap-filled series plus everything needed to judge its quality."""

    ok: bool
    reason: Optional[str] = None
    granularity: str = "monthly"
    metric: str = METRIC_REVENUE
    unit: str = "PKR"
    labels: Tuple[str, ...] = ()
    values: Tuple[float, ...] = ()
    observed: Tuple[bool, ...] = ()
    row_counts: Tuple[int, ...] = ()
    mask: Optional[pd.Series] = None
    columns_used: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    period_index: Optional[pd.PeriodIndex] = None

    @property
    def total_periods(self) -> int:
        return len(self.values)

    @property
    def observed_periods(self) -> int:
        return sum(1 for o in self.observed if o)

    @property
    def observed_ratio(self) -> float:
        return self.observed_periods / self.total_periods if self.total_periods else 0.0

    def points(self, moving_average: Optional[List[Optional[float]]] = None) -> List[Dict[str, Any]]:
        """JSON-serializable history points, oldest first."""
        out: List[Dict[str, Any]] = []
        for i, label in enumerate(self.labels):
            point: Dict[str, Any] = {
                "period": label,
                "value": round(float(self.values[i]), 2),
                "observed": bool(self.observed[i]),
                "row_count": int(self.row_counts[i]),
            }
            if moving_average is not None:
                ma = moving_average[i]
                point["moving_average"] = None if ma is None else round(float(ma), 2)
            out.append(point)
        return out


def resolve_granularity(filters: KPIFilters) -> Tuple[str, List[str]]:
    """Granularity from the filter options, falling back to the configured default."""
    notes: List[str] = []
    requested = str(filters.option("granularity", settings.forecast_granularity)).strip().casefold()
    if requested not in GRANULARITY_FREQ:
        notes.append(
            f"unknown granularity '{requested}'; used '{settings.forecast_granularity}' instead"
        )
        requested = settings.forecast_granularity
    return requested, notes


def resolve_as_of(filters: KPIFilters) -> Tuple[Optional[pd.Timestamp], List[str]]:
    """
    The reference date history is truncated at.

    Injected, never read from the clock inside the maths, so a trend or forecast is
    reproducible and a user can ask "as of month-end". None means "use all data".
    """
    notes: List[str] = []
    if not filters.as_of:
        return None, notes
    parsed = pd.to_datetime(filters.as_of, errors="coerce")
    if pd.isna(parsed):
        notes.append(f"as_of '{filters.as_of}' could not be parsed as a date; ignored")
        return None, notes
    return parsed.normalize(), notes


def _label_for(period, granularity: str) -> str:
    """Stable period label: 'YYYY-MM' monthly, ISO start date for weekly/daily."""
    if granularity == "monthly":
        return period.strftime("%Y-%m")
    return period.start_time.strftime("%Y-%m-%d")


def _metric_values(
    df: pd.DataFrame, metric: str
) -> Tuple[Optional[pd.Series], List[str], List[str]]:
    """The per-row quantity being summed: money for revenue, units for demand."""
    if metric == METRIC_UNITS:
        if "quantity" not in df.columns:
            return None, [], []
        return pd.to_numeric(df["quantity"], errors="coerce"), ["quantity"], []
    return _amount_series(df)


def build_series(
    df: pd.DataFrame,
    filters: KPIFilters,
    metric: str = METRIC_REVENUE,
    granularity: Optional[str] = None,
) -> TimeSeries:
    """
    Aggregate sale rows into a regular series of `granularity` periods.

    Returns a `TimeSeries` whose `ok` is False, with a reason, whenever the frame
    cannot produce one (no date column, no metric column, no usable rows).
    """
    notes: List[str] = []
    if granularity is None:
        granularity, gran_notes = resolve_granularity(filters)
        notes.extend(gran_notes)
    unit = "count" if metric == METRIC_UNITS else "PKR"

    if "date" not in df.columns:
        return TimeSeries(
            ok=False, granularity=granularity, metric=metric, unit=unit,
            reason="canonical 'date' column is not present, so no time series can be built",
            notes=tuple(notes),
        )

    values, columns, value_notes = _metric_values(df, metric)
    if values is None:
        missing = "quantity" if metric == METRIC_UNITS else "amount (or unit_price and quantity)"
        return TimeSeries(
            ok=False, granularity=granularity, metric=metric, unit=unit,
            reason=f"canonical '{missing}' is not present, so {metric} cannot be measured",
            notes=tuple(notes),
        )
    notes.extend(value_notes)

    txn = classify_transactions(df)
    notes.extend(txn.notes)

    dates = pd.to_datetime(df["date"], errors="coerce")
    mask = txn.sale & values.notna() & dates.notna()

    as_of, as_of_notes = resolve_as_of(filters)
    notes.extend(as_of_notes)
    truncated = 0
    if as_of is not None:
        after = mask & (dates > as_of)
        truncated = int(after.sum())
        if truncated:
            notes.append(
                f"{truncated} row(s) dated after the reference date "
                f"{as_of.strftime('%Y-%m-%d')} were excluded from the history"
            )
        mask = mask & (dates <= as_of)

    if not mask.any():
        # Distinguish "the data is unusable" from "everything fell outside the
        # reference window" — they call for completely different user actions.
        if truncated:
            reason = (
                f"no usable sale rows on or before the reference date "
                f"{as_of.strftime('%Y-%m-%d')}: all {truncated} matching row(s) are dated after it"
            )
        else:
            reason = "no sale rows have both a usable value and a parseable date"
        return TimeSeries(
            ok=False, granularity=granularity, metric=metric, unit=unit,
            reason=reason, notes=tuple(notes), columns_used=tuple(columns + ["date"]),
        )

    freq = GRANULARITY_FREQ[granularity]
    periods = dates[mask].dt.to_period(freq)
    grouped = pd.DataFrame({"period": periods, "value": values[mask]}).groupby("period", sort=True)
    totals = grouped["value"].sum()
    counts = grouped["value"].size()

    # Materialise every calendar period between the first and last observation.
    full_index = pd.period_range(totals.index.min(), totals.index.max(), freq=freq)
    filled = totals.reindex(full_index, fill_value=0.0)
    filled_counts = counts.reindex(full_index, fill_value=0)
    observed = tuple(bool(p in set(totals.index)) for p in full_index)

    gaps = len(full_index) - len(totals.index)
    if gaps:
        notes.append(
            f"{gaps} of {len(full_index)} {granularity} period(s) had no transactions and "
            f"were filled with 0"
        )

    return TimeSeries(
        ok=True,
        granularity=granularity,
        metric=metric,
        unit=unit,
        labels=tuple(_label_for(p, granularity) for p in full_index),
        values=tuple(float(v) for v in filled.tolist()),
        observed=observed,
        row_counts=tuple(int(c) for c in filled_counts.tolist()),
        mask=mask,
        columns_used=tuple(columns + ["date"]),
        notes=tuple(notes),
        period_index=full_index,
    )


# ---------------------------------------------------------------------------
# Descriptive statistics (Tier 0 — measurements, not predictions)
# ---------------------------------------------------------------------------


def moving_average(values: Tuple[float, ...], window: Optional[int] = None) -> List[Optional[float]]:
    """
    Trailing mean over `window` periods; None until the window is full.

    Never invents a value for early periods, so a chart cannot imply smoothed data
    that does not exist.
    """
    w = int(window or settings.trend_moving_average_window)
    if w < 1:
        w = 1
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < w:
            out.append(None)
        else:
            window_vals = values[i + 1 - w : i + 1]
            out.append(sum(window_vals) / w)
    return out


def growth_rate(values: Tuple[float, ...]) -> Tuple[Optional[float], Optional[str]]:
    """
    Period-over-period growth as a percentage: (last - previous) / previous x 100.

    Returns (None, reason) when it cannot be computed — fewer than two periods, or
    a zero previous period (a percentage change from zero is undefined, not infinite).
    """
    if len(values) < 2:
        return None, "at least two periods are needed to measure growth"
    previous, latest = values[-2], values[-1]
    if previous == 0:
        return None, "the previous period was zero, so a percentage change is undefined"
    return round(100.0 * (latest - previous) / previous, 2), None


def window_comparison(
    df: pd.DataFrame,
    series: TimeSeries,
    group_column: str,
    window: Optional[int] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Compare a recent window of periods against the window immediately before it,
    grouped by `group_column`. The basis for rising/declining movers.

    Returns (table, None) or (None, reason). The table has one row per group with
    `recent`, `prior`, `change` and `change_pct` (None when the prior window was
    zero — a new seller, not an infinite riser).
    """
    w = int(window or settings.trend_movers_window)
    if w < 1:
        w = 1
    if series.total_periods < 2 * w:
        return None, (
            f"need at least {2 * w} {series.granularity} periods to compare a recent "
            f"window against the one before it, but only {series.total_periods} exist"
        )
    if group_column not in df.columns:
        return None, f"canonical '{group_column}' column is not present"

    labels = series.labels
    recent_labels = set(labels[-w:])
    prior_labels = set(labels[-2 * w : -w])

    freq = GRANULARITY_FREQ[series.granularity]
    dates = pd.to_datetime(df["date"], errors="coerce")
    values, _, _ = _metric_values(df, series.metric)
    row_labels = dates.dt.to_period(freq).map(
        lambda p: _label_for(p, series.granularity) if pd.notna(p) else None
    )

    base = series.mask & df[group_column].notna()
    frame = pd.DataFrame(
        {
            "group": df.loc[base, group_column].astype(str),
            "label": row_labels[base],
            "value": values[base],
        }
    )
    recent = frame[frame["label"].isin(recent_labels)].groupby("group")["value"].sum()
    prior = frame[frame["label"].isin(prior_labels)].groupby("group")["value"].sum()

    table = pd.DataFrame({"recent": recent, "prior": prior}).fillna(0.0)
    if table.empty:
        return None, "no rows fall inside the comparison windows"

    table["change"] = table["recent"] - table["prior"]
    table["change_pct"] = [
        None if p == 0 else round(100.0 * (r - p) / p, 2)
        for r, p in zip(table["recent"], table["prior"])
    ]
    table = table.reset_index().rename(columns={"group": group_column})
    return table, None


def movers_rows(table: pd.DataFrame, group_column: str, rising: bool, top_n: int) -> List[Dict[str, Any]]:
    """Top movers, sorted by absolute change then group name so ties never wobble."""
    ordered = table.sort_values(
        ["change", group_column], ascending=[not rising, True], kind="mergesort"
    )
    ordered = ordered[ordered["change"] > 0] if rising else ordered[ordered["change"] < 0]
    return [
        {
            group_column: row[group_column],
            "recent": round(float(row["recent"]), 2),
            "prior": round(float(row["prior"]), 2),
            "change": round(float(row["change"]), 2),
            "change_pct": None if row["change_pct"] is None else float(row["change_pct"]),
        }
        for _, row in ordered.head(top_n).iterrows()
    ]
