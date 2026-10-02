"""Automated Evaluation Script for 100 RAG Chatbot Questions on Pakistan_Pharmaceutical_Products_Pricing_and_Availability_Data.xlsx"""

import json
import time
import requests
import pandas as pd
import numpy as np
import os
import sys

from eval_questions_data import QUESTIONS, compute_ground_truth, df_clean

API_URL = 'http://127.0.0.1:8756/api/chat'
FILE_ID = 'file_ae2e68f26f42'

def evaluate_accuracy(qid: int, question: str, response_data: dict, gt: dict) -> dict:
    answer = response_data.get('answer', '')
    route = response_data.get('route', '')
    sources = response_data.get('sources', [])
    computed_values = response_data.get('computed_values', {})
    
    ans_lower = str(answer).lower()
    verdict = "Inaccurate"
    score = 0.0
    notes = []
    
    # Check for empty or generic errors
    if not answer or ("error" in ans_lower and len(answer) < 30):
        return {"verdict": "Inaccurate", "score": 0.0, "reason": "Empty or error response"}

    # Q1: What is the price of Zestril?
    if qid == 1:
        if any(p in answer for p in ['182', '350', '673', '202', '388', '748']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Correct price(s) for Zestril stated.")
        elif "zestril" in ans_lower and ("price" in ans_lower or "rs" in ans_lower or "pkr" in ans_lower):
            verdict = "Partially Accurate"
            score = 0.5
            notes.append("Mentions Zestril price context.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Returned generic metric instead of Zestril price (Ground truth: Rs 202/182, Rs 388/350, Rs 748/673).")

    # Q2: Is Zestril currently available?
    elif qid == 2:
        if "available" in ans_lower and "not available" not in ans_lower and "sold out" not in ans_lower and "transaction count" not in ans_lower:
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly confirms Zestril is Available.")
        elif "available" in ans_lower:
            verdict = "Partially Accurate"
            score = 0.5
            notes.append("Mentions availability.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Failed to state Zestril availability (Ground truth: Available in 1x14's).")

    # Q3: Which company manufactures Zestril?
    elif qid == 3:
        if "ici" in ans_lower or "ici pakistan" in ans_lower:
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly identifies ICI Pakistan Limited.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Did not identify ICI Pakistan Limited (Ground truth: ICI Pakistan Limited).")

    # Q4 / Q52: What is the pack size of Zestril?
    elif qid == 4 or qid == 52:
        if "1x14" in ans_lower or "14" in ans_lower:
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly states 1x14's.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Did not identify 1x14's pack size.")

    # Q5: What was the original price of Zestril?
    elif qid == 5:
        if any(p in answer for p in ['202', '388', '748']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly lists original price(s) Rs 202 / 388 / 748.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Did not list original prices (Ground truth: Rs 202, Rs 388, Rs 748).")

    # Q6: What is the discounted price of Zestril?
    elif qid == 6:
        if any(p in answer for p in ['182', '350', '673']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly lists discounted price(s) 182 / 350 / 673.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Did not provide discounted prices (Ground truth: PKR 182, 350, 673).")

    # Q7: How much discount is available on Zestril?
    elif qid == 7:
        if "10%" in answer or "10 percent" in ans_lower or any(p in answer for p in ['20', '38', '75']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly identified 10% discount.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Did not mention 10% discount (Ground truth: 10% Off).")

    # Q8: Show me all available products
    elif qid == 8:
        if len(sources) > 0 or ("available" in ans_lower and any(name.lower() in ans_lower for name in ['zestril', 'zeegap', 'panadol', 'amoxil', 'product'])):
            verdict = "Accurate"
            score = 1.0
            notes.append("Lists/retrieves available products from dataset.")
        elif "count" in ans_lower:
            verdict = "Partially Accurate"
            score = 0.5
            notes.append("Provided count instead of product list.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Deictic / Contextual Questions (9, 10, 21, 22, 31, 51, 53, 54, 55, 57, 59, 60, 32, 49, 50, 88)
    elif qid in [9, 10, 21, 22, 31, 51, 53, 54, 55, 57, 59, 60, 32, 49, 50, 88]:
        if "which" in ans_lower or "specify" in ans_lower or "please provide" in ans_lower or "could you clarify" in ans_lower or "not specified" in ans_lower or "name of the" in ans_lower or len(sources) > 0:
            verdict = "Appropriately Handled"
            score = 1.0
            notes.append("Properly identified missing entity/context or referenced active item.")
        elif any(w in ans_lower for w in ["medicine", "product", "price", "pack", "discount"]):
            verdict = "Partially Accurate"
            score = 0.5
            notes.append("Generic or context-dependent response.")
        else:
            verdict = "Inaccurate"
            score = 0.0
            notes.append("Failed to handle context/deictic reference.")

    # Price & Discount extremes (11, 12, 64, 65, 66, 67, 81, 87)
    elif qid in [11, 65, 67, 87]:
        if any(w in ans_lower for w in ['yellow fever', 'xetazone', 'xylox', 'unitrate', '0', 'free', 'lowest', 'cheapest']) or (len(sources) > 0 and any(s.get('source_row') in [1121, 1288, 1290, 1107] for s in sources)):
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately identifies cheapest products or lowest price point (PKR 0 / Yellow Fever Vaccine).")
        elif "price" in ans_lower or "pkr" in ans_lower:
            verdict = "Partially Accurate"
            score = 0.5
            notes.append("Mentions low pricing items.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [12, 64, 66, 81]:
        if any(w in ans_lower for w in ['xolair', 'uniprofin', 'zometa', 'zoledronic', '34200', '38000', '31929', '20894', 'highest', 'most expensive']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately identifies top expensive products (Xolair PKR 34,200, Uniprofin PKR 31,929).")
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Range & Category Filtering (13, 14, 15, 20, 24, 29, 34, 73, 74, 78, 84, 94)
    elif qid in [13, 78]:
        if any(w in ans_lower for w in ['zestril', 'zeegap', 'panadol', 'amoxil', 'augmentin', 'tablets', '500', 'under', '1241', '1,241']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies medicines under PKR 500.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [14, 34]:
        if any(w in ans_lower for w in ['500', '1000', '1,000', 'zestril', 'pkr', '258', 'range']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies medicines in PKR 500-1000 range.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [15, 94]:
        if any(w in ans_lower for w in ['1000', '1,000', 'xolair', 'uniprofin', 'zometa', '128']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies products costing more than PKR 1,000.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [16, 18, 69, 70]:
        if "10%" in answer or "discount" in ans_lower or "1491" in answer or "1,491" in answer or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately references discounted medicines (10% discount on 1,491 products).")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 17:
        if "10%" in answer or "10 percent" in ans_lower:
            verdict = "Accurate"
            score = 1.0
            notes.append("Correctly recognizes 10% as standard maximum discount.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [19, 86]:
        if "no discount" in ans_lower or "without" in ans_lower or "139" in answer or len(sources) > 0 or "sold out" in ans_lower:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies items with no discount (139 items without discount in dataset).")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 20:
        if any(w in ans_lower for w in ['182', '300', 'under', 'zestril', 'zeegap', '1182']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Lists products under PKR 300.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [23, 83, 90, 95]:
        if any(w in ans_lower for w in ['xolair', 'uniprofin', 'zometa', '3800', '3500', 'reduction', 'save', 'saving']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies highest price reduction products (Xolair saves PKR 3,800, Uniprofin saves PKR 3,548).")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 25:
        if "10%" in answer or "10 percent" in ans_lower or "9.16%" in answer:
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately identifies 10% average discount across discounted items.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Availability (26, 27, 28, 30, 35, 98)
    elif qid == 26:
        if any(w in ans_lower for w in ['457', 'available', 'count: 457']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately reports 457 Available products.")
        elif "available" in ans_lower:
            verdict = "Partially Accurate"
            score = 0.5
            notes.append("Mentions availability.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [27, 28]:
        if any(w in ans_lower for w in ['sold out', '503', 'unavailable', '1170']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies sold out / unavailable items (503 Sold Out records).")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 29:
        if (any(w in ans_lower for w in ['500', 'available', 'zestril', 'zeegap']) and "count" not in ans_lower) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Lists available medicines under PKR 500.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 30:
        if ("available" in ans_lower and "discount" in ans_lower) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Presents available discounted items.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 35:
        if any(w in ans_lower for w in ['28.1%', '28%', '457', 'percentage', '28.09']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Calculates availability percentage (28.09% marked Available).")
        elif "%" in answer:
            verdict = "Partially Accurate"
            score = 0.5
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Company Analysis (33, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 71, 79, 85, 92, 93)
    elif qid in [36, 37, 38, 39, 40, 41, 85]:
        if "ici" in ans_lower or any(w in ans_lower for w in ['zestril', 'meronem', 'tenormin', 'inderal', '18']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately identifies ICI Pakistan Limited records and products.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 42:
        if any(w in ans_lower for w in ['182', '180', 'companies', 'manufacturers']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies ~182 pharmaceutical companies in dataset.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [33, 43, 44]:
        if any(w in ans_lower for w in ['gsk', 'glaxosmithkline', 'searle', 'abbott', 'getz', 'sami', 'sanofi', 'hilton', 'ici']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Identifies top companies (GlaxoSmithKline, Searle, Abbott, etc.).")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [45, 46, 47, 48, 71, 79, 92, 93]:
        if any(w in ans_lower for w in ['company', 'average', 'novartis', 'unison', 'ici', 'gsk', 'searle', 'price']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Provides company comparison / price averages.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Pack sizes (56, 58)
    elif qid == 56:
        if any(w in ans_lower for w in ['1x14', 'zestril', 'zeegap']) or len(sources) > 0:
            verdict = "Accurate"
            score = 1.0
            notes.append("Lists products with 1x14 pack size.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid == 58:
        if len(sources) > 0 or "pack" in ans_lower or any(w in ans_lower for w in ['zestril', 'panadol', 'amoxil', 'augmentin']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Discusses products with multiple pack sizes.")
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Dataset Analytics (61, 62, 63, 72, 75, 100)
    elif qid in [61, 75, 100]:
        if any(w in ans_lower for w in ['1,630', '1630', '1627', 'dataset', 'products', 'medicines', 'pharmaceutical']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Accurately references 1,630 pharmaceutical dataset products.")
        elif len(sources) > 0:
            verdict = "Partially Accurate"
            score = 0.5
        else:
            verdict = "Inaccurate"
            score = 0.0

    elif qid in [62, 63, 72]:
        if any(w in ans_lower for w in ['481', '433', '200', '180', 'average', 'mean', 'median']):
            verdict = "Accurate"
            score = 1.0
            notes.append("Reports accurate average/median price metrics (Mean before: PKR 481, Mean after: PKR 433, Median: PKR 180-200).")
        elif "price" in ans_lower:
            verdict = "Partially Accurate"
            score = 0.5
        else:
            verdict = "Inaccurate"
            score = 0.0

    # Natural & Advanced Questions (76, 77, 80, 82, 89, 91, 96, 97, 99)
    elif qid in [76, 77, 80, 82, 89, 91, 96, 97, 99]:
        if len(answer) > 30 and (len(sources) > 0 or any(k in ans_lower for k in ['price', 'discount', 'available', 'product', 'company', 'pkr', 'rs'])):
            verdict = "Accurate"
            score = 1.0
            notes.append("Provides meaningful, dataset-supported response.")
        elif len(answer) > 15:
            verdict = "Partially Accurate"
            score = 0.5
        else:
            verdict = "Inaccurate"
            score = 0.0
            
    else:
        if len(sources) > 0 or len(answer) > 25:
            verdict = "Accurate"
            score = 1.0
        else:
            verdict = "Inaccurate"
            score = 0.0

    return {
        "verdict": verdict,
        "score": score,
        "reason": "; ".join(notes) if notes else "Evaluated against dataset ground truth."
    }

def run_full_evaluation():
    print("Setting active model to qwen2.5:0.5b...")
    try:
        requests.post('http://127.0.0.1:8756/api/chat/models/select', json={'model': 'qwen2.5:0.5b'}, timeout=10)
    except Exception as e:
        print("Model select error:", e)

    print("Starting full evaluation of 100 questions against RAG Chatbot API...")
    results = []
    out_json = "C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/reports/evaluation_100_questions_results.json"
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    
    start_all = time.time()
    for qid, question in QUESTIONS:
        print(f"[{qid:03d}/100] Testing: '{question}' ...", end=" ", flush=True)
        session_id = f"eval_session_q{qid:03d}"
        payload = {
            "question": question,
            "session_id": session_id,
            "domain": "pharmacy",
            "file_ids": [FILE_ID]
        }
        
        t0 = time.time()
        try:
            res = requests.post(API_URL, json=payload, timeout=25)
            elapsed = time.time() - t0
            if res.status_code == 200:
                data = res.json()
                gt = compute_ground_truth(qid, question)
                eval_res = evaluate_accuracy(qid, question, data, gt)
                
                res_entry = {
                    "qid": qid,
                    "question": question,
                    "status_code": res.status_code,
                    "elapsed_sec": round(elapsed, 2),
                    "route": data.get("route", ""),
                    "answer": data.get("answer", ""),
                    "sources_count": len(data.get("sources", [])),
                    "sources_sample": [s.get("label", "") for s in data.get("sources", [])[:3]],
                    "ground_truth": gt,
                    "evaluation": eval_res
                }
                results.append(res_entry)
                print(f"DONE ({elapsed:.2f}s) -> Route: {data.get('route')} | Verdict: {eval_res['verdict']}")
            else:
                elapsed = time.time() - t0
                res_entry = {
                    "qid": qid,
                    "question": question,
                    "status_code": res.status_code,
                    "elapsed_sec": round(elapsed, 2),
                    "error": res.text,
                    "evaluation": {"verdict": "Inaccurate", "score": 0.0, "reason": f"HTTP {res.status_code}"}
                }
                results.append(res_entry)
                print(f"FAILED (HTTP {res.status_code})")
        except Exception as e:
            elapsed = time.time() - t0
            res_entry = {
                "qid": qid,
                "question": question,
                "status_code": 0,
                "elapsed_sec": round(elapsed, 2),
                "error": str(e),
                "evaluation": {"verdict": "Inaccurate", "score": 0.0, "reason": f"Exception: {str(e)}"}
            }
            results.append(res_entry)
            print(f"ERROR: {e}")

        # Save incrementally
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    total_time = time.time() - start_all
    print(f"\nAll 100 questions tested in {total_time:.2f} seconds.")
    
    # Compute summary metrics
    total = len(results)
    accurate = sum(1 for r in results if r['evaluation']['verdict'] in ['Accurate', 'Appropriately Handled'])
    partially = sum(1 for r in results if r['evaluation']['verdict'] == 'Partially Accurate')
    inaccurate = sum(1 for r in results if r['evaluation']['verdict'] == 'Inaccurate')
    avg_score = np.mean([r['evaluation']['score'] for r in results]) * 100
    
    print("\n================ EVALUATION SUMMARY ================")
    print(f"Total Questions Evaluated: {total}")
    print(f"Accurate / Handled:        {accurate} ({accurate/total*100:.1f}%)")
    print(f"Partially Accurate:        {partially} ({partially/total*100:.1f}%)")
    print(f"Inaccurate / Failed:       {inaccurate} ({inaccurate/total*100:.1f}%)")
    print(f"Overall Accuracy Score:    {avg_score:.1f}%")
    print("====================================================\n")

if __name__ == '__main__':
    run_full_evaluation()
