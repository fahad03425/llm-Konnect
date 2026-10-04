# Test Suite Report 03: Knowledge Base Ingestion, Vector Search & Retrieval

## Overview
- **Module**: Module 6.4 — Document Ingestion and Knowledge Base Module
- **Target File**: `backend/app/ingestion/store.py`
- **Components**: `KnowledgeBase`, ChromaDB Collection, Semantic Retrieval Engine

## Scope & Architectural Verification
This test suite verifies the end-to-end vector indexing and retrieval lifecycle:
1. **Canonical DataFrame Ingestion (`add_dataframe`)**: Transforms structured rows into localized natural language passages via `DomainPack.row_to_text`, generates offline dense embeddings, and writes chunks into the Chroma collection with full metadata attributes.
2. **Natural Language Semantic Search (`search`)**: Encodes user search queries using the asymmetric `query: ` prefix, computes cosine similarity against stored passages, and ranks results.
3. **Structured Metadata Filtering**: Restricts retrieval queries by exact metadata matches (e.g. `product_id`, `domain`, `invoice_id`), guaranteeing precision when scoping answers to specific products or timeframes.
4. **Structured DataFrame Extraction (`get_dataframe`)**: Reads back complete structured records from Chroma metadata, supporting hybrid analytics without re-reading the source disk files.
5. **Source Deletion & Garbage Collection (`delete_source`)**: Prunes all vector chunks belonging to a designated source file, ensuring complete data lifecycle cleanliness.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_add_dataframe_ingests_and_indexes_records` | DataFrame Ingestion Pipeline | Ingests 2 canonical records; Chroma count equals 2. |
| `test_semantic_search_retrieves_relevant_records` | Semantic Vector Search | "fever pain relief" retrieves Panadol record with score > 0.50. |
| `test_metadata_filtering` | Structured Metadata Filters | Strict filtering by `product_id` isolates targeted record. |
| `test_get_dataframe_reconstructs_structured_data` | Reverse DataFrame Reconstruction | Reconstructs DataFrame from Chroma metadata attributes. |
| `test_delete_source_removes_records` | Source Removal & Cleanup | Purges vector chunks from collection; count drops to 0. |
