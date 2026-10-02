from app.rag.chat import RAGChat
from app.rag.router import RouteType, classify_route


def test_followup_keeps_customer_when_referent_is_pronoun(monkeypatch):
    history = [
        {"role": "user", "content": "How many purchases did Talha Awan make?"},
        {"role": "assistant", "content": "Matching records: 45."},
        {"role": "user", "content": "What product did he buy most often?"},
        {"role": "assistant", "content": "Most frequently purchased product: Mefenamic Acid 500mg Tablets 20s (4 purchases)."},
    ]
    monkeypatch.setattr("app.rag.chat.session_manager.get_history", lambda _sid: history)

    rewritten = RAGChat._rewrite_follow_up(
        "What total quantity of that product did they buy?", "test-session"
    )

    assert "Talha Awan" in rewritten
    assert "Mefenamic Acid 500mg Tablets 20s" in rewritten
    assert classify_route(rewritten) == RouteType.ANALYTICS


def test_followup_keeps_entity_from_compact_group_answer(monkeypatch):
    history = [
        {"role": "user", "content": "Which branch sold the most Glucometer Test Strips 50s in April 2026?"},
        {"role": "assistant", "content": "Faisal Town Health Mart: 16 (3 records)."},
    ]
    monkeypatch.setattr("app.rag.chat.session_manager.get_history", lambda _sid: history)

    rewritten = RAGChat._rewrite_follow_up(
        "What was the total sales amount for that product at that branch in April?",
        "test-session",
    )

    assert "Faisal Town Health Mart" in rewritten
    assert "Glucometer Test Strips 50s" in rewritten
    assert "April 2026" in rewritten

