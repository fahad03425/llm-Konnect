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
import threading
from typing import List, Dict, Any, Optional, Callable
import pandas as pd
import torch

from app.core.config import settings, get_default_domain
from app.schema.domain import get_domain_pack
from app.ingestion.models import IngestSummary, RetrievedChunk
from app.security.crypto import encrypt_string, decrypt_string

_global_chroma_clients: Dict[str, Any] = {}
_chroma_init_lock = threading.Lock()

def _get_persistent_chroma_client(chroma_path: str):
    abs_path = os.path.abspath(chroma_path)
    if abs_path not in _global_chroma_clients:
        with _chroma_init_lock:
            if abs_path not in _global_chroma_clients:
                import chromadb
                os.makedirs(abs_path, exist_ok=True)
                _global_chroma_clients[abs_path] = chromadb.PersistentClient(path=abs_path)
    return _global_chroma_clients[abs_path]


_global_embedders: Dict[str, Any] = {}
_embedder_lock = threading.Lock()

def _get_global_embedder(model_name: str, progress_callback: Optional[Callable[[float, str], None]] = None):
    if model_name not in _global_embedders:
        with _embedder_lock:
            if model_name not in _global_embedders:
                if progress_callback:
                    progress_callback(32.0, "Loading local neural embedding model...")
                import os
                import torch
                from sentence_transformers import SentenceTransformer
                device = "cuda" if torch.cuda.is_available() else "cpu"
                try:
                    # Try instant offline loading from local cache first
                    _global_embedders[model_name] = SentenceTransformer(model_name, device=device, local_files_only=True)
                except Exception:
                    # Fall back to online download if not cached yet
                    try:
                        os.environ.pop("HF_HUB_OFFLINE", None)
                        os.environ.pop("TRANSFORMERS_OFFLINE", None)
                        _global_embedders[model_name] = SentenceTransformer(model_name, device=device)
                    finally:
                        os.environ["HF_HUB_OFFLINE"] = "1"
                        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return _global_embedders[model_name]


class KnowledgeBase:
    def __init__(self, chroma_dir: Optional[str] = None, collection_name: Optional[str] = None):
        self.chroma_dir = chroma_dir or settings.chroma_dir
        self.collection_name = collection_name or settings.collection_name
        self.embedding_model_name = settings.embedding_model
        
        # Lazy loaded
        self._chroma_client = None
        self._collection = None
        
        # Determine prefix for e5 models
        self.query_prefix = "query: " if "e5" in self.embedding_model_name.lower() else ""
        self.passage_prefix = "passage: " if "e5" in self.embedding_model_name.lower() else ""

    def _get_chroma(self):
        if self._collection is None:
            client = _get_persistent_chroma_client(self.chroma_dir)
            self._chroma_client = client
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    def _get_embedder(self, progress_callback: Optional[Callable[[float, str], None]] = None):
        return _get_global_embedder(self.embedding_model_name, progress_callback)
        
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
        domain: Optional[str] = None,
        strategy: str = "row",
        merge_key: Optional[str] = None,
        file_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> IngestSummary:
        domain = domain or get_default_domain()
        """
        Batched, idempotent upsert of canonical records into Chroma with live progress reporting and cancellation support.

        strategy="row"   (default) — one record = one chunk.
        strategy="merge" — greedy, token-constrained merge
        """
        start_time = time.time()
        
        source_file = source_meta.get("source_file", "unknown")
        filename = os.path.basename(source_file)
        
        if canonical_df.empty:
            return IngestSummary(
                total_chunks=0,
                source_file=source_file,
                source_connector=source_meta.get("source_connector", "unknown"),
                domain=domain,
                time_taken_sec=0.0,
                file_id=file_id
            )

        if cancel_check and cancel_check():
            raise InterruptedError("Ingestion cancelled by user")

        if progress_callback:
            progress_callback(35.0, "Preparing records for vectorization...")

        pack = get_domain_pack(domain)
        collection = self._get_chroma()
        embedder = self._get_embedder(progress_callback)
        
        # Clean canonical_df NaNs
        canonical_df = canonical_df.where(pd.notnull(canonical_df), None)
        records = canonical_df.to_dict(orient="records")
        total_records = len(records)
        
        # Optimized batch sizes for high-throughput tensor encoding and database writing
        encode_batch_size = 512 if torch.cuda.is_available() else 256
        upsert_batch_size = 1024
        total_chunks = 0
        
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
                "filename": filename,
                "file_id": file_id or "",
                "source_connector": source_meta.get("source_connector", "unknown"),
                "domain": domain,
                "ingested_at": source_meta.get("ingested_at", pd.Timestamp.now().isoformat()),
                "source_row": int(source_row),
            }
            if "database_name" in source_meta:
                base_meta["database_name"] = str(source_meta["database_name"])
            if "table_name" in source_meta:
                base_meta["table_name"] = str(source_meta["table_name"])
            if "group_name" in source_meta:
                base_meta["group_name"] = str(source_meta["group_name"])
            if "source_type" in source_meta:
                base_meta["source_type"] = str(source_meta["source_type"])

            for f in pack.filter_metadata_fields:
                if f in row and row[f] is not None and row[f] != "":
                    base_meta[f] = row[f]
            base_meta.update(derived_meta)
            return self._sanitize_metadata(base_meta)

        ids: List[str] = []
        texts: List[str] = []
        metadatas: List[dict] = []

        def _flush(current_idx: int = 0):
            nonlocal total_chunks
            if not ids:
                return
            if cancel_check and cancel_check():
                raise InterruptedError("Ingestion cancelled by user")
            if progress_callback:
                pct = min(94.0, 35.0 + (58.0 * (current_idx / max(1, total_records))))
                progress_callback(pct, f"Vectorizing records: {current_idx}/{total_records} ({pct:.0f}%)...")
            embeddings = embedder.encode(
                texts,
                batch_size=encode_batch_size,
                show_progress_bar=False,
                normalize_embeddings=True
            ).tolist()
            if cancel_check and cancel_check():
                raise InterruptedError("Ingestion cancelled by user")
            docs_to_store = [encrypt_string(t) for t in texts] if getattr(settings, "encryption_enabled", True) else texts
            collection.upsert(ids=ids, embeddings=embeddings, documents=docs_to_store, metadatas=metadatas)
            total_chunks += len(ids)
            ids.clear(); texts.clear(); metadatas.clear()

        if strategy == "merge":
            # --- Greedy token-constrained merge strategy ---
            try:
                import tiktoken
                encoding = tiktoken.get_encoding("cl100k_base")
                count_tokens = lambda t: len(encoding.encode(t))
            except ImportError:
                count_tokens = lambda t: len(t.split())

            key_field = merge_key
            if not key_field and records:
                first_record_keys = {str(k).lower().strip(): k for k in records[0].keys()}
                for candidate in ["invoice_id", "invoice_no", "invoiceno", "bill_no", "bill_id", "billno", "order_id", "orderno", "voucher_no", "receipt_no", "transaction_id", "doc_no", "id"]:
                    if candidate in first_record_keys:
                        key_field = first_record_keys[candidate]
                        break
            if not key_field:
                key_field = "invoice_id"

            chunk_limit = settings.chunk_size

            current_texts: List[str] = []
            current_source_rows: List[int] = []
            current_group_rows: List[dict] = []
            current_token_count = 0
            current_group_key = object()
            chunk_group_idx = 0

            def _emit_merged_chunk():
                nonlocal chunk_group_idx
                if not current_texts:
                    return
                # Formulate unified chunk text
                if len(current_texts) == 1:
                    merged_text = current_texts[0]
                else:
                    merged_text = " | ".join(current_texts)
                
                first_row_idx = current_source_rows[0]
                first_row_dict = current_group_rows[0] if current_group_rows else {}
                
                meta = _build_row_meta(first_row_idx - 1, first_row_dict)
                meta["merged_rows"] = str(current_source_rows)
                meta["items_in_chunk"] = len(current_texts)
                meta["chunk_type"] = "merged"
                
                chunk_id = self._generate_chunk_id(source_file, first_row_idx, chunk_group_idx)
                ids.append(chunk_id)
                texts.append(self.passage_prefix + merged_text)
                metadatas.append(self._sanitize_metadata(meta))
                chunk_group_idx += 1
                current_texts.clear()
                current_source_rows.clear()
                current_group_rows.clear()

            for i, row in enumerate(records):
                if cancel_check and i % 50 == 0 and cancel_check():
                    raise InterruptedError("Ingestion cancelled by user")
                row_text = pack.row_to_text(row)
                row_tokens = count_tokens(row_text)
                group_key = row.get(key_field) if key_field else None
                source_row_val = int(row.get("source_row", i + 1))

                # If group key changes (e.g. new invoice) or chunk token limit reached, emit
                if (
                    (group_key is not None and group_key != current_group_key)
                    or (current_token_count + row_tokens) > chunk_limit
                ):
                    _emit_merged_chunk()
                    current_group_key = group_key
                    current_token_count = 0

                current_texts.append(row_text)
                current_source_rows.append(source_row_val)
                current_group_rows.append(row)
                current_token_count += row_tokens

                if len(ids) >= upsert_batch_size:
                    _flush(i + 1)

            _emit_merged_chunk()
            if ids:
                _flush(total_records)

        else:
            for i, row in enumerate(records):
                if cancel_check and i % 50 == 0 and cancel_check():
                    raise InterruptedError("Ingestion cancelled by user")
                source_row = row.get("source_row", i + 1)
                clean_meta = _build_row_meta(i, row)
                text = pack.row_to_text(row)
                chunk_id = self._generate_chunk_id(source_file, source_row, 0)
                ids.append(chunk_id)
                texts.append(self.passage_prefix + text)
                metadatas.append(clean_meta)

                if len(ids) >= upsert_batch_size:
                    _flush(i + 1)

            if ids:
                _flush(total_records)

        if progress_callback:
            progress_callback(95.0, f"Finalizing {total_chunks} chunks in Knowledge Base...")

        return IngestSummary(
            total_chunks=total_chunks,
            source_file=source_file,
            source_connector=source_meta.get("source_connector", "unknown"),
            domain=domain,
            time_taken_sec=time.time() - start_time,
            file_id=file_id
        )
        
    def add_text_documents(self, docs: List[str], source_meta: dict, domain: Optional[str] = None, file_id: Optional[str] = None) -> IngestSummary:
        domain = domain or get_default_domain()
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
        filename = os.path.basename(source_file)
        
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
                    "filename": filename,
                    "file_id": file_id or "",
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
                    docs_to_store = [encrypt_string(t) for t in texts] if getattr(settings, "encryption_enabled", True) else texts
                    collection.upsert(
                        ids=ids,
                        embeddings=embeddings,
                        documents=docs_to_store,
                        metadatas=metadatas
                    )
                    total_chunks += len(ids)
                    ids, texts, metadatas = [], [], []

        if ids:
            embeddings = embedder.encode(texts, batch_size=batch_size, normalize_embeddings=True).tolist()
            docs_to_store = [encrypt_string(t) for t in texts] if getattr(settings, "encryption_enabled", True) else texts
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=docs_to_store,
                metadatas=metadatas
            )
            total_chunks += len(ids)

        return IngestSummary(
            total_chunks=total_chunks,
            source_file=source_file,
            source_connector=source_meta.get("source_connector", "text"),
            domain=domain,
            time_taken_sec=time.time() - start_time,
            file_id=file_id
        )

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
        source_files: Optional[List[str]] = None
    ) -> List[RetrievedChunk]:
        """
        Filtered semantic search over the knowledge base with optional file scoping.
        """
        domain = domain or get_default_domain()
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
        
        # Build filter clauses for Chroma metadata
        clauses = []
        if filters:
            if "month" in filters and "date_from" not in filters:
                try:
                    clauses.append({"month": int(filters["month"])})
                except (ValueError, TypeError):
                    pass
            if "year" in filters and "date_from" not in filters:
                try:
                    clauses.append({"year": int(filters["year"])})
                except (ValueError, TypeError):
                    pass
            
            for k, v in filters.items():
                if k not in ("date_from", "date_to", "month", "year"):
                    if isinstance(v, dict):
                        clauses.append({k: v})
                    elif isinstance(v, (str, int, float, bool)):
                        clauses.append({k: v})
            
        if file_ids and len(file_ids) > 0:
            if len(file_ids) == 1:
                clauses.append({"file_id": file_ids[0]})
            else:
                clauses.append({"file_id": {"$in": file_ids}})
        elif source_files and len(source_files) > 0:
            sf_or = []
            for sf in source_files:
                fname = os.path.basename(sf)
                norm = sf.replace("\\", "/")
                sf_or.append({"filename": fname})
                sf_or.append({"source_file": sf})
                sf_or.append({"source_file": norm})
                sf_or.append({"file_id": sf})
            if len(sf_or) == 1:
                clauses.append(sf_or[0])
            else:
                clauses.append({"$or": sf_or})
                
        if len(clauses) == 0:
            where_clause = None
        elif len(clauses) == 1:
            where_clause = clauses[0]
        else:
            where_clause = {"$and": clauses}

        # If date range is active, expand query window to retrieve all candidates
        has_date_range = bool(filters and ("date_from" in filters or "date_to" in filters))
        query_k = min(count, max(top_k * 4, 30)) if has_date_range else top_k
        
        # Multi-source balanced retrieval: when multiple file_ids are requested,
        # retrieve balanced candidates per file_id so one source does not starve the others
        results = None
        if file_ids and len(file_ids) > 1:
            per_source_k = max(3, query_k // len(file_ids))
            all_docs = []
            all_metas = []
            all_distances = []
            other_clauses = [c for c in clauses if "file_id" not in c] if clauses else []
            for fid in file_ids:
                source_clause = {"file_id": fid}
                sub_where = {"$and": [source_clause] + other_clauses} if other_clauses else source_clause
                try:
                    sub_res = collection.query(
                        query_embeddings=[query_embedding],
                        n_results=min(count, per_source_k),
                        where=sub_where,
                        include=["documents", "metadatas", "distances"]
                    )
                    if sub_res and sub_res.get("documents") and sub_res["documents"][0]:
                        all_docs.extend(sub_res["documents"][0])
                        all_metas.extend(sub_res["metadatas"][0])
                        all_distances.extend(sub_res["distances"][0])
                except Exception:
                    pass
            if all_docs:
                results = {"documents": [all_docs], "metadatas": [all_metas], "distances": [all_distances]}

        if results is None:
            try:
                results = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=query_k,
                    where=where_clause,
                    include=["documents", "metadatas", "distances"]
                )
            except Exception:
                # Fallback without filter if filter fails
                results = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=query_k,
                    include=["documents", "metadatas", "distances"]
                )
        
        retrieved = []
        seen_chunk_ids = set()

        def _is_date_in_range(meta_dict: dict) -> bool:
            if not has_date_range or not filters:
                return True
            d_val = str(meta_dict.get("date", ""))[:10]
            if not d_val or d_val in ("nan", "None", "NaT"):
                return True
            if "date_from" in filters and d_val < str(filters["date_from"])[:10]:
                return False
            if "date_to" in filters and d_val > str(filters["date_to"])[:10]:
                return False
            return True

        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0]
            
            for doc, meta, dist in zip(docs, metas, distances):
                if not _is_date_in_range(meta):
                    continue

                clean_text = decrypt_string(doc)
                if self.passage_prefix and clean_text.startswith(self.passage_prefix):
                    clean_text = clean_text[len(self.passage_prefix):]
                    
                sim = max(0.0, 1.0 - dist)
                source_row = meta.get("source_row")
                c_id = f"{meta.get('source_file')}_{source_row}"
                if c_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(c_id)
                
                retrieved.append(RetrievedChunk(
                    text=clean_text,
                    metadata=meta,
                    score=sim,
                    source_row=int(source_row) if source_row is not None else None
                ))

        # If date range is active and semantic search missed any matching records in scoped dataset
        if has_date_range and len(retrieved) < top_k:
            try:
                get_results = collection.get(where=where_clause, include=["documents", "metadatas"])
                if get_results and get_results.get("documents"):
                    for g_doc, g_meta in zip(get_results["documents"], get_results["metadatas"]):
                        if not _is_date_in_range(g_meta):
                            continue
                        g_row = g_meta.get("source_row")
                        g_id = f"{g_meta.get('source_file')}_{g_row}"
                        if g_id in seen_chunk_ids:
                            continue
                        seen_chunk_ids.add(g_id)
                        
                        g_text = decrypt_string(g_doc)
                        if self.passage_prefix and g_text.startswith(self.passage_prefix):
                            g_text = g_text[len(self.passage_prefix):]

                        retrieved.append(RetrievedChunk(
                            text=g_text,
                            metadata=g_meta,
                            score=0.95,
                            source_row=int(g_row) if g_row is not None else None
                        ))
            except Exception as e:
                pass

        return retrieved[:top_k]

    def delete_source(self, source: Optional[str]):
        """Remove all chunks from a specific source file (by file_id, source_file path, or filename)."""
        if not source:
            return
        collection = self._get_chroma()
        if collection.count() == 0:
            return
            
        fname = os.path.basename(source)
        norm_path = source.replace("\\", "/")
        
        # Fast targeted deletion using $or where clause
        try:
            collection.delete(where={"$or": [
                {"file_id": source},
                {"filename": fname},
                {"source_file": source},
                {"source_file": norm_path}
            ]})
        except Exception:
            # Fallback to single field deletion if $or syntax is unsupported
            for key, val in [("file_id", source), ("filename", fname), ("source_file", norm_path)]:
                try:
                    collection.delete(where={key: val})
                except Exception:
                    pass

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
