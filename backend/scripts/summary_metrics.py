import json
import pandas as pd
import numpy as np

with open('reports/evaluation_100_questions_results.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

df = pd.DataFrame(results)

def get_category(qid):
    if 1 <= qid <= 10: return '1. Product & General Lookup (Q1-Q10)'
    elif 11 <= qid <= 25: return '2. Price & Discount (Q11-Q25)'
    elif 26 <= qid <= 35: return '3. Availability (Q26-Q35)'
    elif 36 <= qid <= 50: return '4. Company Analysis (Q36-Q50)'
    elif 51 <= qid <= 60: return '5. Pack Sizes (Q51-Q60)'
    elif 61 <= qid <= 75: return '6. Dataset Analytics (Q61-Q75)'
    elif 76 <= qid <= 90: return '7. Pharmacy-Owner Questions (Q76-Q90)'
    else: return '8. Decision Support & Advanced (Q91-Q100)'

df['category'] = df['qid'].apply(get_category)
df['verdict'] = df['evaluation'].apply(lambda x: x.get('verdict', 'Unknown'))
df['score'] = df['evaluation'].apply(lambda x: x.get('score', 0.0))
df['reason'] = df['evaluation'].apply(lambda x: x.get('reason', ''))

print("=== OVERALL METRICS ===")
total = len(df)
accurate = sum(df['verdict'].isin(['Accurate', 'Appropriately Handled']))
partial = sum(df['verdict'] == 'Partially Accurate')
inaccurate = sum(df['verdict'] == 'Inaccurate')
print(f"Total: {total}, Accurate: {accurate} ({accurate/total*100:.1f}%), Partial: {partial} ({partial/total*100:.1f}%), Inaccurate: {inaccurate} ({inaccurate/total*100:.1f}%)")
print(f"Overall Accuracy: {df['score'].mean()*100:.1f}%\n")

print("=== CATEGORY BREAKDOWN ===")
cat_summary = []
for cat, grp in df.groupby('category'):
    c_tot = len(grp)
    c_acc = sum(grp['verdict'].isin(['Accurate', 'Appropriately Handled']))
    c_part = sum(grp['verdict'] == 'Partially Accurate')
    c_inacc = sum(grp['verdict'] == 'Inaccurate')
    c_score = grp['score'].mean() * 100
    cat_summary.append({
        "Category": cat,
        "Total": c_tot,
        "Accurate": c_acc,
        "Partial": c_part,
        "Inaccurate": c_inacc,
        "AccuracyRate": f"{c_score:.1f}%"
    })
    print(f"{cat:40s} | Acc: {c_acc:2d}/{c_tot:2d} ({c_score:5.1f}%) | Part: {c_part:2d} | Inacc: {c_inacc:2d}")

print("\n=== ROUTING BREAKDOWN ===")
for r, grp in df.groupby('route'):
    print(f"Route: {str(r):15s} | Count: {len(grp):2d} | Accuracy: {grp['score'].mean()*100:.1f}%")

print("\n=== INACCURATE QUESTIONS SUMMARY ===")
for idx, row in df[df['verdict'] == 'Inaccurate'].iterrows():
    print(f"Q{row['qid']:03d}: '{row['question']}' -> Route: {row.get('route')} | Reason: {row['reason']}")
