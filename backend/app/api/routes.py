"""Module 1.4 — API routes. Month 2."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import pandas as pd

from app.connectors.base import detect_connector
from app.schema.mapper import map_headers
from app.schema.normalize import normalize
from app.schema.validate import validate
from app.schema.domain import get_domain_pack

router = APIRouter(prefix="/api/sources", tags=["sources"])

class PreviewRequest(BaseModel):
    file_path: str
    n: int = 5
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None

class NormalizeRequest(BaseModel):
    file_path: str
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    domain: str = "pharmacy"
    # mapping is a dict of raw_header -> canonical_header
    mapping: Optional[Dict[str, str]] = None 

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
        
        # Replace NaN with None for JSON serialization
        df = df.where(pd.notnull(df), None)
        
        return {
            "connector_description": connector.describe(),
            "columns": list(df.columns),
            "data": df.to_dict(orient="records")
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
    """
    Reads the file, optionally auto-maps headers if mapping is not provided,
    normalizes the data, and runs validation.
    """
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
            pass # fallback to core only
            
        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack)
            
        norm_df = normalize(df, mapping, domain=req.domain)
        
        problems = validate(norm_df, domain=req.domain)
        
        # Replace NaN/NaT for JSON serialization
        norm_df = norm_df.where(pd.notnull(norm_df), None)
        
        return {
            "mapped_columns": list(norm_df.columns),
            "mapping_used": mapping,
            "data_preview": norm_df.head(10).to_dict(orient="records"),
            "problems": [p.to_dict() for p in problems]
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
