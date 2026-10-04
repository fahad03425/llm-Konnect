# Test Suite Report 01: Deterministic Core Financial KPIs

## Overview
- **Module**: Module 6.6 — Financial Analytics and KPI Engine Module
- **Target Files**: `backend/app/analytics/kpi.py` & `backend/app/analytics/engine.py`
- **Components**: Financial Arithmetic Engine, Transaction Classifier

## Scope & Architectural Verification
This test suite verifies the core mathematical integrity of financial KPI calculations:
1. **100% LLM-Free Arithmetic**: Guarantees all calculations run exclusively in pure vectorized pandas on CPU. Zero LLM prompts or network calls are utilized.
2. **Gross & Net Revenue**: Verifies `total_revenue` accurately sums sale transactions (excluding refunds and expenses).
3. **Operational Expenses & Refunds**: Verifies segregation of negative refund cash flows and vendor/utility expenses.
4. **Profit & Margin Metrics**:
   - $\text{Net Profit} = \text{Revenue} - \text{Expenses} - \text{Refunds}$
   - $\text{Gross Profit} = \text{Revenue} - \text{COGS}$
   - $\text{Gross Margin \%} = (\text{Gross Profit} / \text{Revenue}) \times 100$
   - $\text{Net Margin \%} = (\text{Net Profit} / \text{Revenue}) \times 100$
   - $\text{Refund Rate \%} = (\text{Refunds} / \text{Revenue}) \times 100$
5. **Volume Metrics**: Verifies distinct transaction invoice counts (`transaction_count`), total physical units sold (`units_sold`), and Average Transaction Value (`average_transaction_value = revenue / transaction_count`).

## Test Cases Summary
| Test Method | Focus Area | Expected Outcome |
| :--- | :--- | :--- |
| `test_total_revenue_calculation` | Sales Revenue Aggregation | Computes exact PKR sum: 3200.0. |
| `test_total_expenses_calculation` | Expense Aggregation | Computes exact PKR sum: 1000.0. |
| `test_total_refunds_calculation` | Return/Refund Aggregation | Computes exact absolute PKR sum: 100.0. |
| `test_net_profit_calculation` | Net Profit Math | Evaluates: $3200 - 1000 - 100 = 2100.0$. |
| `test_gross_profit_and_gross_margin_percentage` | Gross Profit & Margin % | Gross profit = 965.0, Gross margin = 30.16%. |
| `test_net_margin_and_refund_rate_percentages` | Margin & Refund Ratios | Net margin = 65.62%, Refund rate = 3.12%. |
| `test_volume_and_average_transaction_value` | Volume & ATV | Units = 49, Invoices = 5, ATV = PKR 640.0. |
