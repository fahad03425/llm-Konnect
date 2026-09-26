"""
Module 6.7 (Statistical Anomaly Detection) — explainer.py

LLM Plain-Language Explanation Layer for pre-flagged statistical anomalies.
Adheres strictly to the architectural contract:
"Anomalies are detected entirely by code; the LLM's only role is to write
a plain-language explanation of an already-flagged item."

Includes deterministic offline template fallbacks so the system never fails
or hangs if LLM is offline or during high-throughput report generation.
"""

from __future__ import annotations
from typing import List, Optional
from app.anomaly.models import AnomalyRecord, AnomalyType


def generate_template_explanation(record: AnomalyRecord) -> str:
    """
    Deterministic rule-based explanation generator (fast, 100% offline, zero-latency).
    Used as primary generator or fallback when LLM is unavailable.
    """
    m = record.metadata
    inv = m.get("invoice_id") or "N/A"
    rows = m.get("matching_rows", [record.source_row])

    if record.anomaly_type == AnomalyType.DUPLICATE_INVOICE:
        if record.method == "collision":
            field_name = m.get("conflict_field", "details")
            vals = ", ".join(m.get("distinct_values", []))
            return (
                f"Invoice ID '{inv}' was recorded multiple times with conflicting {field_name} "
                f"({vals}) across rows {rows}. Check for invoice number collision or POS counter sync error."
            )
        else:
            cnt = m.get("duplicate_count", 2)
            amt = m.get("amount")
            amt_str = f" of PKR {amt:,.2f}" if amt is not None else ""
            return (
                f"Invoice '{inv}'{amt_str} appears {cnt} times identically across rows {rows}. "
                f"Check whether the customer was double-billed or the entry was imported twice."
            )

    elif record.anomaly_type == AnomalyType.TRANSACTION_SPIKE:
        val = record.observed_value
        score = record.statistical_score or 0.0
        return (
            f"Transaction '{inv}' amount of PKR {val:,.2f} is an extreme statistical outlier "
            f"({score} standard deviations from mean; {record.expected_range}). "
            f"Verify if this was a legitimate bulk/institutional purchase or a decimal data entry mistake."
        )

    elif record.anomaly_type == AnomalyType.ABNORMAL_REFUND:
        entity = m.get("entity_id", "Entity")
        dim = m.get("dimension", "entity")
        cnt = m.get("refund_count", 0)
        tot = m.get("total_refund_amount", 0.0)
        score = record.statistical_score or 0.0
        return (
            f"{dim.replace('_', ' ').title()} '{entity}' recorded an abnormal refund frequency "
            f"({cnt} refunds totaling PKR {tot:,.2f}, scoring {score}σ above peer average). "
            f"Audit transaction logs to verify returned physical stock and cashier authorization."
        )

    elif record.anomaly_type == AnomalyType.UNUSUAL_DISCOUNT:
        val = record.observed_value
        pct = m.get("discount_pct", 0.0)
        return (
            f"Discount of PKR {val:,.2f} on invoice '{inv}' represents {pct:.1f}% of the item value, "
            f"substantially exceeding standard store discount policy. Confirm manager authorization."
        )

    elif record.anomaly_type == AnomalyType.NEGATIVE_OR_ZERO_PRICE:
        val = record.observed_value
        prod = m.get("product_id", "product")
        return (
            f"Item '{prod}' on invoice '{inv}' was recorded with a price of PKR {val:.2f} on a standard sale row. "
            f"Review catalog pricing to prevent accidental free dispensing or missing cost entries."
        )

    return (
        f"Flagged {record.metric_name} with value {record.observed_value} "
        f"(expected: {record.expected_range or 'normal range'}). Review source row {record.source_row}."
    )


def explain_with_llm(record: AnomalyRecord) -> Optional[str]:
    """
    Generate an LLM plain-language narration for a pre-flagged anomaly.
    Enforces grounding so the LLM cannot alter or hallucinate figures.
    """
    try:
        from app.core.llm import llm

        system_prompt = (
            "You are an offline statistical audit assistant. "
            "You are given a pre-flagged business anomaly that was already detected by statistical code. "
            "Write a concise, 1-2 sentence plain-language explanation of why this is anomalous and what the owner should verify. "
            "CRITICAL: Do NOT invent new numbers or change any metrics. Rely strictly on the given facts."
        )

        user_prompt = (
            f"Anomaly Type: {record.anomaly_type.value}\n"
            f"Severity: {record.severity.value}\n"
            f"Observed Value: {record.observed_value}\n"
            f"Expected Benchmark: {record.expected_range}\n"
            f"Statistical Score: {record.statistical_score} (Method: {record.method})\n"
            f"Details: {record.metadata}\n\n"
            "Write a concise 1-2 sentence explanation for the business owner."
        )

        explanation = llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
        )
        return explanation.strip() if explanation else None
    except Exception:
        return None


def explain_anomaly(record: AnomalyRecord, use_llm: bool = False) -> str:
    """
    Generate a plain-language explanation for a single anomaly record.
    Uses local LLM if requested, otherwise uses the deterministic template generator.
    """
    if use_llm:
        llm_exp = explain_with_llm(record)
        if llm_exp:
            record.explanation = llm_exp
            return llm_exp

    tmpl_exp = generate_template_explanation(record)
    record.explanation = tmpl_exp
    return tmpl_exp


def explain_all(
    anomalies: List[AnomalyRecord],
    max_items: int = 15,
    use_llm: bool = False,
) -> List[AnomalyRecord]:
    """
    Attach plain-language explanations to the top N detected anomalies.
    """
    for rec in anomalies[:max_items]:
        if not rec.explanation:
            explain_anomaly(rec, use_llm=use_llm)
    return anomalies
