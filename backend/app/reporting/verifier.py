"""Verify structured fact identifiers against deterministic report data.

Free-form numeric prose fails closed: numeric proximity cannot verify a claim.
"""

from __future__ import annotations

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


def verify(
    narrative: str,
    kpi_results: Union[Dict[str, Any], Any],
) -> VerificationReport:
    """
    Verify *narrative* against *kpi_results* or *ReportData*.

    Returns a VerificationReport with one VerifiedClaim per numeric claim.
    """
    from app.reporting.grounding import GroundedNarrative, fact_catalog, render, has_quantity

    if isinstance(narrative, GroundedNarrative):
        try:
            if narrative.error:
                raise ValueError(narrative.error)
            catalog = fact_catalog(kpi_results)
            expected = render(narrative.fact_ids, narrative.commentary, catalog)
            if str(narrative) != expected:
                raise ValueError("Narrative differs from its grounded facts")
            return VerificationReport(claims=[
                VerifiedClaim(key, catalog[key][0], key, STATUS_VERIFIED, catalog[key][0])
                for key in narrative.fact_ids
            ])
        except (ValueError, TypeError, KeyError) as exc:
            return VerificationReport(claims=[VerifiedClaim(
                str(narrative), 0.0, None, STATUS_MISMATCH, reason=str(exc)
            )])

    # Legacy free-form numeric prose cannot establish metric/entity/period
    # identity. Fail closed instead of matching a convenient nearby number.
    if has_quantity(narrative):
        return VerificationReport(claims=[
            VerifiedClaim(
                narrative, 0.0, None, STATUS_MISMATCH,
                reason="Numeric prose has no structured fact identifiers; regenerate using the fact catalog."
            ),
            VerifiedClaim(
                narrative, 0.0, None, STATUS_UNMATCHED,
                reason="Numeric prose has no structured fact identifiers; regenerate using the fact catalog."
            )
        ])
    return VerificationReport()
