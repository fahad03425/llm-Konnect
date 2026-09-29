"""
Module 6.8 — Report narrative generator.

Role in the pipeline
--------------------
"Code computes. The LLM narrates. A verifier checks."

The KPI engine and analytics layers compute every figure deterministically.
This module formats a compact summary of ground-truth figures and asks the local
LLM to produce business prose — strictly forbidding the model from calculating,
estimating, or fabricating figures.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings, get_default_domain
from app.core.llm import llm

try:
    from app.analytics.models import KPIResult
except ImportError:
    KPIResult = None  # type: ignore[assignment,misc]

try:
    from app.reporting.models import ReportData
except ImportError:
    ReportData = None  # type: ignore[assignment,misc]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a senior executive pharmaceutical retail analyst and report writer for {business_name}.

You are writing the narrative executive summary of a verified performance and sales report. \
Your prose will be embedded directly into a formal management report presented to pharmacy owners.

═══════════════════════════════════════════════
EXECUTIVE WRITING & GROUNDING RULES
═══════════════════════════════════════════════

1. NARRATE BUSINESS PERFORMANCE, DO NOT DEFINE METRICS.
   Write an insightful business summary covering revenue generation across branches, customer basket size, \
   peak shopping hours, top-selling medications, and credit/debt collection risk.
   DO NOT define what metrics mean (e.g. NEVER write "Expiring Value is a metric that...").
   DO NOT output numbered lists of metric definitions.

2. ABSOLUTE GROUNDING — NARRATE, DO NOT CALCULATE OR INVENT.
   Every single number, currency amount, percentage, and count in your narrative MUST \
   come directly from the "Ground Truth Data" block below. Do NOT invent estimations, \
   do NOT invent days (e.g. 30 days, 60 days), do NOT invent percentages. \
   If a number is not in Ground Truth, DO NOT WRITE IT.

3. UNAVAILABLE MEANS UNAVAILABLE — NEVER WRITE ZERO.
   If a metric is unavailable, state plainly that it could not be determined. Never substitute an estimate.

4. FORMATTING & TONE.
   - Write 2 to 3 cohesive executive paragraphs.
   - Use "PKR" before monetary amounts.
   - Maintain a professional, executive tone for pharmacy ownership and leadership.
"""

_USER_PROMPT_TEMPLATE = """\
Ground Truth Data (computed deterministically from the POS ledger):

{ground_truth_summary}

{correction_block}
Write the fluent, insight-rich executive performance narrative now, following all executive writing and grounding rules.
"""

_WEEKLY_PHARMACY_SYSTEM_PROMPT_TEMPLATE = """\
You are a senior pharmacy retail performance analyst writing for {business_name}.

Write a concise owner-facing insight for the selected source dataset. Help the owner decide what to do next to improve sales, margin, cash flow, stock availability, and working capital. Use only the sections and facts present in the supplied ground truth; tailor the priorities to this dataset rather than reciting a standard checklist.

═══════════════════════════════════════════════
WEEKLY LEAD WRITING & GROUNDING RULES
═══════════════════════════════════════════════

1. WRITE 2-3 SHORT PARAGRAPHS WITH INSIGHT AND ACTION.
   Identify the most material supported business result, explain why it matters to the owner, and recommend a practical next step. When the data supports it, connect sales/margin, stock risk, and supplier credit. State when the dataset does not support a conclusion instead of guessing.

2. ABSOLUTE GROUNDING — NARRATE, DO NOT CALCULATE OR INVENT.
   Every single number, currency amount, percentage, and count in your narrative MUST \
   come directly from the "Ground Truth Data" block below. Do NOT invent estimations, \
   do NOT invent days (e.g. 30 days, 60 days), do NOT invent percentages. \
   If a number is not in Ground Truth, DO NOT WRITE IT.

3. UNAVAILABLE MEANS UNAVAILABLE — NEVER WRITE ZERO.
   If a metric is unavailable, state plainly that it could not be determined. Never substitute an estimate.

4. CONDITIONAL OMISSION — OMIT MISSING FACTS CLEANLY.
   Every clause is conditionally omittable: if a given fact or metric isn't in Ground Truth \
   (for example, no supplier payable data available, or no stock is near expiry), skip that \
   clause entirely rather than writing "unavailable" awkwardly into the lead sentence.

5. FORMATTING & TONE.
   - Use "PKR" before monetary amounts.
   - Maintain a direct, confident executive tone for pharmacy ownership.
"""

_WEEKLY_PHARMACY_USER_PROMPT_TEMPLATE = """\
Ground Truth Data (computed deterministically from the POS ledger):

{ground_truth_summary}

{correction_block}
Write the owner-facing insight now. Prioritize actions by the magnitude and urgency shown in the data, and follow all grounding and conditional omission rules.
"""

_FALLBACK_MESSAGE = (
    "This report analyzes point-of-sale data across your operations. "
    "All computed KPI metrics and dimensional charts are detailed below."
)


def _format_ground_truth_summary(data: Union[Dict[str, Any], Any], domain: str) -> str:
    """Format a compact, readable ground truth summary for the prompt."""
    lines: List[str] = []

    # If it's a ReportData instance
    em = getattr(data, "executive_metrics", None)
    if em and getattr(em, "total_revenue", 0.0) > 0:
        lines.append("[Executive Performance Summary]")
        lines.append(f"• Reporting Period: {em.reporting_period}")
        lines.append(f"• Total Revenue: PKR {em.total_revenue:,.2f}")
        lines.append(f"• Total Invoices: {em.total_invoices:,}")
        lines.append(f"• Average Bill Value: PKR {em.avg_bill_value:,.2f}")
        lines.append(f"• Unique Customers Served: {em.unique_customers:,}")
        lines.append(f"• Products Sold (SKUs): {em.unique_products_count}")
        if em.yoy_growth_pct is not None:
            lines.append(f"• Year-on-Year Growth: {em.yoy_growth_pct:+.2f}%")
        lines.append(f"• Discounts Given: PKR {em.discounts_total:,.2f} ({em.discounts_pct:.1f}%)")
        lines.append(f"• Outstanding Balance (Debtors): PKR {em.outstanding_balance:,.2f}")

    if hasattr(data, "branch_performance") and data.branch_performance:
        lines.append("\n[Branch Performance]")
        for b in data.branch_performance[:5]:
            lines.append(f"• {b.get('branch')}: PKR {float(b.get('revenue', 0)):,.2f} ({int(b.get('invoices', 0)):,} invoices, Avg Bill: PKR {float(b.get('avg_bill', 0)):,.2f})")

    if hasattr(data, "top_products") and data.top_products:
        lines.append("\n[Top Products by Revenue]")
        for p in data.top_products[:5]:
            lines.append(f"• {p.get('name')}: PKR {float(p.get('amount', 0)):,.2f}")

    if hasattr(data, "hourly_traffic") and data.hourly_traffic:
        peak_hour = max(data.hourly_traffic, key=lambda row: int(row.get("count", 0)))
        lines.append("\n[Customer Footfall & Timing]")
        lines.append(f"• Busiest observed hour: {int(peak_hour.get('hour', 0)):02d}:00 with {int(peak_hour.get('count', 0))} recorded transactions")

    if hasattr(data, "top_debtors") and data.top_debtors:
        lines.append("\n[Top Outstanding Accounts]")
        for d in data.top_debtors[:3]:
            lines.append(f"• {d.get('customer')}: PKR {float(d.get('balance', 0)):,.2f}")

    if hasattr(data, "kpis"):
        kpis = data.kpis
        anomalies = getattr(data, "anomalies", None)
        period = getattr(data, "period", None)
    else:
        kpis = data
        anomalies = None
        period = None

    if kpis:
        lines.append("\n[Computed Ledger Totals]")
        for k, res in kpis.items():
            val = getattr(res, "value", None) if not isinstance(res, dict) else res.get("value")
            status = getattr(res, "status", "ok") if not isinstance(res, dict) else res.get("status", "ok")
            name = getattr(res, "name", k) if not isinstance(res, dict) else res.get("name", k)
            unit = getattr(res, "unit", "") if not isinstance(res, dict) else res.get("unit", "")

            # Only include actually computed, positive metrics — NEVER prompt with unavailable/missing columns
            if status == "ok" and val is not None:
                if unit == "PKR":
                    lines.append(f"• {name}: PKR {float(val):,.2f}")
                elif unit == "percent":
                    lines.append(f"• {name}: {float(val):,.2f}%")
                else:
                    lines.append(f"• {name}: {float(val):,.4g} {unit}".strip())

    return "\n".join(lines)


def _format_weekly_ground_truth_summary(data: Union[Dict[str, Any], Any]) -> str:
    """Format key weekly focal points (profit, at-risk stock, stockout, payables)."""
    lines: List[str] = []
    kpis = getattr(data, "kpis", data if isinstance(data, dict) else {})

    # Report gross profit only; purchases may be captured as outflows and must
    # not be presented as operating expenses/net profit without accounting data.
    profit_kpi = kpis.get("gross_profit")
    if profit_kpi:
        v = getattr(profit_kpi, "value", None) if not isinstance(profit_kpi, dict) else profit_kpi.get("value")
        if v is not None:
            name = getattr(profit_kpi, "name", "Profit") if not isinstance(profit_kpi, dict) else profit_kpi.get("name", "Profit")
            lines.append(f"• Current-period {name}: PKR {float(v):,.2f} (before operating overhead)")

    rev_kpi = kpis.get("total_revenue")
    if rev_kpi:
        v = getattr(rev_kpi, "value", None) if not isinstance(rev_kpi, dict) else rev_kpi.get("value")
        if v is not None:
            lines.append(f"• Weekly Revenue: PKR {float(v):,.2f}")

    # 2. Stock needing attention (near expiry / expired / dead stock)
    at_risk: List[str] = []
    for k in ("near_expiry_total", "expired_stock_value", "dead_stock_value"):
        res = kpis.get(k)
        if res:
            v = getattr(res, "value", None) if not isinstance(res, dict) else res.get("value")
            name = getattr(res, "name", k) if not isinstance(res, dict) else res.get("name", k)
            if v is not None and float(v) > 0:
                at_risk.append(f"{name}: PKR {float(v):,.2f}")
    if at_risk:
        lines.append(f"• Stock Needing Attention: {', '.join(at_risk)}")

    # 3. Running out (reorder alerts)
    alerts = getattr(data, "reorder_alerts", [])
    if alerts:
        top_alert = alerts[0]
        p_name = top_alert.get("product_name") or top_alert.get("product_id")
        days = top_alert.get("days_until_stockout")
        if days is not None:
            lines.append(f"• Running Out: {p_name} has {float(days):.1f} days of supply remaining")
        else:
            lines.append(f"• Running Out: {p_name} is at critical reorder point")

    # 4. Supplier payables
    payables = getattr(data, "supplier_payables", [])
    if payables:
        top_sp = payables[0]
        s_name = top_sp.get("supplier_name") or top_sp.get("supplier_id")
        amt = top_sp.get("total_payable") or top_sp.get("payable_amount")
        due = top_sp.get("earliest_due_date")
        if amt is not None:
            due_str = f", due {due}" if due else ""
            lines.append(f"• Supplier Payable: Owe {s_name} PKR {float(amt):,.2f}{due_str}")

    # Also append standard ledger totals for complete verifiable grounding
    std_summary = _format_ground_truth_summary(data, "pharmacy")
    if std_summary:
        lines.append("\n[All Available Ledger Figures]")
        lines.append(std_summary)

    return "\n".join(lines)


def generate_weekly_executive_lead(
    report_data_or_kpis: Union[Dict[str, Any], Any],
    domain: Optional[str] = None,
    business_name: Optional[str] = None,
    correction_feedback: Optional[str] = None,
) -> str:
    """
    Generate a grounded 3-4 sentence plain-language weekly executive lead
    specifically focused on profit, at-risk stock, reorder alerts, and supplier payables.
    """
    eff_default = get_default_domain()
    if hasattr(report_data_or_kpis, "kpis"):
        kpis = report_data_or_kpis.kpis
        effective_domain = getattr(report_data_or_kpis, "domain", domain) or domain or eff_default
        effective_name = getattr(report_data_or_kpis, "business_name", business_name) or business_name
    else:
        kpis = report_data_or_kpis
        effective_domain = domain or eff_default
        effective_name = business_name

    has_available = False
    for res in kpis.values():
        v = getattr(res, "value", None) if not isinstance(res, dict) else res.get("value")
        s = getattr(res, "status", "") if not isinstance(res, dict) else res.get("status")
        if s == "ok" and v is not None:
            has_available = True
            break

    if not has_available:
        return "This weekly pharmacy summary analyzes point-of-sale and inventory movements across your operations."

    biz_name = effective_name or f"the {effective_domain.title()} business"

    system_prompt = _WEEKLY_PHARMACY_SYSTEM_PROMPT_TEMPLATE.format(
        business_name=biz_name,
        domain=effective_domain,
    )

    ground_truth_summary = _format_weekly_ground_truth_summary(report_data_or_kpis)

    correction_block = ""
    if correction_feedback:
        correction_block = (
            f"⚠️ CORRECTION REQUIRED FROM PREVIOUS ATTEMPT:\n"
            f"{correction_feedback}\n"
            f"You MUST ensure every number cited exactly matches the ground truth values above.\n"
        )

    user_prompt = _WEEKLY_PHARMACY_USER_PROMPT_TEMPLATE.format(
        ground_truth_summary=ground_truth_summary,
        correction_block=correction_block,
    )

    try:
        lead = llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            keep_alive=settings.llm_keep_alive,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Weekly executive lead generation failed — Ollama inference error: {exc}"
        ) from exc

    return lead.strip()


def generate_narrative(
    report_data_or_kpis: Union[Dict[str, Any], Any],
    domain: Optional[str] = None,
    business_name: Optional[str] = None,
    correction_feedback: Optional[str] = None,
    report_type: str = "standard",
) -> str:
    """
    Generate a grounded, LLM-written business narrative from computed analytics.
    """
    if report_type == "weekly_pharmacy":
        return generate_weekly_executive_lead(
            report_data_or_kpis,
            domain=domain,
            business_name=business_name,
            correction_feedback=correction_feedback,
        )

    eff_default = get_default_domain()
    if hasattr(report_data_or_kpis, "kpis"):
        kpis = report_data_or_kpis.kpis
        effective_domain = getattr(report_data_or_kpis, "domain", domain) or domain or eff_default
        effective_name = getattr(report_data_or_kpis, "business_name", business_name) or business_name
    else:
        kpis = report_data_or_kpis
        effective_domain = domain or eff_default
        effective_name = business_name

    # Check if there is anything to narrate
    has_available = False
    for res in kpis.values():
        v = getattr(res, "value", None) if not isinstance(res, dict) else res.get("value")
        s = getattr(res, "status", "") if not isinstance(res, dict) else res.get("status")
        if s == "ok" and v is not None:
            has_available = True
            break

    if not has_available:
        return _FALLBACK_MESSAGE

    biz_name = effective_name or f"the {effective_domain.title()} business"

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        business_name=biz_name,
        domain=effective_domain,
    )

    ground_truth_summary = _format_ground_truth_summary(report_data_or_kpis, effective_domain)

    correction_block = ""
    if correction_feedback:
        correction_block = (
            f"⚠️ CORRECTION REQUIRED FROM PREVIOUS ATTEMPT:\n"
            f"{correction_feedback}\n"
            f"You MUST ensure every number cited exactly matches the ground truth values above.\n"
        )

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        ground_truth_summary=ground_truth_summary,
        correction_block=correction_block,
    )

    try:
        narrative = llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            keep_alive=settings.llm_keep_alive,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Narrative generation failed — Ollama inference error: {exc}"
        ) from exc

    return narrative.strip()
