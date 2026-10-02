"""Measure the real ingestion + Chroma retrieval path on sample inventory data."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.ingestion import store as store_module
from app.ingestion.store import KnowledgeBase

EVAL = ROOT / "backend/tests/fixtures/pharmacy_retrieval_eval.json"
SOURCE = ROOT / "data/samples/challenging_pharma_inventory.csv"
OUT = ROOT / "backend/pharmacy_retrieval_results.json"


def main() -> int:
    import chromadb

    # In-memory Chroma prevents benchmark data from polluting the user's KB.
    client = chromadb.EphemeralClient()
    store_module._get_persistent_chroma_client = lambda _path: client
    raw = pd.read_csv(SOURCE)
    records = pd.DataFrame({
        "product_id": raw["Item Name"].astype(str),
        "generic_name": raw["Generic"].astype(str),
        "batch_no": raw["Batch No"].astype(str),
        "expiry_date": raw["Exp Date"].astype(str),
        "stock_qty": pd.to_numeric(raw["Stock"], errors="coerce"),
        "reorder_level": pd.to_numeric(raw["Reorder"], errors="coerce"),
        "rack_location": raw["Rack"].astype(str),
        "manufacturer": raw["Company"].astype(str),
        "mrp": pd.to_numeric(raw["MRP"], errors="coerce"),
        "unit_price": pd.to_numeric(raw["TP"], errors="coerce"),
        "source_row": list(range(2, len(raw) + 2)),
    })
    kb = KnowledgeBase(chroma_dir=":memory:", collection_name="pharmacy_retrieval_eval")
    kb.add_dataframe(
        records,
        source_meta={"source_file": SOURCE.name, "source_connector": "evaluation_fixture"},
        domain="pharmacy",
        file_id="retrieval-eval-fixture",
    )

    fixture = json.loads(EVAL.read_text(encoding="utf-8"))
    results = []
    for case in fixture["cases"]:
        found = kb.search(case["q"], top_k=5, domain="pharmacy", file_ids=["retrieval-eval-fixture"])
        ranks = [i for i, chunk in enumerate(found, 1) if chunk.metadata.get("product_id") == case["product"]]
        rank = min(ranks) if ranks else None
        results.append({
            "q": case["q"], "expected_product": case["product"], "expected_rank": rank,
            "retrieved_products": [chunk.metadata.get("product_id") for chunk in found],
            "recall_at_5": rank is not None,
            "reciprocal_rank": 1 / rank if rank else 0.0,
            "ndcg_at_5": 1 / __import__("math").log2(rank + 1) if rank else 0.0,
        })
    metrics = {
        "n": len(results),
        "recall_at_5": sum(r["recall_at_5"] for r in results) / max(1, len(results)),
        "mrr_at_5": sum(r["reciprocal_rank"] for r in results) / max(1, len(results)),
        "ndcg_at_5": sum(r["ndcg_at_5"] for r in results) / max(1, len(results)),
    }
    OUT.write_text(json.dumps({"suite": fixture["name"], "metrics": metrics, "cases": results}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    for result in results:
        if not result["recall_at_5"] or result["expected_rank"] != 1:
            print("RANK", json.dumps(result, ensure_ascii=False))
    print(f"Results: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
