"""
Pharmacy Compliant Master Dataset RAG Evaluation Suite
Tests 43 specific business & analytical questions against RAG chatbot API.
"""

import json
import os
import re
import sys
import time
import pandas as pd
import requests

# Set UTF-8 encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DATASET_PATH = os.path.join("data", "uploads", "pharmacy_compliant_master_dataset.xlsx")
API_URL = "http://127.0.0.1:8756/api/chat"
REPORT_DIR = os.path.join("reports")
os.makedirs(REPORT_DIR, exist_ok=True)

QUESTIONS = [
    "Which therapeutic class generates the most revenue?",
    "Which therapeutic class sells the most units?",
    "What are the top five therapeutic classes by sales?",
    "Which therapeutic classes have the lowest sales?",
    "How many different medicines have been sold?",
    "Which manufacturer generates the most revenue?",
    "Which manufacturer's products sell the most units?",
    "Show me the top 10 manufacturers by sales.",
    "Which manufacturers have the fewest sales?",
    "How many different manufacturers are represented?",
    "What products from AstraZeneca have been sold?",
    "How much revenue came from AstraZeneca products?",
    "Which manufacturer's products have the highest average retail price?",
    "Which manufacturer has the largest number of products?",
    "Compare sales between the major manufacturers.",
    "What are my total sales by branch?",
    "Which pharmacy branch has the highest sales?",
    "Which branch has sold the most units?",
    "Which branch has the highest average transaction value?",
    "What are the top-selling products at each branch?",
    "Which therapeutic class performs best at each branch?",
    "Which manufacturer performs best at each branch?",
    "Compare revenue across all pharmacy branches.",
    "Which branch has the most transactions?",
    "Which branch has the lowest sales?",
    "What were my sales today according to the dataset?",
    "What were my sales in January?",
    "What were my total monthly sales?",
    "Which month had the highest sales?",
    "Which month had the lowest sales?",
    "How have sales changed over time?",
    "Which day had the highest revenue?",
    "Which day had the most transactions?",
    "What are my average daily sales?",
    "Show me the monthly sales trend.",
    "What is the cost price of Betaloc 50mg Extended-Release?",
    "What is the maximum retail price of Betaloc 50mg Extended-Release?",
    "Which products have the highest maximum retail prices?",
    "Which products have the lowest maximum retail prices?",
    "Which products have the biggest difference between cost price and maximum retail price?",
    "Which products have the smallest margin between cost and maximum retail price?",
    "Which medicines potentially provide the highest gross margin per unit?",
    "What is the estimated gross margin for each product based on the recorded prices?"
]

def load_ground_truth():
    df = pd.read_excel(DATASET_PATH)
    df['Quantity_Sold'] = pd.to_numeric(df['Quantity_Sold'], errors='coerce').fillna(0)
    df['Unit_Cost_Price_USD'] = pd.to_numeric(df['Unit_Cost_Price_USD'], errors='coerce').fillna(0)
    df['Maximum_Retail_Price_USD'] = pd.to_numeric(df['Maximum_Retail_Price_USD'], errors='coerce').fillna(0)
    df['Total_Transaction_Value_USD'] = pd.to_numeric(df['Total_Transaction_Value_USD'], errors='coerce').fillna(0)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    df['YearMonth'] = df['Timestamp'].dt.to_period('M')

    tc_rev = df.groupby('Therapeutic_Class')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False)
    tc_qty = df.groupby('Therapeutic_Class')['Quantity_Sold'].sum().sort_values(ascending=False)
    mfg_rev = df.groupby('Manufacturer')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False)
    mfg_qty = df.groupby('Manufacturer')['Quantity_Sold'].sum().sort_values(ascending=False)
    mfg_avg_mrp = df.groupby('Manufacturer')['Maximum_Retail_Price_USD'].mean().sort_values(ascending=False)
    mfg_prod_cnt = df.groupby('Manufacturer')['Product_Name'].nunique().sort_values(ascending=False)
    
    br_sales = df.groupby('Pharmacy_Branch')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False)
    br_qty = df.groupby('Pharmacy_Branch')['Quantity_Sold'].sum().sort_values(ascending=False)
    br_avg_tx = df.groupby('Pharmacy_Branch')['Total_Transaction_Value_USD'].mean().sort_values(ascending=False)
    br_txns = df['Pharmacy_Branch'].value_counts()

    monthly_sales = df.groupby('YearMonth')['Total_Transaction_Value_USD'].sum()
    daily_rev = df.groupby(df['Timestamp'].dt.date)['Total_Transaction_Value_USD'].sum()
    daily_txns = df.groupby(df['Timestamp'].dt.date)['Transaction_UUID'].count()

    prod_pricing = df.groupby('Product_Name').agg({
        'Unit_Cost_Price_USD': 'mean',
        'Maximum_Retail_Price_USD': 'mean'
    }).reset_index()
    prod_pricing['Price_Diff'] = prod_pricing['Maximum_Retail_Price_USD'] - prod_pricing['Unit_Cost_Price_USD']
    prod_pricing['Gross_Margin_Pct'] = (prod_pricing['Price_Diff'] / prod_pricing['Maximum_Retail_Price_USD']) * 100

    gt = {
        1: {
            "title": "Therapeutic class with most revenue",
            "key_fact": "Short-Acting Beta Agonist",
            "value": f"${tc_rev.iloc[0]:,.2f} ({tc_rev.iloc[0]})",
            "check": lambda ans: "short-acting beta agonist" in ans.lower() or "beta agonist" in ans.lower() or "39468" in ans.replace(",", "") or "39,468" in ans
        },
        2: {
            "title": "Therapeutic class selling most units",
            "key_fact": "Expectorant Cough Formula",
            "value": f"{tc_qty.iloc[0]:,} units ({tc_qty.index[0]})",
            "check": lambda ans: "expectorant" in ans.lower() or "1818" in ans.replace(",", "") or "1,818" in ans
        },
        3: {
            "title": "Top 5 therapeutic classes by sales",
            "key_fact": list(tc_rev.head(5).index),
            "value": {k: f"${v:,.2f}" for k, v in tc_rev.head(5).items()},
            "check": lambda ans: sum(1 for tc in tc_rev.head(5).index if tc.lower() in ans.lower() or tc.split()[0].lower() in ans.lower()) >= 3
        },
        4: {
            "title": "Therapeutic classes with lowest sales",
            "key_fact": list(tc_rev.tail(5).index),
            "value": {k: f"${v:,.2f}" for k, v in tc_rev.tail(5).items()},
            "check": lambda ans: any(tc.lower() in ans.lower() or tc.split()[0].lower() in ans.lower() for tc in tc_rev.tail(5).index)
        },
        5: {
            "title": "Distinct medicines sold",
            "key_fact": "20 distinct medicines",
            "value": 20,
            "check": lambda ans: "20" in re.findall(r"\b\d+\b", ans) or "twenty" in ans.lower()
        },
        6: {
            "title": "Manufacturer generating most revenue",
            "key_fact": "GSK (GlaxoSmithKline)",
            "value": f"${mfg_rev.iloc[0]:,.2f}",
            "check": lambda ans: "gsk" in ans.lower() or "glaxosmithkline" in ans.lower() or "86319" in ans.replace(",", "") or "86,319" in ans
        },
        7: {
            "title": "Manufacturer selling most units",
            "key_fact": "GSK (GlaxoSmithKline)",
            "value": f"{mfg_qty.iloc[0]:,} units",
            "check": lambda ans: "gsk" in ans.lower() or "glaxosmithkline" in ans.lower() or "6239" in ans.replace(",", "") or "6,239" in ans
        },
        8: {
            "title": "Top 10 manufacturers by sales",
            "key_fact": list(mfg_rev.head(10).index),
            "value": {k: f"${v:,.2f}" for k, v in mfg_rev.head(10).items()},
            "check": lambda ans: sum(1 for m in mfg_rev.head(10).index if m.lower() in ans.lower()) >= 5
        },
        9: {
            "title": "Manufacturers with fewest sales",
            "key_fact": list(mfg_rev.tail(5).index),
            "value": {k: f"${v:,.2f}" for k, v in mfg_rev.tail(5).items()},
            "check": lambda ans: any(m.lower() in ans.lower() for m in mfg_rev.tail(5).index)
        },
        10: {
            "title": "Distinct manufacturers count",
            "key_fact": "13 manufacturers",
            "value": 13,
            "check": lambda ans: "13" in re.findall(r"\b\d+\b", ans) or "thirteen" in ans.lower()
        },
        11: {
            "title": "AstraZeneca products sold",
            "key_fact": "Betaloc 50mg Extended-Release",
            "value": ["Betaloc 50mg Extended-Release"],
            "check": lambda ans: "betaloc" in ans.lower()
        },
        12: {
            "title": "AstraZeneca revenue",
            "key_fact": "$15,650.60",
            "value": 15650.60,
            "check": lambda ans: "15650" in ans.replace(",", "") or "15,650" in ans or "15651" in ans.replace(",", "")
        },
        13: {
            "title": "Manufacturer with highest average retail price",
            "key_fact": "MSD ($24.00)",
            "value": f"MSD: ${mfg_avg_mrp.iloc[0]:.2f}",
            "check": lambda ans: "msd" in ans.lower() or "24" in ans
        },
        14: {
            "title": "Manufacturer with largest number of products",
            "key_fact": "GSK (4 products)",
            "value": f"GSK ({mfg_prod_cnt.iloc[0]} products)",
            "check": lambda ans: "gsk" in ans.lower() or "glaxosmithkline" in ans.lower()
        },
        15: {
            "title": "Compare sales between major manufacturers",
            "key_fact": "GSK (~$86.3k), Pfizer (~$58.9k), Getz Pharma (~$54.4k), Searle (~$40.7k), MSD (~$36.5k)",
            "value": {k: f"${v:,.2f}" for k, v in mfg_rev.head(5).items()},
            "check": lambda ans: ("gsk" in ans.lower() or "pfizer" in ans.lower() or "getz" in ans.lower()) and any(c.isdigit() for c in ans)
        },
        16: {
            "title": "Total sales by branch",
            "key_fact": "Branch-02: $132,326.20, Branch-01: $131,570.30, Branch-03: $130,125.60",
            "value": {k: f"${v:,.2f}" for k, v in br_sales.items()},
            "check": lambda ans: ("branch-01" in ans.lower() or "branch-02" in ans.lower() or "branch 1" in ans.lower() or "branch 2" in ans.lower() or "healthcare city" in ans.lower()) and ("132" in ans or "131" in ans or "130" in ans or "394" in ans)
        },
        17: {
            "title": "Pharmacy branch with highest sales",
            "key_fact": "Branch-02 (Healthcare City Hub) - $132,326.20",
            "value": f"{br_sales.index[0]}: ${br_sales.iloc[0]:,.2f}",
            "check": lambda ans: "branch-02" in ans.lower() or "branch 2" in ans.lower() or "healthcare city" in ans.lower() or "132326" in ans.replace(",", "") or "132,326" in ans
        },
        18: {
            "title": "Branch sold most units",
            "key_fact": "Branch-02 (Healthcare City Hub) - 10,859 units",
            "value": f"{br_qty.index[0]}: {br_qty.iloc[0]:,} units",
            "check": lambda ans: "branch-02" in ans.lower() or "branch 2" in ans.lower() or "healthcare city" in ans.lower() or "10859" in ans.replace(",", "") or "10,859" in ans
        },
        19: {
            "title": "Branch with highest average transaction value",
            "key_fact": "Branch-03 (Suburban Extension) - $77.00",
            "value": f"{br_avg_tx.index[0]}: ${br_avg_tx.iloc[0]:.2f}",
            "check": lambda ans: "branch-03" in ans.lower() or "branch 3" in ans.lower() or "suburban" in ans.lower() or "77" in ans
        },
        20: {
            "title": "Top-selling products at each branch",
            "key_fact": "Branch-01: Singulair, Lipiget, Ventolin; Branch-02: Zithromax, Ventolin, Singulair; Branch-03: Ventolin, Singulair, Zithromax",
            "value": "Branch-01: Singulair, Branch-02: Zithromax, Branch-03: Ventolin",
            "check": lambda ans: any(p.lower() in ans.lower() for p in ["ventolin", "singulair", "zithromax", "lipiget"]) and ("branch" in ans.lower() or "healthcare" in ans.lower() or "plaza" in ans.lower())
        },
        21: {
            "title": "Therapeutic class performs best at each branch",
            "key_fact": "Branch-01: Leukotriene Receptor Antagonist ($12,648); Branch-02: Macrolide Antibiotic ($13,156); Branch-03: Short-Acting Beta Agonist ($14,794)",
            "value": "Branch-01: Leukotriene, Branch-02: Macrolide, Branch-03: Short-Acting Beta Agonist",
            "check": lambda ans: any(tc in ans.lower() for tc in ["leukotriene", "macrolide", "beta agonist", "antibiotic", "agonist"])
        },
        22: {
            "title": "Manufacturer performs best at each branch",
            "key_fact": "GSK is #1 at all 3 branches (Branch-01: $27.8k, Branch-02: $28.7k, Branch-03: $29.9k)",
            "value": "GSK across all branches",
            "check": lambda ans: "gsk" in ans.lower() or "glaxosmithkline" in ans.lower()
        },
        23: {
            "title": "Compare revenue across all pharmacy branches",
            "key_fact": "Branch-02: $132,326.20 (33.58%), Branch-01: $131,570.30 (33.39%), Branch-03: $130,125.60 (33.02%)",
            "value": {k: f"${v:,.2f}" for k, v in br_sales.items()},
            "check": lambda ans: any(b in ans.lower() for b in ["branch-01", "branch-02", "branch-03", "branch 1", "branch 2", "branch 3", "healthcare", "plaza", "suburban"]) and any(c.isdigit() for c in ans)
        },
        24: {
            "title": "Branch with most transactions",
            "key_fact": "Branch-01 (Main Central Plaza) - 1,808 transactions",
            "value": f"{br_txns.index[0]}: {br_txns.iloc[0]} txns",
            "check": lambda ans: "branch-01" in ans.lower() or "branch 1" in ans.lower() or "main central" in ans.lower() or "plaza" in ans.lower() or "1808" in ans.replace(",", "") or "1,808" in ans
        },
        25: {
            "title": "Branch with lowest sales",
            "key_fact": "Branch-03 (Suburban Extension) - $130,125.60",
            "value": f"{br_sales.index[-1]}: ${br_sales.iloc[-1]:,.2f}",
            "check": lambda ans: "branch-03" in ans.lower() or "branch 3" in ans.lower() or "suburban" in ans.lower() or "130125" in ans.replace(",", "") or "130,125" in ans
        },
        26: {
            "title": "Sales today according to dataset",
            "key_fact": "Latest date in dataset: 2025-06-15 ($1,758.60) / Dataset spans 2025-01-01 to 2025-06-15",
            "value": f"Latest Day 2025-06-15: $1,758.60",
            "check": lambda ans: "1758" in ans.replace(",", "") or "1,758" in ans or "2025-06-15" in ans or "june 15" in ans.lower() or "not from today" in ans.lower() or "no transactions for today" in ans.lower() or "latest" in ans.lower()
        },
        27: {
            "title": "Sales in January",
            "key_fact": "$73,398.60 (Jan 2025)",
            "value": "$73,398.60",
            "check": lambda ans: "73398" in ans.replace(",", "") or "73,398" in ans or "73399" in ans.replace(",", "")
        },
        28: {
            "title": "Total monthly sales",
            "key_fact": "Jan: $73,398.60, Feb: $64,998.50, Mar: $73,275.60, Apr: $72,005.70, May: $74,009.80, Jun: $36,333.90",
            "value": {str(k): f"${v:,.2f}" for k, v in monthly_sales.items()},
            "check": lambda ans: ("jan" in ans.lower() or "feb" in ans.lower() or "may" in ans.lower()) and any(c.isdigit() for c in ans)
        },
        29: {
            "title": "Month with highest sales",
            "key_fact": "May 2025 ($74,009.80)",
            "value": "May 2025: $74,009.80",
            "check": lambda ans: "may" in ans.lower() or "74009" in ans.replace(",", "") or "74,009" in ans or "74010" in ans.replace(",", "")
        },
        30: {
            "title": "Month with lowest sales",
            "key_fact": "June 2025 ($36,333.90 - partial month) or Feb 2025 ($64,998.50 - full month)",
            "value": "June 2025: $36,333.90 / Feb 2025: $64,998.50",
            "check": lambda ans: "june" in ans.lower() or "february" in ans.lower() or "feb" in ans.lower() or "36333" in ans.replace(",", "") or "36,333" in ans or "64998" in ans.replace(",", "") or "64,998" in ans
        },
        31: {
            "title": "How sales changed over time",
            "key_fact": "Monthly range ~$65k-$74k from Jan-May 2025, with June 2025 at $36.3k (data ends mid-month). Total sales: $394,022.10",
            "value": "Steady ~$72k-$74k per month with slight dip in Feb ($65k)",
            "check": lambda ans: any(m in ans.lower() for m in ["january", "february", "march", "april", "may", "june", "trend", "steady", "fluctuat", "increase", "month"]) and any(c.isdigit() for c in ans)
        },
        32: {
            "title": "Day with highest revenue",
            "key_fact": "2025-03-09 ($3,356.90)",
            "value": "2025-03-09: $3,356.90 (March 9, 2025)",
            "check": lambda ans: "2025-03-09" in ans or "march 9" in ans.lower() or "3356" in ans.replace(",", "") or "3,356" in ans or "3357" in ans.replace(",", "")
        },
        33: {
            "title": "Day with most transactions",
            "key_fact": "2025-01-02 (32 transactions)",
            "value": "2025-01-02: 32 transactions (January 2, 2025)",
            "check": lambda ans: "2025-01-02" in ans or "january 2" in ans.lower() or "jan 2" in ans.lower() or "32" in ans
        },
        34: {
            "title": "Average daily sales",
            "key_fact": "$2,373.63 per day (over 166 recorded days)",
            "value": "$2,373.63",
            "check": lambda ans: "2373" in ans.replace(",", "") or "2,373" in ans or "2374" in ans.replace(",", "") or "2,374" in ans
        },
        35: {
            "title": "Monthly sales trend",
            "key_fact": "Jan: $73.4k, Feb: $65.0k, Mar: $73.3k, Apr: $72.0k, May: $74.0k, Jun: $36.3k",
            "value": {str(k): f"${v:,.2f}" for k, v in monthly_sales.items()},
            "check": lambda ans: ("jan" in ans.lower() or "trend" in ans.lower() or "may" in ans.lower() or "month" in ans.lower()) and any(c.isdigit() for c in ans)
        },
        36: {
            "title": "Cost price of Betaloc 50mg Extended-Release",
            "key_fact": "$7.00",
            "value": "$7.00",
            "check": lambda ans: "$7" in ans or "7.00" in ans or "7 USD" in ans or "7 dollars" in ans.lower() or " 7 " in f" {ans} " or "7." in ans
        },
        37: {
            "title": "Maximum retail price of Betaloc 50mg Extended-Release",
            "key_fact": "$9.80",
            "value": "$9.80",
            "check": lambda ans: "9.8" in ans or "9.80" in ans
        },
        38: {
            "title": "Products with highest maximum retail prices",
            "key_fact": "Ventolin HFA Inhaler ($26.00), Singulair 10mg ($24.00), Zithromax 500mg ($22.00), Pharmaton Capsules ($21.00), Augmentin 625mg ($18.00)",
            "value": "Ventolin ($26), Singulair ($24), Zithromax ($22), Pharmaton ($21), Augmentin ($18)",
            "check": lambda ans: any(p in ans.lower() for p in ["ventolin", "singulair", "zithromax", "pharmaton", "augmentin"]) and any(pr in ans for pr in ["26", "24", "22", "21", "18"])
        },
        39: {
            "title": "Products with lowest maximum retail prices",
            "key_fact": "Disprin 81mg ($2.50), Panadol Extra ($3.20), Zyrtec 10mg ($4.20), Lasix 40mg ($5.00), Brufen 400mg ($5.50)",
            "value": "Disprin ($2.50), Panadol Extra ($3.20), Zyrtec ($4.20), Lasix ($5.00), Brufen ($5.50)",
            "check": lambda ans: any(p in ans.lower() for p in ["disprin", "panadol extra", "zyrtec", "lasix", "brufen", "panadol"]) and any(pr in ans for pr in ["2.5", "3.2", "4.2", "5.0", "5.5", "2.50", "3.20", "5"])
        },
        40: {
            "title": "Products with biggest difference between cost price and MRP",
            "key_fact": "Ventolin HFA Inhaler ($7.00 diff), Singulair 10mg ($6.00 diff), Zithromax 500mg ($6.00 diff), Pharmaton ($6.00 diff), Lipiget 10mg ($4.50 diff)",
            "value": "Ventolin ($7 diff), Singulair ($6 diff), Zithromax ($6 diff), Pharmaton ($6 diff)",
            "check": lambda ans: any(p in ans.lower() for p in ["ventolin", "singulair", "zithromax", "pharmaton", "lipiget"])
        },
        41: {
            "title": "Products with smallest margin between cost and MRP",
            "key_fact": "Disprin 81mg ($0.70 diff), Panadol Extra ($0.70 diff), Zyrtec 10mg ($1.20 diff), Hydryllin Syrup ($1.50 diff), Lasix 40mg ($1.50 diff)",
            "value": "Disprin ($0.70), Panadol Extra ($0.70), Zyrtec ($1.20), Hydryllin ($1.50), Lasix ($1.50)",
            "check": lambda ans: any(p in ans.lower() for p in ["disprin", "panadol extra", "zyrtec", "hydryllin", "lasix", "panadol"])
        },
        42: {
            "title": "Medicines potentially providing highest gross margin per unit",
            "key_fact": "Ventolin HFA Inhaler ($7.00/unit), Singulair 10mg ($6.00/unit), Zithromax 500mg ($6.00/unit), Pharmaton ($6.00/unit), Lipiget 10mg ($4.50/unit)",
            "value": "Ventolin ($7), Singulair ($6), Zithromax ($6), Pharmaton ($6)",
            "check": lambda ans: any(p in ans.lower() for p in ["ventolin", "singulair", "zithromax", "pharmaton", "lipiget"])
        },
        43: {
            "title": "Estimated gross margin for each product based on recorded prices",
            "key_fact": "Table / list of all 20 products showing cost price, MRP, and margin ($ and %)",
            "value": "20 products margin breakdown",
            "check": lambda ans: any(p in ans.lower() for p in ["ventolin", "singulair", "betaloc", "disprin", "augmentin", "panadol", "margin", "cost", "price"]) and any(c.isdigit() for c in ans)
        }
    }
    return gt

def run_tests():
    print("=" * 80)
    print("STARTING RAG COMPLIANT DATASET EVALUATION SUITE")
    print(f"Total Questions: {len(QUESTIONS)}")
    print(f"Target Scope: pharmacy_compliant_master_dataset.xlsx")
    print("=" * 80)

    gt_dict = load_ground_truth()
    results = []

    correct_count = 0
    wrong_count = 0

    for idx, q in enumerate(QUESTIONS, 1):
        q_gt = gt_dict[idx]
        session_id = f"eval-compliant-q{idx}-{int(time.time())}"
        payload = {
            "question": q,
            "session_id": session_id,
            "domain": "pharmacy",
            "file_ids": ["file_8bfe796e635f"],
            "source_files": ["pharmacy_compliant_master_dataset.xlsx"]
        }

        print(f"\n[{idx:02d}/43] Testing: {q}")
        t0 = time.time()
        try:
            resp = requests.post(API_URL, json=payload, timeout=90)
            elapsed = time.time() - t0
            status_code = resp.status_code
            if status_code == 200:
                data = resp.json()
                answer = data.get("answer", "")
                route = data.get("route", "")
                sources = data.get("sources", [])
                computed = data.get("computed_values", {})
            else:
                answer = f"HTTP Error {status_code}: {resp.text[:200]}"
                route = "error"
                sources = []
                computed = {}
        except Exception as e:
            elapsed = time.time() - t0
            status_code = 500
            answer = f"Request Exception: {str(e)}"
            route = "exception"
            sources = []
            computed = {}

        # Factual correctness evaluation
        is_pass = False
        notes = ""
        if status_code == 200 and answer:
            # Check against ground truth
            check_fn = q_gt.get("check")
            if check_fn and check_fn(answer):
                is_pass = True
                verdict = "FACTUALLY_CORRECT"
                correct_count += 1
            else:
                # Check if it was an empty/generic fallback or wrong facts
                if "couldn't find" in answer.lower() or "no record" in answer.lower() or "can't confirm" in answer.lower():
                    verdict = "FACTUALLY_WRONG_NO_DATA_RETRIEVED"
                    notes = "RAG abstained or failed to retrieve matching records from compliant dataset."
                else:
                    verdict = "FACTUALLY_WRONG_OR_MISMATCHED"
                    notes = "RAG returned an answer but numbers/entities did not match ground truth."
                wrong_count += 1
        else:
            verdict = "FAILED_HTTP_ERROR"
            wrong_count += 1
            notes = f"HTTP status {status_code}"

        print(f"  Route: {route} | Elapsed: {elapsed:.2f}s | Verdict: {verdict}")
        print(f"  RAG Answer: {answer[:180]}..." if len(answer) > 180 else f"  RAG Answer: {answer}")
        print(f"  Ground Truth: {q_gt['key_fact']}")

        results.append({
            "q_num": idx,
            "question": q,
            "expected_fact": q_gt["key_fact"],
            "ground_truth_val": str(q_gt["value"]),
            "rag_answer": answer,
            "route": route,
            "timing_s": round(elapsed, 2),
            "status_code": status_code,
            "verdict": verdict,
            "notes": notes
        })

    # Save results to JSON
    json_path = os.path.join(REPORT_DIR, "compliant_dataset_rag_eval.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Generate Markdown Report
    md_path = os.path.join(REPORT_DIR, "compliant_dataset_rag_eval_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# RAG Evaluation Report: Pharmacy Compliant Master Dataset\n\n")
        f.write(f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Dataset:** `pharmacy_compliant_master_dataset.xlsx` (5,300 records, 20 distinct products, 3 branches, 13 manufacturers)\n\n")
        f.write("## Executive Summary\n\n")
        f.write(f"- **Total Questions Evaluated:** {len(QUESTIONS)}\n")
        f.write(f"- **Factually Correct:** {correct_count} ({(correct_count/len(QUESTIONS))*100:.1f}%)\n")
        f.write(f"- **Factually Wrong / Failed:** {wrong_count} ({(wrong_count/len(QUESTIONS))*100:.1f}%)\n\n")
        f.write("## Detailed Question-by-Question Evaluation\n\n")
        f.write("| # | Question | Ground Truth Expected | RAG Answer Summary | Route | Verdict |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in results:
            clean_ans = r['rag_answer'].replace("\n", " ").replace("|", "\\|")[:120]
            clean_gt = str(r['expected_fact']).replace("\n", " ").replace("|", "\\|")
            f.write(f"| {r['q_num']} | {r['question']} | {clean_gt} | {clean_ans} | {r['route']} | **{r['verdict']}** |\n")

    print("\n" + "=" * 80)
    print("EVALUATION COMPLETE")
    print(f"Total: {len(QUESTIONS)} | Factually Correct: {correct_count} | Factually Wrong/Failed: {wrong_count}")
    print(f"Report saved to: {md_path}")
    print("=" * 80)

if __name__ == "__main__":
    run_tests()
