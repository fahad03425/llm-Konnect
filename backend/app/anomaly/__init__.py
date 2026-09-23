"""Module 6.7 (Statistical Anomaly Detection)."""

from app.anomaly.models import AnomalyRecord, AnomalyScanResult, AnomalyType, Severity
from app.anomaly.detectors import (
    detect_all_anomalies,
    detect_duplicate_invoices,
    detect_transaction_spikes,
    detect_abnormal_refund_patterns,
    detect_unusual_discounts,
    detect_negative_or_zero_prices,
)
from app.anomaly.explainer import explain_anomaly, explain_all, generate_template_explanation

__all__ = [
    "AnomalyRecord",
    "AnomalyScanResult",
    "AnomalyType",
    "Severity",
    "detect_all_anomalies",
    "detect_duplicate_invoices",
    "detect_transaction_spikes",
    "detect_abnormal_refund_patterns",
    "detect_unusual_discounts",
    "detect_negative_or_zero_prices",
    "explain_anomaly",
    "explain_all",
    "generate_template_explanation",
]
