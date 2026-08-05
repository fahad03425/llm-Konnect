"""Module 6.4 — API routes for Knowledge Base.

FIX (BUG 5): validate() is now called after mapping so only validated rows
              enter the knowledge base, matching the spec's "canonical, validated
              table" requirement.
FIX (BUG 6): a module-level singleton `_kb` is used across all requests so the
              embedding model and Chroma client are loaded once per process
              lifetime, not once per request.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os

from app.connectors.base import detect_connector
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping
from app.schema.validate import validate
from app.schema.domain import get_domain_pack
from app.ingestion.store import KnowledgeBase

router = APIRouter(prefix="/api/kb", tags=["knowledge_base"])

# FIX (BUG 6): module-level singleton — embedder + Chroma client are lazy-loaded
# once on first use and reused for all subsequent requests in this process.
_kb = KnowledgeBase()


class IngestRequest(BaseModel):
    file_path: str
    mapping: Optional[Dict[str, str]] = None
    domain: str = "pharmacy"
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    strategy: str = "row"
    merge_key: Optional[str] = None

class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    filters: Optional[Dict[str, Any]] = None
    domain: str = "pharmacy"

@router.post("/ingest")
def ingest_source(req: IngestRequest):
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs['sheet_name'] = req.sheet_name
        if req.table_or_query:
            kwargs['table_or_query'] = req.table_or_query
            
        df = connector.fetch(**kwargs)
        if df.empty:
            raise ValueError("The source file is empty.")
            
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack)
            
        canonical_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=True)

        # FIX (BUG 5): validate the canonical DataFrame; warn if not usable
        report = validate(canonical_df, domain=req.domain)
        if not report.is_usable:
            raise ValueError(
                f"Source file failed validation (verdict: {report.verdict}). "
                f"Fix errors before ingesting. Errors: "
                + "; ".join(p.message for p in report.errors)
            )

        source_meta = {
            "source_file": req.file_path,
            "source_connector": connector.__class__.__name__
        }
        
        summary = _kb.add_dataframe(
            canonical_df,
            source_meta=source_meta,
            domain=req.domain,
            strategy=req.strategy,
            merge_key=req.merge_key,
        )
        result = summary.model_dump()
        # Surface warnings so callers know about data quality
        if report.warnings:
            result["validation_warnings"] = [p.message for p in report.warnings]
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/search")
def search_kb(req: SearchRequest):
    try:
        results = _kb.search(query=req.query, top_k=req.top_k, filters=req.filters, domain=req.domain)
        return [r.model_dump() for r in results]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/stats")
def get_stats():
    try:
        return _kb.stats()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/source")
def delete_source(source_file: str = Query(...)):
    try:
        _kb.delete_source(source_file)
        return {"status": "success", "message": f"Deleted chunks for {source_file}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
