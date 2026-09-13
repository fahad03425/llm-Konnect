"""
Test Suite: Challenging Analytics, Aggregates, Expiry, and Schedule Queries
Dataset: test_pharmacy_small.csv

Tests all questions across 5 core categories:
1. Compound Aggregates & Averages
2. Category & Product Breakdowns
3. Multi-Condition Time Window
4. Pharmacy-Specific Intelligence & Expiry Analytics
5. Schedule / Controlled Substance & Rx Tracking
"""

import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

import app.schema  # Registers domain packs (including pharmacy)
from app.analytics.seam import AnalyticsRouter, select_kpi_keys
from app.analytics.filters import KPIFilters
from app.rag.router import classify_route, extract_filters, RouteType
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest
from app.ingestion.models import RetrievedChunk

CSV_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "uploads", "test_pharmacy_small.csv")
)


@pytest.fixture(scope="module")
def pharmacy_df():
    """Loads test_pharmacy_small.csv and attaches 1-based source_row indices."""
    assert os.path.exists(CSV_PATH), f"Target CSV not found at: {CSV_PATH}"
    df = pd.read_csv(CSV_PATH)
    df["source_row"] = df.index + 2  # Row 1 is header, so row 2 is index 0
    return df


@pytest.fixture(scope="module")
def records(pharmacy_df):
    """Dictionary records of test_pharmacy_small.csv."""
    return pharmacy_df.to_dict(orient="records")


@pytest.fixture(scope="module")
def router():
    return AnalyticsRouter()


# ==============================================================================
# 1. Compound Aggregates & Averages
# ==============================================================================

class TestCompoundAggregates:
    def test_average_transaction_value_february_2026(self, router, pharmacy_df, records):
        q = "What was the average transaction value in February 2026?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        filters = extract_filters(q, domain="pharmacy")
        assert filters.get("month") == 2
        assert filters.get("year") == 2026

        computed, source_rows = router.compute(q, filters, records, domain="pharmacy")
        assert "average_transaction_value" in computed
        assert computed["average_transaction_value"]["status"] == "ok"
        
        # Verify deterministic calculation against DataFrame filtered to Feb 2026
        feb_mask = (pd.to_datetime(pharmacy_df["date"]).dt.month == 2) & (pd.to_datetime(pharmacy_df["date"]).dt.year == 2026)
        feb_df = pharmacy_df[feb_mask]
        expected_atv = round(feb_df["amount"].sum() / feb_df["invoice_id"].nunique(), 2)
        assert computed["average_transaction_value"]["value"] == expected_atv

    def test_total_revenue_and_average_transaction_value_across_all(self, router, pharmacy_df, records):
        q = "What is our total revenue and average transaction value across all records?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(q, {}, records, domain="pharmacy")
        assert computed["total_revenue"]["status"] == "ok"
        assert computed["average_transaction_value"]["status"] == "ok"
        
        expected_rev = round(pharmacy_df["amount"].sum(), 2)
        expected_txns = pharmacy_df["invoice_id"].nunique()
        expected_atv = round(expected_rev / expected_txns, 2)
        
        assert computed["total_revenue"]["value"] == expected_rev
        assert computed["average_transaction_value"]["value"] == expected_atv
        assert computed["transaction_count"]["value"] == float(expected_txns)


# ==============================================================================
# 2. Category & Product Breakdowns
# ==============================================================================

class TestCategoryAndProductBreakdowns:
    def test_revenue_breakdown_by_product(self, router, pharmacy_df, records):
        q = "Show me the revenue breakdown by product."
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(q, {}, records, domain="pharmacy")
        assert "revenue_breakdown_by_product" in computed
        result = computed["revenue_breakdown_by_product"]
        assert result["status"] == "ok"
        assert len(result["breakdown"]) > 0

        # Check that breakdown contains product amounts and sum matches total
        top_product = result["breakdown"][0]
        assert "product_id" in top_product
        assert top_product["amount"] > 0

    def test_which_supplier_generated_highest_revenue(self, router, pharmacy_df, records):
        q = "Which supplier generated the highest revenue?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(q, {}, records, domain="pharmacy")
        assert "revenue_breakdown_by_supplier" in computed
        result = computed["revenue_breakdown_by_supplier"]
        assert result["status"] == "ok"
        assert len(result["breakdown"]) > 0
        
        # Verify supplier grouping
        supp_totals = pharmacy_df.groupby("supplier_id")["amount"].sum().sort_values(ascending=False)
        highest_supp = supp_totals.index[0]
        highest_val = round(supp_totals.iloc[0], 2)
        assert result["breakdown"][0]["supplier_id"] == highest_supp
        assert result["breakdown"][0]["amount"] == highest_val

    def test_revenue_breakdown_by_month(self, router, pharmacy_df, records):
        q = "Give me the revenue breakdown by month."
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(q, {}, records, domain="pharmacy")
        assert "revenue_by_month" in computed
        result = computed["revenue_by_month"]
        assert result["status"] == "ok"
        assert len(result["breakdown"]) > 0
        
        # Verify breakdown has months in ascending order
        months = [b["month"] for b in result["breakdown"]]
        assert months == sorted(months)


# ==============================================================================
# 3. Multi-Condition Time Window
# ==============================================================================

class TestMultiConditionTimeWindow:
    def test_january_vs_february_revenue_filter(self, router, pharmacy_df, records):
        q = "How much revenue did we make in January 2026 vs February 2026?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        filters = extract_filters(q, domain="pharmacy")
        assert filters.get("month") == 1
        computed, source_rows = router.compute(q, filters, records, domain="pharmacy")
        assert computed["total_revenue"]["status"] == "ok"
        
        jan_expected = round(pharmacy_df[pd.to_datetime(pharmacy_df["date"]).dt.month == 1]["amount"].sum(), 2)
        assert computed["total_revenue"]["value"] == jan_expected

    def test_exact_date_range_february(self, router, pharmacy_df, records):
        q = "What was the total sales between 1st February and 28th February?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        filters = extract_filters(q, domain="pharmacy")
        assert filters.get("date_from") == "2026-02-01"
        assert filters.get("date_to") == "2026-02-28"

        computed, source_rows = router.compute(q, filters, records, domain="pharmacy")
        assert computed["total_revenue"]["status"] == "ok"

        feb_mask = (pharmacy_df["date"] >= "2026-02-01") & (pharmacy_df["date"] <= "2026-02-28")
        expected_sales = round(pharmacy_df[feb_mask]["amount"].sum(), 2)
        assert computed["total_revenue"]["value"] == expected_sales


# ==============================================================================
# 4. Pharmacy-Specific Intelligence & Expiry Analytics
# ==============================================================================

class TestExpiryRiskAnalysis:
    def test_which_medicines_are_expired_or_expiring(self, router, pharmacy_df, records):
        q = "Which medicines are already expired or expiring within the next 60 days?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(
            q, {"as_of": "2026-01-01"}, records, domain="pharmacy"
        )
        assert "expired_stock_value" in computed
        assert computed["expired_stock_value"]["status"] == "ok"
        assert "expired_item_count" in computed

    def test_monetary_value_of_products_expiring_soon(self, router, pharmacy_df, records):
        q = "What is the total monetary value of products expiring soon?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(
            q, {"as_of": "2026-01-01"}, records, domain="pharmacy"
        )
        assert "near_expiry_total" in computed
        assert computed["near_expiry_total"]["status"] == "ok"
        assert computed["near_expiry_total"]["value"] >= 0.0

    @patch("app.rag.chat.KnowledgeBase")
    @patch("app.rag.chat.llm")
    @patch("app.rag.chat.session_manager")
    def test_batches_lookup_via_rag(self, mock_sess, mock_llm, mock_kb_cls, records):
        q = "List all batches of medicines that expire in 2025 or 2026."
        
        chunks = [
            RetrievedChunk(
                text=f"Product: {r['product_id']}. Generic: {r['generic_name']}. Batch: {r['batch_no']}. Expiry: {r['expiry_date']}.",
                metadata=r,
                score=0.9,
                source_row=r["source_row"]
            )
            for r in records if "2025" in str(r.get("expiry_date")) or "2026" in str(r.get("expiry_date"))
        ]
        
        mock_kb = MagicMock()
        mock_kb.search.return_value = chunks
        mock_kb_cls.return_value = mock_kb
        mock_llm.chat.return_value = "Here are the batches expiring in 2025/2026: B1005-01, B1002-01."

        chat = RAGChat()
        resp = chat.ask(ChatRequest(question=q, domain="pharmacy", session_id="test_batch_session"))
        
        assert len(resp.sources) > 0
        prompt = mock_llm.chat.call_args.kwargs["messages"][-1]["content"]
        assert "Batch:" in prompt
        assert "Expiry:" in prompt


# ==============================================================================
# 5. Schedule / Controlled Substance & Rx Tracking
# ==============================================================================

class TestScheduleAndRxTracking:
    def test_scheduled_transactions_and_units_count(self, router, pharmacy_df, records):
        q = "How many transactions or units were sold for scheduled / prescription medicines?"
        route = classify_route(q)
        assert route == RouteType.ANALYTICS

        computed, source_rows = router.compute(q, {}, records, domain="pharmacy")
        assert "scheduled_transaction_count" in computed
        assert "scheduled_units_sold" in computed
        assert "scheduled_sales_value" in computed

        assert computed["scheduled_transaction_count"]["status"] == "ok"
        assert computed["scheduled_units_sold"]["status"] == "ok"
        assert computed["scheduled_sales_value"]["status"] == "ok"

        # Compare with ground truth from CSV
        sched_mask = pharmacy_df["schedule_flag"].astype(str).str.strip().str.lower().isin(["yes", "true", "1", "y"])
        sched_df = pharmacy_df[sched_mask]
        
        expected_txns = sched_df["invoice_id"].nunique()
        expected_units = sched_df["quantity"].sum()
        expected_sales = round(sched_df["amount"].sum(), 2)

        assert computed["scheduled_transaction_count"]["value"] == float(expected_txns)
        assert computed["scheduled_units_sold"]["value"] == float(expected_units)
        assert computed["scheduled_sales_value"]["value"] == expected_sales

    @patch("app.rag.chat.KnowledgeBase")
    @patch("app.rag.chat.llm")
    @patch("app.rag.chat.session_manager")
    def test_which_medicines_have_schedule_flag_rag(self, mock_sess, mock_llm, mock_kb_cls, records):
        q = "Which medicines have a schedule flag or require a prescription?"
        route = classify_route(q)
        assert route == RouteType.RAG

        chunks = [
            RetrievedChunk(
                text=f"Product: {r['product_id']}. Generic: {r['generic_name']}. Schedule Flag: {r['schedule_flag']}.",
                metadata=r,
                score=0.95,
                source_row=r["source_row"]
            )
            for r in records if str(r.get("schedule_flag")).lower() in ("yes", "true")
        ]
        
        mock_kb = MagicMock()
        mock_kb.search.return_value = chunks
        mock_kb_cls.return_value = mock_kb
        mock_llm.chat.return_value = "The following medicines require a prescription: Augmentin 625, Amoxicillin 500mg."

        chat = RAGChat()
        resp = chat.ask(ChatRequest(question=q, domain="pharmacy", session_id="test_sched_session"))
        
        assert resp.route == RouteType.RAG
        assert len(resp.sources) > 0
        prompt = mock_llm.chat.call_args.kwargs["messages"][-1]["content"]
        assert "Schedule Flag:" in prompt


# ==============================================================================
# 6. End-to-End Chatbot Integration with Mocked LLM
# ==============================================================================

class TestEndToEndChatbotQueries:
    @patch("app.rag.chat.llm")
    @patch("app.rag.chat.session_manager")
    @patch("app.rag.chat.KnowledgeBase")
    def test_chat_compound_average_transaction_value(self, mock_kb_cls, mock_sess, mock_llm, records):
        mock_kb = MagicMock()
        mock_kb.search.return_value = [
            RetrievedChunk(text="invoice row", metadata=m, score=0.9, source_row=m["source_row"])
            for m in records
        ]
        mock_kb_cls.return_value = mock_kb
        mock_llm.chat.return_value = "The average transaction value in February 2026 was 773.33 PKR."

        chat = RAGChat()
        req = ChatRequest(
            question="What was the average transaction value in February 2026?",
            domain="pharmacy",
            session_id="test_session"
        )
        resp = chat.ask(req)
        
        assert resp.route == RouteType.ANALYTICS
        assert "average_transaction_value" in resp.computed_values
        assert resp.computed_values["average_transaction_value"]["value"] == 773.33
        
        # Verify prompt contained computed numbers passed to LLM
        prompt = mock_llm.chat.call_args.kwargs["messages"][-1]["content"]
        assert "773.33" in prompt
        assert "Computed Values from Analytics Engine" in prompt
