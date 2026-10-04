"""Regression checks against real local embeddings and isolated Chroma stores."""
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from app.core.config import settings
from app.ingestion.store import KnowledgeBase
from app.ingestion.safety import token_windows
from app.security.crypto import decrypt_string


@pytest.fixture
def kb(tmp_path):
    import sys
    for module in ("chromadb", "tiktoken"):
        if module in sys.modules and not hasattr(sys.modules[module], "__file__"):
            del sys.modules[module]
    return KnowledgeBase(str(tmp_path / "vectors"), "ingestion_safety")


def test_text_chunks_remain_distinct_in_search(kb):
    summary = kb.add_text_documents(["Quarterly financial audit revenue details. " * 500],
        {"source_file": "audit.txt"}, domain="finance", file_id="audit")
    assert summary.total_chunks >= 5
    results = kb.search("financial audit revenue", top_k=5, domain="finance")
    assert len(results) == 5
    assert len({result.metadata["chunk_id"] for result in results}) == 5


def test_merged_rows_roundtrip_without_undercounting(kb):
    frame = pd.DataFrame([
        {"invoice_id": "INV1", "product_id": "Panadol", "amount": 100, "source_row": 7},
        {"invoice_id": "INV1", "product_id": "Brufen", "amount": 200, "source_row": 8},
    ])
    kb.add_dataframe(frame, {"source_file": "invoice.csv"}, domain="pharmacy",
                     strategy="merge", merge_key="invoice_id", file_id="invoice")
    recovered = kb.get_dataframe(file_ids=["invoice"], source_files=["invoice.csv"])
    assert len(recovered) == 2
    assert recovered.amount.sum() == 300
    assert set(recovered.source_row) == {7, 8}
    assert not recovered.attrs.get("incomplete_source")


@pytest.mark.parametrize("text", [
    "Revenue and detailed cost information. " * 500,
    "دوائی کی فروخت اور آمدنی کی تفصیل۔ " * 300,
    "金融销售收入订单记录。" * 300,
], ids=["english", "urdu", "chinese"])
def test_model_token_budget_and_tail_coverage(kb, text):
    model = kb._get_embedder()
    text += " FINAL_SENTINEL_9821"
    windows = token_windows(text, model, kb.passage_prefix, settings.chunk_size, settings.chunk_overlap)
    assert len(windows) > 1
    assert windows[0].startswith(text[:10])
    assert windows[-1].endswith("FINAL_SENTINEL_9821")
    assert all(len(model.tokenizer.encode(kb.passage_prefix + window, add_special_tokens=True))
               <= min(model.max_seq_length, settings.chunk_size) for window in windows)


def test_long_rows_split_and_full_record_is_not_duplicated(kb):
    frame = pd.DataFrame([{"product_id": "Panadol", "amount": 100,
                           "description": "medicine revenue evidence " * 600 + "END_MARKER"}])
    summary = kb.add_dataframe(frame, {"source_file": "long.csv"}, domain="pharmacy", file_id="long")
    assert summary.total_chunks > 1
    stored = kb._get_chroma().get(where={"file_id": "long"}, include=["documents"])
    assert any("END_MARKER" in decrypt_string(document) for document in stored["documents"])
    assert kb.get_dataframe(file_ids=["long"]).amount.sum() == 100


def test_legacy_merged_payload_is_marked_incomplete(kb):
    from app.security.crypto import encrypt_string
    model = kb._get_embedder()
    kb._get_chroma().upsert(ids=["legacy"], embeddings=model.encode(["legacy"]).tolist(),
        documents=["legacy"], metadatas=[{"file_id": "legacy", "source_file": "legacy.csv",
        "source_row": 1, "items_in_chunk": 2, "record_json": encrypt_string(json.dumps({"amount": 100}))}])
    assert kb.get_dataframe(file_ids=["legacy"]).attrs["incomplete_source"]


def test_dataframe_fills_null_provenance_fields_from_chroma_metadata(kb):
    from app.security.crypto import encrypt_string

    collection = Mock()
    collection.get.side_effect = lambda where, **kwargs: {
        "metadatas": ([{
            "file_id": "sales-header", "source_file": "sql://sales/tbl_21",
            "table_name": "tbl_21", "source_row": 7,
            "record_json": encrypt_string(json.dumps({
                "invoice_id": "REC-00007", "payment_method": "Cash",
                "source_file": None, "table_name": None, "source_row": 7,
            })),
        }] if where == {"file_id": "sales-header"} else [])
    }
    kb._collection = collection

    frame = kb.get_dataframe(file_ids=["sales-header"])

    assert frame.loc[0, "source_file"] == "sql://sales/tbl_21"
    assert frame.loc[0, "table_name"] == "tbl_21"


def test_registered_file_id_falls_back_to_existing_filename_metadata(kb, monkeypatch):
    from types import SimpleNamespace
    from app.ingestion import registry

    collection = Mock()
    collection.get.side_effect = lambda where, **kwargs: {
        "metadatas": ([{"filename": "purchases.xlsx", "amount": 42, "source_row": 1}]
                      if where == {"filename": "purchases.xlsx"} else [])
    }
    kb._collection = collection
    monkeypatch.setattr(registry.file_registry, "get_file_by_id",
                        lambda file_id: SimpleNamespace(filename="purchases.xlsx"))

    frame = kb.get_dataframe(file_ids=["registered-purchases"])

    assert frame["amount"].tolist() == [42]
    assert collection.get.call_count == 3


def test_delete_source_does_not_delete_matching_basename(kb):
    frame = pd.DataFrame([{"product_id": "Panadol", "amount": 100}])
    for path, file_id in (("folder_a/sales.csv", "a"), ("folder_b/sales.csv", "b")):
        kb.add_dataframe(frame, {"source_file": path}, domain="pharmacy", file_id=file_id)
    kb.delete_source("folder_a/sales.csv")
    assert kb._get_chroma().get(where={"file_id": "a"})["ids"] == []
    assert len(kb._get_chroma().get(where={"file_id": "b"})["ids"]) == 1


def test_failed_embedding_keeps_old_source_and_success_prunes_stale(kb, monkeypatch):
    original = pd.DataFrame([{"product_id": "Panadol", "amount": 100},
                             {"product_id": "Brufen", "amount": 200}])
    meta = {"source_file": "replace.csv"}
    kb.add_dataframe(original, meta, domain="pharmacy", file_id="replace", replace_existing=True)
    model = kb._get_embedder()
    with monkeypatch.context() as patch:
        patch.setattr(model, "encode", Mock(side_effect=RuntimeError("embedding failure")))
        with pytest.raises(RuntimeError, match="embedding failure"):
            kb.add_dataframe(original.iloc[:1], meta, domain="pharmacy", file_id="replace", replace_existing=True)
    assert kb.get_dataframe(file_ids=["replace"]).amount.sum() == 300
    kb.add_dataframe(original.iloc[:1], meta, domain="pharmacy", file_id="replace", replace_existing=True)
    assert kb.get_dataframe(file_ids=["replace"]).amount.sum() == 100
    assert not any(collection.name.startswith(("stage_", "backup_")) for collection in kb._chroma_client.list_collections())


def test_publication_failure_rolls_back(kb, monkeypatch):
    from app.ingestion import safety
    original = pd.DataFrame([{"product_id": "Panadol", "amount": 100}])
    meta = {"source_file": "rollback.csv"}
    kb.add_dataframe(original, meta, domain="pharmacy", file_id="rollback", replace_existing=True)
    active = kb._get_chroma()
    real_copy = safety.copy_page
    calls = []
    def fail_after_write(target, page):
        real_copy(target, page)
        if target is active and not calls:
            calls.append(True)
            raise RuntimeError("publication failure")
    monkeypatch.setattr(safety, "copy_page", fail_after_write)
    replacement = original.assign(amount=999)
    with pytest.raises(RuntimeError, match="publication failure"):
        kb.add_dataframe(replacement, meta, domain="pharmacy", file_id="rollback", replace_existing=True)
    assert kb.get_dataframe(file_ids=["rollback"]).amount.sum() == 100


def test_cancelled_preparation_keeps_old_source(kb):
    original = pd.DataFrame([{"product_id": "Panadol", "amount": 100}])
    meta = {"source_file": "cancel.csv"}
    kb.add_dataframe(original, meta, domain="pharmacy", file_id="cancel", replace_existing=True)
    with pytest.raises(InterruptedError):
        kb.add_dataframe(original.assign(amount=999), meta, domain="pharmacy", file_id="cancel",
                         replace_existing=True, cancel_check=lambda: True)
    assert kb.get_dataframe(file_ids=["cancel"]).amount.sum() == 100


def test_sql_long_rows_and_failed_sync_preserve_old_data(kb, monkeypatch):
    original = pd.DataFrame([{"id": 1, "product_id": "Panadol", "amount": 100,
                             "description": "financial medicine detail " * 600},
                            {"id": 2, "product_id": "Brufen", "amount": 200}])
    meta = {"source_file": "sql://pharmacy/sales", "database_name": "pharmacy", "table_name": "sales"}
    first = kb.reconcile_database_table(original, meta, pk_cols=["id"], file_id="sql_safety")
    assert first["new_rows"] == 2
    assert first["chunks"] > 2
    assert kb.get_dataframe(file_ids=["sql_safety"]).amount.sum() == 300
    model = kb._get_embedder()
    with monkeypatch.context() as patch:
        patch.setattr(model, "encode", Mock(side_effect=RuntimeError("SQL embedding failure")))
        with pytest.raises(RuntimeError, match="SQL embedding failure"):
            kb.reconcile_database_table(original.iloc[:1].assign(amount=999), meta,
                                        pk_cols=["id"], file_id="sql_safety")
    assert kb.get_dataframe(file_ids=["sql_safety"]).amount.sum() == 300
    unchanged = kb.reconcile_database_table(original, meta, pk_cols=["id"], file_id="sql_safety")
    assert unchanged["status"] == "unchanged"


def test_live_sql_updates_and_deletes_remove_all_old_windows(kb, tmp_path, monkeypatch):
    from app.ingestion import sync_worker as sync
    from app.ingestion.registry import FileRegistry
    registry = FileRegistry(str(tmp_path / "registry.db"))
    monkeypatch.setattr(sync, "file_registry", registry)
    monkeypatch.setattr(sync, "KnowledgeBase", lambda: kb)
    frame = pd.DataFrame([
        {"id": 1, "product_id": "Panadol", "amount": 100, "description": "medicine audit detail " * 600},
        {"id": 2, "product_id": "Brufen", "amount": 200},
    ])
    meta = {"source_file": "sql://pharmacy/sales", "database_name": "pharmacy", "table_name": "sales"}
    kb.reconcile_database_table(frame, meta, pk_cols=["id"], file_id="db_pharmacy_sales")
    registry.register_or_update(meta["source_file"], 2, domain="pharmacy", file_id="db_pharmacy_sales", source_type="database")
    worker = sync.SyncWorker()
    worker._process_batch([sync.ChangeEvent(action="update", database_name="pharmacy", table_name="sales",
        row_id="1", data={"id": 1, "product_id": "Panadol", "amount": 50})])
    recovered = kb.get_dataframe(file_ids=["db_pharmacy_sales"])
    assert len(recovered) == 2
    assert recovered.amount.sum() == 250
    stored = kb._get_chroma().get(where={"row_id": "1"})
    assert len(stored["ids"]) == 1
    worker._process_batch([sync.ChangeEvent(action="delete", database_name="pharmacy", table_name="sales", row_id="1")])
    assert kb.get_dataframe(file_ids=["db_pharmacy_sales"]).amount.sum() == 200


def test_registry_deletion_isolates_identical_filenames(tmp_path):
    from app.ingestion.registry import FileRegistry
    registry = FileRegistry(str(tmp_path / "registry.db"))
    a = registry.register_or_update(str(tmp_path / "a" / "sales.csv"), 1, file_id="a")
    b = registry.register_or_update(str(tmp_path / "b" / "sales.csv"), 1, file_id="b")
    with pytest.raises(ValueError, match="Multiple sources"):
        registry.delete_file("sales.csv")
    assert registry.get_file_by_id("a") and registry.get_file_by_id("b")
    assert registry.delete_file(a.file_path)
    assert registry.get_file_by_id("a") is None
    assert registry.get_file_by_id(b.file_id) is not None
