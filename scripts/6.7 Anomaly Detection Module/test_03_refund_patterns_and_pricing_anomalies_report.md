# Test Report 03: Abnormal Refund Patterns & Pricing Irregularities

## Executive Summary
This report evaluates the statistical detection of abnormal refund behaviors and pricing boundary violations. Financial leakages often go undetected because individual transactions appear technically valid in POS records. Module 6.7 aggregates refund frequency and value by customer/cashier entities and performs boundary sanity audits on discounts and line prices.

---

## Test Cases & Specifications

| Test Case | Method / Component | Tested Behavior | Status |
| :--- | :--- | :--- | :---: |
| `test_abnormal_refund_frequency_by_customer` | Entity Group Z-Score | Flags customer `CUST-ABNORMAL` scoring $Z \ge 2.5\sigma$ above peer average return frequency. | **PASS** |
| `test_abnormal_refund_total_volume` | Refund Magnitude Tracking | Measures total financial refund impact (PKR 2,100.00) against population expectation. | **PASS** |
| `test_zero_refund_dataset_safety` | Specificity Check | Confirms zero false alarms on clean ledgers lacking return transactions. | **PASS** |
| `test_unusual_discount_greater_than_amount` | Boundary Rule | Escalates to `Severity.HIGH` when discount amount exceeds line item gross value. | **PASS** |
| `test_unusual_discount_extreme_rate` | Ratio Outlier | Detects transactions with discount rate $> 50\%$ on standard retail inventory. | **PASS** |
| `test_negative_or_zero_price_detection` | Hard Floor Check | Flags sales items entered with $\le 0.00$ unit price (`INV-ZERO` and `INV-NEG`). | **PASS** |

---

## Key Invariants Verified
1. **Entity-Level Statistical Clustering**:
   - Groups by `customer_id`, `cashier`, or `product_id`.
   - Identifies entities deviating $> 2.5\sigma$ from cohort behavior.
2. **Discount Policy Boundaries**:
   - $\text{Discount Rate} = \frac{\text{Discount}}{\text{Gross Amount} + \text{Discount}}$.
   - Alerts triggered if $\text{Discount Rate} > 50\%$ or $\text{Discount} > \text{Gross Amount}$.
3. **Price Floor Integrity**:
   - Non-refund sales transactions must strictly possess $\text{Unit Price} > 0.00$.
