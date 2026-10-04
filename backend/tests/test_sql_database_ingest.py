import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.connectors.sql import SQLConnector
from app.ingestion.registry import file_registry
from app.connectors.sql import resolve_sql_connection
from app.ingestion.registry import FileRegistry
from app.api.kb import IngestDatabaseRequest


def test_database_ingest_defaults_to_row_strategy():
    request = IngestDatabaseRequest(connection_string="sqlite:///:memory:")

    assert request.strategy == "row"

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


@pytest.mark.parametrize(("uri", "db_type", "scheme", "database"), [
    ("postgres://owner:secret@db.example:5432/pharmacy", "sqlite", "postgresql+psycopg", "pharmacy"),
    ("postgresql://owner:secret@db.example:5432/pharmacy", "postgresql", "postgresql+psycopg", "pharmacy"),
    ("mysql://owner:secret@db.example:3306/pharmacy", "sqlite", "mysql+pymysql", "pharmacy"),
    ("mariadb://owner:secret@db.example:3306/pharmacy", "mysql", "mysql+pymysql", "pharmacy"),
])
def test_sqlalchemy_database_urls_resolve_to_configured_drivers(uri, db_type, scheme, database):
    resolved, resolved_type, extracted = resolve_sql_connection(uri, db_type)
    assert resolved.startswith(scheme + "://")
    assert resolved_type in ("postgresql", "mysql")
    assert extracted == database
    from sqlalchemy import create_engine
    dialect = create_engine(resolved).dialect
    assert dialect.name in ("postgresql", "mysql")
    assert dialect.driver == scheme.split("+")[1]


def test_sql_connector_quotes_discovered_table_names_and_reads_all_rows(tmp_path):
    db_file = str(tmp_path / "quoted-tables.sqlite")
    with sqlite3.connect(db_file) as conn:
        conn.execute('CREATE TABLE "sales register" ("bill_no" TEXT, "amount" REAL)')
        conn.executemany(
            'INSERT INTO "sales register" VALUES (?, ?)',
            [("B-1", 10.0), ("B-2", 20.0), ("B-3", 30.0)],
        )
    connector = SQLConnector(db_file)
    assert connector.list_tables() == ["sales register"]
    records = connector.fetch("sales register")
    assert len(records) == connector.total_rows("sales register") == 3
    assert connector.preview(n=2, table_or_query="sales register").shape[0] == 2
    assert connector.get_table_primary_key("sales register") == []
    with pytest.raises(ValueError, match="not found"):
        connector.fetch('sales register; DROP TABLE "sales register"')
    assert connector.total_rows("sales register") == 3


def test_sql_connector_applies_zero_watermark_value(tmp_path):
    db_file = str(tmp_path / "watermark.sqlite")
    with sqlite3.connect(db_file) as conn:
        conn.execute('CREATE TABLE "sync rows" (id INTEGER NOT NULL, value TEXT)')
        conn.executemany('INSERT INTO "sync rows" VALUES (?, ?)', [(0, "old"), (1, "new")])
    connector = SQLConnector(db_file)
    changed = connector.fetch("sync rows", watermark_column="id", watermark_value=0)
    assert changed["id"].tolist() == [1]


def test_saved_database_credentials_are_encrypted_and_not_returned_by_list_api(tmp_path, monkeypatch):
    registry = FileRegistry(str(tmp_path / "registry.sqlite3"))
    secret_url = "postgresql://owner:very-secret-password@db.example:5432/pharmacy"
    registry.save_db_connection(
        database_name="private_pharmacy", connection_string=secret_url, db_type="postgresql"
    )

    with sqlite3.connect(registry.db_path) as conn:
        stored = conn.execute(
            "SELECT connection_string FROM db_connections WHERE database_name = ?",
            ("private_pharmacy",),
        ).fetchone()[0]
    assert stored.startswith("enc:")
    assert "very-secret-password" not in stored
    assert registry.get_db_connection("private_pharmacy").connection_string == secret_url

    import app.api.kb as kb_api
    monkeypatch.setattr(kb_api, "file_registry", registry)
    response = kb_api.list_database_connections()
    public = next(c for c in response["connections"] if c["database_name"] == "private_pharmacy")
    assert public["connection_string"] == "[stored securely]"
    assert public["has_saved_connection"] is True
    assert "very-secret-password" not in str(response)


def test_sql_connector_description_never_discloses_credentials():
    connector = SQLConnector(
        "postgresql://owner:very-secret-password@db.example:5432/pharmacy",
        db_type="postgresql",
    )
    assert "pharmacy" in connector.describe()
    assert "very-secret-password" not in connector.describe()

def test_api_sql_discover_and_ingest(temp_pharmacy_db, tmp_path, monkeypatch):
    # Real retrieval integration runs against isolated storage, never the user's KB.
    import sys
    from app.api import kb, routes
    from app.core.config import settings
    from app.ingestion.store import KnowledgeBase
    registry = FileRegistry(str(tmp_path / 'registry.db'))
    monkeypatch.setattr(settings, 'storage_dir', str(tmp_path / 'storage'))
    monkeypatch.setattr(kb, 'file_registry', registry)
    monkeypatch.setattr(routes, 'file_registry', registry)
    monkeypatch.setattr(sys.modules[__name__], 'file_registry', registry)
    monkeypatch.setattr(kb, '_kb', KnowledgeBase(chroma_dir=str(tmp_path / 'chroma'), collection_name='schema_sql_test'))
    client = TestClient(app)
    # Clean up any leftover records from prior aborted runs
    try:
        client.delete("/api/kb/database/MockTestPOS")
    except Exception:
        pass

    try:
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
        table_mappings = {table['table_name']: {suggestion['source_column']: suggestion['canonical_field']
                         for suggestion in table['mapping_proposal']['suggestions'] if suggestion['canonical_field']}
                         for table in data['tables']}
        
        # 2. Ingest database endpoint (all tables in 1 step)
        ingest_res = client.post("/api/kb/ingest-database", json={
            "connection_string": f"sqlite:///{temp_pharmacy_db}",
            "db_type": "sqlite",
            "domain": "pharmacy",
            "strategy": "row",
            "table_mappings": table_mappings
        })
        assert ingest_res.status_code == 200
        ingest_data = ingest_res.json()
        assert ingest_data["success"] is True, f"Ingestion failed: {ingest_data}"
        assert ingest_data["database_name"] == "MockTestPOS"
        assert ingest_data["total_tables"] == 3
        assert ingest_data["total_rows"] == 7
        assert ingest_data["total_chunks"] == 7

        # Verify a record from a connected SQL table is retrievable with its
        # table/row provenance intact for the chat citation layer.
        search_res = client.post("/api/kb/search", json={
            "query": "Find invoice B101 for Fahad and Panadol",
            "domain": "pharmacy",
            "top_k": 5,
            "file_ids": ["db_mocktestpos_transactions"],
        })
        assert search_res.status_code == 200
        matching = [
            item for item in search_res.json()
            if item["metadata"].get("invoice_id") == "B101"
        ]
        assert matching
        assert matching[0]["metadata"]["table_name"] == "transactions"
        assert matching[0]["metadata"]["database_name"] == "MockTestPOS"
        assert matching[0]["source_row"] == 1
        from app.ingestion.models import RetrievedChunk
        from app.rag.chat import RAGChat
        source = RAGChat()._format_sources([RetrievedChunk(**matching[0])])[0]
        assert source.source_file == "transactions"
        assert source.label == "Invoice B101"
        assert source.source_row == 1

        # A requested table failure must be visible as an incomplete ingestion,
        # even when another table in the same request is already indexed.
        partial_res = client.post("/api/kb/ingest-database", json={
            "connection_string": f"sqlite:///{temp_pharmacy_db}",
            "db_type": "sqlite",
            "domain": "pharmacy",
            "strategy": "row",
            "tables": ["transactions", "missing_table"],
        })
        assert partial_res.status_code == 200
        partial_data = partial_res.json()
        assert partial_data["success"] is False
        assert partial_data["error_tables"] == 1
        assert "missing_table" in partial_data["message"]
        
        # Verify records in file registry
        registered_files = file_registry.list_files(include_all=True)
        db_records = [f for f in registered_files if f.group_name == "MockTestPOS"]
        active_records = [f for f in db_records if f.status == "active"]
        failed_records = [f for f in db_records if f.table_name == "missing_table"]
        assert len(active_records) == 3
        assert len(failed_records) == 1
        assert failed_records[0].status == "failed"
        
        # 3. Delete database group endpoint
        del_res = client.delete("/api/kb/database/MockTestPOS")
        assert del_res.status_code == 200
        assert del_res.json()["deleted_tables"] == 4
    finally:
        try:
            client.delete("/api/kb/database/MockTestPOS")
        except Exception:
            pass
