"""Module 1.4 — API routes. Month 2."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import pandas as pd

from app.connectors.base import detect_connector
from app.schema.mapper import map_headers, suggest_mapping
from app.schema.normalize import normalize, apply_mapping
from app.schema.validate import validate, clean
from app.schema.domain import get_domain_pack
from app.schema.profile import find_profile, save_profile, source_signature

router = APIRouter(prefix="/api/sources", tags=["sources"])

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
        df = df.where(pd.notnull(df), None)
        
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
        canonical_df = canonical_df.where(pd.notnull(canonical_df), None)
        
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
        
        norm_df = norm_df.where(pd.notnull(norm_df), None)
        
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

        cleaned_df = cleaned_df.where(pd.notnull(cleaned_df), None)

        return {
            "cleaned_preview": cleaned_df.head(10).to_dict(orient="records"),
            "cleaning_summary": summary.to_dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
