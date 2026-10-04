# Test Report 04: Inventory Shrinkage & Domain-Specific Detectors

## Executive Summary
This report validates the inventory reconciliation and domain-specific detectors in Module 6.7. Beyond generic financial anomalies, specialized domains require unique heuristics:
1. **Pharmacy / Brick-and-Mortar Retail**: Physical stock movement must match POS transaction outflows; otherwise, theft, breakage, or unrecorded dispensing has occurred.
2. **E-Commerce**: Monitors margin erosion (negative unit margins), return surges by SKU, and sudden customer review rating collapses.

---

## Test Cases & Specifications

| Test Case | Domain & Target | Tested Behavior | Status |
| :--- | :--- | :--- | :---: |
| `test_stock_movement_mismatch_shrinkage` | Pharmacy / Retail Inventory | Detects physical inventory loss (60 units removed vs 10 recorded sales $\implies$ 50 units discrepancy). | **PASS** |
| `test_stock_tolerance_allows_normal_variance` | Inventory Tolerance | Allows minor variances ($\le 5\%$ tolerance buffer) for measurement precision without raising alerts. | **PASS** |
| `test_ecommerce_margin_erosion` | E-Commerce Pricing | Flags items sold at a loss below wholesale cost ($45 price vs $75 unit cost). | **PASS** |
| `test_ecommerce_refund_surges` | E-Commerce Product Quality | Identifies products with refund rates $> 20\%$ across transactions. | **PASS** |
| `test_ecommerce_review_rating_drop` | E-Commerce Satisfaction | Identifies products with critically low customer ratings ($< 3.0$ stars). | **PASS** |
| `test_domain_aware_routing` | Domain Selector | Verifies that `detect_all_anomalies` dynamically activates relevant domain packages without cross-domain pollution. | **PASS** |

---

## Inventory Shrinkage Mathematical Model
- **Stock Discrepancy Formula**:
  $$\Delta_{\text{stock}} = (\text{Opening Stock} - \text{Closing Stock}) - \text{Quantity Sold}$$
- **Tolerance Gate**:
  $$\text{Discrepancy Triggered} \iff \Delta_{\text{stock}} > \max\left(0.01, 0.05 \times \text{Quantity Sold}\right)$$
- **Severity Rating**:
  $$\text{Severity} = \begin{cases} \text{HIGH}, & \text{if } \Delta_{\text{stock}} \ge 10 \text{ units or } \frac{\Delta_{\text{stock}}}{\text{Sold}} > 20\% \\ \text{MEDIUM}, & \text{otherwise} \end{cases}$$
