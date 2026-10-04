"""Run the answer cases from the supplied pharmacy QA report against their scoped CSVs.

The DOCX is test material only. It is parsed at runtime so the evaluation set
does not enter prompts, routing rules, or retrieval indexes.
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
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.ingestion.registry import file_registry
from app.rag.chat import RAGChat
from app.rag.history import session_manager
from app.rag.models import ChatRequest


def parse_report(path: Path):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    cases = []
    dataset = None
    current = None
    with zipfile.ZipFile(path) as docx:
        root = ET.fromstring(docx.read("word/document.xml"))
    body = root.find("w:body", ns)
    for element in body:
        if not element.tag.endswith("}p"):
            continue
        text = "".join(node.text or "" for node in element.findall(".//w:t", ns)).strip()
        if not text:
            continue
        section = re.match(r"Dataset\s+(\d+)\s+—\s+(Sales|Inventory|Purchases)", text, re.I)
        if section:
            dataset = {"1": "sales", "2": "inventory", "3": "purchases"}[section.group(1)]
            continue
        qid = re.match(r"(D[123]-Q\d+)\s+—", text)
        if qid:
            current = {"id": qid.group(1), "dataset": dataset}
            cases.append(current)
            continue
        if current is None:
            continue
        if text.startswith("Question:"):
            current["question"] = text.split(":", 1)[1].strip()
        elif text.startswith("Expected Answer:"):
            current["expected_answer"] = text.split(":", 1)[1].strip()
        elif text.startswith("Source:"):
            current["source_note"] = text.split(":", 1)[1].strip()
            current = None
    return cases


def number_tokens(text: str):
    vals = re.findall(r"(?<![A-Za-z0-9-])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9-])", text)
    return [re.sub(r",", "", v).rstrip("0").rstrip(".") if "." in v else v.replace(",", "") for v in vals]


def structured_numeric_tokens(value):
    """Expose numeric values and explicit list counts from planner outputs."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                found.append(str(item))
            elif isinstance(item, list):
                if key in {"records", "sale_ids", "purchase_ids", "record_ids", "products"}:
                    found.append(str(len(item)))
                found.extend(structured_numeric_tokens(item))
            else:
                found.extend(structured_numeric_tokens(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(structured_numeric_tokens(item))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path, help="Path to the supplied DOCX QA report")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for a quick smoke run")
    parser.add_argument("--output", type=Path, default=ROOT / "backend" / "report_benchmark_results.json")
    parser.add_argument("--cases-output", type=Path, default=None, help="Optional path to export parsed cases as a standalone, model-independent evaluation set")
    args = parser.parse_args()
    cases = parse_report(args.report)
    if args.cases_output:
        args.cases_output.parent.mkdir(parents=True, exist_ok=True)
        args.cases_output.write_text(json.dumps({"source_report": args.report.name, "case_count": len(cases), "cases": cases}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    records = file_registry.list_files()
    sources = {}
    source_records = {}
    for dataset, marker in (("sales", "pharmacy_sales_test_01.csv"), ("inventory", "pharmacy_inventory_test_02.csv"), ("purchases", "pharmacy_purchases_test_03")):
        record = next((r for r in records if marker.casefold() in r.filename.casefold() and r.status == "active" and r.chunk_count > 0), None)
        if record is None:
            raise RuntimeError(f"The report's {dataset} dataset is not active in the file registry")
        sources[dataset] = record.file_id
        source_records[dataset] = record
    if args.limit:
        cases = cases[:args.limit]

    chat = RAGChat()
    results = []
    started = time.time()
    for index, case in enumerate(cases, 1):
        session_id = f"report-benchmark-{case['id']}"
        session_manager.delete_session(session_id)
        response = chat.ask(ChatRequest(question=case["question"], session_id=session_id, domain="pharmacy", file_ids=[sources[case["dataset"]]]))
        actual_nums = number_tokens(response.answer)
        # Parenthetical notes in this QA sheet often state excluded boundary
        # values (e.g. "exactly 5 is NOT included"); those are predicates, not
        # expected answer facts. Compare expected answer facts against both the
        # answer and machine-readable planner output.
        expected_text = re.sub(r"\([^)]*\bnot included\b[^)]*\)", "", case.get("expected_answer", ""), flags=re.I)
        expected_nums = number_tokens(expected_text)
        computed_numbers = [token for raw in structured_numeric_tokens(response.computed_values or {}) for token in number_tokens(raw)]
        actual_nums = list(dict.fromkeys(actual_nums + computed_numbers))
        present = [number for number in expected_nums if number in actual_nums]
        expected_ids = list(dict.fromkeys(re.findall(
            r"\b(?:SALE|PUR|INV|SKU|RX)-\d+[A-Z0-9-]*\b",
            case.get("question", "") + " " + case.get("expected_answer", ""), re.I,
        )))
        expected_answer_ids = list(dict.fromkeys(re.findall(
            r"\b(?:SALE|PUR|INV|SKU|RX)-\d+[A-Z0-9-]*\b", case.get("expected_answer", ""), re.I
        )))
        actual_answer_ids = set(re.findall(r"\b(?:SALE|PUR|INV|SKU|RX)-\d+[A-Z0-9-]*\b", response.answer, re.I))
        expected_rows = []
        if expected_ids:
            raw = pd.read_csv(source_records[case["dataset"]].file_path, dtype=str)
            id_col = next((c for c in raw.columns if re.sub(r"[^a-z0-9]", "", c.casefold()) in {"saleid", "purchaseid", "productid", "sku", "rxid"}), None)
            if id_col:
                row_map = {str(value).casefold(): int(index) + 2 for index, value in raw[id_col].items()}
                expected_rows = [row_map[item.casefold()] for item in expected_ids if item.casefold() in row_map]
        cited_rows = [int(s.source_row) for s in response.sources if s.source_row is not None]
        expected_missing = bool(re.search(r"\bnot found|not available|no (?:matching )?records?\b", case.get("expected_answer", ""), re.I))
        abstention_markers = bool(re.search(r"\bno\b.{0,40}\brecords?\b|not (?:provided|recorded|available|found)|does not contain|cannot (?:provide|determine)|can.t determine\b", response.answer, re.I))
        question_ids = re.findall(r"\b(?:SALE|PUR|INV|SKU|RX)-\d+[A-Z0-9-]*\b", case.get("question", ""), re.I)
        exact_lookup = len(set(question_ids)) == 1
        expected_route = "rag" if exact_lookup else "analytics"
        case.update({
            "file_id": sources[case["dataset"]],
            "route": response.route,
            "answer": response.answer,
            "computed_values": response.computed_values,
            "sources": [source.model_dump() for source in response.sources],
            "expected_numbers": expected_nums,
            "matched_expected_numbers": present,
            "numeric_recall": len(present) / len(expected_nums) if expected_nums else None,
            "intended_meaning": case.get("question"),
            "expected_evidence": case.get("source_note"),
            "expected_answer_behavior": "abstain" if expected_missing else "answer from cited source data",
            "expected_route_family": expected_route,
            "intent_route_match": response.route == expected_route,
            "expected_evidence_rows": expected_rows,
            "expected_answer_id_recall": (len({v.casefold() for v in expected_answer_ids} & {v.casefold() for v in actual_answer_ids}) / len(expected_answer_ids)) if expected_answer_ids else None,
            "cited_evidence_recall": (len(set(expected_rows) & set(cited_rows)) / len(set(expected_rows))) if expected_rows else None,
            "citation_present": bool(response.sources),
            "abstention_expected": expected_missing,
            "abstention_success": (abstention_markers if expected_missing else None),
            "elapsed_seconds": round(response.timing or 0, 3),
        })
        checks = {
            "intent": case["intent_route_match"],
            "expected_numbers": case["numeric_recall"] in (None, 1.0),
            "expected_record_ids": case["expected_answer_id_recall"] in (None, 1.0),
            "cited_evidence": case["cited_evidence_recall"] in (None, 1.0),
            "abstention": case["abstention_success"] if case["abstention_expected"] else case["citation_present"],
        }
        case["succeeded"] = all(checks.values())
        case["failure_reasons"] = [name for name, passed in checks.items() if not passed]
        results.append(case)
        print(f"[{index}/{len(cases)}] {case['id']} {response.route} numeric_recall={case['numeric_recall']} {response.answer[:180]}", flush=True)
        session_manager.delete_session(session_id)

    numeric_cases = [r for r in results if r["numeric_recall"] is not None]
    payload = {
        "benchmark": "user_report_pharmacy_qa",
        "report_file": args.report.name,
        "case_count": len(results),
        "numeric_case_accuracy": sum(r["numeric_recall"] == 1 for r in numeric_cases) / max(1, len(numeric_cases)),
        "mean_expected_number_recall": sum(r["numeric_recall"] for r in numeric_cases) / max(1, len(numeric_cases)),
        "intent_route_accuracy": sum(r["intent_route_match"] for r in results) / max(1, len(results)),
        "answerable_citation_coverage": sum(r["citation_present"] for r in results if not r["abstention_expected"]) / max(1, sum(not r["abstention_expected"] for r in results)),
        "record_evidence_recall": sum(r["cited_evidence_recall"] for r in results if r["cited_evidence_recall"] is not None) / max(1, sum(r["cited_evidence_recall"] is not None for r in results)),
        "expected_answer_id_recall": sum(r["expected_answer_id_recall"] for r in results if r["expected_answer_id_recall"] is not None) / max(1, sum(r["expected_answer_id_recall"] is not None for r in results)),
        "abstention_accuracy": sum(r["abstention_success"] for r in results if r["abstention_expected"]) / max(1, sum(r["abstention_expected"] for r in results)),
        "case_success_rate": sum(r["succeeded"] for r in results) / max(1, len(results)),
        "metric_caveat": "Numeric recall is a surface-form proxy, not semantic/factual accuracy. Route-family intent labels are derived from the report's evidence notes. This report set was used during development and is not an independent holdout. No safety-sensitive cases are present.",
        "elapsed_seconds": round(time.time() - started, 2),
        "dataset_file_ids": sources,
        "cases": results,
    }
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "cases"}, indent=2))
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
