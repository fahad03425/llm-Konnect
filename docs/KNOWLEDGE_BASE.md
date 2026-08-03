# Knowledge Base Architecture (Module 6.4)

The Knowledge Base in LLM-Konnect handles document ingestion and semantic search. 
Crucially, **retrieval finds records; it never computes aggregates**. Analytics are handled separately (Module 6.6).

## 1. Chunking Strategy
- **Row-Oriented**: Data is primarily structured rows (e.g., pharmacy transactions). Naive chunking is inappropriate here. One logical record becomes one chunk by default.
- **Serialization**: Each row is converted into a natural-language sentence (e.g., `"Sale on 2026-01-05: 20 units of Panadol..."`). The active `DomainPack` determines the serialization rules.
- **Free-Text Path**: Genuine documents (e.g., PDF text) use recursive token splitting (default 512 tokens with 15% overlap) via `tiktoken`.

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
- Ingestion is idempotent: chunk IDs are generated deterministically via hashing (`source_file + source_row + chunk_index`).
- Re-ingesting a file upserts the data instead of duplicating it, supporting iterative data correction.
