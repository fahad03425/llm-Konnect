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

from app.core.config import get_default_domain


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
    reporting_period: str = "Date range unavailable"
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
    domain: str = field(default_factory=get_default_domain)
    business_name: str = "Business Performance Report"
    report_title: str = "Business Performance & Growth Report"
    period_comparison: Dict[str, Any] = field(default_factory=dict)
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

    # Extended pharmacy credit, inventory, and margin fields
    supplier_payables: List[Dict[str, Any]] = field(default_factory=list)
    dead_stock_items: List[Dict[str, Any]] = field(default_factory=list)
    category_margins: List[Dict[str, Any]] = field(default_factory=list)
    reorder_alerts: List[Dict[str, Any]] = field(default_factory=list)
    category_trend_note: Optional[str] = None

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
            "supplier_payable_total",
            "dead_stock_value",
        ]
        return {k: self.kpis[k] for k in headline_keys if k in self.kpis}

    def get_display_kpis(self, limit: int = 8) -> List[Tuple[str, str]]:
        """Return only computed, available KPI values for the report summary."""
        priority = [
            "total_revenue", "gross_profit", "gross_margin_pct",
            "transaction_count", "average_transaction_value", "near_expiry_total",
            "expired_stock_value", "supplier_payable_total", "dead_stock_value",
            "refund_rate_pct", "low_stock_item_count",
        ]
        # Keep headline cards interpretable and owner-relevant. In particular,
        # purchases are not operating expenses and must not appear as net profit.
        ordered = [key for key in priority if key in self.kpis]
        cards: List[Tuple[str, str]] = []
        for key in ordered:
            if key in {"net_profit", "net_margin_pct"}:
                continue
            result = self.kpis[key]
            value = getattr(result, "value", None) if not isinstance(result, dict) else result.get("value")
            status = getattr(result, "status", "ok") if not isinstance(result, dict) else result.get("status", "ok")
            if value is None or status != "ok":
                continue
            name = getattr(result, "name", key.replace("_", " ").title()) if not isinstance(result, dict) else result.get("name", key.replace("_", " ").title())
            unit = getattr(result, "unit", "") if not isinstance(result, dict) else result.get("unit", "")
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if unit == "PKR":
                display = f"PKR {number:,.0f}"
            elif unit == "percent":
                display = f"{number:.1f}%"
            else:
                display = f"{number:,.2f}".rstrip("0").rstrip(".")
            cards.append((str(name), display))
            if len(cards) >= limit:
                break
        return cards

    def get_owner_insights(self, limit: int = 10) -> List[Dict[str, str]]:
        """Build actionable, source-grounded owner insights from computed results."""
        def value(key: str) -> Optional[float]:
            result = self.kpis.get(key)
            if result is None:
                return None
            status = getattr(result, "status", "ok") if not isinstance(result, dict) else result.get("status", "ok")
            raw = getattr(result, "value", None) if not isinstance(result, dict) else result.get("value")
            if status != "ok" or raw is None:
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        def money(number: Optional[float]) -> str:
            return "not available" if number is None else f"PKR {number:,.0f}"

        insights: List[Dict[str, str]] = []
        sales = self.period_comparison.get("total_revenue", {})
        orders = self.period_comparison.get("transaction_count", {})
        basket = self.period_comparison.get("average_transaction_value", {})
        revenue_change = sales.get("change_pct") if isinstance(sales, dict) else None
        if isinstance(sales, dict) and sales.get("current") is not None:
            current = float(sales["current"])
            previous = sales.get("previous")
            if previous is not None:
                previous = float(previous)
                change = float(revenue_change) if revenue_change is not None else None
                finding = f"Sales were {money(current)} this week, compared with {money(previous)} in the previous week."
                if change is not None:
                    finding += f" That is a {abs(change):.1f}% {'increase' if change >= 0 else 'decrease'}."
                action = "Use the branch, product, and transaction comparisons below to identify where the change came from before changing purchasing or promotion plans."
                if change is not None and change < 0:
                    action = "Check stock availability, customer count, and the weakest branch or product group first; target the cause before applying a broad discount."
                insights.append({"title": "Sales movement", "finding": finding, "action": action})

        if isinstance(orders, dict) and isinstance(basket, dict):
            order_change = orders.get("change_pct")
            basket_change = basket.get("change_pct")
            if order_change is not None and basket_change is not None:
                if float(order_change) < -2 and float(basket_change) >= -2:
                    insights.append({"title": "Customer traffic", "finding": f"Transactions changed by {float(order_change):+.1f}% while average bill value changed by {float(basket_change):+.1f}%.", "action": "Focus on repeat visits and local demand capture; average basket value held up, so a blanket basket discount may unnecessarily reduce margin."})
                elif float(basket_change) < -2:
                    insights.append({"title": "Basket value", "finding": f"Average bill value changed by {float(basket_change):+.1f}% while transaction count changed by {float(order_change):+.1f}%.", "action": "Review product mix and availability of commonly paired items; test relevant add-on recommendations and track margin as well as sales."})

        margin = value("gross_margin_pct")
        gross_profit = value("gross_profit")
        if margin is not None or gross_profit is not None:
            insights.append({"title": "Gross margin", "finding": f"Recorded gross profit is {money(gross_profit)} with a gross margin of {f'{margin:.1f}%' if margin is not None else 'not available'} for the selected period.", "action": "Compare item-level selling prices and purchase costs, then protect availability of products and categories that combine healthy margin with reliable demand. Gross margin does not include operating overhead."})

        if len(self.branch_performance) > 1:
            ranked = sorted(self.branch_performance, key=lambda row: float(row.get("revenue", 0) or 0), reverse=True)
            lead, tail = ranked[0], ranked[-1]
            insights.append({"title": "Branch opportunity", "finding": f"{lead.get('branch')} led recorded branch sales at {money(float(lead.get('revenue', 0) or 0))}; {tail.get('branch')} recorded {money(float(tail.get('revenue', 0) or 0))}.", "action": "Compare hours, stock availability, and transaction counts at the lower-sales branch with the leader before reallocating stock or staffing."})

        if self.top_products:
            top = self.top_products[0]
            amount = float(top.get("amount", 0) or 0)
            insights.append({"title": "Product availability", "finding": f"{top.get('name', 'Top product')} generated the highest recorded product sales at {money(amount)}.", "action": "Check its current stock and supplier lead time; avoid a stockout on a proven seller, while reviewing the full product list before expanding order quantities."})

        critical = [row for row in self.reorder_alerts if row.get("days_until_stockout") is not None and float(row.get("days_until_stockout") or 0) <= 3]
        if critical:
            names = ", ".join(str(row.get("product_name") or row.get("product_id")) for row in critical[:4])
            insights.append({"title": "Confirmed replenishment risks", "finding": f"Observed stock and sales velocity indicate critically low cover for: {names}.", "action": "Confirm physical quantities and supplier availability, then prioritize these products using the recorded reorder quantities."})
        elif self.reorder_alerts:
            known = [row for row in self.reorder_alerts if row.get("days_until_stockout") is not None]
            if not known:
                insights.append({"title": "Stock data limitation", "finding": "Inventory quantities are present, but there is not enough dated sales history to calculate days of supply.", "action": "Treat the reorder list as a stock snapshot, not a stockout forecast; include dated sales and on-hand quantities in the POS data to enable demand-based replenishment."})

        declining_result = self.kpis.get("top_declining_products")
        declining = getattr(declining_result, "breakdown", None) if declining_result is not None else None
        if declining:
            first = declining[0]
            product = first.get("product_id") or first.get("name") or "A product"
            insights.append({"title": "Demand change to investigate", "finding": f"{product} appears among products with the sharpest measured sales decline in the source trend analysis.", "action": "Check whether the change reflects stockouts, seasonality, price changes, or discontinued demand before adjusting its shelf allocation."})

        if self.category_margins:
            def margin_pct(row: Dict[str, Any]) -> Optional[float]:
                raw = row.get("margin_pct", row.get("gross_margin_pct", row.get("margin")))
                try:
                    return float(raw) if raw is not None else None
                except (TypeError, ValueError):
                    return None
            ranked_categories = sorted(
                (row for row in self.category_margins if margin_pct(row) is not None),
                key=lambda row: float(margin_pct(row) or 0),
            )
            if ranked_categories:
                low = ranked_categories[0]
                high = ranked_categories[-1]
                insights.append({
                    "title": "Category mix opportunity",
                    "finding": f"Recorded category margins range from {low.get('category', 'lowest-margin category')} at {float(margin_pct(low)):.1f}% to {high.get('category', 'highest-margin category')} at {float(margin_pct(high)):.1f}%.",
                    "action": "Review supplier costs and selling prices in the lower-margin categories, while protecting stock depth in categories that combine stronger margins with repeat demand.",
                })

        for key, title, action in (
            ("near_expiry_total", "Near-expiry stock", "Review affected batches for transfers, supplier returns, or controlled promotions before they expire."),
            ("expired_stock_value", "Expired stock", "Quarantine and reconcile the affected batches; confirm write-off and supplier-credit eligibility."),
            ("dead_stock_value", "Capital tied up in dormant stock", "Review item-level age and demand before reordering; pursue returns or a controlled markdown where appropriate."),
        ):
            amount = value(key)
            if amount is not None and amount > 0:
                insights.append({"title": title, "finding": f"Recorded exposure is {money(amount)}.", "action": action})

        if self.supplier_payables:
            first_supplier = self.supplier_payables[0]
            supplier_name = first_supplier.get("supplier_name") or first_supplier.get("supplier") or first_supplier.get("supplier_id")
            payable = first_supplier.get("total_payable") or first_supplier.get("payable_amount") or first_supplier.get("amount")
            if supplier_name and payable is not None:
                insights.append({"title": "Supplier cash planning", "finding": f"Largest recorded supplier balance is {money(float(payable))} with {supplier_name}.", "action": "Confirm its due date and compare the balance with current cash availability before placing discretionary orders."})

        if self.top_debtors:
            debtor = self.top_debtors[0]
            balance = debtor.get("balance")
            if balance is not None and float(balance) > 0:
                insights.append({"title": "Collections opportunity", "finding": f"Largest recorded customer balance is {money(float(balance))} for {debtor.get('customer', 'an account')}.", "action": "Reconcile the account and agree a follow-up date; review credit limits for repeat late balances."})

        discount_pct = value("discount_rate_pct") or value("discount_pct")
        if discount_pct is not None and discount_pct > 0:
            insights.append({"title": "Discount control", "finding": f"Recorded discounts are {discount_pct:.1f}% of the applicable sales base.", "action": "Review discounting by item and cashier to make sure promotions are increasing profitable sales rather than reducing margin on regular purchases."})

        anomaly_count = value("anomaly_count")
        if anomaly_count is not None and anomaly_count > 0:
            insights.append({"title": "Exceptions to review", "finding": f"The analytics scan flagged {anomaly_count:,.0f} unusual records for review.", "action": "Start with the highest-value exceptions and reconcile them against invoices, refunds, stock movements, and till records before treating them as losses."})

        assumptions = []
        for key in ("total_revenue", "gross_profit", "gross_margin_pct", "transaction_count"):
            result = self.kpis.get(key)
            provenance = getattr(result, "provenance", None) if result is not None else None
            assumptions.extend(getattr(provenance, "assumptions", []) or [])
        if assumptions:
            insights.append({"title": "Data quality to improve", "finding": "Some reported calculations rely on source assumptions: " + "; ".join(dict.fromkeys(assumptions[:3])) + ".", "action": "Standardize invoice, transaction type, item cost, date, and stock fields in the POS export to improve margin, trend, and replenishment decisions."})
        priority = {
            "Confirmed replenishment risks": 0, "Expired stock": 1, "Near-expiry stock": 2,
            "Collections opportunity": 3, "Supplier cash planning": 4, "Sales movement": 5,
            "Customer traffic": 6, "Basket value": 7, "Gross margin": 8,
            "Category mix opportunity": 9, "Branch opportunity": 10,
            "Product availability": 11, "Demand change to investigate": 12,
            "Capital tied up in dormant stock": 13, "Stock data limitation": 14,
            "Discount control": 15, "Exceptions to review": 16, "Data quality to improve": 17,
        }
        insights.sort(key=lambda insight: priority.get(insight["title"], 99))
        return insights[:limit]

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

        # 5a. Observed transaction volume by hour
        for traffic in self.hourly_traffic:
            hour = traffic.get("hour")
            count = traffic.get("count")
            if isinstance(hour, (int, float)):
                ground_truth.append((f"hourly_traffic:{int(hour)}:hour", float(hour), UNIT_COUNT))
            if isinstance(count, (int, float)):
                ground_truth.append((f"hourly_traffic:{hour}:transactions", float(count), UNIT_COUNT))

        # 6. Anomalies count
        if self.anomalies is not None:
            ground_truth.append(("anomaly_count", float(len(self.anomalies)), UNIT_COUNT))

        # 7. Payment mix numbers
        for pm in self.payment_mix:
            m_name = str(pm.get("payment_method") or pm.get("client_type") or pm.get("method") or pm.get("name", "payment"))
            for col, v in pm.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    unit = UNIT_PERCENT if any(w in col for w in ("pct", "percent", "share")) else (UNIT_COUNT if "count" in col else UNIT_CURRENCY)
                    ground_truth.append((f"payment_mix:{m_name}:{col}", float(v), unit))

        # 8. Supplier payables numbers
        for sp in self.supplier_payables:
            s_name = str(sp.get("supplier_name") or sp.get("supplier") or sp.get("supplier_id", "supplier"))
            for col, v in sp.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    unit = UNIT_PERCENT if ("pct" in col or "percent" in col) else (UNIT_COUNT if any(w in col for w in ("count", "days", "invoices")) else UNIT_CURRENCY)
                    ground_truth.append((f"supplier:{s_name}:{col}", float(v), unit))

        # 9. Dead stock items numbers
        for ds in self.dead_stock_items:
            p_name = str(ds.get("product_name") or ds.get("name") or ds.get("product_id", "product"))
            for col, v in ds.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    unit = UNIT_COUNT if any(w in col for w in ("count", "qty", "days", "quantity")) else UNIT_CURRENCY
                    ground_truth.append((f"dead_stock:{p_name}:{col}", float(v), unit))

        # 10. Category margins numbers
        for cm in self.category_margins:
            c_name = str(cm.get("category") or cm.get("name", "category"))
            for col, v in cm.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    unit = UNIT_PERCENT if any(w in col for w in ("pct", "percent", "margin")) else (UNIT_COUNT if "count" in col else UNIT_CURRENCY)
                    ground_truth.append((f"category_margin:{c_name}:{col}", float(v), unit))

        # 11. Reorder alerts numbers
        for ra in self.reorder_alerts:
            p_name = str(ra.get("product_name") or ra.get("name") or ra.get("product_id", "product"))
            for col, v in ra.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    unit = UNIT_COUNT if any(w in col for w in ("days", "count", "qty", "stock", "units")) else (UNIT_PERCENT if "pct" in col else UNIT_CURRENCY)
                    ground_truth.append((f"reorder_alert:{p_name}:{col}", float(v), unit))

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
            "report_title": self.report_title,
            "period_comparison": self.period_comparison,
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
            "payment_mix": self.payment_mix,
            "supplier_payables": self.supplier_payables,
            "dead_stock_items": self.dead_stock_items,
            "category_margins": self.category_margins,
            "reorder_alerts": self.reorder_alerts,
            "category_trend_note": self.category_trend_note,
        }
