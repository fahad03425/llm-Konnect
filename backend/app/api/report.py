"""
Module 4.4 — API endpoints for the Report Generator. Month 5.

Exposes the orchestrator from `app.reporting.report` over HTTP.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from app.analytics.engine import engine
from app.api.analytics import (
    KPIRequest,
    _envelope,
    _filters,
    _load_canonical,
)
from app.core.config import settings
from app.reporting.report import generate_report

router = APIRouter(prefix="/api/report", tags=["report"])


class ReportRequest(KPIRequest):
    """Payload for generating a narrative analytics report."""
    business_name: Optional[str] = None
    max_regeneration_attempts: int = Field(
        1, description="Retry attempts if verification fails. 0 to disable retry."
    )


@router.post("/generate")
def generate(req: ReportRequest):
    """
    Generate a verified HTML analytics report.
    Returns the KPI summary, verification status, and a download URL if successful.
    """
    try:
        # 1. Load canonical data using existing analytics pipeline
        canonical, mapping = _load_canonical(req)
        
        # 2. Compute all ground tools KPIs
        results = engine.compute_all(canonical, _filters(req), domain=req.domain)
        
        # 3. Generate narrative and verify (orchestrator)
        try:
            report_result = generate_report(
                kpi_results=results,
                domain=req.domain,
                business_name=req.business_name,
                max_regeneration_attempts=req.max_regeneration_attempts,
            )
        except RuntimeError as exc:
            # LLM failures are 500s
            raise HTTPException(status_code=500, detail=str(exc))
            
        # 4. Wrap with domain/source metadata using existing envelope
        body = _envelope(req, canonical, mapping)
        
        # 5. Attach report output
        body["report"] = report_result.to_dict()
        
        # 6. Expose download URL if the HTML file was written successfully
        if report_result.html_path:
            filename = Path(report_result.html_path).name
            body["download_url"] = f"/api/report/download/{filename}"
            
        return body

    except HTTPException:
        # Re-raise known API exceptions (e.g. 404 file not found)
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/download/{filename}")
def download_report(filename: str):
    """
    Serve a previously generated HTML report.
    """
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename format.")
        
    reports_dir = Path(settings.reports_dir).resolve()
    file_path = (reports_dir / filename).resolve()
    
    # Secondary guard: ensure the resolved path actually stays inside reports_dir
    # (Though the string check above already makes this practically impossible).
    if file_path.parent != reports_dir:
        raise HTTPException(status_code=400, detail="Path traversal attempt blocked.")
        
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found on disk.")
        
    # FileResponse handles setting the proper Content-Disposition and caching headers
    return FileResponse(
        path=file_path, 
        media_type="text/html", 
        filename=filename
    )
