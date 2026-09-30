"""
Module 6.8 — Test suite for Verified Report Generator.

Enforces offline execution:
- 100% mocked LLM (zero real network/Ollama calls).
- Tests claim extraction, tolerance matching, and small integer exclusions.
- Tests retry loops and the "fail-and-flag-unverified" trust contract.
- Tests graceful degradation on LLM failure or missing anomaly detector.
- Tests chart PNG rendering and HTML/PDF assembly.
"""

import json
from app.reporting.grounding import parse_response
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from app.analytics.engine import engine
from app.analytics.models import (
    STATUS_OK,
    UNIT_COUNT,
    UNIT_CURRENCY,
    UNIT_PERCENT,
    KPIResult,
    Period,
    Provenance,
)
from app.core.config import settings
from app.reporting.charts import (
    render_charts,
    render_supplier_payables_chart,
    render_dead_stock_chart,
    render_category_margin_chart,
)
from app.reporting.models import ReportData
from app.reporting.narrative import (
    generate_narrative,
    generate_weekly_executive_lead,
)
from app.reporting.pdf_report import build_pdf_report
from app.reporting.report import (
    build_report,
    gather_report_data,
    generate_report,
)
from app.reporting.verifier import (
    STATUS_MISMATCH,
    STATUS_UNMATCHED,
    STATUS_VERIFIED,
    VerificationReport,
    verify,
)


@pytest.fixture
def mock_kpi_results():
    """Build a deterministic dictionary of mock KPI results."""
    return {
        "total_revenue": KPIResult(
            key="total_revenue",
            name="Total Revenue",
            value=1_250_000.0,
            unit=UNIT_CURRENCY,
            formula="sum(amount)",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
            period=Period(start="2026-01-01", end="2026-01-31"),
        ),
        "gross_profit": KPIResult(
            key="gross_profit",
            name="Gross Profit",
            value=350_000.0,
            unit=UNIT_CURRENCY,
            formula="revenue - cost",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
        ),
        "gross_margin_pct": KPIResult(
            key="gross_margin_pct",
            name="Gross Margin %",
            value=28.0,
            unit=UNIT_PERCENT,
            formula="gross_profit / revenue * 100",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
        ),
        "transaction_count": KPIResult(
            key="transaction_count",
            name="Transaction Count",
            value=450.0,
            unit=UNIT_COUNT,
            formula="distinct(invoice_id)",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
        ),
        "expiring_value_30d": KPIResult(
            key="expiring_value_30d",
            name="Expiring Value (30 Days)",
            value=45_000.0,
            unit=UNIT_CURRENCY,
            formula="sum(near_expiry)",
            provenance=Provenance(row_count=10),
            status=STATUS_OK,
            breakdown=[
                {"name": "Amoxicillin 500mg", "batch_no": "B101", "expiry_date": "2026-02-15", "days_to_expiry": 15, "amount": 25000.0},
                {"name": "Panadol Extra", "batch_no": "B202", "expiry_date": "2026-02-28", "days_to_expiry": 28, "amount": 20000.0},
            ],
        ),
        "expired_stock_value": KPIResult(
            key="expired_stock_value",
            name="Expired Stock Value",
            value=12_000.0,
            unit=UNIT_CURRENCY,
            formula="sum(expired)",
            provenance=Provenance(row_count=2),
            status=STATUS_OK,
        ),
        "revenue_trend": KPIResult(
            key="revenue_trend",
            name="Revenue Trend",
            value=5.2,
            unit=UNIT_PERCENT,
            formula="growth",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
            series=[
                {"period": "2025-11", "revenue": 1_100_000.0, "moving_average": 1_100_000.0},
                {"period": "2025-12", "revenue": 1_180_000.0, "moving_average": 1_140_000.0},
                {"period": "2026-01", "revenue": 1_250_000.0, "moving_average": 1_176_667.0},
            ],
        ),
        "revenue_forecast": KPIResult(
            key="revenue_forecast",
            name="Revenue Forecast",
            value=1_310_000.0,
            unit=UNIT_CURRENCY,
            formula="forecast",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
            forecast=[
                {"period": "2026-02", "estimate": 1_310_000.0, "lower": 1_220_000.0, "upper": 1_400_000.0},
                {"period": "2026-03", "estimate": 1_360_000.0, "lower": 1_250_000.0, "upper": 1_470_000.0},
            ],
        ),
        "revenue_breakdown_by_category": KPIResult(
            key="revenue_breakdown_by_category",
            name="Revenue Breakdown by Category",
            value=1_250_000.0,
            unit=UNIT_CURRENCY,
            formula="grouped",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
            breakdown=[
                {"category": "Antibiotics", "amount": 600_000.0},
                {"category": "Analgesics", "amount": 400_000.0},
                {"category": "Supplements", "amount": 250_000.0},
            ],
        ),
    }


@pytest.fixture
def mock_sample_df():
    """Create a realistic pharmacy sales dataset."""
    return pd.DataFrame({
        "source_row": list(range(1, 11)),
        "date": ["2026-01-05", "2026-01-10", "2026-01-12", "2026-01-15", "2026-01-18",
                 "2026-01-20", "2026-01-22", "2026-01-25", "2026-01-28", "2026-01-30"],
        "txn_type": ["sale"] * 8 + ["refund", "expense"],
        "invoice_id": [f"INV-100{i}" for i in range(1, 11)],
        "product_id": ["Amox-500", "Panadol", "Augmentin", "Panadol", "Brufen", "Amox-500", "Flagyl", "Disprin", "Brufen", "Rent"],
        "generic_name": ["Amoxicillin", "Paracetamol", "Co-amoxiclav", "Paracetamol", "Ibuprofen", "Amoxicillin", "Metronidazole", "Aspirin", "Ibuprofen", "General"],
        "category": ["Antibiotics", "Analgesics", "Antibiotics", "Analgesics", "NSAIDs", "Antibiotics", "Antiprotozoal", "Analgesics", "NSAIDs", "Overheads"],
        "quantity": [10, 50, 5, 30, 20, 15, 25, 40, -5, 1],
        "amount": [5000.0, 7500.0, 12000.0, 4500.0, 6000.0, 7500.0, 3750.0, 2000.0, -1500.0, 15000.0],
        "cost": [3500.0, 5000.0, 8000.0, 3000.0, 4200.0, 5250.0, 2500.0, 1400.0, -1000.0, 15000.0],
        "expiry_date": ["2026-02-15", "2026-02-28", "2027-05-01", "2026-03-10", "2026-08-15", "2026-02-15", "2027-01-10", "2026-02-01", "2026-08-15", ""],
        "batch_no": ["B101", "B202", "B303", "B202", "B404", "B101", "B505", "B606", "B404", ""],
    })


# ──────────────────────────────────────────────────────────────────────────────
# Unit & Integration Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_gather_report_data_complete(mock_kpi_results):
    """Test gathering ReportData with KPIs and anomalies."""
    anomalies = [{"row_ref": "INV-1009", "kind": "Negative Refund", "amount": 1500.0}]
    rd = gather_report_data(domain="pharmacy", business_name="Shifa Care", anomalies=anomalies, kpi_results=mock_kpi_results)

    assert rd.domain == "pharmacy"
    assert rd.business_name == "Shifa Care"
    assert "revenue_trends" in rd.sections
    assert len(rd.kpis) == len(mock_kpi_results)
    assert rd.anomalies == anomalies

    # Check verifiable ground truth numbers extraction
    numbers = rd.get_all_verifiable_numbers()
    assert len(numbers) > 10
    keys = [n[0] for n in numbers]
    assert "total_revenue" in keys
    assert "gross_margin_pct" in keys
    assert "anomaly_count" in keys


def test_gather_report_data_degrades_without_anomalies(mock_kpi_results):
    """Test gathering ReportData when anomalies are None/absent."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results, anomalies=None)
    assert rd.anomalies is None
    assert rd.get_kpi("total_revenue") is not None


def test_render_charts(mock_kpi_results, tmp_path):
    """Test static chart rendering to PNG files."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results)
    charts = render_charts(rd, output_dir=tmp_path)

    assert "trend" in charts
    assert "breakdown" in charts
    assert "expiry" in charts

    for c_name, c_path in charts.items():
        p = Path(c_path)
        assert p.exists()
        assert p.stat().st_size > 1000  # Non-trivial image size


def test_narrative_verification_pass(mock_kpi_results):
    """Accurate narrative passes verification on first attempt."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results)
    accurate_narrative = parse_response(json.dumps({
        "fact_ids": ["total_revenue", "gross_margin_pct", "transaction_count", "expiring_value_30d", "expired_stock_value"],
        "commentary": ["Review expiry exposure and prioritize affected batches."],
    }), rd)

    vr = verify(accurate_narrative, rd)
    assert vr.all_verified is True
    assert vr.mismatch_count == 0
    assert vr.unmatched_count == 0
    assert vr.verified_count >= 5


def test_narrative_verification_retry_and_pass(mock_kpi_results, tmp_path):
    """Hallucinated narrative triggers retry with corrective prompt and passes on second try."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results)

    bad_narrative = "Revenue was PKR 9,999,999.00 and margin was 99.0%."
    good_narrative = json.dumps({"fact_ids": ["total_revenue", "gross_margin_pct"], "commentary": []})

    with patch("app.core.llm.llm.generate", side_effect=[bad_narrative, good_narrative]):
        with patch.object(settings, "reports_dir", str(tmp_path)):
            result = generate_report(
                domain="pharmacy",
                business_name="Al-Hikmah Pharmacy",
                kpi_results=mock_kpi_results,
                max_regeneration_attempts=1,
            )

            assert result.regenerated is True
            assert result.verification_passed is True
            assert result.verification.all_verified is True
            assert result.html_path is not None
            assert Path(result.html_path).exists()


def test_narrative_verification_fail_and_flag_unverified(mock_kpi_results, tmp_path):
    """Persistent hallucination produces an UNVERIFIED report with clear warnings."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results)

    persistent_hallucination = "Revenue was PKR 9,999,999.00 with 99.9% margin."

    with patch("app.core.llm.llm.generate", return_value=persistent_hallucination):
        with patch.object(settings, "reports_dir", str(tmp_path)):
            result = generate_report(
                domain="pharmacy",
                business_name="Test Store",
                kpi_results=mock_kpi_results,
                max_regeneration_attempts=1,
            )

            assert result.verification_passed is False
            assert result.verification.all_verified is False
            assert result.verification.mismatch_count > 0
            assert any("UNVERIFIED" in w for w in result.warnings)
            
            # Critical trust check: Report is STILL produced on disk
            assert result.html_path is not None
            assert Path(result.html_path).exists()
            assert result.pdf_path is not None
            assert Path(result.pdf_path).exists()

            # Verify HTML contains the warning banner
            html_content = Path(result.html_path).read_text(encoding="utf-8")
            assert "Verification Warning" in html_content
            assert "banner-warn" in html_content


def test_small_integer_exclusion(mock_kpi_results):
    """Small integers (<= 9) like ordinals/counts without currency or % are ignored."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results)
    narrative_with_ordinals = (
        "In step 1 of our evaluation, top 3 categories drove sales. "
        "Total revenue reached PKR 1,250,000.00."
    )

    vr = verify(narrative_with_ordinals, rd)
    # Unbound counts must not evade verification, even when phrased as a ranking.
    assert vr.all_verified is False
    assert vr.unmatched_count > 0


def test_graceful_degradation_when_llm_offline(mock_kpi_results, tmp_path):
    """When Ollama is unreachable, report still builds charts and tables cleanly."""
    with patch("app.core.llm.llm.generate", side_effect=RuntimeError("Ollama offline")):
        with patch.object(settings, "reports_dir", str(tmp_path)):
            result = generate_report(
                domain="pharmacy",
                business_name="Offline Test",
                kpi_results=mock_kpi_results,
            )

            assert result.html_path is not None
            assert Path(result.html_path).exists()
            assert result.pdf_path is not None
            assert Path(result.pdf_path).exists()
            assert any("unavailable" in w.lower() for w in result.warnings)

            html_content = Path(result.html_path).read_text(encoding="utf-8")
            assert "Executive Summary" in html_content

def test_html_and_pdf_generation(mock_kpi_results, tmp_path):
    """Test that both HTML and PDF files are generated on disk and non-empty."""
    rd = gather_report_data(domain="pharmacy", business_name="City Pharmacy", kpi_results=mock_kpi_results)
    charts = render_charts(rd, output_dir=tmp_path / "charts")
    vr = VerificationReport(claims=[])

    out_paths = build_report(
        report_data=rd,
        charts=charts,
        narrative="This is a test narrative for City Pharmacy.",
        verification=vr,
        formats=("html", "pdf"),
        output_dir=tmp_path,
    )

    assert "html" in out_paths
    assert "pdf" in out_paths

    html_file = Path(out_paths["html"])
    pdf_file = Path(out_paths["pdf"])

    assert html_file.exists() and html_file.stat().st_size > 500
    assert pdf_file.exists() and pdf_file.stat().st_size > 2000

    html_text = html_file.read_text(encoding="utf-8")
    assert "City Pharmacy" in html_text
    assert "Executive Summary" in html_text


def test_end_to_end_pharmacy_report(mock_sample_df, tmp_path):
    """End-to-end integration test with canonical DataFrame and mocked LLM."""
    mock_llm_response = (
        "In this period, total revenue reached PKR 48,250.00 with transaction count of 8. "
        "Gross margin stood at 33.7%. "
        "Near expiry stock is monitored closely."
    )

    with patch("app.core.llm.llm.generate", return_value=mock_llm_response):
        with patch.object(settings, "reports_dir", str(tmp_path)):
            result = generate_report(
                source_df=mock_sample_df,
                domain="pharmacy",
                business_name="Medix Pharmacy",
                formats=("html", "pdf"),
            )

            assert result.html_path is not None
            assert result.pdf_path is not None
            assert Path(result.html_path).exists()
            assert Path(result.pdf_path).exists()
            assert "trend" in result.charts


# ---------------------------------------------------------------------------
# Extended ReportData and Verifiable Numbers Tests
# ---------------------------------------------------------------------------


def test_regression_existing_report_data_verifiable_numbers_unaffected():
    """
    Regression guard: Ensure that an existing ReportData without the new fields
    produces the exact same ground-truth tuples as the pre-change implementation.
    """
    anomalies = [{"row_ref": "INV-1009", "kind": "Negative Refund", "amount": 1500.0}]
    kpis = {
        "total_revenue": KPIResult(
            key="total_revenue",
            name="Total Revenue",
            value=1250000.0,
            unit=UNIT_CURRENCY,
            formula="sum(amount)",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
        ),
        "gross_margin_pct": KPIResult(
            key="gross_margin_pct",
            name="Gross Margin %",
            value=28.0,
            unit=UNIT_PERCENT,
            formula="gross_profit / revenue * 100",
            provenance=Provenance(row_count=100),
            status=STATUS_OK,
        ),
    }
    rd = ReportData(
        kpis=kpis,
        domain="pharmacy",
        business_name="Shifa Care",
        anomalies=anomalies,
        branch_performance=[{"branch": "F-8", "revenue": 500000.0, "invoices": 200, "avg_bill": 2500.0}],
        top_products=[{"name": "Panadol", "amount": 75000.0}],
        top_debtors=[{"customer": "Clinic A", "balance": 45000.0}],
    )

    numbers = rd.get_all_verifiable_numbers()
    expected_pre_change_snapshot = [
        ("total_revenue", 1250000.0, "PKR"),
        ("gross_margin_pct", 28.0, "percent"),
        ("branch:F-8:revenue", 500000.0, "PKR"),
        ("branch:F-8:invoices", 200.0, "count"),
        ("branch:F-8:avg_bill", 2500.0, "PKR"),
        ("product:Panadol:revenue", 75000.0, "PKR"),
        ("debtor:Clinic A:balance", 45000.0, "PKR"),
        ("anomaly_count", 1.0, "count"),
    ]
    assert numbers == expected_pre_change_snapshot


def test_extended_report_data_verifiable_numbers_and_headline_kpis(mock_kpi_results):
    """
    Verify ReportData populated with new credit, margin, dead stock, and reorder fields
    emits the expected verifiable tuples and includes new headline KPIs.
    """
    kpis = dict(mock_kpi_results)
    kpis["supplier_payable_total"] = KPIResult(
        key="supplier_payable_total",
        name="Supplier Payable Total",
        value=150000.0,
        unit=UNIT_CURRENCY,
        formula="sum(supplier_payable_amount)",
        provenance=Provenance(row_count=5),
        status=STATUS_OK,
    )
    kpis["dead_stock_value"] = KPIResult(
        key="dead_stock_value",
        name="Dead Stock Value",
        value=35000.0,
        unit=UNIT_CURRENCY,
        formula="sum(dead_stock)",
        provenance=Provenance(row_count=2),
        status=STATUS_OK,
    )

    rd = ReportData(
        kpis=kpis,
        domain="pharmacy",
        payment_mix=[
            {"payment_method": "Cash", "revenue": 80000.0, "share_pct": 80.0},
            {"payment_method": "Card", "revenue": 20000.0, "share_pct": 20.0},
        ],
        supplier_payables=[
            {"supplier_name": "Getz Pharma", "total_payable": 100000.0, "earliest_due_date": "2026-03-01"},
            {"supplier_name": "GSK", "total_payable": 50000.0, "earliest_due_date": "2026-03-15"},
        ],
        dead_stock_items=[
            {"product_name": "Augmentin 625mg", "value": 20000.0, "quantity": 25.0, "days_since_sale": 95},
            {"product_name": "Flagyl 400mg", "value": 15000.0, "quantity": 50.0, "days_since_sale": 70},
        ],
        category_margins=[
            {"category": "Antibiotics", "revenue": 100000.0, "cost": 65000.0, "gross_margin_pct": 35.0},
            {"category": "Analgesics", "revenue": 50000.0, "cost": 35000.0, "gross_margin_pct": 30.0},
        ],
        reorder_alerts=[
            {"product_name": "Panadol 500mg", "days_until_stockout": 3.0, "current_stock": 30.0},
        ],
        category_trend_note="Antibiotics gross margin increased by 2.5% this quarter.",
    )

    # 1. Headline KPIs should include supplier_payable_total and dead_stock_value
    headline = rd.get_headline_kpis()
    assert "supplier_payable_total" in headline
    assert headline["supplier_payable_total"].value == 150000.0
    assert "dead_stock_value" in headline
    assert headline["dead_stock_value"].value == 35000.0

    # 2. Verifiable numbers must include tuples from all extended fields
    numbers = rd.get_all_verifiable_numbers()
    num_dict = {label: (val, unit) for label, val, unit in numbers}

    # Payment mix
    assert num_dict["payment_mix:Cash:revenue"] == (80000.0, UNIT_CURRENCY)
    assert num_dict["payment_mix:Cash:share_pct"] == (80.0, UNIT_PERCENT)
    assert num_dict["payment_mix:Card:revenue"] == (20000.0, UNIT_CURRENCY)
    assert num_dict["payment_mix:Card:share_pct"] == (20.0, UNIT_PERCENT)

    # Supplier payables
    assert num_dict["supplier:Getz Pharma:total_payable"] == (100000.0, UNIT_CURRENCY)
    assert num_dict["supplier:GSK:total_payable"] == (50000.0, UNIT_CURRENCY)

    # Dead stock items
    assert num_dict["dead_stock:Augmentin 625mg:value"] == (20000.0, UNIT_CURRENCY)
    assert num_dict["dead_stock:Augmentin 625mg:quantity"] == (25.0, UNIT_COUNT)
    assert num_dict["dead_stock:Augmentin 625mg:days_since_sale"] == (95.0, UNIT_COUNT)
    assert num_dict["dead_stock:Flagyl 400mg:value"] == (15000.0, UNIT_CURRENCY)

    # Category margins
    assert num_dict["category_margin:Antibiotics:revenue"] == (100000.0, UNIT_CURRENCY)
    assert num_dict["category_margin:Antibiotics:cost"] == (65000.0, UNIT_CURRENCY)
    assert num_dict["category_margin:Antibiotics:gross_margin_pct"] == (35.0, UNIT_PERCENT)
    assert num_dict["category_margin:Analgesics:gross_margin_pct"] == (30.0, UNIT_PERCENT)

    # Reorder alerts
    assert num_dict["reorder_alert:Panadol 500mg:days_until_stockout"] == (3.0, UNIT_COUNT)
    assert num_dict["reorder_alert:Panadol 500mg:current_stock"] == (30.0, UNIT_COUNT)

    # 3. Serialization to dict includes all fields
    rd_dict = rd.to_dict()
    assert rd_dict["supplier_payables"] == rd.supplier_payables
    assert rd_dict["dead_stock_items"] == rd.dead_stock_items
    assert rd_dict["category_margins"] == rd.category_margins
    assert rd_dict["reorder_alerts"] == rd.reorder_alerts
    assert rd_dict["category_trend_note"] == rd.category_trend_note


def test_regression_default_report_type_unaffected(mock_kpi_results, mock_sample_df):
    """
    Regression test: Calling gather_report_data with no report_type argument
    must produce the exact baseline ReportData.sections list and same kpis dict keys.
    """
    rd = gather_report_data(
        source_df=mock_sample_df,
        domain="pharmacy",
        kpi_results=mock_kpi_results,
    )

    baseline_sections = [
        "revenue_trends", "branch_performance", "payment_mix",
        "products", "shopping_hours", "cashiers", "credit_risk", "recommendations"
    ]
    assert rd.sections == baseline_sections
    assert set(rd.kpis.keys()) == set(mock_kpi_results.keys())
    assert rd.supplier_payables == []
    assert rd.dead_stock_items == []
    assert rd.category_margins == []
    assert rd.reorder_alerts == []
    assert rd.category_trend_note is None


def test_weekly_pharmacy_report_type_period_and_sections():
    """
    Verify report_type == 'weekly_pharmacy' and domain == 'pharmacy':
    1. Sets period to last 7 days vs prior 7 days based on source_df max date.
    2. Populates supplier_payables, dead_stock_items, category_margins, payment_mix,
       reorder_alerts, and shrinkage anomalies.
    3. Adds new section keys in required order:
       cash_card_mix, expiry_loss_exposure, supplier_credit, dead_stock,
       category_margin, reorder_alerts, shrinkage_flags, seasonal_trend.
    """
    # Build dataset ending at 2026-03-10
    records = [
        # Recent sales
        {
            "invoice_id": "INV-101", "date": "2026-03-10", "product_id": "MED-A",
            "category": "Antibiotics", "quantity": 10.0, "unit_price": 100.0,
            "cost": 60.0, "amount": 1000.0, "payment_method": "Cash",
            "opening_stock_qty": 50.0, "closing_stock_qty": 20.0,  # decrease=30 vs sales=10 -> 20 units shrinkage
            "last_sold_date": "2026-03-10", "expiry_date": "2026-03-25",
            "supplier_id": "SUPP-1", "supplier_name": "Getz Pharma",
            "supplier_payable_amount": 50000.0, "supplier_payment_due_date": "2026-03-15",
            "txn_type": "sale",
        },
        # Dead stock product (last sold 90 days ago)
        {
            "invoice_id": "INV-102", "date": "2026-03-08", "product_id": "MED-DEAD",
            "category": "Analgesics", "quantity": 5.0, "unit_price": 50.0,
            "cost": 30.0, "amount": 250.0, "payment_method": "Card",
            "opening_stock_qty": 20.0, "closing_stock_qty": 15.0,
            "last_sold_date": "2025-11-01", "expiry_date": "2026-04-10",
            "supplier_id": "SUPP-2", "supplier_name": "GSK",
            "supplier_payable_amount": 20000.0, "supplier_payment_due_date": "2026-03-20",
            "txn_type": "sale",
        },
        # Fast moving low stock for reorder alert
        {
            "invoice_id": "INV-103", "date": "2026-03-04", "product_id": "MED-FAST",
            "category": "Cardio", "quantity": 20.0, "unit_price": 200.0,
            "cost": 150.0, "amount": 4000.0, "payment_method": "Cash",
            "opening_stock_qty": 25.0, "closing_stock_qty": 5.0,
            "last_sold_date": "2026-03-04", "expiry_date": "2027-01-01",
            "stock": 2.0, "reorder_level": 10.0,
            "supplier_id": "SUPP-1", "supplier_name": "Getz Pharma",
            "supplier_payable_amount": 10000.0, "supplier_payment_due_date": "2026-03-18",
            "txn_type": "sale",
        },
    ]
    df = pd.DataFrame(records)
    df["source_row"] = df.index + 2

    rd = gather_report_data(
        source_df=df,
        domain="pharmacy",
        report_type="weekly_pharmacy",
    )

    # 1. Period check: max date is 2026-03-10, so 7-day period is 2026-03-04 to 2026-03-10
    assert rd.period is not None
    assert rd.period.start == "2026-03-04"
    assert rd.period.end == "2026-03-10"

    # 2. Check 5 new fields populated
    assert len(rd.supplier_payables) > 0
    assert len(rd.dead_stock_items) > 0
    assert len(rd.category_margins) > 0
    assert len(rd.payment_mix) > 0
    assert len(rd.reorder_alerts) > 0
    assert rd.category_trend_note is not None

    # Check shrinkage anomalies in rd.anomalies
    assert rd.anomalies is not None
    shrinkage_anoms = [
        a for a in rd.anomalies
        if a.get("metric_name") == "stock_movement_mismatch" or a.get("anomaly_type") == "stock_movement_mismatch"
    ]
    assert len(shrinkage_anoms) >= 1

    # 3. Check section keys in exact specified order
    expected_order = [
        "cash_card_mix", "expiry_loss_exposure", "supplier_credit", "dead_stock",
        "category_margin", "reorder_alerts", "shrinkage_flags", "seasonal_trend"
    ]
    for key in expected_order:
        assert key in rd.sections, f"Expected section '{key}' to be in rd.sections"

    # Verify relative order of the new section keys
    indices = [rd.sections.index(k) for k in expected_order]
    assert indices == sorted(indices), f"Sections are not in expected order: {rd.sections}"


def test_weekly_executive_lead_verification():
    """
    Test generate_weekly_executive_lead() verified flow:
    - Asserts no unmatched/mismatch claims when LLM narrative only uses ground-truth numbers.
    - Asserts a mismatch IS caught if a wrong number is injected into the mocked LLM response.
    - Asserts an unmatched claim IS caught if an ungrounded number is injected.
    - Asserts conditional omission works cleanly when certain facts are omitted from ground truth.
    - Asserts dispatch via generate_narrative(report_type='weekly_pharmacy') works identically.
    """
    kpis = {
        "net_profit": KPIResult(
            key="net_profit",
            name="Net Profit",
            value=120000.0,
            unit=UNIT_CURRENCY,
            formula="revenue - cogs",
            provenance=Provenance(row_count=100),
        ),
        "near_expiry_total": KPIResult(
            key="near_expiry_total",
            name="Near Expiry Total",
            value=15000.0,
            unit=UNIT_CURRENCY,
            formula="sum(near_expiry)",
            provenance=Provenance(row_count=10),
        ),
    }
    supplier_payables = [
        {
            "supplier_name": "Getz Pharma",
            "total_payable": 45000.0,
            "earliest_due_date": "2026-03-15",
        }
    ]
    reorder_alerts = [
        {
            "product_name": "Panadol",
            "days_until_stockout": 2.0,
        }
    ]

    rd = ReportData(
        domain="pharmacy",
        business_name="Fazal Din Pharmacy",
        kpis=kpis,
        supplier_payables=supplier_payables,
        reorder_alerts=reorder_alerts,
    )

    # 1. Grounded response: matches all verifiable numbers
    grounded_response = json.dumps({
        "fact_ids": list(dict.fromkeys(key for key, _, _ in rd.get_all_verifiable_numbers()))[:3],
        "commentary": ["Review affected stock and supplier payment priorities."],
    })

    with patch("app.core.llm.llm.generate", return_value=grounded_response):
        lead = generate_weekly_executive_lead(rd)

    vr = verify(lead, rd)
    assert vr.all_verified is True
    assert vr.unmatched_count == 0
    assert vr.mismatch_count == 0
    assert vr.verified_count >= 3

    # Also test dispatch via generate_narrative(report_type="weekly_pharmacy")
    with patch("app.core.llm.llm.generate", return_value=grounded_response):
        dispatched_lead = generate_narrative(rd, report_type="weekly_pharmacy")
    assert dispatched_lead == lead

    # 2. Injected mismatch: slightly wrong number within candidate threshold but exceeding tolerance
    mismatch_response = (
        "You made PKR 123,500.00 this week. PKR 15,000.00 of stock needs attention. "
        "Panadol is about to run out. You owe Getz Pharma PKR 45,000.00, due 2026-03-15."
    )

    with patch("app.core.llm.llm.generate", return_value=mismatch_response):
        lead_mismatch = generate_weekly_executive_lead(rd)

    vr_mismatch = verify(lead_mismatch, rd)
    assert vr_mismatch.all_verified is False
    assert vr_mismatch.mismatch_count >= 1

    # 3. Injected unmatched figure: completely fabricated number
    unmatched_response = (
        "You made PKR 999,999.00 this week. PKR 15,000.00 of stock needs attention."
    )

    with patch("app.core.llm.llm.generate", return_value=unmatched_response):
        lead_unmatched = generate_weekly_executive_lead(rd)

    vr_unmatched = verify(lead_unmatched, rd)
    assert vr_unmatched.all_verified is False
    assert vr_unmatched.mismatch_count >= 1

    # 4. Conditional omission: when payables & expiry are absent, lead cleanly omits them
    rd_minimal = ReportData(
        domain="pharmacy",
        business_name="Fazal Din Pharmacy",
        kpis={
            "net_profit": KPIResult(
                key="net_profit",
                name="Net Profit",
                value=120000.0,
                unit=UNIT_CURRENCY,
                formula="revenue - cogs",
                provenance=Provenance(row_count=100),
            )
        },
        reorder_alerts=[{"product_name": "Panadol", "days_until_stockout": 2.0}],
    )
    omitted_response = json.dumps({"fact_ids": ["net_profit"], "commentary": []})

    with patch("app.core.llm.llm.generate", return_value=omitted_response):
        lead_omitted = generate_weekly_executive_lead(rd_minimal)

    vr_omitted = verify(lead_omitted, rd_minimal)
    assert vr_omitted.all_verified is True
    assert vr_omitted.unmatched_count == 0
    assert vr_omitted.mismatch_count == 0
    assert vr_omitted.verified_count >= 1


def test_weekly_pharmacy_pdf_and_html_generation(tmp_path):
    """
    Test weekly pharmacy PDF and HTML generation:
    - Verifies new chart functions build valid image files (supplier_payables, dead_stock, category_margin).
    - Verifies HTML rendering contains sections in exact required order:
      Executive Summary -> Cash/Card & Sales -> Expiry Loss Exposure -> Supplier Credit ->
      Dead Stock -> Margin by Category -> Reorder Alerts -> Shrinkage Flags -> Seasonal Trend.
    - Verifies PDF builds successfully and is non-empty.
    - Verifies standard non-weekly report HTML/PDF does not contain the weekly sections.
    """
    records = [
        {
            "invoice_id": "INV-101",
            "date": "2026-03-10",
            "product_id": "P-101",
            "product_name": "Augmentin 625mg",
            "category": "Antibiotics",
            "quantity": 20.0,
            "unit_price": 500.0,
            "cost": 350.0,
            "amount": 10000.0,
            "payment_method": "Cash",
            "opening_stock_qty": 100.0,
            "closing_stock_qty": 40.0,  # shrinkage: 100-40 = 60 vs 20 sold -> 40 shrinkage
            "last_sold_date": "2025-10-01",
            "expiry_date": "2026-03-25",
            "stock": 2.0,
            "reorder_level": 10.0,
            "supplier_id": "SUPP-1",
            "supplier_name": "GSK Pakistan",
            "supplier_payable_amount": 45000.0,
            "supplier_payment_due_date": "2026-03-20",
            "txn_type": "sale",
        },
        {
            "invoice_id": "INV-102",
            "date": "2026-03-09",
            "product_id": "P-102",
            "product_name": "Brufen 400mg",
            "category": "Analgesics",
            "quantity": 10.0,
            "unit_price": 200.0,
            "cost": 120.0,
            "amount": 2000.0,
            "payment_method": "Credit",
            "opening_stock_qty": 50.0,
            "closing_stock_qty": 40.0,
            "last_sold_date": "2026-03-09",
            "expiry_date": "2027-01-01",
            "stock": 15.0,
            "reorder_level": 5.0,
            "supplier_id": "SUPP-2",
            "supplier_name": "Abbott",
            "supplier_payable_amount": 15000.0,
            "supplier_payment_due_date": "2026-03-25",
            "txn_type": "sale",
        },
    ]
    df = pd.DataFrame(records)

    with patch.object(settings, "reports_dir", str(tmp_path)):
        with patch("app.core.llm.llm.generate", return_value="You made PKR 12,000.00 this week."):
            rep = generate_report(
                source_df=df,
                domain="pharmacy",
                business_name="Shifa Care Pharmacy",
                report_type="weekly_pharmacy",
            )

    # 1. Assert files exist and non-trivial size
    assert rep.pdf_path is not None
    assert rep.html_path is not None
    pdf_p = Path(rep.pdf_path)
    html_p = Path(rep.html_path)
    assert pdf_p.exists() and pdf_p.stat().st_size > 5000
    assert html_p.exists() and html_p.stat().st_size > 2000

    # 2. Check generated charts
    charts = rep.charts
    assert "supplier_payables" in charts
    assert "dead_stock" in charts
    assert "category_margin" in charts

    # 3. Check HTML section headings and sequence
    html_text = html_p.read_text(encoding="utf-8")
    assert "Weekly Performance Report" in html_text
    assert "Executive Summary" in html_text
    assert "1. Cash vs. Card &amp; Payment Channel Mix" in html_text
    assert "2. Expiry Loss Exposure" in html_text
    assert "3. Supplier Credit &amp; Accounts Payable" in html_text
    assert "4. Dead Stock &amp; Slow Capital Exposure" in html_text
    assert "5. Gross Profit Margin by Category" in html_text
    assert "6. Stockout Risk &amp; Priority Reorder Alerts" in html_text
    assert "7. Inventory Shrinkage &amp; Movement Flags" in html_text
    assert "8. Seasonal Patterns &amp; Forward Trend" in html_text

    # Verify heading order in HTML
    expected_titles = [
        "Executive Summary",
        "1. Cash vs. Card &amp; Payment Channel Mix",
        "2. Expiry Loss Exposure",
        "3. Supplier Credit &amp; Accounts Payable",
        "4. Dead Stock &amp; Slow Capital Exposure",
        "5. Gross Profit Margin by Category",
        "6. Stockout Risk &amp; Priority Reorder Alerts",
        "7. Inventory Shrinkage &amp; Movement Flags",
        "8. Seasonal Patterns &amp; Forward Trend",
    ]
    pos = 0
    for title in expected_titles:
        found = html_text.find(title, pos)
        assert found != -1, f"Heading '{title}' not found after position {pos}"
        pos = found + len(title)

    # 4. Regression check: standard report does not contain the weekly sections
    with patch.object(settings, "reports_dir", str(tmp_path)):
        with patch("app.core.llm.llm.generate", return_value="During 2026 revenue was strong."):
            rep_std = generate_report(
                source_df=df,
                domain="pharmacy",
                business_name="Standard Pharmacy",
                report_type="standard",
            )
    std_html = Path(rep_std.html_path).read_text(encoding="utf-8")
    assert "Supplier Credit &amp; Accounts Payable" not in std_html
    assert "Dead Stock &amp; Slow Capital Exposure" not in std_html
    assert "Stockout Risk &amp; Priority Reorder Alerts" not in std_html
    assert "1. Revenue Trends" in std_html
    assert "2. Branch Performance" in std_html




