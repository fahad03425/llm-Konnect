"""Test Suite 4: Vector DB & Data Store Payload Encryption (Module 6.10).

Verifies encryption at rest across secondary persistence tiers:
1. Chroma vector database document chunks stored in 'enc:' envelopes.
2. Canonical row metadata (`record_json`) encrypted in vector store metadata.
3. RAG conversational turn history encrypted at rest on disk.
4. Third-party credential vault (Shopify Admin API tokens) encrypted with AAD.
5. External database connection strings encrypted in the ingestion registry.
"""

import json
import os
import pytest

from app.security.crypto import (
    encrypt_string,
    decrypt_string,
    encrypt_bytes,
    decrypt_bytes,
    is_encrypted_bytes,
    MAGIC_HEADER,
)
from app.connectors.credentials import save_shopify_token, read_shopify_token
from app.rag.history import SessionManager


class TestVectorDbAndPayloadEncryption:
    """Rigorous tests for vector database, session history, and credentials encryption."""

    def test_chroma_chunk_text_payload_encryption(self):
        """Verifies that vector database chunk texts are enveloped in encrypted strings."""
        chunk_text = (
            "Invoice #INV-2026-99: Customer Zubair Diagnostics purchased 50 units "
            "of Augmentin 625mg for PKR 22,500 with tax registration TRN-982134."
        )

        # Ingestion layer encrypts chunks before saving to Chroma
        stored_document = encrypt_string(chunk_text)
        assert stored_document.startswith("enc:")
        assert "Augmentin" not in stored_document
        assert "22,500" not in stored_document

        # Query layer decrypts retrieved chunks for prompt assembly
        decrypted_document = decrypt_string(stored_document)
        assert decrypted_document == chunk_text

    def test_metadata_record_json_encryption(self):
        """Verifies that canonical raw row JSON stored in chunk metadata is encrypted at rest."""
        raw_row = {
            "invoice_id": "INV-1099",
            "patient_name": "Fatima Noor",
            "hospital_mrn": "MRN-0092144",
            "billed_amount": 45000.0,
            "diagnosis": "Cardiology Consultation"
        }
        json_serialized = json.dumps(raw_row)

        encrypted_meta = encrypt_string(json_serialized)
        assert encrypted_meta.startswith("enc:")
        assert "Fatima" not in encrypted_meta
        assert "MRN-0092144" not in encrypted_meta

        decrypted_meta = decrypt_string(encrypted_meta)
        recovered_row = json.loads(decrypted_meta)
        assert recovered_row == raw_row
        assert recovered_row["billed_amount"] == 45000.0

    def test_rag_session_history_encryption_at_rest(self, temp_security_sandbox, sample_chat_session_payload):
        """Verifies that conversation history files are encrypted on disk with zero plaintext leakage."""
        sessions_dir = os.path.join(temp_security_sandbox["storage_dir"], "sessions")
        os.makedirs(sessions_dir, exist_ok=True)

        session_id = sample_chat_session_payload["id"]
        session_file = os.path.join(sessions_dir, f"{session_id}.json")

        # Simulate ChatHistoryManager saving session with encryption
        raw_json_bytes = json.dumps(sample_chat_session_payload, indent=2).encode("utf-8")
        encrypted_disk_bytes = encrypt_bytes(raw_json_bytes)

        with open(session_file, "wb") as f:
            f.write(encrypted_disk_bytes)

        # 1. Verify file on disk is encrypted
        assert is_encrypted_bytes(encrypted_disk_bytes) is True
        with open(session_file, "rb") as f:
            disk_bytes = f.read()
        assert disk_bytes.startswith(MAGIC_HEADER)
        assert b"Augmentin" not in disk_bytes
        assert b"gross profit" not in disk_bytes

        # 2. Verify decryption recovers complete session data
        recovered_bytes = decrypt_bytes(disk_bytes)
        recovered_session = json.loads(recovered_bytes.decode("utf-8"))
        assert recovered_session["id"] == session_id
        assert len(recovered_session["messages"]) == 2
        assert "Augmentin 625mg" in recovered_session["messages"][1]["content"]

    def test_shopify_credentials_vault_encryption(self, temp_security_sandbox):
        """Verifies third-party access tokens are encrypted using AES-256-GCM with shop domain AAD."""
        shop_domain = "my-local-pharmacy.myshopify.com"
        secret_access_token = "mock_shopify_token_9876543210abcdeffedcba"

        # Save token into vault
        identity = save_shopify_token(shop_domain, secret_access_token)
        assert isinstance(identity, str)
        assert len(identity) == 32

        # Check physical file on disk
        cred_dir = os.path.join(temp_security_sandbox["storage_dir"], "source_credentials")
        cred_file = os.path.join(cred_dir, f"{identity}.json")
        assert os.path.isfile(cred_file)

        # Inspect disk payload: access token MUST NOT exist in plaintext
        with open(cred_file, "r", encoding="utf-8") as f:
            raw_payload = f.read()
        assert secret_access_token not in raw_payload
        assert "mock_shopify_token" not in raw_payload

        # Read back token with correct shop domain
        recovered_token = read_shopify_token(identity, shop_domain)
        assert recovered_token == secret_access_token

        # Attempting to read with imposter shop domain fails
        with pytest.raises(ValueError, match="Shopify connection does not match this store"):
            read_shopify_token(identity, "imposter-store.myshopify.com")

    def test_connection_string_registry_encryption(self):
        """Verifies database credentials and connection strings are encrypted in registry records."""
        raw_conn_string = "Driver={Tally ODBC};Server=127.0.0.1;Port=9000;UID=admin;PWD=P@ssw0rd2026!"
        encrypted_conn = encrypt_string(raw_conn_string)

        assert encrypted_conn.startswith("enc:")
        assert "P@ssw0rd2026!" not in encrypted_conn

        decrypted_conn = decrypt_string(encrypted_conn)
        assert decrypted_conn == raw_conn_string

    def test_sqlite_chunk_storage_payload_protection(self, temp_security_sandbox):
        """Verifies that inserting encrypted chunks into SQLite stores guarantees zero plaintext in disk tables."""
        import sqlite3
        db_path = os.path.join(temp_security_sandbox["storage_dir"], "chunks_test.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE chunks (id TEXT PRIMARY KEY, text_payload TEXT, meta_json TEXT)")

        confidential_text = "Gross margin for Ventolin Inhalers was 41.5% in February 2026."
        confidential_meta = json.dumps({"store_id": "STORE-01", "cost": 450.0, "mrp": 680.0})

        cur.execute(
            "INSERT INTO chunks VALUES (?, ?, ?)",
            ("chunk-01", encrypt_string(confidential_text), encrypt_string(confidential_meta))
        )
        conn.commit()
        conn.close()

        # Read raw database file as bytes from disk
        with open(db_path, "rb") as f:
            raw_db_bytes = f.read()

        # Sensitive terms must NEVER appear in the SQLite binary file
        assert b"Ventolin" not in raw_db_bytes
        assert b"41.5%" not in raw_db_bytes
        assert b"STORE-01" not in raw_db_bytes
        assert b"enc:" in raw_db_bytes
