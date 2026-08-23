"""
Module 4.1 — Verified report generator (verifier). Month 5.

Deterministic, LLM-free verifier.

Role in the pipeline
--------------------
The LLM produces free-text narrative.  This module acts as a safety gate:
it extracts every numeric claim from that text, finds the matching KPIResult
in the ground-truth dict produced by KPIEngine.compute_all(), and decides
whether the LLM's number is correct (within tolerance), wrong (mismatch),
or refers to something the engine never computed (unmatched).

Design constraints
------------------
* Pure Python — stdlib only (re, dataclasses, math, typing).
* No network calls, no Ollama, no pandas.
* Deterministic: same inputs → identical VerificationReport every time.
* Regex-based extraction is intentionally simple.  Known limitations:
    - Ordinal numbers ("first", "second") are ignored — not claims.
    - Percentages written as "50 percent" (word, no symbol) are not caught;
      only numeric forms like "50 %" or "50%" are matched.
    - Numbers embedded in dates ("2024-01", "FY2025") are filtered out via
      the surrounding-word exclusion list.
    - If the LLM paraphrases a KPI value using a different scale
      (e.g. writes "1.2 million" instead of "1,200,000") the match will
      fail — improve _scale_word_to_factor() to handle more words as needed.
    - Sentence-level context is NOT analysed; attribution is purely numeric.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Import KPIResult/unit constants from the analytics layer.
# We import lazily inside functions where possible so this module can be
# imported even if the analytics package is unavailable (e.g. unit tests).
# ---------------------------------------------------------------------------
try:
    from app.analytics.models import KPIResult, UNIT_CURRENCY, UNIT_PERCENT, UNIT_COUNT
except ImportError:  # allow standalone testing / __main__
    KPIResult = None  # type: ignore[assignment,misc]
    UNIT_CURRENCY = "PKR"
    UNIT_PERCENT = "percent"
    UNIT_COUNT = "count"

# ---------------------------------------------------------------------------
# Tolerance constants
# (Named so callers can override in tests or future config without touching
# the matching logic.)
# ---------------------------------------------------------------------------

# Currency (PKR): allow ±1 % relative error to absorb rounding in prose.
TOL_CURRENCY_RELATIVE: float = 0.01

# Percent KPIs: allow ±0.5 percentage points absolute error.
TOL_PERCENT_ABS: float = 0.5

# Count KPIs: allow ±1 absolute error (e.g. "about 120 transactions" when
# the engine computed 120.0).  For large counts use ±1 % relative.
TOL_COUNT_ABS: int = 1
TOL_COUNT_RELATIVE: float = 0.01

# ---------------------------------------------------------------------------
# Status literals
# ---------------------------------------------------------------------------
STATUS_VERIFIED = "verified"
STATUS_UNMATCHED = "unmatched"
STATUS_MISMATCH = "mismatch"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class VerifiedClaim:
    """One numeric claim found in the LLM narrative, with its verdict."""

    # The raw substring that contained the number, e.g. "PKR 1,234,567.89"
    matched_text: str

    # Normalised float value extracted from `matched_text`
    extracted_value: float

    # Key of the best-matching KPIResult, or None if unmatched
    matched_kpi_key: Optional[str]

    # "verified" | "unmatched" | "mismatch"
    status: str

    # The engine's ground-truth value (only set when a KPI was found)
    expected_value: Optional[float] = None

    # Human-readable explanation when status != "verified"
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched_text": self.matched_text,
            "extracted_value": self.extracted_value,
            "matched_kpi_key": self.matched_kpi_key,
            "status": self.status,
            "expected_value": self.expected_value,
            "reason": self.reason,
        }


@dataclass
class VerificationReport:
    """Aggregate result of verifying one narrative against all KPI ground truth."""

    claims: List[VerifiedClaim] = field(default_factory=list)

    @property
    def all_verified(self) -> bool:
        """True only when every claim is verified (no mismatches or unmatched)."""
        return all(c.status == STATUS_VERIFIED for c in self.claims) if self.claims else True

    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.claims if c.status == STATUS_VERIFIED)

    @property
    def unmatched_count(self) -> int:
        return sum(1 for c in self.claims if c.status == STATUS_UNMATCHED)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for c in self.claims if c.status == STATUS_MISMATCH)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_verified": self.all_verified,
            "verified_count": self.verified_count,
            "unmatched_count": self.unmatched_count,
            "mismatch_count": self.mismatch_count,
            "claims": [c.to_dict() for c in self.claims],
        }


# ---------------------------------------------------------------------------
# Numeric extraction helpers
# ---------------------------------------------------------------------------

# Words around a digit sequence that indicate it is NOT a standalone numeric
# claim (dates, IDs, version strings, etc.).
_DATE_CONTEXT_RE = re.compile(
    r"""
    (?:
        \b\d{4}-\d{2}(?:-\d{2})?   # ISO dates: 2024-01, 2024-01-15
        | \bFY\d{2,4}\b             # fiscal years: FY25, FY2025
        | \bQ[1-4]\b                # quarters: Q1, Q2 …
        | \bv\d+\.\d+               # version: v1.2
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Scale multiplier words that may follow a number in prose.
_SCALE_WORDS: Dict[str, float] = {
    "million": 1_000_000,
    "mn": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "thousand": 1_000,
    "k": 1_000,
    "lakh": 100_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
}

# Master pattern: optional currency prefix, number with optional commas/dots,
# optional scale word, optional % suffix.
# Group names: prefix, intpart, decpart, scale, pct
_CLAIM_RE = re.compile(
    r"""
    (?P<prefix>PKR|Rs\.?|₨)?\s*            # optional currency prefix
    (?P<number>
        (?:\d{1,3}(?:,\d{3})+|\d+)          # integer (with or without commas)
        (?:\.\d+)?                           # optional decimal
    )
    \s*
    (?P<scale>million|mn|billion|bn|thousand|lakh|crore|cr|k)? # optional scale
    \s*
    (?P<pct>%)?                              # optional % suffix
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _extract_claims(narrative: str) -> List[Tuple[str, float, bool, bool]]:
    """
    Scan *narrative* and return a list of (raw_text, value, is_currency, is_percent).

    Limitations (see module docstring for full list):
    - Numbers that are part of date strings are skipped.
    - Scale words like "million" are multiplied in.
    - "%" suffix sets is_percent=True; "PKR/Rs." prefix sets is_currency=True.
    """
    # Remove date-like substrings first so their digits don't get picked up.
    clean = _DATE_CONTEXT_RE.sub(" ", narrative)

    results: List[Tuple[str, float, bool, bool]] = []
    seen_spans: set = set()  # avoid double-counting overlapping matches

    for m in _CLAIM_RE.finditer(clean):
        span = (m.start(), m.end())
        if any(s[0] <= span[0] < s[1] for s in seen_spans):
            continue  # overlapping, skip

        raw_text = m.group(0).strip()
        if not raw_text or not m.group("number"):
            continue

        # Strip commas from the number string before float conversion.
        num_str = m.group("number").replace(",", "")
        try:
            value = float(num_str)
        except ValueError:
            continue

        # Apply scale multiplier.
        scale_word = (m.group("scale") or "").lower()
        if scale_word:
            value *= _SCALE_WORDS.get(scale_word, 1.0)

        is_currency = bool(m.group("prefix"))
        is_percent = bool(m.group("pct"))

        # Heuristic: very small bare integers (≤ 9) that are neither currency
        # nor percent are likely ordinal/incidental and probably not KPI claims.
        # Raise threshold as needed; currently kept conservative.
        if value < 10 and not is_currency and not is_percent:
            continue

        results.append((raw_text, value, is_currency, is_percent))
        seen_spans.add(span)

    return results


# ---------------------------------------------------------------------------
# Tolerance check
# ---------------------------------------------------------------------------

def _within_tolerance(extracted: float, expected: float, unit: str) -> bool:
    """Return True if *extracted* is close enough to *expected* for *unit*."""
    if expected == 0.0:
        # If the ground truth is exactly zero, allow ±TOL_COUNT_ABS leeway.
        return abs(extracted - expected) <= TOL_COUNT_ABS

    if unit == UNIT_CURRENCY:
        return abs(extracted - expected) / abs(expected) <= TOL_CURRENCY_RELATIVE

    if unit == UNIT_PERCENT:
        return abs(extracted - expected) <= TOL_PERCENT_ABS

    # UNIT_COUNT or anything else
    if abs(extracted - expected) <= TOL_COUNT_ABS:
        return True
    return abs(extracted - expected) / abs(expected) <= TOL_COUNT_RELATIVE


# ---------------------------------------------------------------------------
# Candidate matching
# ---------------------------------------------------------------------------

def _find_best_kpi(
    value: float,
    is_currency: bool,
    is_percent: bool,
    kpi_results: Dict[str, Any],
) -> Optional[Tuple[str, float, str]]:
    """
    Scan *kpi_results* and return (key, expected_value, unit) for the KPI
    whose value is closest to *value* AND within the loose proximity window
    used to decide "this claim is probably about this KPI".

    Strategy:
    1. Filter to KPIs whose *unit* is consistent with the text cues
       (is_currency → UNIT_CURRENCY; is_percent → UNIT_PERCENT).
    2. Among those, find the KPI with the smallest relative distance whose
       value is within the "ballpark" window (10× of the tolerance).
    3. Return the best match so the caller can do the tight tolerance check.

    Returns None if no KPI is anywhere near the value.
    """
    # Proximity window is intentionally generous so we can later distinguish
    # "close but wrong" from "no match at all".
    PROXIMITY_FACTOR = 10

    candidates = []
    for key, result in kpi_results.items():
        # Skip unavailable KPIs (value is None — must never be treated as 0).
        result_value = getattr(result, "value", None)
        if result_value is None:
            continue

        unit = getattr(result, "unit", "")

        # Apply unit hint filtering.
        if is_currency and unit != UNIT_CURRENCY:
            continue
        if is_percent and unit != UNIT_PERCENT:
            continue
        # If neither currency nor percent cue, allow any unit.

        expected = float(result_value)
        if expected == 0.0:
            distance = abs(value - expected)
        else:
            distance = abs(value - expected) / abs(expected)

        # Proximity window based on unit.
        if unit == UNIT_CURRENCY:
            threshold = TOL_CURRENCY_RELATIVE * PROXIMITY_FACTOR
        elif unit == UNIT_PERCENT:
            threshold = TOL_PERCENT_ABS * PROXIMITY_FACTOR  # ±5 pp
        else:
            threshold = TOL_COUNT_RELATIVE * PROXIMITY_FACTOR

        if distance <= threshold:
            candidates.append((distance, key, expected, unit))

    if not candidates:
        return None

    # Return the KPI with the smallest distance.
    candidates.sort(key=lambda t: t[0])
    _, best_key, best_expected, best_unit = candidates[0]
    return best_key, best_expected, best_unit


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify(
    narrative: str,
    kpi_results: Dict[str, Any],
) -> VerificationReport:
    """
    Verify *narrative* against *kpi_results* (from KPIEngine.compute_all).

    Returns a :class:`VerificationReport` with one :class:`VerifiedClaim`
    per numeric claim found in the text.

    Parameters
    ----------
    narrative:
        Raw LLM-generated business narrative.
    kpi_results:
        Dict[str, KPIResult] as returned by engine.compute_all(...).  Values
        must be KPIResult instances (or objects with ``value``, ``unit``, and
        ``status`` attributes); plain dicts are not supported.
    """
    claims: List[VerifiedClaim] = []

    for raw_text, extracted_value, is_currency, is_percent in _extract_claims(narrative):
        match = _find_best_kpi(extracted_value, is_currency, is_percent, kpi_results)

        if match is None:
            claims.append(
                VerifiedClaim(
                    matched_text=raw_text,
                    extracted_value=extracted_value,
                    matched_kpi_key=None,
                    status=STATUS_UNMATCHED,
                    expected_value=None,
                    reason=(
                        f"No KPI with a value near {extracted_value} "
                        f"(currency={is_currency}, percent={is_percent}) was found."
                    ),
                )
            )
            continue

        best_key, expected_value, unit = match

        if _within_tolerance(extracted_value, expected_value, unit):
            claims.append(
                VerifiedClaim(
                    matched_text=raw_text,
                    extracted_value=extracted_value,
                    matched_kpi_key=best_key,
                    status=STATUS_VERIFIED,
                    expected_value=expected_value,
                )
            )
        else:
            claims.append(
                VerifiedClaim(
                    matched_text=raw_text,
                    extracted_value=extracted_value,
                    matched_kpi_key=best_key,
                    status=STATUS_MISMATCH,
                    expected_value=expected_value,
                    reason=(
                        f"Extracted {extracted_value:,.4g} but KPI '{best_key}' "
                        f"({unit}) has value {expected_value:,.4g}. "
                        f"Difference exceeds tolerance."
                    ),
                )
            )

    return VerificationReport(claims=claims)


# ---------------------------------------------------------------------------
# __main__ demonstration
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    # ------------------------------------------------------------------
    # Minimal stand-in for KPIResult when running without the full app.
    # ------------------------------------------------------------------
    @dataclass
    class _FakeKPI:
        key: str
        value: Optional[float]
        unit: str
        status: str = "ok"

    def _kpi(key: str, value: Optional[float], unit: str) -> _FakeKPI:
        return _FakeKPI(key=key, value=value, unit=unit)

    # ------------------------------------------------------------------
    # Example 1 — all claims verified
    # ------------------------------------------------------------------
    print("=" * 60)
    print("Example 1: all claims verified")
    print("=" * 60)
    kpis_1 = {
        "total_revenue": _kpi("total_revenue", 1_245_000.0, UNIT_CURRENCY),
        "profit_margin": _kpi("profit_margin", 18.5, UNIT_PERCENT),
        "transaction_count": _kpi("transaction_count", 342.0, UNIT_COUNT),
    }
    narrative_1 = (
        "Total revenue reached PKR 1,245,000 in this period. "
        "The profit margin stood at 18.5%, with 342 transactions recorded."
    )
    report_1 = verify(narrative_1, kpis_1)
    print(json.dumps(report_1.to_dict(), indent=2))

    # ------------------------------------------------------------------
    # Example 2 — one mismatch (LLM fabricated a wrong revenue figure)
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Example 2: one mismatch (LLM hallucinated revenue)")
    print("=" * 60)
    kpis_2 = {
        "total_revenue": _kpi("total_revenue", 1_245_000.0, UNIT_CURRENCY),
        "profit_margin": _kpi("profit_margin", 18.5, UNIT_PERCENT),
    }
    narrative_2 = (
        "Revenue for the quarter was PKR 1,500,000. "  # ← wrong (engine says 1,245,000)
        "Profit margin was 18.5%."
    )
    report_2 = verify(narrative_2, kpis_2)
    print(json.dumps(report_2.to_dict(), indent=2))

    # ------------------------------------------------------------------
    # Example 3 — unavailable KPI must not be treated as zero
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Example 3: KPI unavailable, claim is unmatched (not zero)")
    print("=" * 60)
    kpis_3 = {
        "total_revenue": _kpi("total_revenue", 980_000.0, UNIT_CURRENCY),
        "cogs": _kpi("cogs", None, UNIT_CURRENCY),   # unavailable — value is None
    }
    narrative_3 = (
        "Revenue was PKR 980,000. "
        "Cost of goods sold was PKR 0, which is unbelievable."  # LLM treated unavailable as 0
    )
    report_3 = verify(narrative_3, kpis_3)
    print(json.dumps(report_3.to_dict(), indent=2))
