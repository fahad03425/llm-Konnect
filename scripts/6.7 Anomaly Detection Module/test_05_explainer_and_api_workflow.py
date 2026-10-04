"""Test Suite 05: LLM Plain-Language Explainer & REST API Workflow.

Module: Module 6.7 — Statistical Anomaly Detection Module
Target Files:
- backend/app/anomaly/explainer.py
- backend/app/api/anomaly.py
- backend/app/analytics/kpi.py (anomaly_count & anomaly_breakdown)
Scope:
- Verifies architectural contract: LLM only explains pre-flagged items, never does arithmetic.
- Verifies deterministic rule-based template explanation generator (100% offline, zero-latency).
- Verifies batch explanation attachment (explain_all).
- Verifies FastAPI REST endpoints: POST /api/anomaly/scan and POST /api/anomaly/explain.
- Verifies KPIEngine integration for anomaly_count and anomaly_breakdown.
"""

import pytest
import pandas as pd
from app.anomaly.models import AnomalyRecord, AnomalyType, Severity
from app.anomaly.explainer import (
    generate_template_explanation,
    explain_anomaly,
    explain_all,
)
from app.analytics.engine import KPIEngine
from app.analytics.filters import KPIFilters


@pytest.fixture
def temp_anomaly_csv(tmp_path):
    """Creates a temporary CSV file with duplicate and spiked records for API testing."""
    csv_file = tmp_path / "pharmacy_anomalies_sample.csv"
    data = [
        {"invoice_id": "INV-100", "date": "2026-03-01", "amount": 100.0, "product_id": "PANADOL", "unit_price": 50.0, "quantity": 2},
        {"invoice_id": "INV-100", "date": "2026-03-01", "amount": 100.0, "product_id": "PANADOL", "unit_price": 50.0, "quantity": 2}, # Duplicate
        {"invoice_id": "INV-101", "date": "2026-03-02", "amount": 105.0, "product_id": "PANADOL", "unit_price": 50.0, "quantity": 2},
        {"invoice_id": "INV-102", "date": "2026-03-03", "amount": 95.0, "product_id": "PANADOL", "unit_price": 50.0, "quantity": 2},
        {"invoice_id": "INV-SPIKE", "date": "2026-03-04", "amount": 3500.0, "product_id": "PANADOL", "unit_price": 50.0, "quantity": 70}, # Spike
    ]
    pd.DataFrame(data).to_csv(csv_file, index=False)
    return str(csv_file)


class TestExplainerAndApiWorkflow:
    """Verifies plain-language explanation generation and REST API contracts."""

    def test_template_explanation_generation(self):
        """Generates deterministic, clear explanations referencing exact invoice numbers and rows."""
        dup_record = AnomalyRecord(
            id="anom_test_1",
            anomaly_type=AnomalyType.DUPLICATE_INVOICE,
            severity=Severity.HIGH,
            metric_name="invoice_id",
            observed_value="INV-100",
            expected_range="1 unique record",
            statistical_score=2.0,
            method="exact_duplicate",
            source_row=10,
            metadata={"invoice_id": "INV-100", "duplicate_count": 2, "matching_rows": [10, 11], "amount": 250.0},
        )

        explanation = generate_template_explanation(dup_record)
        assert "INV-100" in explanation
        assert "2 times identically" in explanation
        assert "rows [10, 11]" in explanation
        assert "double-billed" in explanation

    def test_spike_template_explanation(self):
        """Generates clear guidance for transaction spikes without hallucinating figures."""
        spike_record = AnomalyRecord(
            id="anom_test_2",
            anomaly_type=AnomalyType.TRANSACTION_SPIKE,
            severity=Severity.HIGH,
            metric_name="amount",
            observed_value=2500.0,
            expected_range="IQR normal: 80.00 - 150.00",
            statistical_score=5.2,
            method="z-score & iqr",
            source_row=17,
            metadata={"invoice_id": "INV-SPIKE-99"},
        )

        explanation = generate_template_explanation(spike_record)
        assert "INV-SPIKE-99" in explanation
        assert "2,500.00" in explanation
        assert "5.2 standard deviations" in explanation
        assert "bulk/institutional purchase" in explanation

    def test_explain_all_attaches_to_records(self):
        """Attaches explanations in-place to pre-flagged anomaly records."""
        records = [
            AnomalyRecord(
                id="rec_1",
                anomaly_type=AnomalyType.NEGATIVE_OR_ZERO_PRICE,
                severity=Severity.MEDIUM,
                metric_name="unit_price",
                observed_value=0.0,
                expected_range="> 0.00 PKR",
                source_row=5,
                metadata={"invoice_id": "INV-FREE"},
            )
        ]
        assert records[0].explanation is None
        explain_all(records, max_items=5, use_llm=False)
        assert records[0].explanation is not None
        assert "unit_price" in records[0].explanation

    def test_api_scan_endpoint(self, test_client, temp_anomaly_csv):
        """Tests POST /api/anomaly/scan to execute full statistical detection pipeline over HTTP."""
        payload = {
            "file_path": temp_anomaly_csv,
            "domain": "pharmacy",
            "include_explanations": True,
            "use_llm_explanations": False,
        }
        response = test_client.post("/api/anomaly/scan", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert "total_anomalies" in data
        assert data["total_anomalies"] >= 2  # 1 duplicate + 1 spike
        assert "by_type" in data
        assert "by_severity" in data
        assert len(data["anomalies"]) >= 2

        # Check that top anomaly has explanation populated
        assert data["anomalies"][0]["explanation"] is not None

    def test_api_explain_endpoint(self, test_client):
        """Tests POST /api/anomaly/explain to generate plain-language explanation for a single record."""
        payload = {
            "anomaly": {
                "id": "anom_api_test",
                "anomaly_type": "transaction_spike",
                "severity": "high",
                "metric_name": "amount",
                "observed_value": 5000.0,
                "expected_range": "Normal: 100 - 200",
                "statistical_score": 8.1,
                "method": "z-score",
                "source_row": 42,
                "metadata": {"invoice_id": "INV-BIG-SPEND"},
            },
            "use_llm": False,
        }
        response = test_client.post("/api/anomaly/explain", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "anom_api_test"
        assert "explanation" in data
        assert "INV-BIG-SPEND" in data["explanation"]

    def test_kpi_engine_anomaly_integration(self, spiked_transactions_df):
        """Verifies that anomaly_count and anomaly_breakdown KPIs compute via KPIEngine."""
        engine = KPIEngine()

        # Compute anomaly_count
        res_count = engine.compute("anomaly_count", spiked_transactions_df, domain="pharmacy")
        assert res_count.status == "ok"
        assert res_count.value >= 1.0
        assert res_count.unit == "count"

        # Compute anomaly_breakdown
        res_breakdown = engine.compute("anomaly_breakdown", spiked_transactions_df, domain="pharmacy")
        assert res_breakdown.status == "ok"
        assert res_breakdown.breakdown is not None
        assert len(res_breakdown.breakdown) >= 1
        item = res_breakdown.breakdown[0]
        assert "type" in item
        assert "severity" in item
        assert "explanation" in item
