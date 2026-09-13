"""
Test Suite: Pharmacy Sales Dataset Questions
Dataset: Pharmacy_Sales_Dataset.xlsx

Tests all 5 question categories:
1. Specific Record & Invoice Lookups (RAG Lookup)
2. Sales, Revenue & Analytics (KPI Engine)
3. Product & Medicine Insights
4. Branch, Cashier & Customer Breakdown
5. Urdu & Roman-Urdu Queries (Bilingual Support)
"""

import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

import app.schema
from app.analytics.seam import AnalyticsRouter
from app.analytics.filters import KPIFilters
from app.rag.router import classify_route, extract_filters, RouteType
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest
from app.ingestion.models import RetrievedChunk
from app.schema.mapper import map_headers
from app.schema.domain import get_domain_pack
from app.schema.normalize import apply_mapping

EXCEL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "uploads", "Pharmacy_Sales_Dataset.xlsx")
)


@pytest.fixture(scope="module")
def excel_df():
    """Loads Pharmacy_Sales_Dataset.xlsx (first 500 rows for fast and deterministic test execution)."""
    assert os.path.exists(EXCEL_PATH), f"Target Excel dataset not found at: {EXCEL_PATH}"
    df = pd.read_excel(EXCEL_PATH, nrows=500)
    df["source_row"] = df.index + 2
    return df


@pytest.fixture(scope="module")
def canonical_records(excel_df):
    """Maps Excel columns to canonical pharmacy schema and returns records."""
    pack = get_domain_pack("pharmacy")
    mapping = map_headers(list(excel_df.columns), pack)
    mapping["Bill_Date"] = "date"
    mapping["Branch_Name"] = "branch"
    mapping["Product_Name"] = "product_id"
    mapping["Customer_Name"] = "customer_id"
    mapping["Unit_Price"] = "unit_price"
    mapping["Amount"] = "amount"
    mapping["Quantity"] = "quantity"

    c_df = apply_mapping(excel_df, mapping, domain="pharmacy", keep_extras=True)
    c_df["source_row"] = excel_df["source_row"]
    return c_df.to_dict(orient="records")


@pytest.fixture(scope="module")
def router():
    return AnalyticsRouter()


# ==============================================================================
# Category 1: Specific Record & Invoice Lookups (RAG Lookup)
# ==============================================================================

class TestSpecificRecordLookups:
    def test_medicines_sold_in_bill_400001(self, canonical_records):
        q = "What medicines were sold in Bill No 400001?"
        assert classify_route(q) == RouteType.RAG
        
        matches = [r for r in canonical_records if str(r.get("invoice_id")) == "400001"]
        assert len(matches) > 0
        assert any("Eziday" in str(r.get("product_id", "")) for r in matches)

    def test_invoice_400005_details(self, router, canonical_records):
        q = "Find the details of invoice 400005, including total amount and discount."
        # Because "total amount" is present, the query resolves to analytics / KPI computation
        assert classify_route(q) == RouteType.ANALYTICS
        
        matches = [r for r in canonical_records if str(r.get("invoice_id")) == "400005"]
        assert len(matches) > 0
        assert any("Tegral" in str(r.get("product_id", "")) for r in matches)

    def test_eziday_cream_price_and_quantity_in_bill_400001(self, canonical_records):
        q = "What was the price and quantity of Eziday 50mg Cream in bill 400001?"
        assert classify_route(q) == RouteType.RAG
        
        matches = [
            r for r in canonical_records 
            if str(r.get("invoice_id")) == "400001" and "Eziday" in str(r.get("product_id", ""))
        ]
        assert len(matches) > 0
        assert matches[0]["quantity"] == 2
        assert float(matches[0]["unit_price"]) > 0

    def test_umar_mirza_purchases(self, canonical_records):
        q = "Did Umar Mirza make any purchases, and what did he buy?"
        assert classify_route(q) == RouteType.RAG
        
        matches = [r for r in canonical_records if "Umar Mirza" in str(r.get("customer_id", ""))]
        assert len(matches) > 0

    def test_zinnat_tablets_on_date(self, canonical_records):
        q = "Show me the transaction recorded for Zinnat 10 Tablets on 2025-07-19."
        assert classify_route(q) == RouteType.RAG
        
        matches = [
            r for r in canonical_records 
            if "Zinnat" in str(r.get("product_id", "")) and "2025-07-19" in str(r.get("date", ""))
        ]
        assert len(matches) > 0
        assert str(matches[0]["invoice_id"]) == "400002"


# ==============================================================================
# Category 2: Sales, Revenue & Analytics (KPI Engine)
# ==============================================================================

class TestSalesRevenueAnalytics:
    def test_total_sales_revenue(self, router, canonical_records):
        q = "What is the total sales revenue in this dataset?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        computed, sources = router.compute(q, {}, canonical_records, domain="pharmacy")
        assert "total_revenue" in computed
        assert computed["total_revenue"]["status"] == "ok"
        assert computed["total_revenue"]["value"] > 0
        assert len(sources) > 0

    def test_total_revenue_march_2024(self, router, canonical_records):
        q = "What was the total revenue in March 2024?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        filters = extract_filters(q, domain="pharmacy")
        computed, sources = router.compute(q, filters, canonical_records, domain="pharmacy")
        assert "total_revenue" in computed
        assert computed["total_revenue"]["status"] == "ok"

    def test_average_transaction_value(self, router, canonical_records):
        q = "What is the average transaction value?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        computed, _ = router.compute(q, {}, canonical_records, domain="pharmacy")
        assert "average_transaction_value" in computed
        assert computed["average_transaction_value"]["status"] == "ok"
        assert computed["average_transaction_value"]["value"] > 0

    def test_total_transactions_completed(self, router, canonical_records):
        q = "How many total transactions were completed across all branches?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        computed, _ = router.compute(q, {}, canonical_records, domain="pharmacy")
        assert "transaction_count" in computed
        assert computed["transaction_count"]["status"] == "ok"
        assert computed["transaction_count"]["value"] > 0

    def test_total_sales_june_2025(self, router, canonical_records):
        q = "What was the total sales amount in June 2025?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        filters = extract_filters(q, domain="pharmacy")
        computed, sources = router.compute(q, filters, canonical_records, domain="pharmacy")
        assert "total_revenue" in computed


# ==============================================================================
# Category 3: Product & Medicine Insights
# ==============================================================================

class TestProductMedicineInsights:
    def test_bills_including_rigix(self, canonical_records):
        q = "Which bills include Rigix 20 Tablets?"
        assert classify_route(q) == RouteType.RAG
        
        rigix_rows = [r for r in canonical_records if "Rigix" in str(r.get("product_id", ""))]
        assert len(rigix_rows) > 0
        assert any(str(r.get("invoice_id")) == "400004" for r in rigix_rows)

    def test_bonus_quantity_given(self, canonical_records):
        q = "Were there any bonus quantities given for Rigix or Tegral Cream?"
        assert classify_route(q) == RouteType.RAG
        
        bonus_rows = [
            r for r in canonical_records 
            if ("Rigix" in str(r.get("product_id", "")) or "Tegral" in str(r.get("product_id", "")))
            and float(r.get("_extra.Bonus_Quantity", 0) or 0) > 0
        ]
        assert len(bonus_rows) > 0

    def test_unit_price_cardivas(self, canonical_records):
        q = "What is the unit price of Cardivas 10mg Tablet?"
        assert classify_route(q) == RouteType.RAG
        
        matches = [r for r in canonical_records if "Cardivas" in str(r.get("product_id", ""))]
        assert len(matches) > 0
        assert float(matches[0]["unit_price"]) > 0

    def test_products_with_discount(self, canonical_records):
        q = "Which products received a 20% discount?"
        assert classify_route(q) == RouteType.RAG
        
        discount_rows = [
            r for r in canonical_records 
            if float(r.get("_extra.Discount_Percentage", 0) or 0) == 20
        ]
        assert len(discount_rows) > 0
        assert any("Tegral" in str(r.get("product_id", "")) for r in discount_rows)

    def test_godown_locations(self, canonical_records):
        q = "List the godown locations used for storing products like Cardivas and Rigix."
        assert classify_route(q) == RouteType.RAG
        
        godowns = {
            r.get("_extra.Godown_Location") for r in canonical_records 
            if pd.notna(r.get("_extra.Godown_Location")) and r.get("_extra.Godown_Location")
        }
        assert len(godowns) > 0
        assert "GODOWN2" in godowns or "MAIN STORE" in godowns


# ==============================================================================
# Category 4: Branch, Cashier & Customer Breakdown
# ==============================================================================

class TestBranchCashierBreakdown:
    def test_branches_recorded(self, canonical_records):
        q = "Which branches are recorded in this dataset?"
        assert classify_route(q) == RouteType.RAG
        
        branches = {
            r.get("branch") for r in canonical_records 
            if pd.notna(r.get("branch")) and r.get("branch")
        }
        assert len(branches) > 0
        assert any("Al Noor" in b or "Bin-Hayyan" in b or "Waseela" in b for b in branches)

    def test_al_noor_pharmacy_sales(self, router, canonical_records):
        q = "What sales were made at 'Al Noor Pharmacy - F8 Islamabad'?"
        # Queries with "sales" resolve to analytics route
        assert classify_route(q) == RouteType.ANALYTICS
        
        al_noor_rows = [r for r in canonical_records if "Al Noor" in str(r.get("branch", ""))]
        assert len(al_noor_rows) > 0
        
        computed, sources = router.compute(q, {}, canonical_records, domain="pharmacy")
        assert "total_revenue" in computed

    def test_cashier_transactions(self, canonical_records):
        q = "What transactions were handled by cashier Tahir or Zubair?"
        assert classify_route(q) == RouteType.RAG
        
        cashier_rows = [
            r for r in canonical_records 
            if str(r.get("_extra.Cashier_Name", "")) in ("Tahir", "Zubair")
        ]
        assert len(cashier_rows) > 0

    def test_client_type_credit_vs_cash(self, canonical_records):
        q = "Are there any credit client transactions versus cash transactions?"
        assert classify_route(q) == RouteType.RAG
        
        client_types = {
            r.get("_extra.Client_Type") for r in canonical_records 
            if pd.notna(r.get("_extra.Client_Type"))
        }
        assert "Cash" in client_types or "Credit" in client_types

    def test_delivery_rider_orders(self, canonical_records):
        q = "How many delivery orders with rider phone numbers were recorded?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        delivery_rows = [
            r for r in canonical_records 
            if "DELIVERY" in str(r.get("customer_id", ""))
        ]
        assert len(delivery_rows) > 0


# ==============================================================================
# Category 5: Urdu & Roman-Urdu Queries (Bilingual Support)
# ==============================================================================

class TestBilingualQueries:
    def test_roman_urdu_bill_items(self, canonical_records):
        q = "Bill No 400001 mein kon konsi dawaiyan bechi gayi hain?"
        assert classify_route(q) == RouteType.RAG
        
        matches = [r for r in canonical_records if str(r.get("invoice_id")) == "400001"]
        assert len(matches) > 0

    def test_roman_urdu_branch_sales(self, router, canonical_records):
        q = "Al Noor Pharmacy branch ki total sales kitni hain?"
        assert classify_route(q) == RouteType.ANALYTICS
        
        computed, sources = router.compute(q, {}, canonical_records, domain="pharmacy")
        assert "total_revenue" in computed
        assert computed["total_revenue"]["status"] == "ok"

    def test_roman_urdu_bonus_quantity(self, canonical_records):
        q = "Kya Rigix tablets par koi bonus quantity di gayi thi?"
        assert classify_route(q) == RouteType.RAG
        
        rigix_bonus = [
            r for r in canonical_records 
            if "Rigix" in str(r.get("product_id", "")) and float(r.get("_extra.Bonus_Quantity", 0) or 0) > 0
        ]
        assert len(rigix_bonus) > 0

    def test_urdu_script_bill_details(self, canonical_records):
        q = "بل نمبر 400002 کی تفصیلات بتائیں"
        assert classify_route(q) == RouteType.RAG
        
        matches = [r for r in canonical_records if str(r.get("invoice_id")) == "400002"]
        assert len(matches) > 0
        assert any("Zinnat" in str(r.get("product_id", "")) for r in matches)


# ==============================================================================
# Full End-to-End Chatbot Pipeline Integration
# ==============================================================================

@patch("app.rag.chat.llm")
def test_full_rag_chatbot_pipeline_with_excel_data(mock_llm):
    """Verifies that RAGChat handles queries with dataset provenance and formatted context."""
    mock_llm.chat.return_value = (
        "Based on Pharmacy_Sales_Dataset.xlsx (Bill No 400001), Eziday 50mg Cream was sold for Rs 5,679.44."
    )
    
    chat = RAGChat()
    
    # Mock knowledgebase search results
    sample_chunk = RetrievedChunk(
        text="Bill 400001: Eziday 50mg Cream, Quantity: 2, Unit Price: 2989.18, Amount: 5679.44",
        metadata={
            "source_file": "Pharmacy_Sales_Dataset.xlsx",
            "invoice_id": "400001",
            "product_id": "Eziday 50mg Cream",
            "source_row": 2
        },
        score=0.92
    )
    
    with patch.object(chat.kb, "search", return_value=[sample_chunk]):
        req = ChatRequest(
            question="What medicines were sold in Bill No 400001?",
            session_id="test_excel_session",
            domain="pharmacy"
        )
        resp = chat.ask(req)
        
        assert resp.route == RouteType.RAG
        assert "Eziday" in resp.answer
        assert len(resp.sources) == 1
        assert resp.sources[0].source_file == "Pharmacy_Sales_Dataset.xlsx"
        assert resp.sources[0].source_row == 2
        assert "Invoice 400001" in resp.sources[0].label
