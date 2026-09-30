import urllib.request
import json
import time
import pandas as pd
import numpy as np
import os

# Load dataset for ground truth calculations
DATASET_PATH = "C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/data/uploads/pharmacy_single_store_data.xlsx"
df = pd.read_excel(DATASET_PATH)

FILE_ID = "file_dcd43c20fa81"
SOURCE_FILE = "pharmacy_single_store_data.xlsx"
API_URL = "http://127.0.0.1:8756/api/chat"

# Precompute ground truth values
sales_df = df[df['TransactionType'] == 'Sale']
purchases_df = df[df['TransactionType'] == 'Purchase']
completed_sales = sales_df[sales_df['Status'] == 'Completed']

questions_data = [
    # 1-15: General Analytics & Financial Metrics
    {
        "id": 1,
        "category": "General Analytics",
        "question": "What is the total revenue from completed sales in the dataset?",
        "ground_truth": f"PKR {completed_sales['Amount'].sum():,.2f} (or Total Sales Amount across all sales: PKR {sales_df['Amount'].sum():,.2f})",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 2,
        "category": "General Analytics",
        "question": "What is the total purchase amount across all purchase transactions?",
        "ground_truth": f"PKR {purchases_df['Amount'].sum():,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 3,
        "category": "General Analytics",
        "question": "How many total units of products were sold?",
        "ground_truth": f"{sales_df['Quantity'].sum():,} units (or {completed_sales['Quantity'].sum():,} for completed sales)",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 4,
        "category": "General Analytics",
        "question": "What is the total number of transactions in pharmacy_single_store_data.xlsx?",
        "ground_truth": f"22,800 total transactions (20,300 Sales, 2,500 Purchases)",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 5,
        "category": "General Analytics",
        "question": "What is the total count of completed sales vs returned sales?",
        "ground_truth": f"Completed Sales: {len(sales_df[sales_df['Status']=='Completed']):,}, Returned Sales: {len(sales_df[sales_df['Status']=='Returned']):,}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 6,
        "category": "General Analytics",
        "question": "What is the top-selling product by revenue?",
        "ground_truth": f"Blood Pressure Monitor (PKR {sales_df.groupby('ProductName')['Amount'].sum().nlargest(1).values[0]:,.2f})",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 7,
        "category": "General Analytics",
        "question": "What is the top-selling product by quantity sold?",
        "ground_truth": f"Ciplox Eye Drops ({sales_df.groupby('ProductName')['Quantity'].sum().nlargest(1).values[0]:,} units)",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 8,
        "category": "General Analytics",
        "question": "What is the lowest selling product by revenue?",
        "ground_truth": f"Disprin Tablet (PKR {sales_df.groupby('ProductName')['Amount'].sum().nsmallest(1).values[0]:,.2f})",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 9,
        "category": "General Analytics",
        "question": "What is the total discount amount given on sales?",
        "ground_truth": f"PKR {sales_df['DiscountAmount'].sum():,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 10,
        "category": "General Analytics",
        "question": "Which cashier processed the most transactions?",
        "ground_truth": f"Sana Malik ({df['Cashier'].value_counts().index[0]} with {df['Cashier'].value_counts().values[0]:,} transactions)",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 11,
        "category": "General Analytics",
        "question": "How many transactions were processed by cashier Sana Malik?",
        "ground_truth": f"{df[df['Cashier']=='Sana Malik'].shape[0]:,} transactions",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 12,
        "category": "General Analytics",
        "question": "What is the most frequently used payment method?",
        "ground_truth": f"Cash ({df['PaymentMethod'].value_counts().index[0]} with {df['PaymentMethod'].value_counts().values[0]:,} transactions)",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 13,
        "category": "General Analytics",
        "question": "What is the total sales revenue generated from the 'Cardiac' category?",
        "ground_truth": f"PKR {sales_df[sales_df['Category']=='Cardiac']['Amount'].sum():,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 14,
        "category": "General Analytics",
        "question": "What is the total revenue generated from 'Blood Pressure Monitor'?",
        "ground_truth": f"PKR {sales_df[sales_df['ProductName']=='Blood Pressure Monitor']['Amount'].sum():,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 15,
        "category": "General Analytics",
        "question": "How many transactions have a 'Pending' status?",
        "ground_truth": f"{df[df['Status']=='Pending'].shape[0]:,} transactions",
        "is_answerable": True,
        "unanswerable_reason": None
    },

    # 16-27: Category, Supplier & Inventory Analytics
    {
        "id": 16,
        "category": "Category & Supplier Analytics",
        "question": "How many distinct product categories are in the dataset?",
        "ground_truth": f"{df['Category'].nunique()} categories",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 17,
        "category": "Category & Supplier Analytics",
        "question": "How many transactions belong to the 'Antibiotic' category?",
        "ground_truth": f"{df[df['Category']=='Antibiotic'].shape[0]:,} transactions",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 18,
        "category": "Category & Supplier Analytics",
        "question": "Who is the top supplier by total purchase value?",
        "ground_truth": f"Habib Pharma Traders (PKR {purchases_df.groupby('Supplier')['Amount'].sum().nlargest(1).values[0]:,.2f})",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 19,
        "category": "Category & Supplier Analytics",
        "question": "What is the total purchase amount spent with supplier 'Habib Pharma Traders'?",
        "ground_truth": f"PKR {purchases_df[purchases_df['Supplier']=='Habib Pharma Traders']['Amount'].sum():,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 20,
        "category": "Category & Supplier Analytics",
        "question": "What is the total sales amount in 2024?",
        "ground_truth": f"PKR {sales_df[sales_df['Date'].astype(str).str.startswith('2024')]['Amount'].sum():,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 21,
        "category": "Category & Supplier Analytics",
        "question": "What is the unit price of 'Insulin Glargine (Lantus)'?",
        "ground_truth": f"PKR {df[df['ProductName'].str.contains('Insulin Glargine', na=False)]['UnitPrice'].iloc[0]:,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 22,
        "category": "Category & Supplier Analytics",
        "question": "What is the unit price of 'Augmentin 625mg'?",
        "ground_truth": f"PKR {df[df['ProductName'].str.contains('Augmentin', na=False)]['UnitPrice'].iloc[0]:,.2f}",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 23,
        "category": "Category & Supplier Analytics",
        "question": "What is the average transaction amount for sales?",
        "ground_truth": f"PKR {sales_df['Amount'].mean():,.2f} across all sales (PKR {completed_sales['Amount'].mean():,.2f} for completed)",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 24,
        "category": "Category & Supplier Analytics",
        "question": "How many transactions were paid using 'Credit Card'?",
        "ground_truth": f"{df[df['PaymentMethod']=='Credit Card'].shape[0]:,} transactions",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 25,
        "category": "Category & Supplier Analytics",
        "question": "How many transactions were paid using 'Mobile Wallet (EasyPaisa)'?",
        "ground_truth": f"{df[df['PaymentMethod']=='Mobile Wallet (EasyPaisa)'].shape[0]:,} transactions",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 26,
        "category": "Category & Supplier Analytics",
        "question": "How many transactions are recorded for 'Disprin Tablet'?",
        "ground_truth": f"{df[df['ProductName']=='Disprin Tablet'].shape[0]:,} transactions",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 27,
        "category": "Category & Supplier Analytics",
        "question": "What is the total quantity of 'Ciplox Eye Drops' sold?",
        "ground_truth": f"{sales_df[sales_df['ProductName']=='Ciplox Eye Drops']['Quantity'].sum():,} units",
        "is_answerable": True,
        "unanswerable_reason": None
    },

    # 28-40: RAG / Specific Record-Level Lookups
    {
        "id": 28,
        "category": "RAG Record Lookup",
        "question": "Look up transaction SALE0004749 and provide its product, quantity, amount, cashier, and payment method.",
        "ground_truth": "Product: Buscopan Tablet, Quantity: 2, Amount: PKR 32.0, Cashier: Ayesha Khan, Payment: Credit Card, Status: Completed",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 29,
        "category": "RAG Record Lookup",
        "question": "What are the details of transaction PUR001991 including supplier, product, amount, and status?",
        "ground_truth": "Supplier: National Medical Store, Product: Baby Diapers (Pack of 10), Quantity: 247, UnitPrice: 350.0, Amount: PKR 86,450.0, Status: Pending, Payment: Cheque",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 30,
        "category": "RAG Record Lookup",
        "question": "What product and quantity were sold in transaction SALE0006227?",
        "ground_truth": "Product: Lipitor 20mg Tablet, Quantity: 8, Amount: PKR 520.0, Cashier: Hina Rashid",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 31,
        "category": "RAG Record Lookup",
        "question": "Who is the manufacturer of 'Lipitor 20mg Tablet'?",
        "ground_truth": "Pfizer",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 32,
        "category": "RAG Record Lookup",
        "question": "Which category does 'Buscopan Tablet' belong to?",
        "ground_truth": "Antispasmodic",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 33,
        "category": "RAG Record Lookup",
        "question": "Who is the manufacturer of 'Baby Diapers (Pack of 10)'?",
        "ground_truth": "P&G",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 34,
        "category": "RAG Record Lookup",
        "question": "What payment method was used for purchase transaction PUR001991?",
        "ground_truth": "Cheque",
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 35,
        "category": "RAG Record Lookup",
        "question": "Look up transaction SALE0000001 and provide its details.",
        "ground_truth": str(df[df['TransactionID']=='SALE0000001'].to_dict(orient='records')[0] if len(df[df['TransactionID']=='SALE0000001'])>0 else "Not found"),
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 36,
        "category": "RAG Record Lookup",
        "question": "Look up transaction SALE0000010 and provide its date, product name, and total amount.",
        "ground_truth": str(df[df['TransactionID']=='SALE0000010'].to_dict(orient='records')[0] if len(df[df['TransactionID']=='SALE0000010'])>0 else "Not found"),
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 37,
        "category": "RAG Record Lookup",
        "question": "What cashier handled transaction SALE0000025 and what was the payment method?",
        "ground_truth": str(df[df['TransactionID']=='SALE0000025'].to_dict(orient='records')[0] if len(df[df['TransactionID']=='SALE0000025'])>0 else "Not found"),
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 38,
        "category": "RAG Record Lookup",
        "question": "What is the unit price and discount percentage for transaction SALE0000050?",
        "ground_truth": str(df[df['TransactionID']=='SALE0000050'].to_dict(orient='records')[0] if len(df[df['TransactionID']=='SALE0000050'])>0 else "Not found"),
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 39,
        "category": "RAG Record Lookup",
        "question": "What is the status of purchase transaction PUR000005?",
        "ground_truth": str(df[df['TransactionID']=='PUR000005'].to_dict(orient='records')[0] if len(df[df['TransactionID']=='PUR000005'])>0 else "Not found"),
        "is_answerable": True,
        "unanswerable_reason": None
    },
    {
        "id": 40,
        "category": "RAG Record Lookup",
        "question": "What supplier is associated with purchase transaction PUR000001?",
        "ground_truth": str(df[df['TransactionID']=='PUR000001'].to_dict(orient='records')[0] if len(df[df['TransactionID']=='PUR000001'])>0 else "Not found"),
        "is_answerable": True,
        "unanswerable_reason": None
    },

    # 41-50: Edge Cases & Unanswerable / Out-of-Scope Questions
    {
        "id": 41,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What is the customer's age, gender, and CNIC number for transaction SALE0004749?",
        "ground_truth": "Unanswerable: The POS transactions schema does not record customer demographic data (Age, Gender, CNIC).",
        "is_answerable": False,
        "unanswerable_reason": "Customer demographic fields (Age, Gender, CNIC) are not present in the pharmacy dataset schema."
    },
    {
        "id": 42,
        "category": "Unanswerable / Out-of-Scope",
        "question": "Can you provide the scanned doctor's prescription image or PDF for Augmentin?",
        "ground_truth": "Unanswerable: The dataset only contains tabular transaction text; scanned images, binary attachments, and doctor prescription files are not stored.",
        "is_answerable": False,
        "unanswerable_reason": "No prescription image attachments or document blobs exist in this tabular Excel dataset."
    },
    {
        "id": 43,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What will be the projected sales revenue for November 2030 in this store?",
        "ground_truth": "Unanswerable: Historical dataset only covers transactions up to September 2026. Future sales for 2030 cannot be factually answered without speculative hallucination.",
        "is_answerable": False,
        "unanswerable_reason": "The temporal range of the dataset is 2024-01-01 to 2026-09-25; future dates in 2030 exceed available data."
    },
    {
        "id": 44,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What is the expiry date and batch number of Panadol 500mg in the current inventory?",
        "ground_truth": "Unanswerable: The dataset columns do not include batch numbers or expiry dates (columns: TransactionType, TransactionID, Date, Time, ProductID, ProductName, Category, Manufacturer, Supplier, Quantity, UnitPrice, DiscountPct, DiscountAmount, Amount, Cashier, PaymentMethod, Status).",
        "is_answerable": False,
        "unanswerable_reason": "Expiry dates and batch numbers are not tracked in the 17 schema columns of pharmacy_single_store_data.xlsx."
    },
    {
        "id": 45,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What are the GPS coordinates and warehouse street address of Habib Pharma Traders?",
        "ground_truth": "Unanswerable: Supplier physical addresses and GPS coordinates are not recorded in this dataset.",
        "is_answerable": False,
        "unanswerable_reason": "Geographic locations, street addresses, and GPS coordinates are not present in the dataset."
    },
    {
        "id": 46,
        "category": "Unanswerable / Out-of-Scope",
        "question": "Which branch in Lahore had the highest foot traffic compared to this store?",
        "ground_truth": "Unanswerable: This dataset represents a single store (`pharmacy_single_store_data.xlsx`) and does not contain multi-branch comparison or foot traffic data.",
        "is_answerable": False,
        "unanswerable_reason": "The dataset is scoped to a single pharmacy store and does not contain multi-branch foot traffic telemetry."
    },
    {
        "id": 47,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What is the customer's home delivery address and mobile phone number for SALE0006227?",
        "ground_truth": "Unanswerable: Customer PII (phone number, home address) is not stored in the POS sales schema.",
        "is_answerable": False,
        "unanswerable_reason": "Customer contact information and home delivery addresses are omitted from the transaction records."
    },
    {
        "id": 48,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What is the monthly salary, bank account number, and tax bracket for cashier Sana Malik?",
        "ground_truth": "Unanswerable: Employee HR/payroll and compensation records are not part of the POS transaction log.",
        "is_answerable": False,
        "unanswerable_reason": "HR/Payroll data (salaries, bank details, tax brackets) is external to retail POS transaction data."
    },
    {
        "id": 49,
        "category": "Unanswerable / Out-of-Scope",
        "question": "What was the weather condition and temperature during transaction SALE0004749?",
        "ground_truth": "Unanswerable: Meteorological data is not captured in the pharmacy POS records.",
        "is_answerable": False,
        "unanswerable_reason": "Weather and ambient temperature data are not collected in point-of-sale logs."
    },
    {
        "id": 50,
        "category": "Unanswerable / Out-of-Scope",
        "question": "Look up product ID 'MED99999_NON_EXISTENT' and show its current price and stock.",
        "ground_truth": "Unanswerable / Not Found: Product ID 'MED99999_NON_EXISTENT' does not exist in the dataset (50 unique products, MED0001 to MED0050).",
        "is_answerable": False,
        "unanswerable_reason": "Product ID 'MED99999_NON_EXISTENT' is fictitious and does not exist in the dataset."
    }
]

print(f"Loaded {len(questions_data)} test questions. Starting API execution...")

results = []

for idx, q_item in enumerate(questions_data, start=1):
    session_id = f"eval_run_50_{q_item['id']}"
    payload = {
        "question": q_item["question"],
        "session_id": session_id,
        "domain": "pharmacy",
        "file_ids": [FILE_ID],
        "source_files": [SOURCE_FILE]
    }
    
    t_start = time.time()
    try:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - t_start
            
            answer = resp_data.get("answer", "")
            route = resp_data.get("route", "")
            timing_reported = resp_data.get("timing", elapsed)
            sources_count = len(resp_data.get("sources", []))
            
            result_entry = {
                "id": q_item["id"],
                "category": q_item["category"],
                "question": q_item["question"],
                "is_answerable": q_item["is_answerable"],
                "unanswerable_reason": q_item["unanswerable_reason"],
                "ground_truth": q_item["ground_truth"],
                "system_answer": answer,
                "route": route,
                "sources_count": sources_count,
                "latency_seconds": round(elapsed, 3),
                "backend_timing_seconds": round(timing_reported, 3),
                "status": "success"
            }
    except Exception as e:
        elapsed = time.time() - t_start
        result_entry = {
            "id": q_item["id"],
            "category": q_item["category"],
            "question": q_item["question"],
            "is_answerable": q_item["is_answerable"],
            "unanswerable_reason": q_item["unanswerable_reason"],
            "ground_truth": q_item["ground_truth"],
            "system_answer": f"ERROR: {str(e)}",
            "route": "error",
            "sources_count": 0,
            "latency_seconds": round(elapsed, 3),
            "backend_timing_seconds": round(elapsed, 3),
            "status": "error"
        }
    
    results.append(result_entry)
    print(f"[{idx}/50] (ID {q_item['id']}) Latency: {elapsed:.2f}s | Route: {result_entry.get('route')} | Q: {q_item['question'][:40]}...")

# Save raw results
output_file = "C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/backend/evaluation_50_results.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"Successfully evaluated 50 questions and saved to {output_file}!")
