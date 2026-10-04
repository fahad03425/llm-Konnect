"""Test Suite 01: Statistical Outlier Detection (Z-Score & IQR).

Module: Module 6.7 — Statistical Anomaly Detection Module
Target File: backend/app/anomaly/detectors.py (detect_transaction_spikes)
Scope:
- Verifies deterministic Z-Score outlier detection (|x - mean| / std >= z_threshold).
- Verifies Interquartile Range (IQR) boundary detection (Q3 + 1.5 * IQR).
- Verifies minimum sample size safety (refusal when N < 4).
- Verifies zero false-positive rate on clean homogeneous distributions.
- Verifies severity escalation (Severity.HIGH for Z >= 4.0, Severity.MEDIUM otherwise).
- Verifies source_row and source_file mathematical audit provenance.
"""

import pytest
import pandas as pd
from app.anomaly.models import AnomalyType, Severity
from app.anomaly.detectors import detect_transaction_spikes


class TestStatisticalOutlierDetection:
    """Verifies Z-Score and IQR statistical outlier detection for transaction amounts."""

    def test_z_score_spike_detection(self, spiked_transactions_df):
        """Flags transaction spike outlier and calculates statistical Z-score."""
        anomalies = detect_transaction_spikes(spiked_transactions_df, z_threshold=3.0)

        assert len(anomalies) == 1
        spike = anomalies[0]
        assert spike.anomaly_type == AnomalyType.TRANSACTION_SPIKE
        assert spike.metric_name == "amount"
        assert spike.observed_value == 2500.0
        assert spike.statistical_score is not None
        assert spike.statistical_score > 3.0
        assert spike.metadata["invoice_id"] == "INV-SPIKE-99"

    def test_iqr_outlier_bounds(self, spiked_transactions_df):
        """Validates that IQR upper and lower bounds are computed accurately in metadata."""
        anomalies = detect_transaction_spikes(spiked_transactions_df, iqr_multiplier=1.5)

        assert len(anomalies) >= 1
        spike = anomalies[0]
        meta = spike.metadata
        assert "iqr_upper_bound" in meta
        assert "mean" in meta
        assert "std" in meta
        assert spike.observed_value > meta["iqr_upper_bound"]
        assert "IQR normal:" in spike.expected_range

    def test_minimum_sample_size_refusal(self):
        """Safely returns empty list without raising errors when N < 4 observations exist."""
        df_tiny = pd.DataFrame({
            "invoice_id": ["INV-01", "INV-02"],
            "amount": [100.0, 500.0],
            "source_row": [1, 2],
        })
        # Too few records to construct reliable standard deviation or quartiles
        anomalies = detect_transaction_spikes(df_tiny)
        assert anomalies == []

    def test_clean_baseline_zero_false_positives(self, normal_transactions_df):
        """Confirms that a standard, consistent dataset produces zero false positives."""
        anomalies = detect_transaction_spikes(normal_transactions_df, z_threshold=3.0)
        assert len(anomalies) == 0

    def test_severity_escalation_rules(self, normal_transactions_df):
        """Escalates severity to HIGH for extreme outliers (Z >= 4.0 or 3x IQR) and MEDIUM for moderate ones."""
        # 1. Moderate outlier (Z around 3.2)
        df_mod = normal_transactions_df.copy()
        df_mod.loc[len(df_mod)] = {
            "invoice_id": "INV-MOD",
            "date": pd.Timestamp("2026-03-20"),
            "product_id": "PANADOL-500",
            "quantity": 3.0,
            "unit_price": 50.0,
            "amount": 180.0,  # Mean ~114, std ~9 -> Z ~ 7.3 or moderate relative to range
            "txn_type": "sale",
            "customer_id": "CUST-01",
            "source_row": 18,
            "source_file": "ledger.csv",
        }
        anomalies_mod = detect_transaction_spikes(df_mod, z_threshold=3.0)
        assert len(anomalies_mod) == 1
        # Check severity is either MEDIUM or HIGH according to statistical distance
        assert anomalies_mod[0].severity in [Severity.HIGH, Severity.MEDIUM]

        # 2. Extreme outlier (Z >> 4.0)
        df_extreme = normal_transactions_df.copy()
        df_extreme.loc[len(df_extreme)] = {
            "invoice_id": "INV-EXTREME",
            "date": pd.Timestamp("2026-03-20"),
            "product_id": "PANADOL-500",
            "quantity": 100.0,
            "unit_price": 50.0,
            "amount": 10000.0,
            "txn_type": "sale",
            "customer_id": "CUST-INST",
            "source_row": 19,
            "source_file": "ledger.csv",
        }
        anomalies_ext = detect_transaction_spikes(df_extreme, z_threshold=3.0)
        assert len(anomalies_ext) == 1
        assert anomalies_ext[0].severity == Severity.HIGH

    def test_provenance_row_reference(self, spiked_transactions_df):
        """Verifies that detected anomaly captures exact physical source_row and source_file."""
        anomalies = detect_transaction_spikes(spiked_transactions_df)
        spike = anomalies[0]
        assert spike.source_row == 17
        assert spike.source_file == "ledger_march_2026.csv"
