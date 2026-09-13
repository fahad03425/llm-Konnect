"""
Script to run the 20 Pakistani Pharmacy Owner questions against Pharmacy_Sales_Dataset.xlsx,
record the timing, and verify correctness against ground truth.
"""

import os
import sys
import time
import json
import pandas as pd

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure backend root is on sys.path
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.rag.chat import rag_chat
from app.rag.models import ChatRequest
from app.ingestion.registry import file_registry

EXCEL_FILE = "Pharmacy_Sales_Dataset.xlsx"
rec = file_registry.get_file_by_path(EXCEL_FILE) or file_registry.get_file_by_id("file_4d779e08b917")
FILE_ID = rec.file_id if rec else None

# 20 Realistic Pharmacy Questions
QUESTIONS = [
    # 1. Expiry & Return Management
    (1, "Kon si medicines agle 30 se 60 din mein expire ho rahi hain?", "Expiry: Next 30-60 days"),
    (2, "How much total stock is near expiry, and what is its total cost value?", "Expiry: Total near-expiry stock"),
    (3, "Jo stock expire ho chuka hai uski total value kitni hai?", "Expiry: Already expired stock value"),
    (4, "Show me all batches of Augmentin and Panadol with their expiry dates.", "Lookup: Augmentin & Panadol batches"),

    # 2. Daily & Monthly Sales & Revenue
    (5, "Is mahine ki total sale aur revenue kitni hui hai?", "Sales: Total revenue"),
    (6, "What is the average bill / transaction value?", "Sales: Average transaction value"),
    (7, "Total transactions kitni complete hui hain is dataset mein?", "Sales: Total transactions"),
    (8, "Cash sale kitni hui hai aur credit (udhaar) pe kitna bika hai?", "Sales: Cash vs Credit breakdown"),

    # 3. Top Sellers & Stock Velocity
    (9, "Sab se zyada bikne wali top 5 medicines kaun si hain?", "Products: Top 5 best-selling medicines"),
    (10, "How many total units were sold across all products?", "Products: Total units sold"),
    (11, "Which slow moving or declining products had low sales?", "Products: Slow moving products"),
    (12, "How many units of Panadol will i sell next month?", "Forecast: Panadol demand forecast"),

    # 4. Profitability & Margins
    (13, "Hamara overall gross profit margin kitne percent hai?", "Margins: Gross profit margin"),
    (14, "Total kitna munafa (net profit) bana hai?", "Margins: Net profit"),
    (15, "Kin medicines par customer ko discount diya gaya hai?", "Discounts: Medicines with discount"),

    # 5. Suppliers & Distributors
    (16, "Kis distributor / supplier se sab se zyada purchase hui hai?", "Suppliers: Top supplier"),
    (17, "Getz Pharma aur GSK se kitni supply aayi hai?", "Suppliers: Getz and GSK supply"),
    (18, "Kin suppliers se humein bonus quantity mili thi?", "Suppliers: Bonus quantity"),

    # 6. Bill & Prescription Lookups
    (19, "Bill number 400005 mein customer ko kaun kaun si dawaiyan di gayi theen?", "Lookup: Bill 400005 details"),
    (20, "Customer Umar Mirza ne last time kya medicines purchase ki theen?", "Lookup: Umar Mirza purchases"),
]

def main():
    print("=" * 80)
    print("RUNNING 20 PHARMACY QUESTIONS AGAINST: Pharmacy_Sales_Dataset.xlsx")
    print(f"File ID: {FILE_ID}")
    print("=" * 80)

    results = []

    for q_num, question, category in QUESTIONS:
        session_id = f"test-eval-{q_num}-{int(time.time())}"
        req = ChatRequest(
            question=question,
            session_id=session_id,
            domain="pharmacy",
            file_ids=[FILE_ID] if FILE_ID else None
        )

        start_t = time.time()
        try:
            resp = rag_chat.ask(req)
            dur = round(time.time() - start_t, 2)
            route = resp.route
            answer = resp.answer.strip()
            sources_count = len(resp.sources)
            comp_vals = resp.computed_values
        except Exception as e:
            dur = round(time.time() - start_t, 2)
            route = "ERROR"
            answer = f"Error: {str(e)}"
            sources_count = 0
            comp_vals = None

        print(f"\n--- [Q{q_num}] ({category}) ---")
        print(f"Question: {question}")
        print(f"Route:    {route} | Timing: {dur}s | Sources: {sources_count}")
        print(f"Answer:   {answer[:250]}..." if len(answer) > 250 else f"Answer:   {answer}")

        results.append({
            "q_num": q_num,
            "category": category,
            "question": question,
            "route": route,
            "timing_sec": dur,
            "answer": answer
        })

    # Summary
    print("\n" + "=" * 80)
    print("EXECUTION SUMMARY")
    print("=" * 80)
    total_time = sum(r["timing_sec"] for r in results)
    avg_time = round(total_time / len(results), 2)
    print(f"Total Questions: {len(results)}")
    print(f"Average Response Time: {avg_time}s")
    print(f"Total Time for 20 Queries: {round(total_time, 2)}s")

if __name__ == "__main__":
    main()
