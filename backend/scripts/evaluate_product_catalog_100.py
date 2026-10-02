"""Run the supplied 100-question catalog suite through the application chat path."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.rag.chat import RAGChat
from app.rag.models import ChatRequest
from eval_questions_data import QUESTIONS, compute_ground_truth
from run_100_evaluation import evaluate_accuracy

FILE_ID = "file_ae2e68f26f42"
OUT = ROOT / "reports" / "product_catalog_100_results.json"


def main() -> None:
    chat = RAGChat()
    run_id = uuid.uuid4().hex[:10]
    results = []
    started = time.time()
    for qid, question in QUESTIONS:
        response = chat.ask(ChatRequest(
            question=question,
            session_id=f"catalog_eval_{run_id}_{qid:03d}",
            domain="pharmacy",
            file_ids=[FILE_ID],
        ))
        data = {
            "answer": response.answer,
            "route": response.route,
            "sources": [source.model_dump() for source in response.sources],
            "computed_values": response.computed_values or {},
        }
        gt = compute_ground_truth(qid, question)
        legacy = evaluate_accuracy(qid, question, data, gt)
        expected_clarification = qid in {9, 10, 21, 31, 32, 38, 39, 40, 41, 49, 50, 51, 53, 54, 55, 57, 59, 60, 88}
        results.append({
            "id": qid,
            "question": question,
            "intended_meaning": {"question_goal": question, "legacy_ground_truth": gt},
            "expected_evidence": "selected workbook rows matching the product, company, filters, or calculation; no external clinical/legal evidence is present in this price catalog",
            "expected_behavior": "clarify missing referent/entity" if expected_clarification else "answer from selected catalog evidence",
            "route": response.route,
            "answer": response.answer,
            "citation_count": len(response.sources),
            "source_rows": [s.source_row for s in response.sources],
            "computed_values": response.computed_values or {},
            "legacy_heuristic": legacy,
            "legacy_heuristic_success": legacy.get("score", 0.0) >= 1.0,
            "clarification_behavior_success": ("Which " in response.answer or "Please provide" in response.answer) if expected_clarification else None,
        })
        print(f"{qid:03d}/100 {response.route:9s} citations={len(response.sources):2d} {question}")

    # Small development-only additions from the latest failure-analysis round.
    # They are regression probes, not an independent or blind benchmark.
    challenge_cases = [
        (101, "What's the price of Zestil?", "transparent_fuzzy_entity_match"),
        (102, "What is the price of Zestril 10 mg?", "abstain_on_unrepresented_strength"),
        (103, "Compare the prices of Zestril vs Zofran 8mg.", "compare_both_named_products"),
    ]
    challenge_results = []
    for qid, question, expected in challenge_cases:
        response = chat.ask(ChatRequest(
            question=question,
            session_id=f"catalog_eval_{run_id}_challenge_{qid}",
            domain="pharmacy",
            file_ids=[FILE_ID],
        ))
        computed = response.computed_values or {}
        value = next((item.get("value", {}) for item in computed.values() if isinstance(item, dict)), {})
        if expected == "transparent_fuzzy_entity_match":
            success = value.get("fuzzy_name_match") is True and value.get("product") == "Zestril" and bool(response.sources)
        elif expected == "abstain_on_unrepresented_strength":
            success = value.get("status") == "unsupported_strength" and "can't confirm" in response.answer.casefold()
        else:
            success = "Zestril:" in response.answer and "Zofran 8mg:" in response.answer and bool(response.sources)
        challenge_results.append({
            "id": qid, "question": question, "intended_meaning": expected,
            "expected_evidence": "matching product rows in the selected catalog, or explicit absence of a strength field",
            "expected_behavior": expected, "answer": response.answer,
            "source_rows": [s.source_row for s in response.sources], "success": success,
        })

    legacy_pass = sum(row["legacy_heuristic_success"] for row in results)
    clarification_cases = [r for r in results if r["clarification_behavior_success"] is not None]
    citation_cases = [r for r in results if not r["clarification_behavior_success"]]
    payload = {
        "dataset": "Pakistan_Pharmaceutical_Products_Pricing_and_Availability_Data.xlsx",
        "file_id": FILE_ID,
        "case_count": len(results),
        "supplemental_development_case_count": len(challenge_results),
        "run_id": run_id,
        "elapsed_seconds": round(time.time() - started, 2),
        "legacy_heuristic": {
            "passed": legacy_pass,
            "total": len(results),
            "rate": legacy_pass / max(1, len(results)),
            "warning": "The inherited scorer accepts weak keyword matches and sometimes treats any citation as correctness; this is a comparable smoke metric, not independently verified factual accuracy.",
        },
        "citation_coverage_answerable_cases": {
            "with_citations": sum(bool(r["citation_count"]) for r in citation_cases),
            "total": len(citation_cases),
        },
        "clarification_cases": {
            "correct": sum(bool(r["clarification_behavior_success"]) for r in clarification_cases),
            "total": len(clarification_cases),
            "warning": "Several inherited cases describe a deictic query as if it had an active product; each case is deliberately isolated into a fresh session, so clarification is the safe expected behavior.",
        },
        "measured_metrics": {
            "intent_accuracy": None,
            "factual_accuracy": None,
            "source_support_accuracy": None,
            "missing_context_clarification": f"{sum(bool(r['clarification_behavior_success']) for r in clarification_cases)}/{len(clarification_cases)}",
            "answerable_case_citation_coverage": f"{sum(bool(r['citation_count']) for r in citation_cases)}/{len(citation_cases)}",
            "retrieval_recall_and_ranking": "Not measured by this catalog run: calculations execute over the complete selected table, while ordinary semantic RAG ranking needs its own independently labeled relevance set.",
            "paraphrase_consistency": "Not measured as a controlled paraphrase set.",
            "safety_sensitive_accuracy": "Not applicable: this price and availability question set contains no clinical, patient-specific, legal, or recall cases.",
            "supplemental_development_probes": f"{sum(c['success'] for c in challenge_results)}/{len(challenge_results)}",
            "scoring_limit": "Intent/factual/source-support accuracy are deliberately null because the inherited keyword scorer accepts weak matches and the raw workbook ground truth misclassifies some availability rows. A larger independent annotated benchmark is required.",
        },
        "results": results,
        "supplemental_development_cases": challenge_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Legacy heuristic: {legacy_pass}/{len(results)} ({legacy_pass / max(1, len(results)):.1%})")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
