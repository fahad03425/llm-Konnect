"""Module 6.4 — Document Ingestion and Knowledge Base.
Pharmacy-first, offline, CPU-embeddings.

First-run note: the embedding model is downloaded from HuggingFace Hub the very
first time `add_dataframe` or `search` is called. After that the model is cached
locally and the app runs fully offline. For air-gapped installs, point
`settings.embedding_model` at a local directory containing the model files.
"""

import os
import time
import hashlib
from typing import List, Dict, Any, Optional
import pandas as pd

from app.core.config import settings
from app.schema.domain import get_domain_pack
from app.ingestion.models import IngestSummary, RetrievedChunk

class KnowledgeBase:
    def __init__(self, chroma_dir: Optional[str] = None, collection_name: Optional[str] = None):
        self.chroma_dir = chroma_dir or settings.chroma_dir
        self.collection_name = collection_name or settings.collection_name
        self.embedding_model_name = settings.embedding_model
        
        # Lazy loaded
        self._chroma_client = None
        self._collection = None
        self._embedder = None
        
        # Determine prefix for e5 models
        self.query_prefix = "query: " if "e5" in self.embedding_model_name.lower() else ""
        self.passage_prefix = "passage: " if "e5" in self.embedding_model_name.lower() else ""

    def _get_chroma(self):
        if self._chroma_client is None:
            import chromadb
            os.makedirs(self.chroma_dir, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(path=self.chroma_dir)
            self._collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            # Load on CPU to save VRAM
            self._embedder = SentenceTransformer(self.embedding_model_name, device="cpu")
        return self._embedder
        
    def _sanitize_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Chroma requires metadata values to be str, int, float, or bool.
        
        FIX (BUG 1): check for list/array types BEFORE calling pd.isna(), because
        pd.isna() raises ValueError on multi-element arrays/lists.
        """
        import numpy as np
        clean = {}
        for k, v in meta.items():
            # Must check for non-scalar types first — pd.isna() raises ValueError on arrays/lists
            if isinstance(v, (list, np.ndarray)):
                clean[k] = str(v)
                continue
            if v is None:
                continue
            try:
                if pd.isna(v):
                    continue
            except (TypeError, ValueError):
                # Fallback: non-scalar that slipped through
                clean[k] = str(v)
                continue
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            elif isinstance(v, pd.Timestamp):
                clean[k] = v.isoformat()
            elif isinstance(v, (np.integer,)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean[k] = float(v)
            elif isinstance(v, np.bool_):
                clean[k] = bool(v)
            else:
                clean[k] = str(v)
        return clean
        
    def _generate_chunk_id(self, source_file: str, source_row, chunk_index: int) -> str:
        """Generate a deterministic chunk ID.
        
        FIX (BUG 2): always cast source_row to int so float 4.0 and int 4
        produce the same ID string, preventing duplicate chunks on re-ingest.
        """
        s = f"{source_file}_{int(source_row)}_{chunk_index}"
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def add_dataframe(
        self,
        canonical_df: pd.DataFrame,
        source_meta: dict,
        domain: str = "pharmacy",
        strategy: str = "row",
        merge_key: Optional[str] = None,
    ) -> IngestSummary:
        """
        Batched, idempotent upsert of canonical records into Chroma.

        strategy="row"   (default) — one record = one chunk.
        strategy="merge" — greedy, token-constrained merge: consecutive rows that
                           share the same merge_key (e.g. "invoice_id") are
                           concatenated into one chunk up to settings.chunk_size
                           tokens. Rows with different keys (or key=None) start a
                           new chunk. merge_key defaults to "invoice_id" when not
                           supplied. This is overlap-free by design.

        FIX (BUG 4): strategy="merge" is now implemented instead of silently
        falling back to "row".
        """
        start_time = time.time()
        
        if canonical_df.empty:
            return IngestSummary(
                total_chunks=0,
                source_file=source_meta.get("source_file", "unknown"),
                source_connector=source_meta.get("source_connector", "unknown"),
                domain=domain,
                time_taken_sec=0.0
            )

        pack = get_domain_pack(domain)
        collection = self._get_chroma()
        embedder = self._get_embedder()
        
        # Clean canonical_df NaNs
        canonical_df = canonical_df.where(pd.notnull(canonical_df), None)
        records = canonical_df.to_dict(orient="records")
        
        batch_size = 64
        total_chunks = 0
        
        source_file = source_meta.get("source_file", "unknown")
        
        # Prepare dates for derived metadata
        if "date" in canonical_df.columns:
            date_s = pd.to_datetime(canonical_df["date"], errors="coerce")
            years = date_s.dt.year
            months = date_s.dt.month
        else:
            years = pd.Series([None] * len(records))
            months = pd.Series([None] * len(records))
            
        if "expiry_date" in canonical_df.columns:
            exp_date_s = pd.to_datetime(canonical_df["expiry_date"], errors="coerce")
            exp_ym = exp_date_s.dt.strftime('%Y-%m')
        else:
            exp_ym = pd.Series([None] * len(records))

        def _build_row_meta(i: int, row: dict) -> dict:
            source_row = row.get("source_row", i + 1)
            derived_meta: dict = {}
            if pd.notna(years[i]):
                derived_meta["year"] = int(years[i])
            if pd.notna(months[i]):
                derived_meta["month"] = int(months[i])
            if pd.notna(exp_ym[i]):
                derived_meta["expiry_year_month"] = str(exp_ym[i])
            base_meta = {
                "source_file": source_file,
                "source_connector": source_meta.get("source_connector", "unknown"),
                "domain": domain,
                "ingested_at": source_meta.get("ingested_at", pd.Timestamp.now().isoformat()),
                "source_row": int(source_row),
            }
            for f in pack.filter_metadata_fields:
                if f in row and row[f] is not None and row[f] != "":
                    base_meta[f] = row[f]
            base_meta.update(derived_meta)
            return self._sanitize_metadata(base_meta)

        ids: List[str] = []
        texts: List[str] = []
        metadatas: List[dict] = []

        def _flush():
            nonlocal total_chunks
            if not ids:
                return
            embeddings = embedder.encode(texts, batch_size=batch_size, normalize_embeddings=True).tolist()
            collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
            total_chunks += len(ids)
            ids.clear(); texts.clear(); metadatas.clear()

        if strategy == "merge":
            # --- Greedy token-constrained merge strategy (FIX BUG 4) ---
            try:
                import tiktoken
                encoding = tiktoken.get_encoding("cl100k_base")
                count_tokens = lambda t: len(encoding.encode(t))
            except ImportError:
                # Fallback: rough word count
                count_tokens = lambda t: len(t.split())

            key_field = merge_key or "invoice_id"
            chunk_limit = settings.chunk_size

            current_texts: List[str] = []
            current_source_rows: List[int] = []
            current_token_count = 0
            current_group_key = object()  # sentinel
            chunk_group_idx = 0

            def _emit_merged_chunk():
                nonlocal chunk_group_idx
                if not current_texts:
                    return
                merged_text = " | ".join(current_texts)
                # Use first source_row of the group as the chunk's provenance row
                first_row_idx = current_source_rows[0]
                meta = {
                    "source_file": source_file,
                    "source_connector": source_meta.get("source_connector", "unknown"),
                    "domain": domain,
                    "ingested_at": source_meta.get("ingested_at", pd.Timestamp.now().isoformat()),
                    "source_row": first_row_idx,
                    "merged_rows": str(current_source_rows),
                }
                chunk_id = self._generate_chunk_id(source_file, first_row_idx, chunk_group_idx)
                ids.append(chunk_id)
                texts.append(self.passage_prefix + merged_text)
                metadatas.append(self._sanitize_metadata(meta))
                chunk_group_idx += 1
                current_texts.clear()
                current_source_rows.clear()

            for i, row in enumerate(records):
                row_text = pack.row_to_text(row)
                row_tokens = count_tokens(row_text)
                group_key = row.get(key_field)
                source_row_val = int(row.get("source_row", i + 1))

                # Start a new chunk if key changes OR adding would exceed limit
                if (
                    group_key != current_group_key
                    or (current_token_count + row_tokens) > chunk_limit
                ):
                    _emit_merged_chunk()
                    current_group_key = group_key
                    current_token_count = 0

                current_texts.append(row_text)
                current_source_rows.append(source_row_val)
                current_token_count += row_tokens

                if len(ids) >= batch_size:
                    _flush()

            _emit_merged_chunk()  # flush last group

        else:
            # --- Default: one record = one chunk (strategy="row") ---
            for i, row in enumerate(records):
                source_row = row.get("source_row", i + 1)
                clean_meta = _build_row_meta(i, row)
                text = pack.row_to_text(row)
                chunk_id = self._generate_chunk_id(source_file, source_row, 0)
                ids.append(chunk_id)
                texts.append(self.passage_prefix + text)
                metadatas.append(clean_meta)

                if len(ids) >= batch_size:
                    _flush()

        _flush()

        return IngestSummary(
            total_chunks=total_chunks,
            source_file=source_file,
            source_connector=source_meta.get("source_connector", "unknown"),
            domain=domain,
            time_taken_sec=time.time() - start_time
        )
        
    def add_text_documents(self, docs: List[str], source_meta: dict, domain: str = "pharmacy") -> IngestSummary:
        """
        Ingest free-text documents using recursive token splitting.
        """
        import tiktoken
        start_time = time.time()
        
        collection = self._get_chroma()
        embedder = self._get_embedder()
        
        encoding = tiktoken.get_encoding("cl100k_base")
        chunk_size = settings.chunk_size
        chunk_overlap = int(chunk_size * settings.chunk_overlap)
        
        source_file = source_meta.get("source_file", "unknown")
        
        ids = []
        texts = []
        metadatas = []
        
        total_chunks = 0
        batch_size = 64
        
        for doc_idx, doc_text in enumerate(docs):
            tokens = encoding.encode(doc_text)
            
            start = 0
            chunk_idx = 0
            while start < len(tokens) or len(tokens) == 0:
                end = min(start + chunk_size, len(tokens))
                chunk_tokens = tokens[start:end]
                if not chunk_tokens and len(tokens) > 0:
                    break
                
                chunk_text = encoding.decode(chunk_tokens) if chunk_tokens else doc_text
                
                meta = {
                    "source_file": source_file,
                    "source_connector": source_meta.get("source_connector", "text"),
                    "domain": domain,
                    "ingested_at": source_meta.get("ingested_at", pd.Timestamp.now().isoformat()),
                    "doc_index": doc_idx,
                    "chunk_index": chunk_idx,
                    "is_free_text": True
                }
                
                chunk_id = self._generate_chunk_id(source_file, doc_idx, chunk_idx)
                
                ids.append(chunk_id)
                texts.append(self.passage_prefix + chunk_text)
                metadatas.append(self._sanitize_metadata(meta))
                
                if len(tokens) == 0:
                    break
                    
                start += chunk_size - chunk_overlap
                chunk_idx += 1
                
                if len(ids) >= batch_size:
                    embeddings = embedder.encode(texts, batch_size=batch_size, normalize_embeddings=True).tolist()
                    collection.upsert(
                        ids=ids,
                        embeddings=embeddings,
                        documents=texts,
                        metadatas=metadatas
                    )
                    total_chunks += len(ids)
                    ids, texts, metadatas = [], [], []

        if ids:
            embeddings = embedder.encode(texts, batch_size=batch_size, normalize_embeddings=True).tolist()
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas
            )
            total_chunks += len(ids)

        return IngestSummary(
            total_chunks=total_chunks,
            source_file=source_file,
            source_connector=source_meta.get("source_connector", "text"),
            domain=domain,
            time_taken_sec=time.time() - start_time
        )

    def search(self, query: str, top_k: Optional[int] = None, filters: Optional[Dict[str, Any]] = None, domain: str = "pharmacy") -> List[RetrievedChunk]:
        """
        Filtered semantic search over the knowledge base.
        
        FIX (BUG 3): clamp top_k to the actual collection count. Chroma raises
        InvalidArgumentError when n_results > number of items in the index.
        """
        top_k = top_k or settings.retrieval_top_k
        collection = self._get_chroma()
        embedder = self._get_embedder()

        # Guard: Chroma crashes if n_results > collection count
        count = collection.count()
        if count == 0:
            return []
        top_k = min(top_k, count)
        
        query_text = self.query_prefix + query
        query_embedding = embedder.encode([query_text], normalize_embeddings=True).tolist()[0]
        
        where_clause = filters if filters else None
        
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_clause,
            include=["documents", "metadatas", "distances"]
        )
        
        retrieved = []
        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0]
            
            for doc, meta, dist in zip(docs, metas, distances):
                # Remove passage prefix if present for clean display
                clean_text = doc
                if self.passage_prefix and clean_text.startswith(self.passage_prefix):
                    clean_text = clean_text[len(self.passage_prefix):]
                    
                # Cosine distance to similarity (1 - distance)
                # Ensure sim is between 0 and 1
                sim = max(0.0, 1.0 - dist)
                source_row = meta.get("source_row")
                
                retrieved.append(RetrievedChunk(
                    text=clean_text,
                    metadata=meta,
                    score=sim,
                    source_row=source_row
                ))
                
        return retrieved

    def delete_source(self, source_file: str):
        """Remove all chunks from a specific source file."""
        collection = self._get_chroma()
        collection.delete(where={"source_file": source_file})

    def reset(self):
        """Delete and recreate the collection."""
        if self._chroma_client:
            self._chroma_client.delete_collection(self.collection_name)
            self._collection = None

    def stats(self) -> Dict[str, Any]:
        """Return KB statistics."""
        collection = self._get_chroma()
        count = collection.count()
        return {
            "total_chunks": count,
            "collection_name": self.collection_name
        }
