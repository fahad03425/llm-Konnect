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


@pytest.mark.parametrize("question", [
    "What's my turnover this month?",
    "Show me sales by month",
    "How much did we spend on medicines last week?",
    "What was our net loss?",
    "Show profit and loss",
    "How much inventory did we move yesterday?",
    "What was cash flow in June?",
    "What were our takings?",
    "What were the sales yesterday?",
    "Give me the monthly revenue trend",
])
def test_expanded_analytics_vocabulary(question):
    assert classify_route(question) == RouteType.ANALYTICS


@pytest.mark.parametrize("question", [
    "Find invoice 43821",
    "Look up batch B-102",
    "Show me the sales records for invoice 43821",
    "What columns are in the selected dataset?",
    "Search for supplier Acme Pharma",
    "Pull up product Panadol details",
    "Which medicines contain amoxicillin?",
    "Which supplier did we purchase from?",
    "What products do I sell?",
    "Fetch transaction row 22",
    "List all sales records",
    "Show me invoices from yesterday",
])
def test_expanded_rag_lookup_vocabulary(question):
    assert classify_route(question) == RouteType.RAG


@pytest.mark.parametrize("question", [
    "Howdy",
    "Are you online?",
    "How's it going?",
    "Can you help me?",
    "What's up?",
    "Many thanks!",
    "Good afternoon",
    "What are your capabilities?",
    "Who are you?",
    "Assalam o alaikum",
])
def test_expanded_chitchat_vocabulary(question):
    assert classify_route(question) == RouteType.CHITCHAT


def test_analytics_intent_wins_when_greeting_precedes_a_metric_question():
    assert classify_route("Hi, can you show me total sales by month?") == RouteType.ANALYTICS


@pytest.mark.parametrize("question", [
    "Meri sale kitni hui?",
    "Kul bikri kitni thi?",
    "Pichlay mahine kitna munafa hua?",
    "Kitna kharcha hua?",
    "Meri aamdani kitni hai?",
    "Kitne units bikay?",
    "Kaunsi dawa sab se ziada biki?",
    "Rozana bikri ka trend kya hai?",
    "Pichlay 7 din mein meri sale kitni hui?",
    "Dawai ki sale kal kitni hui?",
])
def test_roman_urdu_analytics_vocabulary(question):
    assert classify_route(question) == RouteType.ANALYTICS


@pytest.mark.parametrize("question", [
    "Kis bill mein Panadol tha?",
    "Invoice 123 dikhao",
    "Mujhe Panadol ka bill dikhao",
    "Kis supplier se khareeda?",
    "Panadol ki qeemat batao",
    "Mujhe records dikhayen",
    "Batch B-12 ki tafseel batao",
    "Kaunsi dawa thi bill 12?",
    "Supplier ka naam batao",
    "Panadol ki maloomat dein",
])
def test_roman_urdu_rag_vocabulary(question):
    assert classify_route(question) == RouteType.RAG


@pytest.mark.parametrize("question", [
    "Kya haal hai?",
    "Aap kaise hain?",
    "Assalamualaikum",
    "Walaikum salam",
    "Madad kar saktay ho?",
    "Mein theek hun",
    "Shukria",
    "Ap kon hain?",
    "Tum kaise ho?",
    "Bohat shukriya",
])
def test_roman_urdu_chitchat_vocabulary(question):
    assert classify_route(question) == RouteType.CHITCHAT


def test_roman_urdu_relative_period_and_kal_filters():
    from datetime import date, timedelta

    assert extract_filters("Pichlay 7 din mein meri sale kitni hui?")["relative_days"] == 7
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert extract_filters("Dawai ki sale kal kitni hui?") == {
        "date_from": yesterday, "date_to": yesterday,
    }

def test_extract_filters():
    # Simplistic month extraction
    assert extract_filters("sales in january") == {"month": 1}
    assert extract_filters("profit in oct") == {"month": 10}
    assert extract_filters("what happened in may") == {"month": 5}
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert extract_filters("sales for yesterday") == {"date_from": yesterday, "date_to": yesterday}


def test_sales_in_last_n_days_routes_to_analytics_and_extracts_relative_window():
    question = "what are my sales in last 10 days"
    assert classify_route(question) == RouteType.ANALYTICS
    assert extract_filters(question)["relative_days"] == 10

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


def test_may_typo_before_sales_is_understood_as_my_for_relative_window():
    chat = RAGChat()
    question = chat._normalize_question("what are may sales in last 10 days")
    assert question == "what are my sales in last 10 days"
    assert "month" not in extract_filters(question)
    assert classify_route(question) == RouteType.ANALYTICS


def test_relative_window_anchors_to_latest_date_in_selected_dataset():
    import pandas as pd
    records = pd.DataFrame({"date": ["2026-09-20", "2026-09-25"]})

    filters = RAGChat._resolve_relative_date_filter({"relative_days": 10}, records)

    assert filters == {"date_from": "2026-09-16", "date_to": "2026-09-25"}


def test_yesterday_outside_dataset_coverage_is_reported_instead_of_all_time_totals():
    import pandas as pd
    from datetime import date, timedelta

    today = date.today()
    yesterday = today - timedelta(days=1)
    records = pd.DataFrame({
        "date": [today - timedelta(days=10), today - timedelta(days=3)],
        "txn_type": ["Sale", "Sale"], "amount": [10000000, 12000000],
        "quantity": [50, 60], "source_row": [2, 3],
    })
    filters = RAGChat._resolve_relative_date_filter({
        "date_from": yesterday.isoformat(), "date_to": yesterday.isoformat(),
    }, records)

    assert "_date_filter_error" in filters
    assert "date coverage" in filters["_date_filter_error"]


def test_relative_sales_answer_reports_the_exact_dataset_date_window():
    answer = RAGChat()._format_analytics_answer(
        "what are my sales in last 10 days",
        {"total_revenue": {
            "name": "Total Revenue", "value": 1250, "unit": "PKR", "status": "ok",
        }},
        "english",
        {"date_from": "2026-09-16", "date_to": "2026-09-25"},
    )

    assert "PKR 1,250.00" in answer
    assert "Date range used: Sep 16, 2026 to Sep 25, 2026." in answer


def test_yesterday_typo_routes_to_date_filtered_units_sold():
    import pandas as pd
    from datetime import date, timedelta

    chat = RAGChat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    records = pd.DataFrame([{
        "date": yesterday, "txn_type": "Sale", "amount": 22183.0,
        "quantity": 4, "cost": 100.0, "invoice_id": "Y-1", "product_id": "Paracetamol",
        "source_row": 2,
    }])
    question = chat._normalize_question("how much stock I sold yestarday")

    assert question == "how much stock I sold yesterday"
    assert classify_route(question) == RouteType.ANALYTICS
    assert extract_filters(question)["date_from"] == yesterday
    assert extract_filters(question)["date_to"] == yesterday
    computed, _ = AnalyticsRouter().compute(question, extract_filters(question), records, "pharmacy")
    assert computed["units_sold"]["value"] == 4
    assert computed["units_sold"]["unit"] == "units"


def test_twenty_analytics_questions_against_a_known_dataset():
    """Exercise RAGChat routing, date slicing, KPI selection, and response formatting."""
    import pandas as pd
    from datetime import date, timedelta

    today = pd.Timestamp.today().normalize()
    records = []
    for index, period in enumerate(
        pd.period_range(end=today.to_period("M") - 1, periods=8, freq="M")
    ):
        start = period.start_time.normalize()
        records.extend([
            {"date": start + pd.Timedelta(days=4), "txn_type": "Sale", "amount": 1000 + index * 100,
             "quantity": 10 + index, "cost": 20, "invoice_id": f"S-{index}-1",
             "product_id": "Paracetamol", "source_row": index * 3 + 2},
            {"date": start + pd.Timedelta(days=11), "txn_type": "Sale", "amount": 500 + index * 50,
             "quantity": 5, "cost": 30, "invoice_id": f"S-{index}-2",
             "product_id": "Vitamin C", "source_row": index * 3 + 3},
            {"date": start + pd.Timedelta(days=19), "txn_type": "Purchase", "amount": 400,
             "quantity": 20, "cost": 20, "invoice_id": f"P-{index}",
             "product_id": "Paracetamol", "supplier_id": "Supplier A", "source_row": index * 3 + 4},
        ])
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    records.append({
        "date": yesterday, "txn_type": "Sale", "amount": 77,
        "quantity": 3, "cost": 10, "invoice_id": "S-YESTERDAY",
        "product_id": "Paracetamol", "source_row": len(records) + 2,
    })
    frame = pd.DataFrame(records)
    expected_revenue = float(frame.loc[frame.txn_type.eq("Sale"), "amount"].sum())
    expected_purchase = float(frame.loc[frame.txn_type.eq("Purchase"), "amount"].sum())

    benchmark = [
        ("What were total sales?", "total_revenue"),
        ("What was the total sales revenue?", "total_revenue"),
        ("How much revenue did we earn?", "total_revenue"),
        ("What was the sales amount?", "total_revenue"),
        ("How many units were sold?", "units_sold"),
        ("How much stock I sold yestarday", "units_sold"),
        ("What quantity was sold?", "units_sold"),
        ("How many sales transactions?", "transaction_count"),
        ("What was the transaction count?", "transaction_count"),
        ("What was the average transaction value?", "average_transaction_value"),
        ("What were the total purchases?", "total_expenses"),
        ("How much did we spend on purchases?", "total_expenses"),
        ("What was net profit?", "net_profit"),
        ("What was gross profit?", "gross_profit"),
        ("What was gross margin?", "gross_margin_pct"),
        ("How much did we refund?", "total_refunds"),
        ("What was revenue by product?", "revenue_breakdown_by_product"),
        ("What was revenue by month?", "revenue_by_month"),
        ("What were my sales in last 10 days?", "total_revenue"),
        ("What is the sales forecast for next month?", "revenue_forecast"),
    ]

    chat = RAGChat()
    with patch.object(chat, "_get_records_for_analytics", return_value=(frame, [])), \
         patch("app.rag.chat.session_manager"), patch("app.rag.chat.llm") as mock_llm:
        responses = [
            chat.ask(ChatRequest(question=question, session_id=f"analytics-benchmark-{i}", domain="pharmacy"))
            for i, (question, _) in enumerate(benchmark)
        ]

    assert len(benchmark) == 20
    for response, (question, key) in zip(responses, benchmark):
        assert response.route == RouteType.ANALYTICS, question
        assert key in response.computed_values, question
        assert response.computed_values[key]["status"] == "ok", question
        assert response.answer.startswith("**"), question
    assert responses[0].computed_values["total_revenue"]["value"] == expected_revenue
    assert responses[10].computed_values["total_expenses"]["value"] == expected_purchase
    assert responses[5].computed_values["units_sold"]["value"] == 3.0
    assert "Date range used:" in responses[5].answer
    assert responses[18].computed_values["total_revenue"]["value"] == 77.0
    assert responses[18].answer.find("Date range used:") >= 0
    assert "22,183,468.13" not in responses[3].answer
    mock_llm.chat.assert_not_called()


def test_forecast_typo_is_normalized_before_route_selection():
    chat = RAGChat()
    question = chat._normalize_question("what is the forcast of my sale next month")

    assert question == "what is the forecast of my sale next month"
    assert classify_route(question) == RouteType.ANALYTICS


def test_analytics_citations_only_include_contributing_rows():
    chat = RAGChat()
    records = [
        {"source_row": 2, "source_file": "sales.xlsx", "product_id": "A"},
        {"source_row": 8, "source_file": "sales.xlsx", "product_id": "B"},
    ]

    chunks = chat._analytics_citations(records, [8], "")

    assert [chunk.source_row for chunk in chunks] == [8]
    assert chat._analytics_citations(records, [], "") == []


def test_analytics_forecast_answer_uses_requested_metric_and_exact_band():
    chat = RAGChat()
    values = {
        "revenue_forecast": {
            "name": "Revenue Forecast", "value": 730297.39, "unit": "PKR",
            "status": "ok", "is_estimate": True,
            "forecast": [{"period": "2026-10", "value": 730297.39, "lower": 474975.05, "upper": 985619.73}],
        },
        "demand_forecast": {
            "name": "Demand Forecast (units)", "value": 2634.33, "unit": "count",
            "status": "ok", "is_estimate": True,
            "forecast": [{"period": "2026-10", "value": 2634.33, "lower": 2223.39, "upper": 3045.28}],
        },
    }

    answer = chat._format_analytics_answer("give me forecast in terms of revenue", values, "english")

    assert "PKR 730,297.39" in answer
    assert "PKR 474,975.05 to PKR 985,619.73" in answer
    assert "2,634" not in answer
    assert "Next-month sales forecast — October 2026" in answer
    assert "- Estimated revenue:" in answer
    assert "Method:" not in answer


def test_analytics_forecast_answer_is_structured_when_revenue_and_demand_are_requested():
    chat = RAGChat()
    values = {
        "revenue_forecast": {
            "name": "Revenue Forecast", "value": 708765.58, "unit": "PKR",
            "status": "ok", "is_estimate": True,
            "forecast": [{"period": "2026-10", "value": 708765.58, "lower": 527007.61, "upper": 890523.55}],
        },
        "demand_forecast": {
            "name": "Demand Forecast (units)", "value": 2316, "unit": "count",
            "status": "ok", "is_estimate": True,
            "forecast": [{"period": "2026-10", "value": 2316, "lower": 2038, "upper": 2595}],
        },
    }

    answer = chat._format_analytics_answer("what is the forecast of my sale next month?", values, "english")

    assert "- Estimated revenue: PKR 708,765.58" in answer
    assert "- Estimated units: 2,316 units" in answer
    assert "Likely range: PKR 527,007.61 to PKR 890,523.55" in answer
    assert "Likely range: 2,038 units to 2,595 units" in answer


@patch("app.rag.chat.llm")
@patch("app.rag.chat.session_manager")
@patch("app.rag.chat.KnowledgeBase")
def test_analytics_chat_uses_exact_result_without_llm(mock_kb_cls, mock_session, mock_llm):
    mock_kb = MagicMock()
    mock_kb.search.return_value = [
        RetrievedChunk(text="sale row", metadata={"source_row": 2, "amount": 400, "quantity": 2}, score=1.0)
    ]
    mock_kb_cls.return_value = mock_kb

    response = RAGChat().ask(ChatRequest(question="total sales", session_id="analytics-fast"))

    assert response.computed_values["total_revenue"]["value"] == 400.0
    assert "PKR 400.00" in response.answer
    mock_llm.chat.assert_not_called()


@patch("app.rag.chat.llm")
@patch("app.rag.chat.session_manager")
@patch("app.rag.chat.KnowledgeBase")
def test_streaming_analytics_uses_exact_result_without_llm(mock_kb_cls, mock_session, mock_llm):
    mock_kb = MagicMock()
    mock_kb.search.return_value = [
        RetrievedChunk(text="sale row", metadata={"source_row": 2, "amount": 400, "quantity": 2}, score=1.0)
    ]
    mock_kb_cls.return_value = mock_kb

    events = list(RAGChat().ask_stream(ChatRequest(question="total sales", session_id="analytics-stream")))

    assert len(events) == 1
    assert "PKR 400.00" in events[0]
    mock_llm.chat_stream.assert_not_called()

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

def test_row_count_intent_and_analytics_route():
    q = "how many rows are in this data set"
    assert classify_route(q) == RouteType.ANALYTICS

    router = AnalyticsRouter()
    records = [{"source_row": i, "amount": 100, "quantity": 1} for i in range(1, 101)]
    comp, _ = router.compute(q, {}, records)
    assert comp["row_count"]["value"] == 100.0
    assert comp["row_count"]["status"] == "ok"
    assert comp["row_count"]["provenance"]["rows_used"] == 100

def test_confirmation_followup_route():
    # When last turn was ANALYTICS, "are you sure" stays ANALYTICS
    assert classify_route("are you sure", last_route=RouteType.ANALYTICS) == RouteType.ANALYTICS
    assert classify_route("is that correct?", last_route=RouteType.ANALYTICS) == RouteType.ANALYTICS
    # Standalone without prior analytics defaults to RAG
    assert classify_route("are you sure") == RouteType.RAG

def test_detect_query_language():
    chat = RAGChat()
    # English
    assert chat._detect_query_language("hi") == "english"
    assert chat._detect_query_language("hello there") == "english"
    assert chat._detect_query_language("what can you do") == "english"
    assert chat._detect_query_language("tell me about the doctors") == "english"
    assert chat._detect_query_language("show me sales summary") == "english"
    
    # Roman-Urdu
    assert chat._detect_query_language("me kis kism k data se deal kr rha hu") == "roman_urdu"
    assert chat._detect_query_language("sab se ziada sale kis branch ki hai") == "roman_urdu"
    assert chat._detect_query_language("tm kese ho") == "roman_urdu"
    assert chat._detect_query_language("kya hal hai") == "roman_urdu"
    assert chat._detect_query_language("konsi dawai expire hone wali hai") == "roman_urdu"
    
    # Urdu script
    assert chat._detect_query_language("سب سے زیادہ فروخت کس برانچ کی ہے") == "urdu_script"
    assert chat._detect_query_language("کس ڈیٹا سے ڈیل کر رہے ہیں") == "urdu_script"

def test_language_targeted_system_prompt():
    chat = RAGChat()
    # English prompt should demand English and disallow Namaste / Roman-Urdu
    en_prompt = chat._get_system_prompt("pharmacy", RouteType.CHITCHAT, lang="english")
    assert "The user wrote in English" in en_prompt
    assert "Namaste" in en_prompt
    assert "strictly in natural, professional English" in en_prompt

    # Roman-Urdu prompt should demand Latin letters, forbid Urdu script and forbid Hindi words
    ru_prompt = chat._get_system_prompt("pharmacy", RouteType.RAG, lang="roman_urdu")
    assert "The user wrote in Roman-Urdu" in ru_prompt
    assert "Latin letters (A-Z, a-z) only" in ru_prompt
    assert "DO NOT use Urdu/Arabic script" in ru_prompt
    assert "STRICT PROHIBITION ON HINDI VOCABULARY" in ru_prompt
    assert "jaankari" in ru_prompt
    assert "maloomat" in ru_prompt

def test_clean_roman_urdu_vocabulary():
    chat = RAGChat()
    hindi_reply = "Haan, main aapki ismein adhik jankari pradaan kar sakta hoon. Data sources mein uplabdh hai aur anya jaankari bhi."
    cleaned = chat._clean_roman_urdu_vocabulary(hindi_reply)
    assert "adhik" not in cleaned
    assert "jankari" not in cleaned
    assert "jaankari" not in cleaned
    assert "uplabdh" not in cleaned
    assert "anya" not in cleaned
    assert "maloomat" in cleaned
    assert "ziada" in cleaned
    assert "dastyab" in cleaned


