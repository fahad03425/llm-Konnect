"""
Module 6.8 — API endpoints for the Verified Report Generator.

Exposes the orchestrator from `app.reporting.report` over HTTP.
Supports dual-format HTML and PDF generation and secure download.
"""

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

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
    """Payload for generating an executive verified analytics report."""
    business_name: Optional[str] = None
    max_regeneration_attempts: int = Field(
        1, description="Retry attempts if verification fails. 0 to disable retry."
    )
    formats: List[str] = Field(
        default_factory=lambda: ["html", "pdf"],
        description="Target report formats: ['html', 'pdf']"
    )


@router.post("/generate")
def generate(req: ReportRequest):
    """
    Generate a verified HTML and PDF analytics report.
    Returns the KPI snapshot, verification status, and download URLs.
    """
    try:
        # 1. Load canonical data using existing analytics pipeline
        canonical, mapping = _load_canonical(req)

        # Also fetch raw dataframe so rich domain-specific columns (branches, hours, cashiers, balances, dates) are fully preserved
        raw = None
        try:
            from app.connectors.base import detect_connector
            connector = detect_connector(req.file_path)
            kwargs = {}
            if req.sheet_name:
                kwargs["sheet_name"] = req.sheet_name
            if req.table_or_query:
                kwargs["table_or_query"] = req.table_or_query
            raw = connector.fetch(**kwargs)
        except Exception:
            pass

        # 2. Run full report generation pipeline
        report_result = generate_report(
            source_df=canonical,
            raw_df=raw,
            domain=req.domain,
            filters=_filters(req),
            business_name=req.business_name,
            max_regeneration_attempts=req.max_regeneration_attempts,
            formats=tuple(req.formats),
        )

        # 3. Wrap with metadata envelope
        body = _envelope(req, canonical, mapping)

        # 4. Attach report output
        body["report"] = report_result.to_dict()

        # 5. Expose download URLs for generated formats
        if report_result.html_path:
            html_fn = Path(report_result.html_path).name
            body["download_url_html"] = f"/api/report/download/{html_fn}"
            body["download_url"] = f"/api/report/download/{html_fn}"  # backward compatibility

        if report_result.pdf_path:
            pdf_fn = Path(report_result.pdf_path).name
            body["download_url_pdf"] = f"/api/report/download/{pdf_fn}"

        return body

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/list")
def list_reports():
    """List all available generated reports on disk, grouped by report session."""
    reports_dir = Path(settings.reports_dir).resolve()
    if not reports_dir.exists():
        return {"reports": []}

    from datetime import datetime

    grouped: Dict[str, Dict[str, Any]] = {}
    for f in sorted(reports_dir.glob("report_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in [".html", ".pdf"]:
            continue

        stem = f.stem
        if stem not in grouped:
            mtime = f.stat().st_mtime
            try:
                ts_str = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %I:%M %p")
            except Exception:
                ts_str = stem
            grouped[stem] = {
                "id": stem,
                "stem": stem,
                "created_at": mtime,
                "created_formatted": ts_str,
                "filename_base": stem,
                "download_url_html": None,
                "download_url_pdf": None,
                "has_html": False,
                "has_pdf": False,
                "formats": [],
            }

        if ext == ".html":
            grouped[stem]["download_url_html"] = f"/api/report/download/{f.name}"
            grouped[stem]["has_html"] = True
            grouped[stem]["html_size"] = f"{f.stat().st_size / 1024:.1f} KB"
            grouped[stem]["formats"].append("HTML")
        elif ext == ".pdf":
            grouped[stem]["download_url_pdf"] = f"/api/report/download/{f.name}"
            grouped[stem]["has_pdf"] = True
            grouped[stem]["pdf_size"] = f"{f.stat().st_size / 1024:.1f} KB"
            grouped[stem]["formats"].append("PDF")

    reports_list = sorted(grouped.values(), key=lambda r: r["created_at"], reverse=True)
    return {"reports": reports_list}


@router.delete("/{filename}")
def delete_report(filename: str):
    """Delete a report file (and its companion format sibling) safely."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename format.")

    reports_dir = Path(settings.reports_dir).resolve()
    stem = Path(filename).stem
    deleted = []

    # Delete both siblings if present
    for ext in [".html", ".pdf"]:
        p = reports_dir / f"{stem}{ext}"
        if p.is_file():
            p.unlink()
            deleted.append(p.name)

    # Also check exact filename if not already deleted
    direct = reports_dir / filename
    if direct.is_file() and direct.name not in deleted:
        direct.unlink()
        deleted.append(direct.name)

    if not deleted:
        raise HTTPException(status_code=404, detail="Report file not found on disk.")

    return {"status": "ok", "deleted_files": deleted}


@router.get("/download/{filename}")
def download_report(filename: str, download: bool = False):
    """Serve a previously generated HTML or PDF report safely."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename format.")

    reports_dir = Path(settings.reports_dir).resolve()
    file_path = (reports_dir / filename).resolve()

    if file_path.parent != reports_dir:
        raise HTTPException(status_code=400, detail="Path traversal attempt blocked.")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found on disk.")

    media_type = "application/pdf" if filename.lower().endswith(".pdf") else "text/html; charset=utf-8"
    disposition = "attachment" if download else "inline"

    headers = {
        "X-Content-Type-Options": "nosniff",
    }

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type=disposition,
        headers=headers,
    )

