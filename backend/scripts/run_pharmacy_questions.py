import os
import sys
import pandas as pd

# Ensure backend path is in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.ingestion.store import KnowledgeBase
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest

def run_tests():
    csv_path = os.path.abspath(os.path.join(backend_dir, "..", "data", "uploads", "test_pharmacy_small.csv"))
    print(f"Loading CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} rows.")

    kb = KnowledgeBase()
    # Ingest dataframe for testing
    print("Ingesting test_pharmacy_small.csv into KnowledgeBase...")
    kb.add_dataframe(
        canonical_df=df,
        source_meta={"source_file": "test_pharmacy_small.csv", "source_connector": "CSVConnector"},
        domain="pharmacy",
        file_id="test_pharmacy_small"
    )
    print("Ingestion complete.")

    chat = RAGChat()
    
    questions = [
        # Compound Aggregates & Averages
        "What was the average transaction value in February 2026?",
        "What is our total revenue and average transaction value across all records?",
        
        # Category & Product Breakdowns
        "Show me the revenue breakdown by product.",
        "Which supplier generated the highest revenue?",
        "Give me the revenue breakdown by month.",
        
        # Multi-Condition Time Window
        "How much revenue did we make in January 2026 vs February 2026?",
        "What was the total sales between 1st February and 28th February?",
        
        # Expiry Risk Analysis
        "Which medicines are already expired or expiring within the next 60 days?",
        "What is the total monetary value of products expiring soon?",
        "List all batches of medicines that expire in 2025 or 2026.",
        
        # Schedule / Controlled Substance & Rx Tracking
        "How many transactions or units were sold for scheduled / prescription medicines?",
        "Which medicines have a schedule flag or require a prescription?"
    ]

    for q in questions:
        print("\n" + "="*80)
        print(f"QUESTION: {q}")
        req = ChatRequest(
            question=q,
            domain="pharmacy",
            source_files=["test_pharmacy_small.csv"],
            file_ids=["test_pharmacy_small"],
            session_id=f"test_{hash(q)}"
        )
        resp = chat.ask(req)
        print(f"ROUTE: {resp.route}")
        if resp.computed_values:
            print(f"COMPUTED KEYS: {list(resp.computed_values.keys())}")
        print(f"ANSWER:\n{resp.answer}")
        print(f"SOURCES ({len(resp.sources)}): {[s.label for s in resp.sources[:5]]}")

if __name__ == "__main__":
    run_tests()
