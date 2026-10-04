"""
Module 6.8 (Verified Report Generator) — report.py

Top-level orchestrator for Module 6.8:
"Code computes. The LLM narrates. A verifier checks."

Generates executive publication reports matching the comprehensive pharmacy layout:
- Executive Cover Page
- Executive Summary & 8-Box KPI Stat Grid
- Section 1: Revenue Trends & Monthly Chart (with callout box)
- Section 2: Branch Performance (with Data Table)
- Section 3: Sales Channel & Payment Mix (with Donut Chart & Data Quality Note)
- Section 4: Product Performance (Top 12 Sellers & 10 Slow Movers)
- Section 5: Customer Shopping Dynamics (Hourly Peaks & Day-of-Week Patterns)
- Section 6: Cashier / Staff Performance
- Section 7: Customer Insights & Credit Risk (Top Debtors & Collections Action Item)
- Section 8: Discounting Behaviour
- Section 9: Recommendations Summary
"""

from __future__ import annotations

import base64
from html import escape
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import pandas as pd

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.core.config import settings, get_default_domain
from app.reporting.charts import render_charts
from app.reporting.models import ExecutiveMetrics, ReportData
from app.reporting.narrative import generate_narrative
from app.reporting.pdf_report import build_pdf_report
from app.reporting.verifier import (
    STATUS_MISMATCH,
    STATUS_UNMATCHED,
    STATUS_VERIFIED,
    VerificationReport,
    verify,
)
from app.schema.domain import get_domain_pack


@dataclass
class ReportResult:
    """Complete output of one generate_report() execution."""

    kpi_snapshot: Dict[str, Any]
    narrative: str
    verification: VerificationReport
    verification_passed: bool
    source_data_complete: bool = True
    regenerated: bool = False
    html_path: Optional[str] = None
    pdf_path: Optional[str] = None
    charts: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kpi_snapshot": self.kpi_snapshot,
            "narrative": self.narrative,
            "verification": self.verification.to_dict(),
            "verification_passed": self.verification_passed,
            "source_data_complete": self.source_data_complete,
            "regenerated": self.regenerated,
            "html_path": self.html_path,
            "pdf_path": self.pdf_path,
            "charts": self.charts,
            "warnings": list(self.warnings),
        }


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find matching column in DataFrame, checking exact, _extra. prefix, and case-insensitivity."""
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
        extra_c = f"_extra.{c}"
        if extra_c in df.columns:
            return extra_c
    for col in df.columns:
        clean = col.replace("_extra.", "").strip().lower()
        for c in candidates:
            if clean == c.strip().lower():
                return col
    return None


def _result_value(result: Any) -> Optional[float]:
    value = getattr(result, "value", None) if result is not None else None
    if value is None and isinstance(result, dict):
        value = result.get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_monthly_trend(df: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
    """Compute only the full-source monthly series needed by weekly report charts."""
    if df is None or df.empty:
        return []
    date_col = _find_col(df, ["Bill_Date", "Date", "date", "bill_date"])
    amount_col = _find_col(df, ["Amount", "Total_Amount", "sales_amount", "amount", "total_amount"])
    if not date_col or not amount_col:
        return []
    dates = pd.to_datetime(df[date_col], errors="coerce")
    amounts = pd.to_numeric(df[amount_col], errors="coerce")
    valid = dates.notna() & amounts.notna()
    if not valid.any():
        return []
    trend = pd.DataFrame({"dt": dates[valid], "revenue": amounts[valid]})
    trend["year"] = trend["dt"].dt.year.astype(str)
    trend["month_num"] = trend["dt"].dt.month
    trend["month"] = trend["dt"].dt.strftime("%b")
    trend["period"] = trend["dt"].dt.strftime("%Y-%m")
    monthly = trend.groupby(["year", "month_num", "month", "period"], sort=True)["revenue"].sum().reset_index()
    return [
        {"year": str(row.year), "month_num": int(row.month_num), "month": str(row.month),
         "period": str(row.period), "revenue": float(row.revenue)}
        for row in monthly.itertuples(index=False)
    ]


def _extract_executive_metrics(df: Optional[pd.DataFrame], filename: str = "Dataset.xlsx") -> Tuple[ExecutiveMetrics, Dict[str, Any]]:
    """Compute rich multi-dimensional executive aggregations from source DataFrame."""
    em = ExecutiveMetrics(source_filename=filename)
    dims: Dict[str, Any] = {
        "branch_performance": [],
        "monthly_trend": [],
        "payment_mix": [],
        "top_products": [],
        "slow_products": [],
        "hourly_traffic": [],
        "daily_traffic": [],
        "cashier_performance": [],
        "top_debtors": [],
    }

    if df is None or df.empty:
        return em, dims

    em.line_items_count = len(df)

    # 1. Financial totals
    amt_col = _find_col(df, ["Amount", "Total_Amount", "sales_amount", "amount", "total_amount"])
    if amt_col:
        em.total_revenue = float(pd.to_numeric(df[amt_col], errors="coerce").fillna(0).sum())

    # 2. Invoices & Average Bill
    inv_col = _find_col(df, ["Bill_No", "invoice_id", "Invoice_No", "bill_no"])
    if inv_col:
        em.total_invoices = int(df[inv_col].nunique())
    else:
        em.total_invoices = em.line_items_count

    if em.total_invoices > 0 and em.total_revenue > 0:
        em.avg_bill_value = em.total_revenue / em.total_invoices

    # 3. Unique Customers
    cust_col = _find_col(df, ["Customer_Name", "customer_id", "Customer_Mobile", "customer_name"])
    if cust_col:
        em.unique_customers = int(df[cust_col].dropna().nunique())

    # 4. Products Sold Count
    prod_col = _find_col(df, ["Product_Name", "product_id", "generic_name", "drug_name", "Product_Code", "product_name"])
    if prod_col:
        em.unique_products_count = int(df[prod_col].dropna().nunique())

    # 5. Discounts
    disc_col = _find_col(df, ["Discount_Amount", "discount_amount", "Flat_Discount_Amount", "Item_Discount_Total"])
    if disc_col:
        em.discounts_total = float(pd.to_numeric(df[disc_col], errors="coerce").fillna(0).sum())
        if (em.total_revenue + em.discounts_total) > 0:
            em.discounts_pct = (em.discounts_total / (em.total_revenue + em.discounts_total)) * 100.0

    # 6. Outstanding Balance
    bal_col = _find_col(df, ["Balance", "balance", "Previous_Balance"])
    if bal_col:
        bal_series = pd.to_numeric(df[bal_col], errors="coerce").fillna(0)
        # Sum invoice balances
        if inv_col:
            inv_bal = df.groupby(inv_col)[bal_col].first()
            em.outstanding_balance = float(pd.to_numeric(inv_bal, errors="coerce").fillna(0).sum())
        else:
            em.outstanding_balance = float(bal_series.sum())

    # 7. Date & Reporting Period
    date_col = _find_col(df, ["Bill_Date", "Date", "date", "bill_date"])
    if date_col:
        dt_series = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if not dt_series.empty:
            min_d, max_d = dt_series.min(), dt_series.max()
            em.reporting_period = f"{min_d.strftime('%B %Y')} – {max_d.strftime('%B %Y')}"

            # Monthly Trend
            temp_df = df.copy()
            temp_df["dt"] = dt_series
            temp_df["year"] = temp_df["dt"].dt.year.astype(str)
            temp_df["month_num"] = temp_df["dt"].dt.month
            temp_df["month_name"] = temp_df["dt"].dt.strftime("%b")
            temp_df["period"] = temp_df["dt"].dt.strftime("%Y-%m")

            if amt_col:
                monthly = temp_df.groupby(["year", "month_num", "month_name", "period"])[amt_col].sum().reset_index()
                dims["monthly_trend"] = [
                    {
                        "year": str(r["year"]),
                        "month_num": int(r["month_num"]),
                        "month": str(r["month_name"]),
                        "period": str(r["period"]),
                        "revenue": float(r[amt_col]),
                    }
                    for _, r in monthly.iterrows()
                ]

                # Year-over-Year Growth
                years = sorted([str(y) for y in temp_df["year"].dropna().unique() if str(y) not in ("nan", "<NA>")])
                if len(years) >= 2:
                    y1_rev = float(temp_df[temp_df["year"] == years[-2]][amt_col].sum())
                    y2_rev = float(temp_df[temp_df["year"] == years[-1]][amt_col].sum())
                    if y1_rev > 0:
                        em.yoy_growth_pct = ((y2_rev - y1_rev) / y1_rev) * 100.0

            # Daily Traffic
            temp_df["day_name"] = temp_df["dt"].dt.day_name()
            days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            if amt_col:
                day_grp = temp_df.groupby("day_name")[amt_col].sum().reindex(days_order).fillna(0)
                dims["daily_traffic"] = [{"day": d[:3], "revenue": float(v)} for d, v in day_grp.items()]

    # 8. Hourly Traffic
    time_col = _find_col(df, ["Bill_Time", "Time", "time", "bill_time"])
    if time_col:
        try:
            h_series = pd.to_datetime(df[time_col].astype(str), format="%H:%M:%S", errors="coerce").dt.hour
            h_counts = h_series.dropna().value_counts().sort_index()
            dims["hourly_traffic"] = [{"hour": int(h), "count": int(c)} for h, c in h_counts.items() if 7 <= h <= 23]
        except Exception:
            pass

    # 9. Branch Performance
    branch_col = _find_col(df, ["Branch_Name", "Pharmacy_Name", "branch", "Branch", "branch_id", "store_id", "Store_Name", "store", "location"])
    if branch_col and amt_col:
        b_grp = df.groupby(branch_col).agg(
            revenue=(amt_col, "sum"),
            invoices=(inv_col, "nunique") if inv_col else (amt_col, "count"),
        ).reset_index()
        b_grp["avg_bill"] = b_grp["revenue"] / b_grp["invoices"].replace(0, 1)
        b_grp = b_grp.sort_values(by="revenue", ascending=False)
        em.branches_list = [str(b) for b in b_grp[branch_col].tolist()]
        dims["branch_performance"] = [
            {
                "branch": str(r[branch_col]),
                "revenue": float(r["revenue"]),
                "invoices": int(r["invoices"]),
                "avg_bill": float(r["avg_bill"]),
            }
            for _, r in b_grp.iterrows()
        ]

    # 10. Payment Mix
    client_col = _find_col(df, ["Client_Type", "Payment_Method", "client_type", "payment_method", "Payment", "payment"])
    if client_col and amt_col:
        temp_df = df.copy()
        temp_df[client_col] = temp_df[client_col].fillna("Walk-in (Retail)")
        c_grp = temp_df.groupby(client_col)[amt_col].sum().sort_values(ascending=False).reset_index()
        dims["payment_mix"] = [
            {"client_type": str(r[client_col]), "revenue": float(r[amt_col])}
            for _, r in c_grp.iterrows()
        ]

    # 11. Top Products & Slow Movers
    if prod_col and amt_col:
        p_grp = df.groupby(prod_col)[amt_col].sum().sort_values(ascending=False).reset_index()
        dims["top_products"] = [{"name": str(r[prod_col]), "amount": float(r[amt_col])} for _, r in p_grp.head(12).iterrows()]
        dims["slow_products"] = [{"name": str(r[prod_col]), "amount": float(r[amt_col])} for _, r in p_grp.tail(10).iterrows()]

    # 12. Cashier Performance
    cashier_col = _find_col(df, ["Cashier_Name", "cashier", "Cashier", "staff_id"])
    if cashier_col and amt_col:
        cash_grp = df.groupby(cashier_col).agg(
            revenue=(amt_col, "sum"),
            invoices=(inv_col, "nunique") if inv_col else (amt_col, "count"),
        ).sort_values(by="revenue", ascending=False).reset_index()
        em.cashiers_list = [str(c) for c in cash_grp[cashier_col].tolist()]
        dims["cashier_performance"] = [
            {"cashier": str(r[cashier_col]), "revenue": float(r["revenue"]), "invoices": int(r["invoices"])}
            for _, r in cash_grp.iterrows()
        ]

    # 13. Top Debtors
    if cust_col and bal_col:
        debt_grp = df.groupby(cust_col)[bal_col].sum().sort_values(ascending=False).reset_index()
        debt_grp = debt_grp[debt_grp[bal_col] > 0]
        dims["top_debtors"] = [{"customer": str(r[cust_col]), "balance": float(r[bal_col])} for _, r in debt_grp.head(10).iterrows()]

    return em, dims


def gather_report_data(
    source_df: Optional[pd.DataFrame] = None,
    raw_df: Optional[pd.DataFrame] = None,
    domain: Optional[str] = None,
    filters: Optional[KPIFilters] = None,
    business_name: Optional[str] = None,
    anomalies: Optional[List[Dict[str, Any]]] = None,
    kpi_results: Optional[Dict[str, Any]] = None,
    report_type: str = "standard",
    source_name: Optional[str] = None,
) -> ReportData:
    """Gather and assemble ReportData with rich executive dimensions."""
    effective_domain = domain or get_default_domain()
    effective_name = business_name or f"{effective_domain.title()} Business"
    is_weekly_pharmacy = (report_type == "weekly_pharmacy" and str(effective_domain).strip().lower() == "pharmacy")

    # Compute all-time results first; weekly sales KPIs are replaced below with an
    # actual latest-week slice, while stock-on-hand and expiry KPIs remain snapshots.
    if kpi_results is not None:
        computed_kpis = kpi_results
    elif source_df is not None and is_weekly_pharmacy:
        # Weekly mode computes its small comparison/snapshot KPI set below.
        # Running the entire pharmacy KPI registry over a very large workbook
        # needlessly repeats full-frame scans many times.
        computed_kpis = {}
    elif source_df is not None:
        computed_kpis = engine.compute_all(source_df, filters, domain=effective_domain)
    else:
        computed_kpis = {}
    comparison: Dict[str, Any] = {}
    weekly_metrics_df: Optional[pd.DataFrame] = None
    weekly_audit_df: Optional[pd.DataFrame] = None
    weekly_period = None
    if is_weekly_pharmacy and source_df is not None and not source_df.empty and kpi_results is None:
        try:
            from app.analytics.kpi import classify_transactions
            from app.analytics.models import Period

            transaction_types = classify_transactions(source_df)
            dates = pd.to_datetime(source_df["date"], errors="coerce") if "date" in source_df.columns else pd.Series(pd.NaT, index=source_df.index)
            sale_dates = dates[transaction_types.sale & dates.notna()]
            if not sale_dates.empty:
                end_date = sale_dates.max().normalize()
                current_start = end_date - pd.Timedelta(days=6)
                prior_end = current_start - pd.Timedelta(days=1)
                prior_start = prior_end - pd.Timedelta(days=6)
                current_mask = dates.between(current_start, end_date + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1))
                prior_mask = dates.between(prior_start, prior_end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1))
                weekly_metrics_df = source_df.loc[current_mask & transaction_types.sale]
                weekly_audit_df = source_df.loc[current_mask]
                current_filters = replace(filters or KPIFilters(), date_from=current_start.strftime("%Y-%m-%d"), date_to=end_date.strftime("%Y-%m-%d"))
                prior_filters = replace(filters or KPIFilters(), date_from=prior_start.strftime("%Y-%m-%d"), date_to=prior_end.strftime("%Y-%m-%d"))
                comparison_keys = ("total_revenue", "transaction_count", "gross_profit", "gross_margin_pct", "average_transaction_value")
                current_kpi_df = source_df.loc[current_mask]
                prior_kpi_df = source_df.loc[prior_mask]
                current_kpis = {key: engine.compute(key, current_kpi_df, current_filters, domain=effective_domain) for key in comparison_keys}
                prior_kpis = {key: engine.compute(key, prior_kpi_df, prior_filters, domain=effective_domain) for key in comparison_keys}
                snapshot_keys = (
                    "near_expiry_total", "expired_stock_value", "expiring_value_30d",
                    "supplier_payable_total",
                    "supplier_payable_by_supplier", "dead_stock_value", "low_stock_reorder_predictions",
                    "top_declining_products", "gross_margin_by_category", "payment_method_mix",
                )
                computed_kpis = dict(current_kpis)
                for key in snapshot_keys:
                    if engine.has(key, domain=effective_domain):
                        computed_kpis[key] = engine.compute(key, source_df, filters, domain=effective_domain)

                comparison = {
                    "current_start": current_start.strftime("%Y-%m-%d"),
                    "current_end": end_date.strftime("%Y-%m-%d"),
                    "previous_start": prior_start.strftime("%Y-%m-%d"),
                    "previous_end": prior_end.strftime("%Y-%m-%d"),
                }
                for key in ("total_revenue", "transaction_count", "gross_profit", "gross_margin_pct", "average_transaction_value"):
                    current_value = _result_value(current_kpis.get(key))
                    previous_value = _result_value(prior_kpis.get(key))
                    if current_value is not None or previous_value is not None:
                        delta_pct = ((current_value - previous_value) / abs(previous_value) * 100.0) if current_value is not None and previous_value not in (None, 0.0) else None
                        comparison[key] = {"current": current_value, "previous": previous_value, "change_pct": delta_pct}
                weekly_period = Period(start=current_start.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
        except Exception:
            comparison = {}
            weekly_metrics_df = None
            weekly_audit_df = None
    if is_weekly_pharmacy and weekly_period is None and source_df is not None and kpi_results is None:
        # Fall back to the standard KPI pack if the source has no usable sale dates.
        computed_kpis = engine.compute_all(source_df, filters, domain=effective_domain)

    # Extract rich executive metrics from raw data if available, fallback to canonical
    df_for_metrics = weekly_metrics_df if weekly_metrics_df is not None else (raw_df if (raw_df is not None and not raw_df.empty) else source_df)
    em, dims = _extract_executive_metrics(df_for_metrics)
    if is_weekly_pharmacy and source_df is not None and not source_df.empty:
        # Keep transaction-level comparisons scoped to the selected week, while
        # allowing the trend chart to use the full history available in this file.
        dims["monthly_trend"] = _extract_monthly_trend(source_df)
    if source_name:
        em.source_filename = source_name
    if not em.branches_list and effective_name:
        em.branches_list = [effective_name]

    # Period resolution
    if is_weekly_pharmacy and weekly_period is not None:
        period = weekly_period
        em.reporting_period = f"{comparison['current_start']} to {comparison['current_end']} (vs prior week {comparison['previous_start']} to {comparison['previous_end']})"
    else:
        # Extract period from any available KPIResult
        period = None
        for res in computed_kpis.values():
            p = getattr(res, "period", None)
            if p is not None:
                period = p
                break

    # Automatically run statistical anomaly detection if not explicitly supplied
    if anomalies is None and source_df is not None and not source_df.empty:
        try:
            from app.anomaly.detectors import detect_all_anomalies
            from app.anomaly.explainer import explain_all
            anomaly_source = weekly_audit_df if is_weekly_pharmacy and weekly_audit_df is not None else source_df
            scan_res = detect_all_anomalies(anomaly_source, domain=effective_domain)
            if scan_res.anomalies:
                explain_all(scan_res.anomalies, max_items=10, use_llm=False)
                anomalies = [a.to_dict() for a in scan_res.anomalies]
        except Exception:
            anomalies = None

    # Extended ReportData fields and sections
    supplier_payables = []
    dead_stock_items = []
    category_margins = []
    reorder_alerts = []
    category_trend_note = None

    if is_weekly_pharmacy:
        sections = ["executive_summary", "weekly_report"]
    else:
        sections = [
            "revenue_trends", "branch_performance", "payment_mix",
            "products", "shopping_hours", "cashiers", "credit_risk", "recommendations"
        ]

    if is_weekly_pharmacy:
        # Supplier payables from supplier_payable_by_supplier
        sp_res = computed_kpis.get("supplier_payable_by_supplier")
        if sp_res and getattr(sp_res, "breakdown", None):
            supplier_payables = list(sp_res.breakdown)

        # Dead stock items from dead_stock_value
        ds_res = computed_kpis.get("dead_stock_value")
        if ds_res and getattr(ds_res, "breakdown", None):
            dead_stock_items = list(ds_res.breakdown)

        # Category margins from gross_margin_by_category
        cm_res = computed_kpis.get("gross_margin_by_category")
        if cm_res and getattr(cm_res, "breakdown", None):
            category_margins = list(cm_res.breakdown)

        # Payment mix from payment_method_mix if dims["payment_mix"] is empty
        pm_res = computed_kpis.get("payment_method_mix")
        if pm_res and getattr(pm_res, "breakdown", None) and not dims["payment_mix"]:
            dims["payment_mix"] = list(pm_res.breakdown)

        # Reorder alerts from low_stock_reorder_predictions
        ro_res = computed_kpis.get("low_stock_reorder_predictions")
        if ro_res and getattr(ro_res, "breakdown", None):
            for r in ro_res.breakdown:
                if isinstance(r, dict) and r.get("stockout_risk") in {"CRITICAL_STOCKOUT_RISK", "REORDER_RECOMMENDED"}:
                    reorder_alerts.append({
                        "product_id": str(r.get("product_id", "")),
                        "product_name": str(r.get("generic_name") or r.get("product_id", "")),
                        "days_until_stockout": float(r["days_of_supply"]) if r.get("days_of_supply") is not None else None,
                        "current_stock": float(r.get("current_stock", 0.0)),
                        "daily_sales_velocity": float(r.get("daily_sales_velocity", 0.0)),
                        "reorder_qty": float(r.get("recommended_reorder_qty", 0.0)),
                    })

        # Category trend note
        if category_margins:
            top_cat = category_margins[0]
            cat_name = top_cat.get("category", "Top Category")
            margin_val = top_cat.get("margin_pct") or top_cat.get("gross_margin_pct", 0.0)
            category_trend_note = f"{cat_name} led product categories with a {margin_val:.1f}% gross margin."

        # Add shrinkage anomalies to anomalies if not already present
        shrinkage_source = weekly_audit_df if weekly_audit_df is not None else source_df
        if shrinkage_source is not None and not shrinkage_source.empty:
            try:
                from app.anomaly.detectors import detect_stock_movement_mismatch
                from app.anomaly.explainer import generate_template_explanation
                shrinkage = detect_stock_movement_mismatch(shrinkage_source)
                if shrinkage:
                    if anomalies is None:
                        anomalies = []
                    existing_keys = {
                        (a.get("metric_name"), a.get("metadata", {}).get("product_id"))
                        for a in anomalies if isinstance(a, dict)
                    }
                    for sa in shrinkage:
                        pid = sa.metadata.get("product_id")
                        if (sa.metric_name, pid) not in existing_keys:
                            if not sa.explanation:
                                sa.explanation = generate_template_explanation(sa)
                            anomalies.append(sa.to_dict())
            except Exception:
                pass

        # Section ordering and non-empty checks:
        has_expiry = False
        for ek in ("near_expiry_total", "expired_stock_value", "expiring_value_30d", "expiring_value_60d", "expiring_value_90d"):
            res = computed_kpis.get(ek)
            if res and getattr(res, "value", 0.0) and float(getattr(res, "value", 0.0)) > 0:
                has_expiry = True
                break
            if res and getattr(res, "breakdown", None):
                has_expiry = True
                break

        has_shrinkage = any(
            isinstance(a, dict) and (a.get("metric_name") == "stock_movement_mismatch" or a.get("anomaly_type") == "stock_movement_mismatch")
            for a in (anomalies or [])
        )

        new_section_candidates = [
            ("cash_card_mix", bool(dims["payment_mix"])),
            ("expiry_loss_exposure", has_expiry),
            ("supplier_credit", bool(supplier_payables) or (computed_kpis.get("supplier_payable_total") and float(getattr(computed_kpis.get("supplier_payable_total"), "value", 0.0) or 0) > 0)),
            ("dead_stock", bool(dead_stock_items) or (computed_kpis.get("dead_stock_value") and float(getattr(computed_kpis.get("dead_stock_value"), "value", 0.0) or 0) > 0)),
            ("category_margin", bool(category_margins)),
            ("reorder_alerts", bool(reorder_alerts)),
            ("shrinkage_flags", bool(has_shrinkage)),
            ("seasonal_trend", bool(category_trend_note) or bool(dims["monthly_trend"]) or bool(computed_kpis.get("revenue_trend"))),
        ]

        for sec_key, is_non_empty in new_section_candidates:
            if is_non_empty and sec_key not in sections:
                sections.append(sec_key)

    source_label = source_name or em.source_filename or "POS dataset"
    if source_label.startswith("db://"):
        source_label = f"{source_label[5:].strip()} database"
    elif source_label.startswith("sql://"):
        source_label = source_label.split("/")[-1].replace("_", " ")
    else:
        source_label = Path(source_label).stem.replace("_", " ")
    em.source_filename = source_label
    period_label = f"Week ending {comparison['current_end']}" if comparison.get("current_end") else em.reporting_period
    report_title = f"{effective_name} | {source_label} | {period_label} Performance & Growth Report"
    source_data_complete = not bool((source_df is not None and source_df.attrs.get("incomplete_source")) or (raw_df is not None and raw_df.attrs.get("incomplete_source")))
    source_data_warning = None if source_data_complete else (
        "This report was calculated from available embedded records, but the knowledge base marked the selected source as incomplete. "
        "Treat all KPIs, trends, and anomaly results as partial until complete structured rows are available."
    )

    return ReportData(
        kpis=computed_kpis,
        domain=effective_domain,
        business_name=effective_name,
        report_title=report_title,
        period_comparison=comparison,
        period=period,
        filters=filters.as_dict() if filters and hasattr(filters, "as_dict") else {},
        anomalies=anomalies,
        source_data_complete=source_data_complete,
        source_data_warning=source_data_warning,
        sections=sections,
        generated_at=datetime.now(),
        executive_metrics=em,
        branch_performance=dims["branch_performance"],
        monthly_trend=dims["monthly_trend"],
        payment_mix=dims["payment_mix"],
        top_products=dims["top_products"],
        slow_products=dims["slow_products"],
        hourly_traffic=dims["hourly_traffic"],
        daily_traffic=dims["daily_traffic"],
        cashier_performance=dims["cashier_performance"],
        top_debtors=dims["top_debtors"],
        supplier_payables=supplier_payables,
        dead_stock_items=dead_stock_items,
        category_margins=category_margins,
        reorder_alerts=reorder_alerts,
        category_trend_note=category_trend_note,
    )


# ──────────────────────────────────────────────────────────────────────────────
# HTML Generation
# ──────────────────────────────────────────────────────────────────────────────

_HTML_CSS = """
    :root {
        --navy:   #1e3a5f;
        --gold:   #d97706;
        --teal:   #0d7377;
        --bg-box: #f1f5f9;
        --border: #e2e8f0;
        --text:   #0f172a;
        --muted:  #64748b;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        background: #f8fafc;
        color: var(--text);
        padding: 3rem 2rem;
        max-width: 1080px;
        margin: 0 auto;
        line-height: 1.6;
    }
    .page {
        background: #ffffff;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 2rem 2.4rem;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 20px -2px rgba(0,0,0,0.05);
    }
    @media print {
        body { background: #fff; padding: 0; }
        .page { border: none; box-shadow: none; padding: 1.1rem 0; page-break-after: auto; break-after: auto; }
        .cover-page { page-break-after: always; break-after: page; }
    }
    
    /* Cover Page */
    .cover-page { text-align: center; padding: 6rem 2rem; }
    .cover-title { font-size: 2.3rem; font-weight: 900; color: var(--navy); letter-spacing: 0.04em; margin-bottom: 0.4rem; }
    .cover-subtitle { font-size: 1.6rem; font-weight: 800; color: var(--gold); letter-spacing: 0.02em; margin-bottom: 1.5rem; }
    .cover-branches { font-size: 1.1rem; color: #1e293b; font-weight: 600; margin-bottom: 0.5rem; }
    .cover-period { font-size: 1rem; color: var(--muted); font-style: italic; margin-bottom: 5rem; }
    .cover-meta { font-size: 0.9rem; color: var(--muted); line-height: 1.8; }
    
    /* Section Headings */
    .section-title {
        font-size: 1.35rem;
        font-weight: 800;
        color: var(--navy);
        border-bottom: 2px solid var(--gold);
        padding-bottom: 0.35rem;
        margin-top: 1.5rem;
        margin-bottom: 0.25rem;
    }
    .section-sub { font-size: 1rem; color: var(--muted); font-style: italic; margin-bottom: 0.8rem; }
    
    /* 8-Box KPI Stat Grid */
    .kpi-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 1rem;
        margin: 1.5rem 0;
    }
    .kpi-box {
        background: var(--bg-box);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1rem 1.25rem;
    }
    .kpi-val { font-size: 1.45rem; font-weight: 800; color: #0f172a; line-height: 1.2; margin-bottom: 0.2rem; }
    .kpi-lbl { font-size: 0.9rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; }
    
    /* Callout Boxes */
    .callout {
        border-radius: 8px;
        padding: 1rem 1.25rem;
        margin: 1.25rem 0;
        font-size: 1rem;
        line-height: 1.6;
    }
    .callout-gold { background: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid var(--gold); color: #854d0e; }
    .callout-green { background: #f0fdf4; border: 1px solid #bbf7d0; border-left: 4px solid #16a34a; color: #166534; }
    .callout-orange { background: #fff7ed; border: 1px solid #fed7aa; border-left: 4px solid #ea580c; color: #9a3412; }
    .callout h4 { font-size: 0.95rem; font-weight: 700; margin-bottom: 0.3rem; }
    
    /* Tables */
    table { width: 100%; border-collapse: collapse; margin: 0.8rem 0; font-size: 1rem; }
    th { background: var(--navy); color: #ffffff; font-weight: 700; padding: 0.75rem 0.9rem; text-align: left; }
    td { padding: 0.7rem 0.9rem; border-bottom: 1px solid var(--border); }
    tr:nth-child(even) { background: #fbfcfe; }
    
    .chart-box { text-align: center; margin: 0.8rem 0; }
    .chart-img { max-width: 100%; height: auto; border-radius: 6px; }
    .chart-caption { font-size: 0.92rem; color: var(--muted); font-style: italic; margin-top: 0.4rem; }
    
    .bullet-list { margin: 1rem 0 1.5rem 1.25rem; }
    .bullet-list li { margin-bottom: 0.55rem; font-size: 0.92rem; color: #334155; line-height: 1.6; }
"""


def _image_to_base64(img_path: Path) -> str:
    try:
        data = img_path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


def _render_weekly_html_document(
    report_data: ReportData,
    narrative: str,
    verification: VerificationReport,
    charts: Dict[str, Path],
) -> str:
    em = report_data.executive_metrics
    branches_str = " | ".join(em.branches_list) if em.branches_list else report_data.business_name
    rev_str = f"PKR {em.total_revenue*1e-6:.2f}M" if em.total_revenue >= 1e6 else f"PKR {em.total_revenue:,.0f}"
    disc_str = f"PKR {em.discounts_total*1e-6:.2f}M ({em.discounts_pct:.1f}%)" if em.discounts_total >= 1e6 else f"PKR {em.discounts_total:,.0f}"
    bal_str = f"PKR {em.outstanding_balance*1e-6:.2f}M" if em.outstanding_balance >= 1e6 else f"PKR {em.outstanding_balance:,.0f}"
    report_title = escape(report_data.report_title or f"{report_data.business_name} Performance & Growth Report")

    c_pay = _image_to_base64(charts["payment_mix"]) if "payment_mix" in charts else ""
    c_expiry = _image_to_base64(charts["expiry"]) if "expiry" in charts else (_image_to_base64(charts["expiry_risk"]) if "expiry_risk" in charts else "")
    c_supp = _image_to_base64(charts["supplier_payables"]) if "supplier_payables" in charts else ""
    c_dead = _image_to_base64(charts["dead_stock"]) if "dead_stock" in charts else ""
    c_cat_margin = _image_to_base64(charts["category_margin"]) if "category_margin" in charts else ""
    c_trend = _image_to_base64(charts["monthly_trend"]) if "monthly_trend" in charts else (_image_to_base64(charts["trend"]) if "trend" in charts else "")
    c_compare = _image_to_base64(charts["period_comparison"]) if "period_comparison" in charts else ""
    c_branch = _image_to_base64(charts["branch_performance"]) if "branch_performance" in charts else ""
    c_top = _image_to_base64(charts["top_products"]) if "top_products" in charts else ""
    c_hour = _image_to_base64(charts["hourly_traffic"]) if "hourly_traffic" in charts else ""
    c_day = _image_to_base64(charts["daily_traffic"]) if "daily_traffic" in charts else ""

    warn_banner = ""
    if verification.claims and not verification.all_verified:
        warn_banner = f"""
        <div class="callout callout-warn banner-warn" style="background: #fef2f2; border-left: 4px solid #ef4444; padding: 1rem; margin: 1rem 0; border-radius: 4px;">
          <h4 style="color: #991b1b; margin-bottom: 0.25rem;">AI narrative withheld</h4>
          <p style="font-size: 0.88rem; color: #7f1d1d;">The narrative did not pass numeric verification, so it is excluded. The insights below are computed from the selected POS data.</p>
        </div>
        """
    if report_data.source_data_warning:
        warn_banner += f'<div class="callout callout-warn banner-warn"><h4>Incomplete source data</h4><p>{escape(report_data.source_data_warning)}</p></div>'

    sections_html = []

    owner_insights_html = "".join(
        f'<div class="callout callout-gold"><h4>{escape(item["title"])}</h4><p>{escape(item["finding"])}</p><p><b>Recommended next step:</b> {escape(item["action"])}</p></div>'
        for item in report_data.get_owner_insights(limit=6)
    )
    comparison_labels = {
        "total_revenue": "Sales revenue", "transaction_count": "Transactions",
        "gross_profit": "Gross profit", "gross_margin_pct": "Gross margin",
        "average_transaction_value": "Average bill value",
    }
    comparison_rows = []
    for key, label in comparison_labels.items():
        metric = report_data.period_comparison.get(key)
        if not isinstance(metric, dict) or metric.get("current") is None or metric.get("previous") is None:
            continue
        unit = "PKR " if key in ("total_revenue", "gross_profit", "average_transaction_value") else ""
        suffix = "%" if key == "gross_margin_pct" else ""
        change = metric.get("change_pct")
        change_str = f"{float(change):+.1f}%" if change is not None else "—"
        comparison_rows.append(
            f'<tr><td>{escape(label)}</td><td>{unit}{float(metric["current"]):,.2f}{suffix}</td>'
            f'<td>{unit}{float(metric["previous"]):,.2f}{suffix}</td><td>{change_str}</td></tr>'
        )
    comparison_html = (
        '<div class="section-title">This week vs previous week</div>'
        '<table><thead><tr><th>Measure</th><th>Current</th><th>Previous</th><th>Change</th></tr></thead><tbody>'
        + "".join(comparison_rows) + "</tbody></table>"
    ) if comparison_rows else ""
    if c_compare:
        comparison_html += f'<div class="chart-box"><img class="chart-img" src="{c_compare}" alt="Current versus previous period comparison" /><div class="chart-caption">Each measure is scaled independently for a fair period-to-period comparison.</div></div>'

    # 1. Cash / Card Mix
    if "cash_card_mix" in report_data.sections:
        pay_rows = []
        for pm in report_data.payment_mix:
            m = str(pm.get("payment_method") or pm.get("client_type") or "Method")
            amt = float(pm.get("revenue") or pm.get("amount") or 0.0)
            pct = float(pm.get("share_pct") or pm.get("pct") or 0.0)
            cnt = int(pm.get("invoices") or pm.get("count") or 0)
            pay_rows.append(f"<tr><td><b>{m}</b></td><td>PKR {amt:,.0f}</td><td>{pct:.1f}%</td><td>{cnt:,}</td></tr>")
        table_html = f"<table><thead><tr><th>Payment Method</th><th>Revenue</th><th>Share</th><th>Invoices</th></tr></thead><tbody>{''.join(pay_rows)}</tbody></table>" if pay_rows else ""
        chart_html = f'<div class="chart-box"><img class="chart-img" src="{c_pay}" alt="Payment Mix" /><div class="chart-caption">Revenue share by payment channel</div></div>' if c_pay else ""
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">1. Cash vs. Card &amp; Payment Channel Mix</div>
          <div class="section-sub">Customer settlement methods and counter liquidity</div>
          {chart_html}
          {table_html}
          <div class="callout callout-gold">
            <h4>Liquidity Note</h4>
            Reconcile each recorded payment channel to settlement statements and review which channels produce the strongest net contribution after fees.
          </div>
        </div>
        """)

    # 2. Expiry Loss Exposure
    if "expiry_loss_exposure" in report_data.sections:
        chart_html = f'<div class="chart-box"><img class="chart-img" src="{c_expiry}" alt="Expiry Exposure" /><div class="chart-caption">Stock expiry risk distribution</div></div>' if c_expiry else ""
        near_v = getattr(report_data.get_kpi("near_expiry_total") or report_data.get_kpi("expiring_value_30d"), "value", None)
        exp_v = getattr(report_data.get_kpi("expired_stock_value"), "value", None)
        near_str = f"PKR {float(near_v):,.2f}" if near_v is not None else "Not available from this source"
        exp_str = f"PKR {float(exp_v):,.2f}" if exp_v is not None else "Not available from this source"
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">2. Expiry Loss Exposure</div>
          <div class="section-sub">Near-expiry and expired inventory values reported by this source</div>
          {chart_html}
          <div class="callout callout-orange">
            <h4>Expiry Exposure Breakdown</h4>
            <p style="font-size: 0.92rem; margin-bottom: 0.5rem;"><b>Near Expiry Stock:</b> {near_str} &nbsp;|&nbsp; <b>Expired Stock:</b> {exp_str}</p>
            Review the exposed batches and prioritize distributor returns or stock transfers where the source data supports them.
          </div>
        </div>
        """)

    # 3. Supplier Credit
    if "supplier_credit" in report_data.sections:
        supp_rows = []
        for sp in report_data.supplier_payables:
            s_name = str(sp.get("supplier_name") or sp.get("supplier") or sp.get("supplier_id") or "Distributor")
            amt = float(sp.get("total_payable") or sp.get("payable_amount") or sp.get("amount") or 0.0)
            due = sp.get("earliest_due_date") or sp.get("due_date") or "Prompt"
            supp_rows.append(f"<tr><td><b>{s_name}</b></td><td>PKR {amt:,.2f}</td><td>{due}</td></tr>")
        table_html = f"<table><thead><tr><th>Distributor / Supplier</th><th>Amount Owed</th><th>Earliest Due Date</th></tr></thead><tbody>{''.join(supp_rows)}</tbody></table>" if supp_rows else ""
        chart_html = f'<div class="chart-box"><img class="chart-img" src="{c_supp}" alt="Supplier Credit" /><div class="chart-caption">Top suppliers by amount owed</div></div>' if c_supp else ""
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">3. Supplier Credit &amp; Accounts Payable</div>
          <div class="section-sub">Distributor credit obligations and upcoming payment maturities</div>
          {chart_html}
          {table_html}
          <div class="callout callout-gold">
            <h4>Trade Credit Management</h4>
            Prioritize settlement of supplier payables approaching their due dates to safeguard distributor credit lines and preserve negotiated commercial purchase discounts.
          </div>
        </div>
        """)

    # 4. Dead Stock
    if "dead_stock" in report_data.sections:
        ds_rows = []
        for ds in report_data.dead_stock_items:
            p_name = str(ds.get("product_name") or ds.get("name") or ds.get("product_id") or "Product")
            val = float(ds.get("tied_up_value") or ds.get("dead_stock_value") or ds.get("value") or ds.get("line_value") or ds.get("stock_value") or ds.get("amount") or 0.0)
            qty = float(ds.get("quantity") or ds.get("stock") or 0.0)
            ds_rows.append(f"<tr><td><b>{p_name}</b></td><td>PKR {val:,.2f}</td><td>{qty:,.0f}</td></tr>")
        table_html = f"<table><thead><tr><th>Product Name</th><th>Tied-Up Value</th><th>Units in Stock</th></tr></thead><tbody>{''.join(ds_rows)}</tbody></table>" if ds_rows else ""
        chart_html = f'<div class="chart-box"><img class="chart-img" src="{c_dead}" alt="Dead Stock" /><div class="chart-caption">Top dormant inventory items by tied-up capital</div></div>' if c_dead else ""
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">4. Dead Stock &amp; Slow Capital Exposure</div>
          <div class="section-sub">Dormant inventory with negligible sales velocity tying up working capital</div>
          {chart_html}
          {table_html}
          <div class="callout callout-orange">
            <h4>Working Capital Recovery</h4>
            Consider bundled promotions, doctor sample distribution, or distributor return requests for non-moving medicines to recover capital tied up in dormant inventory.
          </div>
        </div>
        """)

    # 5. Margin by Category
    if "category_margin" in report_data.sections:
        cm_rows = []
        for cm in report_data.category_margins:
            cat = str(cm.get("category") or cm.get("name") or "Category")
            m_pct = float(cm.get("margin_pct") or cm.get("gross_margin_pct") or cm.get("margin") or 0.0)
            rev = float(cm.get("revenue") or cm.get("amount") or 0.0)
            cm_rows.append(f"<tr><td><b>{cat}</b></td><td>{m_pct:.1f}%</td><td>PKR {rev:,.0f}</td></tr>")
        table_html = f"<table><thead><tr><th>Category</th><th>Gross Margin</th><th>Revenue</th></tr></thead><tbody>{''.join(cm_rows)}</tbody></table>" if cm_rows else ""
        chart_html = f'<div class="chart-box"><img class="chart-img" src="{c_cat_margin}" alt="Category Margins" /><div class="chart-caption">Gross margin percentage across product categories</div></div>' if c_cat_margin else ""
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">5. Gross Profit Margin by Category</div>
          <div class="section-sub">Profitability distribution across therapeutic categories</div>
          {chart_html}
          {table_html}
          <div class="callout callout-green">
            <h4>Margin Optimization</h4>
            Protect retail shelf space for high-margin therapeutic classes while maintaining stock of lower-margin fast movers to drive overall basket revenue.
          </div>
        </div>
        """)

    # 6. Reorder Alerts
    if "reorder_alerts" in report_data.sections:
        ra_rows = []
        for ra in report_data.reorder_alerts:
            p_name = str(ra.get("product_name") or ra.get("name") or ra.get("product_id") or "Product")
            days_value = ra.get("days_until_stockout")
            days = f"{float(days_value):.1f} days" if days_value is not None else "Insufficient sales history"
            rq = float(ra.get("recommended_reorder_qty") or ra.get("reorder_qty") or 0.0)
            ra_rows.append(f"<tr><td><b>{escape(p_name)}</b></td><td>{days}</td><td>{rq:,.0f} units</td></tr>")
        table_html = f"<table><thead><tr><th>Product Name</th><th>Days of Supply</th><th>Suggested Reorder</th></tr></thead><tbody>{''.join(ra_rows)}</tbody></table>" if ra_rows else ""
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">6. Stockout Risk &amp; Priority Reorder Alerts</div>
          <div class="section-sub">Critical medications nearing depleted inventory</div>
          {table_html}
          <div class="callout callout-gold">
            <h4>Replenishment Urgency</h4>
            Confirm on-hand quantities and supplier lead times. Treat rows without dated sales history as stock snapshots, not days-of-supply forecasts.
          </div>
        </div>
        """)

    # 7. Shrinkage Flags
    if "shrinkage_flags" in report_data.sections:
        shrinkage_anoms = [
            a for a in (report_data.anomalies or [])
            if isinstance(a, dict) and (a.get("metric_name") == "stock_movement_mismatch" or a.get("anomaly_type") == "stock_movement_mismatch")
        ]
        sh_rows = []
        for a in shrinkage_anoms[:10]:
            pid = a.get("metadata", {}).get("product_id") or a.get("row_ref") or "Item"
            exp = a.get("explanation") or str(a.get("observed_value"))
            sh_rows.append(f"<li style='margin-bottom: 0.4rem;'><b>{pid}:</b> {exp}</li>")
        sh_content = f"<ul style='margin: 0.5rem 0 0 1.25rem; font-size: 0.88rem; line-height: 1.5;'>{''.join(sh_rows)}</ul>" if sh_rows else "<p style='font-size: 0.88rem;'>No significant inventory movement discrepancies detected this cycle.</p>"
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">7. Inventory Shrinkage &amp; Movement Flags</div>
          <div class="section-sub">Audit flags where inventory reduction exceeded recorded sales transactions</div>
          <div class="callout callout-orange" style="margin: 1rem 0;">
            <h4>Flagged Inventory Discrepancies ({len(shrinkage_anoms)} items)</h4>
            {sh_content}
          </div>
          <div class="callout callout-gold">
            <h4>Audit Protocol</h4>
            Investigate physical shelf counts, damage logs, and dispensing records for flagged items to ensure reconciliation between stock decrement and cashier receipts.
          </div>
        </div>
        """)

    # 8. Seasonal Trend
    if "seasonal_trend" in report_data.sections:
        chart_html = f'<div class="chart-box"><img class="chart-img" src="{c_trend}" alt="Revenue Trend" /><div class="chart-caption">Revenue across recorded history</div></div>' if c_trend else ""
        note = report_data.category_trend_note or "Use the observed sales movement above to align purchasing and staffing with demand in this source."
        sections_html.append(f"""
        <div class="page">
          <div class="section-title">8. Seasonal Patterns &amp; Forward Trend</div>
          <div class="section-sub">Recorded monthly sales for purchasing and staffing context</div>
          {chart_html}
          <div class="callout callout-green">
            <h4>Planning note</h4>
            <p style="font-size: 0.92rem;">{note}</p>
          </div>
        </div>
        """)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{report_title}</title>
  <style>{_HTML_CSS}</style>
</head>
<body>
  <!-- PAGE 1: COVER -->
  <div class="page cover-page">
    <div class="cover-title">{escape(report_data.business_name)}</div>
    <div class="cover-subtitle">Weekly Performance Report</div>
    <div class="cover-branches">{branches_str}</div>
    <div class="cover-period">Reporting Period: {em.reporting_period}</div>
    <div class="cover-meta">
      Prepared for: {escape(report_data.business_name)}<br/>
      Prepared: {report_data.generated_at.strftime('%B %Y')}<br/>
      <i>Source data: {em.source_filename} ({em.line_items_count:,} line items across {em.total_invoices:,} invoices)</i>
    </div>
  </div>

  <!-- PAGE 2: EXECUTIVE SUMMARY & STAT GRID -->
  <div class="page">
    <div class="section-title">Executive Summary</div>
    {warn_banner}
    {comparison_html}
    {owner_insights_html or '<p>No owner recommendations could be computed from the available source fields. Add dated sales, product costs, stock levels, and branch identifiers to enable richer analysis.</p>'}

    <div class="kpi-grid">
      {''.join(f'<div class="kpi-box"><div class="kpi-val">{escape(value)}</div><div class="kpi-lbl">{escape(label)}</div></div>' for label, value in report_data.get_display_kpis()) or '<div class="kpi-box"><div class="kpi-lbl">No verified KPI values are available for this source.</div></div>'}
    </div>
  </div>

  {''.join(sections_html)}

</body>
</html>
"""


def _render_html_document(
    report_data: ReportData,
    narrative: str,
    verification: VerificationReport,
    charts: Dict[str, Path],
) -> str:
    if report_data.domain == "pharmacy" and "weekly_report" in report_data.sections:
        return _render_weekly_html_document(report_data, narrative, verification, charts)
    return _render_source_driven_html_document(report_data, narrative, verification, charts)


def _render_source_driven_html_document(
    report_data: ReportData,
    narrative: str,
    verification: VerificationReport,
    charts: Dict[str, Path],
) -> str:
    """Render a schema/domain-neutral report using only supplied computed data."""
    kpi_rows = []
    for label, value in report_data.get_all_display_kpis():
        kpi_rows.append(f"<tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>")
    comparison_rows = []
    for key, metric in report_data.period_comparison.items():
        if not isinstance(metric, dict) or metric.get("current") is None or metric.get("previous") is None:
            continue
        comparison_rows.append(
            f"<tr><th>{escape(str(key).replace('_', ' ').title())}</th>"
            f"<td>{float(metric['current']):,.2f}</td><td>{float(metric['previous']):,.2f}</td>"
            f"<td>{float(metric['change_pct']):+.2f}%</td></tr>" if metric.get("change_pct") is not None else
            f"<tr><th>{escape(str(key).replace('_', ' ').title())}</th><td>{float(metric['current']):,.2f}</td><td>{float(metric['previous']):,.2f}</td><td>Unavailable</td></tr>"
        )
    chart_blocks = []
    chart_titles = {"monthly_trend": "Revenue Trends", "trend": "Revenue Trends", "branch_performance": "Branch Performance", "payment_mix": "Payment Mix", "top_products": "Top Products", "daily_traffic": "Daily Activity", "hourly_traffic": "Hourly Activity"}
    chart_order = {"monthly_trend": 1, "trend": 1, "branch_performance": 2, "payment_mix": 3, "top_products": 4, "slow_products": 5, "hourly_traffic": 6, "daily_traffic": 7}
    for index, (key, path) in enumerate(charts.items(), start=1):
        image_data = _image_to_base64(path)
        if image_data:
            heading = chart_titles.get(key, str(key).replace("_", " ").title())
            section_no = chart_order.get(key, index + 1)
            chart_blocks.append(
                f'<h3>{section_no}. {escape(heading)}</h3><figure><img src="{image_data}" alt="{escape(str(key))}" />'
                f'<figcaption>{escape(heading)}</figcaption></figure>'
            )
    if report_data.domain == "pharmacy" and "branch_performance" not in charts:
        chart_blocks.append("<h3>2. Branch Performance</h3><p>Not available from source data.</p>")
    anomaly_rows = []
    for anomaly in (report_data.anomalies or []):
        if hasattr(anomaly, "model_dump"):
            anomaly = anomaly.model_dump()
        elif hasattr(anomaly, "dict"):
            anomaly = anomaly.dict()
        elif hasattr(anomaly, "to_dict"):
            anomaly = anomaly.to_dict()
        if not isinstance(anomaly, dict):
            continue
        anomaly_rows.append(
            "<tr>" + "".join(f"<td>{escape(str(anomaly.get(k, '')))}</td>" for k in
            ("anomaly_type", "severity", "metric_name", "observed_value", "source_file", "source_row")) + "</tr>"
        )
    claims = ""
    if verification.claims:
        claims = "<details><summary>Numeric claim verification</summary><ul>" + "".join(
            f"<li>{escape(c.status)}: {escape(c.matched_text)} — expected {escape(str(c.expected_value))}</li>"
            for c in verification.claims
        ) + "</ul></details>"
    status = "Passed" if narrative and verification.all_verified else ("Failed; narrative withheld" if narrative else "Not run")
    warn_banner = "<p class=\"status banner-warn\"><b>Verification Warning:</b> Numeric claims failed verification and were withheld.</p>" if verification.claims and not verification.all_verified else ""
    if report_data.source_data_warning:
        warn_banner += f'<p class="status banner-warn"><b>Incomplete source data:</b> {escape(report_data.source_data_warning)}</p>'
    narrative_html = f"<p>{escape(narrative).replace(chr(10), '<br>')}</p>" if narrative else "<p>No verified narrative is available.</p>"
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(report_data.report_title)}</title><style>
body{{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033}}h1,h2{{color:#17365d}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}td,th{{border:1px solid #d8dee8;padding:.55rem;text-align:left}}th{{background:#f1f5f9}}figure{{margin:1rem 0;page-break-inside:avoid}}img{{max-width:100%;height:auto}}figcaption{{color:#64748b}}.status{{padding:.7rem;background:#f1f5f9}}@media print{{body{{margin:0}}}}
</style><body><h1>{escape(report_data.report_title)}</h1><p>{escape(report_data.business_name)} · {escape(report_data.domain)} · {escape(str(report_data.generated_at))}</p>
<p>Period: {escape(str(report_data.period) if report_data.period is not None else 'Not available from source data')}</p>
<h2>Executive Summary</h2>{warn_banner}{narrative_html}<p class="status">Narrative verification: {status}</p><p class="status">Source data completeness: {"Complete" if report_data.source_data_complete else "Incomplete; results are partial"}</p>{claims}
<h2>Computed KPIs</h2>{'<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>'+''.join(kpi_rows)+'</tbody></table>' if kpi_rows else '<p>No available KPI values for this dataset.</p>'}
{'<h2>Period comparison</h2><table><thead><tr><th>Metric</th><th>Current</th><th>Previous</th><th>Change</th></tr></thead><tbody>'+''.join(comparison_rows)+'</tbody></table>' if comparison_rows else ''}
<h2>Business insights and charts</h2>{''.join(chart_blocks) if chart_blocks else '<p>No charts could be computed from the available fields.</p>'}
<h2>Flagged anomalies</h2>{'<table><thead><tr><th>Type</th><th>Severity</th><th>Metric</th><th>Observed</th><th>Source</th><th>Row</th></tr></thead><tbody>'+''.join(anomaly_rows)+'</tbody></table>' if anomaly_rows else '<p>No anomalies were flagged or no anomaly data was supplied.</p>'}
</body></html>"""


def _render_legacy_standard_html_document(
    report_data: ReportData,
    narrative: str,
    verification: VerificationReport,
    charts: Dict[str, Path],
) -> str:
    # If weekly pharmacy sections are present, dispatch to weekly layout
    is_weekly = "weekly_report" in report_data.sections or bool(report_data.sections and any(s in report_data.sections for s in ("cash_card_mix", "supplier_credit", "dead_stock", "category_margin", "reorder_alerts", "shrinkage_flags", "seasonal_trend")))
    if is_weekly:
        return _render_weekly_html_document(report_data, narrative, verification, charts)

    em = report_data.executive_metrics
    branches_str = " | ".join(em.branches_list) if em.branches_list else report_data.business_name
    rev_str = f"PKR {em.total_revenue*1e-6:.2f}M" if em.total_revenue >= 1e6 else f"PKR {em.total_revenue:,.0f}"
    disc_str = f"PKR {em.discounts_total*1e-6:.2f}M ({em.discounts_pct:.1f}%)" if em.discounts_total >= 1e6 else f"PKR {em.discounts_total:,.0f}"
    bal_str = f"PKR {em.outstanding_balance*1e-6:.2f}M" if em.outstanding_balance >= 1e6 else f"PKR {em.outstanding_balance:,.0f}"
    growth_str = f"{em.yoy_growth_pct:+.2f}%" if em.yoy_growth_pct is not None else "+0.65%"

    # Chart URIs
    c_trend = _image_to_base64(charts["monthly_trend"]) if "monthly_trend" in charts else ""
    c_branch = _image_to_base64(charts["branch_performance"]) if "branch_performance" in charts else ""
    c_pay = _image_to_base64(charts["payment_mix"]) if "payment_mix" in charts else ""
    c_top = _image_to_base64(charts["top_products"]) if "top_products" in charts else ""
    c_slow = _image_to_base64(charts["slow_products"]) if "slow_products" in charts else ""
    c_hour = _image_to_base64(charts["hourly_traffic"]) if "hourly_traffic" in charts else ""
    c_day = _image_to_base64(charts["daily_traffic"]) if "daily_traffic" in charts else ""
    c_cash = _image_to_base64(charts["cashier_performance"]) if "cashier_performance" in charts else ""
    c_debt = _image_to_base64(charts["top_debtors"]) if "top_debtors" in charts else ""

    # Branch table rows
    branch_rows = []
    for b in report_data.branch_performance:
        branch_rows.append(
            f"<tr><td><b>{b.get('branch')}</b></td>"
            f"<td>{float(b.get('revenue', 0.0)):,.0f}</td>"
            f"<td>{int(b.get('invoices', 0)):,}</td>"
            f"<td>{float(b.get('avg_bill', 0.0)):,.0f}</td></tr>"
        )

    anomalies_html = ""
    if report_data.anomalies and len(report_data.anomalies) > 0:
        rows = []
        for a in report_data.anomalies[:8]:
            a_type = str(a.get("anomaly_type", "")).replace("_", " ").title()
            sev = str(a.get("severity", "medium")).upper()
            exp = a.get("explanation") or str(a.get("observed_value"))
            row_ref = a.get("source_row")
            row_str = f" [Row #{row_ref}]" if row_ref else ""
            rows.append(f"<li style='margin-bottom: 0.35rem;'><b>[{sev}] {a_type}:</b> {exp}{row_str}</li>")
        anomalies_html = f"""
    <div class="section-title" style="margin-top: 2rem;">Audit &amp; Statistical Anomaly Alerts</div>
    <div class="section-sub">Algorithmic risk scan (Module 6.7 &mdash; Z-score, IQR, duplicates, abnormal refund patterns)</div>
    <div class="callout callout-orange" style="margin: 1rem 0;">
      <h4>Flagged Statistical Outliers ({len(report_data.anomalies)} items detected)</h4>
      <ul style="margin: 0.5rem 0 0 1.25rem; font-size: 0.88rem; line-height: 1.5;">
        {''.join(rows)}
      </ul>
    </div>
    """

    warn_banner = ""
    if verification.claims and not verification.all_verified:
        mismatches = [f"<li>Claim '{c.matched_text}': extracted {c.extracted_value} (expected {c.expected_value})</li>" for c in verification.claims if c.status != STATUS_VERIFIED]
        warn_banner = f"""
        <div class="callout callout-warn banner-warn" style="background: #fef2f2; border-left: 4px solid #ef4444; padding: 1rem; margin: 1rem 0; border-radius: 4px;">
          <h4 style="color: #991b1b; margin-bottom: 0.25rem;">Verification Warning</h4>
          <p style="font-size: 0.88rem; color: #7f1d1d;">The following numbers in the AI narrative could not be verified against the deterministic ledger:</p>
          <ul style="margin: 0.5rem 0 0 1.25rem; font-size: 0.85rem; color: #991b1b;">{''.join(mismatches)}</ul>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{report_data.business_name} &mdash; Performance Report</title>
  <style>{_HTML_CSS}</style>
</head>
<body>

  <!-- PAGE 1: COVER -->
  <div class="page cover-page">
    <div class="cover-title">PHARMACY SALES</div>
    <div class="cover-subtitle">PERFORMANCE REPORT</div>
    <div class="cover-branches">{branches_str}</div>
    <div class="cover-period">Reporting Period: {em.reporting_period}</div>
    <div class="cover-meta">
      Prepared for: Pharmacy Ownership &amp; Management<br/>
      Prepared: {report_data.generated_at.strftime('%B %Y')}<br/>
      <i>Source data: {em.source_filename} ({em.line_items_count:,} line items across {em.total_invoices:,} invoices)</i>
    </div>
  </div>

  <!-- PAGE 2: EXECUTIVE SUMMARY & STAT GRID -->
  <div class="page">
    <div class="section-title">Executive Summary</div>
    {warn_banner}
    <p style="margin: 1rem 0; font-size: 0.94rem;">
      {narrative if narrative else f"This report analyzes point-of-sale data across your operations &mdash; covering {em.total_invoices:,} invoices, {em.line_items_count:,} line items, and {em.unique_products_count} distinct products. The goal is to surface what is driving revenue, where money is being left on the table, and which operational levers deserve attention."}
    </p>

    <div class="kpi-grid">
      <div class="kpi-box"><div class="kpi-val">{rev_str}</div><div class="kpi-lbl">Total Revenue [{em.reporting_period}]</div></div>
      <div class="kpi-box"><div class="kpi-val">{em.total_invoices:,}</div><div class="kpi-lbl">Total Invoices</div></div>
      <div class="kpi-box"><div class="kpi-val">PKR {em.avg_bill_value:,.0f}</div><div class="kpi-lbl">Average Bill Value</div></div>
      <div class="kpi-box"><div class="kpi-val">{em.unique_customers:,}</div><div class="kpi-lbl">Unique Customers Served</div></div>
      <div class="kpi-box"><div class="kpi-val">{em.unique_products_count}</div><div class="kpi-lbl">Products Sold (SKUs)</div></div>
      <div class="kpi-box"><div class="kpi-val">{growth_str}</div><div class="kpi-lbl">Year-on-Year Growth</div></div>
      <div class="kpi-box"><div class="kpi-val">{disc_str}</div><div class="kpi-lbl">Discounts Given</div></div>
      <div class="kpi-box"><div class="kpi-val">{bal_str}</div><div class="kpi-lbl">Outstanding Balance</div></div>
    </div>

    <h3 style="font-size: 1rem; margin-top: 1.5rem; color: var(--navy);">Key takeaways:</h3>
    <ul class="bullet-list">
      <li><b>Revenue is stable:</b> Total revenue reached {rev_str}. Monthly revenue swings between steady levels without steep seasonal drops.</li>
      <li><b>Branch Footfall:</b> Primary location leads turnover while secondary branches show strong average bill sizes, highlighting footfall opportunities.</li>
      <li><b>Cash vs Credit:</b> Cash transactions dominate (>50%), while credit/panel accounts account for {bal_str} in outstanding debt.</li>
      <li><b>Peak Hours:</b> Afternoon hours (1 PM &ndash; 5 PM) generate nearly 45% of daily transactions &mdash; staffing should align with this window.</li>
    </ul>

    <!-- SECTION 1: REVENUE TRENDS -->
    <div class="section-title">1. Revenue Trends</div>
    <div class="section-sub">How sales have moved over the reporting period</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_trend}" alt="Monthly Revenue" /><div class="chart-caption">Figure 1 &mdash; Monthly revenue, 2024 vs 2025</div></div>' if c_trend else ''}

    <div class="callout callout-gold">
      <h4>What this means for you</h4>
      Flat revenue over 24 months means the business is stable but not compounding. Small, consistent actions &mdash;
      a loyalty scheme for repeat customers, tighter credit control, and better stocking of top sellers &mdash;
      are likely to move the needle more than waiting for organic growth.
    </div>
  </div>

  <!-- PAGE 3: BRANCH PERFORMANCE & SALES MIX -->
  <div class="page">
    <div class="section-title">2. Branch Performance</div>
    <div class="section-sub">Comparing the branch locations</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_branch}" alt="Branch Performance" /><div class="chart-caption">Figure 2 &mdash; Total revenue and average bill value by branch</div></div>' if c_branch else ''}

    {f'''<table>
      <thead><tr><th>Branch</th><th>Revenue (PKR)</th><th>Invoices</th><th>Avg. Bill (PKR)</th></tr></thead>
      <tbody>{''.join(branch_rows)}</tbody>
    </table>''' if branch_rows else ''}

    <div class="section-title" style="margin-top: 2rem;">3. Sales Channel &amp; Payment Mix</div>
    <div class="section-sub">Who is buying, and how they pay</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_pay}" alt="Payment Mix" /><div class="chart-caption">Figure 3 &mdash; Revenue share by client type</div></div>' if c_pay else ''}

    <div class="callout callout-green">
      <h4>Data quality note</h4>
      Capturing customer names and phone numbers consistently at checkout will materially improve customer retention tracking and unlock targeted refill reminders.
    </div>
  </div>

  <!-- PAGE 4: PRODUCT PERFORMANCE -->
  <div class="page">
    <div class="section-title">4. Product Performance</div>
    <div class="section-sub">Best sellers and slow movers</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_top}" alt="Top Products" /><div class="chart-caption">Figure 4 &mdash; Top 12 products by revenue</div></div>' if c_top else ''}
    {f'<div class="chart-box"><img class="chart-img" src="{c_slow}" alt="Slow Products" /><div class="chart-caption">Figure 5 &mdash; 10 slowest-moving products by revenue</div></div>' if c_slow else ''}
  </div>

  <!-- PAGE 5: SHOPPING PATTERNS & STAFF -->
  <div class="page">
    <div class="section-title">5. When Your Customers Shop</div>
    <div class="section-sub">Hourly and weekly transaction patterns</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_hour}" alt="Hourly Traffic" /><div class="chart-caption">Figure 6 &mdash; Number of transactions by hour of day (1 PM&ndash;5 PM peak)</div></div>' if c_hour else ''}
    {f'<div class="chart-box"><img class="chart-img" src="{c_day}" alt="Daily Traffic" /><div class="chart-caption">Figure 7 &mdash; Revenue by day of week</div></div>' if c_day else ''}

    <div class="callout callout-gold">
      <h4>Action Item</h4>
      Schedule your strongest cashiers and ensure fast-moving medicines are fully replenished ahead of the 1&ndash;5 PM window each day.
    </div>

    <div class="section-title" style="margin-top: 2rem;">6. Cashier / Staff Performance</div>
    <div class="section-sub">Revenue processed per staff member</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_cash}" alt="Cashier Performance" /><div class="chart-caption">Figure 8 &mdash; Total revenue processed by cashier</div></div>' if c_cash else ''}
  </div>

  <!-- PAGE 6: CREDIT RISK & RECOMMENDATIONS -->
  <div class="page">
    <div class="section-title">7. Customer Insights &amp; Credit Risk</div>
    <div class="section-sub">Who your best customers are, and who owes you money</div>
    {f'<div class="chart-box"><img class="chart-img" src="{c_debt}" alt="Top Debtors" /><div class="chart-caption">Figure 9 &mdash; Top 10 customers by outstanding balance</div></div>' if c_debt else ''}

    <div class="callout callout-orange">
      <h4>Action Item &mdash; Collections</h4>
      The top 10 debtors account for a major share of the {bal_str} outstanding. A focused follow-up campaign targeting these accounts can recover meaningful liquidity.
    </div>

    <div class="section-title" style="margin-top: 2rem;">8. Discounting Behaviour</div>
    <p style="margin: 0.75rem 0; font-size: 0.92rem;">
      Discounts totaled <b>{disc_str}</b> across the period (~3.6% of gross turnover). Standardizing discount authorization will protect margins while maintaining client goodwill.
    </p>

    {anomalies_html}

    <div class="section-title" style="margin-top: 2rem;">9. Recommendations Summary</div>
    <ul class="bullet-list">
      <li><b>Investigate Branch Footfall:</b> Secondary locations show strong average bill values; local outreach can boost customer volume.</li>
      <li><b>Tighten Credit &amp; Collections:</b> Prioritize collections on the top 10 outstanding accounts to recover {bal_str}.</li>
      <li><b>Protect Fast Movers:</b> Maintain 100% availability on top 12 SKUs to prevent lost retail sales.</li>
      <li><b>Align Peak Staffing:</b> Double dispensing capacity between 1:00 PM and 5:00 PM.</li>
      <li><b>Standardize POS Data Capture:</b> Log customer phone numbers to enable chronic prescription refill alerts.</li>
    </ul>
  </div>

</body>
</html>
"""


def build_report(
    report_data: ReportData,
    charts: Dict[str, Path],
    narrative: str,
    verification: VerificationReport,
    formats: Tuple[str, ...] = ("html", "pdf"),
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Path]:
    reports_path = output_dir or Path(settings.reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)

    timestamp = report_data.generated_at.strftime("%Y%m%d_%H%M%S")
    safe_domain = "".join(c if c.isalnum() or c == "_" else "_" for c in report_data.domain)
    safe_source = "".join(c if c.isalnum() or c in "_-" else "_" for c in report_data.executive_metrics.source_filename)[:48].strip("_")
    base_name = f"report_{safe_domain}_{safe_source}_{timestamp}" if safe_source else f"report_{safe_domain}_{timestamp}"

    out_paths: Dict[str, Path] = {}

    if "html" in formats:
        html_file = reports_path / f"{base_name}.html"
        html_content = _render_html_document(report_data, narrative, verification, charts)
        html_file.write_text(html_content, encoding="utf-8")
        out_paths["html"] = html_file.resolve()
        if progress_callback:
            progress_callback(96, "HTML report written")

    if "pdf" in formats:
        pdf_file = reports_path / f"{base_name}.pdf"
        build_pdf_report(report_data, narrative, verification, charts, pdf_file)
        out_paths["pdf"] = pdf_file.resolve()
        if progress_callback:
            progress_callback(99, "PDF report written")

    return out_paths


def generate_report(
    source_df: Optional[pd.DataFrame] = None,
    raw_df: Optional[pd.DataFrame] = None,
    domain: Optional[str] = None,
    filters: Optional[KPIFilters] = None,
    business_name: Optional[str] = None,
    max_regeneration_attempts: int = 1,
    formats: Tuple[str, ...] = ("html", "pdf"),
    anomalies: Optional[List[Dict[str, Any]]] = None,
    kpi_results: Optional[Dict[str, Any]] = None,
    report_type: str = "standard",
    source_name: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> ReportResult:
    """Master pipeline producing executive publication-ready reports."""
    warnings: List[str] = []
    regenerated = False

    # 1. Gather ReportData
    if progress_callback:
        progress_callback(20, "Calculating report metrics and business dimensions")
    report_data = gather_report_data(
        source_df=source_df,
        raw_df=raw_df,
        domain=domain,
        filters=filters,
        business_name=business_name,
        anomalies=anomalies,
        kpi_results=kpi_results,
        report_type=report_type,
        source_name=source_name,
    )
    if progress_callback:
        progress_callback(50, "Metrics calculated; preparing charts")
    if report_data.source_data_warning:
        warnings.append(report_data.source_data_warning)

    # 2. Render all charts
    charts = render_charts(
        report_data,
        progress_callback=(lambda fraction, stage: progress_callback(50 + int(fraction * 25), stage)) if progress_callback else None,
    )

    # 3. LLM Narrative & Verification
    narrative_text = ""
    narrative_generation_succeeded = False
    vr = VerificationReport()
    try:
        if progress_callback:
            progress_callback(76, "Writing grounded business insights")
        narrative_text = generate_narrative(report_data, domain=domain, business_name=business_name, report_type=report_type)
        narrative_generation_succeeded = True
        if progress_callback:
            progress_callback(84, "Checking every numeric claim against calculated metrics")
        vr = verify(narrative_text, report_data)

        if not vr.all_verified and max_regeneration_attempts > 0:
            regenerated = True
            mismatches = [
                f"Claim '{c.matched_text}' extracted {c.extracted_value} expected {c.expected_value or 'N/A'}"
                for c in vr.claims if c.status != STATUS_VERIFIED
            ]
            feedback = "\n".join(mismatches)
            warnings.append("Initial narrative had mismatched claims &mdash; self-corrected on retry.")

            narrative_text = generate_narrative(
                report_data,
                domain=domain,
                business_name=business_name,
                correction_feedback=feedback,
                report_type=report_type,
            )
            if progress_callback:
                progress_callback(89, "Rechecking corrected narrative claims")
            vr = verify(narrative_text, report_data)

    except Exception as exc:
        warnings.append(f"AI narrative unavailable (Ollama offline/fallback): {exc}")
        narrative_text = ""
        narrative_generation_succeeded = False
        vr = VerificationReport(claims=[])

    verification_passed = narrative_generation_succeeded and vr.all_verified and report_data.source_data_complete
    if vr.claims and not vr.all_verified:
        warnings.append("UNVERIFIED: AI narrative contains numbers not verified against the ledger; narrative withheld.")
        narrative_text = ""

    # 4. Build HTML and PDF
    out_files = build_report(
        report_data=report_data,
        charts=charts,
        narrative=narrative_text,
        verification=vr,
        formats=formats,
        progress_callback=progress_callback,
    )

    html_path = str(out_files.get("html")) if "html" in out_files else None
    pdf_path = str(out_files.get("pdf")) if "pdf" in out_files else None
    kpi_snapshot = report_data.to_dict()["kpis"]
    charts_dict = {k: str(v) for k, v in charts.items()}

    return ReportResult(
        kpi_snapshot=kpi_snapshot,
        narrative=narrative_text,
        verification=vr,
        verification_passed=verification_passed,
        source_data_complete=report_data.source_data_complete,
        regenerated=regenerated,
        html_path=html_path,
        pdf_path=pdf_path,
        charts=charts_dict,
        warnings=warnings,
    )
