"""Exercise the 27 conversation turns in the supplied QA report in shared sessions.

The report supplies the follow-up prompts but not an independently machine-readable
answer key for these turns, so this records behavior and citations without claiming
semantic accuracy.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.ingestion.registry import file_registry
from app.rag.chat import RAGChat
from app.rag.history import session_manager
from app.rag.models import ChatRequest


def parse_scenarios(path: Path):
    ns={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as docx: root=ET.fromstring(docx.read("word/document.xml"))
    paras=["".join(n.text or "" for n in p.findall(".//w:t",ns)).strip() for p in root.findall(".//w:p",ns)]
    dataset=None; scenarios=[]; current=None; current_turn=None
    for line in paras:
        section=re.match(r"Dataset\s+(\d+)\s+—\s+(Sales|Inventory|Purchases)",line,re.I)
        if section: dataset={"1":"sales","2":"inventory","3":"purchases"}[section.group(1)]; continue
        source_section=re.match(r"Dataset:\s*pharmacy_(sales|inventory|purchases)_test_",line,re.I)
        if source_section:
            dataset=source_section.group(1).casefold()
            if current and not current["turns"]:
                current["dataset"]=dataset
                current["id"]=f"{dataset}-scenario-{current['scenario_number']}"
            continue
        start=re.match(r"Scenario\s+(\d+)\s+—\s+(.+)",line,re.I)
        if start and dataset:
            current={"id":f"{dataset}-scenario-{start.group(1)}","scenario_number":int(start.group(1)),"dataset":dataset,"title":start.group(2),"turns":[]}; scenarios.append(current); continue
        turn=re.match(r"Turn\s+(\d+)\s+—\s+User:\s*(.*)",line,re.I)
        if turn and current:
            current_turn={"turn":int(turn.group(1)),"question":turn.group(2).strip()}
            current["turns"].append(current_turn)
            continue
        if line.startswith("Expected Answer:") and current_turn is not None:
            current_turn["expected_answer"]=line.split(":",1)[1].strip()
    return scenarios


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("report",type=Path); ap.add_argument("--output",type=Path,default=ROOT/"backend"/"report_followup_results.json"); a=ap.parse_args()
    scenarios=parse_scenarios(a.report)
    active=file_registry.list_files(); ids={}
    for key,marker in (("sales","pharmacy_sales_test_01.csv"),("inventory","pharmacy_inventory_test_02.csv"),("purchases","pharmacy_purchases_test_03")):
        rec=next((r for r in active if marker.casefold() in r.filename.casefold() and r.status=="active" and r.chunk_count>0),None)
        if not rec: raise RuntimeError(f"Report dataset {key} is not active")
        ids[key]=rec.file_id
    chat=RAGChat(); results=[]; began=time.time()
    for scenario in scenarios:
        sid="report-followup-"+scenario["id"]; session_manager.delete_session(sid)
        for turn in scenario["turns"]:
            response=chat.ask(ChatRequest(question=turn["question"],session_id=sid,domain="pharmacy",file_ids=[ids[scenario["dataset"]]]))
            expected_nums=re.findall(r"(?<![A-Za-z0-9-])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9-])",turn.get("expected_answer",""))
            actual_nums=re.findall(r"(?<![A-Za-z0-9-])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9-])",response.answer)
            expected_nums=[x.replace(",","") for x in expected_nums]; actual_nums=[x.replace(",","") for x in actual_nums]
            turn.update({"route":response.route,"answer":response.answer,"citation_count":len(response.sources),"sources":[s.model_dump() for s in response.sources],"has_answer":bool(response.answer.strip()),"expected_numbers":expected_nums,"matched_expected_numbers":[x for x in expected_nums if x in actual_nums],"numeric_recall":sum(x in actual_nums for x in expected_nums)/len(expected_nums) if expected_nums else None,"elapsed_seconds":round(response.timing or 0,3)})
            print(f"{scenario['id']} turn {turn['turn']} {response.route} citations={len(response.sources)} {response.answer[:160]}",flush=True)
        results.append(scenario); session_manager.delete_session(sid)
    turns=[t for s in results for t in s["turns"]]
    numeric=[t for t in turns if t["numeric_recall"] is not None]
    payload={"benchmark":"user_report_followup_scenarios","scenario_count":len(results),"turn_count":len(turns),"answer_rate":sum(t["has_answer"] for t in turns)/max(1,len(turns)),"citation_coverage":sum(t["citation_count"]>0 for t in turns)/max(1,len(turns)),"numeric_case_accuracy":sum(t["numeric_recall"]==1 for t in numeric)/max(1,len(numeric)),"mean_expected_number_recall":sum(t["numeric_recall"] for t in numeric)/max(1,len(numeric)),"metric_caveat":"Numeric overlap is a weak surface-form proxy. The report's follow-up prompts were used for development, not an independent holdout; names and non-numeric semantics require human or semantic grading.","elapsed_seconds":round(time.time()-began,2),"scenarios":results}
    a.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in payload.items() if k!="scenarios"},indent=2)); print(f"Results: {a.output}")


if __name__=="__main__": main()
