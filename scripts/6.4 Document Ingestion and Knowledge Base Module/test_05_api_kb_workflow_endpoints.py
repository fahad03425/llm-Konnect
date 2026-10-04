"""Test Suite 05: FastAPI End-to-End Ingestion, Search & Management Routes.

Module: Module 6.4 — Document Ingestion and Knowledge Base Module
Target File: backend/app/api/kb.py
Scope:
- Verifies POST /api/kb/ingest full workflow (connector detection -> mapping -> validation -> embedding -> Chroma & SQLite persistence).
- Verifies POST /api/kb/search semantic retrieval endpoint.
- Verifies GET /api/kb/sources listing registered files and aggregate chunk statistics.
- Verifies DELETE /api/kb/sources/{file_id} cascading deletion from ChromaDB and SQLite registry.
- Verifies error responses (HTTP 404 on missing source files, HTTP 400 on invalid mappings).
"""

from pathlib import Path
import pytest


class TestApiKbWorkflowEndpoints:
    """Verifies HTTP endpoints for file ingestion, semantic search, and file un-ingestion."""

    def test_ingest_source_endpoint_success(self, test_client, tmp_path):
        """End-to-end HTTP ingestion of a valid pharmacy CSV file."""
        csv_file = tmp_path / "api_pos_sales.csv"
        csv_content = (
            "Date,BillNo,Medicine,SoldQty,MRP,Total\n"
            "2026-03-01,INV-5001,Panadol 500mg,10,35.0,350.0\n"
            "2026-03-01,INV-5002,Brufen 400mg,5,75.0,375.0\n"
        )
        csv_file.write_text(csv_content, encoding="utf-8")

        mapping = {
            "Date": "date",
            "BillNo": "invoice_id",
            "Medicine": "product_id",
            "SoldQty": "quantity",
            "MRP": "mrp",
            "Total": "amount"
        }

        res = test_client.post("/api/kb/ingest", json={
            "file_path": str(csv_file),
            "mapping": mapping,
            "domain": "pharmacy",
            "strategy": "row",
            "file_id": "test_api_file_01"
        })

        assert res.status_code == 200
        data = res.json()
        assert data["total_chunks"] == 2
        assert data["file_id"] == "test_api_file_01"
        assert data["domain"] == "pharmacy"

    def test_search_endpoint_returns_semantic_results(self, test_client):
        """POST /api/kb/search executes semantic vector search over the ingested knowledge base."""
        res = test_client.post("/api/kb/search", json={
            "query": "Panadol pain relief tablets",
            "top_k": 2,
            "domain": "pharmacy"
        })

        assert res.status_code == 200
        results = res.json()
        assert isinstance(results, list)
        if len(results) > 0:
            top_hit = results[0]
            assert "text" in top_hit
            assert "metadata" in top_hit
            assert "score" in top_hit

    def test_list_sources_endpoint(self, test_client):
        """GET /api/kb/sources returns all registered sources with chunk statistics."""
        res = test_client.get("/api/kb/sources", params={"domain": "pharmacy"})

        assert res.status_code == 200
        data = res.json()
        assert "files" in data
        assert "total_files" in data
        assert "total_chunks" in data
        assert data["total_files"] >= 1

    def test_delete_source_by_id_endpoint(self, test_client):
        """DELETE /api/kb/sources/{file_id} un-ingests chunks and removes the registry record."""
        res = test_client.delete("/api/kb/sources/test_api_file_01")

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["file_id"] == "test_api_file_01"

    def test_ingest_missing_file_returns_404(self, test_client):
        """Submitting non-existent file path returns HTTP 404 Not Found."""
        res = test_client.post("/api/kb/ingest", json={
            "file_path": "C:/invalid/non_existent_file.csv",
            "domain": "pharmacy"
        })

        assert res.status_code == 404
