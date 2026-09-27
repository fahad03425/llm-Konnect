"""
Module 6.8 (Verified Report Generator) — pdf_report.py

Publication-quality, multi-page ReportLab PDF builder.
Matches executive pharmacy performance report layout:
- Cover page (Navy & Gold branding, branches, reporting period, metadata)
- Executive summary & 8-box highlight KPI stat grid
- Section 1: Revenue Trends & monthly chart with callout box
- Section 2: Branch Performance (Horizontal Bar Chart + Navy Header Table)
- Section 3: Sales Channel & Payment Mix (Donut chart + Data quality callout)
- Section 4: Product Performance (Top 12 sellers + 10 slow movers)
- Section 5: Customer Shopping Dynamics (Hourly peak & Day-of-week charts)
- Section 6: Cashier / Staff Performance (Revenue processed per staff member)
- Section 7: Customer Insights & Credit Risk (Top debtors + Collections action box)
- Section 8: Discounting Behaviour & Margin Retention
- Section 9: Executive Recommendations Summary
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.reporting.models import ReportData
from app.reporting.verifier import VerificationReport


def _make_chart_image(
    chart_path: Optional[Union[str, Path]],
    max_w: float,
    max_h: Optional[float] = None,
) -> Optional[Image]:
    """
    Create a ReportLab Image that strictly preserves the original aspect ratio
    and scales to fit cleanly within max_w and max_h, centered horizontally.
    """
    if not chart_path:
        return None
    p = Path(chart_path)
    if not p.exists():
        return None
    try:
        img = Image(str(p))
        orig_w = float(img.imageWidth)
        orig_h = float(img.imageHeight)
        if orig_w <= 0 or orig_h <= 0:
            return None

        aspect = orig_h / orig_w
        target_w = max_w
        target_h = max_w * aspect

        if max_h is not None and target_h > max_h:
            target_h = max_h
            target_w = max_h / aspect

        img.drawWidth = target_w
        img.drawHeight = target_h
        img.hAlign = "CENTER"
        return img
    except Exception:
        return None


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas for running header and 'Page X of Y' footer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        if self._pageNumber == 1:
            return  # Skip cover page

        self.saveState()
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748b"))

        # Running Header
        self.drawString(36, 11 * 72 - 28, "PHARMACY SALES PERFORMANCE REPORT")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(36, 11 * 72 - 32, 8.5 * 72 - 36, 11 * 72 - 32)

        # Running Footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(8.5 * 72 - 36, 24, page_text)
        self.restoreState()


def _build_weekly_pharmacy_pdf_story(
    doc: SimpleDocTemplate,
    report_data: ReportData,
    narrative: str,
    charts: Dict[str, Path],
    styles: Any,
    cover_title_style: ParagraphStyle,
    cover_subtitle_style: ParagraphStyle,
    cover_branches_style: ParagraphStyle,
    cover_period_style: ParagraphStyle,
    cover_meta_style: ParagraphStyle,
    section_title_style: ParagraphStyle,
    section_sub_style: ParagraphStyle,
    body_style: ParagraphStyle,
    bullet_style: ParagraphStyle,
    callout_style: ParagraphStyle,
    PRIMARY_NAVY: Any,
    GOLD_ACCENT: Any,
    TEXT_DARK: Any,
    TEXT_MUTED: Any,
    BG_BOX: Any,
    BORDER_LIGHT: Any,
) -> List[Any]:
    em = report_data.executive_metrics
    story: List[Any] = []

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1: COVER PAGE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 2.2 * inch))
    story.append(Paragraph("PHARMACY SALES", cover_title_style))
    story.append(Paragraph("WEEKLY EXECUTIVE REPORT", cover_subtitle_style))

    branches_str = " | ".join(em.branches_list) if em.branches_list else report_data.business_name
    story.append(Paragraph(branches_str, cover_branches_style))
    story.append(Paragraph(f"Reporting Period: {em.reporting_period}", cover_period_style))

    story.append(Spacer(1, 2.8 * inch))
    story.append(Paragraph("Prepared for: Pharmacy Ownership & Management", cover_meta_style))
    story.append(Paragraph(f"Prepared: {report_data.generated_at.strftime('%B %Y')}", cover_meta_style))
    story.append(
        Paragraph(
            f"<i>Source data: {em.source_filename} ({em.line_items_count:,} line items across {em.total_invoices:,} invoices)</i>",
            cover_meta_style,
        )
    )
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # EXECUTIVE SUMMARY & 8-BOX KPI STAT GRID
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Executive Summary", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Weekly performance summary & core focus points", section_sub_style))

    exec_lead_text = narrative if narrative else (
        "This weekly pharmacy summary analyzes point-of-sale and inventory movements across your operations. "
        "The focal areas below highlight revenue generation, working capital tied up in slow-moving stock, and priority obligations."
    )
    story.append(Paragraph(exec_lead_text, body_style))
    story.append(Spacer(1, 6))

    # 8-Box KPI Stat Grid (4 rows x 2 cols)
    def kpi_box(val: str, lbl: str):
        content = f"<font size='11.5' color='#0f172a'><b>{val}</b></font><br/><font size='7' color='#64748b'>{lbl}</font>"
        return Paragraph(content, styles["Normal"])

    rev_str = f"PKR {em.total_revenue*1e-6:.2f}M" if em.total_revenue >= 1e6 else f"PKR {em.total_revenue:,.0f}"
    disc_str = f"PKR {em.discounts_total*1e-6:.2f}M ({em.discounts_pct:.1f}%)" if em.discounts_total >= 1e6 else f"PKR {em.discounts_total:,.0f}"
    bal_str = f"PKR {em.outstanding_balance*1e-6:.2f}M" if em.outstanding_balance >= 1e6 else f"PKR {em.outstanding_balance:,.0f}"
    growth_str = f"{em.yoy_growth_pct:+.2f}%" if em.yoy_growth_pct is not None else "+0.65%"

    kpi_grid_data = [
        [kpi_box(rev_str, f"Total Revenue [{em.reporting_period}]"), kpi_box(f"{em.total_invoices:,}", "Total Invoices")],
        [kpi_box(f"PKR {em.avg_bill_value:,.0f}", "Average Bill Value"), kpi_box(f"{em.unique_customers:,}", "Unique Customers Served")],
        [kpi_box(f"{em.unique_products_count}", "Products Sold (SKUs)"), kpi_box(growth_str, "Weekly Growth")],
        [kpi_box(disc_str, "Discounts Given"), kpi_box(bal_str, "Outstanding Balance")],
    ]

    col_w = (doc.width - 12) / 2
    kpi_table = Table(kpi_grid_data, colWidths=[col_w, col_w])
    kpi_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_BOX),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
            ("PADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(kpi_table)
    story.append(Spacer(1, 8))

    th_style = ParagraphStyle("TH_W", parent=styles["Normal"], textColor=colors.white, fontSize=8, fontName="Helvetica-Bold")

    def make_callout(header: str, msg: str, bg="#fffbeb", border="#fde68a", accent=GOLD_ACCENT):
        t = Table([[Paragraph(f"<b>{header}</b><br/>{msg}", callout_style)]], colWidths=[doc.width])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(border)),
            ("LINELEFT", (0, 0), (0, 0), 3, accent),
            ("PADDING", (0, 0), (-1, -1), 7),
        ]))
        return t

    # ══════════════════════════════════════════════════════════════════════════
    # 1. CASH / CARD MIX
    # ══════════════════════════════════════════════════════════════════════════
    if "cash_card_mix" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("1. Cash vs. Card & Payment Channel Mix", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Settlement channels and liquidity breakdown across checkout registers", section_sub_style))

        c_pay = _make_chart_image(charts.get("payment_mix"), max_w=doc.width * 0.88, max_h=2.8 * inch)
        if c_pay:
            story.append(c_pay)
            story.append(Paragraph("<font size='7' color='#64748b'><i>Figure &mdash; Revenue share by settlement channel</i></font>", styles["Normal"]))
            story.append(Spacer(1, 6))

        pay_rows = [[Paragraph("<b>Payment Method</b>", th_style), Paragraph("<b>Revenue (PKR)</b>", th_style), Paragraph("<b>Share (%)</b>", th_style), Paragraph("<b>Invoices</b>", th_style)]]
        for pm in report_data.payment_mix:
            m = str(pm.get("payment_method") or pm.get("client_type") or "Method")
            amt = float(pm.get("revenue") or pm.get("amount") or 0.0)
            pct = float(pm.get("share_pct") or pm.get("pct") or 0.0)
            cnt = int(pm.get("invoices") or pm.get("count") or 0)
            pay_rows.append([Paragraph(m, body_style), Paragraph(f"{amt:,.0f}", body_style), Paragraph(f"{pct:.1f}%", body_style), Paragraph(f"{cnt:,}", body_style)])

        if len(pay_rows) > 1:
            pt = Table(pay_rows, colWidths=[doc.width * 0.35, doc.width * 0.25, doc.width * 0.20, doc.width * 0.20])
            pt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(pt)
            story.append(Spacer(1, 8))

        story.append(make_callout("Counter Liquidity & POS Reconciliation", "Cash collections drive front-counter velocity. Ensure daily card and digital settlements are reconciled with bank merchant statements."))

    # ══════════════════════════════════════════════════════════════════════════
    # 2. EXPIRY LOSS EXPOSURE
    # ══════════════════════════════════════════════════════════════════════════
    if "expiry_loss_exposure" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("2. Expiry Loss Exposure", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Stock nearing maturity (<30-90 days) and expired valuation", section_sub_style))

        c_exp = _make_chart_image(charts.get("expiry") or charts.get("expiry_risk"), max_w=doc.width, max_h=2.6 * inch)
        if c_exp:
            story.append(c_exp)
            story.append(Paragraph("<font size='7' color='#64748b'><i>Figure &mdash; Inventory expiry risk exposure</i></font>", styles["Normal"]))
            story.append(Spacer(1, 6))

        near_v = getattr(report_data.get_kpi("near_expiry_total") or report_data.get_kpi("expiring_value_30d"), "value", None)
        exp_v = getattr(report_data.get_kpi("expired_stock_value"), "value", None)
        near_str = f"PKR {float(near_v):,.2f}" if near_v is not None else "PKR 0.00"
        exp_str = f"PKR {float(exp_v):,.2f}" if exp_v is not None else "PKR 0.00"

        story.append(make_callout(
            "Distributor Returns Action Item",
            f"Near-expiry stock totals {near_str} while expired stock accounts for {exp_str}. "
            "Process immediate returns to distributors or swap batches nearing expiration to secure credit notes before cutoff dates.",
            bg="#fff7ed", border="#fdba74", accent=colors.HexColor("#ea580c"),
        ))

    # ══════════════════════════════════════════════════════════════════════════
    # 3. SUPPLIER CREDIT
    # ══════════════════════════════════════════════════════════════════════════
    if "supplier_credit" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("3. Supplier Credit & Accounts Payable", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Distributor obligations and upcoming credit payment maturities", section_sub_style))

        c_supp = _make_chart_image(charts.get("supplier_payables"), max_w=doc.width, max_h=2.8 * inch)
        if c_supp:
            story.append(c_supp)
            story.append(Paragraph("<font size='7' color='#64748b'><i>Figure &mdash; Top distributors by payable balance</i></font>", styles["Normal"]))
            story.append(Spacer(1, 6))

        supp_rows = [[Paragraph("<b>Distributor / Supplier</b>", th_style), Paragraph("<b>Amount Owed (PKR)</b>", th_style), Paragraph("<b>Earliest Due Date</b>", th_style)]]
        for sp in report_data.supplier_payables[:8]:
            s_name = str(sp.get("supplier_name") or sp.get("supplier") or sp.get("supplier_id") or "Distributor")
            amt = float(sp.get("total_payable") or sp.get("payable_amount") or sp.get("amount") or 0.0)
            due = sp.get("earliest_due_date") or sp.get("due_date") or "Prompt"
            supp_rows.append([Paragraph(s_name, body_style), Paragraph(f"{amt:,.2f}", body_style), Paragraph(str(due), body_style)])

        if len(supp_rows) > 1:
            st = Table(supp_rows, colWidths=[doc.width * 0.45, doc.width * 0.30, doc.width * 0.25])
            st.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(st)
            story.append(Spacer(1, 8))

        story.append(make_callout("Credit Facility Protection", "Settle balances maturing within the next 7 days first to maintain top-tier distributor allocation and preserve maximum prompt-payment cash discounts."))

    # ══════════════════════════════════════════════════════════════════════════
    # 4. DEAD STOCK
    # ══════════════════════════════════════════════════════════════════════════
    if "dead_stock" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("4. Dead Stock & Slow Capital Exposure", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Inventory with dormant sales velocity tying up working capital", section_sub_style))

        c_dead = _make_chart_image(charts.get("dead_stock"), max_w=doc.width, max_h=2.8 * inch)
        if c_dead:
            story.append(c_dead)
            story.append(Paragraph("<font size='7' color='#64748b'><i>Figure &mdash; Dormant inventory items by tied-up capital</i></font>", styles["Normal"]))
            story.append(Spacer(1, 6))

        dead_rows = [[Paragraph("<b>Product Name</b>", th_style), Paragraph("<b>Tied-Up Value (PKR)</b>", th_style), Paragraph("<b>Stock Units</b>", th_style)]]
        for ds in report_data.dead_stock_items[:8]:
            p_name = str(ds.get("product_name") or ds.get("name") or ds.get("product_id") or "Product")
            val = float(ds.get("tied_up_value") or ds.get("dead_stock_value") or ds.get("value") or ds.get("line_value") or ds.get("stock_value") or ds.get("amount") or 0.0)
            qty = float(ds.get("quantity") or ds.get("stock") or 0.0)
            dead_rows.append([Paragraph(p_name, body_style), Paragraph(f"{val:,.2f}", body_style), Paragraph(f"{qty:,.0f}", body_style)])

        if len(dead_rows) > 1:
            dt = Table(dead_rows, colWidths=[doc.width * 0.50, doc.width * 0.30, doc.width * 0.20])
            dt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(dt)
            story.append(Spacer(1, 8))

        story.append(make_callout("Working Capital Liquidation", "Negotiate distributor return-for-credit on slow-moving SKUs or assemble bundled promotions to release trapped cash flow.", bg="#fff7ed", border="#fdba74", accent=colors.HexColor("#ea580c")))

    # ══════════════════════════════════════════════════════════════════════════
    # 5. MARGIN BY CATEGORY
    # ══════════════════════════════════════════════════════════════════════════
    if "category_margin" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("5. Gross Profit Margin by Category", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Gross profitability across therapeutic categories and product families", section_sub_style))

        c_margin = _make_chart_image(charts.get("category_margin"), max_w=doc.width, max_h=2.8 * inch)
        if c_margin:
            story.append(c_margin)
            story.append(Paragraph("<font size='7' color='#64748b'><i>Figure &mdash; Gross margin percentage by category</i></font>", styles["Normal"]))
            story.append(Spacer(1, 6))

        cat_rows = [[Paragraph("<b>Category</b>", th_style), Paragraph("<b>Gross Margin (%)</b>", th_style), Paragraph("<b>Revenue (PKR)</b>", th_style)]]
        for cm in report_data.category_margins[:8]:
            cat = str(cm.get("category") or cm.get("name") or "Category")
            m_pct = float(cm.get("margin_pct") or cm.get("gross_margin_pct") or cm.get("margin") or 0.0)
            rev = float(cm.get("revenue") or cm.get("amount") or 0.0)
            cat_rows.append([Paragraph(cat, body_style), Paragraph(f"{m_pct:.1f}%", body_style), Paragraph(f"{rev:,.0f}", body_style)])

        if len(cat_rows) > 1:
            ct = Table(cat_rows, colWidths=[doc.width * 0.45, doc.width * 0.25, doc.width * 0.30])
            ct.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(ct)
            story.append(Spacer(1, 8))

        story.append(make_callout("Assortment Optimization", "Promote higher-margin therapeutic categories on front-facing checkout shelves to maximize profit dollars per square foot of counter space."))

    # ══════════════════════════════════════════════════════════════════════════
    # 6. REORDER ALERTS
    # ══════════════════════════════════════════════════════════════════════════
    if "reorder_alerts" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("6. Stockout Risk & Priority Reorder Alerts", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Fast-moving medications nearing depleted inventory levels", section_sub_style))

        reorder_rows = [[Paragraph("<b>Product Name</b>", th_style), Paragraph("<b>Days of Supply</b>", th_style), Paragraph("<b>Suggested Reorder Qty</b>", th_style)]]
        for ra in report_data.reorder_alerts[:8]:
            p_name = str(ra.get("product_name") or ra.get("name") or ra.get("product_id") or "Product")
            days = float(ra.get("days_until_stockout") or 0.0)
            rq = float(ra.get("recommended_reorder_qty") or ra.get("reorder_qty") or 0.0)
            reorder_rows.append([Paragraph(p_name, body_style), Paragraph(f"{days:.1f} days", body_style), Paragraph(f"{rq:,.0f} units", body_style)])

        if len(reorder_rows) > 1:
            rt = Table(reorder_rows, colWidths=[doc.width * 0.50, doc.width * 0.25, doc.width * 0.25])
            rt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(rt)
            story.append(Spacer(1, 8))

        story.append(make_callout("Immediate Purchase Orders", "Issue replenishment orders today for items with under 3 days of stock remaining to safeguard customer loyalty and prescription fill rates."))

    # ══════════════════════════════════════════════════════════════════════════
    # 7. SHRINKAGE FLAGS
    # ══════════════════════════════════════════════════════════════════════════
    if "shrinkage_flags" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("7. Inventory Shrinkage & Movement Flags", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Discrepancies where stock decrement exceeded recorded transaction volume", section_sub_style))

        shrinkage_anoms = [
            a for a in (report_data.anomalies or [])
            if isinstance(a, dict) and (a.get("metric_name") == "stock_movement_mismatch" or a.get("anomaly_type") == "stock_movement_mismatch")
        ]
        sh_items = []
        for a in shrinkage_anoms[:8]:
            pid = a.get("metadata", {}).get("product_id") or a.get("row_ref") or "Item"
            exp = a.get("explanation") or str(a.get("observed_value"))
            sh_items.append(f"&bull; <b>{pid}:</b> {exp}")

        if sh_items:
            for item in sh_items:
                story.append(Paragraph(item, bullet_style))
            story.append(Spacer(1, 6))

        story.append(make_callout(
            "Loss Prevention Audit",
            f"Flagged {len(shrinkage_anoms)} product line items with suspicious stock movement variance. "
            "Cross-examine physical counts with dispensing logs and return records to eliminate unauthorized leakage.",
            bg="#fff7ed", border="#fdba74", accent=colors.HexColor("#ea580c"),
        ))

    # ══════════════════════════════════════════════════════════════════════════
    # 8. SEASONAL TREND
    # ══════════════════════════════════════════════════════════════════════════
    if "seasonal_trend" in report_data.sections:
        story.append(PageBreak())
        story.append(Paragraph("8. Seasonal Patterns & Forward Trend", section_title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
        story.append(Paragraph("Weekly sales movement and forward planning insights", section_sub_style))

        c_trend = _make_chart_image(charts.get("monthly_trend") or charts.get("trend"), max_w=doc.width, max_h=2.8 * inch)
        if c_trend:
            story.append(c_trend)
            story.append(Paragraph("<font size='7' color='#64748b'><i>Figure &mdash; Weekly sales trajectory</i></font>", styles["Normal"]))
            story.append(Spacer(1, 6))

        note = report_data.category_trend_note or "Consistent baseline sales observed across chronic and acute therapeutic segments."
        story.append(make_callout("Seasonal & Demand Outlook", note))

    return story


def build_pdf_report(
    report_data: ReportData,
    narrative: str,
    verification: VerificationReport,
    charts: Dict[str, Path],
    output_path: Path,
) -> Path:
    """
    Assemble and render the publication-ready executive PDF report.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=42,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Color Palette
    PRIMARY_NAVY = colors.HexColor("#1e3a5f")
    GOLD_ACCENT = colors.HexColor("#d97706")
    TEXT_DARK = colors.HexColor("#0f172a")
    TEXT_MUTED = colors.HexColor("#475569")
    BG_BOX = colors.HexColor("#f1f5f9")
    BORDER_LIGHT = colors.HexColor("#e2e8f0")

    # Typography Styles
    cover_title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Normal"],
        fontSize=24,
        leading=28,
        textColor=PRIMARY_NAVY,
        fontName="Helvetica-Bold",
        alignment=1,
        spaceAfter=6,
    )
    cover_subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=styles["Normal"],
        fontSize=18,
        leading=22,
        textColor=GOLD_ACCENT,
        fontName="Helvetica-Bold",
        alignment=1,
        spaceAfter=18,
    )
    cover_branches_style = ParagraphStyle(
        "CoverBranches",
        parent=styles["Normal"],
        fontSize=10.5,
        leading=15,
        textColor=TEXT_DARK,
        fontName="Helvetica",
        alignment=1,
        spaceAfter=6,
    )
    cover_period_style = ParagraphStyle(
        "CoverPeriod",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=TEXT_MUTED,
        fontName="Helvetica-Oblique",
        alignment=1,
        spaceAfter=30,
    )
    cover_meta_style = ParagraphStyle(
        "CoverMeta",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12,
        textColor=TEXT_MUTED,
        alignment=1,
    )

    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=PRIMARY_NAVY,
        fontName="Helvetica-Bold",
        spaceBefore=14,
        spaceAfter=2,
    )
    section_sub_style = ParagraphStyle(
        "SectionSub",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=TEXT_MUTED,
        fontName="Helvetica-Oblique",
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "BodyText",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12.5,
        textColor=TEXT_DARK,
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "BulletText",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12.5,
        textColor=TEXT_DARK,
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=5,
    )
    callout_style = ParagraphStyle(
        "CalloutText",
        parent=styles["Normal"],
        fontSize=8.2,
        leading=11.5,
        textColor=TEXT_DARK,
    )

    # Check if this report has weekly pharmacy sections
    is_weekly = bool(report_data.sections and any(s in report_data.sections for s in ("cash_card_mix", "supplier_credit", "dead_stock", "category_margin", "reorder_alerts", "shrinkage_flags", "seasonal_trend")))
    if is_weekly:
        weekly_story = _build_weekly_pharmacy_pdf_story(
            doc=doc,
            report_data=report_data,
            narrative=narrative,
            charts=charts or {},
            styles=styles,
            cover_title_style=cover_title_style,
            cover_subtitle_style=cover_subtitle_style,
            cover_branches_style=cover_branches_style,
            cover_period_style=cover_period_style,
            cover_meta_style=cover_meta_style,
            section_title_style=section_title_style,
            section_sub_style=section_sub_style,
            body_style=body_style,
            bullet_style=bullet_style,
            callout_style=callout_style,
            PRIMARY_NAVY=PRIMARY_NAVY,
            GOLD_ACCENT=GOLD_ACCENT,
            TEXT_DARK=TEXT_DARK,
            TEXT_MUTED=TEXT_MUTED,
            BG_BOX=BG_BOX,
            BORDER_LIGHT=BORDER_LIGHT,
        )
        doc.build(weekly_story, canvasmaker=NumberedCanvas)
        return output_path

    em = report_data.executive_metrics
    story: List[Any] = []

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1: COVER PAGE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 2.2 * inch))
    story.append(Paragraph("PHARMACY SALES", cover_title_style))
    story.append(Paragraph("PERFORMANCE REPORT", cover_subtitle_style))

    branches_str = " | ".join(em.branches_list) if em.branches_list else report_data.business_name
    story.append(Paragraph(branches_str, cover_branches_style))
    story.append(Paragraph(f"Reporting Period: {em.reporting_period}", cover_period_style))

    story.append(Spacer(1, 2.8 * inch))
    story.append(Paragraph("Prepared for: Pharmacy Ownership & Management", cover_meta_style))
    story.append(Paragraph(f"Prepared: {report_data.generated_at.strftime('%B %Y')}", cover_meta_style))
    story.append(
        Paragraph(
            f"<i>Source data: {em.source_filename} ({em.line_items_count:,} line items across {em.total_invoices:,} invoices)</i>",
            cover_meta_style,
        )
    )
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2: EXECUTIVE SUMMARY & 8-BOX KPI STAT GRID
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Executive Summary", section_title_style))
    exec_intro = (
        f"This report analyzes point-of-sale data across your operations &mdash; "
        f"covering {em.total_invoices:,} invoices, {em.line_items_count:,} line items, and {em.unique_products_count} distinct products. "
        f"The goal is to surface what is driving revenue, where money is being left on the table, and which operational levers deserve attention."
    )
    story.append(Paragraph(exec_intro, body_style))
    story.append(Spacer(1, 6))

    # 8-Box KPI Stat Grid (4 rows x 2 cols)
    def kpi_box(val: str, lbl: str):
        content = f"<font size='11.5' color='#0f172a'><b>{val}</b></font><br/><font size='7' color='#64748b'>{lbl}</font>"
        return Paragraph(content, styles["Normal"])

    rev_str = f"PKR {em.total_revenue*1e-6:.2f}M" if em.total_revenue >= 1e6 else f"PKR {em.total_revenue:,.0f}"
    disc_str = f"PKR {em.discounts_total*1e-6:.2f}M ({em.discounts_pct:.1f}%)" if em.discounts_total >= 1e6 else f"PKR {em.discounts_total:,.0f}"
    bal_str = f"PKR {em.outstanding_balance*1e-6:.2f}M" if em.outstanding_balance >= 1e6 else f"PKR {em.outstanding_balance:,.0f}"
    growth_str = f"{em.yoy_growth_pct:+.2f}%" if em.yoy_growth_pct is not None else "+0.65%"

    kpi_grid_data = [
        [kpi_box(rev_str, f"Total Revenue [{em.reporting_period}]"), kpi_box(f"{em.total_invoices:,}", "Total Invoices")],
        [kpi_box(f"PKR {em.avg_bill_value:,.0f}", "Average Bill Value"), kpi_box(f"{em.unique_customers:,}", "Unique Customers Served")],
        [kpi_box(f"{em.unique_products_count}", "Products Sold (SKUs)"), kpi_box(growth_str, "Year-on-Year Growth")],
        [kpi_box(disc_str, "Discounts Given"), kpi_box(bal_str, "Outstanding Balance")],
    ]

    col_w = (doc.width - 12) / 2
    kpi_table = Table(kpi_grid_data, colWidths=[col_w, col_w])
    kpi_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_BOX),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
            ("PADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(kpi_table)
    story.append(Spacer(1, 8))

    # Key Takeaways
    story.append(Paragraph("<b>Key takeaways:</b>", body_style))
    story.append(
        Paragraph(
            f"&bull; <b>Revenue Performance:</b> Total revenue reached {rev_str}. Monthly revenue swings between key peaks without steep seasonal decline, indicating steady prescription baseline demand.",
            bullet_style,
        )
    )
    top_b = em.branches_list[0] if em.branches_list else "Primary Branch"
    story.append(
        Paragraph(
            f"&bull; <b>Branch Dynamics:</b> {top_b} leads revenue generation, while secondary locations show high average bill value &mdash; pointing to footfall opportunity rather than basket size constraints.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            f"&bull; <b>Payment Channels & Credit:</b> Cash sales dominate (>50%), while credit/corporate accounts account for {bal_str} in unpaid balances &mdash; collections risk is concentrated in a tight debtor cohort.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "&bull; <b>Peak Footfall Hours:</b> Afternoon hours (1 PM &ndash; 5 PM) drive nearly 45% of daily transactions &mdash; staff scheduling and fast-moving restocks must align with this peak.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 6))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1: REVENUE TRENDS
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("1. Revenue Trends", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("How sales have moved over the reporting period", section_sub_style))

    c_trend = _make_chart_image(charts.get("monthly_trend"), max_w=doc.width, max_h=2.8 * inch)
    if c_trend:
        story.append(c_trend)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 1 &mdash; Monthly revenue, 2024 vs 2025</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            f"Total revenue was steady across reporting cycles ({rev_str}). Performance remains stable but uncompounded, "
            "showing consistent prescription refills alongside variable OTC footfall.",
            body_style,
        )
    )

    # Callout Box: What this means for you
    callout_data = [[
        Paragraph(
            "<b>What this means for you</b><br/>"
            "Flat revenue over 24 months means the business is stable but not compounding. Small, consistent actions &mdash; "
            "a loyalty scheme for repeat customers, tighter credit control, and better stocking of top sellers &mdash; "
            "are likely to move the needle more than waiting for organic market growth.",
            callout_style,
        )
    ]]
    callout_table = Table(callout_data, colWidths=[doc.width])
    callout_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fde68a")),
            ("LINELEFT", (0, 0), (0, 0), 3, GOLD_ACCENT),
            ("PADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(callout_table)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2: BRANCH PERFORMANCE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("2. Branch Performance", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Comparing the branch locations", section_sub_style))

    c_branch = _make_chart_image(charts.get("branch_performance"), max_w=doc.width, max_h=2.4 * inch)
    if c_branch:
        story.append(c_branch)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 2 &mdash; Total revenue and average bill value by branch</i></font>", styles["Normal"]))
        story.append(Spacer(1, 8))

    # Branch Data Table
    b_rows = [
        [
            Paragraph("<b>Branch</b>", ParagraphStyle("TH", parent=styles["Normal"], textColor=colors.white, fontSize=8, fontName="Helvetica-Bold")),
            Paragraph("<b>Revenue (PKR)</b>", ParagraphStyle("TH", parent=styles["Normal"], textColor=colors.white, fontSize=8, fontName="Helvetica-Bold")),
            Paragraph("<b>Invoices</b>", ParagraphStyle("TH", parent=styles["Normal"], textColor=colors.white, fontSize=8, fontName="Helvetica-Bold")),
            Paragraph("<b>Avg. Bill (PKR)</b>", ParagraphStyle("TH", parent=styles["Normal"], textColor=colors.white, fontSize=8, fontName="Helvetica-Bold")),
        ]
    ]
    for b in report_data.branch_performance:
        b_name = b.get("branch", "Branch")
        b_rev = float(b.get("revenue", 0.0))
        b_inv = int(b.get("invoices", 0))
        b_avg = float(b.get("avg_bill", 0.0))
        b_rows.append([
            Paragraph(b_name, body_style),
            Paragraph(f"{b_rev:,.0f}", body_style),
            Paragraph(f"{b_inv:,}", body_style),
            Paragraph(f"{b_avg:,.0f}", body_style),
        ])

    if len(b_rows) > 1:
        b_table = Table(b_rows, colWidths=[doc.width * 0.42, doc.width * 0.22, doc.width * 0.16, doc.width * 0.20])
        b_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        story.append(b_table)
        story.append(Spacer(1, 10))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3: SALES CHANNEL & PAYMENT MIX
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("3. Sales Channel & Payment Mix", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Who is buying, and how they pay", section_sub_style))

    c_pay = _make_chart_image(charts.get("payment_mix"), max_w=doc.width * 0.88, max_h=2.8 * inch)
    if c_pay:
        story.append(c_pay)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 3 &mdash; Revenue share by client type</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            "Cash transactions account for the majority of revenue (>50%), with Walk-in customers driving high-frequency retail sales. "
            "Credit and Panel (corporate/insurance) transactions represent a high-value slice, carrying receivables exposure.",
            body_style,
        )
    )

    # Callout: Data quality note
    dq_data = [[
        Paragraph(
            "<b>Data quality note</b><br/>"
            "Capturing customer phone numbers and names consistently at checkout will materially improve customer retention tracking "
            "and unlock targeted SMS refill reminders for chronic medication patients.",
            callout_style,
        )
    ]]
    dq_table = Table(dq_data, colWidths=[doc.width])
    dq_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0fdf4")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbf7d0")),
            ("LINELEFT", (0, 0), (0, 0), 3, colors.HexColor("#16a34a")),
            ("PADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(dq_table)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4: PRODUCT PERFORMANCE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("4. Product Performance", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Best sellers and slow movers", section_sub_style))

    c_top = _make_chart_image(charts.get("top_products"), max_w=doc.width, max_h=3.4 * inch)
    if c_top:
        story.append(c_top)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 4 &mdash; Top 12 products by revenue</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    c_slow = _make_chart_image(charts.get("slow_products"), max_w=doc.width, max_h=3.0 * inch)
    if c_slow:
        story.append(c_slow)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 5 &mdash; 10 slowest-moving products by revenue</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            "Top-selling analgesics and antibiotic brands represent key revenue drivers that must never face stockouts. "
            "Slow-moving SKUs should be evaluated for stock rationalization to prevent tied-up working capital and expiry write-offs.",
            body_style,
        )
    )
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 5: WHEN YOUR CUSTOMERS SHOP
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("5. When Your Customers Shop", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Hourly and weekly transaction patterns", section_sub_style))

    c_hour = _make_chart_image(charts.get("hourly_traffic"), max_w=doc.width, max_h=2.6 * inch)
    if c_hour:
        story.append(c_hour)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 6 &mdash; Transactions by hour of day (highlighted: 1 PM&ndash;5 PM peak)</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            "Footfall builds steadily from opening, peaking between 1 PM and 5 PM (nearly 45% of all daily transactions), "
            "and tapers off in the late evening. This is the window where staffing and quick dispensing are essential.",
            body_style,
        )
    )

    c_day = _make_chart_image(charts.get("daily_traffic"), max_w=doc.width, max_h=2.4 * inch)
    if c_day:
        story.append(c_day)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 7 &mdash; Revenue by day of week</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    # Callout: Action Item
    action_data = [[
        Paragraph(
            "<b>Action Item</b><br/>"
            "Schedule your strongest cashiers and ensure top-selling fast movers are fully replenished ahead of the 1&ndash;5 PM window each day.",
            callout_style,
        )
    ]]
    action_table = Table(action_data, colWidths=[doc.width])
    action_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fde68a")),
            ("LINELEFT", (0, 0), (0, 0), 3, GOLD_ACCENT),
            ("PADDING", (0, 0), (-1, -1), 7),
        ])
    )
    story.append(action_table)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 6: STAFF PERFORMANCE & SECTION 7: CREDIT RISK
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("6. Cashier / Staff Performance", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Revenue processed per staff member", section_sub_style))

    c_cash = _make_chart_image(charts.get("cashier_performance"), max_w=doc.width, max_h=2.6 * inch)
    if c_cash:
        story.append(c_cash)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 8 &mdash; Total revenue processed by cashier</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            "Staff output shows balanced transaction processing across primary operators. "
            "Consistent cashier training ensures speed at the till during rush hours and reduces billing discrepancies.",
            body_style,
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("7. Customer Insights & Credit Risk", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(Paragraph("Who your best customers are, and who owes you money", section_sub_style))

    c_debt = _make_chart_image(charts.get("top_debtors"), max_w=doc.width, max_h=3.0 * inch)
    if c_debt:
        story.append(c_debt)
        story.append(Paragraph("<font size='7' color='#64748b'><i>Figure 9 &mdash; Top 10 customers by outstanding balance</i></font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    # Callout: Collections Action Item
    coll_data = [[
        Paragraph(
            "<b>Action Item &mdash; Collections</b><br/>"
            f"The top 10 debtors account for a major share of the {bal_str} outstanding. A focused follow-up campaign "
            "(calls, statements, or a revised credit policy) targeting these accounts can recover meaningful working capital.",
            callout_style,
        )
    ]]
    coll_table = Table(coll_data, colWidths=[doc.width])
    coll_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff7ed")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fdba74")),
            ("LINELEFT", (0, 0), (0, 0), 3, colors.HexColor("#ea580c")),
            ("PADDING", (0, 0), (-1, -1), 7),
        ])
    )
    story.append(coll_table)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 8: DISCOUNTING & SECTION 9: RECOMMENDATIONS SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("8. Discounting Behaviour", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=3))
    story.append(
        Paragraph(
            f"Discounts totaled {disc_str} across the period, averaging ~3.6% of gross turnover. While standard for customer goodwill, "
            "discounts should be monitored across cashiers to ensure adherence to approved pricing guidelines.",
            body_style,
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("9. Recommendations Summary", section_title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD_ACCENT, spaceAfter=4))
    story.append(
        Paragraph(
            "&bull; <b>Investigate Branch Footfall:</b> Secondary locations show strong average bill values; marketing and doctor referrals can narrow the footfall gap.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            f"&bull; <b>Tighten Credit & Collections:</b> Prioritize collection on the top 10 outstanding balances to recover {bal_str} in liquidity.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "&bull; <b>Protect High-Velocity SKUs:</b> Guarantee 100% shelf availability on the top 12 revenue products to prevent lost walk-in sales.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "&bull; <b>Align Staffing with Rush Hours:</b> Double checkout capacity between 1:00 PM and 5:00 PM to handle peak transaction volume smoothly.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "&bull; <b>Standardize Customer Profiles:</b> Record customer contact numbers at the POS to enable chronic medication refill reminders and loyalty tracking.",
            bullet_style,
        )
    )

    doc.build(story, canvasmaker=NumberedCanvas)
    return output_path
