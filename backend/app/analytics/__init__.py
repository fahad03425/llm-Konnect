"""
Module 6.6 — Financial Analytics / KPI Engine.

The single source of numeric truth for LLM-Konnect. Deterministic pandas only:
code computes, the LLM narrates, a verifier checks. Nothing in this package
imports or calls the LLM, and nothing here touches the network.

Public surface:
    KPIResult, Provenance, Period   — the typed, JSON-serializable result contract
    KPIFilters                      — the slice a KPI pack is computed over
    KPIEngine, KPISpec, engine      — registry + orchestrator (`engine` is process-wide)
    AnalyticsRouter                 — the seam Module 6.5's numeric chat route calls
"""

from app.analytics.engine import KPIEngine, KPISpec, engine
from app.analytics.filters import KPIFilters, apply_filters
from app.analytics.models import (
    STATUS_OK,
    STATUS_UNAVAILABLE,
    UNIT_COUNT,
    UNIT_CURRENCY,
    UNIT_PERCENT,
    KPIResult,
    Period,
    Provenance,
)
from app.analytics.seam import AnalyticsRouter, compute_for_question, results_to_payload

__all__ = [
    "KPIEngine",
    "KPISpec",
    "engine",
    "KPIFilters",
    "apply_filters",
    "KPIResult",
    "Period",
    "Provenance",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "UNIT_CURRENCY",
    "UNIT_PERCENT",
    "UNIT_COUNT",
    "AnalyticsRouter",
    "compute_for_question",
    "results_to_payload",
]
