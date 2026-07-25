import pytest
import os
import pandas as pd
from app.connectors.csv_excel import CSVConnector
from app.schema.mapper import map_headers
from app.schema.normalize import normalize
from app.schema.validate import validate
from app.schema.pharmacy import PharmacyDomainPack

def test_messy_pharmacy_csv():
    file_path = os.path.join(os.path.dirname(__file__), "..", "data", "samples", "messy_pharmacy.csv")
    conn = CSVConnector(file_path)
    
    # 1. Fetch
    raw_df = conn.fetch()
    
    # Header should be detected correctly skipping the first 3 lines of junk
    # So length should be 7 lines: 7 data lines + 1 empty middle line skipped
    assert len(raw_df) == 7 # the completely empty line will be skipped by skip_blank_lines=True
    
    # 2. Map headers
    domain_pack = PharmacyDomainPack()
    mapping = map_headers(list(raw_df.columns), domain_pack=domain_pack)
    
    # Check if messy headers mapped
    assert "product_id" in mapping.values()
    assert "batch_no" in mapping.values()
    assert "expiry_date" in mapping.values()
    assert "quantity" in mapping.values()
    assert "unit_price" in mapping.values()
    assert "amount" in mapping.values()
    assert "cost" in mapping.values()

    # 3. Normalize
    df = normalize(raw_df, mapping, domain="pharmacy")
    
    # Verify row 1: Panadol 500mg
    row_panadol = df[df["product_id"] == "Panadol 500mg"].iloc[0]
    assert row_panadol["quantity"] == 10.0
    assert row_panadol["unit_price"] == 30.5
    assert row_panadol["amount"] == 305.0
    assert row_panadol["cost"] == 25.0
    assert row_panadol["expiry_date"].month == 10
    assert row_panadol["expiry_date"].year == 2025
    assert row_panadol["expiry_date"].day == 31
    
    # Verify row 2: Augmentin (Urdu digits, PKR, commas)
    row_augmentin = df[df["product_id"] == "Augmentin 625mg"].iloc[0]
    assert row_augmentin["quantity"] == 5.0 # ۵ -> 5
    assert row_augmentin["unit_price"] == 250.0 # PKR 250/- -> 250
    assert row_augmentin["amount"] == 1250.0 # "1,250" -> 1250
    assert row_augmentin["cost"] == 220.0
    assert row_augmentin["expiry_date"].month == 12
    assert row_augmentin["expiry_date"].year == 2026
    
    # Verify row 3: Brufen (Excel date 45000, empty batch)
    row_brufen = df[df["product_id"] == "Brufen 400mg"].iloc[0]
    assert pd.isna(row_brufen["batch_no"]) or str(row_brufen["batch_no"]).strip() == "nan"
    # Note: Currently _clean_date does NOT handle excel serial numbers. This will probably fail or become NaT! We'll need to fix normalize.py.
    
    # Verify row 4: Caldic-C (Negative values)
    row_caldic = df[df["product_id"] == "Caldic-C"].iloc[0]
    assert row_caldic["quantity"] == -10.0
    assert row_caldic["unit_price"] == -15.0
    assert row_caldic["amount"] == -150.0
    
    # Verify row 5: Flagyl (Ragged row, missing total and cost)
    row_flagyl = df[df["product_id"] == "Flagyl 400mg"].iloc[0]
    assert row_flagyl["quantity"] == 15.0
    assert row_flagyl["unit_price"] == 15.0
    assert pd.isna(row_flagyl["amount"])
    
    # 4. Validation
    problems = validate(df, domain="pharmacy")
    assert any("Row is completely empty" in p.message for p in problems) # EmptyRowTest row should trigger this
