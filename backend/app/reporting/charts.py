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
        data_2024 = [0.0] * 12
        data_2025 = [0.0] * 12
        for pt in trend_data:
            yr = str(pt.get("year", ""))
            m_idx = int(pt.get("month_num", 1)) - 1
            if 0 <= m_idx < 12:
                val = float(pt.get("revenue", 0.0))
                if yr == "2024":
                    data_2024[m_idx] = val
                elif yr == "2025":
                    data_2025[m_idx] = val

        if any(v > 0 for v in data_2024):
            ax.plot(range(12), data_2024, marker="o", markersize=4.2, color="#1e3a5f", linewidth=1.8, label="2024")
            ax.fill_between(range(12), data_2024, color="#1e3a5f", alpha=0.05)
        if any(v > 0 for v in data_2025):
            ax.plot(range(12), data_2025, marker="s", markersize=4.2, color="#d97706", linewidth=1.8, label="2025")
            ax.fill_between(range(12), data_2025, color="#d97706", alpha=0.05)
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
    ax.set_title("Monthly Revenue: 2024 vs 2025", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)

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
    ax.set_title("Total Revenue by Branch (2024–2025)", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)
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

    colors = ["#d97706" if 13 <= h <= 17 else "#1e3a5f" for h in hours]

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
    ax.set_title("Number of Transactions by Hour of Day (1 PM–5 PM peak highlighted)", fontsize=11.0, fontweight="bold", color="#0f172a", pad=12)

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
    near_kpi = report_data.get_kpi("expiring_value_30d")
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


def render_charts(report_data: ReportData, output_dir: Optional[Path] = None) -> Dict[str, Path]:
    """
    Generate all relevant static chart PNG images for the publication report.
    """
    base_dir = output_dir or (Path(settings.reports_dir) / "charts" / report_data.generated_at.strftime("%Y%m%d_%H%M%S"))
    base_dir.mkdir(parents=True, exist_ok=True)

    charts: Dict[str, Path] = {}

    # 1. Monthly Revenue Trend
    p1 = base_dir / "monthly_trend.png"
    if render_monthly_revenue_chart(report_data, p1):
        charts["monthly_trend"] = p1
        charts["trend"] = p1

    # 2. Branch Performance
    p2 = base_dir / "branch_performance.png"
    if render_branch_performance_chart(report_data, p2):
        charts["branch_performance"] = p2

    # 3. Payment Mix Donut
    p3 = base_dir / "payment_mix.png"
    if render_payment_mix_donut(report_data, p3):
        charts["payment_mix"] = p3

    # 4. Top 12 Products
    p4 = base_dir / "top_products.png"
    if render_top_products_chart(report_data, p4):
        charts["top_products"] = p4
        charts["breakdown"] = p4

    # 5. Slow Moving Products
    p5 = base_dir / "slow_products.png"
    if render_slow_products_chart(report_data, p5):
        charts["slow_products"] = p5

    # 6. Hourly Customer Traffic
    p6 = base_dir / "hourly_traffic.png"
    if render_hourly_traffic_chart(report_data, p6):
        charts["hourly_traffic"] = p6

    # 7. Day of Week Traffic
    p7 = base_dir / "daily_traffic.png"
    if render_daily_traffic_chart(report_data, p7):
        charts["daily_traffic"] = p7

    # 8. Cashier Performance
    p8 = base_dir / "cashier_performance.png"
    if render_cashier_performance_chart(report_data, p8):
        charts["cashier_performance"] = p8

    # 9. Top Debtors
    p9 = base_dir / "top_debtors.png"
    if render_top_debtors_chart(report_data, p9):
        charts["top_debtors"] = p9

    # 10. Expiry Risk
    p10 = base_dir / "expiry_risk.png"
    if render_expiry_chart(report_data, p10):
        charts["expiry"] = p10
        charts["expiry_risk"] = p10

    return charts
