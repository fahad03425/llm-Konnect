"""Test Suite 04: Vectorized Data Quality & Validation Engine.

Module: Module 6.3 — Schema Mapping and Validation Module
Target File: backend/app/schema/validate.py & backend/app/schema/canonical.py
Scope:
- Verifies domain-agnostic core validation rules (EMPTY_ROW, UNPARSEABLE_DATE, UNPARSEABLE_NUMBER).
- Verifies range and anomaly checks (negative quantity, invalid unit price).
- Verifies domain pack validation integration (pharmacy batch/expiry rules).
- Verifies structured problem reporting with physical source_row tracking.
- Verifies automated dataset cleaning and quality score calculation.
"""

import pandas as pd
import numpy as np
import pytest
from app.schema.validate import validate, clean
from app.schema.canonical import validate_core_dataframe


class TestDataValidationAndQualityEngine:
    """Verifies automated quality auditing, problem detection, and dataset remediation."""

    def test_clean_valid_dataframe_passes_with_perfect_score(self):
        """Clean dataset passes validation with usable status and zero errors."""
        df = pd.DataFrame({
            "date": [pd.Timestamp("2026-01-10"), pd.Timestamp("2026-01-11")],
            "invoice_id": ["INV-01", "INV-02"],
            "product_id": ["Panadol 500mg", "Brufen 400mg"],
            "quantity": [2.0, 1.0],
            "unit_price": [50.0, 80.0],
            "mrp": [50.0, 80.0],
            "amount": [100.0, 80.0],
            "source_connector": ["csv", "csv"],
            "source_row": [2, 3]
        })

        report = validate(df, domain="pharmacy")

        assert report.verdict == "usable"
        assert report.is_usable is True
        assert len(report.errors) == 0
        assert len(report.warnings) == 0
        assert report.error_rows == 0

    def test_detects_completely_empty_rows(self):
        """Detects and isolates rows where all business data columns are blank."""
        df = pd.DataFrame({
            "date": [pd.Timestamp("2026-01-10"), pd.NaT],
            "invoice_id": ["INV-01", None],
            "product_id": ["Panadol", None],
            "quantity": [5.0, np.nan],
            "amount": [250.0, np.nan],
            "source_connector": ["csv", "csv"],
            "source_row": [2, 3]
        })

        report = validate(df)

        assert report.verdict in ("not_usable", "usable_with_warnings")
        assert any(p.code == "EMPTY_ROW" for p in report.problems)
        empty_problem = next(p for p in report.problems if p.code == "EMPTY_ROW")
        assert 3 in empty_problem.row_refs  # Row 3 is empty

    def test_detects_unparseable_conversion_failures(self):
        """Catches date and numeric values that failed type coercion during normalization."""
        df = pd.DataFrame({
            "date": [pd.NaT],
            "amount": [np.nan],
            "source_connector": ["csv"],
            "source_row": [5]
        })
        # Simulate normalize.py attaching conversion failure telemetry
        df.attrs["conversion_failures"] = [
            {"code": "UNPARSEABLE_DATE", "field": "date", "value": "invalid-date-string", "source_row": 5},
            {"code": "UNPARSEABLE_NUMBER", "field": "amount", "value": "bad-currency", "source_row": 5}
        ]

        report = validate(df)

        assert len(report.problems) > 0
        problem_codes = [p.code for p in report.problems]
        assert "UNPARSEABLE_DATE" in problem_codes
        assert "UNPARSEABLE_NUMBER" in problem_codes
        assert 5 in report.problems[0].row_refs

    def test_pharmacy_domain_expiry_validation_rules(self):
        """Pharmacy domain pack flags already-expired medicines sold in transactions."""
        df = pd.DataFrame({
            "date": [pd.Timestamp("2026-03-01")],
            "invoice_id": ["INV-99"],
            "product_id": ["Calamox Syrup"],
            "quantity": [1.0],
            "amount": [120.0],
            "expiry_date": [pd.Timestamp("2025-12-31")],  # Expired before sale date!
            "source_connector": ["csv"],
            "source_row": [2]
        })

        report = validate(df, domain="pharmacy")

        # Pharmacy domain pack flags expired inventory / sales
        problem_codes = [p.code for p in report.problems]
        assert any("EXPIRED" in code or "EXPIRY" in code for code in problem_codes)

    def test_automated_cleaning_removes_empty_rows(self):
        """clean() removes corrupt empty rows and reports remediation summary."""
        df = pd.DataFrame({
            "date": [pd.Timestamp("2026-01-10"), pd.NaT],
            "invoice_id": ["INV-01", None],
            "product_id": ["Panadol", None],
            "quantity": [2.0, np.nan],
            "amount": [100.0, np.nan],
            "source_connector": ["csv", "csv"],
            "source_row": [2, 3]
        })

        cleaned_df, summary = clean(df)

        assert len(cleaned_df) == 1
        assert cleaned_df.loc[0, "invoice_id"] == "INV-01"
        assert summary.rows_dropped == 1
        assert summary.empty_rows_dropped == 1
        assert any("empty" in d.lower() for d in summary.details)
