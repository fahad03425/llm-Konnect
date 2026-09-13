"""Module 6.4 — API routes for Knowledge Base.

FIX (BUG 5): validate() is now called after mapping so only validated rows
              enter the knowledge base, matching the spec's "canonical, validated
              table" requirement.
FIX (BUG 6): a module-level singleton `_kb` is used across all requests so the
              embedding model and Chroma client are loaded once per process
              lifetime, not once per request.
"""
from fastapi import APIRouter, HTTPException, Query, Path
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import os

from app.connectors.base import detect_connector
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping
from app.schema.validate import validate
from app.schema.domain import get_domain_pack
from app.ingestion.store import KnowledgeBase
from app.ingestion.registry import file_registry

router = APIRouter(prefix="/api/kb", tags=["knowledge_base"])

_kb = KnowledgeBase()


class IngestRequest(BaseModel):
    file_path: str
    mapping: Optional[Dict[str, str]] = None
    domain: str = "pharmacy"
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    strategy: str = "row"
    merge_key: Optional[str] = None
    file_id: Optional[str] = None

class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    filters: Optional[Dict[str, Any]] = None
    domain: str = "pharmacy"
    file_ids: Optional[List[str]] = None
    source_files: Optional[List[str]] = None

@router.get("/sources")
def list_ingested_sources(domain: Optional[str] = None):
    """List all registered ingested files with chunk counts, domain, and status."""
    try:
        files = file_registry.list_files(domain=domain)
        stats = _kb.stats()
        return {
            "files": [f.model_dump() for f in files],
            "total_files": len(files),
            "total_chunks": stats.get("total_chunks", 0),
            "collection_name": stats.get("collection_name", "llm_konnect_kb")
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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

        report = validate(canonical_df, domain=req.domain)
        if not report.is_usable:
            raise ValueError(
                f"Source file failed validation (verdict: {report.verdict}). "
                f"Fix errors before ingesting. Errors: "
                + "; ".join(p.message for p in report.errors)
            )

        # Check if identical duplicate file content is already ingested under another file
        file_hash = file_registry.calculate_hash(req.file_path)
        existing_by_hash = file_registry.get_file_by_hash(file_hash)
        if existing_by_hash and existing_by_hash.status == "active" and existing_by_hash.chunk_count > 0:
            norm_existing = existing_by_hash.file_path.replace("\\", "/").lower()
            norm_req = req.file_path.replace("\\", "/").lower()
            if norm_existing != norm_req and (not req.file_id or req.file_id != existing_by_hash.file_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"Duplicate content: This file is identical to '{existing_by_hash.filename}', which is already ingested ({existing_by_hash.chunk_count} chunks)."
                )

        # Check existing registration or assign ID
        existing_reg = file_registry.get_file_by_path(req.file_path)
        active_file_id = req.file_id or (existing_reg.file_id if existing_reg else None)

        # If re-ingesting an existing file, clean up old chunks first
        if active_file_id:
            _kb.delete_source(active_file_id)
        _kb.delete_source(req.file_path)

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
            file_id=active_file_id,
        )

        # Register in file registry
        reg_record = file_registry.register_or_update(
            file_path=req.file_path,
            chunk_count=summary.total_chunks,
            domain=req.domain,
            strategy=req.strategy,
            file_id=active_file_id
        )
        
        result = summary.model_dump()
        result["file_id"] = reg_record.file_id
        result["filename"] = reg_record.filename

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
        results = _kb.search(
            query=req.query,
            top_k=req.top_k,
            filters=req.filters,
            domain=req.domain,
            file_ids=req.file_ids,
            source_files=req.source_files
        )
        return [r.model_dump() for r in results]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/stats")
def get_stats():
    try:
        return _kb.stats()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/sources/{file_id}")
def uningest_source_by_id(file_id: str = Path(..., description="The unique file ID or path to un-ingest")):
    """Granularly un-ingest a file from the knowledge base and remove from registry."""
    try:
        record = file_registry.get_file_by_id(file_id)
        target_path = record.file_path if record else file_id
        
        # Delete from Chroma
        _kb.delete_source(file_id)
        if target_path:
            _kb.delete_source(target_path)
            
        # Delete from SQLite registry
        file_registry.delete_file(file_id)
        
        return {
            "status": "success",
            "message": f"Successfully un-ingested {record.filename if record else file_id}",
            "file_id": file_id
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/source")
def delete_source(source_file: str = Query(...)):
    """Legacy source deletion by path or ID."""
    try:
        _kb.delete_source(source_file)
        file_registry.delete_file(source_file)
        return {"status": "success", "message": f"Deleted chunks for {source_file}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
