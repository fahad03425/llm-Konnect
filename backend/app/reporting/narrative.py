"""
Module 4.2 — Report narrative generator. Month 5.

Role in the pipeline
--------------------
"Code computes. The LLM narrates. A verifier checks."

The KPI engine has already computed every figure deterministically.
This module's only job is to hand those numbers to the LLM together with
strict grounding instructions so it writes readable business prose —
never inventing, rounding, or recalculating any figure on its own.

The resulting narrative string is then validated downstream by
`reporting.verifier.verify()` (wired together in report.py — Step 3).

Design constraints
------------------
* The LLM is NEVER the source of truth for numbers.
* Unavailable KPIs (value=None, status="unavailable") must be described
  as "could not be determined" with the engine's reason verbatim —
  never treated as zero or estimated.
* Forecast KPIs carry a range (lower/upper) — the narrative must present
  that range, never narrow it to a single point.
* If there is nothing meaningful to narrate (empty dict OR every single
  KPI is unavailable), we return a fixed message without calling the LLM.
* LLM call failure raises RuntimeError (same pattern as llm.py).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.core.llm import llm

try:
    from app.analytics.models import KPIResult
except ImportError:
    KPIResult = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Grounding system prompt (mirrors rag/chat.py ANALYTICS route discipline)
# ---------------------------------------------------------------------------
# Keep this as a named constant so reviewers can audit it independently of
# the surrounding code, and so report.py can expose it if needed.

_SYSTEM_PROMPT_TEMPLATE = """\
You are a professional business-report writer for {business_name}.

You are writing one section of a structured business analytics report \
for the {domain} domain. Your output will be embedded directly into a \
formal document, so write in clear, confident, third-person business prose.

═══════════════════════════════════════════════
ABSOLUTE GROUNDING RULES — NEVER VIOLATE THESE
═══════════════════════════════════════════════

1. NARRATE, DO NOT CALCULATE.
   Every number in your narrative MUST come word-for-word from the \
"KPI Data" block below. Do NOT add, subtract, average, or otherwise \
recalculate any figure. If you need a number that is not in the KPI Data, \
do not write it.

2. UNAVAILABLE MEANS UNAVAILABLE — NOT ZERO.
   If a KPI has "status": "unavailable", the figure could not be computed.
   State plainly that it "could not be determined" and include the \
"reason" field verbatim. Do NOT substitute an estimate. Do NOT write zero.
   Do NOT write "approximately" or "roughly" for an unavailable value.

3. FORECASTS ARE RANGES, NEVER CERTAINTIES.
   A KPI that carries a "forecast" list contains estimated future points \
with lower/upper bands. Describe each forecast point as a RANGE \
(e.g. "expected to be between X and Y"). Never narrow a range to a single \
number and never present a forecast as a measured fact.

4. UNITS MATTER.
   - Currency values are in PKR — write "PKR" before the number.
   - Percent values — write the number followed by "%".
   - Count values — write the plain integer with an appropriate noun.

5. SCOPE.
   Write 200–400 words. Use short paragraphs. Start with a brief summary \
of the period covered (use the "period" fields if present), then discuss \
each major KPI group in logical order (revenue, margins, volumes, trends, \
forecasts). End with one sentence noting any KPIs that were unavailable, \
if any.

══════════════════════
WHAT YOU MUST NOT DO
══════════════════════
- Do NOT make up numbers.
- Do NOT reference external benchmarks or industry averages.
- Do NOT add qualitative commentary beyond what the data supports.
- Do NOT use hedging language ("approximately", "roughly", "around") for \
any measured value — only for forecasts, and only the range given.
"""

_USER_PROMPT_TEMPLATE = """\
KPI Data (computed by the Analytics Engine — treat as ground truth):

{kpi_json}

Write the business narrative report section now, following all grounding \
rules in the system prompt.
"""

_FALLBACK_MESSAGE = (
    "No KPI data was available to narrate. "
    "All requested metrics could not be computed for the selected period and filters."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_kpis(kpi_results: Dict[str, Any]) -> str:
    """
    Serialize the KPIResult dict into a clean JSON string for the prompt.

    Uses .to_dict() when available (real KPIResult objects), falls back to
    the raw value for plain objects that lack .to_dict() (e.g. test stubs).
    """
    serialized: Dict[str, Any] = {}
    for key, result in kpi_results.items():
        if hasattr(result, "to_dict"):
            serialized[key] = result.to_dict()
        else:
            # Defensive fallback — should not reach here in production.
            serialized[key] = str(result)
    return json.dumps(serialized, indent=2, ensure_ascii=False)


def _has_any_available(kpi_results: Dict[str, Any]) -> bool:
    """Return True if at least one KPI has status 'ok' and a non-None value.

    Note: value=0.0 is a valid, available result (e.g. refund_rate_pct=0.0
    for a business with no refunds). We must never confuse it with None
    (which signals "unavailable"). The extraction below uses explicit
    hasattr/isinstance branching to avoid the `or` operator's falsy-zero trap.
    """
    for result in kpi_results.values():
        # status — non-empty string ("ok" / "unavailable"), always truthy,
        # so `or` is technically safe here. Using the same explicit pattern
        # for consistency and future-proofing.
        if hasattr(result, "status"):
            status = result.status
        elif isinstance(result, dict):
            status = result.get("status")
        else:
            status = None

        # value — may legitimately be 0.0, which `or` would treat as falsy.
        # Must use explicit presence check, NOT `or`.
        if hasattr(result, "value"):
            value = result.value
        elif isinstance(result, dict):
            value = result.get("value")
        else:
            value = None

        if status == "ok" and value is not None:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_narrative(
    kpi_results: Dict[str, Any],
    domain: str = "pharmacy",
    business_name: Optional[str] = None,
) -> str:
    """
    Generate a grounded, LLM-written business narrative from KPI ground truth.

    Parameters
    ----------
    kpi_results:
        Dict[str, KPIResult] as returned by ``engine.compute_all(...)``.
        Each KPIResult must have ``.to_dict()``, ``.status``, and ``.value``.
    domain:
        Business domain label (e.g. "pharmacy"), used in the system prompt
        so the LLM can apply appropriate vocabulary.
    business_name:
        Optional display name for the business (e.g. "Al-Shifa Pharmacy").
        Falls back to a generic label if not provided.

    Returns
    -------
    str
        Plain narrative text suitable for embedding in a report.
        If no KPI data is available, returns _FALLBACK_MESSAGE without
        calling the LLM.

    Raises
    ------
    RuntimeError
        If the LLM call fails (Ollama not running, model not loaded, etc.).
        The error message mirrors the pattern used in ``app.core.llm``.
    """
    # ------------------------------------------------------------------
    # Guard: nothing to narrate
    # ------------------------------------------------------------------
    if not kpi_results or not _has_any_available(kpi_results):
        return _FALLBACK_MESSAGE

    # ------------------------------------------------------------------
    # Build prompts
    # ------------------------------------------------------------------
    effective_name = business_name or f"the {domain.title()} business"

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        business_name=effective_name,
        domain=domain,
    )

    kpi_json = _serialize_kpis(kpi_results)
    user_prompt = _USER_PROMPT_TEMPLATE.format(kpi_json=kpi_json)

    # ------------------------------------------------------------------
    # Call LLM
    # ------------------------------------------------------------------
    try:
        narrative = llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Narrative generation failed — Ollama inference error: {exc}"
        ) from exc

    return narrative.strip()


# ---------------------------------------------------------------------------
# __main__ demonstration
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dataclasses import dataclass, field
    from typing import List

    # ------------------------------------------------------------------
    # Minimal KPIResult stand-in (same style as verifier.py __main__).
    # Does not import the real analytics package so this runs standalone.
    # ------------------------------------------------------------------
    @dataclass
    class _FakeProvenance:
        filter_description: str = "no filter (all rows)"
        row_count: int = 0
        source_rows: List[int] = field(default_factory=list)
        source_rows_truncated: bool = False
        sources: List[str] = field(default_factory=list)
        columns_used: List[str] = field(default_factory=list)
        assumptions: List[str] = field(default_factory=list)
        filters: dict = field(default_factory=dict)

        def to_dict(self):
            return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @dataclass
    class _FakePeriod:
        start: str = "2026-01-01"
        end: str = "2026-06-30"

        def to_dict(self):
            return {"start": self.start, "end": self.end}

    @dataclass
    class _FakeKPI:
        key: str
        name: str
        value: Optional[float]
        unit: str
        formula: str = ""
        status: str = "ok"
        reason: Optional[str] = None
        period: Optional[_FakePeriod] = None
        breakdown: None = None
        breakdown_columns: None = None
        method: Optional[str] = None
        series: None = None
        forecast: None = None
        provenance: _FakeProvenance = field(default_factory=_FakeProvenance)

        def to_dict(self):
            return {
                "key": self.key,
                "name": self.name,
                "value": self.value,
                "unit": self.unit,
                "formula": self.formula,
                "status": self.status,
                "reason": self.reason,
                "period": self.period.to_dict() if self.period else None,
                "breakdown": self.breakdown,
                "breakdown_columns": self.breakdown_columns,
                "method": self.method,
                "series": self.series,
                "forecast": self.forecast,
                "provenance": self.provenance.to_dict(),
            }

    # ------------------------------------------------------------------
    # Demo KPI set: mix of ok, unavailable, and a forecast
    # ------------------------------------------------------------------
    period = _FakePeriod()
    demo_kpis = {
        "total_revenue": _FakeKPI(
            key="total_revenue",
            name="Total Revenue",
            value=2_450_000.0,
            unit="PKR",
            formula="sum(net_amount)",
            period=period,
        ),
        "profit_margin": _FakeKPI(
            key="profit_margin",
            name="Profit Margin",
            value=21.4,
            unit="percent",
            formula="(revenue - cogs) / revenue * 100",
            period=period,
        ),
        "transaction_count": _FakeKPI(
            key="transaction_count",
            name="Transaction Count",
            value=842.0,
            unit="count",
            formula="count(invoice_id)",
            period=period,
        ),
        "expired_stock_value": _FakeKPI(
            key="expired_stock_value",
            name="Expired Stock Value",
            value=None,
            unit="PKR",
            formula="sum(net_amount) where expiry < today",
            status="unavailable",
            reason="Column 'expiry_date' was not present in the uploaded dataset.",
        ),
        "revenue_forecast_next_month": _FakeKPI(
            key="revenue_forecast_next_month",
            name="Revenue Forecast — Next Month",
            value=2_600_000.0,
            unit="PKR",
            formula="linear_regression(monthly_revenue)",
            method="linear_regression",
            forecast=[
                {"month": "2026-07", "value": 2_600_000.0, "lower": 2_300_000.0, "upper": 2_900_000.0}
            ],
        ),
    }

    print("=" * 60)
    print("Narrative Generator — Demo")
    print("(Requires Ollama to be running with the configured model)")
    print("=" * 60)

    try:
        narrative = generate_narrative(
            kpi_results=demo_kpis,
            domain="pharmacy",
            business_name="Al-Shifa Pharmacy",
        )
        print("\nGenerated Narrative:\n")
        print(narrative)
    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
        print("Make sure Ollama is running and the model is loaded.")
