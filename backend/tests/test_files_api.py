import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.ingestion.registry import file_registry, FileRegistry
from app.ingestion.store import KnowledgeBase

client = TestClient(app)

@pytest.fixture
def mock_kb():
    with patch("app.api.files._kb") as mock:
        yield mock

def test_list_files():
    res = client.get("/api/files")
    assert res.status_code == 200
    data = res.json()
    assert "files" in data
    assert "total_files" in data
    assert "total_ingested_files" in data
    assert isinstance(data["files"], list)

def test_upload_and_delete_file():
    test_content = b"product_name,batch,expiry_date,price,quantity\nPanadol,B1,2026-12-31,50.0,100\n"
    uploaded_path = None
    try:
        # 1. Upload a temporary csv file
        res = client.post("/api/files/upload", files={"file": ("test_upload_lifecycle.csv", test_content, "text/csv")})
        assert res.status_code == 200
        data = res.json()
        assert data["filename"] == "test_upload_lifecycle.csv"
        uploaded_path = data["file_path"]
        assert os.path.exists(uploaded_path)

        # 2. Attempt duplicate upload with same filename -> should return 409 Conflict
        dup_res = client.post("/api/files/upload", files={"file": ("test_upload_lifecycle.csv", test_content, "text/csv")})
        assert dup_res.status_code == 409
        assert "Duplicate file" in dup_res.json()["detail"]

        # 3. Attempt duplicate upload with different filename but identical content -> should return 409 Conflict
        dup_content_res = client.post("/api/files/upload", files={"file": ("renamed_duplicate.csv", test_content, "text/csv")})
        assert dup_content_res.status_code == 409
        assert "Duplicate file" in dup_content_res.json()["detail"]

        # 4. Verify file appears in /api/files
        list_res = client.get("/api/files")
        filenames = [f["filename"] for f in list_res.json()["files"]]
        assert "test_upload_lifecycle.csv" in filenames
    finally:
        # 5. Clean up files cleanly
        client.delete("/api/files?filename=test_upload_lifecycle.csv")
        client.delete("/api/files?filename=renamed_duplicate.csv")
        if uploaded_path and os.path.exists(uploaded_path):
            try:
                os.remove(uploaded_path)
            except Exception:
                pass

def test_un_ingest_endpoint(mock_kb):
    res = client.post("/api/files/un-ingest", json={"file_id": "file_test123", "file_path": "data/uploads/sample.csv"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"

def test_processing_status_persists_on_refresh():
    # Simulate a file set to 'processing' with live progress
    mock_file = "data/uploads/test_mock_processing.csv"
    rec = file_registry.set_file_status(
        file_path=mock_file,
        status="processing",
        domain="pharmacy",
        strategy="row",
        progress=45.0,
        step_text="Generating embeddings..."
    )

    try:
        res = client.get("/api/files")
        assert res.status_code == 200
        files = res.json()["files"]
        sample_file = next((f for f in files if "test_mock_processing.csv" in f["filename"]), None)
        assert sample_file is not None
        assert sample_file["is_processing"] is True
        assert sample_file["status"] == "processing"
        assert sample_file["progress"] >= 45.0
        assert "Generating embeddings" in sample_file["step_text"]
    finally:
        # Clean up test status cleanly
        file_registry.delete_file(rec.file_id)
        file_registry.delete_file(mock_file)

def test_failed_status_with_error_message():
    # Simulate a failed ingestion with error message
    mock_file = "data/uploads/test_mock_failed.csv"
    rec = file_registry.set_file_status(
        file_path=mock_file,
        status="failed",
        domain="pharmacy",
        strategy="row",
        progress=0.0,
        step_text="Failed",
        error_message="Invalid column format"
    )

    try:
        res = client.get("/api/files")
        assert res.status_code == 200
        files = res.json()["files"]
        sample_file = next((f for f in files if "test_mock_failed.csv" in f["filename"]), None)
        assert sample_file is not None
        assert sample_file["is_processing"] is False
        assert sample_file["status"] == "failed"
        assert sample_file["error_message"] == "Invalid column format"
    finally:
        # Clean up cleanly
        file_registry.delete_file(rec.file_id)
        file_registry.delete_file(mock_file)
