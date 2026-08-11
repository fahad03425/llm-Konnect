"""
Module 6.6 (KPI Engine) — seam.py

The seam Module 6.5's numeric (analytics) chat route calls.

This SUPERSEDES the temporary in-module pandas fallback that shipped inside
`app.rag.router.AnalyticsRouter` in 6.5. The class here keeps that exact public
signature — `compute(question, filters, kb_records) -> (computed_values, source_rows)`
— so `app/rag/chat.py` needs no call-site change; `app/rag/router.py` now simply
re-exports this implementation.

The mapping from question to KPI is deterministic keyword matching. The LLM does
not choose the metric and does not compute the number: it only narrates the
`computed_values` this function returns.
"""

import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.engine import KPIEngine, engine as default_engine
from app.analytics.filters import KPIFilters
from app.analytics.models import KPIResult

# Question keyword -> KPI keys, evaluated in this order. First match wins, so the
# more specific intents are listed before the generic "total/how much" catch-all.
_INTENT_RULES: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    # Trend and forecast intents come first: they are more specific than the
    # generic "total/how much" catch-all further down.
    (("will i sell", "how many will", "how much will i sell", "kitni sale hogi", "kitna bikay"),
     ("product_demand_forecast", "demand_forecast")),
    (("forecast", "predict", "prediction", "projection", "next month", "next week",
      "expected sales", "agle mahine", "agle month"),
     ("revenue_forecast", "demand_forecast")),
    (("rising", "fastest growing", "top movers", "growing fastest"), ("top_rising_products",)),
    (("declining", "falling", "dropping", "slowing down"), ("top_declining_products",)),
    (("trend", "growth", "growing", "increasing", "decreasing", "barh rah", "kam ho rah"),
     ("revenue_trend", "units_trend")),
    (("margin", "margins"), ("gross_margin_pct", "net_margin_pct", "gross_profit")),
    (("refund", "refunds", "return", "returns", "wapsi"),
     ("total_refunds", "refund_rate_pct")),
    (("profit", "munafa", "nafa"), ("net_profit", "gross_profit", "total_revenue")),
    (("expense", "expenses", "spend", "spent", "purchase", "purchases", "kharcha", "kharch"),
     ("total_expenses", "expense_breakdown_by_category")),
    (("average", "avg", "mean", "ausat"), ("average_transaction_value", "transaction_count")),
    (("how many", "count", "number of", "kitne"), ("transaction_count",)),
    (("per month", "monthly", "by month", "each month"), ("revenue_by_month",)),
    (("by product", "per product", "top product", "best selling", "top selling"),
     ("revenue_breakdown_by_product",)),
    (("by category", "per category"), ("revenue_breakdown_by_category",)),
    (("revenue", "sales", "sale", "turnover", "total", "how much", "sum", "kitna", "bikri"),
     ("total_revenue", "transaction_count")),
]

# Used when nothing matches but the router already decided this is a numeric question.
_DEFAULT_KEYS: Tuple[str, ...] = ("total_revenue", "transaction_count")


def _domain_rules(domain: str) -> List[Tuple[Tuple[str, ...], Tuple[str, ...]]]:
    """
    Question vocabulary contributed by the active domain pack.

    Domain intents are checked BEFORE the core rules, so a domain can claim a word
    that the core would otherwise route to a generic KPI. Keeping the vocabulary on
    the pack is what stops domain nouns leaking in here.
    """
    if not domain:
        return []
    try:
        from app.schema.domain import get_domain_pack

        return list(get_domain_pack(domain).kpi_question_rules)
    except (ImportError, ValueError, AttributeError):
        return []


def select_kpi_keys(question: str, domain: str = "") -> List[str]:
    """
    Deterministically pick which KPIs answer a numeric question.

    Pure string matching — no model call, no randomness, same question always
    yields the same keys in the same order. Domain rules first, then core rules.
    """
    q = (question or "").casefold()
    for keywords, keys in list(_domain_rules(domain)) + _INTENT_RULES:
        if any(word in q for word in keywords):
            return list(keys)
    return list(_DEFAULT_KEYS)


def infer_product_id(question: str, df: pd.DataFrame) -> Optional[str]:
    """
    Resolve which product a question names, by matching against the product ids
    actually present in the data.

    Data-driven and deterministic: no model, no fuzzy scoring. A question word must
    appear as a whole word of a product id, and a word only counts when it carries
    at least four LETTERS — so packaging noise like "500mg", "tab" or "10s" can
    never match a product, while "panadol" can. The longest match wins; ties break
    alphabetically so the answer never depends on row order.

    Returns the FULL canonical product id, which is what the exact-match
    `product_id` filter needs.
    """
    if "product_id" not in df.columns or not question:
        return None

    asked = set(re.split(r"[^a-z0-9]+", question.casefold()))
    best: Optional[str] = None
    best_len = 0
    for value in sorted({str(v) for v in df["product_id"].dropna()}):
        for token in re.split(r"[^a-z0-9]+", value.casefold()):
            letters = sum(1 for ch in token if ch.isalpha())
            if letters < 4 or token not in asked:
                continue
            if letters > best_len or (letters == best_len and best is not None and value < best):
                best, best_len = value, letters
    return best


def _records_to_frame(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Build a canonical-shaped DataFrame from retrieved knowledge-base records.

    KB metadata preserves the canonical field names plus `source_file`/`source_row`,
    so provenance survives the round trip through retrieval.
    """
    df = pd.DataFrame(records)
    if "source_row" in df.columns:
        df["source_row"] = pd.to_numeric(df["source_row"], errors="coerce")
    return df


def compute_for_question(
    question: str,
    df: pd.DataFrame,
    filters: Optional[Dict[str, Any]] = None,
    domain: str = "",
    kpi_engine: Optional[KPIEngine] = None,
) -> Tuple[Dict[str, KPIResult], List[int]]:
    """
    Compute the KPIs that answer `question` over a canonical DataFrame.

    Returns (results_by_key, source_rows) where `source_rows` is the sorted union
    of the rows that contributed to any available result.
    """
    active_engine = kpi_engine or default_engine
    kpi_filters = KPIFilters.from_dict(filters)
    keys = select_kpi_keys(question, domain)

    # A per-product forecast needs to know WHICH product. Resolve it from the
    # question against the ids present in the data, rather than guessing.
    if "product_demand_forecast" in keys and not kpi_filters.product_id:
        named = infer_product_id(question, df)
        if named:
            kpi_filters = replace(kpi_filters, product_id=named)

    results: Dict[str, KPIResult] = {}
    rows: set = set()
    for key in keys:
        if not active_engine.has(key, domain):
            continue
        result = active_engine.compute(key, df, kpi_filters, domain)
        results[key] = result
        if result.is_available:
            rows |= set(result.provenance.source_rows)

    return results, sorted(rows)


def results_to_payload(results: Dict[str, KPIResult]) -> Dict[str, Any]:
    """
    Flatten KPI results into the JSON-serializable `computed_values` dict that
    6.5 puts in the prompt and returns in ChatResponse.

    Both available and unavailable results are included: the chatbot must be able
    to say "that can't be computed from your data, because ..." rather than
    inventing a number.
    """
    payload: Dict[str, Any] = {}
    for key, result in results.items():
        entry: Dict[str, Any] = {
            "name": result.name,
            "value": result.value,
            "unit": result.unit,
            "formula": result.formula,
            "status": result.status,
        }
        if result.reason:
            entry["reason"] = result.reason
        if result.period:
            entry["period"] = result.period.to_dict()
        if result.breakdown:
            entry["breakdown"] = result.breakdown
        if result.method:
            entry["method"] = result.method
        if result.forecast:
            # The band travels with the number so the narration can express the
            # uncertainty instead of stating an estimate as a fact.
            entry["forecast"] = result.forecast
            entry["is_estimate"] = True
            first = result.forecast[0]
            entry["estimate_range"] = {"lower": first["lower"], "upper": first["upper"]}
        entry["provenance"] = {
            "filter": result.provenance.filter_description,
            "rows_used": result.provenance.row_count,
            "source_rows": result.provenance.source_rows,
            "sources": result.provenance.sources,
        }
        if result.provenance.assumptions:
            entry["provenance"]["assumptions"] = result.provenance.assumptions
        payload[key] = entry
    return payload


class AnalyticsRouter:
    """
    The real analytics seam for the 6.5 chat pipeline.

    Drop-in replacement for the temporary 6.5 fallback: identical constructor and
    `compute` signature, but every figure now comes from `KPIEngine` with full
    provenance instead of a handful of ad-hoc aggregates.
    """

    def __init__(self, kpi_engine: Optional[KPIEngine] = None) -> None:
        self.engine = kpi_engine or default_engine

    def compute(
        self,
        question: str,
        filters: Dict[str, Any],
        kb_records: list,
        domain: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], list]:
        """
        Compute the numeric answer for a chat question.

        Args:
            question:   The user's question (already normalized by RAGChat).
            filters:    Filters extracted by `app.rag.router.extract_filters`
                        (e.g. {"month": 1}). Unknown keys are ignored.
            kb_records: Retrieved knowledge-base record metadata (canonical fields
                        plus source_file/source_row).
            domain:     Active domain, so domain KPIs and domain question vocabulary
                        apply. Optional and defaulted, so pre-existing 3-argument
                        callers keep working unchanged.

        Returns:
            (computed_values, source_rows) — `computed_values` is None when there
            is nothing to compute over, matching the 6.5 contract so the chatbot
            replies "no records found" instead of guessing.
        """
        if not kb_records:
            return None, []

        df = _records_to_frame(kb_records)
        if df.empty:
            return None, []

        results, source_rows = compute_for_question(
            question, df, filters, domain=domain, kpi_engine=self.engine
        )
        if not results:
            return None, []

        return results_to_payload(results), source_rows
