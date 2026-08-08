"""
Module 6.6 (KPI Engine) — models.py

Typed, JSON-serializable result types for every computed figure.

Contract for this whole module:
  * Deterministic — same input DataFrame + filters produce byte-identical output.
  * LLM-free — nothing in `app.analytics` imports or calls the LLM. Code computes,
    the LLM only narrates the result produced here.
  * Offline — pure pandas on CPU, no network, no GPU.
  * Traceable — every KPIResult carries a Provenance describing the filter applied,
    how many canonical rows contributed, and which `source_row` values they came from.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Status values a KPIResult can carry.
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"

# Units used across the core KPI pack.
UNIT_CURRENCY = "PKR"
UNIT_PERCENT = "percent"
UNIT_COUNT = "count"


@dataclass(frozen=True)
class Period:
    """The date range actually covered by the contributing rows."""

    start: Optional[str] = None  # ISO date, e.g. "2026-01-05"
    end: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class Provenance:
    """
    Where a number came from.

    This is a first-class output, not a debugging aid: it is what lets a report
    or a chat answer prove which rows of the user's own file produced a figure.

    Attributes:
        filters:            The filter criteria applied, as a plain dict.
        filter_description: Human-readable rendering of `filters`.
        row_count:          Number of canonical rows that contributed.
        source_rows:        `source_row` values of contributing rows, sorted and
                            capped at `max_rows` (see `source_rows_truncated`).
        source_rows_truncated: True when `source_rows` is a sample, not the full set.
        sources:            Distinct origin labels (`source_file` when the column is
                            present, else `source_connector`).
        columns_used:       Canonical columns the computation actually read.
        assumptions:        Explicit, auditable notes about anything the engine had
                            to assume (e.g. a missing column, a derived amount).
                            Never leave a material assumption implicit.
    """

    filters: Dict[str, Any] = field(default_factory=dict)
    filter_description: str = "no filter (all rows)"
    row_count: int = 0
    source_rows: List[int] = field(default_factory=list)
    source_rows_truncated: bool = False
    sources: List[str] = field(default_factory=list)
    columns_used: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filters": dict(self.filters),
            "filter_description": self.filter_description,
            "row_count": int(self.row_count),
            "source_rows": [int(r) for r in self.source_rows],
            "source_rows_truncated": bool(self.source_rows_truncated),
            "sources": list(self.sources),
            "columns_used": list(self.columns_used),
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class KPIResult:
    """
    One computed figure plus everything needed to audit it.

    A result with `status == "unavailable"` has `value is None` and a populated
    `reason`. That is deliberately distinct from a genuine zero: a KPI that cannot
    be computed must never masquerade as 0.
    """

    key: str
    name: str
    value: Optional[float]
    unit: str
    formula: str
    provenance: Provenance
    status: str = STATUS_OK
    reason: Optional[str] = None
    period: Optional[Period] = None
    breakdown: Optional[List[Dict[str, Any]]] = None
    breakdown_columns: Optional[List[str]] = None

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> Dict[str, Any]:
        """Plain-Python, JSON-serializable form (API, report generator, chatbot)."""
        return {
            "key": self.key,
            "name": self.name,
            "value": None if self.value is None else float(self.value),
            "unit": self.unit,
            "formula": self.formula,
            "status": self.status,
            "reason": self.reason,
            "period": self.period.to_dict() if self.period else None,
            "breakdown": self.breakdown,
            "breakdown_columns": self.breakdown_columns,
            "provenance": self.provenance.to_dict(),
        }


def unavailable(
    key: str,
    name: str,
    unit: str,
    formula: str,
    reason: str,
    provenance: Optional[Provenance] = None,
) -> KPIResult:
    """Build a clearly-marked unavailable result. Used instead of crashing or returning 0."""
    return KPIResult(
        key=key,
        name=name,
        value=None,
        unit=unit,
        formula=formula,
        provenance=provenance or Provenance(),
        status=STATUS_UNAVAILABLE,
        reason=reason,
    )
