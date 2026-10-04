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
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
import pandas as pd

from app.core.config import settings, get_default_domain
from app.schema.domain import get_domain_pack
from app.ingestion.models import IngestSummary, RetrievedChunk
from app.security.crypto import encrypt_string, decrypt_string
from app.ingestion.safety import locked, token_windows, chunk_identity, replace_source, publish_delta

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

def _is_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False

def _get_global_embedder(model_name: str, progress_callback: Optional[Callable[[float, str], None]] = None):
    if model_name not in _global_embedders:
        with _embedder_lock:
            if model_name not in _global_embedders:
                if progress_callback:
                    progress_callback(32.0, "Loading local neural embedding model...")
                import os
                from sentence_transformers import SentenceTransformer
                device = "cuda" if _is_cuda_available() else "cpu"
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

    @locked
    def get_dataframe(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None) -> pd.DataFrame:
        """Load complete selected structured records from Chroma metadata.

        Semantic search returns only a ranked sample and must never be used as
        the input to whole-ledger calculations. ``record_json`` is encrypted
        at rest; older indexed rows fall back to their available metadata and
        should be re-ingested to enable complete structured analysis.
        """
        collection = self._get_chroma()
        scope_chains = []
        registry_names = {}
        if file_ids:
            try:
                from app.ingestion.registry import file_registry
                for file_id in dict.fromkeys(str(value) for value in file_ids if value):
                    registered = file_registry.get_file_by_id(file_id)
                    filename = getattr(registered, "filename", None) if registered else None
                    if filename:
                        registry_names[file_id] = str(filename)
            except Exception:
                pass
        for file_id in dict.fromkeys(str(value) for value in (file_ids or []) if value):
            chain = [{"file_id": file_id}, {"group_name": file_id}]
            if registry_names.get(file_id):
                chain.append({"filename": registry_names[file_id]})
            scope_chains.append((chain, registry_names.get(file_id)))
        requested_names = dict.fromkeys(str(value) for value in (source_files or []) if value)
        for filename in requested_names:
            scope_chains.append(([{"filename": filename}, {"source_file": filename}], filename))
        if not scope_chains:
            return pd.DataFrame()
        records = []
        seen = set()
        incomplete = False
        page_size = 20000
        matched_names = set()
        for chain, registered_name in scope_chains:
            if registered_name and registered_name in matched_names:
                continue
            for scope in chain:
                offset = 0
                found_scope = False
                while True:
                    paged = True
                    try:
                        result = collection.get(where=scope, include=["metadatas"], limit=page_size, offset=offset)
                    except TypeError:  # lightweight test/mocked collections may not expose pagination
                        paged = False
                        result = collection.get(where=scope, include=["metadatas"])
                    metadatas = result.get("metadatas") or []
                    if not metadatas:
                        break
                    found_scope = True
                    for metadata in metadatas:
                        encoded = metadata.get("record_json")
                        try:
                            row = json.loads(decrypt_string(encoded)) if encoded else dict(metadata)
                        except Exception:
                            row = dict(metadata)
                            incomplete = True
                        if not encoded:
                            incomplete = True
                        rows = row if isinstance(row, list) else [row]
                        if metadata.get("items_in_chunk", 1) > len(rows):
                            incomplete = True  # legacy merged payload requires re-ingestion
                        for record_index, row in enumerate(rows):
                            row = dict(row)
                            for key in ("file_id", "source_file", "filename", "source_connector", "table_name", "database_name", "group_name", "domain", "row_id"):
                                current_value = row.get(key)
                                missing_value = current_value is None or (isinstance(current_value, str) and not current_value.strip())
                                if key in metadata and (key not in row or missing_value):
                                    row[key] = metadata[key]
                            row.setdefault("source_row", metadata.get("source_row", record_index + 1))
                            identity = (str(row.get("file_id", "")), str(row.get("source_file", "")), str(row.get("row_id", row.get("source_row", ""))))
                            if identity not in seen:
                                seen.add(identity)
                                records.append(row)
                    if not paged or len(metadatas) < page_size:
                        break
                    offset += len(metadatas)
                if found_scope:
                    if registered_name:
                        matched_names.add(registered_name)
                    elif scope.get("filename"):
                        matched_names.add(str(scope["filename"]))
                    break
        frame = pd.DataFrame(records)
        if incomplete:
            frame.attrs["incomplete_source"] = True
        return frame

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
        cancel_check: Optional[Callable[[], bool]] = None,
        replace_existing: bool = False
    ) -> IngestSummary:
        if replace_existing:
            return replace_source(self, "add_dataframe", (canonical_df,), dict(
                source_meta=source_meta, domain=domain, strategy=strategy, merge_key=merge_key,
                file_id=file_id, progress_callback=progress_callback, cancel_check=cancel_check))
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

        total_records = len(canonical_df)
        if progress_callback:
            progress_callback(15.0, f"Ingesting chunks: 0 / {total_records} (15%)...")

        pack = get_domain_pack(domain)
        collection = self._get_chroma()
        embedder = self._get_embedder(progress_callback)
        
        # Clean canonical_df NaNs
        canonical_df = canonical_df.where(pd.notnull(canonical_df), None)
        records = canonical_df.to_dict(orient="records")
        total_records = len(records)
        
        # Optimized batch sizes for high-throughput tensor encoding and responsive progress updates
        encode_batch_size = 512 if _is_cuda_available() else 256
        upsert_batch_size = 256 if total_records > 500 else max(64, min(128, total_records // 4 or 64))
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
            if 0 <= i < len(years):
                try:
                    val = years.iloc[i] if hasattr(years, "iloc") else years[i]
                    if pd.notna(val):
                        derived_meta["year"] = int(val)
                except Exception:
                    pass
            if 0 <= i < len(months):
                try:
                    val = months.iloc[i] if hasattr(months, "iloc") else months[i]
                    if pd.notna(val):
                        derived_meta["month"] = int(val)
                except Exception:
                    pass
            if 0 <= i < len(exp_ym):
                try:
                    val = exp_ym.iloc[i] if hasattr(exp_ym, "iloc") else exp_ym[i]
                    if pd.notna(val):
                        derived_meta["expiry_year_month"] = str(val)
                except Exception:
                    pass
            # Fallbacks directly from row if series indexing missed
            if "year" not in derived_meta and row.get("date"):
                try:
                    d_val = pd.to_datetime(row["date"], errors="coerce")
                    if pd.notna(d_val):
                        derived_meta["year"] = int(d_val.year)
                        derived_meta["month"] = int(d_val.month)
                except Exception:
                    pass
            if "expiry_year_month" not in derived_meta and row.get("expiry_date"):
                try:
                    e_val = pd.to_datetime(row["expiry_date"], errors="coerce")
                    if pd.notna(e_val):
                        derived_meta["expiry_year_month"] = str(e_val.strftime('%Y-%m'))
                except Exception:
                    pass

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
            # Preserve every canonical source field for exact row lookups and
            # full-table calculations. Encrypt the payload so patient/customer
            # identifiers do not become clear-text Chroma metadata.
            try:
                record_json = json.dumps(row, ensure_ascii=False, default=lambda value: value.item() if hasattr(value, "item") else str(value))
                base_meta["record_json"] = encrypt_string(record_json)
            except Exception:
                pass
            base_meta.update(derived_meta)
            return self._sanitize_metadata(base_meta)

        source_namespace = hashlib.sha256(f"{domain}|{file_id or ''}|{source_file}".encode()).hexdigest()[:16]
        ids: List[str] = []
        texts: List[str] = []
        metadatas: List[dict] = []

        def _flush(current_idx: int = 0):
            nonlocal total_chunks
            if not ids:
                return
            if cancel_check and cancel_check():
                raise InterruptedError("Ingestion cancelled by user")
            flushing_count = len(ids)
            start_row = max(0, current_idx - flushing_count)
            if progress_callback:
                pct = min(94.0, 35.0 + (58.0 * (start_row / max(1, total_records))))
                progress_callback(pct, f"Vectorizing chunks: {start_row} / {total_records} ({pct:.0f}%)...")
            expanded_ids, expanded_texts, expanded_metas = [], [], []
            for chunk_id, text, metadata in zip(ids, texts, metadatas):
                raw_text = text[len(self.passage_prefix):] if self.passage_prefix else text
                windows = token_windows(raw_text, embedder, self.passage_prefix,
                                        settings.chunk_size, settings.chunk_overlap)
                for index, window in enumerate(windows):
                    window_id = f"{source_namespace}_{chunk_id}_{index}"
                    window_meta = {**metadata, "chunk_id": window_id, "chunk_index": index}
                    expanded_ids.append(window_id)
                    expanded_texts.append(self.passage_prefix + window)
                    expanded_metas.append(window_meta)
            ids[:] = expanded_ids
            texts[:] = expanded_texts
            metadatas[:] = expanded_metas
            for batch_start in range(0, len(ids), 256):
                if cancel_check and cancel_check():
                    raise InterruptedError("Ingestion cancelled by user")
                batch_end = batch_start + 256
                batch_texts = texts[batch_start:batch_end]
                embeddings = embedder.encode(batch_texts, batch_size=encode_batch_size,
                                             show_progress_bar=False, normalize_embeddings=True).tolist()
                docs_to_store = [encrypt_string(t) for t in batch_texts] if getattr(settings, "encryption_enabled", True) else batch_texts
                collection.upsert(ids=ids[batch_start:batch_end], embeddings=embeddings,
                                  documents=docs_to_store, metadatas=metadatas[batch_start:batch_end])
                total_chunks += len(batch_texts)
            if progress_callback:
                pct_done = min(94.0, 35.0 + (58.0 * (current_idx / max(1, total_records))))
                progress_callback(pct_done, f"Ingesting chunks: {current_idx} / {total_records} ({pct_done:.0f}%)...")
            ids.clear(); texts.clear(); metadatas.clear()

        merge_key_field = merge_key
        if not merge_key_field and records:
            first_record_keys = {str(k).lower().strip(): k for k in records[0].keys()}
            for candidate in ["invoice_id", "invoice_no", "invoiceno", "bill_no", "bill_id", "billno", "order_id", "orderno", "voucher_no", "receipt_no", "transaction_id", "doc_no", "id"]:
                if candidate in first_record_keys:
                    merge_key_field = first_record_keys[candidate]
                    break
        has_usable_merge_key = bool(
            merge_key_field
            and any(row.get(merge_key_field) is not None and str(row.get(merge_key_field)).strip() for row in records)
        )

        if strategy == "merge" and has_usable_merge_key:
            # --- Greedy token-constrained merge strategy ---
            tokenizer = getattr(embedder, "tokenizer", None)
            count_tokens = (lambda text: len(tokenizer.encode(self.passage_prefix + text, add_special_tokens=True))) if tokenizer else (lambda text: len(text.split()))

            key_field = merge_key_field

            chunk_limit = settings.chunk_size

            current_texts: List[str] = []
            current_source_rows: List[int] = []
            current_group_rows: List[dict] = []
            current_record_indices: List[int] = []
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
                
                first_record_idx = current_record_indices[0] if current_record_indices else 0
                first_row_idx = current_source_rows[0] if current_source_rows else (first_record_idx + 1)
                first_row_dict = current_group_rows[0] if current_group_rows else {}
                
                meta = _build_row_meta(first_record_idx, first_row_dict)
                complete_rows = [dict(row, source_row=source_row) for row, source_row in
                                 zip(current_group_rows, current_source_rows)]
                meta["record_json"] = encrypt_string(json.dumps(complete_rows, ensure_ascii=False,
                    default=lambda value: value.item() if hasattr(value, "item") else str(value)))
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
                current_record_indices.clear()

            for i, row in enumerate(records):
                if cancel_check and i % 50 == 0 and cancel_check():
                    raise InterruptedError("Ingestion cancelled by user")
                row_text = pack.row_to_text(row)
                row_tokens = count_tokens(row_text)
                group_key = row.get(key_field) if key_field else None
                source_row_val = int(row.get("source_row", i + 1))

                # A row without a grouping ID is an independent record. Never
                # absorb it into a neighboring invoice chunk or combine a run
                # of unrelated null-key rows into one ambiguous document.
                if group_key is None or not str(group_key).strip():
                    _emit_merged_chunk()
                    current_group_key = object()
                    current_token_count = 0
                    current_texts.append(row_text)
                    current_source_rows.append(source_row_val)
                    current_group_rows.append(row)
                    current_record_indices.append(i)
                    _emit_merged_chunk()
                    if len(ids) >= upsert_batch_size:
                        _flush(i + 1)
                    continue

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
                current_record_indices.append(i)
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

    def reconcile_database_table(
        self,
        canonical_df: pd.DataFrame,
        source_meta: dict,
        pk_cols: Optional[List[str]] = None,
        domain: Optional[str] = None,
        strategy: str = "row",
        file_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """
        Differential reconciliation sync for database tables:
        - Detects primary keys or deterministic row identity.
        - Deletes rows in Chroma that were removed from the database.
        - Only embeds and upserts brand new or modified rows.
        - Skips unchanged tables/rows without re-embedding.
        """
        start_time = time.time()
        effective_domain = domain or get_default_domain()
        pack = get_domain_pack(effective_domain)
        collection = self._get_chroma()

        table_name = str(source_meta.get("table_name", "unknown"))
        database_name = str(source_meta.get("database_name", "unknown"))
        table_path = source_meta.get("source_file", f"sql://{database_name}/{table_name}")
        norm_db = database_name.strip().lower()
        norm_table = table_name.strip().lower()
        effective_file_id = file_id or f"db_{norm_db}_{norm_table}".replace(" ", "_").replace("-", "_").replace(".", "_").lower()

        if canonical_df.empty:
            existing = collection.get(where={"file_id": effective_file_id}, include=["metadatas"])
            prior_count = len(existing["ids"]) if existing and existing.get("ids") else 0
            if prior_count > 0:
                self.delete_source(effective_file_id)
                self.delete_source(table_path)
            return {
                "table_name": table_name,
                "database_name": database_name,
                "status": "empty",
                "total_db_rows": 0,
                "new_rows": 0,
                "updated_rows": 0,
                "deleted_rows": prior_count,
                "chunks": 0,
                "message": f"Table '{table_name}' is empty. {prior_count} previously ingested chunks removed."
            }

        # Query existing chunks in Chroma for this table
        existing_metas = {}
        try:
            from app.api.analytics import _fetch_metadatas_from_sqlite
            metas = _fetch_metadatas_from_sqlite(self.chroma_dir, "file_id", [effective_file_id], collection_name=self.collection_name)
            for m in metas:
                cid = m.get("chunk_id") or m.get("id")
                if cid:
                    existing_metas[cid] = m
        except Exception:
            try:
                existing = collection.get(where={"file_id": effective_file_id}, include=["metadatas"])
                existing_metas = {cid: meta for cid, meta in zip(existing["ids"], existing["metadatas"])} if existing and existing.get("ids") else {}
            except Exception:
                existing_metas = {}
        existing_ids = set(existing_metas.keys())

        # Upgrade old database chunks that lack stable row identity, content
        # fingerprints, or the encrypted complete-record payload needed for
        # accurate structured analysis.
        if existing_metas:
            sample_m = next(iter(existing_metas.values()))
            if "content_hash" not in sample_m or "row_id" not in sample_m or "record_json" not in sample_m:
                # Missing fingerprints force fresh embeddings; retain the old
                # IDs until the prepared replacement is published successfully.
                existing_metas = {key: {**value, "content_hash": ""} for key, value in existing_metas.items()}

        def _resolve_row_id(row: dict, row_idx: int) -> str:
            # 1. Search for explicit PK cols (checking raw name, _extra.name, and case-insensitively)
            if pk_cols:
                vals = []
                for c in pk_cols:
                    val = row.get(c)
                    if val is None:
                        val = row.get(f"_extra.{c}")
                    if val is None:
                        c_clean = c.strip().lower()
                        for k, v in row.items():
                            k_clean = k.replace("_extra.", "").strip().lower()
                            if k_clean == c_clean:
                                val = v
                                break
                    if val is not None and str(val).strip() != "" and str(val).lower() not in ("none", "nan"):
                        vals.append(str(val).strip())
                if vals and len(vals) == len(pk_cols):
                    return "_".join(vals)

            # 2. Check candidate ID columns (detail/line items first, then entities, then headers)
            lower_map = {str(k).replace("_extra.", "").strip().lower(): k for k in row.keys()}
            for cand in [
                "saledetailid", "sale_detail_id", "purchasedetailid", "purchase_detail_id",
                "detail_id", "detailid", "line_id", "lineid", "item_id", "itemid",
                "configid", "config_id", "batchid", "batch_id", "batch_no", "batchno",
                "code", "product_id", "productid", "customer_id", "customerid",
                "doctor_id", "doctorid", "supplier_id", "supplierid",
                "transaction_number", "transactionnumber", "id",
                "billno", "bill_no", "bill_id", "invoiceno", "invoice_no", "invoice_id",
                "order_id", "orderno"
            ]:
                if cand in lower_map:
                    val = str(row.get(lower_map[cand], "")).strip()
                    if val and val.lower() not in ("none", "nan"):
                        return val

            # 3. Fallback: MD5 hash of canonical row content
            filtered_items = sorted((str(k), str(v)) for k, v in row.items() if k not in ("source_connector", "source_row"))
            return hashlib.md5(repr(filtered_items).encode("utf-8")).hexdigest()[:16]

        records = canonical_df.to_dict(orient="records")
        current_chunk_ids: List[str] = []
        current_content_hashes: List[str] = []
        current_texts: List[str] = []
        current_metas: List[dict] = []
        current_ids_set = set()
        embedder = self._get_embedder(progress_callback)

        for i, row in enumerate(records):
            row_id = _resolve_row_id(row, i)
            base_chunk_id = hashlib.md5(f"sql://{norm_db}/{norm_table}_{str(row_id)}".encode("utf-8")).hexdigest()
            chunk_id = base_chunk_id
            if chunk_id in current_ids_set:
                dup_count = 1
                while f"{base_chunk_id}_{dup_count}" in current_ids_set:
                    dup_count += 1
                chunk_id = f"{base_chunk_id}_{dup_count}"
            row_text = pack.row_to_text(row)
            content_hash = hashlib.md5(row_text.encode("utf-8")).hexdigest()

            meta = {
                "source_file": table_path,
                "filename": f"{database_name} — {table_name}",
                "file_id": effective_file_id,
                "source_connector": source_meta.get("source_connector", "sql_database"),
                "database_name": database_name,
                "table_name": table_name,
                "group_name": database_name,
                "source_type": "database",
                "domain": effective_domain,
                "row_id": str(row_id),
                "content_hash": content_hash,
                "source_row": i + 1,
                "ingested_at": source_meta.get("ingested_at", datetime.now().isoformat())
            }

            for f in pack.filter_metadata_fields:
                if f in row and row[f] is not None and str(row[f]).strip() != "":
                    meta[f] = row[f]

            try:
                record_json = json.dumps(row, ensure_ascii=False, default=lambda value: value.item() if hasattr(value, "item") else str(value))
                meta["record_json"] = encrypt_string(record_json)
            except Exception:
                pass

            windows = token_windows(row_text, embedder, self.passage_prefix,
                                    settings.chunk_size, settings.chunk_overlap)
            for window_index, window in enumerate(windows):
                window_id = chunk_id if window_index == 0 else f"{chunk_id}_window_{window_index}"
                window_hash = hashlib.sha256((content_hash + window).encode()).hexdigest()
                window_meta = self._sanitize_metadata({**meta, "chunk_id": window_id,
                    "chunk_index": window_index, "content_hash": window_hash})
                current_chunk_ids.append(window_id)
                current_content_hashes.append(window_hash)
                current_texts.append(self.passage_prefix + window)
                current_metas.append(window_meta)
                current_ids_set.add(window_id)

        # 1. Detect Deleted Rows (present in Chroma, but absent from Database)
        deleted_ids = [cid for cid in existing_ids if cid not in current_ids_set]
        # 2. Detect New or Updated Rows
        to_upsert_indices: List[int] = []
        new_count = 0
        updated_count = 0

        for idx, (cid, chash) in enumerate(zip(current_chunk_ids, current_content_hashes)):
            if cid not in existing_ids:
                to_upsert_indices.append(idx)
                new_count += 1
            else:
                old_m = existing_metas.get(cid, {})
                if old_m.get("content_hash") != chash:
                    to_upsert_indices.append(idx)
                    updated_count += 1

        # Directly upsert new/updated vectors and delete removed vectors
        if to_upsert_indices:
            for b_start in range(0, len(to_upsert_indices), 64):
                if cancel_check and cancel_check():
                    raise InterruptedError("Ingestion cancelled by user")
                indices = to_upsert_indices[b_start:b_start + 64]
                b_texts = [current_texts[index] for index in indices]
                embeddings = embedder.encode(b_texts, batch_size=len(b_texts),
                                             show_progress_bar=False, normalize_embeddings=True).tolist()
                docs = [encrypt_string(t) for t in b_texts] if getattr(settings, "encryption_enabled", True) else b_texts
                collection.upsert(ids=[current_chunk_ids[index] for index in indices],
                                  embeddings=embeddings, documents=docs,
                                  metadatas=[current_metas[index] for index in indices])
        if deleted_ids:
            try:
                collection.delete(ids=list(deleted_ids))
            except Exception:
                pass

        prior_row_ids = {meta.get("row_id") for meta in existing_metas.values()}
        active_row_ids = {meta.get("row_id") for meta in current_metas}
        deleted_count = len({existing_metas[cid].get("row_id", cid) for cid in deleted_ids
                             if existing_metas[cid].get("row_id") not in active_row_ids})
        new_count = len({current_metas[index]["row_id"] for index in to_upsert_indices
                         if current_chunk_ids[index] not in existing_ids
                         and current_metas[index]["row_id"] not in prior_row_ids})
        updated_count = len({current_metas[index]["row_id"] for index in to_upsert_indices
                             if current_metas[index]["row_id"] in prior_row_ids})
        active_chunks = len(current_chunk_ids)
        total_records = len(records)

        if not to_upsert_indices and not deleted_ids:
            status = "unchanged"
            message = f"Table '{table_name}' is already up-to-date ({total_records} rows verified, 0 changes)."
        else:
            status = "synced"
            parts = []
            if new_count:
                parts.append(f"{new_count} new rows ingested")
            if updated_count:
                parts.append(f"{updated_count} updated rows re-indexed")
            if deleted_ids:
                parts.append(f"{deleted_count} deleted rows purged")
            message = f"Table '{table_name}' synced: " + ", ".join(parts) + f" (total: {active_chunks} active chunks)."

        return {
            "table_name": table_name,
            "database_name": database_name,
            "status": status,
            "total_db_rows": total_records,
            "new_rows": new_count,
            "updated_rows": updated_count,
            "deleted_rows": deleted_count,
            "chunks": active_chunks,
            "message": message,
            "time_taken_sec": round(time.time() - start_time, 2)
        }
        
    def add_text_documents(self, docs: List[str], source_meta: dict, domain: Optional[str] = None, file_id: Optional[str] = None, replace_existing: bool = False) -> IngestSummary:
        if replace_existing:
            return replace_source(self, "add_text_documents", (docs,), dict(source_meta=source_meta, domain=domain, file_id=file_id))
        domain = domain or get_default_domain()
        """
        Ingest free-text documents using model-token windows with overlap.
        """
        start_time = time.time()
        
        collection = self._get_chroma()
        embedder = self._get_embedder()
        
        chunk_size = settings.chunk_size
        source_namespace = hashlib.sha256(f"{domain}|{file_id or ''}|{source_meta.get('source_file', 'unknown')}".encode()).hexdigest()[:16]
        
        source_file = source_meta.get("source_file", "unknown")
        filename = os.path.basename(source_file)
        
        ids = []
        texts = []
        metadatas = []
        
        total_chunks = 0
        batch_size = 64
        
        for doc_idx, doc_text in enumerate(docs):
            windows = token_windows(doc_text, embedder, self.passage_prefix, chunk_size, settings.chunk_overlap)
            for chunk_idx, chunk_text in enumerate(windows):
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
                
                chunk_id = source_namespace + "_" + self._generate_chunk_id(source_file, doc_idx, chunk_idx)
                meta["chunk_id"] = chunk_id
                
                ids.append(chunk_id)
                texts.append(self.passage_prefix + chunk_text)
                metadatas.append(self._sanitize_metadata(meta))
                
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

    @locked
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
        
        # Domain is part of the evidence boundary, even for unscoped searches.
        # Chroma combines this with any selected file and metadata filters.
        clauses = [{"domain": domain}]
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
            expanded_fids = set()
            matched_groups = set()
            try:
                from app.ingestion.registry import file_registry
                all_registered = file_registry.list_files()
                for fid in file_ids:
                    fid_clean = str(fid).strip()
                    fid_lower = fid_clean.lower()
                    expanded_fids.add(fid_clean)
                    for rec in all_registered:
                        rec_fid = getattr(rec, "file_id", "")
                        rec_fname = str(getattr(rec, "filename", "") or "").lower()
                        rec_gname = str(getattr(rec, "group_name", "") or "")
                        rec_dname = str(getattr(rec, "database_name", "") or "")
                        if (
                            rec_fid == fid_clean
                            or fid_lower == rec_gname.lower()
                            or fid_lower == rec_dname.lower()
                            or fid_lower in rec_fname
                            or rec_fid.lower().startswith(f"db_{fid_lower}_")
                            or rec_fid.lower().startswith(f"{fid_lower}_")
                        ):
                            expanded_fids.add(rec_fid)
                            if rec_gname:
                                matched_groups.add(rec_gname)
                            if rec_dname:
                                matched_groups.add(rec_dname)
            except Exception:
                expanded_fids = set(file_ids)

            if matched_groups:
                g_list = list(matched_groups)
                if len(g_list) == 1:
                    clauses.append({"group_name": g_list[0]})
                else:
                    clauses.append({"group_name": {"$in": g_list}})
            else:
                fids_list = list(expanded_fids) if expanded_fids else file_ids
                if len(fids_list) == 1:
                    clauses.append({"file_id": fids_list[0]})
                else:
                    clauses.append({"file_id": {"$in": fids_list}})
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

        def matches_requested_scope(meta: dict) -> bool:
            # Chroma applies `where`, but keep a local guard as a second
            # boundary for old indexes, multi-source queries and adapters.
            if str(meta.get("domain", "")).casefold() != str(domain).casefold():
                return False
            if file_ids:
                source_id = str(meta.get("file_id", ""))
                group = str(meta.get("group_name", ""))
                database = str(meta.get("database_name", ""))
                filename = str(meta.get("filename", "")).casefold()
                return any(
                    fid == source_id
                    or fid.casefold() in (group.casefold(), database.casefold())
                    or fid.casefold() in filename
                    or source_id.casefold().startswith(f"db_{fid.casefold()}_")
                    or source_id.casefold().startswith(f"{fid.casefold()}_")
                    for fid in file_ids
                )
            if source_files:
                source_file = str(meta.get("source_file", "")).replace("\\", "/")
                filename = str(meta.get("filename", "")).casefold()
                return any(
                    source_file == str(path).replace("\\", "/")
                    or filename == os.path.basename(str(path)).casefold()
                    for path in source_files
                )
            return True

        # Expand candidate retrieval for date windows and concrete identifiers.
        # Embeddings often rank a semantically similar neighboring batch above an
        # exact batch/invoice code, so retrieve a wider set before reranking.
        has_date_range = bool(filters and ("date_from" in filters or "date_to" in filters))
        import re
        identifier_tokens = re.findall(
            r"(?<!\w)([A-Z]{1,8}[-/]?[A-Z0-9-]*\d[A-Z0-9-]*|\d{4,})(?!\w)",
            query,
            flags=re.IGNORECASE,
        )
        has_identifier = bool(identifier_tokens)
        query_k = min(count, max(top_k * 4, 30)) if (has_date_range or has_identifier) else top_k
        
        # Multi-source retrieval used to issue one synchronous Chroma query per
        # selected file. A database scope with 31 tables therefore performed 31
        # ANN searches before generation. Over-fetch once from the same scoped
        # collection, then apply the existing local scope guard and reranking.
        results = None
        if file_ids and len(file_ids) > 1:
            per_source_k = max(4, query_k // len(file_ids))
            candidate_k = min(count, max(query_k, per_source_k * len(file_ids)))
            try:
                results = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=candidate_k,
                    where=where_clause,
                    include=["documents", "metadatas", "distances"]
                )
            except Exception:
                # Do not broaden a failed selected-source search.
                if where_clause is not None:
                    return []
                raise

        if results is None:
            try:
                results = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=query_k,
                    where=where_clause,
                    include=["documents", "metadatas", "distances"]
                )
            except Exception:
                # A failed scoped query must never broaden into another file or
                # domain. Abstain; the caller can report that retrieval failed.
                if where_clause is not None:
                    return []
                raise
        
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
                if not matches_requested_scope(meta):
                    continue
                if not _is_date_in_range(meta):
                    continue

                clean_text = decrypt_string(doc)
                if self.passage_prefix and clean_text.startswith(self.passage_prefix):
                    clean_text = clean_text[len(self.passage_prefix):]
                    
                sim = max(0.0, 1.0 - dist)
                source_row = meta.get("source_row")
                c_id = chunk_identity(meta)
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
                        if not matches_requested_scope(g_meta):
                            continue
                        if not _is_date_in_range(g_meta):
                            continue
                        g_row = g_meta.get("source_row")
                        g_id = chunk_identity(g_meta)
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

        # A vector query can miss a record whose identifier is new or rare.
        # Resolve exact record codes directly against indexed metadata as well;
        # this is generic across invoice, sale, purchase, product, SKU, and
        # batch identifiers, and retains the selected-source scope.
        if identifier_tokens:
            exact_where_clauses = [c for c in clauses if isinstance(c, dict)]
            seen_exact = {chunk_identity(c.metadata) for c in retrieved}
            for token in identifier_tokens:
                for field in ("invoice_id", "transaction_id", "product_code", "sku", "product_id", "batch_no"):
                    condition = {field: token}
                    direct_clauses = exact_where_clauses + [condition]
                    direct_where = direct_clauses[0] if len(direct_clauses) == 1 else {"$and": direct_clauses}
                    try:
                        exact = collection.get(where=direct_where, include=["documents", "metadatas"])
                    except Exception:
                        continue
                    for doc, meta in zip(exact.get("documents") or [], exact.get("metadatas") or []):
                        if not matches_requested_scope(meta):
                            continue
                        source_row = meta.get("source_row")
                        chunk_key = chunk_identity(meta)
                        if chunk_key in seen_exact:
                            continue
                        seen_exact.add(chunk_key)
                        clean_text = decrypt_string(doc)
                        if self.passage_prefix and clean_text.startswith(self.passage_prefix):
                            clean_text = clean_text[len(self.passage_prefix):]
                        retrieved.append(RetrievedChunk(text=clean_text, metadata=meta, score=1.0,
                                                        source_row=int(source_row) if source_row is not None else None))

        if identifier_tokens and retrieved:
            id_fields = ("batch_no", "invoice_id", "transaction_id", "product_code", "sku", "product_id")
            wanted = {re.sub(r"[^a-z0-9]", "", token.casefold()) for token in identifier_tokens}

            def _identifier_match(chunk: RetrievedChunk) -> bool:
                for field in id_fields:
                    value = chunk.metadata.get(field)
                    if value is None:
                        continue
                    normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
                    if normalized in wanted:
                        return True
                return False

            # Exact record identifiers outrank semantic similarity. Keep the
            # vector score as a tiebreaker among exact matches and other chunks.
            retrieved.sort(key=lambda chunk: (_identifier_match(chunk), chunk.score), reverse=True)

        return retrieved[:top_k]

    @locked
    def delete_source(self, source: Optional[str]):
        """Remove all chunks from a specific source file (by file_id, source_file path, or filename)."""
        if not source:
            return
        collection = self._get_chroma()
        if collection.count() == 0:
            return
            
        # Resolve exact identifiers first. Never broaden a path to its basename.
        for key, value in (("file_id", source), ("source_file", source),
                           ("source_file", source.replace("\\", "/"))):
            collection.delete(where={key: value})

    def reset(self):
        """Delete and recreate the collection."""
        if self._chroma_client:
            self._chroma_client.delete_collection(self.collection_name)
            self._collection = None

    @locked
    def stats(self) -> Dict[str, Any]:
        """Return KB statistics."""
        try:
            count = int(self._get_chroma().count())
        except Exception:
            count = 0
        return {
            "total_chunks": count,
            "collection_name": self.collection_name
        }
