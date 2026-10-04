"""Test Suite 02: Duplicate Invoice & Collision Detection.

Module: Module 6.7 — Statistical Anomaly Detection Module
Target File: backend/app/anomaly/detectors.py (detect_duplicate_invoices)
Scope:
- Verifies exact duplicate transaction detection (matching invoice_id, date, product, amount).
- Verifies invoice ID collisions across conflicting dates.
- Verifies invoice ID collisions across conflicting customer accounts.
- Verifies safe filtering of blank, null, or placeholder invoice IDs.
- Verifies complete row-level audit lineage in metadata['matching_rows'].
"""

import pytest
import pandas as pd
from app.anomaly.models import AnomalyType, Severity
from app.anomaly.detectors import detect_duplicate_invoices


class TestDuplicateAndCollisionDetection:
    """Verifies deterministic identification of duplicate transactions and invoice identifier collisions."""

    def test_exact_duplicate_invoice_detection(self, duplicate_invoices_df):
        """Identifies exact duplicate transactions sharing invoice_id, product_id, date, and amount."""
        anomalies = detect_duplicate_invoices(duplicate_invoices_df)

        exact_dups = [a for a in anomalies if a.method == "exact_duplicate"]
        assert len(exact_dups) >= 1

        dup = exact_dups[0]
        assert dup.anomaly_type == AnomalyType.DUPLICATE_INVOICE
        assert dup.severity == Severity.HIGH
        assert dup.observed_value == "INV-DUP-100"
        assert dup.metadata["duplicate_count"] == 2
        assert set(dup.metadata["matching_rows"]) == {10, 11}

    def test_invoice_id_collision_conflicting_dates(self, duplicate_invoices_df):
        """Identifies invoice ID collision where the same invoice ID appears across conflicting dates."""
        anomalies = detect_duplicate_invoices(duplicate_invoices_df)

        date_collisions = [
            a for a in anomalies
            if a.method == "collision" and a.metadata.get("conflict_field") == "date"
        ]
        assert len(date_collisions) >= 1

        col = date_collisions[0]
        assert col.anomaly_type == AnomalyType.DUPLICATE_INVOICE
        assert col.severity == Severity.HIGH
        assert col.observed_value == "INV-COL-200"
        assert "2026-03-02" in col.metadata["distinct_values"]
        assert "2026-03-15" in col.metadata["distinct_values"]
        assert set(col.metadata["matching_rows"]) == {12, 13}

    def test_invoice_id_collision_conflicting_customers(self, duplicate_invoices_df):
        """Identifies invoice ID collision where the same invoice ID is billed to different customer IDs."""
        anomalies = detect_duplicate_invoices(duplicate_invoices_df)

        cust_collisions = [
            a for a in anomalies
            if a.method == "collision" and a.metadata.get("conflict_field") == "customer_id"
        ]
        assert len(cust_collisions) >= 1

        col = cust_collisions[0]
        assert col.observed_value == "INV-COL-200"
        assert set(col.metadata["distinct_values"]) == {"CUST-02", "CUST-09"}

    def test_placeholder_invoice_ids_ignored(self):
        """Verifies that empty strings, 'nan', 'none', and 'null' invoice IDs are not flagged as collisions."""
        df_placeholders = pd.DataFrame({
            "invoice_id": ["", "  ", "nan", "None", "null", "INV-VALID-01"],
            "date": ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"],
            "product_id": ["ITEM-1"] * 6,
            "amount": [100.0] * 6,
            "source_row": list(range(1, 7)),
        })

        anomalies = detect_duplicate_invoices(df_placeholders)
        # Placeholder rows must be filtered out, yielding 0 duplicates/collisions
        assert anomalies == []

    def test_empty_dataframe_safety(self):
        """Handles empty DataFrames or DataFrames missing invoice_id column without throwing exceptions."""
        assert detect_duplicate_invoices(pd.DataFrame()) == []
        assert detect_duplicate_invoices(pd.DataFrame({"amount": [10.0, 20.0]})) == []
