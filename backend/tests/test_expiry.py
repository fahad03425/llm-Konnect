"""
Pharmacy expiry analytics — tests.

Every assertion below uses a PINNED reference date (2026-01-01) so results are
reproducible and the "today" behaviour is genuinely injected rather than read from
the clock. Exact values are arithmetic you can check by hand. Offline: no LLM,
no network.
"""

import json
import os

import pandas as pd
import pytest

from app.analytics.engine import KPIEngine, engine
from app.analytics.filters import KPIFilters
from app.analytics.models import STATUS_OK, STATUS_UNAVAILABLE
from app.analytics.seam import AnalyticsRouter, select_kpi_keys
from app.core.config import settings
import app.schema  # noqa: F401  (registers the pharmacy domain pack)

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples")

# The pinned "today" every expiry assertion is measured from.
AS_OF = "2026-01-01"
PHARMACY = KPIFilters(as_of=AS_OF)


@pytest.fixture
def stock() -> pd.DataFrame:
    """
    Hand-built pharmacy stock, measured from 2026-01-01. Values are quantity x cost:

      row 2  EXPIRED   2025-12-01  -31d   10 x  50 =   500   <- already expired
      row 3  EXPIRED   2025-06-30 -185d    2 x 100 =   200   <- already expired
      row 4  BUCKET30  2026-01-01    0d    5 x  20 =   100   <- expires today
      row 5  BUCKET30  2026-01-31   30d    3 x 100 =   300   <- exactly 30, lower band
      row 6  BUCKET60  2026-02-01   31d    4 x  25 =   100   <- one day past 30
      row 7  BUCKET60  2026-03-02   60d    2 x 500 =  1000   <- exactly 60
      row 8  BUCKET90  2026-03-03   61d   10 x  10 =   100   <- one day past 60
      row 9  BUCKET90  2026-04-01   90d    1 x 900 =   900   <- exactly 90
      row 10 BEYOND    2026-04-02   91d   50 x  40 =  2000   <- outside every bucket
      row 11 EXCLUDED  (no expiry)         7 x  30            <- must be reported
      row 12 EXCLUDED  2026-01-15   14d  qty NaN             <- must be reported

    expired      = 500 + 200            =  700
    0-30 days    = 100 + 300            =  400
    31-60 days   = 100 + 1000           = 1100
    61-90 days   = 100 + 900            = 1000
    near-expiry  = 400 + 1100 + 1000    = 2500
    """
    return pd.DataFrame(
        [
            (2,  "Panadol",    "B-01", "2025-12-01", 10.0,  50.0,   80.0, "GSK"),
            (3,  "Augmentin",  "B-02", "2025-06-30",  2.0, 100.0,  150.0, "GSK"),
            (4,  "Brufen",     "B-03", "2026-01-01",  5.0,  20.0,   30.0, "Abbott"),
            (5,  "Nexium",     "B-04", "2026-01-31",  3.0, 100.0,  140.0, "Abbott"),
            (6,  "Glucophage", "B-05", "2026-02-01",  4.0,  25.0,   40.0, "Merck"),
            (7,  "Lantus",     "B-06", "2026-03-02",  2.0, 500.0,  650.0, "Sanofi"),
            (8,  "Crestor",    "B-07", "2026-03-03", 10.0,  10.0,   16.0, "Sanofi"),
            (9,  "Plavix",     "B-08", "2026-04-01",  1.0, 900.0, 1200.0, "Sanofi"),
            (10, "Zinc",       "B-09", "2026-04-02", 50.0,  40.0,   55.0, "Merck"),
            (11, "NoExpiry",   "B-10", None,          7.0,  30.0,   45.0, "Merck"),
            (12, "NoQty",      "B-11", "2026-01-15",  None, 60.0,   90.0, "GSK"),
        ],
        columns=[
            "source_row", "product_id", "batch_no", "expiry_date",
            "quantity", "cost", "mrp", "manufacturer",
        ],
    ).assign(source_connector="csv")


def _compute(key, df, filters=PHARMACY):
    return engine.compute(key, df, filters, domain="pharmacy")


# ---------------------------------------------------------------------------
# Registration via the DomainPack hook
# ---------------------------------------------------------------------------


def test_expiry_kpis_register_only_for_pharmacy():
    local = KPIEngine()
    core_keys = {s.key for s in local.list_kpis()}
    pharmacy_keys = {s.key for s in local.list_kpis("pharmacy")}

    for key in (
        "near_expiry_total", "expired_stock_value", "expiring_value_30d",
        "expiring_value_60d", "expiring_value_90d",
        "near_expiry_item_count", "expired_item_count", "expiry_by_manufacturer",
    ):
        assert key in pharmacy_keys, key
        assert key not in core_keys, f"{key} leaked into core"

    # A domain with no pack contributes nothing and does not raise.
    assert {s.key for s in local.list_kpis("grocery")} == core_keys


def test_core_kpis_still_available_in_pharmacy_domain(stock):
    """Domain KPIs are additive: the core pack is untouched."""
    keys = {s.key for s in engine.list_kpis("pharmacy")}
    assert "total_revenue" in keys
    assert "near_expiry_total" in keys


def test_engine_core_has_no_pharmacy_vocabulary():
    """Grep-clean guarantee: domain nouns live only in app/analytics/domains/."""
    core_dir = os.path.join(os.path.dirname(__file__), "..", "app", "analytics")
    banned = ("expiry", "expire", "batch", "mrp", "pharmac", "medicine", "drug")
    for filename in sorted(os.listdir(core_dir)):
        if not filename.endswith(".py"):
            continue
        with open(os.path.join(core_dir, filename), encoding="utf-8") as handle:
            text = handle.read().casefold()
        for word in banned:
            assert word not in text, f"core file {filename} contains domain word '{word}'"


# ---------------------------------------------------------------------------
# Exact bucket values on the pinned date
# ---------------------------------------------------------------------------


def test_expired_stock_value_exact(stock):
    result = _compute("expired_stock_value", stock)
    assert result.status == STATUS_OK
    assert result.value == 700.0  # 500 + 200
    assert result.unit == "PKR"
    assert result.provenance.source_rows == [2, 3]


def test_bucket_30_exact_and_boundary_inclusive(stock):
    """0 days (expires today) and exactly 30 days both land in the 0-30 band."""
    result = _compute("expiring_value_30d", stock)
    assert result.status == STATUS_OK
    assert result.value == 400.0  # 100 + 300
    assert result.provenance.source_rows == [4, 5]


def test_bucket_60_is_banded_not_cumulative(stock):
    """31-60 only: it must NOT include the 0-30 rows."""
    result = _compute("expiring_value_60d", stock)
    assert result.value == 1100.0  # 100 + 1000
    assert result.provenance.source_rows == [6, 7]
    assert 4 not in result.provenance.source_rows
    assert 5 not in result.provenance.source_rows


def test_bucket_90_is_banded(stock):
    """61-90 only, and 90 days exactly is inside."""
    result = _compute("expiring_value_90d", stock)
    assert result.value == 1000.0  # 100 + 900
    assert result.provenance.source_rows == [8, 9]


def test_buckets_are_disjoint_and_sum_to_near_expiry_total(stock):
    buckets = [_compute(f"expiring_value_{n}d", stock) for n in (30, 60, 90)]
    rows = [set(b.provenance.source_rows) for b in buckets]
    assert rows[0] & rows[1] == set()
    assert rows[1] & rows[2] == set()
    assert rows[0] & rows[2] == set()

    total = _compute("near_expiry_total", stock)
    assert total.value == sum(b.value for b in buckets) == 2500.0


def test_near_expiry_total_excludes_expired_and_beyond_horizon(stock):
    total = _compute("near_expiry_total", stock)
    assert total.value == 2500.0
    assert 2 not in total.provenance.source_rows   # expired
    assert 3 not in total.provenance.source_rows   # expired
    assert 10 not in total.provenance.source_rows  # 91 days, past the horizon


def test_expired_and_near_expiry_are_separate(stock):
    expired = _compute("expired_stock_value", stock)
    near = _compute("near_expiry_total", stock)
    assert set(expired.provenance.source_rows) & set(near.provenance.source_rows) == set()


def test_item_counts_exact(stock):
    assert _compute("expired_item_count", stock).value == 2.0
    assert _compute("near_expiry_item_count", stock).value == 6.0
    assert _compute("expired_item_count", stock).unit == "count"


def test_item_counts_dedupe_product_batch_pairs():
    """The same product/batch split across two rows is ONE at-risk item."""
    df = pd.DataFrame(
        {
            "source_row": [2, 3],
            "product_id": ["Panadol", "Panadol"],
            "batch_no": ["B-01", "B-01"],
            "expiry_date": ["2026-01-10", "2026-01-10"],
            "quantity": [5.0, 5.0],
            "cost": [10.0, 10.0],
        }
    )
    assert _compute("near_expiry_item_count", df).value == 1.0
    assert _compute("near_expiry_total", df).value == 100.0  # value still counts both rows


# ---------------------------------------------------------------------------
# Value basis
# ---------------------------------------------------------------------------


def test_value_basis_defaults_to_cost(stock):
    result = _compute("expiring_value_30d", stock)
    assert result.value == 400.0
    assert "quantity x cost" in result.formula


def test_value_basis_mrp_switches_valuation(stock):
    """At MRP the 0-30 bucket is 5x30 + 3x140 = 570, not 400."""
    filters = KPIFilters(as_of=AS_OF, options={"value_basis": "mrp"})
    result = _compute("expiring_value_30d", stock, filters)
    assert result.value == 570.0
    assert "quantity x mrp" in result.formula


def test_missing_cost_falls_back_to_mrp_with_a_note(stock):
    no_cost = stock.drop(columns=["cost"])
    result = _compute("expiring_value_30d", no_cost, PHARMACY)
    assert result.status == STATUS_OK
    assert result.value == 570.0
    assert any("valued at 'mrp'" in note for note in result.provenance.assumptions)


def test_unavailable_when_no_value_basis_at_all(stock):
    bare = stock.drop(columns=["cost", "mrp"])
    result = _compute("near_expiry_total", bare)
    assert result.status == STATUS_UNAVAILABLE
    assert result.value is None
    assert "value basis" in result.reason


# ---------------------------------------------------------------------------
# Reference date injection
# ---------------------------------------------------------------------------


def test_reference_date_shifts_the_buckets(stock):
    """Two months later, previously-near-expiry stock has become expired."""
    later = _compute("expired_stock_value", stock, KPIFilters(as_of="2026-03-01"))
    # Now expired: rows 2, 3 (already were) plus 4, 5, 6 (Jan 1, Jan 31, Feb 1).
    assert later.provenance.source_rows == [2, 3, 4, 5, 6]
    assert later.value == 700.0 + 100.0 + 300.0 + 100.0


def test_reference_date_is_recorded_in_formula_and_provenance(stock):
    result = _compute("near_expiry_total", stock)
    assert AS_OF in result.formula
    assert result.provenance.filters["as_of"] == AS_OF


def test_unparseable_as_of_falls_back_to_today_with_a_note(stock):
    result = _compute("near_expiry_total", stock, KPIFilters(as_of="not-a-date"))
    assert any("could not be parsed" in note for note in result.provenance.assumptions)


def test_no_as_of_uses_today_without_crashing(stock):
    """Default path still works; only that it computes is asserted, not the value."""
    result = engine.compute("near_expiry_total", stock, KPIFilters(), domain="pharmacy")
    assert result.status == STATUS_OK


# ---------------------------------------------------------------------------
# Exclusions are reported, never silent
# ---------------------------------------------------------------------------


def test_missing_expiry_row_is_excluded_and_reported(stock):
    result = _compute("near_expiry_total", stock)
    assert 11 not in result.provenance.source_rows
    assert any(
        "expiry date is missing" in note and "1 of 11" in note
        for note in result.provenance.assumptions
    )


def test_missing_quantity_row_is_excluded_and_reported(stock):
    result = _compute("expiring_value_30d", stock)
    assert 12 not in result.provenance.source_rows
    assert any("quantity is missing" in note for note in result.provenance.assumptions)


def test_excluded_rows_do_not_corrupt_totals(stock):
    """The excluded rows would add 210 at cost if wrongly included."""
    total = sum(_compute(f"expiring_value_{n}d", stock).value for n in (30, 60, 90))
    assert total == 2500.0


def test_unparseable_expiry_is_excluded_not_crashing():
    df = pd.DataFrame(
        {
            "source_row": [2, 3],
            "product_id": ["A", "B"],
            "batch_no": ["X", "Y"],
            "expiry_date": ["2026-01-10", "not-a-date"],
            "quantity": [2.0, 5.0],
            "cost": [10.0, 10.0],
        }
    )
    result = _compute("near_expiry_total", df)
    assert result.value == 20.0
    assert result.provenance.source_rows == [2]
    assert any("expiry date is missing" in note for note in result.provenance.assumptions)


def test_unavailable_with_reason_when_expiry_column_absent(stock):
    result = _compute("near_expiry_total", stock.drop(columns=["expiry_date"]))
    assert result.status == STATUS_UNAVAILABLE
    assert result.value is None
    assert "expiry_date" in result.reason


def test_unavailable_with_reason_when_quantity_column_absent(stock):
    result = _compute("expired_stock_value", stock.drop(columns=["quantity"]))
    assert result.status == STATUS_UNAVAILABLE
    assert "quantity" in result.reason


def test_empty_window_is_a_real_zero_not_unavailable():
    """Good data with nothing expiring is 0.0 and available — not 'unavailable'."""
    df = pd.DataFrame(
        {
            "source_row": [2],
            "product_id": ["A"], "batch_no": ["X"],
            "expiry_date": ["2030-01-01"], "quantity": [5.0], "cost": [10.0],
        }
    )
    result = _compute("expired_stock_value", df)
    assert result.status == STATUS_OK
    assert result.value == 0.0
    assert result.breakdown == []


# ---------------------------------------------------------------------------
# Breakdown and provenance
# ---------------------------------------------------------------------------


def test_breakdown_lists_at_risk_items_soonest_first(stock):
    result = _compute("near_expiry_total", stock)
    assert [row["product_id"] for row in result.breakdown] == [
        "Brufen", "Nexium", "Glucophage", "Lantus", "Crestor", "Plavix",
    ]
    assert [row["days_to_expiry"] for row in result.breakdown] == [0, 30, 31, 60, 61, 90]


def test_breakdown_line_values_are_correct(stock):
    result = _compute("expiring_value_30d", stock)
    assert result.breakdown == [
        {
            "product_id": "Brufen", "batch_no": "B-03", "expiry_date": "2026-01-01",
            "days_to_expiry": 0, "quantity": 5.0, "unit_value": 20.0,
            "line_value": 100.0, "source_row": 4,
        },
        {
            "product_id": "Nexium", "batch_no": "B-04", "expiry_date": "2026-01-31",
            "days_to_expiry": 30, "quantity": 3.0, "unit_value": 100.0,
            "line_value": 300.0, "source_row": 5,
        },
    ]
    assert sum(row["line_value"] for row in result.breakdown) == result.value


def test_provenance_rows_match_the_contributing_batches(stock):
    """Re-sum from only the referenced rows; it must reproduce the figure."""
    result = _compute("expiring_value_60d", stock)
    referenced = stock[stock["source_row"].isin(result.provenance.source_rows)]
    assert float((referenced["quantity"] * referenced["cost"]).sum()) == result.value
    assert sorted(referenced["batch_no"]) == ["B-05", "B-06"]


def test_expiry_by_manufacturer_exact(stock):
    result = _compute("expiry_by_manufacturer", stock)
    assert result.status == STATUS_OK
    assert result.breakdown == [
        {"manufacturer": "Sanofi", "value": 2000.0, "item_count": 3},
        {"manufacturer": "Abbott", "value": 400.0, "item_count": 2},
        {"manufacturer": "Merck", "value": 100.0, "item_count": 1},
    ]
    assert result.value == 2500.0


def test_result_is_json_serializable(stock):
    result = _compute("near_expiry_total", stock)
    restored = json.loads(json.dumps(result.to_dict()))
    assert restored["value"] == 2500.0
    assert restored["breakdown"][0]["batch_no"] == "B-03"
    assert restored["provenance"]["row_count"] == 6


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism_with_pinned_date(stock):
    first = _compute("near_expiry_total", stock).to_dict()
    second = _compute("near_expiry_total", stock.copy()).to_dict()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_row_order_does_not_change_results(stock):
    shuffled = stock.iloc[::-1].reset_index(drop=True)
    for key in ("near_expiry_total", "expired_stock_value", "expiring_value_30d"):
        original, reordered = _compute(key, stock), _compute(key, shuffled)
        assert original.value == reordered.value, key
        assert original.provenance.source_rows == reordered.provenance.source_rows, key
        assert original.breakdown == reordered.breakdown, key


def test_configured_buckets_drive_the_registered_keys():
    """Buckets come from settings, not hard-coded keys."""
    original = settings.expiry_buckets_days
    settings.expiry_buckets_days = [15, 45]
    try:
        local = KPIEngine()
        keys = {s.key for s in local.list_kpis("pharmacy")}
        assert "expiring_value_15d" in keys
        assert "expiring_value_45d" in keys
        assert "expiring_value_30d" not in keys
    finally:
        settings.expiry_buckets_days = original


# ---------------------------------------------------------------------------
# Chatbot wiring
# ---------------------------------------------------------------------------


def test_expiry_questions_route_to_expiry_kpis():
    assert select_kpi_keys("what is expiring in 30 days?", "pharmacy")[0] == "near_expiry_total"
    assert select_kpi_keys("kitna stock expire ho raha hai", "pharmacy")[0] == "near_expiry_total"
    assert select_kpi_keys("kitna maal khatam ho raha hai", "pharmacy")[0] == "near_expiry_total"
    assert select_kpi_keys("show me near expiry items", "pharmacy")[0] == "near_expiry_total"
    assert select_kpi_keys("how much stock is already expired", "pharmacy")[0] == "expired_stock_value"


def test_expiry_vocabulary_is_pharmacy_only():
    """Without the pharmacy domain, 'expiring' must not resolve to a pharmacy KPI."""
    keys = select_kpi_keys("what is expiring in 30 days?", "")
    assert "near_expiry_total" not in keys


def test_domain_rules_do_not_hijack_core_questions():
    assert select_kpi_keys("what were my total sales?", "pharmacy") == [
        "total_revenue", "transaction_count",
    ]


def test_seam_returns_computed_expiry_figure(stock):
    """The chatbot's numeric route gets the engine's figure, with at-risk items."""
    records = stock.assign(source_file="inventory.csv").to_dict(orient="records")
    computed, source_rows = AnalyticsRouter().compute(
        "how much stock is expiring?", {"as_of": AS_OF}, records, "pharmacy"
    )
    assert computed["near_expiry_total"]["value"] == 2500.0
    assert computed["near_expiry_total"]["unit"] == "PKR"
    assert computed["near_expiry_total"]["breakdown"][0]["product_id"] == "Brufen"
    assert computed["near_expiry_item_count"]["value"] == 6.0
    assert source_rows == [4, 5, 6, 7, 8, 9]
    json.dumps(computed)  # must survive chat.py's json.dumps into the prompt


def test_chatbot_expiry_question_number_comes_from_code_not_llm(stock):
    """End to end through RAGChat with a mocked LLM: the LLM never computes."""
    from unittest.mock import patch

    from app.ingestion.models import RetrievedChunk
    from app.rag.models import ChatRequest

    records = stock.assign(source_file="inventory.csv").to_dict(orient="records")
    chunks = [
        RetrievedChunk(text="stock row", metadata=m, score=0.9, source_row=m["source_row"])
        for m in records
    ]

    with patch("app.rag.chat.KnowledgeBase") as kb, patch("app.rag.chat.llm") as llm, patch(
        "app.rag.chat.session_manager"
    ), patch("app.rag.chat.extract_filters", return_value={"as_of": AS_OF}):
        kb.return_value.search.return_value = chunks
        llm.chat.return_value = "You have PKR 2,500 of stock expiring soon."

        from app.rag.chat import RAGChat

        response = RAGChat().ask(
            ChatRequest(question="how much stock is expiring in 30 days?", session_id="t")
        )

    assert response.route == "analytics"
    assert response.computed_values["near_expiry_total"]["value"] == 2500.0

    # The prompt handed to the LLM contained the already-computed figure.
    prompt = llm.chat.call_args.kwargs["messages"][-1]["content"]
    assert "2500.0" in prompt
    assert "Computed Values from Analytics Engine" in prompt

    # And the sources point back at the at-risk rows.
    assert sorted(s.source_row for s in response.sources) == [4, 5, 6, 7, 8, 9]


def test_domain_pack_module_never_imports_the_llm():
    domains_dir = os.path.join(os.path.dirname(__file__), "..", "app", "analytics", "domains")
    for filename in sorted(os.listdir(domains_dir)):
        if not filename.endswith(".py"):
            continue
        with open(os.path.join(domains_dir, filename), encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("app.core.llm", "import ollama", "from ollama"):
            assert forbidden not in source, f"{filename} references {forbidden}"


# ---------------------------------------------------------------------------
# Real sample inventory fixture
# ---------------------------------------------------------------------------


def _canonical_inventory() -> pd.DataFrame:
    from app.connectors.base import detect_connector
    from app.schema.domain import get_domain_pack
    from app.schema.mapper import map_headers
    from app.schema.normalize import apply_mapping

    path = os.path.abspath(os.path.join(SAMPLES_DIR, "challenging_pharma_inventory.csv"))
    raw = detect_connector(path).fetch()
    mapping = map_headers(list(raw.columns), get_domain_pack("pharmacy"))
    return apply_mapping(raw, mapping, domain="pharmacy", keep_extras=False)


def test_expiry_report_runs_on_real_inventory_fixture():
    df = _canonical_inventory()
    results = {
        s.key: engine.compute(s.key, df, PHARMACY, domain="pharmacy")
        for s in engine.list_kpis("pharmacy")
        if "risk" in s.tags
    }
    assert results["near_expiry_total"].status == STATUS_OK
    assert results["expired_stock_value"].status == STATUS_OK
    # The fixture has exactly one row with no expiry date; it must be reported.
    assert any(
        "expiry date is missing" in note
        for note in results["near_expiry_total"].provenance.assumptions
    )
    json.dumps({k: v.to_dict() for k, v in results.items()})


def test_real_inventory_near_expiry_matches_hand_computation():
    """Cross-check the KPI against a plain pandas computation on the same frame."""
    df = _canonical_inventory()
    expiry = pd.to_datetime(df["expiry_date"], errors="coerce")
    days = (expiry.dt.normalize() - pd.Timestamp(AS_OF)).dt.days
    qty = pd.to_numeric(df["quantity"], errors="coerce")
    cost = pd.to_numeric(df["cost"], errors="coerce")
    usable = expiry.notna() & qty.notna() & (qty > 0) & cost.notna()

    expected = round(float((qty * cost)[usable & (days >= 0) & (days <= 90)].sum()), 2)
    assert engine.compute("near_expiry_total", df, PHARMACY, domain="pharmacy").value == expected

    expected_expired = round(float((qty * cost)[usable & (days < 0)].sum()), 2)
    assert engine.compute("expired_stock_value", df, PHARMACY, domain="pharmacy").value == expected_expired
