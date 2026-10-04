# Test Suite Report 04: File Registry, Deduplication & Ingestion State Tracking

## Overview
- **Module**: Module 6.4 — Document Ingestion and Knowledge Base Module
- **Target File**: `backend/app/ingestion/registry.py`
- **Components**: `FileRegistry`, SQLite Catalog, Hash Caching, DB Connection Registry

## Scope & Architectural Verification
This test suite verifies the SQLite metadata catalog that manages all ingested files and active database connections:
1. **Database Schema Initialization**: Verifies creation and backward-compatible schema migration of `file_registry`, `file_hash_cache`, and `db_connections` tables with WAL journal mode.
2. **SHA-256 File Content Hashing**: Validates 64-character SHA-256 calculation and verifies persistent disk-and-memory caching to bypass redundant disk reads.
3. **Progressive Status Lifecycle Tracking**: Tracks status transitions (`pending` $\rightarrow$ `processing` $\rightarrow$ `active`) with percentage progress indicators (`progress`) and human-readable step text (`step_text`).
4. **Duplicate Content Rejection**: Verifies `get_file_by_hash` detection, ensuring that identical files uploaded under different filenames or locations are immediately recognized.
5. **Database Connection Watermarking**: Verifies tracking of live database configurations and incremental sync watermarks (`update_db_watermarks`) for live table sync daemons.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_registry_db_schema_initialization` | Schema Creation | Creates `file_registry`, `file_hash_cache`, `db_connections`. |
| `test_sha256_file_hash_calculation_and_caching` | SHA-256 & Cache | Computes accurate 64-char hash; subsequent call hits cache. |
| `test_file_registration_and_status_progression` | Status Lifecycle | Transitions from registration to processing with progress %. |
| `test_duplicate_content_detection` | Deduplication Detection | Resolves identical content hash to registered source record. |
| `test_file_deletion_and_cleanup` | Record Cleanup | Deletes file record; confirms removal from SQLite catalog. |
| `test_db_connection_registration_and_watermarks` | Live DB Watermarks | Stores database connection and updates table sync timestamp. |
