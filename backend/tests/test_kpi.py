"""
Module 6.6 (KPI Engine) — tests.

Exact-value assertions on hand-built frames whose totals are known by construction,
plus the real `data/samples/` pharmacy fixtures. Fully offline: no LLM, no network.
"""

import json
import os

import pandas as pd
import pytest

from app.analytics.engine import KPIEngine, KPISpec, engine
from app.analytics.filters import KPIFilters, apply_filters
from app.analytics.models import STATUS_OK, STATUS_UNAVAILABLE, KPIResult, Provenance
from app.analytics.seam import AnalyticsRouter, select_kpi_keys

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples")


# ---------------------------------------------------------------------------
# Fixtures — every total below is arithmetic you can check by hand.
# ---------------------------------------------------------------------------


@pytest.fixture
def known_frame() -> pd.DataFrame:
    """
    Hand-built canonical frame with known answers:

        sales     : 1000 + 500 + 2500           = 4000
        expenses  : 300 + 200                   =  500
        refunds   : 250                         =  250
        net profit: 4000 - 500 - 250            = 3250
        COGS      : (60*10) + (40*5) + (200*10) = 2800  [cost x quantity, sale rows]
        gross     : 4000 - 2800                 = 1200
        invoices  : INV-1, INV-2, INV-3         =    3
    """
    return pd.DataFrame(
        [
            # source_row, txn_type, amount, cost, quantity, category, product_id, supplier_id, invoice_id, date
            (2, "sale", 1000.0, 60.0, 10, "Painkillers", "P-100", None, "INV-1", "2026-01-05"),
            (3, "sale", 500.0, 40.0, 5, "Vitamins", "P-200", None, "INV-2", "2026-01-20"),
            (4, "sale", 2500.0, 200.0, 10, "Painkillers", "P-100", None, "INV-3", "2026-02-11"),
            (5, "expense", 300.0, None, None, "Rent", None, "SUP-A", None, "2026-01-31"),
            (6, "purchase", 200.0, None, None, "Stock", None, "SUP-B", None, "2026-02-01"),
            (7, "refund", 250.0, None, None, "Painkillers", "P-100", None, "INV-1", "2026-02-15"),
        ],
        columns=[
            "source_row", "txn_type", "amount", "cost", "quantity",
            "category", "product_id", "supplier_id", "invoice_id", "date",
        ],
    ).assign(source_connector="csv")


@pytest.fixture
def no_cost_frame() -> pd.DataFrame:
    """Sales with no `cost` column at all — gross margin must be unavailable."""
    return pd.DataFrame(
        {
            "source_row": [2, 3],
            "txn_type": ["sale", "sale"],
            "amount": [100.0, 400.0],
            "date": ["2026-03-01", "2026-03-02"],
            "source_connector": ["csv", "csv"],
        }
    )


# ---------------------------------------------------------------------------
# Core money KPIs — exact values
# ---------------------------------------------------------------------------


def test_total_revenue_exact(known_frame):
    result = engine.compute("total_revenue", known_frame)
    assert result.status == STATUS_OK
    assert result.value == 4000.0
    assert result.unit == "PKR"
    assert result.provenance.row_count == 3


def test_total_expenses_exact(known_frame):
    """Both 'expense' and 'purchase' labels count as expenses."""
    result = engine.compute("total_expenses", known_frame)
    assert result.status == STATUS_OK
    assert result.value == 500.0
    assert result.provenance.row_count == 2


def test_total_refunds_exact(known_frame):
    result = engine.compute("total_refunds", known_frame)
    assert result.status == STATUS_OK
    assert result.value == 250.0
    assert result.provenance.row_count == 1


def test_refund_row_is_excluded_from_revenue(known_frame):
    """A refund must never be counted as a sale, or net profit double-counts it."""
    revenue = engine.compute("total_revenue", known_frame)
    assert 7 not in revenue.provenance.source_rows


def test_net_profit_exact(known_frame):
    result = engine.compute("net_profit", known_frame)
    assert result.status == STATUS_OK
    assert result.value == 3250.0  # 4000 - 500 - 250


def test_gross_profit_and_margin_exact(known_frame):
    gross = engine.compute("gross_profit", known_frame)
    assert gross.status == STATUS_OK
    assert gross.value == 1200.0  # 4000 revenue - 2800 COGS

    margin = engine.compute("gross_margin_pct", known_frame)
    assert margin.status == STATUS_OK
    assert margin.value == 30.0  # 1200 / 4000 * 100
    assert margin.unit == "percent"


def test_net_margin_and_refund_rate_exact(known_frame):
    net_margin = engine.compute("net_margin_pct", known_frame)
    assert net_margin.value == 81.25  # 3250 / 4000 * 100

    refund_rate = engine.compute("refund_rate_pct", known_frame)
    assert refund_rate.value == 6.25  # 250 / 4000 * 100


def test_transaction_count_and_average_bill_exact(known_frame):
    count = engine.compute("transaction_count", known_frame)
    assert count.status == STATUS_OK
    assert count.value == 3.0  # distinct invoice ids on sale rows

    avg = engine.compute("average_transaction_value", known_frame)
    assert avg.status == STATUS_OK
    assert avg.value == pytest.approx(1333.33)  # 4000 / 3


def test_transaction_count_dedupes_multi_line_invoice():
    """Two lines of one invoice are ONE transaction."""
    df = pd.DataFrame(
        {
            "source_row": [2, 3],
            "txn_type": ["sale", "sale"],
            "invoice_id": ["INV-9", "INV-9"],
            "amount": [100.0, 300.0],
        }
    )
    assert engine.compute("transaction_count", df).value == 1.0
    assert engine.compute("average_transaction_value", df).value == 400.0


# ---------------------------------------------------------------------------
# Margin unavailability when cost is absent
# ---------------------------------------------------------------------------


def test_gross_margin_unavailable_when_cost_absent(no_cost_frame):
    """No cost data must produce unavailable + reason, never a guessed number or 0."""
    gross = engine.compute("gross_profit", no_cost_frame)
    assert gross.status == STATUS_UNAVAILABLE
    assert gross.value is None
    assert "cost" in gross.reason.lower()

    margin = engine.compute("gross_margin_pct", no_cost_frame)
    assert margin.status == STATUS_UNAVAILABLE
    assert margin.value is None
    assert margin.reason


def test_expenses_unavailable_when_txn_type_absent(no_cost_frame):
    """Without txn_type we cannot tell expenses from sales — say so, don't return 0."""
    df = no_cost_frame.drop(columns=["txn_type"])
    expenses = engine.compute("total_expenses", df)
    assert expenses.status == STATUS_UNAVAILABLE
    assert expenses.value is None
    assert "txn_type" in expenses.reason

    # Revenue is still computable, with the assumption recorded.
    revenue = engine.compute("total_revenue", df)
    assert revenue.value == 500.0
    assert any("txn_type" in note for note in revenue.provenance.assumptions)


def test_refunds_zero_is_distinguishable_from_unavailable():
    """txn_type present but no refund rows -> a genuine, auditable 0.0."""
    df = pd.DataFrame({"source_row": [2], "txn_type": ["sale"], "amount": [100.0]})
    refunds = engine.compute("total_refunds", df)
    assert refunds.status == STATUS_OK
    assert refunds.value == 0.0
    assert refunds.provenance.row_count == 0


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------


def test_expense_breakdown_by_category_exact(known_frame):
    result = engine.compute("expense_breakdown_by_category", known_frame)
    assert result.status == STATUS_OK
    assert result.value == 500.0
    assert result.breakdown == [
        {"category": "Rent", "amount": 300.0, "row_count": 1},
        {"category": "Stock", "amount": 200.0, "row_count": 1},
    ]


def test_expense_breakdown_by_supplier_exact(known_frame):
    result = engine.compute("expense_breakdown_by_supplier", known_frame)
    assert result.breakdown == [
        {"supplier_id": "SUP-A", "amount": 300.0, "row_count": 1},
        {"supplier_id": "SUP-B", "amount": 200.0, "row_count": 1},
    ]


def test_revenue_breakdown_by_product_exact(known_frame):
    """P-100 sold twice (1000 + 2500); the refund row must not be netted in here."""
    result = engine.compute("revenue_breakdown_by_product", known_frame)
    assert result.status == STATUS_OK
    assert result.breakdown == [
        {"product_id": "P-100", "amount": 3500.0, "row_count": 2},
        {"product_id": "P-200", "amount": 500.0, "row_count": 1},
    ]


def test_revenue_breakdown_by_category_exact(known_frame):
    result = engine.compute("revenue_breakdown_by_category", known_frame)
    assert result.breakdown == [
        {"category": "Painkillers", "amount": 3500.0, "row_count": 2},
        {"category": "Vitamins", "amount": 500.0, "row_count": 1},
    ]


def test_revenue_by_month_exact_and_ascending(known_frame):
    result = engine.compute("revenue_by_month", known_frame)
    assert result.status == STATUS_OK
    assert result.breakdown == [
        {"month": "2026-01", "amount": 1500.0, "row_count": 2},
        {"month": "2026-02", "amount": 2500.0, "row_count": 1},
    ]
    months = [row["month"] for row in result.breakdown]
    assert months == sorted(months)


def test_breakdown_top_n_truncates_but_total_stays_complete():
    """`value` is the total over ALL groups even when the table shows only top-N."""
    df = pd.DataFrame(
        {
            "source_row": range(2, 7),
            "txn_type": ["sale"] * 5,
            "product_id": [f"P-{i}" for i in range(5)],
            "amount": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )
    small_engine = KPIEngine()
    from app.core.config import settings

    original = settings.analytics_top_n
    settings.analytics_top_n = 2
    try:
        result = small_engine.compute("revenue_breakdown_by_product", df)
        assert len(result.breakdown) == 2
        assert result.value == 150.0  # total of all five groups
        assert any("top 2" in note for note in result.provenance.assumptions)
    finally:
        settings.analytics_top_n = original


def test_breakdown_ordering_is_deterministic_on_ties():
    """Equal amounts fall back to group label ascending, so output never wobbles."""
    df = pd.DataFrame(
        {
            "source_row": [2, 3, 4],
            "txn_type": ["sale"] * 3,
            "category": ["Zinc", "Aspirin", "Multivit"],
            "amount": [100.0, 100.0, 100.0],
        }
    )
    result = engine.compute("revenue_breakdown_by_category", df)
    assert [row["category"] for row in result.breakdown] == ["Aspirin", "Multivit", "Zinc"]


# ---------------------------------------------------------------------------
# Robustness — zero division, empty frames, missing columns
# ---------------------------------------------------------------------------


def test_zero_revenue_does_not_leak_nan_or_inf():
    """Revenue 0 must yield unavailable + reason for every ratio — never NaN or inf."""
    df = pd.DataFrame(
        {
            "source_row": [2, 3, 4],
            "txn_type": ["sale", "refund", "expense"],
            "amount": [0.0, 50.0, 0.0],
        }
    )
    revenue = engine.compute("total_revenue", df)
    assert revenue.value == 0.0
    assert engine.compute("net_profit", df).value == -50.0

    for key in ("net_margin_pct", "refund_rate_pct"):
        result = engine.compute(key, df)
        assert result.status == STATUS_UNAVAILABLE
        assert result.value is None
        assert "zero" in result.reason.lower()
        # And nothing NaN/inf survives serialization.
        assert json.loads(json.dumps(result.to_dict()))["value"] is None


def test_empty_frame_returns_unavailable_for_every_kpi():
    results = engine.compute_all(pd.DataFrame())
    assert results
    for key, result in results.items():
        assert result.status == STATUS_UNAVAILABLE, key
        assert result.value is None, key
        assert result.reason, key


def test_filter_matching_nothing_is_unavailable_not_zero(known_frame):
    results = engine.compute_all(known_frame, KPIFilters(year=1999))
    for key, result in results.items():
        assert result.status == STATUS_UNAVAILABLE, key
        assert "no rows match" in result.reason


def test_missing_columns_never_crash():
    """A frame with only an amount column must produce results, not exceptions."""
    df = pd.DataFrame({"amount": [10.0, 20.0]})
    results = engine.compute_all(df)
    assert results["total_revenue"].value == 30.0
    assert results["expense_breakdown_by_category"].status == STATUS_UNAVAILABLE
    assert results["revenue_by_month"].status == STATUS_UNAVAILABLE


def test_single_row_frame(known_frame):
    result = engine.compute("total_revenue", known_frame.head(1))
    assert result.value == 1000.0
    assert result.provenance.source_rows == [2]


def test_unparseable_amounts_are_excluded_not_crashing():
    df = pd.DataFrame(
        {"source_row": [2, 3, 4], "txn_type": ["sale"] * 3, "amount": [100.0, "abc", 50.0]}
    )
    result = engine.compute("total_revenue", df)
    assert result.value == 150.0
    assert result.provenance.source_rows == [2, 4]


def test_amount_derived_when_column_absent():
    df = pd.DataFrame(
        {"source_row": [2], "txn_type": ["sale"], "unit_price": [30.0], "quantity": [10]}
    )
    result = engine.compute("total_revenue", df)
    assert result.value == 300.0
    assert any("derived" in note for note in result.provenance.assumptions)


def test_unknown_kpi_key_raises():
    with pytest.raises(KeyError):
        engine.compute("no_such_kpi", pd.DataFrame({"amount": [1.0]}))


# ---------------------------------------------------------------------------
# Provenance — the referenced rows must be the rows that actually contributed
# ---------------------------------------------------------------------------


def test_provenance_rows_match_contributing_rows(known_frame):
    """Recompute the figure from ONLY the referenced source_rows; it must match."""
    revenue = engine.compute("total_revenue", known_frame)
    assert revenue.provenance.source_rows == [2, 3, 4]

    referenced = known_frame[known_frame["source_row"].isin(revenue.provenance.source_rows)]
    assert referenced["amount"].sum() == revenue.value
    assert set(referenced["txn_type"]) == {"sale"}

    expenses = engine.compute("total_expenses", known_frame)
    assert expenses.provenance.source_rows == [5, 6]
    assert known_frame[known_frame["source_row"].isin([5, 6])]["amount"].sum() == expenses.value


def test_provenance_records_filter_and_sources(known_frame):
    result = engine.compute("total_revenue", known_frame, KPIFilters(month=1))
    assert result.value == 1500.0  # only the two January sales
    assert result.provenance.source_rows == [2, 3]
    assert result.provenance.filters == {"month": 1}
    assert result.provenance.filter_description == "month = 1"
    assert result.provenance.sources == ["csv"]
    assert "amount" in result.provenance.columns_used


def test_provenance_of_breakdown_rows(known_frame):
    result = engine.compute("revenue_breakdown_by_product", known_frame)
    referenced = known_frame[known_frame["source_row"].isin(result.provenance.source_rows)]
    assert referenced["amount"].sum() == result.value


def test_unavailable_filter_note_when_column_absent():
    """A filter on a column that does not exist is reported, not silently ignored."""
    df = pd.DataFrame({"source_row": [2], "txn_type": ["sale"], "amount": [10.0]})
    result = engine.compute("total_revenue", df, KPIFilters(supplier_id="SUP-A"))
    assert any("supplier_id" in note for note in result.provenance.assumptions)


def test_result_is_json_serializable(known_frame):
    result = engine.compute("revenue_by_month", known_frame)
    restored = json.loads(json.dumps(result.to_dict()))
    assert restored["key"] == "revenue_by_month"
    assert restored["provenance"]["row_count"] == 3
    assert restored["period"]["start"] == "2026-01-05"
    assert restored["period"]["end"] == "2026-02-11"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_compute_all_is_deterministic(known_frame):
    first = {k: v.to_dict() for k, v in engine.compute_all(known_frame).items()}
    second = {k: v.to_dict() for k, v in engine.compute_all(known_frame.copy()).items()}
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert list(first.keys()) == list(second.keys())


def test_row_order_does_not_change_results(known_frame):
    shuffled = known_frame.iloc[::-1].reset_index(drop=True)
    original = engine.compute_all(known_frame)
    reversed_run = engine.compute_all(shuffled)
    for key in original:
        assert original[key].value == reversed_run[key].value, key
        assert original[key].provenance.source_rows == reversed_run[key].provenance.source_rows, key


# ---------------------------------------------------------------------------
# Registry — domain KPIs attach additively
# ---------------------------------------------------------------------------


def test_register_domain_kpi_without_touching_core(known_frame):
    """The hook the pharmacy pack (and grocery, later) will use."""
    local = KPIEngine()

    def _row_count(df, filters, domain=""):
        return KPIResult(
            key="demo_row_count", name="Demo Row Count", value=float(len(df)),
            unit="count", formula="len(df)", provenance=Provenance(row_count=len(df)),
        )

    local.register(
        KPISpec("demo_row_count", "Demo Row Count", "count", "Rows in the slice.",
                _row_count, domain="demo_domain")
    )

    assert "demo_row_count" not in {s.key for s in local.list_kpis()}
    assert "demo_row_count" in {s.key for s in local.list_kpis("demo_domain")}
    assert local.compute_all(known_frame, domain="demo_domain")["demo_row_count"].value == 6.0
    # Core engine is untouched by a domain registration on another instance.
    assert not engine.has("demo_row_count")


def test_duplicate_registration_rejected_unless_replace():
    local = KPIEngine()
    spec = KPISpec("total_revenue", "X", "PKR", "d", lambda d, f, dom="": None)
    with pytest.raises(ValueError):
        local.register(spec)
    local.register(spec, replace=True)


def test_list_kpis_covers_the_core_pack():
    keys = {spec.key for spec in engine.list_kpis()}
    for expected in (
        "total_revenue", "total_expenses", "total_refunds", "net_profit",
        "gross_profit", "gross_margin_pct", "net_margin_pct", "refund_rate_pct",
        "transaction_count", "average_transaction_value",
        "expense_breakdown_by_category", "expense_breakdown_by_supplier",
        "revenue_breakdown_by_category", "revenue_breakdown_by_product", "revenue_by_month",
    ):
        assert expected in keys


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_apply_filters_date_range_is_inclusive(known_frame):
    sliced, notes = apply_filters(known_frame, KPIFilters(date_from="2026-01-05", date_to="2026-01-20"))
    assert sorted(sliced["source_row"]) == [2, 3]
    assert notes == []


def test_apply_filters_exact_match_is_case_insensitive(known_frame):
    sliced, _ = apply_filters(known_frame, KPIFilters(category="painkillers"))
    assert sorted(sliced["source_row"]) == [2, 4, 7]


def test_filters_from_dict_ignores_unknown_keys():
    filters = KPIFilters.from_dict({"month": 3, "not_a_filter": "x", "category": None})
    assert filters.month == 3
    assert filters.as_dict() == {"month": 3}


# ---------------------------------------------------------------------------
# The Module 6.5 chatbot seam
# ---------------------------------------------------------------------------


def test_seam_returns_computed_number_with_provenance(known_frame):
    """The numeric chat route gets a real engine figure, not an ad-hoc aggregate."""
    records = known_frame.assign(source_file="sample_pharmacy.csv").to_dict(orient="records")
    computed, source_rows = AnalyticsRouter().compute("What were my total sales?", {}, records)

    assert computed is not None
    assert computed["total_revenue"]["value"] == 4000.0
    assert computed["total_revenue"]["unit"] == "PKR"
    assert computed["total_revenue"]["status"] == STATUS_OK
    assert computed["total_revenue"]["provenance"]["rows_used"] == 3
    assert computed["total_revenue"]["provenance"]["sources"] == ["sample_pharmacy.csv"]
    assert source_rows == [2, 3, 4]
    # chat.py filters retrieved chunks by this list, so it must be plain ints.
    assert all(isinstance(r, int) for r in source_rows)


def test_seam_payload_is_json_serializable(known_frame):
    """chat.py json.dumps() the payload straight into the prompt."""
    records = known_frame.to_dict(orient="records")
    computed, _ = AnalyticsRouter().compute("total profit?", {}, records)
    assert json.loads(json.dumps(computed))["net_profit"]["value"] == 3250.0


def test_seam_applies_router_filters(known_frame):
    """Filters extracted from the question (e.g. month) reach the engine."""
    records = known_frame.to_dict(orient="records")
    computed, source_rows = AnalyticsRouter().compute("total sales in January?", {"month": 1}, records)
    assert computed["total_revenue"]["value"] == 1500.0
    assert source_rows == [2, 3]


def test_seam_intent_selection_is_deterministic():
    assert select_kpi_keys("what is my profit margin?")[0] == "gross_margin_pct"
    assert select_kpi_keys("how many transactions") == ["transaction_count"]
    assert select_kpi_keys("total refunds this month")[0] == "total_refunds"
    assert select_kpi_keys("kitna kharcha hua") == ["total_expenses", "expense_breakdown_by_category"]
    for _ in range(3):
        assert select_kpi_keys("total sales") == ["total_revenue", "transaction_count"]


def test_seam_returns_none_for_no_records():
    """Preserves the 6.5 contract: no records -> chatbot says it found nothing."""
    assert AnalyticsRouter().compute("total sales", {}, []) == (None, [])


def test_seam_reports_unavailable_instead_of_inventing(no_cost_frame):
    """The chatbot must be told a figure is uncomputable, so it cannot make one up."""
    records = no_cost_frame.drop(columns=["txn_type"]).to_dict(orient="records")
    computed, _ = AnalyticsRouter().compute("what were my expenses?", {}, records)
    assert computed["total_expenses"]["status"] == STATUS_UNAVAILABLE
    assert computed["total_expenses"]["value"] is None
    assert computed["total_expenses"]["reason"]


def test_rag_router_reexports_the_engine_backed_seam():
    """app/rag/chat.py imports AnalyticsRouter from app.rag.router — it must be ours."""
    from app.rag.router import AnalyticsRouter as RouterSeam

    assert RouterSeam is AnalyticsRouter


def test_analytics_package_never_imports_the_llm():
    """A reviewer should be able to confirm this by grep; assert it too."""
    analytics_dir = os.path.join(os.path.dirname(__file__), "..", "app", "analytics")
    for filename in sorted(os.listdir(analytics_dir)):
        if not filename.endswith(".py"):
            continue
        with open(os.path.join(analytics_dir, filename), encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("app.core.llm", "import ollama", "from ollama"):
            assert forbidden not in source, f"{filename} references {forbidden}"


# ---------------------------------------------------------------------------
# End-to-end over the real sample pharmacy fixtures
# ---------------------------------------------------------------------------


def _canonical_from_sample(filename: str) -> pd.DataFrame:
    from app.connectors.base import detect_connector
    from app.schema.domain import get_domain_pack
    from app.schema.mapper import map_headers
    from app.schema.normalize import apply_mapping
    import app.schema  # noqa: F401  (registers domain packs)

    path = os.path.abspath(os.path.join(SAMPLES_DIR, filename))
    raw = detect_connector(path).fetch()
    mapping = map_headers(list(raw.columns), get_domain_pack("pharmacy"))
    return apply_mapping(raw, mapping, domain="pharmacy", keep_extras=False)


@pytest.mark.parametrize("filename", ["sample_pharmacy.csv", "challenging_pharma_sales.csv"])
def test_kpi_pack_runs_on_real_sample_fixtures(filename):
    """The pack must survive real, messy pharmacy exports without raising."""
    df = _canonical_from_sample(filename)
    results = engine.compute_all(df, domain="pharmacy")

    revenue = results["total_revenue"]
    assert revenue.status == STATUS_OK
    assert revenue.value > 0
    assert revenue.provenance.row_count > 0
    # These fixtures carry no txn_type, so the engine must say so rather than guess.
    assert results["total_expenses"].status == STATUS_UNAVAILABLE
    assert any("txn_type" in note for note in revenue.provenance.assumptions)
    # And the whole pack must serialize for the API / report generator.
    json.dumps({k: v.to_dict() for k, v in results.items()})


def test_sample_pharmacy_revenue_matches_hand_sum():
    """Cross-check the engine against a plain pandas sum of the canonical frame."""
    df = _canonical_from_sample("sample_pharmacy.csv")
    expected = round(float(pd.to_numeric(df["amount"], errors="coerce").sum()), 2)
    assert engine.compute("total_revenue", df).value == expected


def test_sample_pharmacy_gross_profit_is_costed():
    """`cost` is a unit price in these fixtures, so COGS must be cost x quantity."""
    df = _canonical_from_sample("sample_pharmacy.csv")
    amounts = pd.to_numeric(df["amount"], errors="coerce")
    cogs = pd.to_numeric(df["cost"], errors="coerce") * pd.to_numeric(df["quantity"], errors="coerce")
    usable = amounts.notna() & cogs.notna()
    expected = round(float(amounts[usable].sum() - cogs[usable].sum()), 2)
    assert engine.compute("gross_profit", df).value == expected
