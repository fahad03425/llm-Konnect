"""
Module 6.7 (Statistical Anomaly Detection) — detectors.py

Pure deterministic code-driven statistical anomaly detectors:
- Z-score and IQR outlier detection for transaction spikes
- Duplicate invoice detection (exact duplicates and invoice ID collisions)
- Abnormal refund pattern analysis (by customer, cashier, product)
- Unusual discount detection
- Negative or zero price detection

Contract:
"Anomalies are detected entirely by code; the LLM's only role is to write
a plain-language explanation of an already-flagged item."
"""

from __future__ import annotations
import uuid
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.core.config import get_default_domain
from app.anomaly.models import AnomalyRecord, AnomalyScanResult, AnomalyType, Severity


# ---------------------------------------------------------------------------
# Tolerance constants for inventory shrinkage / stock movement mismatch
# ---------------------------------------------------------------------------
SHRINKAGE_TOLERANCE_PCT: float = 0.05  # Allow up to 5% relative variance between stock decrease and recorded sales
SHRINKAGE_TOLERANCE_ABS: float = 0.01  # Small absolute buffer for float precision / minimal unit variations

if not hasattr(AnomalyType, "STOCK_MOVEMENT_MISMATCH"):
    try:
        setattr(AnomalyType, "STOCK_MOVEMENT_MISMATCH", "stock_movement_mismatch")
    except Exception:
        pass


def _safe_float(val: Any) -> Optional[float]:
    try:
        f = float(val)
        return f if not np.isnan(f) else None
    except (ValueError, TypeError):
        return None


def _get_row_ref(row: pd.Series, idx: int) -> int:
    if "source_row" in row and pd.notna(row["source_row"]):
        try:
            return int(row["source_row"])
        except (ValueError, TypeError):
            pass
    return idx + 1


def detect_duplicate_invoices(df: pd.DataFrame) -> List[AnomalyRecord]:
    """
    Detect duplicate invoices:
    1. Exact duplicate rows (identical invoice_id, date, product_id, and amount).
    2. Invoice ID collisions (same invoice_id used with conflicting amounts, dates, or customers).
    """
    anomalies: List[AnomalyRecord] = []
    if df.empty or "invoice_id" not in df.columns:
        return anomalies

    clean_df = df.copy()
    clean_df["_clean_inv"] = clean_df["invoice_id"].astype(str).str.strip()
    # Ignore empty or placeholder invoice IDs
    valid_mask = clean_df["_clean_inv"].notna() & (~clean_df["_clean_inv"].isin(["", "nan", "none", "null"]))
    valid_df = clean_df[valid_mask]
    if valid_df.empty:
        return anomalies

    # 1. Exact Duplicate Transactions
    subset_cols = ["_clean_inv"]
    for col in ["date", "product_id", "amount", "unit_price", "quantity"]:
        if col in valid_df.columns:
            subset_cols.append(col)

    if len(subset_cols) > 1:
        dup_mask = valid_df.duplicated(subset=subset_cols, keep=False)
        if dup_mask.any():
            dup_rows = valid_df[dup_mask]
            for inv_id, grp in dup_rows.groupby("_clean_inv"):
                if len(grp) > 1:
                    row_refs = [_get_row_ref(r, idx) for idx, r in grp.iterrows()]
                    first_idx = grp.index[0]
                    first_row = grp.loc[first_idx]
                    anomalies.append(
                        AnomalyRecord(
                            id=f"anom_dup_{uuid.uuid4().hex[:8]}",
                            anomaly_type=AnomalyType.DUPLICATE_INVOICE,
                            severity=Severity.HIGH,
                            metric_name="invoice_id",
                            observed_value=inv_id,
                            expected_range="1 unique record per invoice/product",
                            statistical_score=float(len(grp)),
                            method="exact_duplicate",
                            source_row=row_refs[0],
                            source_file=str(first_row.get("source_file") or ""),
                            metadata={
                                "invoice_id": inv_id,
                                "duplicate_count": len(grp),
                                "matching_rows": row_refs,
                                "date": str(first_row.get("date") or ""),
                                "amount": _safe_float(first_row.get("amount")),
                                "product_id": str(first_row.get("product_id") or ""),
                            },
                        )
                    )

    # 2. Invoice ID Collisions (same invoice ID across conflicting dates or customers)
    collision_check_cols = [c for c in ["date", "customer_id"] if c in valid_df.columns]
    for chk_col in collision_check_cols:
        grouped = valid_df.groupby("_clean_inv")[chk_col].nunique()
        conflict_invs = grouped[grouped > 1].index.tolist()
        for inv_id in conflict_invs:
            grp = valid_df[valid_df["_clean_inv"] == inv_id]
            row_refs = [_get_row_ref(r, idx) for idx, r in grp.iterrows()]
            distinct_vals = grp[chk_col].dropna().unique().tolist()
            anomalies.append(
                AnomalyRecord(
                    id=f"anom_col_{uuid.uuid4().hex[:8]}",
                    anomaly_type=AnomalyType.DUPLICATE_INVOICE,
                    severity=Severity.HIGH,
                    metric_name=f"invoice_id_collision_{chk_col}",
                    observed_value=inv_id,
                    expected_range=f"1 distinct {chk_col} per invoice",
                    statistical_score=float(len(distinct_vals)),
                    method="collision",
                    source_row=row_refs[0],
                    source_file=str(grp.iloc[0].get("source_file") or ""),
                    metadata={
                        "invoice_id": inv_id,
                        "conflict_field": chk_col,
                        "distinct_values": [str(v) for v in distinct_vals],
                        "matching_rows": row_refs,
                    },
                )
            )

    return anomalies


def detect_transaction_spikes(
    df: pd.DataFrame,
    z_threshold: float = 3.0,
    iqr_multiplier: float = 1.5,
) -> List[AnomalyRecord]:
    """
    Detect unusual transaction spikes using both Z-score and Interquartile Range (IQR).
    - Z-Score: Identifies values where |x - mean| / std >= z_threshold (default 3.0).
    - IQR: Identifies values above Q3 + (iqr_multiplier * IQR) or below Q1 - (iqr_multiplier * IQR).
    """
    anomalies: List[AnomalyRecord] = []
    if df.empty or "amount" not in df.columns:
        return anomalies

    amounts = pd.to_numeric(df["amount"], errors="coerce").dropna()
    # Filter to non-negative sales amounts if txn_type is present
    if "txn_type" in df.columns:
        valid_indices = df[df["txn_type"] != "refund"].index.intersection(amounts.index)
        amounts = amounts.loc[valid_indices]

    if len(amounts) < 4:
        # Insufficient data for reliable statistical outlier calculation
        return anomalies

    mean_val = float(amounts.mean())
    std_val = float(amounts.std(ddof=1)) if len(amounts) > 1 else 0.0

    q25 = float(amounts.quantile(0.25))
    q75 = float(amounts.quantile(0.75))
    iqr = q75 - q25
    upper_iqr = q75 + (iqr_multiplier * iqr)
    lower_iqr = max(0.0, q25 - (iqr_multiplier * iqr))

    # Evaluate each row
    for idx in amounts.index:
        val = float(amounts.loc[idx])
        row = df.loc[idx]
        row_ref = _get_row_ref(row, idx)

        z_score = abs(val - mean_val) / std_val if std_val > 1e-6 else 0.0
        is_z_outlier = z_score >= z_threshold
        is_iqr_outlier = (val > upper_iqr) or (val < lower_iqr and val > 0)

        if is_z_outlier or is_iqr_outlier:
            severity = Severity.HIGH if z_score >= 4.0 or val > (q75 + 3.0 * iqr) else Severity.MEDIUM
            method_used = "z-score & iqr" if (is_z_outlier and is_iqr_outlier) else ("z-score" if is_z_outlier else "iqr")

            anomalies.append(
                AnomalyRecord(
                    id=f"anom_spk_{uuid.uuid4().hex[:8]}",
                    anomaly_type=AnomalyType.TRANSACTION_SPIKE,
                    severity=severity,
                    metric_name="amount",
                    observed_value=round(val, 2),
                    expected_range=f"IQR normal: {lower_iqr:.2f} - {upper_iqr:.2f} (Mean: {mean_val:.2f} ± {z_threshold}σ)",
                    statistical_score=round(z_score, 2),
                    method=method_used,
                    source_row=row_ref,
                    source_file=str(row.get("source_file") or ""),
                    metadata={
                        "invoice_id": str(row.get("invoice_id") or ""),
                        "customer_id": str(row.get("customer_id") or ""),
                        "product_id": str(row.get("product_id") or ""),
                        "date": str(row.get("date") or ""),
                        "z_score": round(z_score, 2),
                        "iqr_upper_bound": round(upper_iqr, 2),
                        "mean": round(mean_val, 2),
                        "std": round(std_val, 2),
                    },
                )
            )

    return anomalies


def detect_abnormal_refund_patterns(
    df: pd.DataFrame,
    threshold_sigma: float = 2.5,
) -> List[AnomalyRecord]:
    """
    Detect abnormal refund patterns grouped by customer, cashier, or product:
    - Flags entities with statistically abnormal refund count or refund amounts.
    """
    anomalies: List[AnomalyRecord] = []
    if df.empty or "amount" not in df.columns:
        return anomalies

    clean_df = df.copy()
    clean_df["_amt"] = pd.to_numeric(clean_df["amount"], errors="coerce").fillna(0.0)

    # Identify refund rows
    is_refund = pd.Series(False, index=clean_df.index)
    if "txn_type" in clean_df.columns:
        is_refund = is_refund | (clean_df["txn_type"].astype(str).str.lower() == "refund")
    is_refund = is_refund | (clean_df["_amt"] < 0)

    refund_df = clean_df[is_refund]
    if refund_df.empty:
        return anomalies

    # Check anomalies across grouping dimensions: customer_id, cashier, product_id
    group_cols = [c for c in ["customer_id", "cashier", "cashier_name", "product_id"] if c in clean_df.columns]

    for g_col in group_cols:
        counts = refund_df.groupby(g_col).size()
        amounts_sum = refund_df.groupby(g_col)["_amt"].apply(lambda s: s.abs().sum())

        if len(counts) >= 3:
            cnt_mean = float(counts.mean())
            cnt_std = float(counts.std(ddof=1)) if len(counts) > 1 else 0.0

            amt_mean = float(amounts_sum.mean())
            amt_std = float(amounts_sum.std(ddof=1)) if len(amounts_sum) > 1 else 0.0

            for entity, cnt in counts.items():
                if str(entity).strip().lower() in ("", "nan", "none", "walk-in"):
                    continue

                entity_amt = float(amounts_sum.get(entity, 0.0))
                cnt_z = (cnt - cnt_mean) / cnt_std if cnt_std > 1e-6 else 0.0
                amt_z = (entity_amt - amt_mean) / amt_std if amt_std > 1e-6 else 0.0

                # Outlier condition: Z-Score exceeds threshold OR (for small samples N < 10) exceeds 2.5x mean
                is_outlier = (cnt_z >= threshold_sigma) or (amt_z >= threshold_sigma)
                if not is_outlier and len(counts) < 10:
                    is_outlier = (cnt >= 2.5 * cnt_mean and cnt >= 3) or (entity_amt >= 2.5 * amt_mean and entity_amt > 100)

                if is_outlier:
                    sample_row = refund_df[refund_df[g_col] == entity].iloc[0]
                    anomalies.append(
                        AnomalyRecord(
                            id=f"anom_ref_{uuid.uuid4().hex[:8]}",
                            anomaly_type=AnomalyType.ABNORMAL_REFUND,
                            severity=Severity.HIGH if (cnt_z >= 3.5 or amt_z >= 3.5) else Severity.MEDIUM,
                            metric_name=f"refunds_by_{g_col}",
                            observed_value={
                                "refund_count": int(cnt),
                                "total_refund_amount": round(entity_amt, 2),
                            },
                            expected_range=f"Avg {g_col} refunds: {cnt_mean:.1f} count, {amt_mean:.2f} PKR",
                            statistical_score=round(max(cnt_z, amt_z), 2),
                            method="group_z_score",
                            source_row=_get_row_ref(sample_row, 0),
                            source_file=str(sample_row.get("source_file") or ""),
                            metadata={
                                "dimension": g_col,
                                "entity_id": str(entity),
                                "refund_count": int(cnt),
                                "total_refund_amount": round(entity_amt, 2),
                                "count_z_score": round(cnt_z, 2),
                                "amount_z_score": round(amt_z, 2),
                            },
                        )
                    )

    return anomalies


def detect_unusual_discounts(df: pd.DataFrame, z_threshold: float = 3.0) -> List[AnomalyRecord]:
    """
    Detect unusual discount amounts:
    - Discounts exceeding gross line amount (discount > amount).
    - Discount percentage exceeding 50% on standard items.
    - Z-Score outliers on the discount distribution.
    """
    anomalies: List[AnomalyRecord] = []
    if df.empty or "discount" not in df.columns:
        return anomalies

    clean_df = df.copy()
    clean_df["_disc"] = pd.to_numeric(clean_df["discount"], errors="coerce")
    clean_df["_amt"] = pd.to_numeric(clean_df.get("amount", pd.Series(0, index=clean_df.index)), errors="coerce").fillna(0.0)

    disc_valid = clean_df[clean_df["_disc"] > 0]
    if disc_valid.empty:
        return anomalies

    mean_disc = float(disc_valid["_disc"].mean())
    std_disc = float(disc_valid["_disc"].std(ddof=1)) if len(disc_valid) > 1 else 0.0

    for idx, row in disc_valid.iterrows():
        disc_val = float(row["_disc"])
        amt_val = float(row["_amt"])
        row_ref = _get_row_ref(row, idx)

        disc_rate = (disc_val / (amt_val + disc_val)) if (amt_val + disc_val) > 0 else 0.0
        z_score = abs(disc_val - mean_disc) / std_disc if std_disc > 1e-6 else 0.0

        # Flag if discount exceeds total or has extreme rate/Z-score
        is_greater_than_amount = (amt_val > 0 and disc_val > amt_val)
        is_extreme_rate = disc_rate > 0.50
        is_z_outlier = z_score >= z_threshold and disc_val > 100.0

        if is_greater_than_amount or is_extreme_rate or is_z_outlier:
            severity = Severity.HIGH if is_greater_than_amount else Severity.MEDIUM
            anomalies.append(
                AnomalyRecord(
                    id=f"anom_disc_{uuid.uuid4().hex[:8]}",
                    anomaly_type=AnomalyType.UNUSUAL_DISCOUNT,
                    severity=severity,
                    metric_name="discount",
                    observed_value=round(disc_val, 2),
                    expected_range=f"Avg discount: {mean_disc:.2f} PKR (Normal rate: < 20%)",
                    statistical_score=round(z_score, 2),
                    method="z_score_and_ratio",
                    source_row=row_ref,
                    source_file=str(row.get("source_file") or ""),
                    metadata={
                        "invoice_id": str(row.get("invoice_id") or ""),
                        "product_id": str(row.get("product_id") or ""),
                        "discount_amount": round(disc_val, 2),
                        "gross_amount": round(amt_val, 2),
                        "discount_pct": round(disc_rate * 100, 2),
                        "z_score": round(z_score, 2),
                    },
                )
            )

    return anomalies


def detect_negative_or_zero_prices(df: pd.DataFrame) -> List[AnomalyRecord]:
    """
    Detect non-refund sales rows with zero or negative price/amount.
    """
    anomalies: List[AnomalyRecord] = []
    if df.empty or "unit_price" not in df.columns:
        return anomalies

    clean_df = df.copy()
    clean_df["_price"] = pd.to_numeric(clean_df["unit_price"], errors="coerce")

    # Only inspect regular sales
    sale_mask = clean_df["_price"].notna()
    if "txn_type" in clean_df.columns:
        sale_mask = sale_mask & (clean_df["txn_type"].astype(str).str.lower() != "refund")

    bad_prices = clean_df[sale_mask & (clean_df["_price"] <= 0)]
    for idx, row in bad_prices.iterrows():
        val = float(row["_price"])
        row_ref = _get_row_ref(row, idx)
        anomalies.append(
            AnomalyRecord(
                id=f"anom_prc_{uuid.uuid4().hex[:8]}",
                anomaly_type=AnomalyType.NEGATIVE_OR_ZERO_PRICE,
                severity=Severity.MEDIUM,
                metric_name="unit_price",
                observed_value=round(val, 2),
                expected_range="Unit price must be > 0.00 PKR for sales transactions",
                statistical_score=0.0,
                method="boundary_check",
                source_row=row_ref,
                source_file=str(row.get("source_file") or ""),
                metadata={
                    "invoice_id": str(row.get("invoice_id") or ""),
                    "product_id": str(row.get("product_id") or ""),
                    "unit_price": val,
                },
            )
        )

    return anomalies


def detect_stock_movement_mismatch(df: pd.DataFrame) -> List[AnomalyRecord]:
    """
    Detect physical inventory shrinkage / stock movement mismatch:
    For each product_id, compares (opening_stock_qty - closing_stock_qty) against
    the actual quantity sold recorded in transactions over the same period.
    Flags products where stock decrease exceeds recorded sales by more than
    SHRINKAGE_TOLERANCE_PCT (5%) and SHRINKAGE_TOLERANCE_ABS.

    Returns an empty list if required stock columns are missing.
    """
    anomalies: List[AnomalyRecord] = []
    if df is None or df.empty:
        return anomalies

    # Graceful handling: required columns missing
    if "opening_stock_qty" not in df.columns or "closing_stock_qty" not in df.columns:
        return anomalies
    if "product_id" not in df.columns:
        return anomalies

    clean_df = df.copy()

    # Pre-parse numeric columns
    clean_df["_open"] = pd.to_numeric(clean_df["opening_stock_qty"], errors="coerce")
    clean_df["_close"] = pd.to_numeric(clean_df["closing_stock_qty"], errors="coerce")
    if clean_df["_open"].dropna().empty or clean_df["_close"].dropna().empty:
        return anomalies

    if "quantity" in clean_df.columns:
        clean_df["_qty"] = pd.to_numeric(clean_df["quantity"], errors="coerce").fillna(0.0)
    else:
        clean_df["_qty"] = 0.0

    # Handle txn_type if present
    is_refund = pd.Series(False, index=clean_df.index)
    if "txn_type" in clean_df.columns:
        txn_str = clean_df["txn_type"].astype(str).str.lower().str.strip()
        is_refund = txn_str.isin(["refund", "refunds", "return", "returns"])

    for pid, grp in clean_df.groupby("product_id"):
        if pd.isna(pid) or str(pid).strip() in ("", "nan", "none", "null"):
            continue

        open_vals = grp["_open"].dropna()
        close_vals = grp["_close"].dropna()
        if open_vals.empty or close_vals.empty:
            continue

        opening = float(open_vals.iloc[0])
        closing = float(close_vals.iloc[-1])
        stock_decrease = opening - closing

        # No stock decrease (stock increased or stayed same) -> no shrinkage
        if stock_decrease <= 0:
            continue

        # Compute net quantity sold
        grp_refunds = is_refund.loc[grp.index]
        sales_qty = float(grp.loc[~grp_refunds, "_qty"].sum())
        refund_qty = float(grp.loc[grp_refunds, "_qty"].sum())
        qty_sold = max(0.0, sales_qty - refund_qty)

        discrepancy = stock_decrease - qty_sold
        # Tolerance check: must exceed both absolute buffer and relative percentage
        tol_threshold = max(SHRINKAGE_TOLERANCE_ABS, qty_sold * SHRINKAGE_TOLERANCE_PCT)

        if discrepancy > tol_threshold:
            first_idx = grp.index[0]
            first_row = grp.loc[first_idx]
            row_ref = _get_row_ref(first_row, int(first_idx) if isinstance(first_idx, int) else 0)

            discrepancy_pct = ((discrepancy / qty_sold) * 100.0) if qty_sold > 0 else 100.0
            severity = Severity.HIGH if (discrepancy >= 10.0 or discrepancy_pct > 20.0) else Severity.MEDIUM

            anom_type = getattr(AnomalyType, "STOCK_MOVEMENT_MISMATCH", "stock_movement_mismatch")
            prod_name = str(first_row.get("description") or first_row.get("product_name") or pid)

            anomalies.append(
                AnomalyRecord(
                    id=f"anom_stk_{uuid.uuid4().hex[:8]}",
                    anomaly_type=anom_type,
                    severity=severity,
                    metric_name="stock_movement_mismatch",
                    observed_value=round(discrepancy, 2),
                    expected_range=f"Stock decrease <= {qty_sold:.2f} units (+{int(SHRINKAGE_TOLERANCE_PCT * 100)}% tolerance)",
                    statistical_score=round(discrepancy, 2),
                    method="inventory_reconciliation",
                    source_row=row_ref,
                    source_file=str(first_row.get("source_file") or ""),
                    metadata={
                        "product_id": str(pid),
                        "product_name": prod_name,
                        "opening_stock_qty": round(opening, 2),
                        "closing_stock_qty": round(closing, 2),
                        "stock_decrease": round(stock_decrease, 2),
                        "quantity_sold": round(qty_sold, 2),
                        "discrepancy_units": round(discrepancy, 2),
                        "discrepancy_pct": round(discrepancy_pct, 2),
                    },
                )
            )

    return anomalies


def detect_all_anomalies(df: pd.DataFrame, domain: Optional[str] = None) -> AnomalyScanResult:
    """
    Master runner: Executes all statistical detectors, compiles summary,
    and returns a structured AnomalyScanResult sorted by severity and statistical score.
    """
    effective_domain = domain or get_default_domain()
    if df is None or df.empty:
        return AnomalyScanResult(total_anomalies=0, by_type={}, by_severity={}, anomalies=[])

    all_anomalies: List[AnomalyRecord] = []

    # 1. Duplicates & Collisions
    all_anomalies.extend(detect_duplicate_invoices(df))

    # 2. Transaction Spikes (Z-Score + IQR)
    all_anomalies.extend(detect_transaction_spikes(df))

    # 3. Abnormal Refund Patterns
    all_anomalies.extend(detect_abnormal_refund_patterns(df))

    # 4. Unusual Discounts
    all_anomalies.extend(detect_unusual_discounts(df))

    # 5. Zero or Negative Pricing
    all_anomalies.extend(detect_negative_or_zero_prices(df))

    # 6. Domain-specific checks (Pharmacy inventory shrinkage)
    if effective_domain and str(effective_domain).strip().lower() == "pharmacy":
        all_anomalies.extend(detect_stock_movement_mismatch(df))

    # Severity ordering: HIGH > MEDIUM > LOW
    severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
    all_anomalies.sort(
        key=lambda a: (
            severity_order.get(a.severity, 3),
            -(a.statistical_score or 0.0),
        )
    )

    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}

    for a in all_anomalies:
        t_key = a.anomaly_type.value if hasattr(a.anomaly_type, "value") else str(a.anomaly_type)
        s_key = a.severity.value if hasattr(a.severity, "value") else str(a.severity)
        by_type[t_key] = by_type.get(t_key, 0) + 1
        by_severity[s_key] = by_severity.get(s_key, 0) + 1

    return AnomalyScanResult(
        total_anomalies=len(all_anomalies),
        by_type=by_type,
        by_severity=by_severity,
        anomalies=all_anomalies,
    )
