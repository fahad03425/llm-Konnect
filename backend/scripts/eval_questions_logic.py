import os
import sys
import json
import pandas as pd

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.rag.router import classify_route, extract_filters, AnalyticsRouter, RouteType
from app.analytics.seam import select_kpi_keys, compute_for_question
from app.schema.domain import get_domain_pack
import app.schema # register domain packs

csv_path = os.path.abspath(os.path.join(backend_dir, "..", "data", "uploads", "test_pharmacy_small.csv"))
df = pd.read_csv(csv_path)
df["source_row"] = df.index + 2 # row numbering in CSV

print(f"Loaded {len(df)} rows from {csv_path}")
print("Columns:", list(df.columns))

questions = [
    # 1. Compound Aggregates & Averages
    ("Compound Aggregates", "What was the average transaction value in February 2026?"),
    ("Compound Aggregates", "What is our total revenue and average transaction value across all records?"),
    
    # 2. Category & Product Breakdowns
    ("Breakdowns", "Show me the revenue breakdown by product."),
    ("Breakdowns", "Which supplier generated the highest revenue?"),
    ("Breakdowns", "Give me the revenue breakdown by month."),
    
    # 3. Multi-Condition Time Window
    ("Time Window", "How much revenue did we make in January 2026 vs February 2026?"),
    ("Time Window", "What was the total sales between 1st February and 28th February?"),
    
    # 4. Expiry Risk Analysis
    ("Expiry Risk", "Which medicines are already expired or expiring within the next 60 days?"),
    ("Expiry Risk", "What is the total monetary value of products expiring soon?"),
    ("Expiry Risk", "List all batches of medicines that expire in 2025 or 2026."),
    
    # 5. Schedule / Controlled Substance & Rx Tracking
    ("Schedule Tracking", "How many transactions or units were sold for scheduled / prescription medicines?"),
    ("Schedule Tracking", "Which medicines have a schedule flag or require a prescription?")
]

router = AnalyticsRouter()
records = df.to_dict(orient="records")

for cat, q in questions:
    print("\n" + "="*80)
    print(f"[{cat}] Q: {q}")
    route = classify_route(q)
    filters = extract_filters(q, domain="pharmacy")
    kpi_keys = select_kpi_keys(q, domain="pharmacy")
    print(f"Route: {route} | Filters: {filters} | Selected KPI Keys: {kpi_keys}")
    
    if route == RouteType.ANALYTICS:
        comp, source_rows = router.compute(q, filters, records, domain="pharmacy")
        print(f"Computed Metrics ({len(comp)}):")
        for k, v in comp.items():
            val = v.get('value')
            status = v.get('status')
            unit = v.get('unit')
            print(f"  - {k} ({status}): {val} {unit}")
        print(f"Source rows used: {len(source_rows)}")
