"""
Module 6.7 — Test Suite for Statistical Anomaly Detection.

Tests:
1. Z-Score and IQR transaction spike detection.
2. Duplicate invoice detection (exact duplicates and invoice ID collisions).
3. Abnormal refund pattern analysis.
4. Unusual discount detection.
5. Negative/zero pricing detection.
6. Master detect_all_anomalies runner.
7. Explainer layer (deterministic templates and mocked LLM narration).
8. FastAPI endpoints (/api/anomaly/scan and /api/anomaly/explain).
9. KPIEngine integration (anomaly_count, anomaly_breakdown).
"""

from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.anomaly.detectors import (
    detect_abnormal_refund_patterns,
    detect_all_anomalies,
    detect_duplicate_invoices,
    detect_negative_or_zero_prices,
    detect_transaction_spikes,
    detect_unusual_discounts,
)
from app.anomaly.explainer import explain_all, explain_anomaly, generate_template_explanation
from app.anomaly.models import AnomalyRecord, AnomalyScanResult, AnomalyType, Severity
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Duplicate Invoice Detection
# ---------------------------------------------------------------------------


def test_detect_duplicate_invoices_exact_and_collision():
    data = [
        # Normal unique invoices
        {"invoice_id": "INV-101", "date": "2026-01-01", "amount": 100.0, "customer_id": "CUST-1", "product_id": "PRD-A"},
        {"invoice_id": "INV-102", "date": "2026-01-02", "amount": 150.0, "customer_id": "CUST-2", "product_id": "PRD-B"},
        # Exact duplicate of INV-101
        {"invoice_id": "INV-101", "date": "2026-01-01", "amount": 100.0, "customer_id": "CUST-1", "product_id": "PRD-A"},
        # Collision: Same invoice_id INV-103 with different dates and customers
        {"invoice_id": "INV-103", "date": "2026-01-03", "amount": 200.0, "customer_id": "CUST-3", "product_id": "PRD-C"},
        {"invoice_id": "INV-103", "date": "2026-01-10", "amount": 200.0, "customer_id": "CUST-9", "product_id": "PRD-D"},
    ]
    df = pd.DataFrame(data)
    df["source_row"] = df.index + 2

    anomalies = detect_duplicate_invoices(df)
    assert len(anomalies) >= 2

    types = [a.anomaly_type for a in anomalies]
    assert AnomalyType.DUPLICATE_INVOICE in types

    methods = [a.method for a in anomalies]
    assert "exact_duplicate" in methods
    assert "collision" in methods


# ---------------------------------------------------------------------------
# 2. Transaction Spike Detection (Z-Score & IQR)
# ---------------------------------------------------------------------------


def test_detect_transaction_spikes_zscore_and_iqr():
    # Normal distribution centered around 100 with small variance, plus an extreme spike of 10,000
    normal_amounts = [95.0, 98.0, 100.0, 102.0, 105.0, 97.0, 101.0, 99.0, 103.0, 96.0]
    spike_amount = 10_000.0
    amounts = normal_amounts + [spike_amount]

    df = pd.DataFrame({
        "invoice_id": [f"INV-{i}" for i in range(len(amounts))],
        "amount": amounts,
        "txn_type": ["sale"] * len(amounts),
    })
    df["source_row"] = df.index + 2

    anomalies = detect_transaction_spikes(df, z_threshold=3.0, iqr_multiplier=1.5)
    assert len(anomalies) >= 1

    spike_anomaly = next((a for a in anomalies if a.observed_value == spike_amount), None)
    assert spike_anomaly is not None
    assert spike_anomaly.anomaly_type == AnomalyType.TRANSACTION_SPIKE
    assert spike_anomaly.severity == Severity.HIGH
    assert spike_anomaly.statistical_score is not None
    assert spike_anomaly.statistical_score >= 3.0


def test_detect_transaction_spikes_insufficient_data():
    df = pd.DataFrame({"amount": [10.0, 20.0]})
    anomalies = detect_transaction_spikes(df)
    assert anomalies == []


# ---------------------------------------------------------------------------
# 3. Abnormal Refund Pattern Detection
# ---------------------------------------------------------------------------


def test_detect_abnormal_refund_patterns():
    # 5 customers: 4 have 1 small refund, customer CUST-FRAUD has 8 large refunds
    rows = []
    for c in ["CUST-1", "CUST-2", "CUST-3", "CUST-4"]:
        rows.append({"customer_id": c, "amount": -50.0, "txn_type": "refund"})

    for _ in range(8):
        rows.append({"customer_id": "CUST-SUSPICIOUS", "amount": -2500.0, "txn_type": "refund"})

    df = pd.DataFrame(rows)
    df["source_row"] = df.index + 2

    anomalies = detect_abnormal_refund_patterns(df, threshold_sigma=2.0)
    assert len(anomalies) >= 1

    fraud_anom = next((a for a in anomalies if a.metadata.get("entity_id") == "CUST-SUSPICIOUS"), None)
    assert fraud_anom is not None
    assert fraud_anom.anomaly_type == AnomalyType.ABNORMAL_REFUND
    assert fraud_anom.metadata["refund_count"] == 8


# ---------------------------------------------------------------------------
# 4. Unusual Discount & Pricing Detection
# ---------------------------------------------------------------------------


def test_detect_unusual_discounts_and_zero_prices():
    df = pd.DataFrame([
        # Normal row
        {"invoice_id": "INV-1", "amount": 1000.0, "discount": 50.0, "unit_price": 100.0, "txn_type": "sale"},
        # Anomaly 1: Discount exceeds gross amount
        {"invoice_id": "INV-2", "amount": 200.0, "discount": 500.0, "unit_price": 50.0, "txn_type": "sale"},
        # Anomaly 2: Zero or negative price on standard sale
        {"invoice_id": "INV-3", "amount": 0.0, "discount": 0.0, "unit_price": 0.0, "txn_type": "sale"},
    ])
    df["source_row"] = df.index + 2

    disc_anomalies = detect_unusual_discounts(df)
    assert len(disc_anomalies) >= 1
    assert disc_anomalies[0].anomaly_type == AnomalyType.UNUSUAL_DISCOUNT
    assert disc_anomalies[0].metadata["discount_amount"] == 500.0

    price_anomalies = detect_negative_or_zero_prices(df)
    assert len(price_anomalies) >= 1
    assert price_anomalies[0].anomaly_type == AnomalyType.NEGATIVE_OR_ZERO_PRICE
    assert price_anomalies[0].observed_value == 0.0


# ---------------------------------------------------------------------------
# 5. Master detect_all_anomalies Runner
# ---------------------------------------------------------------------------


def test_detect_all_anomalies_full_pipeline():
    df = pd.DataFrame([
        {"invoice_id": "INV-10", "date": "2026-01-01", "amount": 100.0, "discount": 5.0, "unit_price": 10.0, "txn_type": "sale"},
        # Duplicate
        {"invoice_id": "INV-10", "date": "2026-01-01", "amount": 100.0, "discount": 5.0, "unit_price": 10.0, "txn_type": "sale"},
        # Extreme spike
        {"invoice_id": "INV-11", "date": "2026-01-02", "amount": 50000.0, "discount": 0.0, "unit_price": 5000.0, "txn_type": "sale"},
        {"invoice_id": "INV-12", "date": "2026-01-03", "amount": 95.0, "discount": 0.0, "unit_price": 10.0, "txn_type": "sale"},
        {"invoice_id": "INV-13", "date": "2026-01-04", "amount": 105.0, "discount": 0.0, "unit_price": 10.0, "txn_type": "sale"},
    ])
    df["source_row"] = df.index + 2

    result = detect_all_anomalies(df, domain="pharmacy")
    assert isinstance(result, AnomalyScanResult)
    assert result.total_anomalies >= 2
    assert "duplicate_invoice" in result.by_type
    assert "transaction_spike" in result.by_type
    assert len(result.anomalies) == result.total_anomalies


# ---------------------------------------------------------------------------
# 6. Explainer Layer (Deterministic Template & Mock LLM)
# ---------------------------------------------------------------------------


def test_explainer_template_and_llm():
    rec = AnomalyRecord(
        id="anom_test",
        anomaly_type=AnomalyType.TRANSACTION_SPIKE,
        severity=Severity.HIGH,
        metric_name="amount",
        observed_value=45000.0,
        expected_range="100 - 2500 PKR",
        statistical_score=4.5,
        method="z-score & iqr",
        source_row=12,
        metadata={"invoice_id": "INV-999"},
    )

    tmpl_exp = generate_template_explanation(rec)
    assert "INV-999" in tmpl_exp
    assert "45,000.00" in tmpl_exp
    assert "4.5 standard deviations" in tmpl_exp

    # Test with mocked LLM
    with patch("app.core.llm.llm.chat", return_value="Invoice INV-999 is unusually high due to bulk ordering."):
        llm_exp = explain_anomaly(rec, use_llm=True)
        assert llm_exp == "Invoice INV-999 is unusually high due to bulk ordering."
        assert rec.explanation == llm_exp


# ---------------------------------------------------------------------------
# 7. KPIEngine Integration
# ---------------------------------------------------------------------------


def test_kpi_engine_anomaly_metrics():
    from app.analytics.engine import engine
    from app.analytics.filters import KPIFilters

    df = pd.DataFrame([
        {"invoice_id": "INV-1", "date": "2026-01-01", "amount": 100.0, "unit_price": 10.0, "txn_type": "sale"},
        {"invoice_id": "INV-1", "date": "2026-01-01", "amount": 100.0, "unit_price": 10.0, "txn_type": "sale"},
        {"invoice_id": "INV-2", "date": "2026-01-02", "amount": 95.0, "unit_price": 10.0, "txn_type": "sale"},
        {"invoice_id": "INV-3", "date": "2026-01-03", "amount": 105.0, "unit_price": 10.0, "txn_type": "sale"},
        {"invoice_id": "INV-4", "date": "2026-01-04", "amount": 80000.0, "unit_price": 10.0, "txn_type": "sale"},
    ])
    df["source_row"] = df.index + 2

    kpis = engine.compute_all(df, KPIFilters(), domain="pharmacy")
    assert "anomaly_count" in kpis
    assert "anomaly_breakdown" in kpis

    cnt_res = kpis["anomaly_count"]
    assert cnt_res.value is not None
    assert cnt_res.value >= 2.0
    assert cnt_res.unit == "count"

    bk_res = kpis["anomaly_breakdown"]
    assert bk_res.value >= 2.0
    assert len(bk_res.breakdown) >= 2


# ---------------------------------------------------------------------------
# 8. API Endpoint Tests
# ---------------------------------------------------------------------------


def test_api_anomaly_explain(client):
    payload = {
        "anomaly": {
            "id": "anom_api_1",
            "anomaly_type": "duplicate_invoice",
            "severity": "high",
            "metric_name": "invoice_id",
            "observed_value": "INV-777",
            "statistical_score": 2.0,
            "source_row": 5,
            "metadata": {"invoice_id": "INV-777", "duplicate_count": 2, "matching_rows": [5, 6]},
        },
        "use_llm": False,
    }
    res = client.post("/api/anomaly/explain", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "anom_api_1"
    assert "INV-777" in data["explanation"]
    assert "2 times" in data["explanation"]
