import pytest
import pandas as pd
from app.ingestion.registry import FileRegistry
from app.ingestion.store import KnowledgeBase
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest

@pytest.fixture
def temp_registry(tmp_path):
    db_path = str(tmp_path / "test_reg.sqlite3")
    return FileRegistry(db_path=db_path)

def test_file_registry_crud(temp_registry, tmp_path):
    sample_file = tmp_path / "pharmacy_sample.csv"
    sample_file.write_text("item,price\nPanadol,50\nAugmentin,250")
    
    rec = temp_registry.register_or_update(
        file_path=str(sample_file),
        chunk_count=2,
        domain="pharmacy",
        strategy="row",
        file_id="sample_001"
    )
    assert rec.file_id == "sample_001"
    assert rec.filename == "pharmacy_sample.csv"
    assert rec.chunk_count == 2
    assert len(rec.file_hash) == 64
    
    # List files
    files = temp_registry.list_files(domain="pharmacy")
    assert len(files) == 1
    assert files[0].file_id == "sample_001"
    
    # Delete file
    deleted = temp_registry.delete_file("sample_001")
    assert deleted is True
    assert len(temp_registry.list_files()) == 0

def test_kb_scoped_search_and_uningest(tmp_path):
    chroma_dir = str(tmp_path / "chroma_test")
    kb = KnowledgeBase(chroma_dir=chroma_dir, collection_name="test_scope_kb")
    
    df1 = pd.DataFrame([
        {"product_id": "MED1", "generic_name": "Paracetamol 500mg Tablets", "quantity": 100, "unit_price": 50.0},
        {"product_id": "MED2", "generic_name": "Ibuprofen 400mg Tablets", "quantity": 50, "unit_price": 120.0},
    ])
    
    df2 = pd.DataFrame([
        {"product_id": "SUP1", "generic_name": "Surgical Mask 3-Ply", "quantity": 500, "unit_price": 10.0},
        {"product_id": "SUP2", "generic_name": "Digital Thermometer", "quantity": 20, "unit_price": 850.0},
    ])
    
    summary1 = kb.add_dataframe(
        canonical_df=df1,
        source_meta={"source_file": "meds_inventory.csv", "source_connector": "CSVConnector"},
        domain="pharmacy",
        file_id="meds_file_1"
    )
    assert summary1.total_chunks == 2
    
    summary2 = kb.add_dataframe(
        canonical_df=df2,
        source_meta={"source_file": "supplies_inventory.csv", "source_connector": "CSVConnector"},
        domain="pharmacy",
        file_id="supplies_file_2"
    )
    assert summary2.total_chunks == 2
    assert kb.stats()["total_chunks"] == 4
    
    # 1. Search across ALL files
    all_res = kb.search("thermometer", top_k=5)
    assert any("Thermometer" in r.text for r in all_res)
    
    # 2. Search SCOPED to meds file (should NOT find thermometer)
    scoped_res = kb.search("thermometer", top_k=5, file_ids=["meds_file_1"])
    assert not any("Thermometer" in r.text for r in scoped_res)
    
    # 3. Search SCOPED to supplies file (should find thermometer)
    scoped_res2 = kb.search("thermometer", top_k=5, file_ids=["supplies_file_2"])
    assert any("Thermometer" in r.text for r in scoped_res2)
    
    # 4. Un-ingest meds_file_1 cleanly
    kb.delete_source("meds_file_1")
    assert kb.stats()["total_chunks"] == 2
    
    # Verify meds chunks are gone while supplies chunks remain
    meds_check = kb.search("Paracetamol", top_k=5)
    assert not any("Paracetamol" in r.text for r in meds_check)
    
    supplies_check = kb.search("Thermometer", top_k=5)
    assert any("Thermometer" in r.text for r in supplies_check)
