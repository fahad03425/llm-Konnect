import pytest
from unittest.mock import patch, MagicMock
from app.rag.router import classify_route, extract_filters, AnalyticsRouter, RouteType
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest
from app.ingestion.models import RetrievedChunk
from app.rag.history import SessionManager

def test_classify_route():
    # Chit-chat
    assert classify_route("hello there") == RouteType.CHITCHAT
    assert classify_route("what can you do?") == RouteType.CHITCHAT
    
    # Analytics
    assert classify_route("what is the total sales?") == RouteType.ANALYTICS
    assert classify_route("how much profit did we make") == RouteType.ANALYTICS
    assert classify_route("how many items are expiring") == RouteType.ANALYTICS
    assert classify_route("average margin") == RouteType.ANALYTICS
    assert classify_route("kitna profit hua") == RouteType.ANALYTICS
    
    # RAG lookup
    assert classify_route("which supplier sold batch B1") == RouteType.RAG
    assert classify_route("did we return anything to Getz") == RouteType.RAG
    assert classify_route("show me invoices from yesterday") == RouteType.RAG

def test_extract_filters():
    # Simplistic month extraction
    assert extract_filters("sales in january") == {"month": 1}
    assert extract_filters("profit in oct") == {"month": 10}
    assert extract_filters("what happened in may") == {"month": 5}
    assert extract_filters("sales for yesterday") == {}

def test_analytics_seam_is_backed_by_the_kpi_engine():
    """
    Module 6.6 superseded the temporary in-router pandas fallback: the numeric
    route now returns real KPIEngine results (keyed by KPI key, with provenance)
    instead of the old ad-hoc 'total_amount'/'average_amount' aggregates.
    """
    router = AnalyticsRouter()

    records = [
        {"source_row": 1, "amount": 100, "quantity": 10},
        {"source_row": 2, "amount": 250, "quantity": 5},
        {"source_row": 3, "amount": 50,  "quantity": 2},
    ]

    # total -> total_revenue, computed by the engine
    comp, sources = router.compute("total sales", {}, records)
    assert comp["total_revenue"]["value"] == 400.0
    assert comp["total_revenue"]["status"] == "ok"
    assert comp["total_revenue"]["provenance"]["rows_used"] == 3
    assert set(sources) == {1, 2, 3}

    # average -> average_transaction_value (no invoice_id here, so 3 transactions)
    comp, _ = router.compute("average amount", {}, records)
    assert comp["average_transaction_value"]["value"] == round(400.0 / 3, 2)
    assert comp["transaction_count"]["value"] == 3.0

    # Expiry analytics are pharmacy-specific and deliberately NOT part of 6.6 core;
    # a "how many" question resolves to the domain-agnostic transaction count.
    comp, _ = router.compute("how many transactions", {}, records)
    assert comp["transaction_count"]["value"] == 3.0

def test_urdu_digit_normalization():
    chat = RAGChat()
    assert chat._normalize_question("batch ۱۲۳۴") == "batch 1234"

@patch("app.rag.chat.llm")
@patch("app.rag.chat.session_manager")
@patch("app.rag.chat.KnowledgeBase")
def test_rag_empty_shortcircuit(mock_kb_class, mock_session, mock_llm):
    # Setup mocks
    mock_kb = MagicMock()
    mock_kb.search.return_value = []
    mock_kb_class.return_value = mock_kb
    
    chat = RAGChat()
    req = ChatRequest(question="unknown batch xyz", session_id="test_session")
    
    resp = chat.ask(req)
    
    assert resp.route == RouteType.RAG
    assert "couldn't find anything" in resp.answer
    assert not resp.sources
    mock_llm.chat.assert_not_called()  # Must short-circuit

@patch("app.rag.chat.llm")
@patch("app.rag.chat.session_manager")
@patch("app.rag.chat.KnowledgeBase")
def test_rag_with_results(mock_kb_class, mock_session, mock_llm):
    mock_kb = MagicMock()
    mock_kb.search.return_value = [
        RetrievedChunk(text="invoice 101 for panadol", metadata={"invoice_id": "101", "source_row": 10}, score=0.9),
        RetrievedChunk(text="invoice 102 for brufen", metadata={"product_id": "brufen", "source_row": 15}, score=0.85)
    ]
    mock_kb_class.return_value = mock_kb
    mock_llm.chat.return_value = "I found panadol and brufen."
    
    chat = RAGChat()
    req = ChatRequest(question="show me invoices", session_id="test_session")
    
    resp = chat.ask(req)
    
    assert resp.route == RouteType.RAG
    assert resp.answer == "I found panadol and brufen."
    assert len(resp.sources) == 2
    assert resp.sources[0].source_row == 10
    assert resp.sources[0].label == "Invoice 101"
    assert resp.sources[1].source_row == 15
    assert resp.sources[1].label == "Product brufen"
    
    mock_llm.chat.assert_called_once()
    
def test_history_windowing():
    # Use real session manager to test bounded history
    manager = SessionManager()
    manager.max_history_size = 2 # 2 turns (4 messages)
    sid = "test_bound"
    
    manager.append_turn(sid, "user", "q1")
    manager.append_turn(sid, "assistant", "a1")
    manager.append_turn(sid, "user", "q2")
    manager.append_turn(sid, "assistant", "a2")
    manager.append_turn(sid, "user", "q3")
    manager.append_turn(sid, "assistant", "a3")
    
    history = manager.get_history(sid)
    assert len(history) == 4
    assert history[0]["content"] == "q2"
    assert history[-1]["content"] == "a3"
