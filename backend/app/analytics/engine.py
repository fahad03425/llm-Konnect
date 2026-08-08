"""
Module 6.6 (KPI Engine) — engine.py

The registry + orchestrator. `KPIEngine` is the single source of numeric truth for
the whole application: the report generator and the chatbot's numeric route both
consume it, so a figure has exactly one definition no matter who asks.

Deterministic, LLM-free, offline, and traceable — see models.Provenance.

Adding a KPI is ADDITIVE: call `engine.register(KPISpec(...))`. Domain-specific
KPIs pass `domain="<pack name>"` and are then included in `compute_all` for that
domain only. Core code never needs editing to add one.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics import kpi as core_kpis
from app.analytics.filters import KPIFilters, apply_filters
from app.analytics.models import KPIResult, Provenance, unavailable

# A KPI computation: (canonical_df, filters, domain) -> KPIResult
ComputeFn = Callable[[pd.DataFrame, KPIFilters, str], KPIResult]


@dataclass(frozen=True)
class KPISpec:
    """Registration record for one KPI."""

    key: str
    name: str
    unit: str
    definition: str
    fn: ComputeFn
    domain: Optional[str] = None  # None = core / domain-agnostic
    tags: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "unit": self.unit,
            "definition": self.definition,
            "domain": self.domain,
            "tags": list(self.tags),
        }


# The core pack, in the order `compute_all` returns it.
_CORE_SPECS: List[KPISpec] = [
    KPISpec("total_revenue", "Total Revenue", "PKR",
            "Sum of amount over sale rows.", core_kpis.total_revenue, tags=("money", "headline")),
    KPISpec("total_expenses", "Total Expenses", "PKR",
            "Sum of amount over expense/purchase rows.", core_kpis.total_expenses, tags=("money", "headline")),
    KPISpec("total_refunds", "Total Refunds", "PKR",
            "Sum of |amount| over refund/return rows.", core_kpis.total_refunds, tags=("money",)),
    KPISpec("net_profit", "Net Profit", "PKR",
            "total_revenue - total_expenses - total_refunds.", core_kpis.net_profit, tags=("money", "headline")),
    KPISpec("gross_profit", "Gross Profit", "PKR",
            "Sale revenue minus COGS (cost x quantity), over sale rows with a known cost.",
            core_kpis.gross_profit, tags=("money", "headline")),
    KPISpec("gross_margin_pct", "Gross Margin %", "percent",
            "gross_profit / revenue of costed sale rows x 100.", core_kpis.gross_margin_pct, tags=("ratio",)),
    KPISpec("net_margin_pct", "Net Margin %", "percent",
            "net_profit / total_revenue x 100.", core_kpis.net_margin_pct, tags=("ratio",)),
    KPISpec("refund_rate_pct", "Refund Rate %", "percent",
            "total_refunds / total_revenue x 100.", core_kpis.refund_rate_pct, tags=("ratio",)),
    KPISpec("transaction_count", "Transaction Count", "count",
            "Distinct invoice_id over sale rows, or sale row count when invoice_id is absent.",
            core_kpis.transaction_count, tags=("volume",)),
    KPISpec("average_transaction_value", "Average Transaction Value", "PKR",
            "total_revenue / transaction_count.", core_kpis.average_transaction_value, tags=("money",)),
    KPISpec("expense_breakdown_by_category", "Expense Breakdown by Category", "PKR",
            "Expense amount grouped by category.", core_kpis.expense_breakdown_by_category,
            tags=("breakdown",)),
    KPISpec("expense_breakdown_by_supplier", "Expense Breakdown by Supplier", "PKR",
            "Expense amount grouped by supplier_id.", core_kpis.expense_breakdown_by_supplier,
            tags=("breakdown",)),
    KPISpec("revenue_breakdown_by_category", "Revenue Breakdown by Category", "PKR",
            "Sale amount grouped by category.", core_kpis.revenue_breakdown_by_category,
            tags=("breakdown",)),
    KPISpec("revenue_breakdown_by_product", "Revenue Breakdown by Product", "PKR",
            "Sale amount grouped by product_id, top-N by amount.", core_kpis.revenue_breakdown_by_product,
            tags=("breakdown",)),
    KPISpec("revenue_by_month", "Revenue by Month", "PKR",
            "Sale amount grouped by calendar month. A historical aggregation, not a forecast.",
            core_kpis.revenue_by_month, tags=("breakdown", "time")),
]


class KPIEngine:
    """
    Computes registered KPIs over a canonical, validated DataFrame.

    The input is the canonical frame produced by `app.schema` (apply_mapping +
    validate). Filters are applied once, up front, so every KPI in a pack covers
    an identical slice, and the slice is echoed in each result's provenance.
    """

    def __init__(self, register_core: bool = True) -> None:
        self._specs: Dict[str, KPISpec] = {}
        self._order: List[str] = []
        if register_core:
            for spec in _CORE_SPECS:
                self.register(spec)

    # -- registry ---------------------------------------------------------

    def register(self, spec: KPISpec, replace: bool = False) -> None:
        """
        Add a KPI. Domain packs call this to attach domain-specific KPIs without
        any edit to core code.
        """
        if spec.key in self._specs and not replace:
            raise ValueError(f"KPI '{spec.key}' is already registered; pass replace=True to override.")
        if spec.key not in self._specs:
            self._order.append(spec.key)
        self._specs[spec.key] = spec

    def list_kpis(self, domain: Optional[str] = None) -> List[KPISpec]:
        """
        Registered KPIs in registration order: core KPIs always, plus the ones
        registered for `domain` when given.
        """
        return [
            self._specs[k]
            for k in self._order
            if self._specs[k].domain is None or (domain is not None and self._specs[k].domain == domain)
        ]

    def has(self, key: str) -> bool:
        return key in self._specs

    def get_spec(self, key: str) -> Optional[KPISpec]:
        return self._specs.get(key)

    # -- computation ------------------------------------------------------

    def _prepare(
        self, df: Optional[pd.DataFrame], filters: Optional[KPIFilters]
    ) -> Tuple[pd.DataFrame, KPIFilters, List[str]]:
        """Normalize inputs and apply the filter slice once."""
        active = filters or KPIFilters()
        if df is None:
            return pd.DataFrame(), active, ["no DataFrame was supplied"]
        sliced, notes = apply_filters(df, active)
        return sliced, active, notes

    def compute(
        self,
        key: str,
        df: pd.DataFrame,
        filters: Optional[KPIFilters] = None,
        domain: str = "",
    ) -> KPIResult:
        """
        Compute one registered KPI.

        Never raises for data reasons: an unknown column, an empty slice, or a
        zero denominator all come back as `status="unavailable"` with a reason.
        An unregistered key is a programming error and does raise KeyError.
        """
        spec = self._specs.get(key)
        if spec is None:
            raise KeyError(f"Unknown KPI key '{key}'. Registered: {sorted(self._specs)}")

        sliced, active, filter_notes = self._prepare(df, filters)

        if sliced.empty:
            reason = (
                "no rows match the requested filter"
                if not active.is_empty()
                else "the dataset contains no rows"
            )
            return unavailable(
                spec.key, spec.name, spec.unit, spec.definition, reason,
                Provenance(
                    filters=active.as_dict(),
                    filter_description=active.describe(),
                    row_count=0,
                    assumptions=filter_notes,
                ),
            )

        result = spec.fn(sliced, active, domain)

        if filter_notes:
            merged = list(result.provenance.assumptions)
            for note in filter_notes:
                if note not in merged:
                    merged.append(note)
            result = KPIResult(
                key=result.key, name=result.name, value=result.value, unit=result.unit,
                formula=result.formula,
                provenance=Provenance(
                    filters=result.provenance.filters,
                    filter_description=result.provenance.filter_description,
                    row_count=result.provenance.row_count,
                    source_rows=result.provenance.source_rows,
                    source_rows_truncated=result.provenance.source_rows_truncated,
                    sources=result.provenance.sources,
                    columns_used=result.provenance.columns_used,
                    assumptions=merged,
                ),
                status=result.status, reason=result.reason, period=result.period,
                breakdown=result.breakdown, breakdown_columns=result.breakdown_columns,
            )
        return result

    def compute_all(
        self,
        df: pd.DataFrame,
        filters: Optional[KPIFilters] = None,
        domain: str = "",
    ) -> Dict[str, KPIResult]:
        """
        Compute the full KPI pack — core KPIs plus any registered for `domain`.

        Returns an insertion-ordered dict keyed by KPI key. Order is stable, so
        two runs over the same data produce identical output.
        """
        return {spec.key: self.compute(spec.key, df, filters, domain) for spec in self.list_kpis(domain)}


# Process-wide engine. Domain packs and future modules register onto this one.
engine = KPIEngine()
