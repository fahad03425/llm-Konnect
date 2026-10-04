# Module 6.4: Document Ingestion and Knowledge Base Module — Architecture & File Manifest

## Executive Summary
The **Document Ingestion and Knowledge Base Module (Module 6.4)** provides an offline-first, private semantic search and knowledge retrieval foundation for LLM-Konnect. It chunks ingested financial and inventory records, generates local dense vector embeddings using **Sentence-Transformers** (multilingual E5 architecture), and persists them into a local, file-based **Chroma** vector database (`chromadb.PersistentClient`).

### Core Design Principles
1. **100% Offline & Private**: Embeddings are generated locally on CPU or CUDA via `SentenceTransformer(..., local_files_only=True)`. Zero data or telemetry is transmitted to external embedding APIs.
2. **Deterministic & Deduplicated**: Chunks receive deterministic IDs derived from source file identity, row index, and window sequence, preventing duplicate entries on re-ingestion.
3. **Domain-Aware Structured Row Representation**: Converts structured canonical rows into rich natural language passages using domain packs (e.g. `PharmacyDomainPack.row_to_text`), enabling semantic search over both keywords and conceptual meanings.
4. **Token-Safe Windowing**: Implements character-span-preserving chunk windowing that respects embedding model token budgets and prefix overheads without truncating crucial data.
5. **Thread-Safe & Protected Atomic Updates**: Wraps collection operations in thread locks (`@locked`) and utilizes shadow collections (`replace_source`) to ensure rollback protection on ingestion failure.

---

## Module Files & Responsibilities

| File Path | Component | Architectural Role | Key Functions / Classes |
| :--- | :--- | :--- | :--- |
| [`backend/app/ingestion/store.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/ingestion/store.py) | **KnowledgeBase Core** | Vector store management, local embedder lifecycle, dataframe/document ingestion, and semantic retrieval. | `KnowledgeBase`, `_get_persistent_chroma_client`, `_get_global_embedder`, `add_dataframe`, `add_document`, `search`, `delete_source`, `get_dataframe` |
| [`backend/app/ingestion/safety.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/ingestion/safety.py) | **Ingestion Safety & Chunker** | Token windowing chunker, thread-safe collection locking, and atomic publication with rollback protection. | `token_windows`, `locked`, `collection_lock`, `replace_source`, `chunk_identity` |
| [`backend/app/ingestion/models.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/ingestion/models.py) | **Pydantic Data Models** | Schema definitions for ingestion statistics and retrieved vector chunk payloads. | `IngestSummary`, `RetrievedChunk` |
| [`backend/app/ingestion/registry.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/ingestion/registry.py) | **File Registry Store** | SQLite-backed metadata catalog tracking ingested files, SHA-256 deduplication hashes, and progressive statuses. | `FileRegistry`, `FileRecord`, `DBConnectionRecord`, `compute_file_hash`, `set_file_status` |
| [`backend/app/ingestion/sync_worker.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/ingestion/sync_worker.py) | **Database Sync Daemon** | Background daemon synchronizing live SQL database changes into ChromaDB without blocking request threads. | `KBSyncWorker`, `_generate_chunk_id`, `sync_table`, `sync_database` |
| [`backend/app/api/kb.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/api/kb.py) | **REST API Router** | Exposes HTTP endpoints for full ingestion pipelines, semantic search, and file registry management. | `/api/kb/ingest`, `/api/kb/search`, `/api/kb/files`, `/api/kb/files/{file_id}`, `/api/kb/clear` |

---

## Detailed Component Specifications

### 1. `store.py` (`KnowledgeBase`)
- **Embedding Model Initialization**:
  - Model: `intfloat/multilingual-e5-small` (configured in `settings.embedding_model`).
  - Device: Auto-selects CUDA if available (`_is_cuda_available()`), otherwise CPU.
  - Prefix Handling: E5 models require `passage: ` for indexed documents and `query: ` for user search queries to ensure maximum retrieval performance.
- **ChromaDB Client**:
  - `chromadb.PersistentClient(path=abs_path)` maintains persistent on-disk storage in `data/chroma`.
  - HNSW Space: Configured for `"hnsw:space": "cosine"` metric distance.
- **Ingestion Strategies**:
  - `"row"`: Default strategy. Each row of the canonical DataFrame is transformed into a natural language text passage via the active domain pack and embedded as a single chunk.
  - `"document"`: Multi-page textual documents or markdown reports are split into token-safe overlapping windows using `token_windows`.
  - `"grouped"`: Aggregates records by an entity key (e.g. `invoice_id` or `product_id`) into unified contextual passages.
- **Search & Retrieval**:
  - Encodes the search query with the appropriate prefix (`query: `).
  - Queries Chroma collection using vector embeddings, returning top-k nearest neighbors.
  - Converts cosine distance to similarity score: `score = 1.0 - (dist / 2.0)`.
  - Supports structured metadata filtering: `{"domain": "pharmacy", "product_id": "Panadol"}`.
  - Metadata Sanitization: Coerces booleans, removes complex nested objects, and preserves numbers/strings for Chroma compatibility.

### 2. `safety.py` (Token Safety & Locking)
- **`token_windows`**:
  - Takes raw text, an embedder instance, a prefix, max window size, and overlap ratio.
  - Accounts for tokenizer overhead (special tokens + prefix tokens).
  - Employs offset mappings to ensure no words or character boundaries are split arbitrarily.
- **`@locked`**:
  - Process-wide reentrant locks (`RLock`) keyed by `(chroma_dir, collection_name)`.
  - Prevents race conditions during simultaneous reads/writes across asynchronous worker threads.
- **`replace_source`**:
  - Ingests new records into a shadow temporary collection first.
  - If embedding or storage fails, the active collection remains completely untouched and valid.
  - Upon success, swaps or updates the target records atomically.

### 3. `registry.py` (`FileRegistry`)
- **SQLite Database**: `data/file_registry.sqlite3` with WAL journal mode (`PRAGMA journal_mode=WAL;`).
- **Deduplication**:
  - Computes SHA-256 hash of file contents in 64KB blocks.
  - Checks if an identical file hash has already been indexed; prevents redundant model encoding.
- **Progressive Lifecycle Tracking**:
  - Tracks file states: `pending` $\rightarrow$ `vectorizing` $\rightarrow$ `indexed` (or `error`).
  - Stores chunk counts, file size in bytes, and execution timestamps.
- **Live Database Tracking**:
  - Tracks live database connection strings, sync intervals, and table watermarks.

### 4. `kb.py` (API Endpoints)
- `POST /api/kb/ingest`:
  - Receives `file_path`, domain, and optional manual mapping.
  - Detects connector (`detect_connector`), maps headers (`map_headers`), normalizes data (`apply_mapping`), validates schema (`validate`), and writes to `KnowledgeBase`.
  - Registers the file in `FileRegistry` upon successful completion.
- `POST /api/kb/search`:
  - Executes vector semantic search, accepts query text, `top_k`, and metadata filter parameters.
  - Returns `List[RetrievedChunk]` with text, metadata, similarity score, and source row index.
- `GET /api/kb/files`:
  - Returns the list of all registered source files, chunk counts, and indexing statuses.
- `DELETE /api/kb/files/{file_id}`:
  - Cascading deletion: removes all associated chunks from ChromaDB and deletes the registry entry.
- `POST /api/kb/clear`:
  - Clears all collections and resets the knowledge base.

---

## Test Verification Scope

The test suite inside this folder verifies:
1. **Local Neural Embeddings**: Sentence-Transformers model caching, offline loading, and cosine vector space math.
2. **Chunking & Token Safety**: `token_windows` token bounds, prefix overhead handling, and deterministic chunk ID generation.
3. **Ingestion & Search Engine**: Canonical DataFrame ingestion, natural language vector retrieval, metadata filtering, and round-trip DataFrame extraction.
4. **File Registry & Deduplication**: SQLite state management, SHA-256 hash deduplication, progressive status tracking, and cascading deletion.
5. **FastAPI Endpoints**: Full HTTP ingestion, vector search, file deletion, and input validation handling.
