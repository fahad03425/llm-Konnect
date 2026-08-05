import json
import os
from typing import List, Dict, Any
from collections import defaultdict
from app.core.config import settings

class SessionManager:
    def __init__(self):
        self._history: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        self.max_history_size = settings.llm_chat_history_size
        self.storage_path = os.path.join(settings.storage_dir, "sessions")
        os.makedirs(self.storage_path, exist_ok=True)
        
    def _get_file_path(self, session_id: str) -> str:
        # Prevent path traversal
        safe_id = os.path.basename(session_id)
        return os.path.join(self.storage_path, f"{safe_id}.json")
        
    def start_session(self) -> str:
        import uuid
        return str(uuid.uuid4())
        
    def append_turn(self, session_id: str, role: str, content: str):
        if session_id not in self._history:
            self._load(session_id)
            
        self._history[session_id].append({"role": role, "content": content})
        
        # Trim history if it exceeds max size
        if len(self._history[session_id]) > self.max_history_size * 2: # User + Assistant turns
            self._history[session_id] = self._history[session_id][-(self.max_history_size * 2):]
            
        self._save(session_id)
        
    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._history:
            self._load(session_id)
        return list(self._history[session_id])
        
    def reset_session(self, session_id: str):
        self._history[session_id] = []
        self._save(session_id)
        
    def _load(self, session_id: str):
        path = self._get_file_path(session_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._history[session_id] = json.load(f)
            except Exception:
                self._history[session_id] = []
        else:
            self._history[session_id] = []
            
    def _save(self, session_id: str):
        path = self._get_file_path(session_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._history[session_id], f)
        except Exception:
            pass # Fail silently on save error to keep it robust

session_manager = SessionManager()
