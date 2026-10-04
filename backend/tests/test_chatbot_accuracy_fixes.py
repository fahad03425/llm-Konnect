import numpy as np
import pandas as pd
from unittest.mock import patch

from app.analytics.tabular_query import answer_cross_dataset_question
from app.ingestion.models import RetrievedChunk
from app.ingestion.store import KnowledgeBase
from app.rag.chat import RAGChat
from app.rag.router import RouteType, classify_route


def test_common_metric_typos_normalize_before_routing():
    chat = RAGChat()
    cases = {
        "What are toatl slaes?": "total sales",
        "What is gros proft?": "gross profit",
        "Show totla reveneu": "total revenue",
        "Which items have low stcok?": "which items have low stock?",
    }
    for question, expected in cases.items():
        normalized = chat._normalize_question(question).casefold()
        assert expected in normalized
        assert classify_route(normalized) == RouteType.ANALYTICS


def test_typos_do_not_rewrite_record_ids_or_medicine_strengths():
    normalized = RAGChat()._normalize_question("Show INV-123 for Panadol 500mg")
    assert "INV-123" in normalized
    assert "Panadol 500mg" in normalized


def test_typo_normalization_preserves_recorded_in_analytics_questions():
    question = "Which calendar month has the highest recorded sales revenue?"
    normalized = RAGChat()._normalize_question(question)
    assert "recorded" in normalized.casefold()
    assert "reorder" not in normalized.casefold()
    assert classify_route(normalized) == RouteType.ANALYTICS


def test_cross_dataset_sales_question_does_not_add_purchase_totals():
    frame = pd.DataFrame([
        {"source_file": "sales", "transaction_id": "S-1", "unit_price": 50, "amount": 50, "date": "2026-10-01", "source_row": 1},
        {"source_file": "purchases", "purchase_order_no": "P-1", "supplier_name": "A", "cost": 20, "invoice_total": 20, "date": "2026-10-01", "source_row": 1},
    ])
    sales = answer_cross_dataset_question("What are total sales?", frame)
    purchases = answer_cross_dataset_question("What are total purchases?", frame)
    comparison = answer_cross_dataset_question("Compare total sales and purchases", frame)
    assert sales is None
    assert purchases is None
    assert comparison is not None
    assert comparison["values"]["total_sales"] == 50
    assert comparison["values"]["total_purchase_value"] == 20


def test_explicit_two_period_comparison_calculates_each_period():
    chat = RAGChat()
    records = pd.DataFrame([
        {"date": "2026-09-03", "txn_type": "Purchase", "amount": 5000, "quantity": 1, "transaction_id": "P1", "source_row": 3},
        {"date": "2026-10-01", "txn_type": "Sale", "amount": 100, "quantity": 1, "transaction_id": "S1", "source_row": 1},
        {"date": "2026-09-15", "txn_type": "Sale", "amount": 250, "quantity": 1, "transaction_id": "S2", "source_row": 2},
    ])
    result = chat._relative_period_comparison(
        "Compare total sales for last 7 days and last 28 days", records, "pharmacy", "english"
    )
    assert result["values"]["period_comparison"]["periods"]["7"]["value"] == 100
    assert result["values"]["period_comparison"]["periods"]["28"]["value"] == 350


def test_storage_instructions_are_not_treated_as_storage_location():
    chunk = RetrievedChunk(
        text="Storage instructions: keep below 25 degrees Celsius.",
        metadata={"product_id": "Norevia"}, score=0.9,
    )
    answer, sources = RAGChat._direct_record_answer(
        "Explain the storage instructions for Norevia.", [chunk]
    )
    assert answer is None
    assert sources == []


def test_capability_and_data_question_suggestions_use_grounded_rag():
    import json
    from app.rag.models import ChatRequest

    chat = RAGChat()
    questions = (
        "What type of questions can you answer?",
        "What type of questions can you answer about sales?",
        "What questions can you answer?",
        "What can you help with?",
        "Give me some questions that I can ask related to this data?",
    )
    chunk = RetrievedChunk(
        text="Table sales: transaction_date, product_name, quantity, sale_amount, cost.",
        metadata={"file_id": "sales", "table_name": "sales", "source_row": 1}, score=0.1,
    )
    with patch("app.rag.chat.session_manager") as sessions, patch("app.rag.chat.llm") as local_llm:
        sessions.get_history.return_value = []
        sessions.get_last_assistant_turn.return_value = None
        local_llm.resolve_chat_model.return_value = "qwen2.5:1.5b"
        local_llm.chat_stream.side_effect = lambda **_kwargs: iter([
            "1. Sales by date?\n2. Which products sold most?\n3. What is gross profit?"
        ])
        with patch.object(chat.kb, "search", return_value=[chunk]) as search, \
             patch.object(chat, "_format_sources", return_value=[]), \
             patch.object(chat, "_direct_record_answer", return_value=(None, [])), \
             patch.object(chat, "_format_context_records", return_value="Context Records: sales fields: transaction_date, product_name, quantity, sale_amount, cost"):
            for index, question in enumerate(questions):
                assert classify_route(chat._normalize_question(question)) == RouteType.RAG
                events = [json.loads(line) for line in chat.ask_stream(
                    ChatRequest(question=question, session_id=f"capability-{index}", domain="pharmacy", file_ids=["sales"])
                )]
                assert events[0]["route"] == RouteType.RAG
            assert search.call_count == len(questions)
        assert local_llm.chat_stream.call_count == len(questions)
        for call in local_llm.chat_stream.call_args_list:
            assert call.kwargs["options"] == {"num_predict": 128}
            assert "retrieved context/schema" in call.kwargs["messages"][-1]["content"]
        assert sessions.append_turn.call_count == len(questions) * 2


class _FakeCollection:
    def __init__(self, *, raise_on_scoped_query=False):
        self.raise_on_scoped_query = raise_on_scoped_query
        self.last_where = None

    def count(self):
        return 1

    def query(self, **kwargs):
        self.last_where = kwargs.get("where")
        if self.raise_on_scoped_query and self.last_where:
            raise ValueError("simulated filter failure")
        return {
            "documents": [["other domain row"]],
            "metadatas": [[{"domain": "ecommerce", "file_id": "other", "source_row": 1}]],
            "distances": [[0.1]],
        }


class _FakeEmbedder:
    def encode(self, *_args, **_kwargs):
        return np.asarray([[1.0, 0.0]])


def test_scoped_retrieval_fails_closed_and_checks_domain():
    kb = KnowledgeBase()
    failing = _FakeCollection(raise_on_scoped_query=True)
    kb._get_chroma = lambda: failing
    kb._get_embedder = lambda: _FakeEmbedder()
    assert kb.search("find this", domain="pharmacy", file_ids=["selected"]) == []
    assert failing.last_where is not None

    wrong_domain = _FakeCollection()
    kb._get_chroma = lambda: wrong_domain
    results = kb.search("find this", domain="pharmacy")
    assert wrong_domain.last_where == {"domain": "pharmacy"}
    assert results == []
