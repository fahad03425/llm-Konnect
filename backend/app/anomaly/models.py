"""
Module 6.7 (Statistical Anomaly Detection) — models.py

Typed data models for statistical anomaly detection:
- Duplicate invoices (exact duplicates & invoice ID collisions)
- Transaction spikes (via Z-score & IQR analysis)
- Abnormal refund patterns
- Unusual discounts
- Negative or zero pricing
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class AnomalyType(str, Enum):
    DUPLICATE_INVOICE = "duplicate_invoice"
    TRANSACTION_SPIKE = "transaction_spike"
    ABNORMAL_REFUND = "abnormal_refund"
    UNUSUAL_DISCOUNT = "unusual_discount"
    NEGATIVE_OR_ZERO_PRICE = "negative_or_zero_price"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class AnomalyRecord:
    """A single statistically detected anomaly with complete mathematical provenance."""

    id: str
    anomaly_type: AnomalyType
    severity: Severity
    metric_name: str
    observed_value: Any
    expected_range: Optional[str] = None
    statistical_score: Optional[float] = None  # Z-score or IQR distance factor
    method: str = "code_statistical"          # "z-score", "iqr", "frequency_ratio", "collision"
    source_row: Optional[int] = None
    source_file: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    explanation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["anomaly_type"] = self.anomaly_type.value if isinstance(self.anomaly_type, AnomalyType) else self.anomaly_type
        data["severity"] = self.severity.value if isinstance(self.severity, Severity) else self.severity
        return data


@dataclass
class AnomalyScanResult:
    """Summary and itemized list of all detected anomalies across a dataset."""

    total_anomalies: int
    by_type: Dict[str, int]
    by_severity: Dict[str, int]
    anomalies: List[AnomalyRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_anomalies": self.total_anomalies,
            "by_type": self.by_type,
            "by_severity": self.by_severity,
            "anomalies": [a.to_dict() for a in self.anomalies],
        }
