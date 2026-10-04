"""Test Suite 04: File Registry, Deduplication & Ingestion State Tracking.

Module: Module 6.4 — Document Ingestion and Knowledge Base Module
Target File: backend/app/ingestion/registry.py
Scope:
- Verifies SQLite schema creation for file_registry, file_hash_cache, and db_connections.
- Verifies SHA-256 file content hash calculation and persistent caching.
- Verifies file lifecycle registration and progressive status updates (pending -> processing -> active).
- Verifies content-based deduplication detection (get_file_by_hash).
- Verifies file record deletion and database connection tracking.
"""

from pathlib import Path
import pytest
from app.ingestion.registry import FileRegistry


class TestFileRegistryAndDeduplication:
    """Verifies SQLite registry state catalog, SHA-256 deduplication, and file tracking."""

    def test_registry_db_schema_initialization(self, temp_registry_db):
        """Initializes SQLite tables for file registry, hash cache, and database connections."""
        with temp_registry_db._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}

            assert "file_registry" in tables
            assert "file_hash_cache" in tables
            assert "db_connections" in tables

    def test_sha256_file_hash_calculation_and_caching(self, temp_registry_db, tmp_path):
        """Calculates accurate 64-char SHA-256 hash of file content and caches result."""
        test_file = tmp_path / "pharmacy_sample.csv"
        test_file.write_text("date,product,qty,amount\n2026-03-01,Panadol,10,350", encoding="utf-8")

        file_hash_1 = temp_registry_db.calculate_hash(str(test_file))
        assert len(file_hash_1) == 64  # SHA-256 hex digest length

        # Second call hits persistent / memory cache
        file_hash_2 = temp_registry_db.calculate_hash(str(test_file))
        assert file_hash_1 == file_hash_2

    def test_file_registration_and_status_progression(self, temp_registry_db, tmp_path):
        """Registers a source file and updates status through ingestion lifecycle."""
        csv_file = tmp_path / "daily_ledger.csv"
        csv_file.write_text("id,val\n1,100", encoding="utf-8")

        # Initial registration
        record = temp_registry_db.register_or_update(
            file_path=str(csv_file),
            chunk_count=12,
            domain="pharmacy",
            strategy="row",
            file_id="reg_test_01"
        )

        assert record.file_id == "reg_test_01"
        assert record.chunk_count == 12
        assert record.domain == "pharmacy"
        assert record.status == "active"

        # Update progressive status
        temp_registry_db.set_file_status(
            file_path=str(csv_file),
            status="processing",
            progress=55.0,
            step_text="Vectorizing chunks...",
            file_id="reg_test_01"
        )

        updated = temp_registry_db.get_file_by_id("reg_test_01")
        assert updated.status == "processing"
        assert updated.progress == 55.0
        assert updated.step_text == "Vectorizing chunks..."

    def test_duplicate_content_detection(self, temp_registry_db, tmp_path):
        """Identifies existing identical files by SHA-256 content hash."""
        original_file = tmp_path / "original_sales.csv"
        content = "colA,colB\nVal1,Val2\nVal3,Val4"
        original_file.write_text(content, encoding="utf-8")

        file_hash = temp_registry_db.calculate_hash(str(original_file))

        temp_registry_db.register_or_update(
            file_path=str(original_file),
            chunk_count=2,
            domain="pharmacy",
            file_id="file_orig_01"
        )

        # Lookup by hash
        match = temp_registry_db.get_file_by_hash(file_hash, active_only=True)
        assert match is not None
        assert match.file_id == "file_orig_01"
        assert match.chunk_count == 2

    def test_file_deletion_and_cleanup(self, temp_registry_db, tmp_path):
        """Deletes file record from SQLite registry."""
        csv_file = tmp_path / "to_delete.csv"
        csv_file.write_text("data\n1", encoding="utf-8")

        temp_registry_db.register_or_update(
            file_path=str(csv_file),
            chunk_count=5,
            domain="pharmacy",
            file_id="file_del_01"
        )

        assert temp_registry_db.get_file_by_id("file_del_01") is not None

        # Delete
        success = temp_registry_db.delete_file("file_del_01")
        assert success is True
        assert temp_registry_db.get_file_by_id("file_del_01") is None

    def test_db_connection_registration_and_watermarks(self, temp_registry_db):
        """Registers a live database connection and tracks table sync watermarks."""
        db_name = "pos_main_db"
        conn_str = "sqlite:///data/test_pos.db"

        reg = temp_registry_db.save_db_connection(
            database_name=db_name,
            connection_string=conn_str,
            db_type="sqlite",
            domain="pharmacy",
            strategy="row",
            sync_interval_sec=30
        )

        assert reg.database_name == db_name
        assert reg.sync_interval_sec == 30

        # Retrieve and list
        conns = temp_registry_db.list_db_connections()
        assert len(conns) == 1
        assert conns[0].database_name == db_name

        # Update watermark status
        temp_registry_db.update_db_sync_status(
            database_name=db_name,
            status="active",
            watermarks='{"sales_table": "2026-03-01T12:00:00"}'
        )
        updated_conn = temp_registry_db.get_db_connection(db_name)
        assert "sales_table" in updated_conn.watermarks

        # Delete connection
        assert temp_registry_db.delete_db_connection(db_name) is True
        assert temp_registry_db.get_db_connection(db_name) is None
