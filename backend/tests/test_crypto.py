"""Tests for Module 5.1 — Local Data Security (Encryption at Rest)."""

import os
import io
import json
import pytest
import pandas as pd
from fastapi.testclient import TestClient

from app.core.config import settings
from app.security.crypto import (
    get_vault_key,
    reset_cached_key,
    encrypt_bytes,
    decrypt_bytes,
    encrypt_string,
    decrypt_string,
    encrypt_file,
    decrypt_file_to_bytes,
    decrypt_file_to_stream,
    is_encrypted_bytes,
    is_encrypted_file,
    MAGIC_HEADER
)
from app.connectors.csv_excel import CSVConnector, ExcelConnector
from app.ingestion.store import KnowledgeBase
from app.rag.history import SessionManager
from app.main import app

client = TestClient(app)


def test_vault_key_generation_and_persistence(tmp_path, monkeypatch):
    """Verify local 256-bit AES master key generation and persistence."""
    test_key_file = str(tmp_path / ".test_vault_key")
    monkeypatch.setattr(settings, "vault_key_path", test_key_file)
    monkeypatch.delenv("LLM_KONNECT_SECRET_KEY", raising=False)
    reset_cached_key()

    key1 = get_vault_key()
    assert len(key1) == 32
    assert os.path.exists(test_key_file)

    # Calling again should load the exact same persisted key
    reset_cached_key()
    key2 = get_vault_key()
    assert key1 == key2


def test_encrypt_decrypt_bytes():
    """Verify authenticated AES-256-GCM byte encryption and decryption."""
    data = b"Patient: John Doe, Drug: Panadol 500mg, Price: 250 PKR"
    encrypted = encrypt_bytes(data)

    # Must start with LLMENC01 magic header
    assert encrypted.startswith(MAGIC_HEADER)
    assert is_encrypted_bytes(encrypted) is True
    assert data not in encrypted  # Plaintext is not exposed

    # Decrypt
    decrypted = decrypt_bytes(encrypted)
    assert decrypted == data


def test_decrypt_bytes_tamper_detection():
    """Verify that tampering with ciphertext triggers authentication error."""
    from cryptography.exceptions import InvalidTag
    data = b"Sensitive Financial Record"
    encrypted = bytearray(encrypt_bytes(data))

    # Tamper with a byte in the ciphertext payload
    encrypted[-1] ^= 0xFF

    with pytest.raises(InvalidTag):
        decrypt_bytes(bytes(encrypted), allow_passthrough=False)


def test_encrypt_decrypt_string():
    """Verify string encryption with portable enc: base64 envelope."""
    text = "Panadol 500mg — 100 boxes sold for PKR 2,500"
    enc = encrypt_string(text)

    assert enc.startswith("enc:")
    assert "Panadol" not in enc

    dec = decrypt_string(enc)
    assert dec == text

    # Passthrough for unencrypted string
    plain = "regular plain text"
    assert decrypt_string(plain) == plain


def test_urdu_string_encryption():
    """Verify unicode / Urdu support in encryption."""
    urdu_text = "پیناڈول گولی — قیمت ۲۵۰ روپے"
    enc = encrypt_string(urdu_text)
    assert enc.startswith("enc:")
    dec = decrypt_string(enc)
    assert dec == urdu_text


def test_encrypt_file_and_in_memory_decrypt(tmp_path):
    """Verify file encryption on disk and pure in-memory decryption."""
    csv_file = tmp_path / "financial_data.csv"
    original_content = b"invoice_id,product,amount\nINV-001,Brufen,450.0\nINV-002,Panadol,180.0\n"
    csv_file.write_bytes(original_content)

    assert is_encrypted_file(str(csv_file)) is False

    # Encrypt the file on disk
    encrypted_path = encrypt_file(str(csv_file))
    assert is_encrypted_file(encrypted_path) is True

    # Inspect raw file on disk: it must NOT contain plaintext strings
    raw_disk_bytes = csv_file.read_bytes()
    assert raw_disk_bytes.startswith(MAGIC_HEADER)
    assert b"Brufen" not in raw_disk_bytes
    assert b"INV-001" not in raw_disk_bytes

    # Decrypt in memory without touching disk
    in_mem_bytes = decrypt_file_to_bytes(str(csv_file))
    assert in_mem_bytes == original_content

    stream = decrypt_file_to_stream(str(csv_file))
    assert isinstance(stream, io.BytesIO)
    assert stream.read() == original_content


def test_csv_connector_with_encrypted_file(tmp_path):
    """Verify CSVConnector transparently reads encrypted CSVs in memory."""
    csv_file = tmp_path / "sales.csv"
    csv_file.write_text("Medicine Name,Qty,Rate PKR\nPanadol 500mg,20,18.0\nAugmentin 625mg,5,220.0\n", encoding="utf-8")

    encrypt_file(str(csv_file))
    assert is_encrypted_file(str(csv_file)) is True

    conn = CSVConnector(str(csv_file))
    df = conn.fetch()

    assert len(df) == 2
    assert "Panadol 500mg" in df["Medicine Name"].values
    assert df["source_connector"].iloc[0] == "csv"


def test_excel_connector_with_encrypted_file(tmp_path):
    """Verify ExcelConnector transparently reads encrypted Excel files in memory."""
    xlsx_file = tmp_path / "inventory.xlsx"
    df_src = pd.DataFrame([
        {"product": "Panadol", "stock": 100, "mrp": 25.0},
        {"product": "Ciproxin", "stock": 45, "mrp": 95.0}
    ])
    df_src.to_excel(str(xlsx_file), index=False)

    encrypt_file(str(xlsx_file))
    assert is_encrypted_file(str(xlsx_file)) is True

    conn = ExcelConnector(str(xlsx_file))
    df = conn.fetch()

    assert len(df) == 2
    assert "Panadol" in df["product"].values

class MockCollection:
    def __init__(self):
        self.data = []

    def upsert(self, ids, embeddings, documents, metadatas):
        for cid, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.data.append({"id": cid, "embedding": emb, "document": doc, "metadata": meta})

    def count(self):
        return len(self.data)

    def query(self, query_embeddings, n_results, where=None, include=None):
        docs = [d["document"] for d in self.data[:n_results]]
        metas = [d["metadata"] for d in self.data[:n_results]]
        dists = [0.1] * len(docs)
        return {"documents": [docs], "metadatas": [metas], "distances": [dists]}


class MockEmbedder:
    def encode(self, texts, **kwargs):
        import numpy as np
        return np.ones((len(texts), 384))


@pytest.fixture
def fake_kb(monkeypatch):
    mock_col = MockCollection()
    kb = KnowledgeBase()
    monkeypatch.setattr(kb, "_get_chroma", lambda: mock_col)
    monkeypatch.setattr(kb, "_get_embedder", lambda *a, **kw: MockEmbedder())
    return kb


def test_vector_db_stores_encrypted_documents(fake_kb):
    """Verify Chroma collection stores encrypted ciphertext documents on disk."""
    df = pd.DataFrame([
        {
            "source_row": 1, "date": "2026-01-05", "txn_type": "sale",
            "product_id": "SecretMed", "quantity": 10, "manufacturer": "PharmaOne",
            "amount": 1500
        }
    ])
    meta = {"source_file": "confidential_pos.csv"}

    summary = fake_kb.add_dataframe(df, source_meta=meta, domain="pharmacy")
    assert summary.total_chunks == 1

    # Check raw documents in collection
    col = fake_kb._get_chroma()
    raw_stored_docs = [d["document"] for d in col.data]
    assert len(raw_stored_docs) == 1

    # The raw document on disk must be encrypted with enc: prefix
    assert raw_stored_docs[0].startswith("enc:")
    assert "SecretMed" not in raw_stored_docs[0]
    assert "1500" not in raw_stored_docs[0]

    # Semantic search retrieves and decrypts the text in memory
    results = fake_kb.search("SecretMed", top_k=1)
    assert len(results) == 1
    assert "SecretMed" in results[0].text


def test_session_manager_encryption_at_rest(tmp_path):
    """Verify chat sessions are encrypted at rest on disk."""
    manager = SessionManager()
    manager.storage_path = str(tmp_path / "sessions")
    os.makedirs(manager.storage_path, exist_ok=True)

    session_id = "test-enc-sess-1"
    manager.append_turn(session_id, "user", "What was our highest sale?", domain="pharmacy")
    manager.append_turn(session_id, "assistant", "Invoice 3001 for 17,500 PKR", domain="pharmacy")

    session_file = tmp_path / "sessions" / f"{session_id}.json"
    assert session_file.exists()

    # Raw file on disk is ciphertext starting with MAGIC_HEADER
    raw_disk = session_file.read_bytes()
    assert raw_disk.startswith(MAGIC_HEADER)
    assert b"Invoice 3001" not in raw_disk
    assert b"highest sale" not in raw_disk

    # SessionManager loads and decrypts seamlessly
    sess = manager.get_session(session_id)
    assert len(sess["messages"]) == 2
    assert "Invoice 3001" in sess["messages"][1]["content"]


def test_security_api_status():
    """Verify GET /api/security/status endpoint returns active AES-256-GCM status."""
    resp = client.get("/api/security/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["algorithm"] == "AES-256-GCM"
    assert data["key_present"] is True
    assert data["vector_db_encrypted"] is True
    assert data["sessions_encrypted"] is True
    assert data["never_leaves_machine"] is True
