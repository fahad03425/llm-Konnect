# Test Report 02: Duplicate Invoice & Collision Detection

## Executive Summary
This report documents the verification of `detect_duplicate_invoices` in Module 6.7. In retail and accounting systems, duplicate records manifest in two distinct forms:
1. **Exact Transaction Duplicates**: Identical records produced when an import file is accidentally processed twice or a POS till double-submits.
2. **Invoice Identifier Collisions**: Re-use of the same invoice number for distinct sales across conflicting dates or different customer accounts.

All duplicate checks execute deterministically in Pandas code with zero LLM intervention.

---

## Test Cases & Specifications

| Test Case | Verification Scope | Tested Behavior | Status |
| :--- | :--- | :--- | :---: |
| `test_exact_duplicate_invoice_detection` | Exact Duplicates | Identifies identical rows (`INV-DUP-100`), tags `Severity.HIGH`, records duplicate count (2), and captures matching source rows `[10, 11]`. | **PASS** |
| `test_invoice_id_collision_conflicting_dates` | Date Collisions | Flags `INV-COL-200` appearing on both `2026-03-02` and `2026-03-15`. | **PASS** |
| `test_invoice_id_collision_conflicting_customers` | Customer Account Collisions | Flags `INV-COL-200` assigned to different customers (`CUST-02` vs `CUST-09`). | **PASS** |
| `test_placeholder_invoice_ids_ignored` | Data Sanitization | Correctly ignores unassigned/placeholder identifiers (`""`, `"nan"`, `"null"`), avoiding spurious false collisions. | **PASS** |
| `test_empty_dataframe_safety` | Input Resilience | Gracefully returns an empty list when input DataFrames are empty or lack an `invoice_id` column. | **PASS** |

---

## Mathematical & Algorithmic Rules
- **Exact Duplicate Mask**:
  $$\text{DupMask} = \text{DataFrame.duplicated}(\text{subset}=[\text{invoice\_id}, \text{date}, \text{product\_id}, \text{amount}], \text{keep}=\text{False})$$
- **Collision Condition**:
  $$\text{Collision}(field) \iff \text{nunique}(field \mid \text{invoice\_id}) > 1$$
- **High Severity Assignment**:
  Duplicate invoices are unconditionally tagged with `Severity.HIGH` because duplicate entries distort revenue totals, tax liabilities, and customer billing ledgers.
