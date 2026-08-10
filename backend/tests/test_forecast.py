"""
Trend & forecast analytics — tests.

Every assertion uses a PINNED as-of date and a hand-built dated frame whose
expected values are arithmetic you can verify. Offline: no LLM, no network.

The honesty behaviour is tested as hard as the arithmetic: short history, gappy
history and sparse products must all REFUSE to forecast, with a reason.
"""

import json
import math
import os

import pandas as pd
import pytest

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.analytics.forecast import _rmse, _statsmodels_available, make_forecast
from app.analytics.models import STATUS_OK, STATUS_UNAVAILABLE
from app.analytics.seam import AnalyticsRouter, infer_product_id, select_kpi_keys
from app.analytics.timeseries import build_series, growth_rate, moving_average
from app.core.config import settings
import app.schema  # noqa: F401  (registers domain packs)

AS_OF = "2025-12-31"
PINNED = KPIFilters(as_of=AS_OF)
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples")


def _month_rows(monthly_amounts, product="Panadol", start="2025-01-01", first_row=2):
    """One sale row per month, amounts given oldest-first."""
    rows = []
    for i, amount in enumerate(monthly_amounts):
        when = pd.Timestamp(start) + pd.DateOffset(months=i)
        rows.append(
            {
                "source_row": first_row + i,
                "txn_type": "sale",
                "date": when.strftime("%Y-%m-%d"),
                "amount": float(amount),
                "quantity": float(amount) / 10.0,
                "product_id": product,
            }
        )
    return rows


@pytest.fixture
def flat_series() -> pd.DataFrame:
    """
    Six months: 100, 110, 90, 120, 90, 120. Baseline level = mean of the last
    three = (120 + 90 + 120) / 3 = 110.

    Trailing-mean residuals (window 3):
        120 - mean(100,110,90)  = 120 - 100.0000 =  20.0000
         90 - mean(110,90,120)  =  90 - 106.6667 = -16.6667
        120 - mean(90,120,90)   = 120 - 100.0000 =  20.0000
    RMSE = sqrt((400 + 277.78 + 400) / 3) = 18.9541
    """
    return pd.DataFrame(_month_rows([100, 110, 90, 120, 90, 120]))


@pytest.fixture
def rising_series() -> pd.DataFrame:
    """Twelve months rising 100, 110, ... 210."""
    return pd.DataFrame(_month_rows([100 + 10 * i for i in range(12)]))


# ---------------------------------------------------------------------------
# Tier 0 — series building, moving average, growth
# ---------------------------------------------------------------------------


def test_series_resamples_to_monthly_totals(flat_series):
    series = build_series(flat_series, PINNED, "revenue")
    assert series.ok
    assert series.labels == ("2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06")
    assert series.values == (100.0, 110.0, 90.0, 120.0, 90.0, 120.0)
    assert series.observed_periods == 6
    assert series.observed_ratio == 1.0


def test_moving_average_exact_and_none_before_window_is_full():
    values = (100.0, 110.0, 90.0, 120.0)
    assert moving_average(values, 3) == [None, None, 100.0, pytest.approx(106.6667)]


def test_growth_rate_exact(flat_series):
    """Last two periods are 90 -> 120, i.e. +33.33%."""
    result = engine.compute("revenue_trend", flat_series, PINNED)
    assert result.status == STATUS_OK
    assert result.value == 33.33
    assert result.unit == "percent"
    assert "predicts nothing" in result.method


def test_growth_rate_undefined_when_previous_period_is_zero():
    value, reason = growth_rate((0.0, 50.0))
    assert value is None
    assert "zero" in reason


def test_trend_series_carries_moving_average(flat_series):
    result = engine.compute("revenue_trend", flat_series, PINNED)
    assert result.series[0]["moving_average"] is None      # window not full yet
    assert result.series[2]["moving_average"] == 100.0     # (100+110+90)/3
    assert result.series[-1]["moving_average"] == 110.0    # (120+90+120)/3


def test_units_trend_uses_quantity(flat_series):
    result = engine.compute("units_trend", flat_series, PINNED)
    assert result.status == STATUS_OK
    assert [p["value"] for p in result.series] == [10.0, 11.0, 9.0, 12.0, 9.0, 12.0]


# ---------------------------------------------------------------------------
# Missing periods
# ---------------------------------------------------------------------------


def test_missing_periods_are_zero_filled_and_flagged():
    """A month with no sales becomes 0 and is marked unobserved."""
    rows = _month_rows([100, 100]) + _month_rows([100], start="2025-06-01", first_row=20)
    series = build_series(pd.DataFrame(rows), PINNED, "revenue")
    assert series.labels == ("2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06")
    assert series.values == (100.0, 100.0, 0.0, 0.0, 0.0, 100.0)
    assert series.observed == (True, True, False, False, False, True)
    assert series.observed_ratio == 0.5
    assert any("filled with 0" in note for note in series.notes)


def test_series_never_extends_past_the_data(flat_series):
    series = build_series(flat_series, PINNED, "revenue")
    assert series.labels[-1] == "2025-06"  # not padded out to the as-of date


def test_as_of_truncates_history(flat_series):
    series = build_series(flat_series, KPIFilters(as_of="2025-03-31"), "revenue")
    assert series.labels == ("2025-01", "2025-02", "2025-03")
    assert any("dated after the reference date" in note for note in series.notes)


def test_everything_after_as_of_says_so_rather_than_blaming_the_data(flat_series):
    """'All your rows are in the future' is a different problem from 'unusable data'."""
    series = build_series(flat_series, KPIFilters(as_of="2024-01-01"), "revenue")
    assert not series.ok
    assert "all 6 matching row(s) are dated after it" in series.reason


# ---------------------------------------------------------------------------
# Tier 1 baseline — exact values and bands
# ---------------------------------------------------------------------------


def test_tier1_baseline_value_and_band_exact(flat_series):
    """
    Level = mean(120, 90, 120) = 110. Residual RMSE = 18.9541 (see fixture).
    Band step 1 = 1.96 x 18.9541 x sqrt(1) = 37.15 -> [72.85, 147.15]
    """
    result = engine.compute("revenue_forecast", flat_series, PINNED)
    assert result.status == STATUS_OK
    assert result.value == 110.0
    assert "Tier 1" in result.method

    first = result.forecast[0]
    assert first["period"] == "2025-07"
    assert first["value"] == 110.0
    assert first["lower"] == pytest.approx(72.85, abs=0.01)
    assert first["upper"] == pytest.approx(147.15, abs=0.01)


def test_band_widens_with_horizon(flat_series):
    """Uncertainty must grow the further ahead the estimate goes."""
    result = engine.compute("revenue_forecast", flat_series, PINNED)
    widths = [p["upper"] - p["lower"] for p in result.forecast]
    assert widths == sorted(widths)
    assert widths[1] == pytest.approx(widths[0] * math.sqrt(2), abs=0.01)
    assert widths[2] == pytest.approx(widths[0] * math.sqrt(3), abs=0.01)


def test_forecast_horizon_is_configurable(flat_series):
    result = engine.compute(
        "revenue_forecast", flat_series, KPIFilters(as_of=AS_OF, options={"horizon": 5})
    )
    assert len(result.forecast) == 5
    assert [p["period"] for p in result.forecast] == [
        "2025-07", "2025-08", "2025-09", "2025-10", "2025-11",
    ]


def test_lower_band_is_clamped_at_zero():
    """Negative sales are not a thing."""
    result = engine.compute("revenue_forecast", pd.DataFrame(_month_rows([1, 200, 1, 200])), PINNED)
    assert all(p["lower"] >= 0.0 for p in result.forecast)
    assert result.forecast[0]["lower"] == 0.0


def test_rmse_counts_systematic_bias_not_just_scatter():
    """Constant error must NOT read as zero uncertainty."""
    assert _rmse([20.0, 20.0, 20.0]) == 20.0


def test_rising_series_gets_a_nonzero_band(rising_series):
    """A flat baseline on a rising series is reliably wrong; the band must say so."""
    result = engine.compute("revenue_forecast", rising_series, PINNED)
    assert result.forecast[0]["upper"] > result.forecast[0]["value"] > result.forecast[0]["lower"]
    assert result.forecast[0]["upper"] - result.forecast[0]["lower"] > 0


def test_band_never_collapses_to_zero_width(rising_series):
    """
    A model that fits its own history perfectly still cannot claim certainty about
    the future: in-sample residuals understate out-of-sample error, so a floor of
    `forecast_min_band_ratio` of the estimate always applies.
    """
    result = engine.compute("revenue_forecast", rising_series, PINNED)
    first = result.forecast[0]
    floor = settings.forecast_min_band_ratio * first["value"]
    tolerance = 0.01  # results are rounded to 2 decimals
    assert first["upper"] - first["value"] >= floor - tolerance
    assert first["value"] - first["lower"] >= floor - tolerance


def test_band_floor_scales_with_horizon(rising_series):
    result = engine.compute("revenue_forecast", rising_series, PINNED)
    widths = [p["upper"] - p["lower"] for p in result.forecast]
    assert widths[1] > widths[0]
    assert widths[2] > widths[1]


# ---------------------------------------------------------------------------
# Tier selection and the refusal path
# ---------------------------------------------------------------------------


def test_insufficient_history_refuses_to_forecast():
    result = engine.compute("revenue_forecast", pd.DataFrame(_month_rows([100, 110, 90])), PINNED)
    assert result.status == STATUS_UNAVAILABLE
    assert result.value is None
    assert result.forecast is None
    assert "3 monthly period(s) available, at least 4 needed" in result.reason
    # The observed history is still returned, so the user sees what does exist.
    assert len(result.series) == 3


def test_gappy_history_refuses_even_when_long_enough():
    """Jan, Feb then a hole to September: 9 periods but only 3 observed."""
    rows = _month_rows([100, 100]) + _month_rows([100], start="2025-09-01", first_row=20)
    result = engine.compute("revenue_forecast", pd.DataFrame(rows), PINNED)
    assert result.status == STATUS_UNAVAILABLE
    assert "too gappy" in result.reason
    assert "3 of 9" in result.reason


def test_minimums_are_configurable():
    original = settings.forecast_tier1_min_periods
    settings.forecast_tier1_min_periods = 3
    try:
        result = engine.compute("revenue_forecast", pd.DataFrame(_month_rows([100, 110, 90])), PINNED)
        assert result.status == STATUS_OK
    finally:
        settings.forecast_tier1_min_periods = original


def test_tier1_used_when_history_is_short_of_the_tier2_minimum(flat_series):
    result = engine.compute("revenue_forecast", flat_series, PINNED)
    assert "Tier 1" in result.method
    assert "Tier 2" not in result.method


@pytest.mark.skipif(_statsmodels_available(), reason="statsmodels IS installed here")
def test_missing_statsmodels_degrades_to_tier1_with_a_note(rising_series):
    """Tier 2 is optional; its absence must be reported, not hidden."""
    result = engine.compute("revenue_forecast", rising_series, PINNED)
    assert result.status == STATUS_OK
    assert "Tier 1" in result.method
    assert any("statsmodels is not installed" in n for n in result.provenance.assumptions)


@pytest.mark.skipif(not _statsmodels_available(), reason="statsmodels not installed")
def test_tier2_used_when_history_is_long_enough(rising_series):
    result = engine.compute("revenue_forecast", rising_series, PINNED)
    assert "Tier 2" in result.method
    assert result.forecast[0]["upper"] > result.forecast[0]["lower"]


def test_tier2_failure_falls_back_to_tier1_with_a_note(monkeypatch, rising_series):
    """A model that raises must degrade to the baseline, never to a crash."""
    import app.analytics.forecast as fc

    monkeypatch.setattr(fc, "_statsmodels_available", lambda: True)
    monkeypatch.setattr(
        fc, "_tier2_smoothing", lambda s, h: (_ for _ in ()).throw(ValueError("fit blew up"))
    )
    series = build_series(rising_series, PINNED, "revenue")
    outcome = fc.make_forecast(series, 3)

    assert outcome.ok
    assert outcome.tier == 1
    assert any("fit blew up" in n and "fell back" in n for n in outcome.notes)


def test_tier2_can_be_disabled_by_config(rising_series):
    original = settings.forecast_enable_tier2
    settings.forecast_enable_tier2 = False
    try:
        result = engine.compute("revenue_forecast", rising_series, PINNED)
        assert "Tier 1" in result.method
        assert not any("statsmodels" in n for n in result.provenance.assumptions)
    finally:
        settings.forecast_enable_tier2 = original


# ---------------------------------------------------------------------------
# Per-product demand
# ---------------------------------------------------------------------------


def test_product_demand_forecast_requires_a_product(flat_series):
    result = engine.compute("product_demand_forecast", flat_series, PINNED)
    assert result.status == STATUS_UNAVAILABLE
    assert "no product was specified" in result.reason


def test_product_demand_forecast_uses_that_products_own_history():
    """Panadol has 6 months; Zinc has 2. Only Panadol can be forecast."""
    rows = _month_rows([100] * 6, product="Panadol") + _month_rows(
        [50, 50], product="Zinc", start="2025-01-01", first_row=50
    )
    df = pd.DataFrame(rows)

    good = engine.compute(
        "product_demand_forecast", df, KPIFilters(as_of=AS_OF, product_id="Panadol")
    )
    assert good.status == STATUS_OK
    assert good.value == 10.0  # 100 revenue -> 10 units per month
    assert good.forecast[0]["period"] == "2025-07"


def test_sparse_product_returns_insufficient_data_naming_the_product():
    rows = _month_rows([100] * 6, product="Panadol") + _month_rows(
        [50, 50], product="Zinc", start="2025-01-01", first_row=50
    )
    result = engine.compute(
        "product_demand_forecast", pd.DataFrame(rows), KPIFilters(as_of=AS_OF, product_id="Zinc")
    )
    assert result.status == STATUS_UNAVAILABLE
    assert result.value is None
    assert result.reason.startswith("'Zinc' cannot be forecast")
    assert "at least 4 needed" in result.reason


# ---------------------------------------------------------------------------
# Movers
# ---------------------------------------------------------------------------


def test_top_rising_and_declining_identify_the_right_products():
    """Riser doubles month over month; Faller halves; Steady stays put."""
    rows = (
        _month_rows([100, 100, 100, 200], product="Riser", first_row=2)
        + _month_rows([100, 100, 100, 50], product="Faller", first_row=20)
        + _month_rows([100, 100, 100, 100], product="Steady", first_row=40)
    )
    df = pd.DataFrame(rows)

    rising = engine.compute("top_rising_products", df, PINNED)
    assert rising.status == STATUS_OK
    assert [r["product_id"] for r in rising.breakdown] == ["Riser"]
    assert rising.breakdown[0] == {
        "product_id": "Riser", "recent": 200.0, "prior": 100.0,
        "change": 100.0, "change_pct": 100.0,
    }

    declining = engine.compute("top_declining_products", df, PINNED)
    assert [r["product_id"] for r in declining.breakdown] == ["Faller"]
    assert declining.breakdown[0]["change"] == -50.0
    assert declining.breakdown[0]["change_pct"] == -50.0


def test_movers_need_two_windows():
    result = engine.compute("top_rising_products", pd.DataFrame(_month_rows([100])), PINNED)
    assert result.status == STATUS_UNAVAILABLE
    assert "at least 2" in result.reason


# ---------------------------------------------------------------------------
# Provenance, serialization, determinism
# ---------------------------------------------------------------------------


def test_provenance_and_period_range(flat_series):
    result = engine.compute("revenue_forecast", flat_series, PINNED)
    assert result.provenance.row_count == 6
    assert result.provenance.source_rows == [2, 3, 4, 5, 6, 7]
    assert result.period.start == "2025-01"
    assert result.period.end == "2025-06"
    assert result.provenance.filters["as_of"] == AS_OF


def test_result_is_json_serializable(flat_series):
    result = engine.compute("revenue_forecast", flat_series, PINNED)
    restored = json.loads(json.dumps(result.to_dict()))
    assert restored["forecast"][0]["upper"] > restored["forecast"][0]["lower"]
    assert restored["series"][0]["period"] == "2025-01"
    assert "Tier 1" in restored["method"]


def test_determinism(flat_series):
    first = engine.compute("revenue_forecast", flat_series, PINNED).to_dict()
    second = engine.compute("revenue_forecast", flat_series.copy(), PINNED).to_dict()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_row_order_does_not_change_results(flat_series):
    shuffled = flat_series.iloc[::-1].reset_index(drop=True)
    for key in ("revenue_trend", "revenue_forecast", "units_trend"):
        a, b = engine.compute(key, flat_series, PINNED), engine.compute(key, shuffled, PINNED)
        assert a.value == b.value, key
        assert a.series == b.series, key
        assert a.forecast == b.forecast, key


def test_no_heavy_forecasting_dependencies():
    """
    Prophet and ML frameworks are explicitly out of scope.

    Checks IMPORT statements rather than any mention of the name, so the module
    docstring can still say "no Prophet" without defeating its own test.
    """
    import re as _re

    for module in ("app/analytics/forecast.py", "app/analytics/timeseries.py"):
        path = os.path.join(os.path.dirname(__file__), "..", module)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        imported = set(_re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, _re.MULTILINE))
        roots = {name.split(".")[0].casefold() for name in imported}
        for banned in ("prophet", "sklearn", "torch", "tensorflow", "keras", "ollama"):
            assert banned not in roots, f"{module} imports {banned}"
        assert "app.core.llm" not in imported, f"{module} imports the LLM client"


def test_missing_date_or_metric_columns_are_unavailable_not_crashes():
    no_date = pd.DataFrame({"source_row": [2], "txn_type": ["sale"], "amount": [10.0]})
    result = engine.compute("revenue_forecast", no_date, PINNED)
    assert result.status == STATUS_UNAVAILABLE
    assert "date" in result.reason

    no_qty = pd.DataFrame(
        {"source_row": [2], "txn_type": ["sale"], "amount": [10.0], "date": ["2025-01-01"]}
    )
    result = engine.compute("demand_forecast", no_qty, PINNED)
    assert result.status == STATUS_UNAVAILABLE
    assert "quantity" in result.reason


# ---------------------------------------------------------------------------
# Chatbot wiring
# ---------------------------------------------------------------------------


def test_forecast_and_trend_questions_route_correctly():
    assert select_kpi_keys("what is my revenue forecast?")[0] == "revenue_forecast"
    assert select_kpi_keys("sales next month")[0] == "revenue_forecast"
    assert select_kpi_keys("show me the revenue trend")[0] == "revenue_trend"
    assert select_kpi_keys("which products are rising")[0] == "top_rising_products"
    assert select_kpi_keys("which products are declining")[0] == "top_declining_products"
    assert select_kpi_keys("how many will i sell")[0] == "product_demand_forecast"
    assert select_kpi_keys("kitni sale hogi")[0] == "product_demand_forecast"


def test_trend_intents_do_not_hijack_plain_totals():
    assert select_kpi_keys("what were my total sales?") == ["total_revenue", "transaction_count"]


def test_pharmacy_demand_vocabulary_routes_to_per_product():
    assert select_kpi_keys("kitna mangwana chahiye", "pharmacy")[0] == "product_demand_forecast"
    assert select_kpi_keys("what is the demand", "pharmacy")[0] == "product_demand_forecast"
    # ...and the expiry vocabulary still wins for expiry questions.
    assert select_kpi_keys("what is expiring soon", "pharmacy")[0] == "near_expiry_total"


def test_infer_product_id_from_question():
    df = pd.DataFrame({"product_id": ["Panadol 500mg Tab", "Augmentin 625mg Tab", "Zinc"]})
    assert infer_product_id("how many units of Panadol will i sell?", df) == "Panadol 500mg Tab"
    assert infer_product_id("augmentin next month", df) == "Augmentin 625mg Tab"
    assert infer_product_id("how many will i sell", df) is None      # nothing named
    assert infer_product_id("500mg tab", df) is None                 # too generic to match


def test_seam_returns_forecast_with_uncertainty_band():
    rows = _month_rows([100] * 6, product="Panadol")
    computed, source_rows = AnalyticsRouter().compute(
        "how many units of Panadol will i sell next month?", {"as_of": AS_OF}, rows
    )
    entry = computed["product_demand_forecast"]
    assert entry["value"] == 10.0
    assert entry["is_estimate"] is True
    assert entry["estimate_range"]["lower"] <= entry["value"] <= entry["estimate_range"]["upper"]
    assert "Tier 1" in entry["method"]
    assert source_rows == [2, 3, 4, 5, 6, 7]
    json.dumps(computed)


def test_seam_passes_through_the_refusal_so_the_llm_cannot_invent():
    rows = _month_rows([100, 100])  # only two months
    computed, _ = AnalyticsRouter().compute("revenue forecast", {"as_of": AS_OF}, rows)
    entry = computed["revenue_forecast"]
    assert entry["status"] == STATUS_UNAVAILABLE
    assert entry["value"] is None
    assert "at least 4 needed" in entry["reason"]


def test_chatbot_forecast_number_comes_from_code_with_uncertainty():
    """End to end through RAGChat with a mocked LLM."""
    from unittest.mock import patch

    from app.ingestion.models import RetrievedChunk
    from app.rag.models import ChatRequest

    rows = _month_rows([100] * 6, product="Panadol")
    for r in rows:
        r["source_file"] = "sales.csv"
    chunks = [
        RetrievedChunk(text="sale", metadata=m, score=0.9, source_row=m["source_row"])
        for m in rows
    ]

    with patch("app.rag.chat.KnowledgeBase") as kb, patch("app.rag.chat.llm") as llm, patch(
        "app.rag.chat.session_manager"
    ), patch("app.rag.chat.extract_filters", return_value={"as_of": AS_OF}):
        kb.return_value.search.return_value = chunks
        llm.chat.return_value = "Roughly 10 units, likely between 0 and 22."

        from app.rag.chat import RAGChat

        response = RAGChat().ask(
            ChatRequest(question="how many units of Panadol will i sell next month?", session_id="t")
        )

    assert response.route == "analytics"
    entry = response.computed_values["product_demand_forecast"]
    assert entry["value"] == 10.0
    assert entry["is_estimate"] is True

    # The band reached the prompt, and the system prompt demands it be conveyed.
    messages = llm.chat.call_args.kwargs["messages"]
    assert "estimate_range" in messages[-1]["content"]
    assert "FORECAST, not a measured fact" in messages[0]["content"]
    assert "likely between" in messages[0]["content"]


# ---------------------------------------------------------------------------
# Real sample fixture
# ---------------------------------------------------------------------------


def _canonical_sales() -> pd.DataFrame:
    from app.connectors.base import detect_connector
    from app.schema.domain import get_domain_pack
    from app.schema.mapper import map_headers
    from app.schema.normalize import apply_mapping

    path = os.path.abspath(os.path.join(SAMPLES_DIR, "challenging_pharma_sales.csv"))
    raw = detect_connector(path).fetch()
    mapping = map_headers(list(raw.columns), get_domain_pack("pharmacy"))
    return apply_mapping(raw, mapping, domain="pharmacy", keep_extras=False)


def test_real_fixture_monthly_history_is_correctly_refused():
    """
    The shipped sales fixture has Jan, Feb and September 2026 only — a six-month
    hole. Monthly forecasting must refuse rather than average the zero-filled gap.
    """
    result = engine.compute(
        "revenue_forecast", _canonical_sales(), KPIFilters(as_of="2026-09-30"), domain="pharmacy"
    )
    assert result.status == STATUS_UNAVAILABLE
    assert "too gappy" in result.reason
    assert "3 of 9" in result.reason


def test_real_fixture_weekly_history_is_dense_enough_to_forecast():
    """The same data at weekly granularity over Jan-Feb is contiguous and usable."""
    filters = KPIFilters(as_of="2026-02-28", options={"granularity": "weekly"})
    result = engine.compute("revenue_forecast", _canonical_sales(), filters, domain="pharmacy")
    assert result.status == STATUS_OK
    assert result.value > 0
    assert result.forecast[0]["upper"] > result.forecast[0]["lower"]
    assert len(result.series) >= settings.forecast_tier1_min_periods
    json.dumps(result.to_dict())


def test_real_fixture_trend_is_always_available():
    """Tier 0 describes the past, so it works even where forecasting refuses."""
    result = engine.compute(
        "revenue_trend", _canonical_sales(), KPIFilters(as_of="2026-09-30"), domain="pharmacy"
    )
    assert result.series
    assert result.provenance.row_count > 0
