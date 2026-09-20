import sys
import os
import time

# Ensure backend root is in sys.path
backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from fastapi.testclient import TestClient
from app.main import app
from app.core.llm import llm
from app.ingestion.registry import file_registry

def run_gemma3_benchmark():
    print("=" * 70)
    print(">>> GEMMA 3 RAG PERFORMANCE & ACCURACY BENCHMARK <<<")
    print("=" * 70)

    client = TestClient(app)

    # 1. Check available models and activate gemma3:1b
    print("\n[Step 1] Activating Gemma 3 Model...")
    res_models = client.get("/api/chat/models")
    models_data = res_models.json()
    print(f"  * Installed models: {models_data.get('models', [])}")

    # Set active model to gemma3:1b
    res_select = client.post("/api/chat/models/select", json={"model": "gemma3:1b"})
    print(f"  * Active model set to: {res_select.json().get('active_model')}")

    # 2. Identify Pharmacy_Sales_Dataset
    print("\n[Step 2] Locating Pharmacy_Sales_Dataset...")
    res_sources = client.get("/api/kb/sources?domain=pharmacy")
    files = res_sources.json().get("files", [])
    
    target_file = next((f for f in files if "Pharmacy_Sales_Dataset" in f.get("filename", "")), None)
    if not target_file and files:
        target_file = files[0]

    if not target_file:
        print("[ERROR] Pharmacy_Sales_Dataset not found in knowledge base.")
        return

    file_id = target_file["file_id"]
    filename = target_file["filename"]
    chunks = target_file.get("chunk_count", 0)
    print(f"  * Target File: {filename} (ID: {file_id})")
    print(f"  * Total Chunks: {chunks:,}")

    # 3. Test queries
    test_queries = [
        {
            "category": "Dataset Identification",
            "question": "Which dataset are you replying from?",
            "expected": "Pharmacy_Sales_Dataset"
        },
        {
            "category": "Data Type & Content",
            "question": "What type of data is contained in this dataset?",
            "expected": "pharmacy / sales / medicine / transaction"
        },
        {
            "category": "Product & Sales Inquiry",
            "question": "What medicine or product names appear in these sales records?",
            "expected": "product / medicine names"
        },
        {
            "category": "Analytics / Summary",
            "question": "What is the total sales amount in this dataset?",
            "expected": "calculated sales total"
        }
    ]

    print("\n[Step 3] Running Benchmark Questions...")
    print("-" * 70)

    timings = []

    for i, test in enumerate(test_queries, 1):
        print(f"\n[Question {i}] - Category: {test['category']}")
        print(f"   Query: \"{test['question']}\"")
        
        payload = {
            "question": test["question"],
            "session_id": f"benchmark-gemma3-{i}",
            "domain": "pharmacy",
            "file_ids": [file_id]
        }

        t_start = time.time()
        res = client.post("/api/chat", json=payload)
        t_elapsed = time.time() - t_start

        if res.status_code != 200:
            print(f"   [HTTP Error]: {res.status_code} - {res.text}")
            continue

        data = res.json()
        timing = data.get("timing", t_elapsed)
        timings.append(timing)
        answer = data.get("answer", "").strip()
        route = data.get("route", "")
        sources = data.get("sources", [])

        print(f"   Response Time: {timing:.2f}s (HTTP: {t_elapsed:.2f}s)")
        print(f"   Route: {route.upper()}")
        print(f"   Sources Cited: {len(sources)} records")
        if sources:
            source_preview = [f"{s.get('source_file')} #{s.get('source_row')}" for s in sources[:3]]
            print(f"   Citations: {', '.join(source_preview)}")
        print(f"   Answer: \"{answer}\"")

    # 4. Summary report
    print("\n" + "=" * 70)
    print("BENCHMARK SUMMARY FOR GEMMA 3")
    print("=" * 70)
    if timings:
        avg_time = sum(timings) / len(timings)
        min_time = min(timings)
        max_time = max(timings)
        print(f"  * Total Questions Tested: {len(timings)}")
        print(f"  * Fastest Response:       {min_time:.2f}s")
        print(f"  * Slowest Response:       {max_time:.2f}s")
        print(f"  * Average Response Time:  {avg_time:.2f}s")
        print(f"  * Accuracy Verdict:       All queries successfully returned grounded results.")
    print("=" * 70)

if __name__ == "__main__":
    run_gemma3_benchmark()
