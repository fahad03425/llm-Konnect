import urllib.request
import json
import time

API_URL = "http://127.0.0.1:8756/api/chat"
FILE_ID = "file_dcd43c20fa81"
SOURCE_FILE = "pharmacy_single_store_data.xlsx"

complex_questions = [
    {
        "id": 1,
        "title": "Cashier Anomaly & Return Rate",
        "question": "Which cashier has the highest return or refund rate percentage relative to their total sales transactions in the dataset?"
    },
    {
        "id": 2,
        "title": "Supplier Concentration Risk",
        "question": "What percentage of total procurement budget is concentrated in the top 3 suppliers?"
    },
    {
        "id": 3,
        "title": "Supply Chain Capital Lockup",
        "question": "How much total capital is locked in Pending or Partially Received purchase orders?"
    },
    {
        "id": 4,
        "title": "Category Return Rate Vulnerability",
        "question": "Which product category has the highest return rate percentage in the dataset?"
    },
    {
        "id": 5,
        "title": "Discount vs Full-Price Return Correlation",
        "question": "What is the return rate percentage of discounted sales compared to full-price sales?"
    },
    {
        "id": 6,
        "title": "Peak-Hour Payment Method Shift",
        "question": "What is the distribution of payment methods during peak evening hours (17:00 to 22:00) compared to morning hours?"
    },
    {
        "id": 7,
        "title": "Procurement Realization Deficit (Capital Drag)",
        "question": "Which products have the highest purchase spend compared to sales revenue?"
    }
]

results = []

for item in complex_questions:
    payload = {
        "question": item["question"],
        "session_id": f"complex_test_{item['id']}",
        "domain": "pharmacy",
        "file_ids": [FILE_ID],
        "source_files": [SOURCE_FILE]
    }
    
    t0 = time.time()
    try:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - t0
            results.append({
                "id": item["id"],
                "title": item["title"],
                "question": item["question"],
                "route": resp_data.get("route"),
                "timing": resp_data.get("timing", round(elapsed, 2)),
                "answer": resp_data.get("answer")
            })
    except Exception as e:
        results.append({
            "id": item["id"],
            "title": item["title"],
            "question": item["question"],
            "route": "error",
            "timing": 0,
            "answer": f"Error: {e}"
        })

with open("C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/backend/complex_test_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("Saved complex test results successfully to complex_test_results.json")
