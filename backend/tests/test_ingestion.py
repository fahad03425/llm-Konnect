"""Tests for Module 6.4 -- Knowledge Base & Ingestion."""
import pytest
import pandas as pd
import os
import shutil

from app.core.config import settings
from app.ingestion.store import KnowledgeBase
from app.schema.domain import get_domain_pack

class FakeEmbedder:
    """Mock for SentenceTransformer that deterministically hashes text to vectors."""
    def __init__(self, model_name=None, device=None):
        pass

    def encode(self, texts, batch_size=32, normalize_embeddings=True):
        import numpy as np
        embeddings = []
        for text in texts:
            h = hash(text)
            vec = np.random.RandomState(h % (2**32)).rand(384).astype(np.float32)
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                vec = vec / norm if norm > 0 else vec
            embeddings.append(vec)
        return np.array(embeddings)

class FakeCollection:
    def __init__(self):
        self.data = []

    def upsert(self, ids, embeddings, documents, metadatas):
        for i in range(len(ids)):
            self.data = [d for d in self.data if d["id"] != ids[i]]
            self.data.append({
                "id": ids[i],
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            })

    def count(self):
        return len(self.data)

    def delete(self, where):
        for k, v in where.items():
            self.data = [d for d in self.data if d["metadata"].get(k) != v]

    def query(self, query_embeddings, n_results, where, include):
        results = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        filtered = list(self.data)
        if where:
            for k, v in where.items():
                filtered = [d for d in filtered if d["metadata"].get(k) == v]
        for d in filtered[:n_results]:
            results["documents"][0].append(d["document"])
            results["metadatas"][0].append(d["metadata"])
            results["distances"][0].append(0.1)
        return results


class FakeChromaClient:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name, metadata=None):
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

    def delete_collection(self, name):
        if name in self.collections:
            del self.collections[name]


import sys
from types import ModuleType

# Mock chromadb at import time so store.py never touches the real library
mock_chromadb = ModuleType("chromadb")
mock_chromadb.PersistentClient = lambda path: FakeChromaClient()
sys.modules["chromadb"] = mock_chromadb

# Mock tiktoken
class FakeEncoding:
    def encode(self, text):
        return text.split()
    def decode(self, tokens):
        return " ".join(tokens)

mock_tiktoken = ModuleType("tiktoken")
mock_tiktoken.get_encoding = lambda name: FakeEncoding()
sys.modules["tiktoken"] = mock_tiktoken


@pytest.fixture
def fake_kb(monkeypatch, tmp_path):
    test_chroma_dir = str(tmp_path / "test_chroma")
    monkeypatch.setattr(settings, "chroma_dir", test_chroma_dir)
    monkeypatch.setattr(
        KnowledgeBase, "_get_embedder",
        lambda self: FakeEmbedder(self.embedding_model_name, "cpu"),
    )
    kb = KnowledgeBase()
    yield kb
    if os.path.exists(test_chroma_dir):
        shutil.rmtree(test_chroma_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Original tests
# ---------------------------------------------------------------------------

def test_pharmacy_row_to_text():
    pack = get_domain_pack("pharmacy")
    row = {
        "txn_type": "sale",
        "date": "2026-01-05",
        "quantity": 20,
        "product_id": "Panadol 500mg Tablet",
        "generic_name": "Paracetamol",
        "batch_no": "B1234",
        "manufacturer": "GSK",
        "unit_price": 18.0,
        "amount": 360,
        "invoice_id": "INV-1001",
    }
    text = pack.row_to_text(row)
    assert "Sale on 2026-01-05" in text
    assert "20 units of Panadol 500mg Tablet" in text
    assert "generic Paracetamol" in text
    assert "batch B1234" in text
    assert "mfg GSK" in text
    assert "at Rs 18.0 each" in text
    assert "total Rs 360" in text
    assert "invoice INV-1001" in text


def test_add_dataframe_and_search(fake_kb):
    df = pd.DataFrame([
        {
            "source_row": 1, "date": "2026-01-01", "txn_type": "sale",
            "product_id": "Aspirin", "quantity": 10, "manufacturer": "Bayer",
            "amount": 100,
        },
        {
            "source_row": 2, "date": "2026-01-02", "txn_type": "return",
            "product_id": "Panadol", "quantity": 5, "manufacturer": "GSK",
            "amount": 50,
        },
    ])
    meta = {"source_file": "test.csv", "source_connector": "CSVConnector"}

    summary = fake_kb.add_dataframe(df, source_meta=meta, domain="pharmacy")
    assert summary.total_chunks == 2

    stats = fake_kb.stats()
    assert stats["total_chunks"] == 2

    # Filter by manufacturer
    results = fake_kb.search("painkiller", top_k=5, filters={"manufacturer": "Bayer"})
    assert len(results) == 1
    assert results[0].metadata["manufacturer"] == "Bayer"
    assert results[0].source_row == 1

    # Idempotency: re-ingest produces same count (upsert, not duplicate)
    summary2 = fake_kb.add_dataframe(df, source_meta=meta, domain="pharmacy")
    assert summary2.total_chunks == 2
    assert fake_kb.stats()["total_chunks"] == 2


def test_add_text_documents(fake_kb):
    docs = [
        "Supplier agreement for Getz Pharma. Valid from 2026-01-01 to 2026-12-31.",
        "Return policy: all expired items must be returned within 30 days.",
    ]
    meta = {"source_file": "policy.txt", "source_connector": "Text"}

    summary = fake_kb.add_text_documents(docs, source_meta=meta)
    assert summary.total_chunks > 0

    stats = fake_kb.stats()
    assert stats["total_chunks"] == 2

    results = fake_kb.search("return policy", top_k=1, filters={"is_free_text": True})
    assert len(results) == 1


def test_urdu_roundtrip(fake_kb):
    row = {
        "txn_type": "sale",
        "product_id": "\u067e\u06cc\u0646\u0627\u0688\u0648\u0644",
        "description": "\u062f\u0631\u062f \u06a9\u0634 \u062f\u0648\u0627",
    }
    df = pd.DataFrame([row])
    meta = {"source_file": "urdu.csv"}

    summary = fake_kb.add_dataframe(df, source_meta=meta, domain="pharmacy")
    assert summary.total_chunks == 1

    results = fake_kb.search("\u067e\u06cc\u0646\u0627\u0688\u0648\u0644", top_k=1)
    assert len(results) == 1
    assert "\u067e\u06cc\u0646\u0627\u0688\u0648\u0644" in results[0].text


# ---------------------------------------------------------------------------
# NEW: spec-required tests that were previously missing
# ---------------------------------------------------------------------------

def test_derived_year_month_expiry_metadata(fake_kb):
    """Checklist item 3: year, month, and expiry_year_month must be derived and stored."""
    df = pd.DataFrame([{
        "source_row": 1,
        "date": "2026-03-15",
        "expiry_date": "2027-06-30",
        "product_id": "Metformin",
        "txn_type": "purchase",
    }])
    meta = {"source_file": "meta_test.csv"}
    fake_kb.add_dataframe(df, source_meta=meta, domain="pharmacy")

    results = fake_kb.search("Metformin", top_k=1)
    assert len(results) == 1
    m = results[0].metadata
    assert m.get("year") == 2026, f"Expected year=2026, got {m.get('year')}"
    assert m.get("month") == 3,   f"Expected month=3, got {m.get('month')}"
    assert m.get("expiry_year_month") == "2027-06", \
        f"Expected expiry_year_month='2027-06', got {m.get('expiry_year_month')}"


def test_delete_source_removes_only_that_source(fake_kb):
    """Checklist item 5: delete_source must remove only chunks for the named source."""
    df_a = pd.DataFrame([{"source_row": 1, "product_id": "DrugA"}])
    df_b = pd.DataFrame([{"source_row": 1, "product_id": "DrugB"}])

    fake_kb.add_dataframe(df_a, source_meta={"source_file": "source_a.csv"}, domain="pharmacy")
    fake_kb.add_dataframe(df_b, source_meta={"source_file": "source_b.csv"}, domain="pharmacy")
    assert fake_kb.stats()["total_chunks"] == 2

    fake_kb.delete_source("source_a.csv")
    assert fake_kb.stats()["total_chunks"] == 1, "Only source_a chunks should be deleted"

    results = fake_kb.search("DrugB", top_k=1)
    assert len(results) == 1
    assert results[0].metadata.get("source_file") == "source_b.csv"


def test_e5_prefix_applied_correctly(monkeypatch, tmp_path):
    """Checklist item 4: 'passage: ' prefix stored in docs; 'query: ' prefix on queries.

    We build a fresh KnowledgeBase with a CapturingEmbedder installed directly
    as the singleton (_embedder), bypassing the fixture's class-level monkeypatch
    so that search() reliably uses our capturing instance.
    """
    import numpy as np

    monkeypatch.setattr(settings, "chroma_dir", str(tmp_path / "chroma_prefix"))

    captured = []

    class CapturingEmbedder:
        def encode(self, texts, **kw):
            captured.extend(texts)
            vecs = []
            for t in texts:
                h = hash(t)
                v = np.random.RandomState(h % (2**32)).rand(384).astype("float32")
                norm = np.linalg.norm(v)
                vecs.append(v / norm if norm > 0 else v)
            return np.array(vecs)

    kb = KnowledgeBase()
    kb._embedder = CapturingEmbedder()

    df = pd.DataFrame([{"source_row": 1, "product_id": "Aspirin"}])
    meta = {"source_file": "prefix_test.csv"}
    captured.clear()
    kb.add_dataframe(df, source_meta=meta, domain="pharmacy")

    # Stored document must carry the passage: prefix
    collection = kb._get_chroma()
    stored_docs = [d["document"] for d in collection.data]
    assert any(doc.startswith("passage: ") for doc in stored_docs), \
        "Stored chunks should start with 'passage: '"

    # The texts passed to encode() during ingestion should also start with passage:
    ingestion_texts = [t for t in captured if not t.startswith("query: ")]
    assert all(t.startswith("passage: ") for t in ingestion_texts), \
        f"All ingested texts should start with 'passage: '; got: {ingestion_texts}"

    # Now test that search() prefixes the query with 'query: '
    captured.clear()
    kb.search("pain reliever", top_k=1)
    assert captured, "encode() was never called during search"
    assert captured[0].startswith("query: "), \
        f"Query embedding input should start with 'query: ', got: {captured[0]!r}"


def test_greedy_merge_strategy(fake_kb):
    """Checklist item 2: strategy='merge' must group rows by key into fewer chunks."""
    df = pd.DataFrame([
        {"source_row": 1, "invoice_id": "INV-001", "product_id": "DrugA"},
        {"source_row": 2, "invoice_id": "INV-001", "product_id": "DrugB"},
        {"source_row": 3, "invoice_id": "INV-002", "product_id": "DrugC"},
        {"source_row": 4, "invoice_id": "INV-002", "product_id": "DrugD"},
    ])
    meta = {"source_file": "merge_test.csv"}
    summary = fake_kb.add_dataframe(
        df, source_meta=meta, domain="pharmacy",
        strategy="merge", merge_key="invoice_id",
    )
    # 4 rows with 2 invoice groups => 2 merged chunks
    assert summary.total_chunks == 2, \
        f"Merge strategy should produce 2 chunks, got {summary.total_chunks}"

    # Re-ingest must upsert (still 2, not 4)
    summary2 = fake_kb.add_dataframe(
        df, source_meta=meta, domain="pharmacy",
        strategy="merge", merge_key="invoice_id",
    )
    assert summary2.total_chunks == 2
    assert fake_kb.stats()["total_chunks"] == 2


def test_search_returns_empty_on_empty_collection(fake_kb):
    """BUG 3 regression: search on an empty collection must return [] not crash."""
    results = fake_kb.search("anything", top_k=5)
    assert results == [], f"Expected [], got {results}"


def test_chunk_id_stable_for_float_source_row(fake_kb):
    """BUG 2 regression: float and int source_row values must produce the same chunk ID."""
    import hashlib
    kb = fake_kb
    id_int   = kb._generate_chunk_id("f.csv", 4,   0)
    id_float = kb._generate_chunk_id("f.csv", 4.0, 0)
    assert id_int == id_float, \
        f"Chunk IDs differ for int/float source_row: {id_int!r} vs {id_float!r}"


def test_sanitize_metadata_handles_list_values(fake_kb):
    """BUG 1 regression: _sanitize_metadata must not crash on list/array values."""
    import numpy as np
    result = fake_kb._sanitize_metadata({
        "normal": "value",
        "list_field": [1, 2, 3],
        "array_field": np.array([0.1, 0.2]),
        "none_field": None,
        "nan_field": float("nan"),
    })
    # list and array should be stringified, None/NaN dropped
    assert result["normal"] == "value"
    assert "list_field" in result and isinstance(result["list_field"], str)
    assert "array_field" in result and isinstance(result["array_field"], str)
    assert "none_field" not in result
    assert "nan_field" not in result
