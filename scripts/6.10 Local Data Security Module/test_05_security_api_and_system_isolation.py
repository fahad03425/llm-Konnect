"""Test Suite 5: Security API & Air-Gapped System Isolation (Module 6.10).

Verifies the HTTP API endpoints and air-gapped environment isolation:
1. `GET /api/security/status`: Telemetry endpoint reporting AES-256-GCM status and metrics.
2. `POST /api/security/encrypt-all`: Migration endpoint converting legacy plaintext files to ciphertext.
3. `/api/files/upload`: Contract verifying incoming uploads are encrypted immediately upon receipt.
4. Offline environment flags ensuring strict air-gapped operation (HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE).
5. Dynamic configuration toggle: Graceful passthrough handling if encryption_enabled is False.
"""

import io
import os
import pytest
from app.core.config import settings
from app.security.crypto import (
    is_encrypted_file,
    encrypt_bytes,
    decrypt_bytes,
)


class TestSecurityApiAndSystemIsolation:
    """Rigorous tests for the security endpoints and offline runtime configuration."""

    def test_security_status_endpoint(self, test_client):
        """Verifies GET /api/security/status returns accurate security telemetry and local guarantees."""
        response = test_client.get("/api/security/status")
        assert response.status_code == 200

        data = response.json()
        assert data["enabled"] is True
        assert data["algorithm"] == "AES-256-GCM"
        assert data["key_present"] is True
        assert data["vector_db_encrypted"] is True
        assert data["sessions_encrypted"] is True
        assert data["never_leaves_machine"] is True
        assert "Local AES-256-GCM encryption at rest active" in data["status_summary"]
        assert isinstance(data["encrypted_files_count"], int)
        assert isinstance(data["unencrypted_files_count"], int)

    def test_encrypt_all_endpoint(self, test_client, sample_financial_csv_bytes):
        """Verifies POST /api/security/encrypt-all converts legacy plaintext files in data/uploads."""
        # Find project uploads dir
        from app.security.crypto import get_project_root
        uploads_dir = os.path.join(get_project_root(), "data", "uploads")
        os.makedirs(uploads_dir, exist_ok=True)

        legacy_file = os.path.join(uploads_dir, "legacy_plaintext_test_file.csv")
        try:
            with open(legacy_file, "wb") as f:
                f.write(sample_financial_csv_bytes)

            assert is_encrypted_file(legacy_file) is False

            # Call /encrypt-all
            response = test_client.post("/api/security/encrypt-all")
            assert response.status_code == 200
            res = response.json()

            assert res["status"] == "ok"
            assert "legacy_plaintext_test_file.csv" in res["newly_encrypted"] or "legacy_plaintext_test_file.csv" in res["already_encrypted"]

            # File must now be encrypted on disk
            assert is_encrypted_file(legacy_file) is True
        finally:
            if os.path.exists(legacy_file):
                os.remove(legacy_file)

    def test_instant_upload_encryption_contract(self, test_client, sample_financial_csv_bytes):
        """Verifies that uploading a file via /api/files/upload encrypts it on disk before responding."""
        from app.security.crypto import get_project_root
        upload_filename = "api_upload_encryption_contract.csv"

        response = test_client.post(
            "/api/files/upload",
            files={"file": (upload_filename, io.BytesIO(sample_financial_csv_bytes), "text/csv")}
        )
        assert response.status_code == 200
        res = response.json()

        assert res["filename"] == upload_filename
        assert res.get("is_encrypted") is True

        # Check physical disk file
        disk_path = os.path.join(get_project_root(), "data", "uploads", upload_filename)
        try:
            assert os.path.isfile(disk_path)
            assert is_encrypted_file(disk_path) is True

            # Disk content must be ciphertext
            with open(disk_path, "rb") as f:
                raw_disk = f.read()
            assert b"Augmentin 625mg" not in raw_disk
        finally:
            if os.path.exists(disk_path):
                os.remove(disk_path)

    def test_offline_airgapped_isolation_flags(self):
        """Verifies that offline environment variables are strictly enforced to prevent external leakage."""
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        assert os.environ.get("HF_DATASETS_OFFLINE") == "1"

    def test_encryption_disabled_graceful_fallback(self, monkeypatch):
        """Verifies that when encryption_enabled is False, functions safely operate in plaintext passthrough."""
        monkeypatch.setattr(settings, "encryption_enabled", False)

        test_data = b"Plaintext passthrough test data"
        result = encrypt_bytes(test_data)
        assert result == test_data  # Should return unencrypted data

        decrypted = decrypt_bytes(result)
        assert decrypted == test_data
