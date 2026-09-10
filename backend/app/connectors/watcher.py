"""
Automated Directory File Watcher Connector.
Monitors local folders for newly dropped export files (CSV, Excel, JSON).
"""

import os
import glob
from typing import List, Dict, Any, Optional
from app.connectors.base import Connector, detect_connector

class DirectoryWatcherConnector(Connector):
    def __init__(self, watch_dir: str, file_pattern: str = "*.*"):
        self.watch_dir = watch_dir
        self.file_pattern = file_pattern

    def get_latest_file(() -> Optional[str]:
        pass  # Helper method

    def list_pending_files(self) -> List[str]:
        if not os.path.exists(self.watch_dir):
            return []
        
        pattern = os.path.join(self.watch_dir, self.file_pattern)
        files = [f for f in glob.glob(pattern) if os.path.isfile(f) and f.endswith(('.csv', '.xlsx', '.xls', '.json'))]
        # Sort by modification time (latest first)
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return files

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
