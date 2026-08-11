"""
Module 6.6 (KPI Engine) — analytics API routes.

Thin transport layer only: connect a source through the existing
connectors -> schema mapping -> validation pipeline, then hand the canonical
DataFrame to `KPIEngine`. All arithmetic lives in `app.analytics`.

No LLM is involved on this path.
"""

import os
from dataclasses import replace
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.connectors.base import detect_connector
from app.core.config import settings
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
    as_of: Optional[str] = Field(
        None,
        description="Reference date KPIs compute 'now' against (YYYY-MM-DD). Defaults to today.",
    )
    options: Optional[Dict[str, Any]] = Field(
        None,
        description="KPI-specific options, e.g. {\"value_basis\": \"mrp\"} for expiry valuation.",
    )


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


class ExpiryReportRequest(KPIRequest):
    """An expiry report is a KPI request with a value basis and reference date."""

    value_basis: Optional[str] = Field(
        None, description="'cost' (money lost, default) or 'mrp' (revenue foregone)."
    )
    as_of: Optional[str] = Field(
        None, description="Reference date for expiry maths (YYYY-MM-DD). Defaults to today."
    )


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


class TrendRequest(KPIRequest):
    """Trend/forecast request: metric, period size, horizon, optional single product."""

    metric: str = Field("revenue", description="'revenue' or 'units'.")
    granularity: Optional[str] = Field(None, description="daily | weekly | monthly.")
    horizon: Optional[int] = Field(None, description="Periods to forecast ahead.")
    product_id: Optional[str] = Field(
        None, description="Restrict to one product (required for a per-product forecast)."
    )
    as_of: Optional[str] = Field(
        None, description="Reference date; history is truncated here. Defaults to all data."
    )


def _trend_filters(req: "TrendRequest") -> KPIFilters:
    """Fold the trend-specific fields into the shared filter object."""
    filters = _filters(req)
    options = dict(filters.options or {})
    if req.granularity:
        options["granularity"] = req.granularity
    if req.horizon is not None:
        options["horizon"] = req.horizon
    return replace(
        filters,
        options=options,
        as_of=req.as_of or filters.as_of,
        product_id=req.product_id or filters.product_id,
    )


@router.post("/trend")
def trend(req: TrendRequest):
    """Descriptive trend only: observed series, moving average, growth. No prediction."""
    try:
        canonical, mapping = _load_canonical(req)
        key = "units_trend" if req.metric == "units" else "revenue_trend"
        result = engine.compute(key, canonical, _trend_filters(req), domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["metric"] = req.metric
        body["granularity"] = req.granularity or settings.forecast_granularity
        body["trend"] = result.to_dict()
        return body
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/forecast")
def forecast(req: TrendRequest):
    """
    Forecast with uncertainty bands.

    Returns the history, the forecast points with lower/upper bands, and the method
    and tier used. When history is too short or too gappy the result is
    `unavailable` with the reason — that refusal is deliberate, not an error.
    """
    try:
        canonical, mapping = _load_canonical(req)
        filters = _trend_filters(req)

        if req.product_id:
            key = "product_demand_forecast"
        elif req.metric == "units":
            key = "demand_forecast"
        else:
            key = "revenue_forecast"

        result = engine.compute(key, canonical, filters, domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["metric"] = req.metric
        body["granularity"] = req.granularity or settings.forecast_granularity
        body["horizon"] = req.horizon or settings.forecast_horizon
        body["forecast"] = result.to_dict()
        return body
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/expiry-report")
def expiry_report(req: ExpiryReportRequest):
    """
    Convenience report: every expiry KPI registered for the domain, in one call.

    Returns the banded buckets, already-expired value, the near-expiry headline and
    counts, and the by-manufacturer breakdown — each with its own item-level
    breakdown and provenance. Thin: all maths happens in the domain KPI pack.
    """
    try:
        canonical, mapping = _load_canonical(req)

        filters = _filters(req)
        options = dict(filters.options or {})
        if req.value_basis:
            options["value_basis"] = req.value_basis
        filters = replace(
            filters,
            options=options,
            as_of=req.as_of or filters.as_of,
        )

        specs = [s for s in engine.list_kpis(req.domain) if "risk" in s.tags]
        if not specs:
            raise HTTPException(
                status_code=404,
                detail=f"Domain '{req.domain}' registers no expiry KPIs.",
            )

        results = {s.key: engine.compute(s.key, canonical, filters, domain=req.domain) for s in specs}

        body = _envelope(req, canonical, mapping)
        body["reference_date"] = filters.as_of or date.today().isoformat()
        body["value_basis"] = options.get("value_basis", settings.expiry_value_basis)
        body["buckets_days"] = list(settings.expiry_buckets_days)
        body["kpis"] = {key: result.to_dict() for key, result in results.items()}
        return body
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
