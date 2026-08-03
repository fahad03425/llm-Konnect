import pytest
import datetime
import json
import os
import pandas as pd

from app.schema.domain import Problem, ValidationReport, CleaningSummary
from app.schema.canonical import validate_core_dataframe
from app.schema.pharmacy import PharmacyDomainPack
from app.schema.validate import validate, clean
from app.connectors.csv_excel import CSVConnector
from app.schema.normalize import apply_mapping
from app.schema.mapper import map_headers

_SAMPLE_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "sample_pharmacy.csv")
_MESSY_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "messy_pharmacy.csv")


def test_core_empty_row():
    df = pd.DataFrame([
        {"date": "2026-01-01", "amount": 100.0, "source_row": 10},
        {"date": "", "amount": None, "source_row": 11},
    ])
    problems = validate_core_dataframe(df)
    codes = [p.code for p in problems]
    assert "EMPTY_ROW" in codes
    empty_p = next(p for p in problems if p.code == "EMPTY_ROW")
    assert 11 in empty_p.row_refs


def test_core_missing_required_field():
    # Transaction missing amount
    df_txn = pd.DataFrame([
        {"date": "2026-01-01", "amount": None, "source_row": 5}
    ])
    report_txn = validate(df_txn, domain="pharmacy", table_kind="transactions")
    assert any(p.code == "MISSING_REQUIRED_FIELD" for p in report_txn.problems)
    assert report_txn.verdict == "not_usable"

    # Inventory passing
    df_inv = pd.DataFrame([
        {"product_id": "Paracetamol 500mg", "quantity": 10.0, "source_row": 1}
    ])
    report_inv = validate(df_inv, domain="pharmacy", table_kind="inventory")
    assert not any(p.code == "MISSING_REQUIRED_FIELD" for p in report_inv.problems)


def test_core_duplicate_row():
    df = pd.DataFrame([
        {"date": "2026-01-01", "amount": 100.0, "description": "Panadol", "source_row": 1},
        {"date": "2026-01-01", "amount": 100.0, "description": "Panadol", "source_row": 2},
    ])
    report = validate(df, domain="pharmacy")
    assert any(p.code == "DUPLICATE_ROW" for p in report.problems)
    assert report.verdict == "usable_with_warnings"


def test_core_line_total_mismatch():
    df = pd.DataFrame([
        {"date": "2026-01-01", "unit_price": 10.0, "quantity": 5.0, "amount": 100.0, "source_row": 3}
    ])
    report = validate(df, domain="pharmacy")
    assert any(p.code == "LINE_TOTAL_MISMATCH" for p in report.problems)


def test_core_negative_quantity():
    df = pd.DataFrame([
        {"date": "2026-01-01", "amount": 50.0, "quantity": -5.0, "source_row": 4}
    ])
    report = validate(df, domain="pharmacy")
    assert any(p.code == "NEGATIVE_QUANTITY" for p in report.problems)


def test_core_invalid_date_range():
    df = pd.DataFrame([
        {"date": "2099-12-31", "amount": 50.0, "source_row": 7}
    ])
    report = validate(df, domain="pharmacy")
    assert any(p.code == "INVALID_DATE_RANGE" for p in report.problems)


def test_pharmacy_below_cost():
    df = pd.DataFrame([
        {"date": "2026-01-01", "amount": 50.0, "unit_price": 10.0, "mrp": 8.0, "cost": 12.0, "source_row": 1}
    ])
    pack = PharmacyDomainPack()
    problems = pack.validate_dataframe(df)
    codes = [p.code for p in problems]
    assert "BELOW_COST" in codes


def test_pharmacy_mrp_overcharge():
    df = pd.DataFrame([
        {"date": "2026-01-01", "amount": 50.0, "unit_price": 15.0, "mrp": 10.0, "cost": 8.0, "source_row": 1}
    ])
    pack = PharmacyDomainPack()
    problems = pack.validate_dataframe(df)
    codes = [p.code for p in problems]
    assert "MRP_OVERCHARGE" in codes


def test_pharmacy_expired_stock():
    past_date = pd.Timestamp("2020-01-01")
    df = pd.DataFrame([
        {"product_id": "Augmentin", "quantity": 10.0, "expiry_date": past_date, "source_row": 1}
    ])
    pack = PharmacyDomainPack()
    problems = pack.validate_dataframe(df)
    codes = [p.code for p in problems]
    assert "EXPIRED_STOCK" in codes


def test_pharmacy_missing_expiry():
    df = pd.DataFrame([
        {"product_id": "Brufen", "quantity": 20.0, "batch_no": "B123", "expiry_date": None, "source_row": 2}
    ])
    pack = PharmacyDomainPack()
    problems = pack.validate_dataframe(df)
    codes = [p.code for p in problems]
    assert "MISSING_EXPIRY" in codes


def test_pharmacy_missing_mrp_and_unregistered_hint():
    df = pd.DataFrame([
        {"product_id": "Disprin", "quantity": 5.0, "source_row": 3}
    ])
    pack = PharmacyDomainPack()
    problems = pack.validate_dataframe(df)
    codes = [p.code for p in problems]
    assert "MISSING_MRP" in codes
    assert "UNREGISTERED_HINT" in codes


def test_report_json_serializability():
    df = pd.DataFrame([
        {"date": "2026-01-01", "amount": 100.0, "source_row": 1}
    ])
    report = validate(df, domain="pharmacy")
    json_str = report.to_json()
    parsed = json.loads(json_str)
    assert parsed["verdict"] in ("usable", "usable_with_warnings")
    assert "total_rows" in parsed
    assert "null_counts" in parsed


def test_opt_in_clean():
    df = pd.DataFrame([
        {"product_id": " Panadol ", "quantity": 10.0, "amount": 100.0, "source_row": 1},
        {"product_id": None, "quantity": None, "amount": None, "source_row": 2},
        {"product_id": " Panadol ", "quantity": 10.0, "amount": 100.0, "source_row": 3},
    ])
    cleaned_df, summary = clean(df, options={"drop_empty": True, "trim_whitespace": True, "drop_duplicates": True})
    
    assert summary.original_rows == 3
    assert summary.cleaned_rows == 1
    assert summary.empty_rows_dropped == 1
    assert summary.duplicate_rows_dropped == 1
    assert summary.whitespace_trimmed_cells >= 1
    assert cleaned_df.iloc[0]["product_id"] == "Panadol"
    # Money/quantity values unchanged
    assert cleaned_df.iloc[0]["amount"] == 100.0


def test_end_to_end_sample_pharmacy_csv():
    if not os.path.exists(_SAMPLE_CSV):
        pytest.skip(f"Sample file not found at {_SAMPLE_CSV}")
        
    connector = CSVConnector(_SAMPLE_CSV)
    raw_df = connector.fetch()
    mapping = map_headers(list(raw_df.columns), PharmacyDomainPack())
    canonical_df = apply_mapping(raw_df, mapping, domain="pharmacy")
    
    report = validate(canonical_df, domain="pharmacy")
    assert isinstance(report, ValidationReport)
    assert report.total_rows > 0
    assert report.is_usable
