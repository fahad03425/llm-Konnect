"""
Module 6.8 — Verified Report Generator

Public API exports for report generation, claim verification, and chart rendering.
"Code computes. The LLM narrates. A verifier checks."
"""

from app.reporting.charts import render_charts
from app.reporting.models import ReportData
from app.reporting.narrative import generate_narrative
from app.reporting.pdf_report import build_pdf_report
from app.reporting.report import (
    ReportResult,
    build_report,
    gather_report_data,
    generate_report,
)
from app.reporting.verifier import (
    STATUS_MISMATCH,
    STATUS_UNMATCHED,
    STATUS_VERIFIED,
    VerificationReport,
    VerifiedClaim,
    verify,
)

__all__ = [
    "ReportData",
    "ReportResult",
    "VerificationReport",
    "VerifiedClaim",
    "STATUS_VERIFIED",
    "STATUS_UNMATCHED",
    "STATUS_MISMATCH",
    "gather_report_data",
    "render_charts",
    "generate_narrative",
    "verify",
    "build_pdf_report",
    "build_report",
    "generate_report",
]
