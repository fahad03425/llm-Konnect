# Test Suite Report 02: Chunking Strategies, Token Safety & Metadata Sanitization

## Overview
- **Module**: Module 6.4 — Document Ingestion and Knowledge Base Module
- **Target Files**: `backend/app/ingestion/safety.py` & `backend/app/ingestion/store.py`
- **Components**: Token Windowing Chunker (`token_windows`), Chunk ID Engine, Metadata Sanitizer

## Scope & Architectural Verification
This test suite verifies chunking algorithms and token safety constraints:
1. **Character-Span Token Windowing**: Tests `token_windows` against long documents. Verifies that windows are partitioned according to the embedding model's token limits and specified window budget while accounting for prefix overhead (e.g. `passage: `).
2. **Bounds & Parameter Validation**: Tests strict input validation ensuring `chunk_size > 0` and `0 <= chunk_overlap < 1.0`, raising descriptive `ValueError` exceptions on out-of-bound inputs.
3. **Deterministic Chunk Identification**: Verifies `_generate_chunk_id` in `store.py`. Enforces deterministic chunk hashing (`{source_file}_{source_row}_{chunk_index}`) to guarantee idempotency across re-ingestion passes.
4. **ChromaDB Metadata Sanitization**: Tests `_sanitize_metadata` verifying that complex nested types, lists, and booleans are coerced into Chroma-compliant primitives (strings, floats, ints, bools) without runtime exceptions.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_token_windows_splits_long_text_within_budget` | Token Safety Windowing | Text cleanly partitioned into overlapping windows within token limit. |
| `test_token_windows_validates_chunk_parameters` | Input Parameter Bounds | Raises `ValueError` for `size <= 0` or `overlap` outside `[0, 1)`. |
| `test_deterministic_chunk_id_generation` | Chunk ID Determinism | Stable, idempotent IDs derived from file, row, and window index. |
| `test_metadata_sanitization_for_chromadb` | Metadata Sanitization | Sanitizes nested structures and primitives for ChromaDB compatibility. |
