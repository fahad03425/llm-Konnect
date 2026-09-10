"""Module 1.4 — API routes. Month 2."""
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import shutil
import pandas as pd

from app.connectors.base import detect_connector
from app.schema.mapper import map_headers, suggest_mapping
from app.schema.normalize import normalize, apply_mapping
from app.schema.validate import validate, clean
from app.schema.domain import get_domain_pack
from app.schema.profile import find_profile, save_profile, source_signature

router = APIRouter(prefix="/api/sources", tags=["sources"])

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the server for easy testing in Swagger UI."""
    # Project root (llm-konnect)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    upload_dir = os.path.join(base_dir, "data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    # Secure the filename or just use it directly for testing
    safe_filename = file.filename.replace("/", "").replace("\\", "")
    file_path = os.path.join(upload_dir, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Return with normalized slashes so it can be easily copied to other endpoints
    normalized_path = file_path.replace("\\", "/")
    return {
        "message": "File uploaded successfully",
        "file_path": normalized_path,
        "filename": safe_filename
    }

class PreviewRequest(BaseModel):
    file_path: str
    n: int = 5
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    domain: str = "pharmacy"

class MappingConfirmRequest(BaseModel):
    file_path: str
    mapping: Dict[str, str]
    domain: str = "pharmacy"
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    keep_extras: bool = True
    save_profile: bool = True

class NormalizeRequest(BaseModel):
    file_path: str
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    domain: str = "pharmacy"
    mapping: Optional[Dict[str, str]] = None 

class ValidateRequest(BaseModel):
    file_path: str
    mapping: Optional[Dict[str, str]] = None
    domain: str = "pharmacy"
    table_kind: str = "auto"
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None

class CleanRequest(BaseModel):
    file_path: str
    mapping: Optional[Dict[str, str]] = None
    domain: str = "pharmacy"
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    options: Optional[Dict[str, Any]] = None

@router.post("/preview")
def preview_source(req: PreviewRequest):
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        connector = detect_connector(req.file_path)
        
        kwargs = {}
        if req.sheet_name:
            kwargs['sheet_name'] = req.sheet_name
        if req.table_or_query:
            kwargs['table_or_query'] = req.table_or_query
            
        df = connector.preview(n=req.n, **kwargs)
        df = df.astype(object).where(pd.notnull(df), None)
        
        columns = list(df.columns)
        sample_rows = df.to_dict(orient="records")
        
        # Check for saved profile
        sig = source_signature(columns, connector.__class__.__name__)
        saved_profile = find_profile(sig)
        
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        # Generate mapping proposal
        proposal = suggest_mapping(columns, sample_rows, domain_pack)
        
        return {
            "connector_description": connector.describe(),
            "columns": columns,
            "data": sample_rows,
            "signature": sig,
            "saved_profile": saved_profile,
            "mapping_proposal": proposal.dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/mapping/confirm")
def confirm_mapping(req: MappingConfirmRequest):
    """Confirm a mapping, save profile, and return canonical preview."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs['sheet_name'] = req.sheet_name
        if req.table_or_query:
            kwargs['table_or_query'] = req.table_or_query
            
        # Get preview for quick verification
        df = connector.preview(n=10, **kwargs)
        
        if req.save_profile:
            sig = source_signature(list(df.columns), connector.__class__.__name__)
            label = f"{connector.__class__.__name__} - {os.path.basename(req.file_path)}"
            save_profile(sig, req.mapping, label)
            
        canonical_df = apply_mapping(df, req.mapping, req.domain, req.keep_extras)
        canonical_df = canonical_df.astype(object).where(pd.notnull(canonical_df), None)
        
        return {
            "mapped_columns": list(canonical_df.columns),
            "data_preview": canonical_df.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sheets")
def list_sheets(file_path: str = Query(...)):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        connector = detect_connector(file_path)
        if not connector.capabilities().get("multi_sheet"):
            return {"sheets": []}
            
        return {"sheets": connector.list_sheets()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/normalize")
def normalize_source(req: NormalizeRequest):
    """Legacy normalize endpoint."""
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
            return {"columns": [], "data": [], "problems": []}
            
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack)
            
        norm_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=False)
        report = validate(norm_df, domain=req.domain)
        
        norm_df = norm_df.astype(object).where(pd.notnull(norm_df), None)
        
        return {
            "mapped_columns": list(norm_df.columns),
            "mapping_used": mapping,
            "data_preview": norm_df.head(10).to_dict(orient="records"),
            "problems": [p.to_dict() for p in report.problems],
            "validation_report": report.to_dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/validate")
def validate_source(req: ValidateRequest):
    """Run mapping + validation on a connected source file and return the ValidationReport."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs["sheet_name"] = req.sheet_name
        if req.table_or_query:
            kwargs["table_or_query"] = req.table_or_query

        df = connector.fetch(**kwargs)
        if df.empty:
            from app.schema.validate import ValidationReport
            return ValidationReport(
                total_rows=0, error_rows=0, warning_rows=0, null_counts={}, verdict="not_usable", problems=[]
            ).to_dict()

        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass

        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack)

        canonical_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=True)
        report = validate(canonical_df, domain=req.domain, table_kind=req.table_kind)
        return report.to_dict()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/clean")
def clean_source(req: CleanRequest):
    """Run opt-in cleaning pass on a connected source file."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs["sheet_name"] = req.sheet_name
        if req.table_or_query:
            kwargs["table_or_query"] = req.table_or_query

        df = connector.fetch(**kwargs)
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass

        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack)

        canonical_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=True)
        cleaned_df, summary = clean(canonical_df, options=req.options)

        cleaned_df = cleaned_df.astype(object).where(pd.notnull(cleaned_df), None)

        return {
            "cleaned_preview": cleaned_df.head(10).to_dict(orient="records"),
            "cleaning_summary": summary.to_dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class SQLConnectRequest(BaseModel):
    connection_string: str
    db_type: str = "sqlite"
    table_or_query: str
    n: int = 5
    watermark_column: Optional[str] = None
    watermark_value: Optional[Any] = None
    domain: str = "pharmacy"

class WatcherConfigRequest(BaseModel):
    watch_dir: str
    file_pattern: str = "*.*"
    domain: str = "pharmacy"

@router.get("/domains")
def list_available_domains():
    from app.schema.domain import registry
    return {"domains": registry.available_domains()}

@router.post("/sql/preview")
def preview_sql_source(req: SQLConnectRequest):
    try:
        from app.connectors.sql import SQLConnector
        connector = SQLConnector(connection_string=req.connection_string, db_type=req.db_type)
        df = connector.preview(n=req.n, table_or_query=req.table_or_query)
        df = df.astype(object).where(pd.notnull(df), None)
        
        columns = list(df.columns)
        sample_rows = df.to_dict(orient="records")
        
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        proposal = suggest_mapping(columns, sample_rows, domain_pack)
        
        return {
            "connector_description": connector.describe(),
            "columns": columns,
            "data": sample_rows,
            "mapping_proposal": proposal.dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/watcher/list")
def list_watcher_files(req: WatcherConfigRequest):
    try:
        from app.connectors.watcher import DirectoryWatcherConnector
        watcher = DirectoryWatcherConnector(watch_dir=req.watch_dir, file_pattern=req.file_pattern)
        pending = watcher.list_pending_files()
        return {
            "watch_dir": req.watch_dir,
            "pending_files": [f.replace("\\", "/") for f in pending]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

