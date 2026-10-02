"""Run answer-level evaluation with the configured local Ollama model.

Uses an ephemeral Chroma collection and synthetic fixture rows, never the
user's saved knowledge base. This is a development holdout, not a clinical or
legal authority benchmark.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.ingestion import store as store_module
from app.ingestion.store import KnowledgeBase
from app.rag.chat import RAGChat
from app.rag.models import ChatRequest
from app.rag.history import session_manager

FIXTURE = ROOT / "backend/tests/fixtures/pharmacy_answer_eval_holdout.json"
OUTPUT = ROOT / "backend/pharmacy_answer_live_results.json"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


def _contains_any(answer: str, values) -> bool:
    normalized = _norm(answer)
    return any(_norm(value) in normalized for value in values)


def _score_case(case, response):
    answer = response.answer
    normalized = _norm(answer)
    expected_all = case.get("expected_all", [])
    expected_any = case.get("expected_any", [])
    facts_ok = all(_norm(value) in normalized for value in expected_all)
    if expected_any:
        facts_ok = facts_ok and _contains_any(answer, expected_any)
    if case.get("expected_date_any"):
        facts_ok = facts_ok and _contains_any(answer, case["expected_date_any"])
    if case.get("abstention_any"):
        facts_ok = facts_ok and _contains_any(answer, case["abstention_any"])
    if case.get("safety_any"):
        facts_ok = facts_ok and _contains_any(answer, case["safety_any"])
    forbidden = case.get("forbidden_any", [])
    forbidden_ok = not _contains_any(answer, forbidden) if forbidden else True

    cited_rows = [source.source_row for source in response.sources if source.source_row is not None]
    expected_rows = case.get("source_rows", [])
    cited_expected = sorted(set(cited_rows) & set(expected_rows))
    citation_recall = len(cited_expected) / len(expected_rows) if expected_rows else None
    citation_ok = bool(cited_expected) if expected_rows else not response.sources
    return {
        "id": case["id"],
        "behavior": case["behavior"],
        "route": response.route,
        "answer": answer,
        "facts_ok": facts_ok,
        "forbidden_claims_ok": forbidden_ok,
        "citation_ok": citation_ok,
        "citation_recall": citation_recall,
        "expected_source_rows": expected_rows,
        "cited_source_rows": cited_rows,
        "sources": [source.model_dump() for source in response.sources],
        "success": facts_ok and forbidden_ok and citation_ok,
    }


def main() -> int:
    import chromadb
    import requests

    try:
        response = requests.get(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=3)
        response.raise_for_status()
    except Exception as exc:
        print(f"Local Ollama is unavailable at {settings.ollama_host}: {exc}", file=sys.stderr)
        return 2

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    client = chromadb.EphemeralClient()
    store_module._get_persistent_chroma_client = lambda _path: client
    kb = KnowledgeBase(chroma_dir=":memory:", collection_name="pharmacy_answer_eval_live")
    kb.add_dataframe(
        pd.DataFrame(fixture["records"]),
        source_meta={"source_file": "synthetic_pharmacy_answer_fixture.csv", "source_connector": "evaluation_fixture"},
        domain="pharmacy",
        file_id="answer-eval-fixture-v1",
    )
    chat = RAGChat()
    chat.kb = kb

    sessions = {}
    results = []
    ranking = []
    started = time.time()
    for case in fixture["cases"]:
        previous_id = case.get("followup_after")
        if previous_id and previous_id in sessions:
            session_id = sessions[previous_id]
        else:
            session_id = f"pharmacy-live-eval-{case['id']}"
            session_manager.delete_session(session_id)
        sessions[case["id"]] = session_id

        hits = kb.search(case["question"], top_k=5, domain="pharmacy", file_ids=["answer-eval-fixture-v1"])
        found_rows = [chunk.source_row if chunk.source_row is not None else chunk.metadata.get("source_row") for chunk in hits]
        expected_rows = case.get("source_rows", [])
        ranks = [rank for rank, row in enumerate(found_rows, 1) if row in expected_rows]
        ranking.append({"id": case["id"], "expected_source_rows": expected_rows,
                        "retrieved_source_rows": found_rows, "first_relevant_rank": min(ranks) if ranks else None})

        # Keep the live answer path scoped to the isolated fixture, including
        # deterministic analytics. Without this, analytics reads the user's
        # saved KB and contaminates a supposedly synthetic holdout.
        answer = chat.ask(ChatRequest(question=case["question"], session_id=session_id, domain="pharmacy", file_ids=["answer-eval-fixture-v1"]))
        results.append(_score_case(case, answer))

    fact_results = [item for item in results if item["behavior"] in ("factual", "factual_derived", "multi_part", "followup", "conflicting_dated_evidence", "record_status_safety")]
    abstention_results = [item for case, item in zip(fixture["cases"], results) if case.get("abstention_any")]
    safety_results = [item for item in results if item["behavior"] in ("clinical_safety", "legal_safety", "recall_abstain", "adversarial_abstain", "record_status_safety")]
    citation_results = [item for item in results if item["expected_source_rows"]]
    ranked = [item for item in ranking if item["expected_source_rows"]]
    metrics = {
        "n": len(results),
        "overall_success": sum(item["success"] for item in results) / max(1, len(results)),
        "factual_answer_accuracy": sum(item["facts_ok"] for item in fact_results) / max(1, len(fact_results)),
        "missing_information_abstention_accuracy": sum(item["facts_ok"] for item in abstention_results) / max(1, len(abstention_results)),
        "safety_sensitive_accuracy": sum(item["facts_ok"] and item["forbidden_claims_ok"] for item in safety_results) / max(1, len(safety_results)),
        "forbidden_claim_control": sum(item["forbidden_claims_ok"] for item in results) / max(1, len(results)),
        "citation_case_accuracy": sum(item["citation_ok"] for item in citation_results) / max(1, len(citation_results)),
        "citation_source_recall": sum(item["citation_recall"] or 0 for item in citation_results) / max(1, len(citation_results)),
        "retrieval_recall_at_5": sum(item["first_relevant_rank"] is not None for item in ranked) / max(1, len(ranked)),
        "retrieval_mrr_at_5": sum((1 / item["first_relevant_rank"]) if item["first_relevant_rank"] else 0 for item in ranked) / max(1, len(ranked)),
        "retrieval_ndcg_at_5": sum((1 / __import__("math").log2(item["first_relevant_rank"] + 1)) if item["first_relevant_rank"] else 0 for item in ranked) / max(1, len(ranked)),
        "paraphrase_consistency_A01_A02": all(results[index]["facts_ok"] for index in (0, 1)),
        "elapsed_seconds": round(time.time() - started, 2),
        "model": settings.llm_model,
        "ollama_host": settings.ollama_host,
    }
    payload = {"suite": fixture["suite"], "metrics": metrics, "ranking": ranking, "cases": results}
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    for item in results:
        if not item["success"]:
            print("FAIL", json.dumps(item, ensure_ascii=False))
    print(f"Results: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
