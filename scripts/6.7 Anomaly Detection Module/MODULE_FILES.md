# Module 6.7: Statistical Anomaly Detection Module — File Catalog & Architecture

## Overview
Module 6.7 applies statistical and deterministic algorithms to flag operational and financial irregularities—such as duplicate invoices, invoice ID collisions, unusual transaction spikes, abnormal refund patterns, excessive discounts, zero/negative pricing, and stock shrinkage.

### Core Architectural Contract:
> **"Anomalies are detected entirely by code; the LLM's only role is to write a plain-language explanation of an already-flagged item."**

The LLM is strictly prohibited from performing outlier math or deciding which records are anomalous. All statistical classification occurs in pure deterministic code using NumPy, SciPy/Pandas vector operations.

---

## Identified Files of Module 6.7

| File Path | Component Role | Key Symbols & Responsibilities |
| :--- | :--- | :--- |
| [`backend/app/anomaly/__init__.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/anomaly/__init__.py) | Package Entry Point | Public exports for `AnomalyRecord`, `AnomalyScanResult`, `AnomalyType`, `Severity`, statistical detectors, and explainer utilities. |
| [`backend/app/anomaly/models.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/anomaly/models.py) | Data Contracts | `AnomalyType` enum, `Severity` enum, `AnomalyRecord` dataclass (storing mathematical provenance, observed vs expected values, Z-score, method, and source row), and `AnomalyScanResult` aggregate. |
| [`backend/app/anomaly/detectors.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/anomaly/detectors.py) | Statistical Detectors | Vectorized algorithms: `detect_duplicate_invoices`, `detect_transaction_spikes` (Z-score & IQR), `detect_abnormal_refund_patterns`, `detect_unusual_discounts`, `detect_negative_or_zero_prices`, `detect_stock_movement_mismatch`, `detect_ecommerce_margin_erosion`, `detect_all_anomalies`. |
| [`backend/app/anomaly/explainer.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/anomaly/explainer.py) | Plain-Language Explanation Layer | `generate_template_explanation` (deterministic offline fallback), `explain_with_llm` (grounded LLM narrative generator), `explain_anomaly`, and `explain_all`. |
| [`backend/app/api/anomaly.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/api/anomaly.py) | REST Transport Router | FastAPI endpoints: `POST /api/anomaly/scan` and `POST /api/anomaly/explain`. Decouples detection transport from business logic. |
| [`backend/app/analytics/kpi.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/kpi.py) | Analytics Integration | `anomaly_count` and `anomaly_breakdown` KPI implementations enabling unified dashboard reporting. |
| [`backend/app/analytics/engine.py`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/backend/app/analytics/engine.py) | Engine Specifications | Core registry entries for `anomaly_count` and `anomaly_breakdown` specs. |

---

## Statistical Detection Methods & Thresholds

| Anomaly Type | Statistical Method | Threshold & Evaluation Logic | Default Severity |
| :--- | :--- | :--- | :---: |
| **Transaction Spikes** | **Z-Score & IQR** | $Z = \frac{\|x - \mu\|}{\sigma} \ge 3.0$<br>$x > Q_3 + 1.5 \times \text{IQR}$ or $x < Q_1 - 1.5 \times \text{IQR}$ | **HIGH** if $Z \ge 4.0$, else **MEDIUM** |
| **Exact Duplicates** | Exact Hash Collision | Identical `invoice_id`, `date`, `product_id`, and `amount` occurring $>1$ times. | **HIGH** |
| **Invoice ID Collision** | Group Collision Check | Same `invoice_id` mapped to $>1$ distinct dates or customer IDs. | **HIGH** |
| **Abnormal Refunds** | Group Z-Score | Refund frequency or refund volume $Z \ge 2.5\sigma$ grouped by customer or cashier. | **HIGH** if $Z \ge 3.5$, else **MEDIUM** |
| **Unusual Discounts** | Ratio & Z-Score | Discount $> 50\%$ of gross amount, or discount amount $> \text{amount}$, or $Z \ge 3.0\sigma$. | **HIGH** if discount $>$ amount, else **MEDIUM** |
| **Zero/Negative Price** | Boundary Check | Unit price $\le 0.00$ on regular (non-refund) sale items. | **MEDIUM** |
| **Stock Shrinkage** | Inventory Reconciliation | $(\text{Opening} - \text{Closing}) - \text{Quantity Sold} > \max(0.01, 5\% \times \text{Sold})$. | **HIGH** if $>20\%$ or $\ge 10$ units, else **MEDIUM** |
| **Margin Erosion** | Unit Economics | $\text{Unit Price} < \text{Unit Cost}$ (negative gross margin per item). | **HIGH** if loss $>25\%$, else **MEDIUM** |
