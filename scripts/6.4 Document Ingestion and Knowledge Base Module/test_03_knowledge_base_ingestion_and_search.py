"""Test Suite 03: Knowledge Base Ingestion, Vector Search & Retrieval.

Module: Module 6.4 — Document Ingestion and Knowledge Base Module
Target File: backend/app/ingestion/store.py
Scope:
- Verifies add_dataframe pipeline (canonical DataFrame conversion, embedding, and Chroma indexing).
- Verifies semantic search (search) using natural language business queries and cosine scoring.
- Verifies structured metadata filtering (product_id, domain, invoice_id).
- Verifies get_dataframe round-trip extraction of canonical data from Chroma metadata.
- Verifies delete_source cleanup removing chunks from the vector database.
"""

import pandas as pd
import pytest
from app.ingestion.store import KnowledgeBase


class TestKnowledgeBaseIngestionAndSearch:
    """Verifies end-to-end vector indexing, semantic search, and metadata filtering."""

    @pytest.fixture
    def sample_pharmacy_df(self):
        """Constructs a clean canonical pharmacy DataFrame."""
        return pd.DataFrame({
            "date": [pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-02")],
            "invoice_id": ["INV-101", "INV-102"],
            "product_id": ["Panadol 500mg Tablets", "Brufen 400mg Tablets"],
            "generic_name": ["Paracetamol", "Ibuprofen"],
            "batch_no": ["B1029", "B5542"],
            "quantity": [10.0, 5.0],
            "unit_price": [35.0, 75.0],
            "amount": [350.0, 375.0],
            "source_connector": ["CSVConnector", "CSVConnector"],
            "source_row": [2, 3]
        })

    def test_add_dataframe_ingests_and_indexes_records(self, isolated_kb, sample_pharmacy_df):
        """Ingests canonical records, embeds them locally, and persists into ChromaDB."""
        source_meta = {
            "source_file": "C:/data/pharmacy_pos_march.csv",
            "source_connector": "CSVConnector"
        }

        summary = isolated_kb.add_dataframe(
            sample_pharmacy_df,
            source_meta=source_meta,
            domain="pharmacy",
            strategy="row",
            file_id="file_pharma_01"
        )

        assert summary.total_chunks == 2
        assert summary.domain == "pharmacy"
        assert summary.file_id == "file_pharma_01"
        assert summary.time_taken_sec >= 0.0

        # Direct ChromaDB verification
        coll = isolated_kb._get_chroma()
        assert coll.count() == 2

    def test_semantic_search_retrieves_relevant_records(self, isolated_kb, sample_pharmacy_df):
        """Performs vector search using natural language concept query and retrieves nearest neighbor."""
        source_meta = {
            "source_file": "C:/data/pharmacy_pos_march.csv",
            "source_connector": "CSVConnector"
        }
        isolated_kb.add_dataframe(
            sample_pharmacy_df,
            source_meta=source_meta,
            domain="pharmacy",
            file_id="file_pharma_01"
        )

        # Search for fever and pain relief medication (should match Panadol / Paracetamol)
        results = isolated_kb.search("fever pain relief tablets", top_k=2, domain="pharmacy")

        assert len(results) > 0
        top_match = results[0]
        assert top_match.score > 0.50
        # Text or metadata includes product identity
        assert "Panadol" in top_match.text or top_match.metadata.get("product_id") == "Panadol 500mg Tablets"

    def test_metadata_filtering(self, isolated_kb, sample_pharmacy_df):
        """Restricts vector search results using structured metadata filters."""
        source_meta = {
            "source_file": "C:/data/pharmacy_pos_march.csv",
            "source_connector": "CSVConnector"
        }
        isolated_kb.add_dataframe(
            sample_pharmacy_df,
            source_meta=source_meta,
            domain="pharmacy",
            file_id="file_pharma_01"
        )

        # Filter strictly for Brufen product_id
        results = isolated_kb.search(
            "tablets",
            top_k=5,
            filters={"product_id": "Brufen 400mg Tablets"},
            domain="pharmacy"
        )

        assert len(results) == 1
        assert results[0].metadata.get("product_id") == "Brufen 400mg Tablets"
        assert "Brufen" in results[0].text

    def test_get_dataframe_reconstructs_structured_data(self, isolated_kb, sample_pharmacy_df):
        """Extracts structured DataFrame back from Chroma metadata attributes."""
        source_meta = {
            "source_file": "C:/data/pharmacy_pos_march.csv",
            "source_connector": "CSVConnector"
        }
        isolated_kb.add_dataframe(
            sample_pharmacy_df,
            source_meta=source_meta,
            domain="pharmacy",
            file_id="file_pharma_01"
        )

        extracted_df = isolated_kb.get_dataframe(file_ids=["file_pharma_01"])

        assert not extracted_df.empty
        assert len(extracted_df) == 2
        # Check canonical columns survived in metadata extraction
        assert "product_id" in extracted_df.columns
        assert set(extracted_df["product_id"]) == {"Panadol 500mg Tablets", "Brufen 400mg Tablets"}

    def test_delete_source_removes_records(self, isolated_kb, sample_pharmacy_df):
        """Deletes all chunks associated with a source file or file_id."""
        source_meta = {
            "source_file": "C:/data/pharmacy_pos_march.csv",
            "source_connector": "CSVConnector"
        }
        isolated_kb.add_dataframe(
            sample_pharmacy_df,
            source_meta=source_meta,
            domain="pharmacy",
            file_id="file_pharma_01"
        )

        coll = isolated_kb._get_chroma()
        assert coll.count() == 2

        # Delete by source path
        isolated_kb.delete_source("C:/data/pharmacy_pos_march.csv")
        assert coll.count() == 0

        # Query returns empty
        results = isolated_kb.search("Panadol", top_k=2, domain="pharmacy")
        assert len(results) == 0
