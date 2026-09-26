"""Module 6.4 — API routes for Knowledge Base.

FIX (BUG 5): validate() is now called after mapping so only validated rows
              enter the knowledge base, matching the spec's "canonical, validated
              table" requirement.
FIX (BUG 6): a module-level singleton `_kb` is used across all requests so the
              embedding model and Chroma client are loaded once per process
              lifetime, not once per request.
"""
from fastapi import APIRouter, HTTPException, Query, Path
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import os

from app.core.config import get_default_domain

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
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    strategy: str = "row"
    merge_key: Optional[str] = None
    file_id: Optional[str] = None

class IngestDatabaseRequest(BaseModel):
    connection_string: str
    db_type: str = "sqlite"
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    tables: Optional[List[str]] = None
    strategy: str = "merge"
    merge_key: Optional[str] = None
    table_mappings: Optional[Dict[str, Dict[str, str]]] = None

class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    filters: Optional[Dict[str, Any]] = None
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
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
        if target_path:
            file_registry.delete_file(target_path)
        
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

@router.post("/ingest-database")
def ingest_sql_database(req: IngestDatabaseRequest):
    """Batch ingest all discovered or selected tables from a database in a single step."""
    from app.connectors.sql import SQLConnector
    try:
        connector = SQLConnector(connection_string=req.connection_string, db_type=req.db_type)
        database_name = connector.extract_db_name()
        
        tables_to_ingest = req.tables
        if not tables_to_ingest:
            tables_to_ingest = connector.list_tables()
            
        if not tables_to_ingest:
            raise ValueError(f"No user tables found in database '{database_name}'.")

        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass

        total_chunks = 0
        total_rows = 0
        table_results = []
        total_tables = len(tables_to_ingest)
        
        for idx, table_name in enumerate(tables_to_ingest):
            table_path = f"sql://{database_name}/{table_name}"
            file_id = f"db_{database_name}_{table_name}".replace(" ", "_").replace("-", "_").replace(".", "_").lower()
            
            # Update progress in registry
            progress_pct = round((idx / total_tables) * 100, 1)
            file_registry.set_file_status(
                file_path=table_path,
                file_id=file_id,
                domain=req.domain,
                strategy=req.strategy,
                progress=progress_pct,
                step_text=f"Ingesting table {idx+1}/{total_tables}: {table_name}",
                group_name=database_name,
                source_type="database",
                table_name=table_name,
                filename_override=f"{database_name} — {table_name}",
                status="processing"
            )
            
            try:
                df = connector.fetch(table_or_query=table_name)
                if df.empty:
                    table_results.append({
                        "table_name": table_name,
                        "status": "empty",
                        "rows": 0,
                        "chunks": 0
                    })
                    file_registry.register_or_update(
                        file_path=table_path,
                        chunk_count=0,
                        domain=req.domain,
                        strategy=req.strategy,
                        file_id=file_id,
                        group_name=database_name,
                        source_type="database",
                        table_name=table_name,
                        filename_override=f"{database_name} — {table_name}"
                    )
                    continue

                # Mapping
                mapping = None
                if req.table_mappings and table_name in req.table_mappings:
                    mapping = req.table_mappings[table_name]
                if mapping is None:
                    mapping = map_headers(list(df.columns), domain_pack)

                canonical_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=True)
                
                # Delete old chunks for this table if re-ingesting
                _kb.delete_source(file_id)
                _kb.delete_source(table_path)

                source_meta = {
                    "source_file": table_path,
                    "filename": f"{database_name} — {table_name}",
                    "source_connector": f"sql_{req.db_type}",
                    "database_name": database_name,
                    "table_name": table_name,
                    "group_name": database_name,
                    "source_type": "database"
                }

                summary = _kb.add_dataframe(
                    canonical_df,
                    source_meta=source_meta,
                    domain=req.domain,
                    strategy=req.strategy,
                    merge_key=req.merge_key,
                    file_id=file_id
                )

                file_registry.register_or_update(
                    file_path=table_path,
                    chunk_count=summary.total_chunks,
                    domain=req.domain,
                    strategy=req.strategy,
                    file_id=file_id,
                    group_name=database_name,
                    source_type="database",
                    table_name=table_name,
                    filename_override=f"{database_name} — {table_name}"
                )

                total_chunks += summary.total_chunks
                total_rows += len(df)
                table_results.append({
                    "table_name": table_name,
                    "status": "success",
                    "rows": len(df),
                    "chunks": summary.total_chunks
                })
            except Exception as table_err:
                import traceback
                err_details = traceback.format_exc()
                file_registry.set_file_status(
                    file_path=table_path,
                    file_id=file_id,
                    status="failed",
                    error_message=str(table_err),
                    domain=req.domain,
                    group_name=database_name,
                    source_type="database",
                    table_name=table_name,
                    filename_override=f"{database_name} — {table_name}"
                )
                table_results.append({
                    "table_name": table_name,
                    "status": "error",
                    "error": str(table_err),
                    "traceback": err_details,
                    "rows": 0,
                    "chunks": 0
                })

        successful_tables = [t for t in table_results if t["status"] == "success"]
        empty_tables = [t for t in table_results if t["status"] == "empty"]
        error_tables = [t for t in table_results if t["status"] == "error"]

        if len(successful_tables) > 0:
            msg = f"Successfully ingested {len(successful_tables)} tables ({total_chunks} chunks, {total_rows} rows) from database '{database_name}'."
            if empty_tables:
                msg += f" ({len(empty_tables)} empty tables skipped)."
            # Automatically save connection for continuous background auto-sync (<2ms DMV check)
            file_registry.save_db_connection(
                database_name=database_name,
                connection_string=req.connection_string,
                db_type=req.db_type,
                domain=req.domain,
                strategy=req.strategy,
                auto_sync=1,
                sync_interval_sec=15,
                table_count=len(successful_tables),
                row_count=total_rows
            )
        elif len(error_tables) > 0:
            msg = f"0 tables ingested from database '{database_name}'. {len(error_tables)} tables encountered errors."
        else:
            msg = f"0 tables ingested: all {len(empty_tables)} selected tables in database '{database_name}' contain 0 rows."

        return {
            "success": len(successful_tables) > 0 or len(error_tables) == 0,
            "database_name": database_name,
            "db_type": req.db_type,
            "total_tables": len(tables_to_ingest),
            "successful_tables": len(successful_tables),
            "empty_tables": len(empty_tables),
            "error_tables": len(error_tables),
            "total_rows": total_rows,
            "total_chunks": total_chunks,
            "table_results": table_results,
            "message": msg
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/database/{database_name}")
def delete_database_group(database_name: str = Path(..., description="The database group name to delete")):
    """Delete all tables belonging to a database group from Chroma and registry."""
    try:
        # Find all files belonging to this group
        records = [f for f in file_registry.list_files(include_all=True) if f.group_name and f.group_name.lower() == database_name.lower()]
        for r in records:
            _kb.delete_source(r.file_id)
            _kb.delete_source(r.file_path)
        
        deleted_count = file_registry.delete_group(database_name)
        file_registry.delete_db_connection(database_name)
        return {
            "status": "success",
            "message": f"Successfully deleted database group '{database_name}' ({deleted_count} tables removed).",
            "deleted_tables": deleted_count
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sync-database/{database_name}")
def sync_database_on_demand(database_name: str = Path(..., description="Database name to sync")):
    """Manually trigger immediate sync of all tables for a specific database."""
    from app.ingestion.sync_worker import sync_worker
    try:
        res = sync_worker.sync_database_now(database_name)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/database-connections")
def list_database_connections():
    """List all registered database connections and their auto-sync status."""
    try:
        conns = file_registry.list_db_connections()
        return {"connections": [c.model_dump() for c in conns]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class UpdateDBConnectionRequest(BaseModel):
    auto_sync: Optional[bool] = None
    sync_interval_sec: Optional[int] = None

@router.patch("/database-connections/{database_name}")
def update_database_connection(database_name: str, req: UpdateDBConnectionRequest):
    """Update auto-sync settings for a database connection."""
    conn = file_registry.get_db_connection(database_name)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Database '{database_name}' connection not found")
    auto_sync_val = (1 if req.auto_sync else 0) if req.auto_sync is not None else conn.auto_sync
    interval_val = req.sync_interval_sec if req.sync_interval_sec is not None else conn.sync_interval_sec
    file_registry.save_db_connection(
        database_name=conn.database_name,
        connection_string=conn.connection_string,
        db_type=conn.db_type,
        domain=conn.domain,
        strategy=conn.strategy,
        auto_sync=auto_sync_val,
        sync_interval_sec=interval_val
    )
    return {"status": "success", "message": f"Updated settings for {database_name}", "auto_sync": bool(auto_sync_val)}

@router.post("/sync-event")
def receive_sync_event(event: Dict[str, Any]):
    """Receive a real-time row change event (INSERT/UPDATE/DELETE) and enqueue for micro-batch sync."""
    from app.ingestion.sync_worker import sync_worker, ChangeEvent
    try:
        event_obj = ChangeEvent(**event)
        return sync_worker.push_event(event_obj)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sync-batch")
def receive_sync_batch(events: List[Dict[str, Any]]):
    """Receive a batch of real-time row change events and enqueue for micro-batch sync."""
    from app.ingestion.sync_worker import sync_worker, ChangeEvent
    try:
        event_objs = [ChangeEvent(**e) for e in events]
        return sync_worker.push_batch(event_objs)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/sync-status")
def get_sync_status():
    """Get the current health, metrics, and queue status of the real-time sync worker."""
    from app.ingestion.sync_worker import sync_worker
    return sync_worker.get_status()


