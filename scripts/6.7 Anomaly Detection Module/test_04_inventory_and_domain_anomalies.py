"""Test Suite 04: Inventory Shrinkage & Domain-Specific Anomaly Detectors.

Module: Module 6.7 — Statistical Anomaly Detection Module
Target File: backend/app/anomaly/detectors.py
Scope:
- Verifies physical inventory shrinkage / stock movement reconciliation (opening - closing vs sales).
- Verifies shrinkage tolerance threshold (<= 5% variance allowed).
- Verifies e-commerce margin erosion (selling below unit cost).
- Verifies e-commerce refund surges and customer review rating drops (< 3.0 stars).
- Verifies domain-aware routing in detect_all_anomalies (pharmacy vs ecommerce).
"""

import pytest
import pandas as pd
from app.anomaly.models import AnomalyType, Severity
from app.anomaly.detectors import (
    detect_stock_movement_mismatch,
    detect_ecommerce_margin_erosion,
    detect_ecommerce_refund_surges,
    detect_ecommerce_review_rating_drops,
    detect_all_anomalies,
)


class TestInventoryAndDomainAnomalies:
    """Verifies inventory shrinkage detection and domain-specific retail/e-commerce rules."""

    def test_stock_movement_mismatch_shrinkage(self, stock_shrinkage_df):
        """Detects physical stock disappearance exceeding recorded transaction quantities."""
        anomalies = detect_stock_movement_mismatch(stock_shrinkage_df)

        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.STOCK_MOVEMENT_MISMATCH
        assert anom.severity == Severity.HIGH
        assert anom.metadata["product_id"] == "PANADOL-SHRINK"
        # Opening (100) - Closing (40) = 60 units lost; Sold = 10 -> Discrepancy = 50 units
        assert anom.metadata["discrepancy_units"] == 50.0
        assert anom.observed_value == 50.0

    def test_stock_tolerance_allows_normal_variance(self):
        """Allows up to 5% relative variance for unit precision and measurement tolerances."""
        df_normal_stock = pd.DataFrame([{
            "product_id": "STABLE-ITEM",
            "opening_stock_qty": 100.0,
            "closing_stock_qty": 0.0,    # Stock decrease = 100.0
            "quantity": 98.0,            # Sold = 98.0 (discrepancy 2.0 = 2.04% < 5% tolerance)
            "source_row": 1,
            "source_file": "audit.csv",
        }])

        anomalies = detect_stock_movement_mismatch(df_normal_stock)
        assert anomalies == []

    def test_ecommerce_margin_erosion(self):
        """Flags e-commerce transactions where product is sold at a loss below unit cost."""
        df_ecom = pd.DataFrame([{
            "order_id": "ORD-5001",
            "product_name": "Leather Jacket",
            "unit_price": 45.0,
            "cost_per_item": 75.0,  # Sold at $30 loss per unit
            "quantity": 1,
            "source_row": 1,
            "source_file": "orders.csv",
        }])

        anomalies = detect_ecommerce_margin_erosion(df_ecom)
        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.MARGIN_EROSION
        assert anom.severity == Severity.HIGH
        assert anom.metadata["loss_per_unit"] == 30.0
        assert anom.metadata["loss_pct"] == 40.0

    def test_ecommerce_refund_surges(self):
        """Flags products with abnormal refund rate (> 20%) over multiple orders."""
        df_refunds = pd.DataFrame([
            {"product_sku": "SKU-BAD-BATCH", "sale_amount": 100.0, "refund_amount": 50.0},
            {"product_sku": "SKU-BAD-BATCH", "sale_amount": 100.0, "refund_amount": 30.0},
        ])

        anomalies = detect_ecommerce_refund_surges(df_refunds)
        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.REFUND_SURGE
        assert anom.metadata["refund_rate_pct"] == 40.0

    def test_ecommerce_review_rating_drop(self):
        """Flags products with average customer rating below 3.0 stars across multiple reviews."""
        df_reviews = pd.DataFrame([
            {"product_name": "Defective Kettle", "rating": 1.0},
            {"product_name": "Defective Kettle", "rating": 2.0},
            {"product_name": "Quality Toaster", "rating": 5.0},
            {"product_name": "Quality Toaster", "rating": 5.0},
        ])

        anomalies = detect_ecommerce_review_rating_drops(df_reviews)
        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == AnomalyType.REVIEW_RATING_DROP
        assert anom.metadata["product"] == "Defective Kettle"
        assert anom.observed_value == 1.5

    def test_domain_aware_routing(self, stock_shrinkage_df):
        """Confirms that detect_all_anomalies executes domain-specific checks dynamically."""
        res_pharmacy = detect_all_anomalies(stock_shrinkage_df, domain="pharmacy")
        types_pharmacy = {a.anomaly_type for a in res_pharmacy.anomalies}
        assert AnomalyType.STOCK_MOVEMENT_MISMATCH in types_pharmacy

        # E-commerce domain should not trigger stock mismatch on this shape
        res_ecom = detect_all_anomalies(stock_shrinkage_df, domain="ecommerce")
        types_ecom = {a.anomaly_type for a in res_ecom.anomalies}
        assert AnomalyType.STOCK_MOVEMENT_MISMATCH not in types_ecom
