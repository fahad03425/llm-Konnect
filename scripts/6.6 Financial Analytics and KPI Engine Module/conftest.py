"""Pytest configuration and shared fixtures for Module 6.6 test suites."""

import sys
from pathlib import Path
import pytest
import pandas as pd

# Ensure backend root is on sys.path
backend_path = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from app.analytics.engine import KPIEngine, engine
from app.analytics.filters import KPIFilters
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def clean_engine():
    """Provides a fresh, isolated KPIEngine instance with core specifications."""
    return KPIEngine()


@pytest.fixture
def global_engine():
    """Provides the singleton application KPIEngine."""
    return engine


@pytest.fixture
def sample_financial_df():
    """
    Constructs a rich canonical financial dataset with sales, expenses, and refunds.
    Includes cost prices, quantities, categories, and physical source_row tracking.
    """
    return pd.DataFrame({
        "date": [
            pd.Timestamp("2026-03-01"),
            pd.Timestamp("2026-03-02"),
            pd.Timestamp("2026-03-03"),
            pd.Timestamp("2026-03-04"),
            pd.Timestamp("2026-03-05"),
            pd.Timestamp("2026-03-06"),
            pd.Timestamp("2026-03-07"),
            pd.Timestamp("2026-03-08"),
        ],
        "invoice_id": [
            "INV-1001", "INV-1002", "INV-1003", "INV-1004",
            "PUR-2001", "PUR-2002", "REF-3001", "INV-1005"
        ],
        "txn_type": [
            "sale", "sale", "sale", "sale",
            "expense", "expense", "refund", "sale"
        ],
        "product_id": [
            "Panadol 500mg", "Brufen 400mg", "Amoxil 250mg", "Panadol 500mg",
            "Store Utilities", "Packaging Boxes", "Panadol 500mg", "Brufen 400mg"
        ],
        "category": [
            "Analgesics", "Anti-inflammatory", "Antibiotics", "Analgesics",
            "Overhead", "Supplies", "Analgesics", "Anti-inflammatory"
        ],
        "supplier_id": [
            "GSK Pakistan", "Abbott Labs", "Pfizer", "GSK Pakistan",
            "K-Electric", "Packages Ltd", "GSK Pakistan", "Abbott Labs"
        ],
        "customer_id": [
            "CUST-01", "CUST-02", "CUST-01", "CUST-03",
            None, None, "CUST-01", "CUST-04"
        ],
        "quantity": [
            10.0, 5.0, 4.0, 20.0,
            1.0, 100.0, 2.0, 10.0
        ],
        "unit_price": [
            50.0, 80.0, 125.0, 50.0,
            500.0, 5.0, 50.0, 80.0
        ],
        "cost": [
            35.0, 55.0, 90.0, 35.0,
            500.0, 5.0, 35.0, 55.0
        ],
        "amount": [
            500.0, 400.0, 500.0, 1000.0,
            500.0, 500.0, -100.0, 800.0
        ],
        "source_connector": ["CSVConnector"] * 8,
        "source_file": ["C:/data/financial_ledger_2026.csv"] * 8,
        "source_row": [2, 3, 4, 5, 6, 7, 8, 9]
    })


@pytest.fixture
def test_client():
    """Provides a Starlette/FastAPI TestClient for analytics endpoint tests."""
    with TestClient(app) as client:
        yield client
