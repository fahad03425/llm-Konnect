from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.rag.models import ChatRequest, ChatResponse
from app.rag.chat import rag_chat
from app.rag.history import session_manager

router = APIRouter(prefix="/api/chat", tags=["chat"])

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
            media_type="application/x-ndjson"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session")
def start_session():
    """Start a new chat session and return its ID."""
    session_id = session_manager.start_session()
    return {"session_id": session_id}

@router.get("/session/{session_id}/history")
def get_session_history(session_id: str):
    """Get the history of a session."""
    history = session_manager.get_history(session_id)
    return {"session_id": session_id, "history": history}

@router.delete("/session/{session_id}")
def reset_session(session_id: str):
    """Reset a chat session."""
    session_manager.reset_session(session_id)
    return {"message": "Session reset successfully"}
