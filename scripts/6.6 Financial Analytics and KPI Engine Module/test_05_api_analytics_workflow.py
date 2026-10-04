"""
Module 6.6 — Test Suite 05: FastAPI Analytics REST Endpoints and Workflow.

Validates the transport layer (/api/analytics) for computing deterministic KPIs
over HTTP/REST without LLM involvement. Tests list specifications, single KPI
execution, full KPI pack calculation, trend analysis, forecast endpoints, and error handling.
"""

import os
import json
import pytest
import pandas as pd
from datetime import date


@pytest.fixture
def temp_sales_csv(tmp_path):
    """Create a temporary canonical sales and expenses CSV file."""
    csv_file = tmp_path / "pharmacy_sample_txns.csv"
    data = [
        {"date": "2026-01-05", "amount": 100.0, "cost": 60.0, "txn_type": "sale", "product_id": "PANADOL", "quantity": 10},
        {"date": "2026-01-10", "amount": 200.0, "cost": 120.0, "txn_type": "sale", "product_id": "AUGMENTIN", "quantity": 5},
        {"date": "2026-01-15", "amount": 50.0, "cost": 30.0, "txn_type": "refund", "product_id": "PANADOL", "quantity": 5},
        {"date": "2026-01-20", "amount": 40.0, "cost": 0.0, "txn_type": "expense", "product_id": "RENT", "quantity": 1},
    ]
    df = pd.DataFrame(data)
    df.to_csv(csv_file, index=False)
    return str(csv_file)


@pytest.fixture
def temp_trend_csv(tmp_path):
    """Create a time series CSV spanning multiple months for trend/forecast tests."""
    csv_file = tmp_path / "pharmacy_trend_data.csv"
    dates = [
        "2025-05-15", "2025-06-15", "2025-07-15", "2025-08-15",
        "2025-09-15", "2025-10-15", "2025-11-15", "2025-12-15",
    ]
    data = []
    for i, dt in enumerate(dates):
        data.append({
            "date": dt,
            "amount": 1000.0 + (i * 100.0),
            "cost": 600.0 + (i * 60.0),
            "txn_type": "sale",
            "product_id": "AMOXIL",
            "quantity": 20 + i,
        })
    df = pd.DataFrame(data)
    df.to_csv(csv_file, index=False)
    return str(csv_file)


def test_list_kpis_endpoint(test_client):
    """Test GET /api/analytics/kpis returns registered KPI specifications."""
    response = test_client.get("/api/analytics/kpis")
    assert response.status_code == 200
    data = response.json()
    assert "kpis" in data
    kpi_keys = {item["key"] for item in data["kpis"]}
    assert "total_revenue" in kpi_keys
    assert "total_expenses" in kpi_keys
    assert "net_profit" in kpi_keys
    assert "refund_rate_pct" in kpi_keys


def test_compute_single_kpi_endpoint(test_client, temp_sales_csv):
    """Test POST /api/analytics/kpi/{key} calculates revenue accurately with provenance."""
    payload = {
        "file_path": temp_sales_csv,
        "domain": "pharmacy",
        "mapping": {
            "date": "date",
            "amount": "amount",
            "cost": "cost",
            "txn_type": "txn_type",
            "product_id": "product_id",
            "quantity": "quantity",
        },
        "include_validation": True,
    }
    response = test_client.post("/api/analytics/kpi/total_revenue", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["file_path"] == temp_sales_csv
    assert data["total_rows"] == 4
    assert "validation_report" in data

    kpi = data["kpi"]
    assert kpi["key"] == "total_revenue"
    assert kpi["status"] == "ok"
    assert kpi["value"] == 300.0  # 100 + 200
    assert "provenance" in kpi
    assert "formula" in kpi
    assert len(kpi["provenance"]["source_rows"]) == 2


def test_compute_full_kpi_pack_endpoint(test_client, temp_sales_csv):
    """Test POST /api/analytics/kpis calculates all core KPIs in one deterministic pass."""
    payload = {
        "file_path": temp_sales_csv,
        "domain": "pharmacy",
        "mapping": {
            "date": "date",
            "amount": "amount",
            "cost": "cost",
            "txn_type": "txn_type",
            "product_id": "product_id",
            "quantity": "quantity",
        },
        "include_validation": True,
    }
    response = test_client.post("/api/analytics/kpis", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "kpis" in data
    kpis = data["kpis"]
    assert kpis["total_revenue"]["value"] == 300.0
    assert kpis["total_expenses"]["value"] == 40.0
    assert kpis["total_refunds"]["value"] == 50.0
    assert kpis["net_profit"]["value"] == 210.0  # 300 - 50 - 40
    assert kpis["units_sold"]["value"] == 15.0   # 10 + 5


def test_unknown_kpi_returns_404(test_client, temp_sales_csv):
    """Test POST /api/analytics/kpi/{key} returns 404 for unrecognized KPI keys."""
    payload = {
        "file_path": temp_sales_csv,
        "domain": "pharmacy",
    }
    response = test_client.post("/api/analytics/kpi/non_existent_super_metric", json=payload)
    assert response.status_code == 404
    assert "Unknown KPI 'non_existent_super_metric'" in response.json()["detail"]


def test_missing_file_returns_404(test_client):
    """Test POST /api/analytics/kpi/{key} returns 404 when file does not exist on disk."""
    payload = {
        "file_path": "c:/path/does/not/exist/fake_transactions_file.csv",
        "domain": "pharmacy",
    }
    response = test_client.post("/api/analytics/kpi/total_revenue", json=payload)
    assert response.status_code == 404
    assert "does not exist on disk" in response.json()["detail"]


def test_trend_endpoint(test_client, temp_trend_csv):
    """Test POST /api/analytics/trend delivers historical aggregation series."""
    payload = {
        "file_path": temp_trend_csv,
        "domain": "pharmacy",
        "metric": "revenue",
        "granularity": "monthly",
        "mapping": {
            "date": "date",
            "amount": "amount",
            "cost": "cost",
            "txn_type": "txn_type",
            "product_id": "product_id",
            "quantity": "quantity",
        },
    }
    response = test_client.post("/api/analytics/trend", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["metric"] == "revenue"
    assert data["granularity"] == "monthly"
    assert data["point_count"] == 8
    assert data["total_revenue"] > 8000.0


def test_forecast_endpoint(test_client, temp_trend_csv):
    """Test POST /api/analytics/forecast computes time-series projections with upper/lower bounds."""
    payload = {
        "file_path": temp_trend_csv,
        "domain": "pharmacy",
        "metric": "revenue",
        "granularity": "monthly",
        "horizon": 3,
        "mapping": {
            "date": "date",
            "amount": "amount",
            "cost": "cost",
            "txn_type": "txn_type",
            "product_id": "product_id",
            "quantity": "quantity",
        },
    }
    response = test_client.post("/api/analytics/forecast", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["metric"] == "revenue"
    assert data["horizon"] == 3
    assert "forecast" in data
    fc = data["forecast"]
    assert fc["status"] == "ok"
    points = fc["forecast"]
    assert len(points) == 3
    # Check bounds
    for pt in points:
        assert pt["lower"] <= pt["value"] <= pt["upper"]
