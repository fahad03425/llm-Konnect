import pytest
from app.rag.history import SessionManager
from app.rag.models import SourceReference

def test_session_manager_full_flow(tmp_path):
    manager = SessionManager()
    manager.storage_path = str(tmp_path / "test_sessions")
    import os
    os.makedirs(manager.storage_path, exist_ok=True)

    session_id = "sess-test-123"
    
    # 1. Append turns with rich metadata
    manager.append_turn(
        session_id=session_id,
        role="user",
        content="What are the top expiring medicines in pharmacy?",
        domain="pharmacy"
    )
    
    sources = [{"source_file": "inv.csv", "source_row": 12, "label": "Invoice 12"}]
    manager.append_turn(
        session_id=session_id,
        role="assistant",
        content="Panadol and Brufen are expiring soon.",
        domain="pharmacy",
        route="rag",
        sources=sources,
        timing=0.45
    )

    # 2. Get session
    session = manager.get_session(session_id)
    assert session["id"] == session_id
    assert session["domain"] == "pharmacy"
    assert session["title"] == "What are the top expiring medicines in pharmacy?"
    assert len(session["messages"]) == 2
    assert session["messages"][1]["route"] == "rag"
    assert session["messages"][1]["sources"] == sources
    assert session["messages"][1]["timing"] == 0.45

    # 3. List sessions
    sessions_list = manager.list_sessions()
    assert len(sessions_list) == 1
    assert sessions_list[0]["id"] == session_id
    assert sessions_list[0]["message_count"] == 2
    assert "Panadol and Brufen" in sessions_list[0]["last_message"]

    # 4. Update title
    manager.update_title(session_id, "Custom Pharmacy Expiry Analysis")
    session = manager.get_session(session_id)
    assert session["title"] == "Custom Pharmacy Expiry Analysis"

    # 5. Save full session update
    new_messages = session["messages"] + [{
        "id": "new-msg-1",
        "role": "user",
        "content": "Thank you!",
        "timestamp": "2026-09-11T12:00:00Z"
    }]
    manager.save_session(session_id, new_messages, domain="pharmacy")
    session = manager.get_session(session_id)
    assert len(session["messages"]) == 3

    # 6. Delete session
    assert manager.delete_session(session_id) is True
    assert len(manager.list_sessions()) == 0
