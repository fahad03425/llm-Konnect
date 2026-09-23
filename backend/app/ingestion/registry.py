"""File Registry Module for tracking ingested files, deduplication hashes, and metadata."""
import os
import sqlite3
import hashlib
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel

class FileRecord(BaseModel):
    file_id: str
    filename: str
    file_path: str
    file_hash: str
    chunk_count: int
    domain: str
    strategy: str
    status: str
    ingested_at: str
    file_size_bytes: Optional[int] = 0
    progress: Optional[float] = 0.0
    step_text: Optional[str] = ""
    error_message: Optional[str] = None
    group_name: Optional[str] = None
    source_type: Optional[str] = "file"
    table_name: Optional[str] = None

class DBConnectionRecord(BaseModel):
    database_name: str
    connection_string: str
    db_type: str
    domain: str = "pharmacy"
    strategy: str = "row"
    auto_sync: int = 1
    sync_interval_sec: int = 15
    last_synced_at: Optional[str] = None
    last_status: Optional[str] = "active"
    table_count: int = 0
    row_count: int = 0
    watermarks: Optional[str] = "{}"

_hash_cache: Dict[str, tuple] = {}


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class FileRegistry:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            storage_dir = os.path.join(PROJECT_ROOT, "data")
            os.makedirs(storage_dir, exist_ok=True)
            self.db_path = os.path.join(storage_dir, "file_registry.sqlite3")
        else:
            self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_registry (
                    file_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    chunk_count INTEGER DEFAULT 0,
                    domain TEXT DEFAULT 'pharmacy',
                    strategy TEXT DEFAULT 'row',
                    status TEXT DEFAULT 'active',
                    ingested_at TEXT NOT NULL,
                    file_size_bytes INTEGER DEFAULT 0,
                    progress REAL DEFAULT 0.0,
                    step_text TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    group_name TEXT DEFAULT NULL,
                    source_type TEXT DEFAULT 'file',
                    table_name TEXT DEFAULT NULL
                )
            """)
            conn.commit()

            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_hash_cache (
                    norm_path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    file_hash TEXT NOT NULL
                )
            """)
            conn.commit()

            conn.execute("""
                CREATE TABLE IF NOT EXISTS db_connections (
                    database_name TEXT PRIMARY KEY,
                    connection_string TEXT NOT NULL,
                    db_type TEXT NOT NULL,
                    domain TEXT DEFAULT 'pharmacy',
                    strategy TEXT DEFAULT 'row',
                    auto_sync INTEGER DEFAULT 1,
                    sync_interval_sec INTEGER DEFAULT 15,
                    last_synced_at TEXT,
                    last_status TEXT DEFAULT 'active',
                    table_count INTEGER DEFAULT 0,
                    row_count INTEGER DEFAULT 0,
                    watermarks TEXT DEFAULT '{}'
                )
            """)
            conn.commit()

            # Ensure newly added columns exist in older database files
            cursor = conn.execute("PRAGMA table_info(file_registry)")
            cols = {row["name"] for row in cursor.fetchall()}
            if "progress" not in cols:
                conn.execute("ALTER TABLE file_registry ADD COLUMN progress REAL DEFAULT 0.0")
            if "step_text" not in cols:
                conn.execute("ALTER TABLE file_registry ADD COLUMN step_text TEXT DEFAULT ''")
            if "error_message" not in cols:
                conn.execute("ALTER TABLE file_registry ADD COLUMN error_message TEXT DEFAULT ''")
            if "group_name" not in cols:
                conn.execute("ALTER TABLE file_registry ADD COLUMN group_name TEXT DEFAULT NULL")
            if "source_type" not in cols:
                conn.execute("ALTER TABLE file_registry ADD COLUMN source_type TEXT DEFAULT 'file'")
            if "table_name" not in cols:
                conn.execute("ALTER TABLE file_registry ADD COLUMN table_name TEXT DEFAULT NULL")
            conn.commit()

    @staticmethod
    def normalize_path(file_path: str) -> str:
        if not os.path.isabs(file_path):
            # Check if relative to project root or current dir
            proj_cand = os.path.join(PROJECT_ROOT, file_path)
            if os.path.exists(proj_cand) or file_path.startswith("data"):
                file_path = proj_cand
        return os.path.abspath(file_path).replace("\\", "/")

    def calculate_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of a file with persistent DB + memory caching."""
        if not os.path.exists(file_path):
            return ""
        norm_path = self.normalize_path(file_path)
        try:
            mtime = os.path.getmtime(file_path)
            size = os.path.getsize(file_path)
            # 1. Fast in-memory check
            cached = _hash_cache.get(norm_path)
            if cached and cached[0] == mtime and cached[1] == size:
                return cached[2]
        except Exception:
            mtime = 0.0
            size = 0

        # 2. Fast SQLite persistent cache check
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT mtime, size_bytes, file_hash FROM file_hash_cache WHERE norm_path = ?", (norm_path,))
                row = cursor.fetchone()
                if row and abs(row["mtime"] - mtime) < 0.001 and row["size_bytes"] == size:
                    h = row["file_hash"]
                    _hash_cache[norm_path] = (mtime, size, h)
                    return h
        except Exception:
            pass

        # 3. Compute SHA-256 using large 1MB buffer (or decrypted content if encrypted at rest)
        sha256 = hashlib.sha256()
        try:
            from app.security.crypto import is_encrypted_file, decrypt_file_to_bytes
            if is_encrypted_file(file_path):
                decrypted = decrypt_file_to_bytes(file_path)
                sha256.update(decrypted)
            else:
                with open(file_path, "rb") as f:
                    while chunk := f.read(1048576):
                        sha256.update(chunk)
            h = sha256.hexdigest()
            _hash_cache[norm_path] = (mtime, size, h)
            # Save to persistent cache
            try:
                with self._get_connection() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO file_hash_cache (norm_path, mtime, size_bytes, file_hash) VALUES (?, ?, ?, ?)",
                        (norm_path, mtime, size, h)
                    )
                    conn.commit()
            except Exception:
                pass
            return h
        except Exception:
            return ""

    def get_file_by_hash(self, file_hash: str, active_only: bool = False) -> Optional[FileRecord]:
        if not file_hash:
            return None
        with self._get_connection() as conn:
            if active_only:
                cursor = conn.execute("SELECT * FROM file_registry WHERE file_hash = ? AND status = 'active'", (file_hash,))
            else:
                cursor = conn.execute(
                    """SELECT * FROM file_registry 
                       WHERE file_hash = ? AND status IN ('active', 'processing') 
                       ORDER BY CASE WHEN status = 'active' THEN 1 ELSE 2 END, ingested_at DESC""", 
                    (file_hash,)
                )
            row = cursor.fetchone()
            if row:
                return FileRecord(**dict(row))
        return None

    def get_file_by_id(self, file_id: str) -> Optional[FileRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM file_registry WHERE file_id = ?", (file_id,))
            row = cursor.fetchone()
            if row:
                return FileRecord(**dict(row))
        return None

    def get_file_by_path(self, file_path: str) -> Optional[FileRecord]:
        normalized = self.normalize_path(file_path)
        raw_norm = file_path.replace("\\", "/")
        fname = os.path.basename(file_path)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM file_registry 
                   WHERE (LOWER(file_path) = LOWER(?) OR LOWER(file_path) = LOWER(?) OR LOWER(file_path) = LOWER(?) OR LOWER(filename) = LOWER(?)) 
                   ORDER BY ingested_at DESC""", 
                (normalized, file_path, raw_norm, fname)
            )
            row = cursor.fetchone()
            if row:
                return FileRecord(**dict(row))
        return None

    def list_files(self, domain: Optional[str] = None, status: Optional[str] = None, include_all: bool = False) -> List[FileRecord]:
        """List files with optional domain or status filter. If include_all=True, returns all files regardless of status."""
        with self._get_connection() as conn:
            query = "SELECT * FROM file_registry"
            clauses = []
            params: List[Any] = []

            if domain:
                clauses.append("domain = ?")
                params.append(domain)

            if status:
                clauses.append("status = ?")
                params.append(status)
            elif not include_all:
                clauses.append("status = 'active'")

            if clauses:
                query += " WHERE " + " AND ".join(clauses)

            query += " ORDER BY ingested_at DESC"
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [FileRecord(**dict(r)) for r in rows]

    def register_or_update(
        self,
        file_path: str,
        chunk_count: int,
        domain: str = "pharmacy",
        strategy: str = "row",
        file_id: Optional[str] = None,
        group_name: Optional[str] = None,
        source_type: Optional[str] = "file",
        table_name: Optional[str] = None,
        filename_override: Optional[str] = None
    ) -> FileRecord:
        canonical_path = self.normalize_path(file_path) if source_type == "file" else file_path.replace("\\", "/")
        file_hash = self.calculate_hash(file_path) if (source_type == "file" and os.path.exists(file_path)) else hashlib.sha256(canonical_path.encode()).hexdigest()
        filename = filename_override or os.path.basename(file_path) or table_name or "data_source"
        file_size = os.path.getsize(file_path) if (source_type == "file" and os.path.exists(file_path)) else 0
        ingested_at = datetime.now().isoformat()
        
        existing = None
        if file_id:
            existing = self.get_file_by_id(file_id)
        if not existing:
            existing = self.get_file_by_path(canonical_path) or (self.get_file_by_hash(file_hash) if source_type == "file" else None)

        with self._get_connection() as conn:
            if existing:
                fid = existing.file_id
                conn.execute("""
                    UPDATE file_registry 
                    SET filename = ?, file_path = ?, file_hash = ?, chunk_count = ?, domain = ?, strategy = ?, 
                        status = 'active', progress = 100.0, step_text = 'Completed', error_message = '', 
                        ingested_at = ?, file_size_bytes = ?, group_name = COALESCE(?, group_name),
                        source_type = COALESCE(?, source_type), table_name = COALESCE(?, table_name)
                    WHERE file_id = ?
                """, (filename, canonical_path, file_hash, chunk_count, domain, strategy, ingested_at, file_size, group_name, source_type, table_name, fid))
            else:
                fid = file_id or f"file_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT INTO file_registry (file_id, filename, file_path, file_hash, chunk_count, domain, strategy, status, ingested_at, file_size_bytes, progress, step_text, error_message, group_name, source_type, table_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 100.0, 'Completed', '', ?, ?, ?)
                """, (fid, filename, canonical_path, file_hash, chunk_count, domain, strategy, ingested_at, file_size, group_name, source_type, table_name))
            conn.commit()

        return self.get_file_by_id(fid)

    def set_file_status(
        self,
        file_path: str,
        status: str,
        file_id: Optional[str] = None,
        domain: str = "pharmacy",
        strategy: str = "row",
        progress: float = 0.0,
        step_text: str = "",
        error_message: Optional[str] = None,
        group_name: Optional[str] = None,
        source_type: Optional[str] = "file",
        table_name: Optional[str] = None,
        filename_override: Optional[str] = None
    ) -> FileRecord:
        canonical_path = self.normalize_path(file_path) if source_type == "file" else file_path.replace("\\", "/")
        filename = filename_override or os.path.basename(file_path) or table_name or "data_source"
        file_size = os.path.getsize(file_path) if (source_type == "file" and os.path.exists(file_path)) else 0
        file_hash = self.calculate_hash(file_path) if (source_type == "file" and os.path.exists(file_path)) else hashlib.sha256(canonical_path.encode()).hexdigest()
        now_ts = datetime.now().isoformat()
        err_str = error_message or ""
        
        existing = None
        if file_id:
            existing = self.get_file_by_id(file_id)
        if not existing:
            existing = self.get_file_by_path(canonical_path)

        with self._get_connection() as conn:
            if existing:
                fid = existing.file_id
                conn.execute("""
                    UPDATE file_registry 
                    SET filename = ?, file_path = ?, status = ?, domain = ?, strategy = ?, 
                        file_size_bytes = ?, progress = ?, step_text = ?, error_message = ?, ingested_at = ?,
                        group_name = COALESCE(?, group_name), source_type = COALESCE(?, source_type), table_name = COALESCE(?, table_name)
                    WHERE file_id = ?
                """, (filename, canonical_path, status, domain, strategy, file_size, progress, step_text, err_str, now_ts, group_name, source_type, table_name, fid))
            else:
                fid = file_id or f"file_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT INTO file_registry (file_id, filename, file_path, file_hash, chunk_count, domain, strategy, status, ingested_at, file_size_bytes, progress, step_text, error_message, group_name, source_type, table_name)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (fid, filename, canonical_path, file_hash, domain, strategy, status, now_ts, file_size, progress, step_text, err_str, group_name, source_type, table_name))
            conn.commit()

        return self.get_file_by_id(fid)

    def update_progress(
        self,
        file_path: str,
        progress: float,
        step_text: str,
        status: str = "processing",
        error_message: Optional[str] = None
    ) -> Optional[FileRecord]:
        existing = self.get_file_by_path(file_path)
        if not existing:
            return None
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE file_registry 
                SET progress = ?, step_text = ?, status = ?, error_message = ?
                WHERE file_id = ?
            """, (progress, step_text, status, error_message or "", existing.file_id))
            conn.commit()
        return self.get_file_by_id(existing.file_id)

    def cleanup_stale_processing(self, active_keys: Optional[set] = None):
        """Clean up orphaned tasks left in 'processing' state after restart or failure."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM file_registry WHERE status = 'processing'")
            rows = cursor.fetchall()
            for r in rows:
                norm = r["file_path"].replace("\\", "/").lower()
                if active_keys is not None and norm in active_keys:
                    continue
                if r["chunk_count"] and r["chunk_count"] > 0:
                    conn.execute("UPDATE file_registry SET status = 'active', progress = 100.0, step_text = 'Completed' WHERE file_id = ?", (r["file_id"],))
                else:
                    conn.execute("DELETE FROM file_registry WHERE file_id = ?", (r["file_id"],))
            conn.commit()

    def delete_file(self, file_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM file_registry WHERE file_id = ? OR file_path = ?", (file_id, file_id))
            conn.commit()
            return cursor.rowcount > 0

    def delete_group(self, group_name: str) -> int:
        """Delete all records belonging to a database group."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM file_registry WHERE group_name = ? OR LOWER(group_name) = LOWER(?)", (group_name, group_name))
            conn.commit()
            return cursor.rowcount

    def clear_all(self):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM file_registry")
            conn.commit()

    # -------------------------------------------------------------
    # Database Connections & Auto-Sync Tracking
    # -------------------------------------------------------------
    def save_db_connection(
        self,
        database_name: str,
        connection_string: str,
        db_type: str,
        domain: str = "pharmacy",
        strategy: str = "row",
        auto_sync: int = 1,
        sync_interval_sec: int = 15,
        last_status: str = "active",
        table_count: int = 0,
        row_count: int = 0
    ) -> DBConnectionRecord:
        now_ts = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO db_connections (
                    database_name, connection_string, db_type, domain, strategy,
                    auto_sync, sync_interval_sec, last_synced_at, last_status, table_count, row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(database_name) DO UPDATE SET
                    connection_string = excluded.connection_string,
                    db_type = excluded.db_type,
                    domain = excluded.domain,
                    strategy = excluded.strategy,
                    auto_sync = excluded.auto_sync,
                    sync_interval_sec = excluded.sync_interval_sec,
                    last_status = excluded.last_status,
                    table_count = CASE WHEN excluded.table_count > 0 THEN excluded.table_count ELSE table_count END,
                    row_count = CASE WHEN excluded.row_count > 0 THEN excluded.row_count ELSE row_count END
            """, (database_name, connection_string, db_type, domain, strategy, auto_sync, sync_interval_sec, now_ts, last_status, table_count, row_count))
            conn.commit()
        return self.get_db_connection(database_name)

    def get_db_connection(self, database_name: str) -> Optional[DBConnectionRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM db_connections WHERE LOWER(database_name) = LOWER(?)",
                (database_name,)
            )
            row = cursor.fetchone()
            if row:
                return DBConnectionRecord(**dict(row))
        return None

    def list_db_connections(self, auto_sync_only: bool = False) -> List[DBConnectionRecord]:
        with self._get_connection() as conn:
            query = "SELECT * FROM db_connections"
            if auto_sync_only:
                query += " WHERE auto_sync = 1 AND last_status = 'active'"
            cursor = conn.execute(query)
            return [DBConnectionRecord(**dict(r)) for r in cursor.fetchall()]

    def update_db_sync_status(
        self,
        database_name: str,
        status: str,
        last_synced_at: Optional[str] = None,
        table_count: Optional[int] = None,
        row_count: Optional[int] = None,
        watermarks: Optional[str] = None
    ):
        now_ts = last_synced_at or datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE db_connections
                SET last_status = ?,
                    last_synced_at = ?,
                    table_count = COALESCE(?, table_count),
                    row_count = COALESCE(?, row_count),
                    watermarks = COALESCE(?, watermarks)
                WHERE LOWER(database_name) = LOWER(?)
            """, (status, now_ts, table_count, row_count, watermarks, database_name))
            conn.commit()

    def delete_db_connection(self, database_name: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM db_connections WHERE LOWER(database_name) = LOWER(?)",
                (database_name,)
            )
            conn.commit()
            return cursor.rowcount > 0

file_registry = FileRegistry()
file_registry.cleanup_stale_processing()

# Pre-seed active connection for PharmacyPOS if present in registry
try:
    existing_pharmacy = file_registry.get_db_connection("PharmacyPOS")
    should_seed = False
    if not existing_pharmacy:
        should_seed = True
    elif "pytest" in existing_pharmacy.connection_string or (
        existing_pharmacy.db_type == "sqlite" and not os.path.exists(existing_pharmacy.connection_string.replace("sqlite:///", ""))
    ):
        should_seed = True

    if should_seed:
        file_registry.save_db_connection(
            database_name="PharmacyPOS",
            connection_string="mssql+pyodbc://localhost\\SQLEXPRESS/PharmacyPOS?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes",
            db_type="mssql",
            domain="pharmacy",
            strategy="row",
            auto_sync=1,
            sync_interval_sec=15,
            last_status="active",
            table_count=10,
            row_count=772
        )
except Exception:
    pass

