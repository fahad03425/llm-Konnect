"""
Automated Directory File Watcher Connector.
Monitors local folders for newly dropped export files (CSV, Excel, JSON, MDF).
"""

import os
import glob
import re
from typing import List, Dict, Any, Optional
from app.connectors.base import Connector, detect_connector

def resolve_target_path(raw_path: str) -> tuple[Optional[str], Optional[str]]:
    """
    Intelligently resolves target path, handling common cases such as:
    - User pointing to a log file (e.g. PharmacyPOS_log.mdf or PharmacyPOS_log.ldf -> PharmacyPOS.mdf)
    - User typing a filename where parent directory exists
    Returns (resolved_path, error_or_info_message)
    """
    clean = raw_path.strip("\"' ")
    if not clean:
        return None, "Please enter a valid directory path or database file path."

    if os.path.exists(clean):
        # If user pointed to a transaction log (.ldf), map to the primary .mdf
        if clean.lower().endswith(".ldf"):
            mdf_cand = re.sub(r'_log\.ldf$', '.mdf', clean, flags=re.IGNORECASE)
            if not os.path.exists(mdf_cand):
                mdf_cand = clean[:-4] + ".mdf"
            if os.path.exists(mdf_cand):
                return mdf_cand, None
        return clean, None

    parent = os.path.dirname(clean)
    base = os.path.basename(clean)

    if parent and os.path.isdir(parent):
        # Case A: User typed _log.mdf instead of .mdf (e.g. PharmacyPOS_log.mdf -> PharmacyPOS.mdf)
        if "_log" in base.lower():
            candidate = os.path.join(parent, re.sub(r'_log', '', base, flags=re.IGNORECASE))
            if os.path.exists(candidate):
                return candidate, None

        # Case B: Prefix match for primary .mdf
        prefix = base.split('_')[0].split('.')[0]
        if prefix:
            matches = glob.glob(os.path.join(parent, f"{prefix}*.mdf"))
            data_mdfs = [m for m in matches if not m.lower().endswith("_log.mdf")]
            if data_mdfs:
                return data_mdfs[0], None

        # Case C: Any user .mdf in that folder
        all_mdfs = glob.glob(os.path.join(parent, "*.mdf"))
        user_mdfs = [
            m for m in all_mdfs
            if os.path.basename(m).lower() not in ("master.mdf", "model.mdf", "msdbdata.mdf", "tempdb.mdf")
            and not m.lower().endswith("_log.mdf")
        ]
        if user_mdfs:
            return user_mdfs[0], None

        return None, f"File '{base}' was not found in directory '{parent}'."

    return None, f"Directory or file does not exist: '{clean}'. Please verify the path."

class DirectoryWatcherConnector(Connector):
    def __init__(self, watch_dir: str, file_pattern: str = "*.*"):
        self.raw_watch_dir = watch_dir.strip("\"' ")
        self.file_pattern = file_pattern
        self.resolved_watch_dir, self.resolve_error = resolve_target_path(self.raw_watch_dir)
        self.watch_dir = self.resolved_watch_dir or self.raw_watch_dir

    def get_latest_file(self) -> Optional[str]:
        pending = self.list_pending_files()
        return pending[0] if pending else None

    def list_pending_files(self) -> List[str]:
        if not self.resolved_watch_dir or not os.path.exists(self.watch_dir):
            raise FileNotFoundError(
                self.resolve_error or f"Path does not exist: '{self.raw_watch_dir}'. Please verify the directory or file path."
            )
        
        # If user passed a specific file path directly
        if os.path.isfile(self.watch_dir):
            ext = os.path.splitext(self.watch_dir)[1].lower()
            if ext in ('.csv', '.xlsx', '.xls', '.json', '.mdf'):
                return [self.watch_dir.replace("\\", "/")]
            raise ValueError(f"File '{os.path.basename(self.watch_dir)}' is not a supported data format (.csv, .xlsx, .json, .mdf).")

        pattern = os.path.join(self.watch_dir, self.file_pattern)
        files = [
            f.replace("\\", "/")
            for f in glob.glob(pattern)
            if os.path.isfile(f) and f.lower().endswith(('.csv', '.xlsx', '.xls', '.json', '.mdf'))
        ]
        # Sort by modification time (latest first)
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return files

    def detect_sql_database(self) -> Optional[Dict[str, Any]]:
        """
        Detects if the monitored target is an MS SQL Server database file (.mdf)
        or if the target directory contains .mdf files.
        """
        if not os.path.exists(self.watch_dir):
            return None

        mdf_path = None
        if os.path.isfile(self.watch_dir) and self.watch_dir.lower().endswith(".mdf"):
            mdf_path = self.watch_dir
        elif os.path.isdir(self.watch_dir):
            mdfs = glob.glob(os.path.join(self.watch_dir, "*.mdf"))
            user_mdfs = [
                m for m in mdfs
                if os.path.basename(m).lower() not in ("master.mdf", "model.mdf", "msdbdata.mdf", "tempdb.mdf")
                and not m.lower().endswith("_log.mdf")
            ]
            if user_mdfs:
                user_mdfs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                mdf_path = user_mdfs[0]
            elif mdfs:
                mdfs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                mdf_path = mdfs[0]

        if not mdf_path:
            return None

        try:
            from app.connectors.sql import SQLConnector
            connector = SQLConnector(mdf_path, db_type="mssql")
            db_name = connector.extract_db_name()
            tables = connector.list_tables()
            return {
                "database_name": db_name,
                "file_name": os.path.basename(mdf_path),
                "file_path": mdf_path.replace("\\", "/"),
                "server": r".\SQLEXPRESS",
                "is_attached": True,
                "table_count": len(tables),
                "tables": tables,
                "connection_string": mdf_path.replace("\\", "/")
            }
        except Exception as e:
            base = os.path.basename(mdf_path)
            db_name, _ = os.path.splitext(base)
            return {
                "database_name": db_name,
                "file_name": base,
                "file_path": mdf_path.replace("\\", "/"),
                "server": r".\SQLEXPRESS",
                "is_attached": False,
                "table_count": 0,
                "tables": [],
                "error": str(e),
                "connection_string": mdf_path.replace("\\", "/")
            }

    def fetch(self, file_path: Optional[str] = None, **kwargs):
        target_file = file_path
        if not target_file:
            pending = self.list_pending_files()
            if not pending:
                raise FileNotFoundError(f"No pending data files found in directory: {self.watch_dir}")
            target_file = pending[0]

        connector = detect_connector(target_file)
        return connector.fetch(**kwargs)

    def preview(self, n: int = 5, file_path: Optional[str] = None, **kwargs):
        target_file = file_path
        if not target_file:
            pending = self.list_pending_files()
            if not pending:
                raise FileNotFoundError(f"No pending data files found in directory: {self.watch_dir}")
            target_file = pending[0]

        connector = detect_connector(target_file)
        return connector.preview(n=n, **kwargs)

    def describe(self) -> str:
        return f"Directory Watcher Connector monitoring: {self.watch_dir}"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "watch_dir": self.watch_dir,
            "supports_auto_sync": True
        }
