# Test Suite Report 01: Local Embeddings & Vector Store Foundation

## Overview
- **Module**: Module 6.4 — Document Ingestion and Knowledge Base Module
- **Target File**: `backend/app/ingestion/store.py`
- **Component**: Sentence-Transformers Embedder & ChromaDB Persistent Vector Store

## Scope & Architectural Verification
This test suite verifies the foundational vector embedding and storage infrastructure:
1. **Offline Model Initialization**: Validates that `SentenceTransformer` loads directly from local weights in `data/` or HuggingFace local cache (`local_files_only=True`) without making outbound network requests or requiring external API keys.
2. **Persistent ChromaDB Client**: Validates that `_get_persistent_chroma_client` initializes an on-disk embedded Chroma database configured with the HNSW cosine distance metric space (`{"hnsw:space": "cosine"}`).
3. **Embedding Vector Math**: Encodes natural language passages into dense normalized vectors (384 dimensions for `intfloat/multilingual-e5-small`) and validates semantic similarity via cosine dot products.
4. **Asymmetric E5 Prefixes**: Validates prefix assignment (`query: ` and `passage: `) to prevent performance degradation inherent in asymmetric bi-encoder architectures.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_offline_embedder_initialization` | Local Sentence-Transformers | Weights loaded from offline cache; `encode` method ready. |
| `test_chroma_persistent_client_and_collection` | Persistent Chroma Client | Isolated directory created with `cosine` distance metric. |
| `test_embedding_dimension_and_cosine_similarity` | Vector Dimensions & Similarity | 384-dim normalized vector; semantic similarity score > 0.70. |
| `test_query_and_passage_prefix_convention` | Asymmetric Retrieval Prefixes | Properly sets `query: ` and `passage: ` for e5 models. |
