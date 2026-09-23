"""Module for Real-Time Event-Driven Sync / CDC from SQL database to Knowledge Base.

Provides an asynchronous micro-batching worker that handles INSERT, UPDATE, and DELETE
events on database rows, generating embeddings and updating ChromaDB without blocking
API requests or database transactions.
"""

import time
import queue
import threading
import hashlib
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field
import pandas as pd

from app.ingestion.store import KnowledgeBase
from app.ingestion.registry import file_registry
from app.schema.domain import get_domain_pack
from app.core.config import get_default_domain


class ChangeEvent(BaseModel):
    action: str = Field(..., description="Action type: insert, update, or delete")
    database_name: str = Field(..., description="Name of the source database")
    table_name: str = Field(..., description="Name of the table")
    row_id: Union[str, int] = Field(..., description="Primary key or row identifier")
    data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Row column values (required for insert/update)")
    domain: str = Field(default="pharmacy", description="Domain pack (e.g. pharmacy)")
    timestamp: Optional[str] = Field(default=None, description="ISO timestamp of change event")


class SyncWorker:
    def __init__(self, sync_interval_sec: float = 2.0, max_batch_size: int = 50, db_poll_interval_sec: float = 15.0):
        self.sync_interval_sec = sync_interval_sec
        self.max_batch_size = max_batch_size
        self._queue: queue.Queue[ChangeEvent] = queue.Queue()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._kb = KnowledgeBase()
        
        # Database auto-sync poller settings (<2ms DMV check)
        self.db_poll_interval_sec = db_poll_interval_sec
        self._last_db_poll_time = 0.0
        self._known_table_stats: Dict[str, Dict[str, int]] = {}
        self._is_polling_db = False
        self.total_db_syncs = 0
        
        # Performance & telemetry statistics
        self.stats_lock = threading.Lock()
        self.total_received = 0
        self.total_processed = 0
        self.total_upserted = 0
        self.total_deleted = 0
        self.total_failed = 0
        self.last_sync_time: Optional[str] = None
        self.last_error: Optional[str] = None

    def start(self):
        """Start the background micro-batch processing worker."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, name="KBSyncWorkerThread", daemon=True)
        self._worker_thread.start()
        print("[KBSyncWorker] Real-time sync worker started.")

    def stop(self):
        """Gracefully stop the worker and flush remaining events."""
        if not self._running:
            return
        self._running = False
        # Process remaining items in queue before shutting down
        self._flush_queue()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        print("[KBSyncWorker] Real-time sync worker stopped.")

    def push_event(self, event: ChangeEvent) -> Dict[str, Any]:
        """Enqueue a single row change event."""
        if not self._running:
            self.start()
        if not event.timestamp:
            event.timestamp = datetime.now().isoformat()
        self._queue.put(event)
        with self.stats_lock:
            self.total_received += 1
        return {
            "status": "queued",
            "queue_size": self._queue.qsize(),
            "event": {
                "action": event.action,
                "database_name": event.database_name,
                "table_name": event.table_name,
                "row_id": str(event.row_id)
            }
        }

    def push_batch(self, events: List[ChangeEvent]) -> Dict[str, Any]:
        """Enqueue multiple change events in bulk."""
        if not self._running:
            self.start()
        now_ts = datetime.now().isoformat()
        for evt in events:
            if not evt.timestamp:
                evt.timestamp = now_ts
            self._queue.put(evt)
        with self.stats_lock:
            self.total_received += len(events)
        return {
            "status": "queued",
            "enqueued_count": len(events),
            "queue_size": self._queue.qsize()
        }


    def get_status(self) -> Dict[str, Any]:
        """Return real-time sync telemetry, queue health, and DB poller status."""
        with self.stats_lock:
            return {
                "is_running": self._running,
                "queue_size": self._queue.qsize(),
                "sync_interval_sec": self.sync_interval_sec,
                "db_poll_interval_sec": self.db_poll_interval_sec,
                "total_db_syncs": self.total_db_syncs,
                "is_polling_db": self._is_polling_db,
                "total_received": self.total_received,
                "total_processed": self.total_processed,
                "total_upserted": self.total_upserted,
                "total_deleted": self.total_deleted,
                "total_failed": self.total_failed,
                "last_sync_time": self.last_sync_time,
                "last_error": self.last_error
            }

    def _run_loop(self):
        """Continuous debounce loop that drains the queue and checks registered databases."""
        while self._running:
            try:
                time.sleep(self.sync_interval_sec)
                self._flush_queue()

                # Check registered databases periodically (every 15s) using fast DMV metadata (<2ms)
                now = time.time()
                if now - self._last_db_poll_time >= self.db_poll_interval_sec:
                    self._last_db_poll_time = now
                    self.poll_registered_databases()
            except Exception as e:
                with self.stats_lock:
                    self.last_error = str(e)
                print(f"[KBSyncWorker] Error in worker loop: {e}")

    def poll_registered_databases(self) -> Dict[str, Any]:
        """Check all registered databases for row count changes via fast DMV metadata (<2ms)."""
        if self._is_polling_db:
            return {"status": "in_progress"}
        
        self._is_polling_db = True
        synced_tables = []
        try:
            from app.connectors.sql import SQLConnector
            connections = file_registry.list_db_connections(auto_sync_only=True)
            if not connections:
                return {"status": "no_active_connections", "synced": []}

            for conn_rec in connections:
                db_name = conn_rec.database_name
                try:
                    connector = SQLConnector(conn_rec.connection_string, db_type=conn_rec.db_type)
                    engine = connector._get_engine()
                    
                    # 1. Ultra-fast metadata row count query (<2ms, no locks)
                    row_counts: Dict[str, int] = {}
                    if conn_rec.db_type == "mssql":
                        query = """
                        SELECT t.name AS table_name, SUM(p.rows) AS row_count
                        FROM sys.tables t
                        JOIN sys.partitions p ON t.object_id = p.object_id
                        WHERE p.index_id IN (0, 1) AND t.is_ms_shipped = 0
                        GROUP BY t.name
                        """
                        counts_df = pd.read_sql(query, engine)
                        for _, r in counts_df.iterrows():
                            row_counts[str(r["table_name"])] = int(r["row_count"])
                    else:
                        tables = connector.list_tables()
                        for t in tables:
                            c_df = pd.read_sql(f"SELECT COUNT(*) AS total FROM {t}", engine)
                            row_counts[t] = int(c_df.iloc[0, 0])

                    # 2. Check for differences against known stats & registry
                    known = self._known_table_stats.setdefault(db_name, {})
                    changed_tables = []
                    total_db_rows = 0

                    for tbl, cnt in row_counts.items():
                        total_db_rows += cnt
                        file_id = f"db_{db_name}_{tbl}".replace(" ", "_").replace("-", "_").replace(".", "_").lower()
                        existing_reg = file_registry.get_file_by_id(file_id)
                        
                        reg_chunk_count = existing_reg.chunk_count if existing_reg else None
                        last_known_cnt = known.get(tbl)
                        
                        needs_sync = False
                        if last_known_cnt is not None and cnt != last_known_cnt:
                            needs_sync = True
                        elif reg_chunk_count is not None and cnt > 0 and reg_chunk_count < cnt:
                            needs_sync = True
                        elif last_known_cnt is None and reg_chunk_count is None and cnt > 0:
                            needs_sync = True

                        if needs_sync:
                            changed_tables.append((tbl, cnt))

                    # 3. Incrementally sync only the changed tables
                    for tbl, cnt in changed_tables:
                        chunks = self.sync_table(connector, db_name, tbl, domain=conn_rec.domain, strategy=conn_rec.strategy)
                        known[tbl] = cnt
                        synced_tables.append({"database": db_name, "table": tbl, "rows": cnt, "chunks": chunks})
                        print(f"[KBSyncWorker] Auto-ingested direct change in {db_name}.{tbl} ({cnt} rows, {chunks} chunks)")

                    # Update known stats for unchanged tables too
                    for tbl, cnt in row_counts.items():
                        if tbl not in known:
                            known[tbl] = cnt

                    if changed_tables:
                        with self.stats_lock:
                            self.total_db_syncs += len(changed_tables)
                            self.last_sync_time = datetime.now().isoformat()
                        file_registry.update_db_sync_status(
                            database_name=db_name,
                            status="active",
                            table_count=len(row_counts),
                            row_count=total_db_rows
                        )

                except Exception as conn_err:
                    print(f"[KBSyncWorker] Notice polling DB {db_name}: {conn_err}")

        finally:
            self._is_polling_db = False

        return {"status": "ok", "synced": synced_tables}

    def sync_table(
        self,
        connector,
        database_name: str,
        table_name: str,
        domain: Optional[str] = None,
        strategy: str = "row"
    ) -> int:
        """Sync a single database table into ChromaDB and update registry."""
        effective_domain = domain or get_default_domain()
        table_path = f"sql://{database_name}/{table_name}"
        file_id = f"db_{database_name}_{table_name}".replace(" ", "_").replace("-", "_").replace(".", "_").lower()
        
        try:
            df = connector.fetch(table_or_query=table_name)
            if df.empty:
                return 0
            
            try:
                domain_pack = get_domain_pack(effective_domain)
            except ValueError:
                domain_pack = get_domain_pack()

            from app.schema.mapper import map_headers
            from app.schema.normalize import apply_mapping
            mapping = map_headers(list(df.columns), domain_pack)
            canonical_df = apply_mapping(df, mapping, domain=domain, keep_extras=True)

            self._kb.delete_source(file_id)
            self._kb.delete_source(table_path)

            source_meta = {
                "source_file": table_path,
                "filename": f"{database_name} — {table_name}",
                "source_connector": f"sql_{connector.db_type}",
                "database_name": database_name,
                "table_name": table_name,
                "group_name": database_name,
                "source_type": "database"
            }

            summary = self._kb.add_dataframe(
                canonical_df,
                source_meta=source_meta,
                domain=domain,
                strategy=strategy,
                file_id=file_id
            )

            file_registry.register_or_update(
                file_path=table_path,
                chunk_count=summary.total_chunks,
                domain=domain,
                strategy=strategy,
                file_id=file_id,
                group_name=database_name,
                source_type="database",
                table_name=table_name,
                filename_override=f"{database_name} — {table_name}"
            )

            # Invalidate backend analytics cache for this database
            try:
                from app.api.analytics import clear_analytics_cache
                clear_analytics_cache(database_name)
            except Exception:
                pass

            return summary.total_chunks
        except Exception as e:
            print(f"[KBSyncWorker] Error syncing table {database_name}.{table_name}: {e}")
            return 0

    def sync_database_now(self, database_name: str) -> Dict[str, Any]:
        """Manually trigger immediate sync of all tables for a specific database."""
        from app.connectors.sql import SQLConnector
        conn_rec = file_registry.get_db_connection(database_name)
        if not conn_rec:
            raise ValueError(f"No saved database connection found for '{database_name}'.")

        connector = SQLConnector(conn_rec.connection_string, db_type=conn_rec.db_type)
        tables = connector.list_tables()
        results = []
        total_chunks = 0

        for tbl in tables:
            chunks = self.sync_table(connector, database_name, tbl, domain=conn_rec.domain, strategy=conn_rec.strategy)
            try:
                self._known_table_stats.setdefault(database_name, {})[tbl] = chunks
            except Exception:
                pass
            results.append({"table": tbl, "chunks": chunks})
            total_chunks += chunks

        file_registry.update_db_sync_status(
            database_name=database_name,
            status="active",
            table_count=len(tables),
            row_count=total_chunks
        )

        try:
            from app.api.analytics import clear_analytics_cache
            clear_analytics_cache(database_name)
        except Exception:
            pass

        return {
            "status": "success",
            "database_name": database_name,
            "tables_synced": len(results),
            "total_chunks": total_chunks,
            "details": results
        }

    def _generate_chunk_id(self, db_name: str, table_name: str, row_id: Union[str, int]) -> str:
        """Deterministic chunk ID for SQL rows."""
        norm_db = db_name.strip().lower()
        norm_table = table_name.strip().lower()
        s = f"sql://{norm_db}/{norm_table}_{str(row_id)}"
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def _flush_queue(self):
        """Drain up to max_batch_size events and apply them in batches."""
        if self._queue.empty():
            return

        batch: List[ChangeEvent] = []
        while not self._queue.empty() and len(batch) < self.max_batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not batch:
            return

        self._process_batch(batch)

    def _process_batch(self, batch: List[ChangeEvent]):
        """Vectorize and apply micro-batch of row changes."""
        upsert_items: List[Dict[str, Any]] = []
        delete_ids: List[str] = []
        affected_tables: set = set()

        for evt in batch:
            action = evt.action.strip().lower()
            chunk_id = self._generate_chunk_id(evt.database_name, evt.table_name, evt.row_id)
            affected_tables.add((evt.database_name, evt.table_name, evt.domain))

            if action == "delete":
                delete_ids.append(chunk_id)
            elif action in ("insert", "update", "upsert"):
                upsert_items.append({
                    "chunk_id": chunk_id,
                    "event": evt
                })

        collection = self._kb._get_chroma()
        embedder = self._kb._get_embedder()

        # 1. Execute deletions
        if delete_ids:
            try:
                collection.delete(ids=delete_ids)
                with self.stats_lock:
                    self.total_deleted += len(delete_ids)
                    self.total_processed += len(delete_ids)
            except Exception as e:
                with self.stats_lock:
                    self.total_failed += len(delete_ids)
                    self.last_error = f"Delete error: {str(e)}"
                print(f"[KBSyncWorker] Failed to delete chunks: {e}")

        # 2. Execute upserts
        if upsert_items:
            try:
                texts = []
                ids = []
                metadatas = []

                for item in upsert_items:
                    evt: ChangeEvent = item["event"]
                    chunk_id = item["chunk_id"]
                    
                    try:
                        domain_pack = get_domain_pack(evt.domain)
                    except ValueError:
                        domain_pack = get_domain_pack("pharmacy")

                    row_dict = evt.data or {}
                    # Auto-map synonyms to canonical headers
                    from app.schema.mapper import map_headers
                    mapping = map_headers(list(row_dict.keys()), domain_pack)
                    canonical_row = {}
                    for raw_col, val in row_dict.items():
                        canon_col = mapping.get(raw_col, raw_col)
                        canonical_row[canon_col] = val
                    # Preserve original keys too
                    for raw_col, val in row_dict.items():
                        if raw_col not in canonical_row:
                            canonical_row[raw_col] = val

                    # Build canonical row text using domain pack formatter
                    doc_text = domain_pack.row_to_text(canonical_row)
                    if not doc_text or doc_text.strip() in ("", "Empty record", "Empty record."):
                        doc_text = f"Table {evt.table_name}: " + ", ".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None and v != "")

                    table_path = f"sql://{evt.database_name}/{evt.table_name}"
                    file_id = f"db_{evt.database_name}_{evt.table_name}".replace(" ", "_").replace("-", "_").replace(".", "_").lower()

                    meta = {
                        "source_file": table_path,
                        "filename": f"{evt.database_name} — {evt.table_name}",
                        "file_id": file_id,
                        "source_connector": "sql_realtime",
                        "database_name": evt.database_name,
                        "table_name": evt.table_name,
                        "group_name": evt.database_name,
                        "source_type": "database",
                        "domain": evt.domain,
                        "row_id": str(evt.row_id),
                        "ingested_at": evt.timestamp or datetime.now().isoformat()
                    }

                    # Add filterable domain metadata if available
                    for f in domain_pack.filter_metadata_fields:
                        if f in canonical_row and canonical_row[f] is not None and canonical_row[f] != "":
                            meta[f] = canonical_row[f]


                    sanitized_meta = self._kb._sanitize_metadata(meta)

                    ids.append(chunk_id)
                    texts.append(self._kb.passage_prefix + doc_text)
                    metadatas.append(sanitized_meta)

                if ids:
                    embeddings = embedder.encode(
                        texts,
                        batch_size=len(texts),
                        show_progress_bar=False,
                        normalize_embeddings=True
                    ).tolist()

                    collection.upsert(
                        ids=ids,
                        embeddings=embeddings,
                        documents=texts,
                        metadatas=metadatas
                    )

                    with self.stats_lock:
                        self.total_upserted += len(ids)
                        self.total_processed += len(ids)
            except Exception as e:
                with self.stats_lock:
                    self.total_failed += len(upsert_items)
                    self.last_error = f"Upsert error: {str(e)}"
                print(f"[KBSyncWorker] Failed to upsert batch: {e}")

        # 3. Ensure table records exist and are marked active in file_registry
        for db_name, tbl_name, domain_name in affected_tables:
            try:
                table_path = f"sql://{db_name}/{tbl_name}"
                file_id = f"db_{db_name}_{tbl_name}".replace(" ", "_").replace("-", "_").replace(".", "_").lower()
                existing = file_registry.get_file_by_id(file_id)
                if not existing:
                    file_registry.register_or_update(
                        file_path=table_path,
                        chunk_count=1,
                        domain=domain_name,
                        strategy="row",
                        file_id=file_id,
                        group_name=db_name,
                        source_type="database",
                        table_name=tbl_name,
                        filename_override=f"{db_name} — {tbl_name}"
                    )
            except Exception as reg_err:
                print(f"[KBSyncWorker] Registry update warning: {reg_err}")

        with self.stats_lock:
            self.last_sync_time = datetime.now().isoformat()


# Global Singleton instance
sync_worker = SyncWorker()
