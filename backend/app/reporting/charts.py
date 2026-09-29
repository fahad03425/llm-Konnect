"""
Module 6.8 (Verified Report Generator) — charts.py

Deterministic, publication-quality static chart generation using Matplotlib (Agg backend).
Matches executive pharmacy performance report aesthetics:
- Figure 1: Monthly Revenue (2024 vs 2025 / Historical Trend)
- Figure 2: Total Revenue by Branch (Horizontal Bar)
- Figure 3: Revenue Share by Client Type (Donut Chart - strictly circular)
- Figure 4: Top 12 Products by Revenue (Horizontal Teal Bar)
- Figure 5: 10 Slowest-Moving Products by Revenue (Horizontal Coral Bar)
- Figure 6: Number of Transactions by Hour of Day (Peak Hours Highlighted)
- Figure 7: Revenue by Day of Week (Bar Chart)
- Figure 8: Cashier Performance — Revenue Processed (Horizontal Navy Bar)
- Figure 9: Top 10 Customers by Outstanding Balance (Horizontal Orange Bar)
- Figure 10: Stock Expiry Risk by Time Window (if expiry metrics present)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from app.core.config import settings
from app.reporting.models import ReportData

# ══════════════════════════════════════════════════════════════════════════════
# Crisp, Publication-Ready Typography & Layout Styles
# ══════════════════════════════════════════════════════════════════════════════
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Arial", "DejaVu Sans", "Helvetica", "sans-serif"]
plt.rcParams["font.size"] = 9.0
plt.rcParams["axes.titlesize"] = 11.0
plt.rcParams["axes.titleweight"] = "bold"
plt.rcParams["axes.titlepad"] = 12
plt.rcParams["axes.labelsize"] = 8.5
plt.rcParams["axes.labelweight"] = "normal"
plt.rcParams["axes.edgecolor"] = "#cbd5e1"
plt.rcParams["axes.linewidth"] = 0.8
plt.rcParams["text.color"] = "#0f172a"
plt.rcParams["axes.labelcolor"] = "#475569"
plt.rcParams["xtick.color"] = "#475569"
plt.rcParams["ytick.color"] = "#475569"
plt.rcParams["xtick.labelsize"] = 8.0
plt.rcParams["ytick.labelsize"] = 8.0
plt.rcParams["figure.autolayout"] = False


def _format_rupee_axis(x, pos=None):
    """Format large numbers for axis labels (e.g. 500k, 1.5M, 20.0M)."""
    if abs(x) >= 1_000_000:
        return f"{x*1e-6:.1f}M" if (x % 1_000_000 != 0) else f"{x*1e-6:.0f}M"
    if abs(x) >= 1_000:
        return f"{x*1e-3:.0f}k"
    return f"{x:.0f}"


def render_monthly_revenue_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 1: Monthly Revenue (2024 vs 2025 or timeline)."""
    trend_data = report_data.monthly_trend
    if not trend_data:
        rev_trend = report_data.get_kpi("revenue_trend")
        if rev_trend and getattr(rev_trend, "series", None):
            trend_data = rev_trend.series

    if not trend_data:
        return None

    fig, ax = plt.subplots(figsize=(7.0, 2.8), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    has_years = any("year" in pt for pt in trend_data)
    months_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if has_years:
        years = sorted({str(pt.get("year")) for pt in trend_data if pt.get("year") is not None})[-5:]
        palette = ["#1e3a5f", "#0d7377", "#d97706", "#7c3aed", "#ea580c"]
        for year_idx, year in enumerate(years):
            year_rows = [pt for pt in trend_data if str(pt.get("year")) == year]
            points = sorted(
                ((int(pt.get("month_num", 1)) - 1, float(pt.get("revenue") or 0.0)) for pt in year_rows),
                key=lambda item: item[0],
            )
            if not points:
                continue
            xs, ys = zip(*points)
            ax.plot(xs, ys, marker="o", markersize=5, color=palette[year_idx % len(palette)], linewidth=2.2, label=year)
            if len(years) == 1:
                ax.fill_between(xs, ys, color=palette[year_idx % len(palette)], alpha=0.10)
        ax.set_xticks(range(12))
        ax.set_xticklabels(months_order, fontsize=8.0, color="#334155")
    else:
        labels = [str(pt.get("period") or f"P{i+1}") for i, pt in enumerate(trend_data[:12])]
        values = [float(pt.get("revenue") or pt.get("value") or 0.0) for pt in trend_data[:12]]
        ax.plot(range(len(labels)), values, marker="o", markersize=4.2, color="#1e3a5f", linewidth=1.8, label="Monthly Revenue")
        ax.fill_between(range(len(labels)), values, color="#1e3a5f", alpha=0.05)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8.0, color="#334155")

    ax.yaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_ylabel("Revenue (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Monthly sales trend", fontsize=13.0, fontweight="bold", color="#0f172a", pad=12)

    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="upper right", frameon=True, facecolor="#ffffff", edgecolor="#e2e8f0", fontsize=8.0)
    ax.tick_params(axis="both", labelsize=8.0, colors="#334155")
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_branch_performance_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 2: Total Revenue by Branch (Horizontal Bar Chart)."""
    branches = report_data.branch_performance
    if not branches:
        return None

    names = [str(b.get("branch", "Branch")) for b in branches]
    values = [float(b.get("revenue", 0.0)) for b in branches]
    avg_bills = [float(b.get("avg_bill", 0.0)) for b in branches]

    names.reverse()
    values.reverse()
    avg_bills.reverse()

    colors = ["#1e3a5f", "#0d7377", "#d97706"]
    bar_colors = [colors[i % len(colors)] for i in range(len(names))]

    fig, ax = plt.subplots(figsize=(7.0, 2.3), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(names, values, color=bar_colors, height=0.52, edgecolor="none")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Revenue (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Revenue by branch", fontsize=13.0, fontweight="bold", color="#0f172a", pad=12)
    ax.tick_params(axis="both", labelsize=8.0, colors="#334155")

    for bar, val, bill in zip(bars, values, avg_bills):
        w = bar.get_width()
        bill_str = f" (Avg bill: {bill:,.0f})" if bill > 0 else ""
        ax.text(w * 1.01, bar.get_y() + bar.get_height()/2, f" PKR {val*1e-6:.2f}M{bill_str}",
                va="center", ha="left", fontsize=7.6, color="#1e293b", fontweight="600")

    if values:
        ax.set_xlim(0, max(values) * 1.35)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_payment_mix_donut(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 3: Revenue Share by Client Type (Strictly Circular Donut Chart)."""
    mix = report_data.payment_mix
    if not mix:
        return None

    labels = [str(m.get("client_type", "Other")) for m in mix]
    values = [float(m.get("revenue", 0.0)) for m in mix]
    total_val = sum(values) if sum(values) > 0 else 1.0
    colors = ["#1e3a5f", "#0d7377", "#d97706", "#f97316", "#64748b"]

    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    ax.axis("equal")  # Strict 1:1 circle, preventing any oval squishing!

    wedges, texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct="%1.1f%%",
        pctdistance=0.76,
        startangle=140,
        colors=colors[:len(values)],
        wedgeprops=dict(width=0.38, edgecolor="#ffffff", linewidth=2),
    )

    for at in autotexts:
        at.set_color("#ffffff")
        at.set_fontsize(8.0)
        at.set_fontweight("bold")

    tot_str = f"PKR {total_val*1e-6:.1f}M" if total_val >= 1e6 else f"PKR {total_val:,.0f}"
    ax.text(0, 0, f"Total Sales\n{tot_str}", ha="center", va="center", fontsize=8.8, fontweight="bold", color="#0f172a")

    legend_labels = []
    for l, v in zip(labels, values):
        pct = (v / total_val) * 100.0
        v_str = f"PKR {v*1e-6:.1f}M" if v >= 1e6 else f"PKR {v*1e-3:.0f}k"
        legend_labels.append(f"{l}: {pct:.1f}% ({v_str})")

    ax.legend(wedges, legend_labels, loc="center left", bbox_to_anchor=(0.95, 0.5), frameon=True, facecolor="#f8fafc", edgecolor="#e2e8f0", fontsize=8.0)
    ax.set_title("Revenue Share by Client Type", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_top_products_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 4: Top 12 Products by Revenue (Horizontal Teal Bar)."""
    products = report_data.top_products[:12]
    if not products:
        cat_kpi = report_data.get_kpi("revenue_breakdown_by_category")
        if cat_kpi and getattr(cat_kpi, "breakdown", None):
            products = [{"name": b.get("category", "Category"), "amount": b.get("amount", 0.0)} for b in cat_kpi.breakdown]
    if not products:
        return None

    names = [str(p.get("name", "Product")) for p in products]
    values = [float(p.get("amount", 0.0)) for p in products]

    names.reverse()
    values.reverse()

    fig, ax = plt.subplots(figsize=(7.0, 3.6), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(names, values, color="#0d7377", height=0.58, edgecolor="none")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Revenue (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Top 12 Products by Revenue", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)
    ax.tick_params(axis="both", labelsize=7.8, colors="#334155")

    for bar in bars:
        w = bar.get_width()
        lbl = f" {_format_rupee_axis(w)}"
        ax.text(w * 1.01, bar.get_y() + bar.get_height()/2, lbl, va="center", ha="left", fontsize=7.5, color="#0f172a")

    if values:
        ax.set_xlim(0, max(values) * 1.20)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_slow_products_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 5: 10 Slowest-Moving Products by Revenue (Horizontal Coral Bar)."""
    products = report_data.slow_products[:10]
    if not products:
        return None

    names = [str(p.get("name", "Product")) for p in products]
    values = [float(p.get("amount", 0.0)) for p in products]

    names.reverse()
    values.reverse()

    fig, ax = plt.subplots(figsize=(7.0, 3.1), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(names, values, color="#ea580c", height=0.58, edgecolor="none")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Revenue (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("10 Slowest-Moving Products by Revenue", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)
    ax.tick_params(axis="both", labelsize=7.8, colors="#334155")

    for bar in bars:
        w = bar.get_width()
        lbl = f" {_format_rupee_axis(w)}"
        ax.text(w * 1.01, bar.get_y() + bar.get_height()/2, lbl, va="center", ha="left", fontsize=7.5, color="#0f172a")

    if values:
        ax.set_xlim(0, max(values) * 1.22)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_hourly_traffic_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 6: Number of Transactions by Hour of Day (Peak hours highlighted)."""
    traffic = report_data.hourly_traffic
    if not traffic:
        return None

    hours = [int(t.get("hour", 0)) for t in traffic]
    counts = [int(t.get("count", 0)) for t in traffic]
    labels = [f"{h:02d}:00" for h in hours]

    peak_idx = max(range(len(counts)), key=counts.__getitem__)
    colors = ["#d97706" if idx == peak_idx else "#1e3a5f" for idx in range(len(hours))]

    fig, ax = plt.subplots(figsize=(7.0, 2.6), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    ax.bar(range(len(labels)), counts, color=colors, width=0.68, edgecolor="none")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=7.8, color="#334155")
    ax.set_ylabel("Number of Bills", fontsize=8.2, color="#475569")
    ax.grid(True, axis="y", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(f"Transactions by hour (busiest: {hours[peak_idx]:02d}:00)", fontsize=12.5, fontweight="bold", color="#0f172a", pad=12)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_daily_traffic_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 7: Revenue by Day of Week."""
    traffic = report_data.daily_traffic
    if not traffic:
        return None

    days = [str(t.get("day", "Day")) for t in traffic]
    revenues = [float(t.get("revenue", 0.0)) for t in traffic]

    fig, ax = plt.subplots(figsize=(7.0, 2.4), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    ax.bar(days, revenues, color="#0d7377", width=0.55, edgecolor="none")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_ylabel("Revenue (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="y", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Revenue by Day of Week", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)
    ax.tick_params(axis="both", labelsize=8.0, colors="#334155")

    # Add daily average benchmark line
    if revenues:
        avg_rev = sum(revenues) / len(revenues)
        ax.axhline(avg_rev, color="#d97706", linestyle=":", linewidth=1.2, label=f"Avg Daily: {_format_rupee_axis(avg_rev)}")
        ax.legend(loc="upper right", frameon=True, facecolor="#ffffff", edgecolor="#e2e8f0", fontsize=7.8)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_cashier_performance_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 8: Cashier Performance — Revenue Processed (Horizontal Navy Bar)."""
    cashiers = report_data.cashier_performance
    if not cashiers:
        return None

    names = [str(c.get("cashier", "Cashier")) for c in cashiers]
    revenues = [float(c.get("revenue", 0.0)) for c in cashiers]
    bills = [int(c.get("invoices", 0)) for c in cashiers]

    names.reverse()
    revenues.reverse()
    bills.reverse()

    fig, ax = plt.subplots(figsize=(7.0, 2.6), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(names, revenues, color="#1e3a5f", height=0.55, edgecolor="none")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Revenue Processed (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Cashier Performance — Total Revenue Processed", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)
    ax.tick_params(axis="both", labelsize=8.0, colors="#334155")

    for bar, rev, b_cnt in zip(bars, revenues, bills):
        w = bar.get_width()
        b_str = f" ({b_cnt} bills)" if b_cnt > 0 else ""
        ax.text(w * 1.01, bar.get_y() + bar.get_height()/2, f" PKR {rev*1e-6:.2f}M{b_str}",
                va="center", ha="left", fontsize=7.6, color="#0f172a")

    if revenues:
        ax.set_xlim(0, max(revenues) * 1.35)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_top_debtors_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 9: Top 10 Customers by Outstanding Balance (Horizontal Orange Bar)."""
    debtors = report_data.top_debtors[:10]
    if not debtors:
        return None

    names = [str(d.get("customer", "Customer")) for d in debtors]
    balances = [float(d.get("balance", 0.0)) for d in debtors]

    names.reverse()
    balances.reverse()

    fig, ax = plt.subplots(figsize=(7.0, 3.1), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(names, balances, color="#ea580c", height=0.58, edgecolor="none")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Outstanding Balance (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Top 10 Customers by Outstanding Balance", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)
    ax.tick_params(axis="both", labelsize=7.8, colors="#334155")

    for bar in bars:
        w = bar.get_width()
        ax.text(w * 1.01, bar.get_y() + bar.get_height()/2, f" PKR {w:,.0f}", va="center", ha="left", fontsize=7.5, color="#0f172a")

    if balances:
        ax.set_xlim(0, max(balances) * 1.25)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_expiry_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 10: Stock Expiry Risk."""
    near_kpi = report_data.get_kpi("expiring_value_30d") or report_data.get_kpi("near_expiry_total")
    exp_kpi = report_data.get_kpi("expired_stock_value")
    if not near_kpi and not exp_kpi:
        return None
    labels = []
    values = []
    if near_kpi and near_kpi.value is not None:
        labels.append("Near Expiry (<30d)")
        values.append(float(near_kpi.value))
    if exp_kpi and exp_kpi.value is not None:
        labels.append("Expired Stock")
        values.append(float(exp_kpi.value))
    if not values or sum(values) == 0:
        return None

    fig, ax = plt.subplots(figsize=(6.5, 2.8), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    colors = ["#f59e0b", "#ef4444"]
    bars = ax.bar(labels, values, color=colors[:len(labels)], width=0.4)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_ylabel("Value (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="y", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Stock Expiry Exposure", fontsize=10.5, fontweight="bold", color="#0f172a", pad=10)
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, h * 1.01, f" {_format_rupee_axis(h)}", ha="center", va="bottom", fontsize=7.8, color="#0f172a")
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_supplier_payables_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 11: Top Suppliers by Amount Owed (Payables)."""
    payables = report_data.supplier_payables
    if not payables:
        sp_kpi = report_data.get_kpi("supplier_payable_by_supplier")
        if sp_kpi and getattr(sp_kpi, "breakdown", None):
            payables = list(sp_kpi.breakdown)
    if not payables:
        return None

    items = []
    for sp in payables:
        s_name = str(sp.get("supplier_name") or sp.get("supplier") or sp.get("supplier_id") or "Supplier")
        if len(s_name) > 26:
            s_name = s_name[:23] + "..."
        amt = float(sp.get("total_payable") or sp.get("payable_amount") or sp.get("amount") or 0.0)
        due = sp.get("earliest_due_date") or sp.get("due_date")
        if amt > 0:
            items.append((s_name, amt, str(due) if due else None))

    if not items:
        return None

    items.sort(key=lambda x: x[1], reverse=True)
    top_items = items[:8]
    top_items.reverse()

    suppliers = [it[0] for it in top_items]
    amounts = [it[1] for it in top_items]

    fig, ax = plt.subplots(figsize=(6.8, max(2.6, len(suppliers) * 0.38 + 0.8)), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(suppliers, amounts, color="#0284c7", height=0.55)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Outstanding Balance (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Supplier Credit & Payables by Distributor", fontsize=10.5, fontweight="bold", color="#0f172a", pad=10)

    for bar, item in zip(bars, top_items):
        w = bar.get_width()
        due_lbl = f" (Due {item[2]})" if item[2] else ""
        ax.text(w * 1.01, bar.get_y() + bar.get_height() / 2, f" {_format_rupee_axis(w)}{due_lbl}", ha="left", va="center", fontsize=7.6, color="#0f172a")

    if amounts:
        ax.set_xlim(0, max(amounts) * 1.30)

    try:
        plt.tight_layout()
    except Exception:
        pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_dead_stock_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 12: Top Items by Tied-Up Dead Stock Value."""
    dead_items = report_data.dead_stock_items
    if not dead_items:
        ds_kpi = report_data.get_kpi("dead_stock_value")
        if ds_kpi and getattr(ds_kpi, "breakdown", None):
            dead_items = list(ds_kpi.breakdown)
    if not dead_items:
        return None

    items = []
    for ds in dead_items:
        p_name = str(ds.get("product_name") or ds.get("name") or ds.get("product_id") or "Product")
        if len(p_name) > 26:
            p_name = p_name[:23] + "..."
        val = float(ds.get("tied_up_value") or ds.get("dead_stock_value") or ds.get("value") or ds.get("line_value") or ds.get("stock_value") or ds.get("amount") or 0.0)
        if val > 0:
            items.append((p_name, val))

    if not items:
        return None

    items.sort(key=lambda x: x[1], reverse=True)
    top_items = items[:8]
    top_items.reverse()

    products = [it[0] for it in top_items]
    values = [it[1] for it in top_items]

    fig, ax = plt.subplots(figsize=(6.8, max(2.6, len(products) * 0.38 + 0.8)), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(products, values, color="#e11d48", height=0.55)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_rupee_axis))
    ax.set_xlabel("Tied-Up Value (PKR)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Dead Stock: Top Dormant Inventory Capital", fontsize=10.5, fontweight="bold", color="#0f172a", pad=10)

    for bar in bars:
        w = bar.get_width()
        ax.text(w * 1.01, bar.get_y() + bar.get_height() / 2, f" {_format_rupee_axis(w)}", ha="left", va="center", fontsize=7.6, color="#0f172a")

    if values:
        ax.set_xlim(0, max(values) * 1.25)

    try:
        plt.tight_layout()
    except Exception:
        pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_category_margin_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Figure 13: Gross Profit Margin by Product Category."""
    margins = report_data.category_margins
    if not margins:
        cm_kpi = report_data.get_kpi("gross_margin_by_category")
        if cm_kpi and getattr(cm_kpi, "breakdown", None):
            margins = list(cm_kpi.breakdown)
    if not margins:
        return None

    items = []
    for cm in margins:
        c_name = str(cm.get("category") or cm.get("name") or "Category")
        if len(c_name) > 26:
            c_name = c_name[:23] + "..."
        m_pct = float(cm.get("margin_pct") or cm.get("gross_margin_pct") or cm.get("margin") or 0.0)
        items.append((c_name, m_pct))

    if not items:
        return None

    items.sort(key=lambda x: x[1], reverse=True)
    top_items = items[:8]
    top_items.reverse()

    categories = [it[0] for it in top_items]
    pcts = [it[1] for it in top_items]

    fig, ax = plt.subplots(figsize=(6.8, max(2.6, len(categories) * 0.38 + 0.8)), dpi=250)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    bars = ax.barh(categories, pcts, color="#059669", height=0.55)
    ax.set_xlabel("Gross Margin (%)", fontsize=8.2, color="#475569")
    ax.grid(True, axis="x", linestyle="--", alpha=0.45, color="#e2e8f0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Gross Margin by Category", fontsize=10.5, fontweight="bold", color="#0f172a", pad=10)

    for bar in bars:
        w = bar.get_width()
        ax.text(w + 0.8, bar.get_y() + bar.get_height() / 2, f" {w:.1f}%", ha="left", va="center", fontsize=7.6, color="#0f172a")

    if pcts:
        ax.set_xlim(0, max(max(pcts) * 1.25, 20.0))

    try:
        plt.tight_layout()
    except Exception:
        pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_period_comparison_chart(report_data: ReportData, out_path: Path) -> Optional[Path]:
    """Compare only measured current/prior period KPIs, with a separate scale per metric."""
    labels = {
        "total_revenue": ("Sales revenue", "PKR"),
        "transaction_count": ("Transactions", "count"),
        "average_transaction_value": ("Average bill", "PKR"),
        "gross_profit": ("Gross profit", "PKR"),
    }
    available = []
    for key, (label, unit) in labels.items():
        row = report_data.period_comparison.get(key, {})
        if isinstance(row, dict) and row.get("current") is not None and row.get("previous") is not None:
            available.append((label, unit, float(row["previous"]), float(row["current"])))
    if not available:
        return None

    fig, axes = plt.subplots(1, len(available), figsize=(max(5.4, 3.0 * len(available)), 3.0), squeeze=False, dpi=250)
    fig.patch.set_facecolor("#ffffff")
    colors = ["#94a3b8", "#0d7377"]
    for ax, (label, unit, previous, current) in zip(axes[0], available):
        ax.set_facecolor("#ffffff")
        bars = ax.bar(["Previous", "Current"], [previous, current], color=colors, width=0.58)
        ax.set_title(label, fontsize=11, weight="bold", color="#0f172a", pad=10)
        ax.grid(True, axis="y", linestyle="--", alpha=0.35, color="#cbd5e1")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", left=False, labelleft=False)
        ax.tick_params(axis="x", labelsize=9)
        max_abs = max(abs(previous), abs(current), 1.0)
        ax.set_ylim(min(0, min(previous, current) * 1.25), max(0, max(previous, current) * 1.3))
        for bar, value in zip(bars, (previous, current)):
            text = f"PKR {value:,.0f}" if unit == "PKR" else f"{value:,.0f}"
            offset = max_abs * 0.04
            ax.text(bar.get_x() + bar.get_width() / 2, value + (offset if value >= 0 else -offset), text,
                    ha="center", va="bottom" if value >= 0 else "top", fontsize=8, color="#334155", weight="bold")
    fig.suptitle("Current period compared with previous period", fontsize=14, weight="bold", color="#0f172a", y=1.03)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_charts(report_data: ReportData, output_dir: Optional[Path] = None, progress_callback=None) -> Dict[str, Path]:
    """
    Generate all relevant static chart PNG images for the publication report.
    """
    base_dir = output_dir or (Path(settings.reports_dir) / "charts" / report_data.generated_at.strftime("%Y%m%d_%H%M%S"))
    base_dir.mkdir(parents=True, exist_ok=True)

    charts: Dict[str, Path] = {}

    chart_tasks = [
        ("period_comparison", "period_comparison.png", render_period_comparison_chart),
        ("monthly_trend", "monthly_trend.png", render_monthly_revenue_chart),
        ("branch_performance", "branch_performance.png", render_branch_performance_chart),
        ("payment_mix", "payment_mix.png", render_payment_mix_donut),
        ("top_products", "top_products.png", render_top_products_chart),
        ("slow_products", "slow_products.png", render_slow_products_chart),
        ("hourly_traffic", "hourly_traffic.png", render_hourly_traffic_chart),
        ("daily_traffic", "daily_traffic.png", render_daily_traffic_chart),
        ("cashier_performance", "cashier_performance.png", render_cashier_performance_chart),
        ("top_debtors", "top_debtors.png", render_top_debtors_chart),
        ("expiry", "expiry_risk.png", render_expiry_chart),
        ("supplier_payables", "supplier_payables.png", render_supplier_payables_chart),
        ("dead_stock", "dead_stock.png", render_dead_stock_chart),
        ("category_margin", "category_margin.png", render_category_margin_chart),
    ]
    aliases = {"monthly_trend": "trend", "top_products": "breakdown", "expiry": "expiry_risk"}
    for index, (key, filename, renderer) in enumerate(chart_tasks, start=1):
        path = base_dir / filename
        if renderer(report_data, path):
            charts[key] = path
            if key in aliases:
                charts[aliases[key]] = path
        if progress_callback:
            progress_callback(index / len(chart_tasks), f"Rendering report charts ({index}/{len(chart_tasks)})")

    return charts
