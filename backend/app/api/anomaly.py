"""
Module 6.7 (Statistical Anomaly Detection) — API Routes.

Endpoints:
- POST /api/anomaly/scan: Run statistical anomaly detection on a file, table, or database.
- POST /api/anomaly/explain: Generate plain-language explanation for flagged items.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_default_domain
from app.anomaly.detectors import detect_all_anomalies
from app.anomaly.explainer import explain_all, explain_anomaly
from app.anomaly.models import AnomalyRecord, AnomalyScanResult

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])


class AnomalyScanRequest(BaseModel):
    file_path: Optional[str] = Field(None, description="Path to file, sql:// table, or db:// database")
    file_id: Optional[str] = Field(None, description="Registry file ID")
    domain: str = Field(default_factory=get_default_domain, description="Domain pack name (defaults to active domain)")
    include_explanations: bool = Field(True, description="Attach plain-language explanations to top anomalies")
    use_llm_explanations: bool = Field(False, description="Use local LLM for narrative explanations (slower)")


class AnomalyExplainRequest(BaseModel):
    anomaly: Dict[str, Any] = Field(..., description="Anomaly record dictionary to explain")
    use_llm: bool = Field(True, description="Whether to use local LLM or fast deterministic template")


@router.post("/scan")
def scan_anomalies(req: AnomalyScanRequest):
    """
    Run deterministic statistical anomaly detection across a dataset.
    Returns categorized anomalies (duplicates, spikes, refund patterns, unusual discounts).
    """
    from app.api.analytics import _load_canonical, KPIRequest
    from app.ingestion.registry import file_registry

    target_path = req.file_path
    if not target_path and req.file_id:
        rec = file_registry.get_file_by_id(req.file_id)
        if rec and rec.file_path:
            target_path = rec.file_path

    if not target_path:
        # Fallback to connected database if exists
        conns = file_registry.list_db_connections()
        if conns:
            target_path = f"db://{conns[0].database_name}"
        else:
            files = file_registry.list_files()
            if files:
                target_path = files[0].file_path

    if not target_path:
        raise HTTPException(status_code=400, detail="No data source specified and no active files found in registry.")

    try:
        kpi_req = KPIRequest(file_path=target_path, domain=req.domain)
        df, _ = _load_canonical(kpi_req)
        if df is None or df.empty:
            return AnomalyScanResult(total_anomalies=0, by_type={}, by_severity={}, anomalies=[]).to_dict()

        scan_result = detect_all_anomalies(df, domain=req.domain)

        if req.include_explanations and scan_result.anomalies:
            explain_all(scan_result.anomalies, max_items=20, use_llm=req.use_llm_explanations)

        res_dict = scan_result.to_dict()
        res_dict["source"] = target_path
        return res_dict

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error executing anomaly detection scan: {str(e)}")


@router.post("/explain")
def explain_single_anomaly(req: AnomalyExplainRequest):
    """
    Generate a plain-language explanation for an already-flagged anomaly record.
    """
    try:
        from app.anomaly.models import AnomalyType, Severity

        d = req.anomaly
        rec = AnomalyRecord(
            id=d.get("id", "anom_custom"),
            anomaly_type=AnomalyType(d.get("anomaly_type", AnomalyType.TRANSACTION_SPIKE.value)),
            severity=Severity(d.get("severity", Severity.MEDIUM.value)),
            metric_name=d.get("metric_name", "value"),
            observed_value=d.get("observed_value"),
            expected_range=d.get("expected_range"),
            statistical_score=d.get("statistical_score"),
            method=d.get("method", "code_statistical"),
            source_row=d.get("source_row"),
            source_file=d.get("source_file"),
            metadata=d.get("metadata", {}),
        )

        explanation = explain_anomaly(rec, use_llm=req.use_llm)
        return {
            "id": rec.id,
            "explanation": explanation,
            "used_llm": req.use_llm,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate explanation: {str(e)}")
