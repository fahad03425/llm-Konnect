import json
from unittest.mock import Mock

import pandas as pd
import pytest

from app.rag.router import classify_route, RouteType
from app.rag.intent import analytics_request_guard
from app.analytics.seam import AnalyticsRouter, select_kpi_keys


@pytest.mark.parametrize("question", [
    "How does inventory turnover affect business performance?",
    "How do discounts and taxes influence final prices?",
    "Explain how payment methods work",
    "How do I void a posted invoice in Asan Pos?",
    "The receipt printer stopped working after a sale. What should I check in Asan Pos?",
    "What are the steps to reprint a receipt?",
    "What does gross margin mean?",
    "What sales columns exist in the database?",
])
def test_explanations_use_rag(question):
    assert classify_route(question) == RouteType.RAG


@pytest.mark.parametrize("question", [
    "What are the sales trends for specific products?",
    "Can you analyze customer purchase patterns and identify high-value items?",
    "What is the impact of different payment methods on transaction amounts?",
    "Compare average transaction amounts by payment method last month",
    "Calculate my inventory turnover this month",
    "How much tax did we collect yesterday?",
    "Explain my actual sales trend this month",
    "What is the combined on-hand quantity for product 76 across the selected inventory tables?",
    "What is the summed on-hand quantity in the current inventory snapshot table tbl_10?",
    "Was any sales tax recorded on invoice REC-00072?",
])
def test_measured_questions_use_analytics(question):
    assert classify_route(question) == RouteType.ANALYTICS


@pytest.mark.parametrize("question", [
    "What are the sales trends for specific products?",
    "Can you analyze customer purchase patterns and identify high-value items?",
    "What is the impact of different payment methods on transaction amounts?",
])
def test_ambiguous_analyses_do_not_produce_generic_totals(question):
    result = analytics_request_guard(question)
    assert result["values"]["status"] == "needs_clarification"
    assert result["source_rows"] == []
    payload, rows = AnalyticsRouter().compute(question, {}, pd.DataFrame({"amount": [999]}))
    assert payload["query"]["value"] is None
    assert rows == []


def test_unqualified_performance_request_asks_for_measure():
    result = analytics_request_guard("Can you show me the latest performance?")
    assert result["values"]["status"] == "needs_clarification"
    assert "sales/revenue" in result["answer"]
    assert result["source_rows"] == []
    assert analytics_request_guard("Show me latest sales performance") is None


def test_unknown_calculation_never_defaults_to_revenue():
    assert select_kpi_keys("customer loyalty coefficient") == []
    payload, rows = AnalyticsRouter().compute("customer loyalty coefficient", {}, pd.DataFrame({"amount": [999]}))
    assert payload["query"]["status"] == "unavailable"
    assert payload["query"]["value"] is None
    assert rows == []


def test_normal_comparison_does_not_trigger_guard():
    assert analytics_request_guard("Compare average transaction amounts by payment method last month") is None


@pytest.mark.parametrize("question", [
    "What are the sales trends for specific products?",
    "Can you analyze customer purchase patterns and identify high-value items?",
    "What is the impact of different payment methods on transaction amounts?",
])
def test_regular_and_streaming_clarify_without_loading_data(question, monkeypatch):
    from app.rag.chat import RAGChat
    from app.rag.models import ChatRequest
    history = Mock()
    history.get_last_assistant_turn.return_value = None
    monkeypatch.setattr("app.rag.chat.session_manager", history)
    chat = RAGChat()
    monkeypatch.setattr(chat, "_rewrite_follow_up", lambda q, session_id: q)
    load = Mock(side_effect=AssertionError("Clarification must not load the database"))
    monkeypatch.setattr(chat, "_get_records_for_analytics", load)
    monkeypatch.setattr(chat, "_exact_identifier_answer", lambda *args: None)
    request = ChatRequest(question=question, session_id="routing-guard-test", domain="pharmacy", file_ids=["test"])
    regular = chat.ask(request)
    chunks = [json.loads(chunk) for chunk in chat.ask_stream(request)]
    assert regular.route == "analytics"
    assert regular.answer == "".join(chunk.get("chunk", "") for chunk in chunks)
    assert regular.computed_values["tabular_result"]["status"] == "needs_clarification"
    assert regular.sources == []
    load.assert_not_called()


def test_named_product_trend_preserves_product_filter():
    from app.analytics.seam import compute_for_question
    from app.analytics.models import KPIResult, Provenance
    engine = Mock()
    engine.has.return_value = True
    engine.compute.side_effect = lambda key, frame, filters, domain: KPIResult(key=key, name=key, value=1, unit="percent", formula="", provenance=Provenance())
    compute_for_question("sales trend for Panadol", pd.DataFrame({"product_id": ["Panadol", "Brufen"]}), kpi_engine=engine)
    assert engine.compute.call_count == 2
    assert all(call.args[2].product_id == "Panadol" for call in engine.compute.call_args_list)


def test_trend_answer_exposes_comparison_periods():
    from app.rag.chat import RAGChat
    chat = RAGChat()
    values = {"revenue_trend": {"name": "Revenue Trend", "value": -20, "unit": "percent", "status": "ok",
              "series": [{"period": "2026-01", "value": 100}, {"period": "2026-02", "value": 80}],
              "period": {"start": "2026-01-01", "end": "2026-02-28"},
              "provenance": {"filter": "product_id=Panadol"}}}
    answer = chat._format_analytics_answer("sales trend", values, "english", {})
    assert "2026-01 to 2026-02" in answer
    assert "2026-01-01 to 2026-02-28" in answer
    assert "Panadol" in answer
