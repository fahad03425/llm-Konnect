import json
import os
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from collections import defaultdict
from app.core.config import settings

class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
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

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_session_data(self, session_id: str) -> Dict[str, Any]:
        path = self._get_file_path(session_id)
        if os.path.exists(path):
            try:
                from app.security.crypto import decrypt_bytes
                with open(path, "rb") as f:
                    raw_bytes = f.read()
                decrypted = decrypt_bytes(raw_bytes, allow_passthrough=True)
                raw = json.loads(decrypted.decode("utf-8"))
                if isinstance(raw, list):
                    # Backward compatibility for legacy raw turn lists
                    return {
                        "id": session_id,
                        "title": (raw[0]["content"][:60] if raw and "content" in raw[0] else "Conversation"),
                        "domain": "pharmacy",
                        "created_at": self._now_iso(),
                        "updated_at": self._now_iso(),
                        "messages": raw
                    }
                elif isinstance(raw, dict):
                    return raw
            except Exception:
                pass
        return {
            "id": session_id,
            "title": "New Conversation",
            "domain": "pharmacy",
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "messages": []
        }

    def _save_session_data(self, session_data: Dict[str, Any]):
        session_id = session_data.get("id", "default")
        path = self._get_file_path(session_id)
        try:
            from app.security.crypto import encrypt_bytes
            json_str = json.dumps(session_data, indent=2, ensure_ascii=False)
            raw_bytes = json_str.encode("utf-8")
            to_write = encrypt_bytes(raw_bytes) if getattr(settings, "encryption_enabled", True) else raw_bytes
            with open(path, "wb") as f:
                f.write(to_write)
        except Exception:
            pass

    def get_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self._sessions:
            self._sessions[session_id] = self._load_session_data(session_id)
        return self._sessions[session_id]

    def list_sessions(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        sessions_meta = []
        if not os.path.exists(self.storage_path):
            return []

        for fname in os.listdir(self.storage_path):
            if not fname.endswith(".json"):
                continue
            session_id = fname[:-5]
            data = self._load_session_data(session_id)
            if not data or not data.get("messages"):
                continue
            
            if domain and data.get("domain") and data.get("domain") != domain:
                continue

            msgs = data.get("messages", [])
            last_msg = msgs[-1]["content"] if msgs and "content" in msgs[-1] else ""
            if len(last_msg) > 120:
                last_msg = last_msg[:120] + "..."

            sessions_meta.append({
                "id": data.get("id", session_id),
                "title": data.get("title", "Conversation"),
                "domain": data.get("domain", "pharmacy"),
                "created_at": data.get("created_at", self._now_iso()),
                "updated_at": data.get("updated_at", self._now_iso()),
                "message_count": len(msgs),
                "last_message": last_msg
            })

        # Sort by updated_at descending
        sessions_meta.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions_meta

    def save_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        domain: str = "pharmacy",
        title: Optional[str] = None,
        selected_file_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        curr = self.get_session(session_id)
        curr["id"] = session_id
        curr["domain"] = domain or curr.get("domain", "pharmacy")
        curr["messages"] = messages
        curr["updated_at"] = self._now_iso()
        if selected_file_ids is not None:
            curr["selected_file_ids"] = selected_file_ids

        if title and title.strip():
            curr["title"] = title.strip()
        elif curr.get("title") in ("New Conversation", "Conversation", "", None):
            first_user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            if first_user_msg:
                curr["title"] = first_user_msg.strip()[:60]
            else:
                curr["title"] = "Conversation"

        self._sessions[session_id] = curr
        self._save_session_data(curr)
        return curr

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        domain: str = "pharmacy",
        route: Optional[str] = None,
        sources: Optional[List[Any]] = None,
        timing: Optional[float] = None
    ):
        curr = self.get_session(session_id)
        msg_obj: Dict[str, Any] = {
            "id": f"{int(time.time() * 1000)}-{role}",
            "role": role,
            "content": content,
            "timestamp": self._now_iso()
        }
        if route:
            msg_obj["route"] = route
        if sources:
            msg_obj["sources"] = sources
        if timing is not None:
            msg_obj["timing"] = timing

        if "messages" not in curr:
            curr["messages"] = []
        curr["messages"].append(msg_obj)
        curr["updated_at"] = self._now_iso()
        if domain:
            curr["domain"] = domain

        # Auto-set title from first user message if needed
        if curr.get("title") in ("New Conversation", "Conversation", "", None) and role == "user":
            curr["title"] = content.strip()[:60]

        self._sessions[session_id] = curr
        self._save_session_data(curr)

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        curr = self.get_session(session_id)
        msgs = curr.get("messages", [])
        
        # Format for LLM prompt context: [{"role": ..., "content": ...}]
        llm_turns = []
        for m in msgs:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
                llm_turns.append({"role": m["role"], "content": m["content"]})
        
        # Trim history if it exceeds max size (User + Assistant turns)
        max_turns = self.max_history_size * 2
        if len(llm_turns) > max_turns:
            llm_turns = llm_turns[-max_turns:]
        return llm_turns

    def get_last_assistant_turn(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Returns the most recent assistant message from this session, if any."""
        curr = self.get_session(session_id)
        msgs = curr.get("messages", [])
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                return m
        return None

    def update_title(self, session_id: str, title: str) -> bool:
        curr = self.get_session(session_id)
        curr["title"] = title.strip()
        curr["updated_at"] = self._now_iso()
        self._sessions[session_id] = curr
        self._save_session_data(curr)
        return True

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
        path = self._get_file_path(session_id)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except Exception:
                return False
        return True

    def clear_all_sessions(self) -> int:
        self._sessions.clear()
        count = 0
        if os.path.exists(self.storage_path):
            for fname in os.listdir(self.storage_path):
                if fname.endswith(".json"):
                    try:
                        os.remove(os.path.join(self.storage_path, fname))
                        count += 1
                    except Exception:
                        pass
        return count

    def reset_session(self, session_id: str):
        curr = self.get_session(session_id)
        curr["messages"] = []
        curr["updated_at"] = self._now_iso()
        self._sessions[session_id] = curr
        self._save_session_data(curr)

session_manager = SessionManager()
