import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.connectors.sql import SQLConnector
from app.ingestion.registry import file_registry

@pytest.fixture
def temp_pharmacy_db(tmp_path):
    db_file = str(tmp_path / "MockTestPOS.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # Create sales table
    cursor.execute("""
        CREATE TABLE transactions (
            bill_no TEXT,
            date TEXT,
            patient_name TEXT,
            product_name TEXT,
            quantity INTEGER,
            unit_price REAL,
            total_amount REAL
        )
    """)
    cursor.executemany("""
        INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        ("B101", "2026-08-01", "Fahad", "Panadol 500mg", 2, 50.0, 100.0),
        ("B102", "2026-08-02", "Usman", "Brufen 400mg", 1, 80.0, 80.0),
        ("B103", "2026-08-03", "Aisha", "Augmentin 625mg", 1, 350.0, 350.0),
    ])

    # Create products inventory table
    cursor.execute("""
        CREATE TABLE products (
            product_code TEXT,
            product_name TEXT,
            generic_salt TEXT,
            stock_quantity INTEGER,
            expiry_date TEXT
        )
    """)
    cursor.executemany("""
        INSERT INTO products VALUES (?, ?, ?, ?, ?)
    """, [
        ("P001", "Panadol 500mg", "Paracetamol", 150, "2027-12-31"),
        ("P002", "Brufen 400mg", "Ibuprofen", 80, "2027-06-30"),
    ])

    # Create customers table
    cursor.execute("""
        CREATE TABLE customers (
            customer_id TEXT,
            customer_name TEXT,
            phone TEXT
        )
    """)
    cursor.executemany("""
        INSERT INTO customers VALUES (?, ?, ?)
    """, [
        ("C01", "Fahad", "0330-1234567"),
        ("C02", "Usman", "0331-7654321"),
    ])

    conn.commit()
    conn.close()
    return db_file

def test_sql_connector_discovery(temp_pharmacy_db):
    connector = SQLConnector(connection_string=f"sqlite:///{temp_pharmacy_db}", db_type="sqlite")
    assert connector.extract_db_name() == "MockTestPOS"
    
    tables = connector.list_tables()
    assert "transactions" in tables
    assert "products" in tables
    assert "customers" in tables

    info = connector.inspect_database()
    assert info["database_name"] == "MockTestPOS"
    assert info["total_tables"] == 3
    
    tables_map = {t["table_name"]: t for t in info["tables"]}
    assert tables_map["transactions"]["row_count"] == 3
    assert tables_map["products"]["row_count"] == 2
    assert tables_map["customers"]["row_count"] == 2

def test_api_sql_discover_and_ingest(temp_pharmacy_db):
    client = TestClient(app)
    
    # 1. Discover endpoint
    res = client.post("/api/sources/sql/discover", json={
        "connection_string": f"sqlite:///{temp_pharmacy_db}",
        "db_type": "sqlite",
        "domain": "pharmacy"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["database_name"] == "MockTestPOS"
    assert data["total_tables"] == 3
    
    # 2. Ingest database endpoint (all tables in 1 step)
    ingest_res = client.post("/api/kb/ingest-database", json={
        "connection_string": f"sqlite:///{temp_pharmacy_db}",
        "db_type": "sqlite",
        "domain": "pharmacy",
        "strategy": "row"
    })
    assert ingest_res.status_code == 200
    ingest_data = ingest_res.json()
    assert ingest_data["success"] is True
    assert ingest_data["database_name"] == "MockTestPOS"
    assert ingest_data["total_tables"] == 3
    assert ingest_data["total_rows"] == 7
    assert ingest_data["total_chunks"] >= 7
    
    # Verify records in file registry
    registered_files = file_registry.list_files(include_all=True)
    db_records = [f for f in registered_files if f.group_name == "MockTestPOS"]
    assert len(db_records) == 3
    
    # 3. Delete database group endpoint
    del_res = client.delete("/api/kb/database/MockTestPOS")
    assert del_res.status_code == 200
    assert del_res.json()["deleted_tables"] == 3
