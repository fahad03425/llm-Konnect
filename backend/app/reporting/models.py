"""
Module 6.8 (Verified Report Generator) — models.py

Typed data structures for Module 6.8.
Enforces the core contract:
"Code computes. The LLM narrates. A verifier checks."

`ReportData` is the SINGLE SOURCE OF TRUTH for everything rendered in a report
(charts, tables, AI narrative grounding, claim verification, and audit trail).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from app.analytics.models import KPIResult, Period, UNIT_CURRENCY, UNIT_PERCENT, UNIT_COUNT
except ImportError:
    KPIResult = Any  # type: ignore[assignment,misc]
    Period = Any     # type: ignore[assignment,misc]
    UNIT_CURRENCY = "PKR"
    UNIT_PERCENT = "percent"
    UNIT_COUNT = "count"


@dataclass
class ExecutiveMetrics:
    """Pre-aggregated executive dashboard figures."""
    total_revenue: float = 0.0
    total_invoices: int = 0
    avg_bill_value: float = 0.0
    unique_customers: int = 0
    unique_products_count: int = 0
    yoy_growth_pct: Optional[float] = None
    discounts_total: float = 0.0
    discounts_pct: float = 0.0
    outstanding_balance: float = 0.0
    line_items_count: int = 0
    branches_list: List[str] = field(default_factory=list)
    cashiers_list: List[str] = field(default_factory=list)
    reporting_period: str = "January 2024 – December 2025"
    source_filename: str = "Dataset.xlsx"


@dataclass
class ReportData:
    """
    Consolidated single source of truth for an analytics report.

    Attributes
    ----------
    kpis:
        Dict[str, KPIResult] produced by KPIEngine.compute_all().
    domain:
        The active business domain (e.g. "pharmacy", "retail").
    business_name:
        Display name of the store/business (e.g. "Al-Shifa Family Pharmacy").
    period:
        Date range covered by the source data.
    filters:
        Dictionary of active filters applied to the dataset.
    anomalies:
        Optional list of anomaly dictionaries from Module 6.7.
    sections:
        List of section keys to render (core + domain-pack contributed).
    generated_at:
        Timestamp when the data was gathered.
    executive_metrics:
        Rich dimensional aggregates for multi-section executive reports.
    branch_performance:
        List of branch revenue, invoice count, and avg bill.
    monthly_trend:
        List of monthly revenue records (with year/month).
    payment_mix:
        List of revenue share by client/payment type.
    top_products:
        Top selling products by revenue.
    slow_products:
        Lowest revenue products.
    hourly_traffic:
        Transaction count by hour of day.
    daily_traffic:
        Revenue by day of week.
    cashier_performance:
        Revenue and bills per cashier.
    top_debtors:
        Top customers by outstanding balance.
    """

    kpis: Dict[str, Any] = field(default_factory=dict)
    domain: str = "pharmacy"
    business_name: str = "Pharmacy Performance Report"
    period: Optional[Any] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    anomalies: Optional[List[Dict[str, Any]]] = None
    sections: List[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)

    # Rich dimensional payloads
    executive_metrics: ExecutiveMetrics = field(default_factory=ExecutiveMetrics)
    branch_performance: List[Dict[str, Any]] = field(default_factory=list)
    monthly_trend: List[Dict[str, Any]] = field(default_factory=list)
    payment_mix: List[Dict[str, Any]] = field(default_factory=list)
    top_products: List[Dict[str, Any]] = field(default_factory=list)
    slow_products: List[Dict[str, Any]] = field(default_factory=list)
    hourly_traffic: List[Dict[str, Any]] = field(default_factory=list)
    daily_traffic: List[Dict[str, Any]] = field(default_factory=list)
    cashier_performance: List[Dict[str, Any]] = field(default_factory=list)
    top_debtors: List[Dict[str, Any]] = field(default_factory=list)

    def get_kpi(self, key: str) -> Optional[Any]:
        """Fetch a KPI by key, returning None if missing."""
        return self.kpis.get(key)

    def get_headline_kpis(self) -> Dict[str, Any]:
        """Return primary financial and operational headline KPIs."""
        headline_keys = [
            "total_revenue",
            "net_profit",
            "gross_profit",
            "gross_margin_pct",
            "net_margin_pct",
            "total_expenses",
            "total_refunds",
            "refund_rate_pct",
            "transaction_count",
            "average_transaction_value",
            "near_expiry_total",
            "expired_stock_value",
        ]
        return {k: self.kpis[k] for k in headline_keys if k in self.kpis}

    def get_expiry_kpis(self) -> Dict[str, Any]:
        """Return pharmacy expiry-specific KPIs if present."""
        expiry_keys = [
            "near_expiry_total",
            "near_expiry_item_count",
            "expired_stock_value",
            "expired_item_count",
            "expiring_value_30d",
            "expiring_value_60d",
            "expiring_value_90d",
            "expiring_items_30d",
            "expiring_items_60d",
            "expiring_items_90d",
        ]
        return {k: self.kpis[k] for k in expiry_keys if k in self.kpis}

    def get_forecast_kpi(self) -> Optional[Any]:
        """Return the primary revenue or demand forecast KPI if present and available."""
        res = self.kpis.get("revenue_forecast") or self.kpis.get("demand_forecast")
        if res and getattr(res, "is_available", getattr(res, "status", "") == "ok"):
            return res
        return None

    def get_all_verifiable_numbers(self) -> List[Tuple[str, float, str]]:
        """
        Extract every valid ground-truth number computed in this ReportData.
        Returns a list of (context_label, value, unit) tuples.
        """
        ground_truth: List[Tuple[str, float, str]] = []

        # 1. Top-level KPIs
        for key, res in self.kpis.items():
            val = getattr(res, "value", None)
            if val is None and isinstance(res, dict):
                val = res.get("value")
            unit = getattr(res, "unit", "") or (res.get("unit", "") if isinstance(res, dict) else "")

            if val is not None:
                try:
                    ground_truth.append((key, float(val), unit))
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
                                ground_truth.append((f"{key}:{row_label}:{col}", float(v), unit or UNIT_CURRENCY))

            # Check forecasts
            forecast = getattr(res, "forecast", None)
            if forecast is None and isinstance(res, dict):
                forecast = res.get("forecast")
            if isinstance(forecast, list):
                for pt in forecast:
                    if isinstance(pt, dict):
                        period_label = str(pt.get("period") or "")
                        for f_field, f_val in pt.items():
                            if f_field in ("estimate", "lower", "upper", "value") and isinstance(f_val, (int, float)):
                                ground_truth.append((f"{key}:forecast:{period_label}:{f_field}", float(f_val), unit or UNIT_CURRENCY))

        # 2. Executive Metrics numbers
        em = self.executive_metrics
        if em.total_revenue > 0:
            ground_truth.append(("executive_revenue", float(em.total_revenue), UNIT_CURRENCY))
        if em.total_invoices > 0:
            ground_truth.append(("executive_invoices", float(em.total_invoices), UNIT_COUNT))
        if em.avg_bill_value > 0:
            ground_truth.append(("executive_avg_bill", float(em.avg_bill_value), UNIT_CURRENCY))
        if em.unique_customers > 0:
            ground_truth.append(("executive_customers", float(em.unique_customers), UNIT_COUNT))
        if em.unique_products_count > 0:
            ground_truth.append(("executive_products", float(em.unique_products_count), UNIT_COUNT))
        if em.yoy_growth_pct is not None:
            ground_truth.append(("executive_growth", float(em.yoy_growth_pct), UNIT_PERCENT))
        if em.discounts_total > 0:
            ground_truth.append(("executive_discounts", float(em.discounts_total), UNIT_CURRENCY))
        if em.discounts_pct > 0:
            ground_truth.append(("executive_discounts_pct", float(em.discounts_pct), UNIT_PERCENT))
        if em.outstanding_balance > 0:
            ground_truth.append(("executive_balance", float(em.outstanding_balance), UNIT_CURRENCY))

        # 3. Branch performance numbers
        for bp in self.branch_performance:
            b_name = str(bp.get("branch", "branch"))
            if "revenue" in bp:
                ground_truth.append((f"branch:{b_name}:revenue", float(bp["revenue"]), UNIT_CURRENCY))
            if "invoices" in bp:
                ground_truth.append((f"branch:{b_name}:invoices", float(bp["invoices"]), UNIT_COUNT))
            if "avg_bill" in bp:
                ground_truth.append((f"branch:{b_name}:avg_bill", float(bp["avg_bill"]), UNIT_CURRENCY))

        # 4. Top products numbers
        for tp in self.top_products:
            p_name = str(tp.get("name", "prod"))
            if "amount" in tp:
                ground_truth.append((f"product:{p_name}:revenue", float(tp["amount"]), UNIT_CURRENCY))

        # 5. Top debtors numbers
        for td in self.top_debtors:
            c_name = str(td.get("customer", "cust"))
            if "balance" in td:
                ground_truth.append((f"debtor:{c_name}:balance", float(td["balance"]), UNIT_CURRENCY))

        # 6. Anomalies count
        if self.anomalies is not None:
            ground_truth.append(("anomaly_count", float(len(self.anomalies)), UNIT_COUNT))

        return ground_truth

    def to_dict(self) -> Dict[str, Any]:
        """Serialize ReportData to a plain JSON-compatible dictionary."""
        serialized_kpis: Dict[str, Any] = {}
        for k, v in self.kpis.items():
            if hasattr(v, "to_dict"):
                serialized_kpis[k] = v.to_dict()
            elif isinstance(v, dict):
                serialized_kpis[k] = v
            else:
                serialized_kpis[k] = str(v)

        return {
            "domain": self.domain,
            "business_name": self.business_name,
            "period": self.period.to_dict() if hasattr(self.period, "to_dict") else self.period,
            "filters": self.filters,
            "anomalies": self.anomalies,
            "sections": self.sections,
            "generated_at": self.generated_at.isoformat(),
            "kpi_count": len(self.kpis),
            "kpis": serialized_kpis,
            "branch_performance": self.branch_performance,
            "top_products": self.top_products,
            "cashier_performance": self.cashier_performance,
            "top_debtors": self.top_debtors,
        }
