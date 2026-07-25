import pytest
import pandas as pd
from app.schema.mapper import map_headers
from app.schema.normalize import normalize
from app.schema.validate import validate

def test_mapper():
    raw_headers = ["product name", "batch no", "expiry", "qty", "price", "total", "mrp", "tp", "میعاد"]
    mapping = map_headers(raw_headers, domain_pack=None) # fallback
    assert "qty" not in mapping
    
    from app.schema.pharmacy import PharmacyDomainPack
    mapping = map_headers(raw_headers, domain_pack=PharmacyDomainPack())
    assert mapping["batch no"] == "batch_no"
    assert mapping["mrp"] == "mrp"
    assert mapping["tp"] == "cost"
    assert mapping["میعاد"] == "expiry_date"

def test_normalize():
    raw_data = pd.DataFrame([
        {"product name": "Panadol", "batch no": "B1", "expiry": "05/25", "qty": "10", "price": "Rs 30.00", "total": "300"},
        {"product name": "Brufen", "batch no": "B2", "expiry": "12-2024", "qty": "۲", "price": "40.5", "total": "81"}
    ])
    
    from app.schema.pharmacy import PharmacyDomainPack
    mapping = map_headers(list(raw_data.columns), domain_pack=PharmacyDomainPack())
    
    df = normalize(raw_data, mapping, domain="pharmacy")
    
    # Check quantities
    assert df.loc[0, "quantity"] == 10.0
    assert df.loc[1, "quantity"] == 2.0  # Urdu digit conversion
    
    # Check money
    assert df.loc[0, "unit_price"] == 30.0
    
    # Check dates (mm/yy -> end of month)
    assert df.loc[0, "expiry_date"].month == 5
    assert df.loc[0, "expiry_date"].year == 2025
    assert df.loc[0, "expiry_date"].day == 31
    
def test_validation():
    # MRP overcharge
    df = pd.DataFrame([
        {"product_id": "Panadol", "unit_price": 40.0, "mrp": 35.0, "cost": 25.0, "quantity": 1, "date": "2024-01-01", "amount": 40.0},
        {"product_id": "Augmentin", "unit_price": 250.0, "mrp": 250.0, "cost": 260.0, "quantity": 1, "date": "2024-01-01", "amount": 250.0},
        {"product_id": "Expired", "expiry_date": pd.Timestamp("2020-01-01"), "quantity": 10}
    ])
    
    problems = validate(df, domain="pharmacy")
    
    overcharge = [p for p in problems if "Sale price exceeds MRP" in p.message]
    assert len(overcharge) == 1
    
    below_cost = [p for p in problems if "MRP is less than cost" in p.message]
    assert len(below_cost) == 1
    
    expired = [p for p in problems if "Stock is expired" in p.message]
    assert len(expired) == 1
