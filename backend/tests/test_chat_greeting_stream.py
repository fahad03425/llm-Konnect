import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag.chat import rag_chat


@pytest.mark.parametrize("question", ["hi", "hello", "thanks", "who are you"])
def test_fast_reply_stream_matches_nonstream(question, monkeypatch):
    history = Mock()
    history.get_last_assistant_turn.return_value = None
    monkeypatch.setattr("app.rag.chat.session_manager", history)
    monkeypatch.setattr(rag_chat, "_rewrite_follow_up", lambda question, session_id: question)
    client = TestClient(app)
    payload = {"question": question, "session_id": "greeting-regression", "domain": "pharmacy"}
    response = client.post("/api/chat/stream", json=payload)
    assert response.status_code == 200
    chunks = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert chunks
    assert chunks[0]["route"] == "chit-chat"
    answer = "".join(chunk["chunk"] for chunk in chunks)
    assert answer.strip()
    regular = client.post("/api/chat", json=payload)
    assert regular.status_code == 200
    assert answer == regular.json()["answer"]
