"""
Module 6.6 (KPI Engine) — forecast.py

Trend and forecast analytics, registered as ordinary core KPIs. Domain-agnostic:
a product is a product, whether it is sold in a shop or a clinic.

Deterministic given its inputs and the injected reference date, LLM-free, offline,
CPU-light. No Prophet, no ML frameworks — deliberately.

Honesty is the design constraint
--------------------------------
A small business's data is short and noisy. A confident wrong forecast is worse
than no forecast, so this module:

  * prefers simple, explainable methods and only climbs to a statistical model
    when there is genuinely enough regular history to justify one;
  * always returns a RANGE (lower/upper band), never a bare number;
  * REFUSES to forecast when history is too short or too gappy, returning
    `unavailable` with exactly how much data exists versus how much is needed;
  * names the method and tier behind every number, so nothing looks more certain
    than it is.

The tier ladder
---------------
    Tier 0  Descriptive trend        always available; measures the past, predicts nothing
    Tier 1  Moving-average baseline  the DEFAULT forecast; robust on thin retail data
    Tier 2  Exponential smoothing    optional (statsmodels), only with ample regular history

Auto-selection is deterministic: take the highest tier whose data requirement is
met, else drop a tier; below the Tier-1 minimum, refuse. A Tier-2 fit that raises
or returns non-finite numbers falls back to Tier 1 with a note — never to silence.

The uncertainty band is a VOLATILITY band, not a rigorous prediction interval: it
is `multiplier x residual_std x sqrt(horizon_step)`, so it widens the further out
the estimate goes. It is clamped at zero on the lower side, because negative sales
are not a thing. This is stated in the docs and in each result's method string.
"""

import math
from dataclasses import dataclass, field
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
    Provenance,
    unavailable,
)
from app.analytics.timeseries import (
    METRIC_REVENUE,
    METRIC_UNITS,
    TimeSeries,
    build_series,
    growth_rate,
    movers_rows,
    moving_average,
    window_comparison,
)
from app.core.config import settings

TIER_DESCRIPTIVE = 0
TIER_BASELINE = 1
TIER_SMOOTHING = 2


@dataclass(frozen=True)
class ForecastOutcome:
    """A forecast, or a documented refusal to produce one."""

    ok: bool
    reason: Optional[str] = None
    tier: Optional[int] = None
    method: Optional[str] = None
    points: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tier gate
# ---------------------------------------------------------------------------


def _history_verdict(series: TimeSeries) -> Tuple[bool, Optional[str]]:
    """
    Is this history good enough to forecast from at all?

    Two independent gates: enough periods, and enough of them actually observed.
    The second is what rejects "January, February, six-month hole, September" —
    zero-filling that gap would otherwise manufacture a plausible-looking series.
    """
    needed = int(settings.forecast_tier1_min_periods)
    if series.total_periods < needed:
        return False, (
            f"not enough history to forecast: {series.total_periods} {series.granularity} "
            f"period(s) available, at least {needed} needed"
        )

    min_ratio = float(settings.forecast_min_observed_ratio)
    if series.observed_ratio < min_ratio:
        return False, (
            f"history is too gappy to forecast: only {series.observed_periods} of "
            f"{series.total_periods} {series.granularity} periods contain any transactions "
            f"({series.observed_ratio:.0%}), below the {min_ratio:.0%} minimum. The gaps are "
            f"more likely to be missing records than genuine zero-sales periods."
        )
    return True, None


def _select_tier(series: TimeSeries) -> int:
    """Highest tier whose data requirement is met. Deterministic, no heuristics."""
    if (
        settings.forecast_enable_tier2
        and series.total_periods >= int(settings.forecast_tier2_min_periods)
        and _statsmodels_available()
    ):
        return TIER_SMOOTHING
    return TIER_BASELINE


def _statsmodels_available() -> bool:
    """Tier 2 is optional: the package is declared but may not be installed."""
    try:
        import statsmodels.tsa.holtwinters  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Tier 1 — moving-average baseline
# ---------------------------------------------------------------------------


def _rmse(errors: List[float]) -> float:
    """
    Root-mean-square error, measured about ZERO rather than about the mean error.

    This matters: on a steadily rising series a flat baseline is wrong by a similar
    amount every period, so those errors have almost no *scatter* — a standard
    deviation would report near-zero uncertainty for a forecast that is reliably
    too low. RMSE counts that systematic bias as the error it is, so the band
    reflects how wrong the method has actually been.
    """
    if not errors:
        return 0.0
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def _residual_spread(values: Tuple[float, ...], window: int) -> float:
    """
    Typical size of the baseline's own past errors: how far each period landed from
    the trailing mean of the periods before it. Falls back to the spread of the
    series itself when there are too few residuals to measure.
    """
    residuals = [
        values[i] - (sum(values[i - window : i]) / window) for i in range(window, len(values))
    ]
    if residuals:
        return _rmse(residuals)
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return _rmse([v - mean for v in values])


def _band(value: float, sigma: float, step: int) -> Tuple[float, float]:
    """
    Volatility band, widening with the square root of how far ahead we look.

    A floor of `forecast_min_band_ratio` of the estimate applies. In-sample
    residuals systematically UNDERSTATE out-of-sample error — a model that fits its
    own history perfectly (a smoother on a clean linear series, say) has residuals
    near zero and would otherwise report a zero-width band, i.e. total certainty
    about the future from a dozen data points. The floor makes that impossible.
    """
    step_scale = math.sqrt(step)
    spread = float(settings.forecast_band_multiplier) * sigma * step_scale
    floor = float(settings.forecast_min_band_ratio) * abs(value) * step_scale
    spread = max(spread, floor)
    return max(0.0, value - spread), value + spread


def _future_labels(series: TimeSeries, horizon: int) -> List[str]:
    """Labels for the periods after the end of the history."""
    from app.analytics.timeseries import _label_for

    last = series.period_index[-1]
    return [_label_for(last + i, series.granularity) for i in range(1, horizon + 1)]


def _tier1_baseline(series: TimeSeries, horizon: int) -> ForecastOutcome:
    """Forecast every future period at the mean of the last `w` observed periods."""
    values = series.values
    w = min(int(settings.forecast_baseline_window), len(values))
    w = max(w, 1)
    level = sum(values[-w:]) / w
    sigma = _residual_spread(values, w)

    points = []
    for step, label in enumerate(_future_labels(series, horizon), start=1):
        lower, upper = _band(level, sigma, step)
        points.append(
            {
                "period": label,
                "value": round(level, 2),
                "lower": round(lower, 2),
                "upper": round(upper, 2),
            }
        )

    return ForecastOutcome(
        ok=True,
        tier=TIER_BASELINE,
        method=(
            f"Tier 1 moving-average baseline: mean of the last {w} {series.granularity} "
            f"period(s), with a +/-{settings.forecast_band_multiplier} x volatility band "
            f"widening as sqrt(steps ahead)"
        ),
        points=points,
    )


# ---------------------------------------------------------------------------
# Tier 2 — exponential smoothing (optional)
# ---------------------------------------------------------------------------


def _tier2_smoothing(series: TimeSeries, horizon: int) -> ForecastOutcome:
    """
    Holt exponential smoothing (level + additive trend).

    Any failure — import, fit, or non-finite output — is caught by the caller and
    downgraded to Tier 1 with a note. A broken model must never become a number.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    data = pd.Series(list(series.values), dtype="float64")
    model = ExponentialSmoothing(
        data, trend="add", seasonal=None, initialization_method="estimated"
    ).fit(optimized=True)

    predicted = model.forecast(horizon)
    if predicted.isna().any() or not all(math.isfinite(float(v)) for v in predicted):
        raise ValueError("exponential smoothing produced non-finite values")

    residuals = pd.Series(model.resid).dropna()
    sigma = _rmse([float(r) for r in residuals])
    if not math.isfinite(sigma):
        raise ValueError("exponential smoothing produced a non-finite residual spread")

    points = []
    for step, (label, value) in enumerate(
        zip(_future_labels(series, horizon), predicted.tolist()), start=1
    ):
        value = max(0.0, float(value))
        lower, upper = _band(value, sigma, step)
        points.append(
            {
                "period": label,
                "value": round(value, 2),
                "lower": round(lower, 2),
                "upper": round(upper, 2),
            }
        )

    return ForecastOutcome(
        ok=True,
        tier=TIER_SMOOTHING,
        method=(
            f"Tier 2 exponential smoothing (Holt, level + additive trend) over "
            f"{series.total_periods} {series.granularity} periods, with a "
            f"+/-{settings.forecast_band_multiplier} x residual band widening as sqrt(steps ahead), floored at "
            f"+/-{settings.forecast_min_band_ratio:.0%} of the estimate"
        ),
        points=points,
    )


def make_forecast(series: TimeSeries, horizon: int) -> ForecastOutcome:
    """Run the ladder: gate on history, pick the tier, fall back on failure."""
    usable, reason = _history_verdict(series)
    if not usable:
        return ForecastOutcome(ok=False, reason=reason)

    notes: List[str] = []
    if _select_tier(series) == TIER_SMOOTHING:
        try:
            return _tier2_smoothing(series, horizon)
        except Exception as exc:  # noqa: BLE001 - any failure downgrades, never crashes
            notes.append(
                f"exponential smoothing (Tier 2) failed and was not used ({exc}); "
                f"fell back to the Tier 1 moving-average baseline"
            )
    elif (
        settings.forecast_enable_tier2
        and series.total_periods >= int(settings.forecast_tier2_min_periods)
        and not _statsmodels_available()
    ):
        notes.append(
            "statsmodels is not installed, so the Tier 2 exponential-smoothing model "
            "was unavailable; used the Tier 1 moving-average baseline instead"
        )

    outcome = _tier1_baseline(series, horizon)
    return ForecastOutcome(
        ok=True, tier=outcome.tier, method=outcome.method, points=outcome.points,
        notes=notes + outcome.notes,
    )


# ---------------------------------------------------------------------------
# Shared result assembly
# ---------------------------------------------------------------------------


def _horizon(filters: KPIFilters) -> int:
    requested = filters.option("horizon", settings.forecast_horizon)
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return int(settings.forecast_horizon)
    return max(1, value)


def _empty_provenance(df: pd.DataFrame, filters: KPIFilters, notes: List[str]) -> Provenance:
    return build_provenance(df, pd.Series(False, index=df.index), filters, [], notes)


def _series_period(series: TimeSeries) -> Optional[Period]:
    if not series.labels:
        return None
    return Period(start=series.labels[0], end=series.labels[-1])


def _trend_kpi(
    df: pd.DataFrame, filters: KPIFilters, key: str, name: str, metric: str, unit: str
) -> KPIResult:
    """Tier 0: the observed series, its moving average, and period-over-period growth."""
    series = build_series(df, filters, metric)
    formula = (
        f"{metric} per {series.granularity} period, with a "
        f"{settings.trend_moving_average_window}-period moving average; headline value is "
        f"period-over-period growth %"
    )

    if not series.ok:
        return unavailable(
            key, name, UNIT_PERCENT, formula, series.reason,
            _empty_provenance(df, filters, list(series.notes)),
        )

    ma = moving_average(series.values)
    points = series.points(ma)
    growth, growth_reason = growth_rate(series.values)
    provenance = build_provenance(
        df, series.mask, filters, list(series.columns_used), list(series.notes)
    )
    method = (
        f"Tier 0 descriptive: measured {series.granularity} totals. "
        f"This describes the past and predicts nothing."
    )

    if growth is None:
        return KPIResult(
            key=key, name=name, value=None, unit=UNIT_PERCENT, formula=formula,
            provenance=provenance, status="unavailable", reason=growth_reason,
            period=_series_period(series), method=method, series=points,
        )

    return KPIResult(
        key=key, name=name, value=growth, unit=UNIT_PERCENT, formula=formula,
        provenance=provenance, period=_series_period(series), method=method, series=points,
    )


def _forecast_kpi(
    df: pd.DataFrame, filters: KPIFilters, key: str, name: str, metric: str, unit: str
) -> KPIResult:
    """Tier 1/2: history plus `horizon` future periods, each with an uncertainty band."""
    horizon = _horizon(filters)
    series = build_series(df, filters, metric)
    formula = (
        f"{metric} forecast for the next {horizon} {series.granularity} period(s), "
        f"each reported as a range rather than a single number"
    )

    if not series.ok:
        return unavailable(
            key, name, unit, formula, series.reason,
            _empty_provenance(df, filters, list(series.notes)),
        )

    outcome = make_forecast(series, horizon)
    notes = list(series.notes) + list(outcome.notes)
    provenance = build_provenance(df, series.mask, filters, list(series.columns_used), notes)

    if not outcome.ok:
        return KPIResult(
            key=key, name=name, value=None, unit=unit, formula=formula,
            provenance=provenance, status="unavailable", reason=outcome.reason,
            period=_series_period(series), method="no forecast produced",
            series=series.points(moving_average(series.values)),
        )

    first = outcome.points[0]
    return KPIResult(
        key=key, name=name, value=first["value"], unit=unit, formula=formula,
        provenance=provenance, period=_series_period(series), method=outcome.method,
        series=series.points(moving_average(series.values)), forecast=outcome.points,
    )


# ---------------------------------------------------------------------------
# Registered KPIs
# ---------------------------------------------------------------------------


def revenue_trend(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Observed revenue per period, smoothed, with period-over-period growth."""
    return _trend_kpi(df, filters, "revenue_trend", "Revenue Trend", METRIC_REVENUE, UNIT_CURRENCY)


def units_trend(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Observed units sold per period, smoothed, with period-over-period growth."""
    return _trend_kpi(df, filters, "units_trend", "Units Sold Trend", METRIC_UNITS, UNIT_COUNT)


def revenue_forecast(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Forecast revenue for the next `horizon` periods, with uncertainty bands."""
    return _forecast_kpi(
        df, filters, "revenue_forecast", "Revenue Forecast", METRIC_REVENUE, UNIT_CURRENCY
    )


def demand_forecast(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Forecast total units sold for the next `horizon` periods, with uncertainty bands."""
    return _forecast_kpi(
        df, filters, "demand_forecast", "Demand Forecast (units)", METRIC_UNITS, UNIT_COUNT
    )


def product_demand_forecast(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Forecast units for ONE product, from that product's own history.

    Requires a `product_id` filter — forecasting "a product" without saying which
    is meaningless. Sparse sellers are common and correctly return
    insufficient-history rather than a fabricated number.
    """
    key, name = "product_demand_forecast", "Product Demand Forecast (units)"
    if not filters.product_id:
        return unavailable(
            key, name, UNIT_COUNT,
            "units forecast for a single product, from that product's own history",
            "no product was specified: set the product_id filter to forecast one product's demand",
            _empty_provenance(df, filters, []),
        )
    result = _forecast_kpi(df, filters, key, name, METRIC_UNITS, UNIT_COUNT)
    if not result.is_available and result.reason and "history" in result.reason:
        # Make the sparse-seller case unmistakable to whoever reads it.
        return KPIResult(
            key=result.key, name=result.name, value=None, unit=result.unit,
            formula=result.formula, provenance=result.provenance, status=result.status,
            reason=f"'{filters.product_id}' cannot be forecast: {result.reason}",
            period=result.period, method=result.method, series=result.series,
        )
    return result


def _movers_kpi(df: pd.DataFrame, filters: KPIFilters, rising: bool) -> KPIResult:
    """Products whose sales grew or shrank most between the last two windows."""
    key = "top_rising_products" if rising else "top_declining_products"
    name = "Top Rising Products" if rising else "Top Declining Products"
    window = int(settings.trend_movers_window)
    formula = (
        f"change in revenue over the last {window} period(s) versus the "
        f"{window} period(s) before, grouped by product_id"
    )

    series = build_series(df, filters, METRIC_REVENUE)
    if not series.ok:
        return unavailable(
            key, name, UNIT_CURRENCY, formula, series.reason,
            _empty_provenance(df, filters, list(series.notes)),
        )

    table, reason = window_comparison(df, series, "product_id", window)
    provenance = build_provenance(
        df, series.mask, filters, list(series.columns_used) + ["product_id"], list(series.notes)
    )
    if table is None:
        return unavailable(key, name, UNIT_CURRENCY, formula, reason, provenance)

    rows = movers_rows(table, "product_id", rising, int(settings.trend_movers_top_n))
    total = round(float(sum(r["change"] for r in rows)), 2)
    return KPIResult(
        key=key, name=name, value=total, unit=UNIT_CURRENCY, formula=formula,
        provenance=provenance, period=_series_period(series),
        method=(
            f"Tier 0 descriptive: measured window-over-window change. "
            f"Recent window {series.labels[-window:]} vs prior {series.labels[-2 * window:-window]}."
        ),
        breakdown=rows,
        breakdown_columns=["product_id", "recent", "prior", "change", "change_pct"],
    )


def top_rising_products(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Products growing fastest between the last two windows."""
    return _movers_kpi(df, filters, rising=True)


def top_declining_products(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Products shrinking fastest between the last two windows."""
    return _movers_kpi(df, filters, rising=False)
