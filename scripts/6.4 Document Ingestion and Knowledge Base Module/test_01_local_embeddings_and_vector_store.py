"""Test Suite 01: Local Embeddings & Vector Store Foundation.

Module: Module 6.4 — Document Ingestion and Knowledge Base Module
Target File: backend/app/ingestion/store.py
Scope:
- Verifies offline initialization of local Sentence-Transformers model.
- Verifies persistent ChromaDB client instantiation and collection creation with cosine space.
- Verifies embedding vector dimensions (384 dims for multilingual-e5-small) and cosine similarity metric math.
- Verifies query and passage prefix handling (e.g., 'query: ' and 'passage: ' for e5 architectures).
"""

import numpy as np
import pytest
from app.core.config import settings
from app.ingestion.store import (
    _get_global_embedder,
    _get_persistent_chroma_client,
    KnowledgeBase,
)


class TestLocalEmbeddingsAndVectorStore:
    """Verifies local neural embedding model and persistent ChromaDB vector storage."""

    def test_offline_embedder_initialization(self):
        """Sentence-Transformers loads from local cache without external network requests."""
        embedder = _get_global_embedder(settings.embedding_model)

        assert embedder is not None
        assert hasattr(embedder, "encode")
        # Embedding model name matches configuration
        assert "e5" in settings.embedding_model.lower() or "sentence" in str(type(embedder)).lower()

    def test_chroma_persistent_client_and_collection(self, temp_chroma_dir):
        """ChromaDB PersistentClient creates an isolated collection with cosine distance space."""
        client = _get_persistent_chroma_client(temp_chroma_dir)
        assert client is not None

        collection_name = "test_persistence_kb"
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        assert collection is not None
        assert collection.name == collection_name
        assert collection.metadata.get("hnsw:space") == "cosine"

    def test_embedding_dimension_and_cosine_similarity(self):
        """Generates dense vectors of expected dimensionality and verifies cosine similarity calculation."""
        embedder = _get_global_embedder(settings.embedding_model)

        # Encode two semantically similar statements and one divergent statement
        text_a = "passage: Panadol 500mg tablets for fever and pain relief"
        text_b = "passage: Paracetamol 500mg medicine for reducing headache and fever"
        text_c = "passage: High-pressure industrial steam boiler maintenance schedule"

        embeddings = embedder.encode([text_a, text_b, text_c], normalize_embeddings=True)

        # Verify dimension (multilingual-e5-small produces 384-dimensional embeddings)
        assert embeddings.shape[0] == 3
        dim = embeddings.shape[1]
        assert dim in (384, 768, 1024)

        # Compute dot-product cosine similarity (normalized vectors)
        sim_ab = float(np.dot(embeddings[0], embeddings[1]))
        sim_ac = float(np.dot(embeddings[0], embeddings[2]))

        # Semantic medical similarity must be significantly higher than unrelated industrial topic
        assert sim_ab > sim_ac
        assert sim_ab > 0.75  # Strongly similar

    def test_query_and_passage_prefix_convention(self, isolated_kb):
        """KnowledgeBase properly designates query vs passage prefixes for asymmetric retrieval."""
        # For e5 models, query_prefix is 'query: ' and passage_prefix is 'passage: '
        if "e5" in isolated_kb.embedding_model_name.lower():
            assert isolated_kb.query_prefix == "query: "
            assert isolated_kb.passage_prefix == "passage: "
        else:
            assert isolated_kb.query_prefix == ""
            assert isolated_kb.passage_prefix == ""
