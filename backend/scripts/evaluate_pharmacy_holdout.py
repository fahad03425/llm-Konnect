"""Run offline intent/vocabulary checks for the held-out pharmacy owner set.

This deliberately does not send the holdout questions to an LLM or add them to
the vocabulary anchors. See docs/PHARMACY_EVALUATION.md for metric limits.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.rag.router import classify_route
from app.language.pharmacy_vocabulary import match_pharmacy_concepts

FIXTURE = ROOT / "backend/tests/fixtures/pharmacy_owner_holdout.json"
OUT = ROOT / "backend/pharmacy_holdout_results.json"


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    results = []
    for case in fixture["cases"]:
        expected = set(case["intent"].split("+"))
        detected = set(match_pharmacy_concepts(case["q"]))
        route = classify_route(case["q"])
        results.append({
            "id": case["id"], "route_expected": case["route"], "route_actual": route,
            "route_ok": route == case["route"], "intent_expected": sorted(expected),
            "intent_detected": sorted(detected), "intent_ok": expected <= detected,
            "intent_route_succeeded": route == case["route"] and expected <= detected,
            "chatbot_succeeded_end_to_end": None,
            "evidence": case["evidence"], "behavior": case["behavior"],
            # End-to-end evidence/answer checks require the live scoped data and model.
            "retrieval_evaluated": False, "answer_evaluated": False,
        })
    totals = {
        "n": len(results),
        "route_correct": sum(r["route_ok"] for r in results),
        "intent_concepts_complete": sum(r["intent_ok"] for r in results),
        "retrieval_evaluated": 0,
        "answer_grounding_evaluated": 0,
    }
    totals["route_accuracy"] = totals["route_correct"] / max(1, totals["n"])
    totals["intent_accuracy"] = totals["intent_concepts_complete"] / max(1, totals["n"])
    by_id = {result["id"]: result for result in results}
    pair_results = []
    for left_id, right_id in fixture.get("paraphrase_pairs", []):
        left, right = by_id[left_id], by_id[right_id]
        pair_results.append({
            "ids": [left_id, right_id],
            "expected_intent_matches": left["intent_expected"] == right["intent_expected"],
            "route_consistent": left["route_actual"] == right["route_actual"],
            "concepts_consistent": left["intent_detected"] == right["intent_detected"],
        })
    totals["paraphrase_pairs"] = len(pair_results)
    totals["paraphrase_route_and_concept_consistency"] = (
        sum(p["expected_intent_matches"] and p["route_consistent"] and p["concepts_consistent"] for p in pair_results)
        / len(pair_results) if pair_results else None
    )
    payload_pairs = pair_results
    payload = {"suite": fixture["name"], "totals": totals, "paraphrase_pair_results": payload_pairs, "cases": results}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(totals, indent=2))
    for r in results:
        if not r["route_ok"] or not r["intent_ok"]:
            print(f"FAIL {r['id']}: route {r['route_actual']} vs {r['route_expected']}; concepts {r['intent_detected']} vs {r['intent_expected']}")
    print(f"Results: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
