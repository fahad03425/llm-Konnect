"""
Module 6.8 — API endpoints for the Verified Report Generator.

Exposes the orchestrator from `app.reporting.report` over HTTP.
Supports dual-format HTML and PDF generation and secure download.
"""

from pathlib import Path
from threading import Lock
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from app.api.analytics import (
    KPIRequest,
    _envelope,
    _filters,
    _load_canonical,
    _sanitize_json,
)
from app.core.config import settings
from app.reporting.report import generate_report

router = APIRouter(prefix="/api/report", tags=["report"])

_progress_lock = Lock()
_generation_progress = {}


def _set_progress(generation_id: Optional[str], progress: int, stage: str, status: str = "running") -> None:
    if not generation_id:
        return
    with _progress_lock:
        cutoff = time.time() - 3600
        for key in [key for key, value in _generation_progress.items() if value["updated_at"] < cutoff]:
            _generation_progress.pop(key, None)
        previous = _generation_progress.get(generation_id)
        if previous and previous["status"] == "running":
            progress = max(progress, previous["progress"])
        _generation_progress[generation_id] = {
            "generation_id": generation_id,
            "progress": min(100, max(0, int(progress))),
            "stage": stage,
            "status": status,
            "updated_at": time.time(),
        }


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
    report_type: Optional[str] = Field(
        "standard", description="Report variant: 'standard' or 'weekly_pharmacy'"
    )
    generation_id: Optional[str] = Field(None, description="Client-generated ID used to poll report progress")


@router.post("/generate")
def generate(req: ReportRequest, report_type: Optional[str] = None):
    """
    Generate a verified HTML and PDF analytics report.
    Returns the KPI snapshot, verification status, and download URLs.
    """
    try:
        _set_progress(req.generation_id, 2, "Starting report generation")
        effective_report_type = report_type if report_type is not None else (req.report_type or "standard")

        # 1. Load canonical data using existing analytics pipeline
        _set_progress(req.generation_id, 5, "Reading selected data source and mapping columns")
        canonical, mapping, raw = _load_canonical(req, include_raw=True)
        _set_progress(req.generation_id, 18, "Source loaded; starting calculations")

        # 2. Run full report generation pipeline
        report_result = generate_report(
            source_df=canonical,
            raw_df=raw,
            domain=req.domain,
            filters=_filters(req),
            business_name=req.business_name,
            max_regeneration_attempts=req.max_regeneration_attempts,
            formats=tuple(req.formats),
            report_type=effective_report_type,
            source_name=req.file_path,
            progress_callback=lambda pct, stage: _set_progress(req.generation_id, pct, stage),
        )

        # 3. Wrap with metadata envelope
        # This response only needs report metadata. Running a second validation
        # scan over every cell is unnecessary for an 89 MB workbook.
        body = _envelope(req, canonical, mapping, include_validation=False)

        # 4. Attach report output
        body["report"] = report_result.to_dict()

        # 5. Expose download URLs for generated formats
        # 5. Expose download URLs for generated formats
        if report_result.html_path:
            html_fn = Path(report_result.html_path).name
            body["download_url_html"] = f"/api/report/download/{html_fn}"
            body["download_url"] = f"/api/report/download/{html_fn}"  # backward compatibility

        if report_result.pdf_path:
            pdf_fn = Path(report_result.pdf_path).name
            body["download_url_pdf"] = f"/api/report/download/{pdf_fn}"

        # 6. Save companion metadata file for disk history
        try:
            import json
            meta_path = None
            if report_result.html_path:
                meta_path = Path(report_result.html_path).with_suffix(".meta.json")
            elif report_result.pdf_path:
                meta_path = Path(report_result.pdf_path).with_suffix(".meta.json")

            if meta_path:
                source_name = Path(req.file_path).name if req.file_path else "dataset"
                meta_payload = {
                    "source_file": source_name,
                    "business_name": req.business_name or "Analytics Report",
                    "domain": req.domain or "pharmacy",
                    "report_type": effective_report_type,
                    "verification_passed": report_result.verification_passed,
                    "verified_count": getattr(report_result.verification, "verified_count", 0),
                }
                meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
        except Exception:
            pass

        result = _sanitize_json(body)
        _set_progress(req.generation_id, 100, "Report ready", "complete")
        return result

    except HTTPException as exc:
        _set_progress(req.generation_id, 100, f"Report failed: {exc.detail}", "error")
        raise
    except Exception as exc:
        _set_progress(req.generation_id, 100, f"Report failed: {exc}", "error")
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/progress/{generation_id}")
def report_progress(generation_id: str):
    """Return the latest server-reported stage for an active report request."""
    with _progress_lock:
        state = _generation_progress.get(generation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Report generation progress not found.")
    return state


@router.get("/list")
def list_reports():
    """List all available generated reports on disk, grouped by report session."""
    reports_dir = Path(settings.reports_dir).resolve()
    if not reports_dir.exists():
        return {"reports": []}

    import json
    from datetime import datetime
    from typing import Dict, Any

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

            meta_file = reports_dir / f"{stem}.meta.json"
            meta_data: Dict[str, Any] = {}
            if meta_file.is_file():
                try:
                    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta_data = {}

            grouped[stem] = {
                "id": stem,
                "stem": stem,
                "created_at": mtime,
                "created_formatted": ts_str,
                "filename_base": stem,
                "business_name": meta_data.get("business_name") or "Analytics Report",
                "source_file": meta_data.get("source_file") or "Uploaded Dataset",
                "domain": meta_data.get("domain") or "pharmacy",
                "report_type": meta_data.get("report_type") or "standard",
                "verification_passed": meta_data.get("verification_passed", True),
                "verified_count": meta_data.get("verified_count", 5),
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
