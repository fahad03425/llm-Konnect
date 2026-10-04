# Test Suite Report 05: FastAPI End-to-End Ingestion, Search & Management Routes

## Overview
- **Module**: Module 6.4 — Document Ingestion and Knowledge Base Module
- **Target File**: `backend/app/api/kb.py`
- **Components**: FastAPI Knowledge Base API Router (`/api/kb/*`)

## Scope & Architectural Verification
This test suite verifies the public HTTP REST interfaces enabling the desktop and web clients to interface with the knowledge base:
1. **Full Ingestion Pipeline (`POST /api/kb/ingest`)**:
   - Detects input format using connector abstractions (`CSVConnector`).
   - Applies schema mapping and normalizes types.
   - Runs data validation to reject corrupt datasets.
   - Computes local dense vector embeddings and stores passages in ChromaDB.
   - Records metadata and file hash in SQLite `file_registry`.
2. **Semantic Search (`POST /api/kb/search`)**:
   - Accepts free-form natural language queries, `top_k`, and metadata filters.
   - Returns structured `RetrievedChunk` items with similarity scores.
3. **Source Inventory Inspection (`GET /api/kb/sources`)**:
   - Returns complete file catalog, indexing progress, and aggregate chunk counts.
4. **Cascading File Un-ingestion (`DELETE /api/kb/sources/{file_id}`)**:
   - Atomically purges all associated vector embeddings from the Chroma collection.
   - Deletes the file tracking record from SQLite.
5. **Robust Error Handling**:
   - Returns HTTP 404 on missing source files and HTTP 400 on corrupted/invalid mappings.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_ingest_source_endpoint_success` | Full HTTP Ingestion Pipeline | Ingests CSV; returns 2 total chunks and assigned file_id. |
| `test_search_endpoint_returns_semantic_results` | Vector Search Endpoint | Returns ranked results with text, score, and metadata. |
| `test_list_sources_endpoint` | Source Catalog Listing | Returns files list, total_files count, total_chunks. |
| `test_delete_source_by_id_endpoint` | Cascading Un-ingestion | Purges vector chunks and SQLite record; returns success. |
| `test_ingest_missing_file_returns_404` | Error Handling (404) | Non-existent file path correctly yields HTTP 404. |
