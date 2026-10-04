"""End-to-end evaluation of the user-supplied 100-question pharmacy benchmark.

The expected answers are calculated independently from the workbook with
pandas. Chat answers are obtained through the same FastAPI /api/chat endpoint
used by the application. The evaluation JSON is a held-out test set; this
script never modifies it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.main import app  # noqa: E402

DEFAULT_DATA = ROOT / "data" / "uploads" / "pharmacy_compliant_master_dataset.xlsx"
DEFAULT_CASES = ROOT / "evals" / "pharmacy_compliant_dataset_100.json"
DEFAULT_REPORT = ROOT / "reports" / "compliant_dataset_100_after.json"
FILE_ID = "file_8bfe796e635f"


def expected_facts(df: pd.DataFrame, qid: int):
    """Independent expected entity/value pairs computed only from source rows."""
    amount = pd.to_numeric(df["Total_Transaction_Value_USD"], errors="coerce")
    qty = pd.to_numeric(df["Quantity_Sold"], errors="coerce")
    mrp = pd.to_numeric(df["Maximum_Retail_Price_USD"], errors="coerce")
    cost = pd.to_numeric(df["Unit_Cost_Price_USD"], errors="coerce")
    date = pd.to_datetime(df["Timestamp"], errors="coerce")
    trans = df["Transaction_UUID"].astype(str)
    rx_mask = df["Prescription_Required"].fillna("").astype(str).str.contains(r"rx|prescription|required|yes|true", case=False, regex=True) & ~df["Prescription_Required"].fillna("").astype(str).str.contains(r"otc|no|false", case=False, regex=True)
    otc_mask = df["Prescription_Required"].fillna("").astype(str).str.contains(r"otc|non.?prescription|no", case=False, regex=True)

    def pairs(column, metric, agg="sum", mask=None, n=None, low=False):
        work = df if mask is None else df.loc[mask]
        if metric == "tx":
            grouped = work.groupby(column, dropna=True)["Transaction_UUID"].nunique()
        elif metric == "products":
            grouped = work.groupby(column, dropna=True)["Product_Name"].nunique()
        else:
            s = {"amount": amount, "qty": qty, "mrp": mrp, "cost": cost, "margin": mrp-cost, "gross_margin": (mrp-cost)*qty}[metric].loc[work.index]
            grouped = s.groupby(work[column]).agg(agg)
        grouped = grouped.sort_values(ascending=low, kind="stable")
        if n:
            chosen = grouped.head(n)
            if len(chosen):
                cutoff = chosen.iloc[-1]
                grouped = grouped[grouped.le(cutoff) if low else grouped.ge(cutoff)]
        return [(str(k), float(v)) for k, v in grouped.items()]

    def scalar(value):
        return [("", float(value))]

    if qid == 1: return scalar(amount.sum())
    if qid == 2: return scalar(trans.nunique())
    if qid == 3: return scalar(qty.sum())
    if qid in (4, 12): return scalar(amount.mean())
    if qid == 5: return pairs("Product_Name", "amount", n=1)
    if qid == 6: return pairs("Product_Name", "amount", n=1, low=True)
    if qid == 7: return pairs("Product_Name", "amount", n=10)
    if qid == 8: return pairs("Product_Name", "qty", n=10)
    if qid == 9: return pairs("Product_Name", "qty", n=1)
    if qid == 10: return pairs("Product_Name", "qty", n=1, low=True)
    if qid == 11: return pairs("Product_Name", "amount")
    if qid == 13: return pairs("Product_Name", "amount", n=5)
    if qid in (14, 15):
        top = (amount.nlargest(5) if qid == 14 else amount.nsmallest(5))
        return [(str(df.loc[i, "Transaction_UUID"]), float(amount.loc[i])) for i in top.index]
    if qid == 16: return pairs("Therapeutic_Class", "amount", n=1)
    if qid == 17: return pairs("Therapeutic_Class", "qty", n=1)
    if qid == 18: return pairs("Therapeutic_Class", "amount", n=5)
    if qid == 19: return pairs("Therapeutic_Class", "amount", n=5, low=True)
    if qid == 20: return scalar(df.Product_Name.nunique())
    if qid == 21: return pairs("Manufacturer", "amount", n=1)
    if qid == 22: return pairs("Manufacturer", "qty", n=1)
    if qid == 23: return pairs("Manufacturer", "amount", n=10)
    if qid == 24: return pairs("Manufacturer", "amount", n=5, low=True)
    if qid == 25: return scalar(df.Manufacturer.nunique())
    if qid == 26:
        names = sorted(df.loc[df.Manufacturer.eq("AstraZeneca"), "Product_Name"].dropna().unique())
        return [(x, None) for x in names]
    if qid == 27: return scalar(amount[df.Manufacturer.eq("AstraZeneca")].sum())
    if qid == 28: return pairs("Manufacturer", "mrp", agg="mean", n=1)
    if qid == 29: return pairs("Manufacturer", "products", n=1)
    if qid == 30: return pairs("Manufacturer", "amount", n=5)
    if qid == 31 or qid == 38: return pairs("Pharmacy_Branch", "amount")
    if qid == 32: return pairs("Pharmacy_Branch", "amount", n=1)
    if qid == 33: return pairs("Pharmacy_Branch", "qty", n=1)
    if qid == 34: return pairs("Pharmacy_Branch", "amount", agg="mean", n=1)
    if qid == 35:
        out=[]
        for branch, part in df.groupby("Pharmacy_Branch", sort=True): out.extend((f"{branch} {p}",v) for p,v in pairs_in(part,"Product_Name",qty,5))
        return out
    if qid == 36:
        out=[]
        for branch, part in df.groupby("Pharmacy_Branch", sort=True): out.extend((f"{branch} {p}",v) for p,v in pairs_in(part,"Therapeutic_Class",amount,1))
        return out
    if qid == 37:
        out=[]
        for branch, part in df.groupby("Pharmacy_Branch", sort=True): out.extend((f"{branch} {p}",v) for p,v in pairs_in(part,"Manufacturer",amount,1))
        return out
    if qid == 39: return pairs("Pharmacy_Branch", "tx", n=1)
    if qid == 40: return pairs("Pharmacy_Branch", "amount", n=1, low=True)
    if qid == 41:
        latest = date.max().date(); return scalar(amount[date.dt.date.eq(latest)].sum())
    if qid == 42: return scalar(amount[date.dt.month.eq(1)].sum())
    if qid in (43, 46, 50):
        return [(str(k),float(v)) for k,v in amount.groupby(date.dt.to_period("M").astype(str)).sum().sort_index().items()]
    if qid == 44: return pairs("_month", "amount", n=1) if "_month" in df else pairs_from_series(date.dt.to_period("M").astype(str), amount, 1)
    if qid == 45: return pairs_from_series(date.dt.to_period("M").astype(str), amount, 1, low=True)
    if qid == 47: return pairs_from_series(date.dt.strftime("%Y-%m-%d"), amount, 1)
    if qid == 48: return pairs("_day", "tx", n=1) if "_day" in df else pairs_from_series(date.dt.strftime("%Y-%m-%d"), trans, 1, unique=True)
    if qid == 49: return scalar(float(amount.groupby(date.dt.strftime("%Y-%m-%d")).sum().mean()))
    if qid == 51: return scalar(cost[df.Product_Name.eq("Betaloc 50mg Extended-Release")].mean())
    if qid == 52: return scalar(mrp[df.Product_Name.eq("Betaloc 50mg Extended-Release")].mean())
    if qid == 53: return pairs("Product_Name", "mrp", agg="mean", n=5)
    if qid == 54: return pairs("Product_Name", "mrp", agg="mean", n=5, low=True)
    if qid in (55, 57): return pairs("Product_Name", "margin", agg="mean", n=5)
    if qid == 56: return pairs("Product_Name", "margin", agg="mean", n=5, low=True)
    if qid == 58: return pairs("Product_Name", "margin", agg="mean")
    if qid == 59:
        margin=(mrp-cost).groupby(df.Product_Name).mean(); cutoff=margin.median()
        work=df.loc[df.Product_Name.isin(margin[margin.ge(cutoff)].index)]
        return pairs_on(work,"Product_Name",qty,10)
    if qid == 60: return pairs("Therapeutic_Class", "gross_margin", n=1)
    if qid == 61: return scalar(trans[rx_mask].nunique())
    if qid == 62: return scalar(trans[otc_mask].nunique())
    if qid == 63: return scalar(trans[rx_mask].nunique()/trans.nunique()*100)
    if qid == 64: return pairs("Product_Name", "qty", mask=rx_mask, n=1)
    if qid == 65: return pairs("Product_Name", "qty", mask=otc_mask, n=1)
    if qid == 66: return scalar(amount[rx_mask].sum())
    if qid == 67: return scalar(amount[otc_mask].sum())
    if qid == 68: return pairs("Therapeutic_Class", "tx", mask=rx_mask, n=1)
    if qid == 69: return pairs("Manufacturer", "amount", mask=rx_mask, n=1)
    if qid == 70: return [("prescription",float(amount[rx_mask].sum())),("otc",float(amount[otc_mask].sum()))]
    if qid == 71: return pairs("Payment_Method", "tx", n=1)
    if qid == 72: return scalar(amount[df.Payment_Method.str.contains("cash",case=False,na=False)].sum())
    if qid == 73: return scalar(amount[df.Payment_Method.str.contains("insurance",case=False,na=False)].sum())
    if qid == 74: return pairs("Payment_Method", "amount", n=1)
    if qid == 75: return [(k,v/trans.nunique()*100) for k,v in pairs("Payment_Method","tx")]
    if qid == 76: return pairs("Payment_Method", "amount", agg="mean")
    if qid == 77: return pairs("Pharmacy_Branch","tx",mask=df.Payment_Method.str.contains("cash",case=False,na=False),n=1)
    if qid == 78: return pairs("Pharmacy_Branch","tx",mask=df.Payment_Method.str.contains("insurance",case=False,na=False),n=1)
    if qid == 79: return pairs("Product_Name","tx",mask=df.Payment_Method.str.contains("insurance",case=False,na=False),n=1)
    if qid == 80: return [("cash",float(amount[df.Payment_Method.str.contains("cash",case=False,na=False)].sum())),("insurance",float(amount[df.Payment_Method.str.contains("insurance",case=False,na=False)].sum()))]
    if qid == 81: return pairs("Attending_Pharmacist","tx",n=1)
    if qid == 82: return pairs("Attending_Pharmacist","amount",n=1)
    if qid == 83: return pairs("Attending_Pharmacist","amount",agg="mean")
    if qid == 84: return pairs("Attending_Pharmacist","qty",n=1)
    if qid == 85: return scalar(trans[df.Attending_Pharmacist.str.contains("Hina Mukhtar",case=False,na=False)].nunique())
    if qid == 86: return pairs("Prescribing_Doctor","tx",n=1)
    if qid == 87: return pairs("Prescribing_Doctor","amount",n=1)
    if qid in (88,89):
        out=[]; col="Product_Name" if qid==88 else "Therapeutic_Class"
        for doctor,part in df.groupby("Prescribing_Doctor",sort=True): out.extend((f"{doctor} {v}",n) for v,n in pairs_in(part,col,trans,1,unique=True))
        return out
    if qid == 90: return scalar(df.Prescribing_Doctor.nunique())
    if qid in (91,95): return []
    if qid == 92: return [(str(p),None) for p in df.loc[df.Batch_Number.eq("BTH-2025-1000"),"Product_Name"].dropna().unique()]
    if qid == 93: return pairs("Batch_Number","qty",n=30)
    if qid == 94: return pairs("Batch_Number","amount",n=1)[:25]
    if qid == 96: return [(str(p),None) for p in df.loc[df.Stock_Location_Rack.eq("Rack-A-Shelf-1"),"Product_Name"].dropna().unique()]
    if qid == 97: return pairs("Stock_Location_Rack","tx",n=1)
    if qid == 98: return pairs("Product_Name","qty",n=5)
    if qid == 99:
        margins=(mrp-cost).groupby(df.Product_Name).mean(); cutoff=margins.median()
        selected=df.loc[df.Product_Name.isin(margins[margins.le(cutoff)].index)]
        return pairs_on(selected,"Product_Name",qty,5)
    if qid == 100: return pairs("Pharmacy_Branch","amount",n=5)
    return []


def pairs_in(frame, column, values, n, unique=False):
    grouped = values.loc[frame.index].groupby(frame[column]).nunique() if unique else values.loc[frame.index].groupby(frame[column]).sum()
    grouped=grouped.sort_values(ascending=False,kind="stable"); chosen=grouped.head(n)
    if len(chosen): grouped=grouped[grouped.ge(chosen.iloc[-1])]
    return [(str(k),float(v)) for k,v in grouped.items()]


def pairs_on(frame, column, values, n):
    grouped=values.loc[frame.index].groupby(frame[column]).sum()
    grouped=grouped.sort_values(ascending=False,kind="stable"); chosen=grouped.head(n)
    if len(chosen): grouped=grouped[grouped.ge(chosen.iloc[-1])]
    return [(str(k),float(v)) for k,v in grouped.items()]


def pairs_from_series(keys, values, n, low=False, unique=False):
    grouped=values.groupby(keys).nunique() if unique else values.groupby(keys).sum()
    grouped=grouped.sort_values(ascending=low,kind="stable"); chosen=grouped.head(n)
    if len(chosen): grouped=grouped[grouped.le(chosen.iloc[-1]) if low else grouped.ge(chosen.iloc[-1])]
    return [(str(k),float(v)) for k,v in grouped.items()]


def value_in_answer(answer, value):
    if value is None: return True
    value=float(value)
    candidates={f"{value:,.2f}",f"{value:,.1f}",f"{value:,.0f}"}
    return any(x in answer for x in candidates)


def check_facts(answer, facts, qid):
    low=answer.casefold()
    if qid in (91,95): return bool(re.search(r"which medicine|provide its name|which product",low))
    normalized_answer=re.sub(r"[^a-z0-9]+"," ",low).strip()
    if qid == 48:
        return bool(facts and value_in_answer(answer,facts[0][1]) and any(re.sub(r"[^a-z0-9]+"," ",name.casefold()).strip() in normalized_answer for name,_ in facts) and f"{len(facts)} results tie" in low)
    if qid == 93:
        return bool(facts and "showing 30 of" in low and all(re.sub(r"[^a-z0-9]+"," ",name.casefold()).strip() in normalized_answer and value_in_answer(answer,value) for name,value in facts[:30]))
    if qid == 35:
        branches=("Branch-01 (Main Central Plaza)","Branch-02 (Healthcare City Hub)","Branch-03 (Suburban Extension)")
        for label,value in facts:
            branch=next((b for b in branches if label.startswith(b)),None)
            if not branch: return False
            start=answer.find(branch)
            if start<0: return False
            next_starts=[answer.find("; "+b,start+len(branch)) for b in branches if answer.find("; "+b,start+len(branch))>=0]
            end=min(next_starts) if next_starts else len(answer)
            segment=re.sub(r"[^a-z0-9]+"," ",answer[start:end].casefold()).strip()
            child=label[len(branch):].strip()
            if re.sub(r"[^a-z0-9]+"," ",child.casefold()).strip() not in segment or not value_in_answer(answer[start:end],value): return False
        return bool(facts)
    if qid == 94:
        return bool(facts and value_in_answer(answer,facts[0][1]) and any(re.sub(r"[^a-z0-9]+"," ",name.casefold()).strip() in re.sub(r"[^a-z0-9]+"," ",low) for name,_ in facts))
    if qid == 70:
        return all(value_in_answer(answer,value) and ((label=="otc" and "otc" in low) or (label=="prescription" and re.search(r"rx|prescription",low))) for label,value in facts)
    if not facts: return False
    checks=[]
    for label,value in facts:
        label_norm=re.sub(r"[^a-z0-9]+"," ",label.casefold()).strip()
        # Nested evidence prepends the parent entity; require the salient child
        # text while keeping names and values from the independent calculation.
        if label_norm:
            entity_ok=label_norm in normalized_answer
        else: entity_ok=True
        checks.append(entity_ok and value_in_answer(answer,value))
    return bool(checks) and all(checks)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",type=Path,default=DEFAULT_DATA)
    parser.add_argument("--cases",type=Path,default=DEFAULT_CASES)
    parser.add_argument("--report",type=Path,default=DEFAULT_REPORT)
    parser.add_argument("--file-id",default=FILE_ID)
    args=parser.parse_args()
    raw=pd.read_excel(args.dataset)
    cases=json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    client=TestClient(app)
    rows=[]
    partial_path=args.report.with_suffix(".partial.json")
    for case in cases:
        payload={"question":case["question"],"domain":"pharmacy","file_ids":[args.file_id],"session_id":f"eval100-{case['id']}"}
        try:
            response=client.post("/api/chat",json=payload)
            data=response.json()
            answer=str(data.get("answer", ""))
            route=data.get("route")
            sources=data.get("sources") or []
            facts=expected_facts(raw,case["id"])
            factual=check_facts(answer,facts,case["id"])
            clarify=case["id"] in (91,95)
            route_ok=route=="analytics"
            citation_ok=(len(sources)>0) if not clarify else len(sources)==0
            completion_ok=case["id"]!=93
            rows.append({**case,"expected_facts":facts,"answer":answer,"route":route,"route_success":route_ok,"source_count":len(sources),"source_rows":[s.get("source_row") for s in sources],"citation_coverage_success":citation_ok,"factual_success":factual,"full_request_completion_success":completion_ok,"success":route_ok and citation_ok and factual and completion_ok,"status_code":response.status_code,"error":data.get("detail") if response.status_code>=400 else None})
        except Exception as exc:
            rows.append({**case,"expected_facts":expected_facts(raw,case["id"]),"answer":"","route":None,"route_success":False,"source_count":0,"source_rows":[],"citation_coverage_success":False,"factual_success":False,"full_request_completion_success":False,"success":False,"error":f"{type(exc).__name__}: {exc}"})
        if case["id"] % 10 == 0:
            partial_path.parent.mkdir(parents=True,exist_ok=True)
            partial_path.write_text(json.dumps(rows,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
            print(f"Evaluated {case['id']}/{len(cases)}",flush=True)
    supported=[r for r in rows if r["id"] not in (91,95)]
    ranked_ids={5,6,7,8,9,10,13,14,15,16,17,18,19,21,22,23,24,28,29,30,32,33,34,35,36,37,39,40,44,45,47,53,54,55,56,57,60,64,65,68,69,71,74,77,78,79,81,82,84,86,87,88,89,94,97}
    ranked=[r for r in rows if r["id"] in ranked_ids and r["expected_facts"]]
    top1_hits=sum(check_facts(r["answer"],r["expected_facts"][:1],r["id"]) for r in ranked)
    top5_facts=[(r, r["expected_facts"][:5]) for r in ranked]
    top5_hits=sum(sum(check_facts(r["answer"],[fact],r["id"]) for fact in facts) for r,facts in top5_facts)
    top5_total=sum(len(facts) for _,facts in top5_facts)
    paraphrases={}
    for row in rows:
        if row.get("paraphrase_group"):
            paraphrases.setdefault(row["paraphrase_group"],[]).append(row)
    paraphrase_scores={name:all(x["factual_success"] for x in group) for name,group in paraphrases.items()}
    baseline_path=ROOT/"reports"/"compliant_dataset_rag_eval.json"
    baseline_comparison=None
    if baseline_path.exists():
        old=json.loads(baseline_path.read_text(encoding="utf-8"))
        case_by_question={x["question"]:x for x in rows}
        prior=[]
        for old_case in old:
            matched=case_by_question.get(old_case.get("question"))
            if matched:
                before_answer=str(old_case.get("rag_answer", ""))
                prior.append({"question":old_case["question"],"before_answer":before_answer,"before_success":check_facts(before_answer,matched["expected_facts"],matched["id"]),"after_answer":matched["answer"],"after_success":matched["factual_success"]})
        if prior:
            baseline_comparison={"matched_questions":len(prior),"before_factual_accuracy":sum(x["before_success"] for x in prior)/len(prior),"after_factual_accuracy":sum(x["after_success"] for x in prior)/len(prior),"change_percentage_points":100*(sum(x["after_success"] for x in prior)-sum(x["before_success"] for x in prior))/len(prior),"cases":prior}
    report={"dataset":args.dataset.name,"dataset_rows":len(raw),"run_time":datetime.now().astimezone().isoformat(timespec="seconds"),"case_count":len(rows),"bounded_output_cases":[{"id":93,"reason":"5,300 batch groups exceed a practical chat response; the assistant returns 30 cited examples and states the count and filter needed to narrow it. The full all-batch request remains incomplete until export is supported."}],"metrics":{"intent_accuracy":sum(x["route_success"] for x in rows)/max(1,len(rows)),"factual_accuracy_supported":sum(x["factual_success"] for x in supported)/max(1,len(supported)),"citation_coverage_supported":sum(x["citation_coverage_success"] for x in supported)/max(1,len(supported)),"abstention_accuracy":sum(x["factual_success"] for x in rows if x["id"] in (91,95))/2,"full_request_completion":sum(x["full_request_completion_success"] for x in rows)/max(1,len(rows)),"end_to_end_success":sum(x["success"] for x in rows)/max(1,len(rows)),"paraphrase_group_accuracy":sum(paraphrase_scores.values())/max(1,len(paraphrase_scores)),"ranked_cases_scored":len(ranked),"answer_ranking_top1_accuracy":top1_hits/max(1,len(ranked)),"answer_result_recall_at_5":top5_hits/max(1,top5_total),"safety_sensitive_cases":0,"safety_sensitive_accuracy":None},"baseline_comparison_on_matched_questions":baseline_comparison,"limitations":{"retrieval_recall_and_vector_ranking":"Not scored on this dataset run: supported analytics questions execute over the complete selected table and cite contributing source rows rather than depending on top-k Chroma retrieval.","safety":"The 100 questions contain no clinical, legal, or regulatory advice questions.","fact_scoring":"Entity/value assertions are independently calculated from the workbook; broad summary cases score the explicitly checked values only."},"cases":rows}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
    partial_path.unlink(missing_ok=True)
    md=["# Pharmacy RAG evaluation — compliant master dataset", "", f"Run: {report['run_time']}", f"Source rows: {len(raw):,}; held-out questions: {len(rows)}", "", "## Measured results", "", "| Metric | Result |", "|---|---:|"]
    for name,value in report["metrics"].items(): md.append(f"| {name.replace('_',' ').title()} | {'N/A' if value is None else (f'{value*100:.1f}%' if isinstance(value,float) and value<=1 else value)} |")
    if baseline_comparison:
        md.extend(["", f"Same-question comparison: {baseline_comparison['matched_questions']} overlapping questions; independently rescored before **{baseline_comparison['before_factual_accuracy']*100:.1f}%**, after **{baseline_comparison['after_factual_accuracy']*100:.1f}%** ({baseline_comparison['change_percentage_points']:+.1f} percentage points)."])
        prior_failures=[c for c in baseline_comparison["cases"] if not c["before_success"]][:3]
        if prior_failures:
            md.extend(["", "## Representative failures before the fix", "", "| Question | Before | After |", "|---|---|---|"])
            for c in prior_failures: md.append(f"| {c['question']} | {c['before_answer'].replace('|','/').replace(chr(10),' ')[:240]} | {c['after_answer'].replace('|','/').replace(chr(10),' ')[:240]} |")
    md.extend(["", "## Failed cases", "", "| # | Question | Expected evidence | Expected behavior | Actual answer | Route | Sources |", "|---:|---|---|---|---|---|---:|"])
    for r in rows:
        if not r["success"]: md.append(f"| {r['id']} | {r['question']} | {', '.join(r['expected_evidence'])} | {r['expected_answer_behavior']} | {r['answer'].replace('|','/').replace(chr(10),' ')[:300]} | {r['route']} | {r['source_count']} |")
    md.extend(["", "## Limits", "", "- Question 93 has 5,300 distinct batch groups. The chatbot returns 30 cited examples, names the total and asks for a narrower filter; full-result export is a product gap.", "- Chroma recall and vector ranking are not scored here because these 100 questions route to full-table structured analytics; the result is ranked against workbook ground truth and cited to source rows.", "- This question set contains no clinical, legal, regulatory, or patient privacy challenge cases.", "- The workbook is a transaction sample with limited date coverage and does not include cash-flow ledgers, prescription details, patient histories, or regulatory source texts.", ""])
    args.report.with_suffix(".md").write_text("\n".join(md),encoding="utf-8")
    print(json.dumps({"report":str(args.report.resolve()),"metrics":report["metrics"],"baseline_comparison":({k:v for k,v in baseline_comparison.items() if k!='cases'} if baseline_comparison else None),"failures":[{"id":r["id"],"question":r["question"],"answer":r["answer"][:220],"factual":r["factual_success"],"route":r["route"],"sources":r["source_count"]} for r in rows if not r["success"]]},indent=2,ensure_ascii=False))


if __name__=="__main__": main()
