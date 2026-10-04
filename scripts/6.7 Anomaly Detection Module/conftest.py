"""Pytest configuration and shared fixtures for Module 6.7 Anomaly Detection tests."""

import sys
from pathlib import Path
import pytest
import pandas as pd
import numpy as np

# Ensure backend root is on sys.path
backend_path = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from fastapi.testclient import TestClient
from app.main import app
from app.anomaly.models import AnomalyRecord, AnomalyType, Severity


@pytest.fixture
def normal_transactions_df():
    """Constructs a clean baseline dataset of 15 standard, consistent retail sales transactions."""
    np.random.seed(42)
    dates = pd.date_range("2026-03-01", periods=15, freq="D")
    amounts = [100.0 + (i * 2.0) for i in range(15)]  # Smooth 100 to 128
    return pd.DataFrame({
        "invoice_id": [f"INV-10{i:02d}" for i in range(15)],
        "date": dates,
        "product_id": ["PANADOL-500"] * 15,
        "quantity": [2.0] * 15,
        "unit_price": [50.0] * 15,
        "amount": amounts,
        "txn_type": ["sale"] * 15,
        "customer_id": [f"CUST-{i%3:02d}" for i in range(15)],
        "source_row": list(range(2, 17)),
        "source_file": ["ledger_march_2026.csv"] * 15,
    })


@pytest.fixture
def spiked_transactions_df(normal_transactions_df):
    """Augments the normal baseline with a severe transaction spike outlier (amount = 2500.0)."""
    df = normal_transactions_df.copy()
    spike_row = pd.DataFrame([{
        "invoice_id": "INV-SPIKE-99",
        "date": pd.Timestamp("2026-03-16"),
        "product_id": "PANADOL-500",
        "quantity": 50.0,
        "unit_price": 50.0,
        "amount": 2500.0,  # Extreme outlier: mean is ~114, std is ~9, Z > 200
        "txn_type": "sale",
        "customer_id": "CUST-INSTITUTIONAL",
        "source_row": 17,
        "source_file": "ledger_march_2026.csv",
    }])
    return pd.concat([df, spike_row], ignore_index=True)


@pytest.fixture
def duplicate_invoices_df():
    """Dataset containing both exact duplicate transactions and invoice ID collisions."""
    return pd.DataFrame({
        "invoice_id": [
            "INV-DUP-100", "INV-DUP-100",  # Exact duplicate rows
            "INV-COL-200", "INV-COL-200",  # Collision across conflicting dates
            "INV-NORM-300",
        ],
        "date": [
            "2026-03-01", "2026-03-01",
            "2026-03-02", "2026-03-15",    # Conflicting dates for INV-COL-200
            "2026-03-03",
        ],
        "product_id": [
            "AUGMENTIN-625", "AUGMENTIN-625",
            "BRUFEN-400", "BRUFEN-400",
            "PANADOL-500",
        ],
        "quantity": [1.0, 1.0, 2.0, 5.0, 2.0],
        "unit_price": [250.0, 250.0, 80.0, 80.0, 50.0],
        "amount": [250.0, 250.0, 160.0, 400.0, 100.0],
        "txn_type": ["sale"] * 5,
        "customer_id": ["CUST-01", "CUST-01", "CUST-02", "CUST-09", "CUST-03"],
        "source_row": [10, 11, 12, 13, 14],
        "source_file": ["invoices_pos.csv"] * 5,
    })


@pytest.fixture
def abnormal_refunds_df():
    """Dataset with abnormal refund frequency clustered around one specific customer/cashier."""
    return pd.DataFrame({
        "invoice_id": [f"TXN-{i:03d}" for i in range(12)],
        "date": ["2026-03-05"] * 12,
        "customer_id": [
            "CUST-ABNORMAL", "CUST-ABNORMAL", "CUST-ABNORMAL", "CUST-ABNORMAL",  # 4 refunds
            "CUST-PEER-1", "CUST-PEER-2", "CUST-PEER-3", "CUST-PEER-4",
            "CUST-PEER-5", "CUST-PEER-6", "CUST-PEER-7", "CUST-PEER-8",
        ],
        "amount": [
            -500.0, -450.0, -600.0, -550.0,  # Total -2100.0
            -50.0, -40.0, -30.0, -60.0,
            -20.0, -50.0, -30.0, -40.0,
        ],
        "txn_type": ["refund"] * 12,
        "source_row": list(range(1, 13)),
        "source_file": ["refunds_log.csv"] * 12,
    })


@pytest.fixture
def pricing_anomalies_df():
    """Dataset containing zero/negative prices and excessive discount percentages."""
    return pd.DataFrame({
        "invoice_id": ["INV-ZERO", "INV-NEG", "INV-DISC-HIGH", "INV-NORMAL"],
        "date": ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"],
        "product_id": ["FREE-SAMPLE", "RETURN-BUG", "PROMO-ITEM", "REGULAR-ITEM"],
        "unit_price": [0.0, -25.0, 100.0, 100.0],
        "amount": [0.0, -25.0, 30.0, 100.0],
        "discount": [0.0, 0.0, 70.0, 0.0],  # 70 discount on 30 sale = 70% discount rate
        "txn_type": ["sale", "sale", "sale", "sale"],
        "source_row": [101, 102, 103, 104],
        "source_file": ["pos_register.csv"] * 4,
    })


@pytest.fixture
def stock_shrinkage_df():
    """Dataset with severe inventory shrinkage discrepancy."""
    return pd.DataFrame({
        "product_id": ["PANADOL-SHRINK", "AMOXIL-STABLE"],
        "opening_stock_qty": [100.0, 50.0],
        "closing_stock_qty": [40.0, 45.0],    # Stock decrease: Panadol = 60, Amoxil = 5
        "quantity": [10.0, 5.0],              # Recorded sales: Panadol = 10, Amoxil = 5
        "source_row": [201, 202],
        "source_file": ["inventory_audit.csv"] * 2,
    })


@pytest.fixture
def test_client():
    """Provides a Starlette/FastAPI TestClient for testing anomaly detection API routes."""
    with TestClient(app) as client:
        yield client
