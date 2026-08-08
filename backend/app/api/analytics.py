"""
Module 6.6 (KPI Engine) — analytics API routes.

Thin transport layer only: connect a source through the existing
connectors -> schema mapping -> validation pipeline, then hand the canonical
DataFrame to `KPIEngine`. All arithmetic lives in `app.analytics`.

No LLM is involved on this path.
"""

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.connectors.base import detect_connector
from app.schema.domain import get_domain_pack
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping
from app.schema.validate import validate

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class AnalyticsFilters(BaseModel):
    """Filter slice applied before any KPI is computed."""

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    category: Optional[str] = None
    product_id: Optional[str] = None
    supplier_id: Optional[str] = None
    customer_id: Optional[str] = None
    txn_type: Optional[str] = None


class KPIRequest(BaseModel):
    file_path: str
    domain: str = "pharmacy"
    mapping: Optional[Dict[str, str]] = None
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    filters: Optional[AnalyticsFilters] = None
    include_validation: bool = Field(
        True, description="Include the validation report so a figure can be judged against data quality."
    )


def _load_canonical(req: KPIRequest):
    """Connector -> mapping -> canonical DataFrame, mirroring /api/sources/validate."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    connector = detect_connector(req.file_path)
    kwargs: Dict[str, Any] = {}
    if req.sheet_name:
        kwargs["sheet_name"] = req.sheet_name
    if req.table_or_query:
        kwargs["table_or_query"] = req.table_or_query

    raw = connector.fetch(**kwargs)

    domain_pack = None
    try:
        domain_pack = get_domain_pack(req.domain)
    except ValueError:
        pass

    mapping = req.mapping or map_headers(list(raw.columns), domain_pack)
    canonical = apply_mapping(raw, mapping, domain=req.domain, keep_extras=True)
    return canonical, mapping


def _filters(req: KPIRequest) -> KPIFilters:
    return KPIFilters.from_dict(req.filters.dict() if req.filters else None)


def _envelope(req: KPIRequest, canonical, mapping: Dict[str, str]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "file_path": req.file_path,
        "domain": req.domain,
        "mapping_used": mapping,
        "total_rows": int(len(canonical)),
    }
    if req.include_validation:
        body["validation_report"] = validate(canonical, domain=req.domain).to_dict()
    return body


@router.post("/kpis")
def compute_kpi_pack(req: KPIRequest):
    """Compute the full core KPI pack (plus any domain-registered KPIs) with provenance."""
    try:
        canonical, mapping = _load_canonical(req)
        results = engine.compute_all(canonical, _filters(req), domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["kpis"] = {key: result.to_dict() for key, result in results.items()}
        return body
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/kpi/{key}")
def compute_single_kpi(key: str, req: KPIRequest):
    """Compute one KPI by key."""
    if not engine.has(key):
        raise HTTPException(
            status_code=404,
            detail=f"Unknown KPI '{key}'. Available: {[s.key for s in engine.list_kpis(req.domain)]}",
        )
    try:
        canonical, mapping = _load_canonical(req)
        result = engine.compute(key, canonical, _filters(req), domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["kpi"] = result.to_dict()
        return body
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/kpis")
def list_available_kpis(domain: str = "pharmacy") -> Dict[str, List[Dict[str, Any]]]:
    """List registered KPIs (core + those registered for `domain`) and their definitions."""
    return {"kpis": [spec.to_dict() for spec in engine.list_kpis(domain)]}
