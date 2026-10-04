"""Test Suite 03: Abnormal Refund Patterns & Pricing Irregularities.

Module: Module 6.7 — Statistical Anomaly Detection Module
Target File: backend/app/anomaly/detectors.py
Scope:
- Verifies abnormal refund frequency and volume grouped by customer or cashier.
- Verifies unusual discount detection (> 50% discount rate or discount > amount).
- Verifies zero or negative unit price detection on regular sales rows.
- Verifies resilience against datasets with zero refunds or missing pricing columns.
"""

import pytest
import pandas as pd
from app.anomaly.models import AnomalyType, Severity
from app.anomaly.detectors import (
    detect_abnormal_refund_patterns,
    detect_unusual_discounts,
    detect_negative_or_zero_prices,
)


class TestRefundPatternsAndPricingAnomalies:
    """Verifies statistical refund clustering and deterministic pricing irregularity detection."""

    def test_abnormal_refund_frequency_by_customer(self, abnormal_refunds_df):
        """Detects customer entity with statistically abnormal refund frequency."""
        anomalies = detect_abnormal_refund_patterns(abnormal_refunds_df, threshold_sigma=2.5)

        assert len(anomalies) >= 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.ABNORMAL_REFUND
        assert anom.metadata["dimension"] == "customer_id"
        assert anom.metadata["entity_id"] == "CUST-ABNORMAL"
        assert anom.metadata["refund_count"] == 4
        assert anom.statistical_score is not None

    def test_abnormal_refund_total_volume(self, abnormal_refunds_df):
        """Verifies that total refund amount is captured and compared against peer averages."""
        anomalies = detect_abnormal_refund_patterns(abnormal_refunds_df, threshold_sigma=2.5)
        anom = anomalies[0]
        meta = anom.metadata
        assert meta["total_refund_amount"] == 2100.0
        assert "PKR" in anom.expected_range

    def test_zero_refund_dataset_safety(self, normal_transactions_df):
        """Ensures that a sales-only dataset without returns yields 0 refund anomalies."""
        anomalies = detect_abnormal_refund_patterns(normal_transactions_df)
        assert anomalies == []

    def test_unusual_discount_greater_than_amount(self):
        """Flags transaction where discount amount exceeds the gross line amount."""
        df_disc = pd.DataFrame([{
            "invoice_id": "INV-DISC-EXTREME",
            "date": "2026-03-01",
            "product_id": "PROMO-ITEM",
            "amount": 50.0,
            "discount": 120.0,  # Discount > Amount
            "source_row": 50,
            "source_file": "ledger.csv",
        }])

        anomalies = detect_unusual_discounts(df_disc)
        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.UNUSUAL_DISCOUNT
        assert anom.severity == Severity.HIGH
        assert anom.observed_value == 120.0
        assert anom.metadata["discount_amount"] == 120.0
        assert anom.metadata["gross_amount"] == 50.0

    def test_unusual_discount_extreme_rate(self, pricing_anomalies_df):
        """Flags transaction with discount rate exceeding 50% threshold."""
        anomalies = detect_unusual_discounts(pricing_anomalies_df)
        assert len(anomalies) >= 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.UNUSUAL_DISCOUNT
        assert anom.metadata["invoice_id"] == "INV-DISC-HIGH"
        assert anom.metadata["discount_pct"] >= 50.0

    def test_negative_or_zero_price_detection(self, pricing_anomalies_df):
        """Flags regular sales transactions with unit price <= 0.00 PKR."""
        anomalies = detect_negative_or_zero_prices(pricing_anomalies_df)
        assert len(anomalies) == 2

        inv_ids = {a.metadata["invoice_id"] for a in anomalies}
        assert "INV-ZERO" in inv_ids
        assert "INV-NEG" in inv_ids

        for a in anomalies:
            assert a.anomaly_type == AnomalyType.NEGATIVE_OR_ZERO_PRICE
            assert a.severity == Severity.MEDIUM
            assert a.method == "boundary_check"
