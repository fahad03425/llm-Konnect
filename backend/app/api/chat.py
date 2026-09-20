from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.rag.models import ChatRequest, ChatResponse
from app.rag.chat import rag_chat
from app.rag.history import session_manager

from app.core.llm import llm

router = APIRouter(prefix="/api/chat", tags=["chat"])

class SelectModelRequest(BaseModel):
    model: str = Field(..., description="Name of local model to activate")

class SaveSessionRequest(BaseModel):
    messages: List[Dict[str, Any]] = Field(..., description="Full list of messages in this chat session")
    domain: Optional[str] = Field("pharmacy", description="The domain/vertical")
    title: Optional[str] = Field(None, description="Optional custom session title")
    selected_file_ids: Optional[List[str]] = Field(None, description="Optional list of scoped file/table IDs")

class UpdateTitleRequest(BaseModel):
    title: str = Field(..., description="New session title")

@router.get("/models")
def list_chat_models():
    """List all installed local models and the currently active model."""
    try:
        models = llm.list_installed_models()
        return {"models": models, "active_model": llm.model}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/models/select")
def select_chat_model(payload: SelectModelRequest):
    """Set active model for chat inference."""
    try:
        active = llm.set_active_model(payload.model)
        return {"status": "ok", "active_model": active}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Non-streaming chat endpoint."""
    try:
        response = rag_chat.ask(request)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stream")
def chat_stream(request: ChatRequest):
    """Streaming chat endpoint for interactive UI."""
    try:
        return StreamingResponse(
            rag_chat.ask_stream(request), 
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions")
def list_sessions(domain: Optional[str] = Query(None, description="Optional filter by domain")):
    """List all saved chat sessions metadata."""
    try:
        sessions = session_manager.list_sessions(domain=domain)
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Get full session data including all messages."""
    try:
        session_data = session_manager.get_session(session_id)
        return session_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/save")
def save_session(session_id: str, payload: SaveSessionRequest):
    """Save or update full conversation messages and metadata."""
    try:
        saved = session_manager.save_session(
            session_id=session_id,
            messages=payload.messages,
            domain=payload.domain or "pharmacy",
            title=payload.title,
            selected_file_ids=payload.selected_file_ids
        )
        return {"status": "ok", "session": saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/sessions/{session_id}/title")
def update_session_title(session_id: str, payload: UpdateTitleRequest):
    """Update title of a conversation session."""
    try:
        ok = session_manager.update_title(session_id, payload.title)
        return {"status": "ok" if ok else "error"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """Delete a specific session."""
    try:
        ok = session_manager.delete_session(session_id)
        return {"status": "ok" if ok else "error", "message": "Session deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/sessions")
def clear_all_sessions():
    """Clear all saved chat sessions."""
    try:
        deleted_count = session_manager.clear_all_sessions()
        return {"status": "ok", "deleted_count": deleted_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Backward compatibility endpoints
@router.post("/session")
def start_session():
    """Start a new chat session and return its ID."""
    session_id = session_manager.start_session()
    return {"session_id": session_id}

@router.get("/session/{session_id}/history")
def get_session_history(session_id: str):
    """Get the prompt history of a session."""
    history = session_manager.get_history(session_id)
    return {"session_id": session_id, "history": history}

@router.delete("/session/{session_id}")
def reset_session(session_id: str):
    """Reset a chat session."""
    session_manager.reset_session(session_id)
    return {"message": "Session reset successfully"}
