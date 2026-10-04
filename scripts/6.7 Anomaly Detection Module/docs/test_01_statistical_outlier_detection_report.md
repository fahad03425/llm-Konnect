# Test Report 01: Statistical Outlier Detection (Z-Score & IQR)

## Executive Summary
This report validates the deterministic, purely code-driven statistical outlier detector (`detect_transaction_spikes`) within Module 6.7. The module identifies anomalous transaction spikes using **dual statistical methodologies**: **Z-Score** ($Z = \frac{|x - \mu|}{\sigma}$) and **Interquartile Range** ($\text{IQR} = Q_3 - Q_1$).

Crucially, **no Large Language Model (LLM)** is involved in the calculation, thresholding, or identification of outliers. Every flagged transaction record contains exact mathematical provenance (`mean`, `std`, `iqr_upper_bound`, and `z_score`).

---

## Test Cases & Specifications

| Test Case | Target Feature | Tested Behavior | Status |
| :--- | :--- | :--- | :---: |
| `test_z_score_spike_detection` | Z-Score Outlier Analysis | Accurately identifies extreme amount spikes ($Z \ge 3.0$), tags `TRANSACTION_SPIKE`, and stores the numerical Z-score. | **PASS** |
| `test_iqr_outlier_bounds` | Interquartile Range Bounds | Computes $Q_3 + (1.5 \times \text{IQR})$ boundary, population mean, and standard deviation in record metadata. | **PASS** |
| `test_minimum_sample_size_refusal` | Sample Size Safety Gate | Gracefully returns an empty anomaly list when observations $N < 4$, preventing spurious outlier calculations on tiny datasets. | **PASS** |
| `test_clean_baseline_zero_false_positives` | Statistical Specificity | Guarantees zero false-positive alerts on homogeneous baseline transactions within normal variance. | **PASS** |
| `test_severity_escalation_rules` | Severity Classification | Classifies extreme spikes ($Z \ge 4.0$ or $> Q_3 + 3\times\text{IQR}$) as `HIGH`, and moderate outliers as `MEDIUM`. | **PASS** |
| `test_provenance_row_reference` | Row-Level Audit Lineage | Captures exact source row index (`source_row == 17`) and file name (`ledger_march_2026.csv`). | **PASS** |

---

## Mathematical Formulation
- **Standardized Z-Score**:
  $$Z_i = \frac{|x_i - \mu|}{\sigma}, \quad \text{where } \sigma = \sqrt{\frac{1}{N-1} \sum_{j=1}^N (x_j - \mu)^2}$$
- **IQR Threshold Bounds**:
  $$\text{Upper Bound} = Q_3 + (1.5 \times \text{IQR}), \quad \text{Lower Bound} = \max\left(0, Q_1 - (1.5 \times \text{IQR})\right)$$
- **Severity Escalation Threshold**:
  $$\text{Severity} = \begin{cases} \text{HIGH}, & \text{if } Z_i \ge 4.0 \text{ or } x_i > Q_3 + 3.0 \times \text{IQR} \\ \text{MEDIUM}, & \text{otherwise} \end{cases}$$
