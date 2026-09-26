"""
LLaMA 3.2:3b - Pharmacy POS RAG Chatbot Accuracy Benchmark
30 English + 30 Roman Urdu Questions
Scope: PharmacyPOS Database
Focus: Accuracy of replies (not speed)
"""
import sys, os, time, json, re
backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from fastapi.testclient import TestClient
from app.main import app
from app.core.llm import llm

PHARMACY_POS_FILE_IDS = [
    "db_pharmacypos_tbl_products", "db_pharmacypos_tbl_salesheader",
    "db_pharmacypos_tbl_salesdetails", "db_pharmacypos_tbl_purchaseheader",
    "db_pharmacypos_tbl_purchasedetails", "db_pharmacypos_tbl_suppliers",
    "db_pharmacypos_tbl_customers", "db_pharmacypos_tbl_doctors",
    "db_pharmacypos_tbl_batches", "db_pharmacypos_tbl_config",
    "db_pharmacypos_transactions", "db_pharmacypos_products",
    "db_pharmacypos_customers",
]

CANNOT_ANSWER = [
    r"i (?:cannot|can't|couldn't|can not) (?:answer|find|determine|provide|locate)",
    r"no records? found", r"not (?:able|enough|available|found)",
    r"(?:unfortunately|sorry),?\s*i (?:don't|do not) have",
    r"i do(?:n't| not) have (?:enough|any|the) (?:data|information|records|context)",
    r"couldn't find anything", r"data (?:is )?not (?:available|present|found)",
    r"no (?:data|information|records) (?:available|found|present)",
    r"mujhe.*(?:maloomat|data|record).*nahi mil",
    r"koi record nahi mila",
    r"(?:filhaal|abhi).*(?:nahi|nahin).*(?:mil|dastyab|mojood)",
]

def classify(answer, expected_kw, question):
    a = answer.lower().strip()
    for p in CANNOT_ANSWER:
        if re.search(p, a):
            return "NEUTRAL"
    if expected_kw:
        for kw in expected_kw:
            if kw.lower() in a:
                return "PASS"
    if len(a) < 15 or a.startswith("error:"):
        return "FAIL"
    if not expected_kw:
        return "PASS"
    if len(a) > 50:
        return "PASS"
    return "FAIL"

ENGLISH_QS = [
    ("EN-01","Data Discovery","What data sources are you currently answering from?",["pharmacypos","data source","table","database"]),
    ("EN-02","Data Discovery","What tables are available in the PharmacyPOS database?",["products","sales","purchase","suppliers","customers","batches"]),
    ("EN-03","Data Discovery","How many products are in the product catalog?",["product","total","count"]),
    ("EN-04","Product Lookup","List the medicine names available in the products table",["medicine","product","name","tablet","capsule","syrup"]),
    ("EN-05","Product Lookup","Which product has the highest MRP price?",["price","mrp","highest","product"]),
    ("EN-06","Product Lookup","Show me all products that are antibiotics or related to infection treatment",["antibiotic","infection","product"]),
    ("EN-07","Batch & Expiry","Which medicine batches are expiring within the next 60 days?",["batch","expir","day"]),
    ("EN-08","Batch & Expiry","Show me all expired batches that are still in stock",["expire","batch","stock"]),
    ("EN-09","Sales Analytics","What is the total sales amount in the database?",["total","sales","amount","pkr","rs"]),
    ("EN-10","Sales Analytics","Which medicine has the highest sales quantity?",["highest","sale","quantity","product","medicine"]),
    ("EN-11","Sales Analytics","How many sales transactions are recorded in the system?",["transaction","sale","record","total","count"]),
    ("EN-12","Sales Analytics","Show me the top 5 best-selling products by revenue",["top","best","sell","product","revenue"]),
    ("EN-13","Sales Detail","What is the average discount percentage given on sales?",["average","discount","percent"]),
    ("EN-14","Purchase Analytics","What is the total purchase amount from all suppliers?",["total","purchase","amount","supplier"]),
    ("EN-15","Purchase Analytics","Which supplier do we buy the most medicines from?",["supplier","most","buy","purchase"]),
    ("EN-16","Purchase Detail","List all purchase invoices from the last month",["purchase","invoice"]),
    ("EN-17","Supplier Lookup","How many suppliers are registered in the system?",["supplier","total","count","registered"]),
    ("EN-18","Supplier Lookup","List the names of all suppliers in the database",["supplier","name","list"]),
    ("EN-19","Customer Data","How many customers are registered in the pharmacy system?",["customer","total","count","registered"]),
    ("EN-20","Customer Data","Which customer has made the most purchases?",["customer","most","purchase"]),
    ("EN-21","Doctor Data","How many doctors are in the system?",["doctor","total","count"]),
    ("EN-22","Doctor Data","List the names of all doctors registered in the database",["doctor","name"]),
    ("EN-23","Business Intelligence","What is the profit margin on the most sold product?",["profit","margin","product"]),
    ("EN-24","Business Intelligence","Which products have a very low stock and need reordering?",["stock","low","reorder","product"]),
    ("EN-25","Business Intelligence","What is the total revenue vs total cost of goods purchased?",["revenue","cost","total","purchase"]),
    ("EN-26","Edge Case","Are there any duplicate invoices in the sales records?",["duplicate","invoice","sale"]),
    ("EN-27","Edge Case","What is the most expensive medicine we have ever purchased?",["expensive","medicine","purchase","price"]),
    ("EN-28","Edge Case","Can you forecast next month sales based on current trends?",["forecast","sale","month","trend","predict"]),
    ("EN-29","Edge Case","Show me sales transactions where quantity sold is unusually high",["sale","quantity","high","unusual","transaction"]),
    ("EN-30","Edge Case","Which payment method is used the most cash card or credit?",["payment","cash","card","credit","method"]),
]

ROMAN_URDU_QS = [
    ("RU-01","Data Discovery","Aap kis data source se jawab de rahe ho?",["pharmacypos","data source","table","database"]),
    ("RU-02","Data Discovery","PharmacyPOS database mein konse tables hain?",["products","sales","purchase","supplier","customer","batches"]),
    ("RU-03","Data Discovery","Kitni medicines database mein registered hain?",["product","medicine","total","count"]),
    ("RU-04","Product Lookup","Products table mein konsi konsi dawaiyan hain?",["medicine","product","dawa","name"]),
    ("RU-05","Product Lookup","Sab se mehngi dawai konsi hai hamare paas?",["mehngi","expensive","price","product","dawai"]),
    ("RU-06","Product Lookup","Kya hamare paas koi antibiotic medicine hai stock mein?",["antibiotic","medicine","stock","product"]),
    ("RU-07","Batch & Expiry","Konsi dawaiyan aglay 60 din mein expire hone wali hain?",["expire","batch","din","day","dawai"]),
    ("RU-08","Batch & Expiry","Kya koi batch pehle se expire ho chuka hai jo abhi stock mein hai?",["expire","batch","stock"]),
    ("RU-09","Sales Analytics","Total kitni sale hui hai ab tak?",["total","sale","amount","pkr"]),
    ("RU-10","Sales Analytics","Sab se ziada konsi dawai biki hai?",["ziada","dawai","sale","product","medicine"]),
    ("RU-11","Sales Analytics","Kitni sales transactions record hain system mein?",["transaction","sale","record","total","count"]),
    ("RU-12","Sales Analytics","Top 5 sab se ziada bikne wali products batao",["top","ziada","product","sale","best"]),
    ("RU-13","Sales Detail","Average kitna discount diya gaya hai sales pe?",["average","discount","sale"]),
    ("RU-14","Purchase Analytics","Total kitna purchase kiya hai hum ne ab tak?",["total","purchase","amount"]),
    ("RU-15","Purchase Analytics","Kis supplier se sab se ziada khareedari ki hai?",["supplier","ziada","khareed","purchase"]),
    ("RU-16","Purchase Detail","Pichle mahine ki purchase invoices dikhao",["purchase","invoice","mahine"]),
    ("RU-17","Supplier Lookup","Kitne suppliers registered hain hamare system mein?",["supplier","total","count","registered"]),
    ("RU-18","Supplier Lookup","Tamam suppliers ke naam batao",["supplier","naam","name","list"]),
    ("RU-19","Customer Data","Kitne customers registered hain pharmacy system mein?",["customer","total","count","registered"]),
    ("RU-20","Customer Data","Kis customer ne sab se ziada khareedari ki hai?",["customer","ziada","khareed","purchase"]),
    ("RU-21","Doctor Data","Kitne doctors hain system mein?",["doctor","total","count"]),
    ("RU-22","Doctor Data","Tamam doctors ke naam dikhao jo registered hain",["doctor","naam","name"]),
    ("RU-23","Business Intelligence","Sab se ziada bikne wali dawai pe profit margin kitna hai?",["profit","margin","dawai","product"]),
    ("RU-24","Business Intelligence","Konsi medicines ka stock kam hai aur reorder karna chahiye?",["stock","kam","reorder","medicine"]),
    ("RU-25","Business Intelligence","Total revenue kitna hai aur total purchase cost kitna hai?",["revenue","total","purchase","cost"]),
    ("RU-26","Edge Case","Kya sales records mein koi duplicate invoice hai?",["duplicate","invoice","sale"]),
    ("RU-27","Edge Case","Sab se mehngi dawai konsi hai jo hum ne kabhi khareedi hai?",["mehngi","dawai","khareed","purchase","expensive"]),
    ("RU-28","Edge Case","Agle mahine ki sales ka andaza lagao current trend ke hisab se",["forecast","sale","month","trend","andaza"]),
    ("RU-29","Edge Case","Konsi sales transactions mein unusually ziada quantity biki hai?",["sale","quantity","ziada","unusual"]),
    ("RU-30","Edge Case","Sab se ziada konsa payment method use hota hai cash card ya credit?",["payment","cash","card","credit"]),
]

def run_benchmark():
    print("\n" + "=" * 80)
    print("  LLaMA 3.2:3b  PHARMACY POS RAG CHATBOT ACCURACY BENCHMARK")
    print("  Scope: PharmacyPOS Database | 30 English + 30 Roman Urdu")
    print("  Focus: ACCURACY (not speed)")
    print("=" * 80)

    client = TestClient(app)

    # Setup
    print("\n[Setup] Activating llama3.2:3b ...")
    res = client.post("/api/chat/models/select", json={"model": "llama3.2:3b"})
    print(f"  Active model: {res.json().get('active_model','unknown')}")

    print("[Setup] Verifying PharmacyPOS data sources ...")
    res = client.get("/api/kb/sources?domain=pharmacy")
    files = res.json().get("files", [])
    pos_tables = [f for f in files if f.get("group_name") == "PharmacyPOS"]
    print(f"  PharmacyPOS tables found: {len(pos_tables)}")
    for t in pos_tables:
        print(f"    - {t.get('table_name', t.get('filename'))} ({t.get('chunk_count', 0)} chunks)")

    all_results = []
    sc = [0]

    def ask(qid, cat, question, expected_kw, lang):
        sc[0] += 1
        payload = {
            "question": question,
            "session_id": f"bench-llama32-{qid}-{sc[0]}",
            "domain": "pharmacy",
            "file_ids": PHARMACY_POS_FILE_IDS,
        }
        t0 = time.time()
        try:
            r = client.post("/api/chat", json=payload)
            te = time.time() - t0
            if r.status_code != 200:
                return {"id":qid,"lang":lang,"category":cat,"question":question,
                        "answer":f"HTTP {r.status_code}","route":"error",
                        "sources":0,"timing":round(te,2),"verdict":"FAIL"}
            d = r.json()
            ans = d.get("answer","").strip()
            route = d.get("route","")
            srcs = len(d.get("sources",[]))
            timing = d.get("timing", round(te,2))
            v = classify(ans, expected_kw, question)
            return {"id":qid,"lang":lang,"category":cat,"question":question,
                    "answer":ans,"route":route,"sources":srcs,
                    "timing":timing,"verdict":v}
        except Exception as e:
            return {"id":qid,"lang":lang,"category":cat,"question":question,
                    "answer":f"ERROR: {e}","route":"error",
                    "sources":0,"timing":round(time.time()-t0,2),"verdict":"FAIL"}

    # English
    print("\n" + "=" * 80)
    print("  SECTION A: ENGLISH QUESTIONS (30)")
    print("=" * 80)
    for i, (qid, cat, q, kw) in enumerate(ENGLISH_QS, 1):
        print(f"\n[{qid}] {cat}")
        print(f"  Q: {q}")
        r = ask(qid, cat, q, kw, "English")
        all_results.append(r)
        print(f"  Route: {r['route']} | Time: {r['timing']}s | Sources: {r['sources']}")
        print(f"  A: {r['answer'][:150].replace(chr(10),' ')}...")
        vi = {"PASS":"PASS","FAIL":"FAIL","NEUTRAL":"NEUTRAL"}[r["verdict"]]
        print(f"  Verdict: {vi}")

    # Roman Urdu
    print("\n" + "=" * 80)
    print("  SECTION B: ROMAN URDU QUESTIONS (30)")
    print("=" * 80)
    for i, (qid, cat, q, kw) in enumerate(ROMAN_URDU_QS, 1):
        print(f"\n[{qid}] {cat}")
        print(f"  Q: {q}")
        r = ask(qid, cat, q, kw, "Roman Urdu")
        all_results.append(r)
        print(f"  Route: {r['route']} | Time: {r['timing']}s | Sources: {r['sources']}")
        print(f"  A: {r['answer'][:150].replace(chr(10),' ')}...")
        print(f"  Verdict: {r['verdict']}")

    # FINAL REPORT
    print("\n\n" + "=" * 80)
    print("  FINAL ACCURACY REPORT  LLaMA 3.2:3b on PharmacyPOS Database")
    print("=" * 80)

    def report(name, results):
        t = len(results)
        p = sum(1 for x in results if x["verdict"]=="PASS")
        f = sum(1 for x in results if x["verdict"]=="FAIL")
        n = sum(1 for x in results if x["verdict"]=="NEUTRAL")
        print(f"\n  {name}")
        print(f"  Total: {t} | PASS: {p} ({p/t*100:.1f}%) | FAIL: {f} ({f/t*100:.1f}%) | NEUTRAL: {n} ({n/t*100:.1f}%)")
        print(f"  Accuracy: {p/t*100:.1f}% | Accuracy (excl NEUTRAL): {p/max(1,p+f)*100:.1f}%")
        if f > 0:
            print(f"  FAILED:")
            for x in results:
                if x["verdict"]=="FAIL":
                    print(f"    [{x['id']}] {x['question']}")
                    print(f"      -> {x['answer'][:100]}")
        if n > 0:
            print(f"  NEUTRAL (chatbot said cannot answer):")
            for x in results:
                if x["verdict"]=="NEUTRAL":
                    print(f"    [{x['id']}] {x['question']}")
                    print(f"      -> {x['answer'][:100]}")

    en = [x for x in all_results if x["lang"]=="English"]
    ru = [x for x in all_results if x["lang"]=="Roman Urdu"]
    report("SECTION A: ENGLISH (30)", en)
    report("SECTION B: ROMAN URDU (30)", ru)

    # Combined
    t = len(all_results)
    p = sum(1 for x in all_results if x["verdict"]=="PASS")
    f = sum(1 for x in all_results if x["verdict"]=="FAIL")
    n = sum(1 for x in all_results if x["verdict"]=="NEUTRAL")
    print(f"\n{'='*60}")
    print(f"  OVERALL: Model=llama3.2:3b | Scope=PharmacyPOS")
    print(f"  Total={t} | PASS={p} ({p/t*100:.1f}%) | FAIL={f} ({f/t*100:.1f}%) | NEUTRAL={n} ({n/t*100:.1f}%)")
    print(f"  Accuracy: {p/t*100:.1f}% | Accuracy (excl NEUTRAL): {p/max(1,p+f)*100:.1f}%")
    print(f"{'='*60}")

    # Per-category
    cats = sorted(set(x["category"] for x in all_results))
    print(f"\n  PER-CATEGORY:")
    print(f"  {'Category':<25} {'Pass':>5} {'Fail':>5} {'Neutral':>7} {'Acc':>7}")
    for c in cats:
        cr = [x for x in all_results if x["category"]==c]
        cp = sum(1 for x in cr if x["verdict"]=="PASS")
        cf = sum(1 for x in cr if x["verdict"]=="FAIL")
        cn = sum(1 for x in cr if x["verdict"]=="NEUTRAL")
        ct = len(cr)
        print(f"  {c:<25} {cp:>5} {cf:>5} {cn:>7} {cp/ct*100:>6.1f}%")

    # Detailed log
    print(f"\n{'='*80}")
    print("  DETAILED ANSWER LOG")
    print(f"{'='*80}")
    for x in all_results:
        icon = {"PASS":"PASS","FAIL":"FAIL","NEUTRAL":"NEUTRAL"}[x["verdict"]]
        print(f"\n  [{x['id']}] {icon} | Route: {x['route']} | {x['timing']}s")
        print(f"  Q: {x['question']}")
        print(f"  A: {x['answer'][:200]}")

    return all_results

if __name__ == "__main__":
    results = run_benchmark()
