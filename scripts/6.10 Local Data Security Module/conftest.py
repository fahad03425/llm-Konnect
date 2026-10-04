"""Pytest configuration and shared fixtures for Module 6.10 Local Data Security tests."""

import io
import os
import sys
import shutil
import tempfile
from pathlib import Path
import pytest
import pandas as pd

# Ensure backend root is on sys.path
backend_path = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.security.crypto import (
    reset_cached_key,
    get_vault_key,
    KEY_LENGTH,
    MAGIC_HEADER,
    NONCE_LENGTH,
)


@pytest.fixture(autouse=True)
def clean_crypto_cache():
    """Ensure in-memory key cache is cleared before and after each test."""
    reset_cached_key()
    yield
    reset_cached_key()


@pytest.fixture
def test_client():
    """FastAPI TestClient instance connected to LLM-Konnect backend."""
    return TestClient(app)


@pytest.fixture
def temp_security_sandbox(monkeypatch):
    """
    Creates an isolated temporary directory for security tests,
    pointing vault_key_path and storage_dir into a temporary sandbox.
    """
    temp_dir = tempfile.mkdtemp(prefix="konnect_security_test_")
    vault_file = os.path.join(temp_dir, ".vault_key")
    storage_dir = os.path.join(temp_dir, "storage")
    uploads_dir = os.path.join(temp_dir, "uploads")
    os.makedirs(storage_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)

    monkeypatch.setattr(settings, "vault_key_path", vault_file)
    monkeypatch.setattr(settings, "storage_dir", storage_dir)
    monkeypatch.setattr(settings, "encryption_enabled", True)
    monkeypatch.delenv("LLM_KONNECT_SECRET_KEY", raising=False)

    reset_cached_key()

    yield {
        "root": temp_dir,
        "vault_file": vault_file,
        "storage_dir": storage_dir,
        "uploads_dir": uploads_dir,
    }

    reset_cached_key()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_financial_csv_bytes():
    """Constructs raw UTF-8 bytes for a realistic financial ledger with PII/sensitive data."""
    csv_text = (
        "invoice_id,date,customer_name,tax_id,product,quantity,unit_price,total_amount,payment_method\n"
        "INV-9001,2026-03-01,Alpha Healthcare Ltd,TRN-982134,Augmentin 625mg,50,450.00,22500.00,Bank Transfer\n"
        "INV-9002,2026-03-02,City Pharmacy & Care,TRN-441209,Panadol Extra,120,50.00,6000.00,Direct Deposit\n"
        "INV-9003,2026-03-03,Metro Health Clinic,TRN-773821,Lipitor 20mg,30,1200.00,36000.00,Corporate Check\n"
        "INV-9004,2026-03-04,Zubair Diagnostic,TRN-339811,Ventolin Inhaler,15,680.00,10200.00,Cash on Delivery\n"
        "INV-9005,2026-03-05,Federal Hospital Stores,TRN-110294,Cravit 500mg,40,950.00,38000.00,Bank Wire\n"
    )
    return csv_text.encode("utf-8")


@pytest.fixture
def sample_financial_excel_bytes():
    """Generates an in-memory binary Excel (.xlsx) file with financial transactions."""
    df = pd.DataFrame({
        "invoice_id": ["XLS-01", "XLS-02", "XLS-03"],
        "date": ["2026-03-10", "2026-03-11", "2026-03-12"],
        "client": ["BioPharm Dist", "CarePlus Retail", "Apex Clinics"],
        "revenue": [54000.0, 32500.0, 89000.0],
        "profit": [12400.0, 7150.0, 21300.0]
    })
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Transactions")
    return buffer.getvalue()


@pytest.fixture
def sample_chat_session_payload():
    """Generates a realistic RAG conversational turn with confidential business intelligence."""
    return {
        "id": "session-sec-9921",
        "title": "Q1 Margin Analysis Discussion",
        "domain": "pharmacy",
        "created_at": "2026-03-15T10:00:00Z",
        "updated_at": "2026-03-15T10:05:00Z",
        "messages": [
            {
                "role": "user",
                "content": "What was the total gross profit for Augmentin in March 2026?",
                "timestamp": "2026-03-15T10:00:00Z"
            },
            {
                "role": "assistant",
                "content": "Based on verified ledger INV-9001, total gross profit for Augmentin 625mg was PKR 7,500 with a 33.3% margin.",
                "timestamp": "2026-03-15T10:00:05Z",
                "sources": ["ledger_march_2026.csv"]
            }
        ]
    }
