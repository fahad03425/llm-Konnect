# Knowledge Base Architecture (Module 6.4)

The Knowledge Base in LLM-Konnect handles document ingestion and semantic search. 
Crucially, **retrieval finds records; it never computes aggregates**. Analytics are handled separately (Module 6.6).

## 1. Chunking Strategy
- **Row-Oriented**: Data is primarily structured rows (e.g., pharmacy transactions). Naive chunking is inappropriate here. Short logical records become one chunk. Long records are split into overlapping windows that fit the embedding model.
- **Serialization**: Each row is converted into a natural-language sentence (e.g., `"Sale on 2026-01-05: 20 units of Panadol..."`). The active `DomainPack` determines the serialization rules.
- **Free-Text Path**: Genuine documents (e.g., PDF text) use the embedding model's own tokenizer with 15% overlap. The 512-token setting is capped by the model input limit and reserves room for prefixes and special tokens. This internal text method expects extracted strings; document extraction is outside these fixes.

## 2. Metadata Split
Retrieval is made precise through rich metadata filtering. The `DomainPack` divides canonical fields into:
- **Searchable Text**: Core entity names and descriptions (e.g., generic name, manufacturer) that are embedded into the semantic vector.
- **Filter Metadata**: Exact matches and aggregators (e.g., date, txn_type, amount, product_id) stored as Chroma metadata to power strict SQL-like `where` filters.

## 3. Embeddings & e5 Conventions
- Embeddings are generated locally on CPU using `intfloat/multilingual-e5-small`.
- This ensures offline functionality and spares VRAM for the LLM.
- **First-run download**: The system requires an internet connection on its very first run to download the model into the cache. Subsequent runs are fully offline.
- e5 models require `"passage: "` prefix for ingested chunks and `"query: "` prefix for queries, which the `KnowledgeBase` applies automatically.

## 4. Idempotency & Storage
- The storage backend is a local file-based Chroma DB (`chromadb.PersistentClient`).
- Ingestion is idempotent: chunk IDs are generated deterministically via hashing with a domain/source identity and a distinct window index.
- Re-ingesting a file upserts the data instead of duplicating it, supporting iterative data correction.


## 5. Retrieval and complete financial records

Every chunk has its own identity, so different windows from one document or row
remain distinct during retrieval. Older text chunks use their document and chunk
indices as a compatibility fallback. Financial calculations recover complete
structured rows rather than adding up the ranked search results.

Merged invoice chunks store every original row in an encrypted JSON list. Reading
structured data expands that list and removes repeated rows introduced by overlap.
Legacy merged chunks containing only the first row are flagged as incomplete and
need re-importing; their missing original values cannot be reconstructed reliably.

## 6. Protected replacement and deletion

The file import APIs prepare embeddings in a temporary local collection. Only
successful preparation starts publication. A local backup holds the previous
records during publication, and a shared collection lock protects application
search and structured reads from intermediate results. An exception or cancellation
rolls back publication; successful replacement removes stale chunks. SQL table and
live updates prepare only changed vectors and remove old windows after preparation.

Source deletion uses the exact file ID or path. Registry deletion permits a bare
filename only when it resolves to one source. Files with the same basename in
other directories are preserved. A cancelled replacement retains the previous
index and its active registry entry.

This protection handles errors during a running backend process. Chroma does not
provide a multi-batch transaction across an operating-system crash or power loss.
New chunking and merged payload improvements apply when sources are re-imported;
existing data is not automatically rebuilt.

Regression coverage: `backend/tests/test_ingestion_safety.py` uses real local
Sentence-Transformers and isolated Chroma collections to check distinct retrieval,
full invoice totals, English/Urdu/Chinese token limits, long-record coverage,
replacement rollback, cancellation, deletion isolation and SQL synchronization.
