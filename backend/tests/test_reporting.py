"""
Module 6.8 — Test suite for Verified Report Generator.

Enforces offline execution:
- 100% mocked LLM (zero real network/Ollama calls).
- Tests claim extraction, tolerance matching, and small integer exclusions.
- Tests retry loops and the "fail-and-flag-unverified" trust contract.
- Tests graceful degradation on LLM failure or missing anomaly detector.
- Tests chart PNG rendering and HTML/PDF assembly.
"""

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
from app.reporting.charts import render_charts
from app.reporting.models import ReportData
from app.reporting.narrative import generate_narrative
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
    accurate_narrative = (
        "During January 2026, total revenue reached PKR 1,250,000.00 with a strong gross margin of 28.0%. "
        "The pharmacy successfully processed 450 transactions. "
        "Stock expiry analysis shows PKR 45,000.00 at risk within 30 days and PKR 12,000.00 in expired stock."
    )

    vr = verify(accurate_narrative, rd)
    assert vr.all_verified is True
    assert vr.mismatch_count == 0
    assert vr.unmatched_count == 0
    assert vr.verified_count >= 5


def test_narrative_verification_retry_and_pass(mock_kpi_results, tmp_path):
    """Hallucinated narrative triggers retry with corrective prompt and passes on second try."""
    rd = gather_report_data(domain="pharmacy", kpi_results=mock_kpi_results)

    bad_narrative = "Revenue was PKR 9,999,999.00 and margin was 99.0%."
    good_narrative = "Revenue was PKR 1,250,000.00 with a gross margin of 28.0%."

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
    # Step 1 and top 3 should not be marked as unmatched hallucinations
    assert vr.all_verified is True
    assert vr.unmatched_count == 0


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
