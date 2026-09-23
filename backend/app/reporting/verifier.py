"""
Module 6.8 — Verified report generator (verifier).

Deterministic, LLM-free verifier.

Role in the pipeline
--------------------
The LLM produces free-text narrative. This module acts as the core safety gate:
it extracts every numeric claim from that text, finds the matching ground-truth
number from `ReportData` or `KPIEngine.compute_all()`, and decides whether the
LLM's number is correct (within tolerance), wrong (mismatch), or refers to
something never computed (unmatched).

Design constraints
------------------
* Pure Python — stdlib only (re, dataclasses, math, typing).
* No network calls, no Ollama, no pandas.
* Deterministic: same inputs → identical VerificationReport every time.
* Checks against rich KPIResult objects, breakdown tables, forecasts, and expiry buckets.
* Small non-financial integers (<= 9) are ignored to avoid false positives on ordinals/counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from app.analytics.models import KPIResult, UNIT_CURRENCY, UNIT_PERCENT, UNIT_COUNT
except ImportError:
    KPIResult = None  # type: ignore[assignment,misc]
    UNIT_CURRENCY = "PKR"
    UNIT_PERCENT = "percent"
    UNIT_COUNT = "count"

try:
    from app.reporting.models import ReportData
except ImportError:
    ReportData = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Tolerance constants
# ---------------------------------------------------------------------------

# Currency (PKR): allow ±1 % relative error to absorb rounding in prose.
TOL_CURRENCY_RELATIVE: float = 0.01

# Percent KPIs: allow ±0.5 percentage points absolute error.
TOL_PERCENT_ABS: float = 0.5

# Count KPIs: allow ±1 absolute error or ±1 % relative error.
TOL_COUNT_ABS: int = 1
TOL_COUNT_RELATIVE: float = 0.01

# ---------------------------------------------------------------------------
# Status literals
# ---------------------------------------------------------------------------
STATUS_VERIFIED = "verified"
STATUS_UNMATCHED = "unmatched"
STATUS_MISMATCH = "mismatch"


@dataclass
class VerifiedClaim:
    """One numeric claim found in the LLM narrative, with its verdict."""

    # The raw substring that contained the number, e.g. "PKR 1,234,567.89"
    matched_text: str

    # Normalised float value extracted from `matched_text`
    extracted_value: float

    # Key/label of the best-matching ground truth figure, or None if unmatched
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
    """Aggregate result of verifying one narrative against ground truth."""

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

# Words around a digit sequence that indicate it is NOT a standalone numeric claim
_DATE_CONTEXT_RE = re.compile(
    r"""
    (?:
        \b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{2,4}\b # Month Year: January 2026
        | \b\d{4}-\d{2}(?:-\d{2})?   # ISO dates: 2024-01, 2024-01-15
        | \b\d{1,2}/\d{1,2}/\d{2,4}\b # Date format: 01/15/2026
        | \b(?:19\d{2}|20\d{2})\b     # Standalone years: 1990–2099
        | \b\d+\s*[-–]?\s*(?:days?|months?|years?|weeks?|hours?)\b # Durations/Windows: 30 days, 60-day
        | \bFY\d{2,4}\b             # fiscal years: FY25, FY2025
        | \bQ[1-4]\b                # quarters: Q1, Q2 …
        | \bv\d+\.\d+               # version: v1.2
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Scale multiplier words
_SCALE_WORDS: Dict[str, float] = {
    "million": 1_000_000,
    "mn": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
    "thousand": 1_000,
    "k": 1_000,
    "lakh": 100_000,
    "lac": 100_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
}

# Regex pattern for numeric claims
_CLAIM_RE = re.compile(
    r"""
    (?P<prefix>PKR|Rs\.?|₨|\$)?\s*          # optional currency prefix
    (?P<number>
        (?:\d{1,3}(?:,\d{3})+|\d+)          # integer (with or without commas)
        (?:\.\d+)?                           # optional decimal
    )
    \s*
    (?P<scale>million|mn|billion|bn|thousand|lakh|lac|crore|cr|k)? # optional scale
    \s*
    (?P<pct>%)?                              # optional % suffix
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _extract_claims(narrative: str) -> List[Tuple[str, float, bool, bool]]:
    """
    Scan *narrative* and return a list of (raw_text, value, is_currency, is_percent).
    """
    clean = _DATE_CONTEXT_RE.sub(" ", narrative)
    results: List[Tuple[str, float, bool, bool]] = []
    seen_spans: set = set()

    for m in _CLAIM_RE.finditer(clean):
        span = (m.start(), m.end())
        if any(s[0] <= span[0] < s[1] for s in seen_spans):
            continue

        raw_text = m.group(0).strip()
        if not raw_text or not m.group("number"):
            continue

        num_str = m.group("number").replace(",", "")
        try:
            value = float(num_str)
        except ValueError:
            continue

        scale_word = (m.group("scale") or "").lower()
        if scale_word:
            value *= _SCALE_WORDS.get(scale_word, 1.0)

        is_currency = bool(m.group("prefix"))
        is_percent = bool(m.group("pct"))

        # Heuristic: small bare integers (<= 9) that are neither currency nor percent
        # are likely ordinals ("step 1", "top 3") and excluded from financial verification.
        if value < 10 and not is_currency and not is_percent:
            continue

        results.append((raw_text, value, is_currency, is_percent))
        seen_spans.add(span)

    return results


def _within_tolerance(extracted: float, expected: float, unit: str) -> bool:
    """Return True if extracted is close enough to expected for unit."""
    if expected == 0.0:
        return abs(extracted - expected) <= TOL_COUNT_ABS

    if unit in (UNIT_CURRENCY, "PKR", "Rs", "currency"):
        return abs(extracted - expected) / abs(expected) <= TOL_CURRENCY_RELATIVE

    if unit in (UNIT_PERCENT, "percent", "%"):
        return abs(extracted - expected) <= TOL_PERCENT_ABS

    # UNIT_COUNT or generic
    if abs(extracted - expected) <= TOL_COUNT_ABS:
        return True
    return abs(extracted - expected) / abs(expected) <= TOL_COUNT_RELATIVE


def _extract_ground_truth_entries(ground_truth: Union[Dict[str, Any], Any]) -> List[Tuple[str, float, str]]:
    """Extract (label, value, unit) tuples from ReportData or a KPI dict."""
    if hasattr(ground_truth, "get_all_verifiable_numbers"):
        return ground_truth.get_all_verifiable_numbers()

    entries: List[Tuple[str, float, str]] = []
    if isinstance(ground_truth, dict):
        for key, res in ground_truth.items():
            val = getattr(res, "value", None)
            if val is None and isinstance(res, dict):
                val = res.get("value")
            unit = getattr(res, "unit", "") or (res.get("unit", "") if isinstance(res, dict) else "")

            if val is not None:
                try:
                    entries.append((key, float(val), unit))
                except (ValueError, TypeError):
                    pass

            # Check breakdowns
            breakdown = getattr(res, "breakdown", None)
            if breakdown is None and isinstance(res, dict):
                breakdown = res.get("breakdown")
            if isinstance(breakdown, list):
                for i, row in enumerate(breakdown):
                    if isinstance(row, dict):
                        for col, v in row.items():
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                row_label = str(row.get("name") or row.get("product_id") or row.get("category") or f"{key}_{i}")
                                entries.append((f"{key}:{row_label}:{col}", float(v), unit or UNIT_CURRENCY))

            # Check forecast series
            forecast = getattr(res, "forecast", None)
            if forecast is None and isinstance(res, dict):
                forecast = res.get("forecast")
            if isinstance(forecast, list):
                for pt in forecast:
                    if isinstance(pt, dict):
                        period_label = str(pt.get("period") or "")
                        for f_field, f_val in pt.items():
                            if f_field in ("estimate", "lower", "upper", "value") and isinstance(f_val, (int, float)):
                                entries.append((f"{key}:forecast:{period_label}:{f_field}", float(f_val), unit or UNIT_CURRENCY))

    return entries


def _find_best_match(
    value: float,
    is_currency: bool,
    is_percent: bool,
    ground_truth_entries: List[Tuple[str, float, str]],
) -> Optional[Tuple[str, float, str]]:
    """Find the ground truth candidate with smallest distance to extracted value."""
    PROXIMITY_FACTOR = 10.0
    candidates = []

    for key, expected, unit in ground_truth_entries:
        # Unit filtering
        if is_currency and unit not in (UNIT_CURRENCY, "PKR", "Rs", "currency"):
            continue
        if is_percent and unit not in (UNIT_PERCENT, "percent", "%"):
            continue

        if expected == 0.0:
            distance = abs(value - expected)
        else:
            distance = abs(value - expected) / abs(expected)

        # Proximity window based on unit
        if unit in (UNIT_CURRENCY, "PKR", "Rs", "currency"):
            threshold = TOL_CURRENCY_RELATIVE * PROXIMITY_FACTOR
        elif unit in (UNIT_PERCENT, "percent", "%"):
            threshold = TOL_PERCENT_ABS * PROXIMITY_FACTOR
        else:
            threshold = TOL_COUNT_RELATIVE * PROXIMITY_FACTOR

        if distance <= threshold:
            candidates.append((distance, key, expected, unit))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0])
    _, best_key, best_expected, best_unit = candidates[0]
    return best_key, best_expected, best_unit


def verify(
    narrative: str,
    kpi_results: Union[Dict[str, Any], Any],
) -> VerificationReport:
    """
    Verify *narrative* against *kpi_results* or *ReportData*.

    Returns a VerificationReport with one VerifiedClaim per numeric claim.
    """
    ground_truth_entries = _extract_ground_truth_entries(kpi_results)
    claims: List[VerifiedClaim] = []

    for raw_text, extracted_value, is_currency, is_percent in _extract_claims(narrative):
        match = _find_best_match(extracted_value, is_currency, is_percent, ground_truth_entries)

        if match is None:
            claims.append(
                VerifiedClaim(
                    matched_text=raw_text,
                    extracted_value=extracted_value,
                    matched_kpi_key=None,
                    status=STATUS_UNMATCHED,
                    expected_value=None,
                    reason=(
                        f"No ground-truth figure near {extracted_value:,.4g} "
                        f"(currency={is_currency}, percent={is_percent}) was found in computed analytics."
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
                        f"Extracted {extracted_value:,.4g} but '{best_key}' "
                        f"({unit}) has value {expected_value:,.4g}. "
                        f"Difference exceeds allowed tolerance."
                    ),
                )
            )

    return VerificationReport(claims=claims)
