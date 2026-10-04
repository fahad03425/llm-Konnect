# Test Suite Report 03: Pre-filtering & Dimensional Breakdowns

## Overview
- **Module**: Module 6.6 — Financial Analytics and KPI Engine Module
- **Target Files**: `backend/app/analytics/filters.py` & `backend/app/analytics/kpi.py`
- **Components**: `KPIFilters`, `apply_filters`, Grouped Breakdown Aggregations

## Scope & Architectural Verification
This test suite verifies consistent dimensional slicing and multi-group aggregations:
1. **Unified Filter Execution**: Verifies that `apply_filters` executes once prior to computing any metric, ensuring all metrics in a report cover the exact same dataset slice.
2. **Date & Calendar Slicing**: Validates inclusive date boundaries (`date_from`, `date_to`) and year/month filters.
3. **Categorical Slicing**: Validates exact filtering by `product_id`, `category`, `supplier_id`, and `customer_id`.
4. **Grouped Dimensional Breakdowns**:
   - `revenue_breakdown_by_product`: Validates Top-N sorting descending by money amount (Panadol: 1500, Brufen: 1200, Amoxil: 500).
   - `expense_breakdown_by_category`: Validates grouping expenses by business category (Overhead: 500, Supplies: 500).
   - `revenue_breakdown_by_supplier`: Validates attribution by vendor (GSK: 1500, Abbott: 1200, Pfizer: 500).
   - `quantity_breakdown_by_product`: Validates aggregation of unit volumes.

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_apply_filters_date_range_slice` | Date Range Slicing | Restricts dataset to March 1-3; revenue equals PKR 1400. |
| `test_apply_filters_product_and_category_slice` | Categorical Filtering | Scopes revenue to Panadol (1500) and Antibiotics (500). |
| `test_revenue_breakdown_by_product` | Product Revenue Breakdown | Groups by product and sorts descending by amount. |
| `test_expense_breakdown_by_category` | Expense Category Breakdown | Groups overhead and supply expenses accurately. |
| `test_revenue_breakdown_by_supplier` | Vendor Revenue Attribution | Attributes sales by pharmaceutical manufacturer. |
| `test_quantity_breakdown_by_product` | Unit Volume Aggregation | Sums physical units: Panadol 30, Brufen 15, Amoxil 4. |
