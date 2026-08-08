# Module 6.6 — Financial Analytics & KPI Engine

The KPI engine is the **single source of numeric truth** for LLM-Konnect. Every figure the
app shows — in a report (6.8) or in a chat answer (6.5) — comes from here.

> **Code computes. The LLM narrates. A verifier checks.**
>
> This module is the "code computes" half. **No figure in LLM-Konnect is ever produced by
> the LLM.** Nothing under `backend/app/analytics/` imports the LLM client, and a test
> (`test_analytics_package_never_imports_the_llm`) asserts it so the guarantee cannot rot.

Everything here is pure pandas on CPU: offline, no network, no GPU, no randomness.

---

## 1. What the engine consumes

The **canonical, validated DataFrame** produced by the existing pipeline:

```
connector (app/connectors) -> map_headers + apply_mapping (app/schema) -> validate (app/schema)
                                                                              |
                                                                              v
                                                                    KPIEngine.compute_all
```

Relevant canonical fields: `date`, `amount`, `cost`, `quantity`, `unit_price`, `category`,
`product_id`, `supplier_id`, `customer_id`, `invoice_id`, `txn_type`, plus the traceability
columns `source_row` and `source_connector` that the connectors attach to every row.

---

## 2. The result contract

Every KPI — core today, pharmacy/forecasting later — returns the same `KPIResult`
(`app/analytics/models.py`), and it is JSON-serializable via `.to_dict()`.

| Field | Meaning |
|---|---|
| `key` | Stable identifier, e.g. `total_revenue`. Never changes. |
| `name` | Human-readable label. |
| `value` | The number, or `None` when `status == "unavailable"`. |
| `unit` | `PKR`, `percent`, or `count`. |
| `formula` | The exact definition used, e.g. `sum of amount where txn_type is a sale`. |
| `status` | `ok` or `unavailable`. |
| `reason` | Why it is unavailable. Populated **only** when `status == "unavailable"`. |
| `period` | `{start, end}` — the date span the contributing rows actually cover. |
| `breakdown` | Small grouped table (list of dicts), for breakdown KPIs. |
| `breakdown_columns` | Column order for the breakdown table. |
| `provenance` | See below. |

### 2.1 Provenance — the important half

Provenance is a first-class output. It is what lets a report prove where a number came from.

| Field | Meaning |
|---|---|
| `filters` / `filter_description` | The exact slice the figure covers. |
| `row_count` | How many canonical rows contributed. |
| `source_rows` | The `source_row` values of those rows, sorted. |
| `source_rows_truncated` | `True` when `source_rows` is a capped sample (`analytics_max_provenance_rows`, default 500). `row_count` is always the true total. |
| `sources` | Origin labels — `source_file` when present, else `source_connector`. |
| `columns_used` | Which canonical columns the computation actually read. |
| `assumptions` | **Every material assumption, stated explicitly.** |

`source_rows` is exact, not approximate: filtering the input frame to those rows and
re-summing reproduces `value`. `test_provenance_rows_match_contributing_rows` enforces this.

### 2.2 `unavailable` is not zero

A KPI that cannot be computed returns `status="unavailable"`, `value=None`, and a `reason`.
It never crashes and it never returns a silently-wrong `0`. This distinction matters most in
analytics, where a plausible-looking wrong number is the worst possible failure.

The two are genuinely different:

- Sales data with a `txn_type` column and no refund rows → `total_refunds = 0.0`, `status="ok"`.
  A real, auditable zero.
- Sales data with **no** `txn_type` column → `total_refunds` is `unavailable`, because refund
  rows cannot be identified at all.

---

## 3. Core KPIs (domain-agnostic)

All fifteen work unchanged on a grocery dataset. No domain vocabulary appears in core code.

| Key | Unit | Definition |
|---|---|---|
| `total_revenue` | PKR | Sum of `amount` over sale rows. |
| `total_expenses` | PKR | Sum of `amount` over expense/purchase rows. |
| `total_refunds` | PKR | Sum of `|amount|` over refund/return rows (direction-independent). |
| `net_profit` | PKR | `total_revenue - total_expenses - total_refunds`. |
| `gross_profit` | PKR | `sum(amount) - sum(cost x quantity)` over sale rows **that have a cost**. |
| `gross_margin_pct` | percent | `gross_profit / revenue of costed sale rows x 100`. |
| `net_margin_pct` | percent | `net_profit / total_revenue x 100`. |
| `refund_rate_pct` | percent | `total_refunds / total_revenue x 100`. |
| `transaction_count` | count | Distinct `invoice_id` over sale rows; sale row count when `invoice_id` is absent. |
| `average_transaction_value` | PKR | `total_revenue / transaction_count`. |
| `expense_breakdown_by_category` | PKR | Expense `amount` grouped by `category`. |
| `expense_breakdown_by_supplier` | PKR | Expense `amount` grouped by `supplier_id`. |
| `revenue_breakdown_by_category` | PKR | Sale `amount` grouped by `category`. |
| `revenue_breakdown_by_product` | PKR | Sale `amount` grouped by `product_id`, top-N. |
| `revenue_by_month` | PKR | Sale `amount` grouped by calendar month. |

### 3.1 Definitions that need stating precisely

**Net profit** subtracts refunds *separately* from expenses. Refunds are classified out of the
sale mask, so a source that tags a row `sale return` is counted once as a refund and never as
revenue — otherwise refunds would be double-counted.

**Gross margin** divides by the revenue of the **costed rows only**, not by total revenue.
Dividing by total revenue would understate margin whenever some rows lack a cost.

**COGS**: `cost` is a *unit* cost in the canonical schema, so COGS is `cost x quantity`. When
there is no `quantity` column, `cost` is taken as a per-row line cost and that is recorded in
`assumptions`.

**Amount**: prefers the canonical `amount` (line total). If `amount` is absent but
`unit_price` and `quantity` are present, amount is derived as `unit_price x quantity` and the
derivation is recorded in `assumptions`.

**`revenue_by_month` is a plain historical aggregation.** It fits no trend and predicts
nothing. Forecasting is a separate, later module.

### 3.2 Transaction classification

`txn_type` is matched against generic business vocabulary (no domain nouns):

- **sale**: sale, sales, sold, sell, invoice, pos, revenue, income
- **expense**: expense, expenses, purchase, purchases, buy, bought, bill, payable, cogs, overhead
- **refund**: refund, refunds, return, returns, returned, creditnote, reversal

Rows matching nothing are excluded and counted in `assumptions`.

**When the `txn_type` column is absent entirely** — which is the case for the plain sales
exports in `data/samples/` — every row is treated as a sale for revenue purposes, and that
assumption is written into the provenance of every affected result. Expenses and refunds are
then reported as `unavailable`, never as `0`.

### 3.3 Safety rules

- **Zero division**: a zero denominator yields `unavailable` + reason. `NaN` and `inf` never escape.
- **Empty frame / filter matching nothing**: every KPI returns `unavailable` + reason.
- **Missing column**: `unavailable` naming the missing column. Never a crash.
- **Unparseable values**: coerced with `errors="coerce"` and excluded; excluded rows never
  appear in `source_rows`.
- **Determinism**: breakdowns sort by amount descending, then by group label ascending, so ties
  never reorder between runs. Input row order does not affect any result.

---

## 4. Filtering

`KPIFilters` supports `date_from`, `date_to` (both inclusive), `month`, `year`, `category`,
`product_id`, `supplier_id`, `customer_id`, `txn_type`. Exact-match filters are
case-insensitive.

Filters are applied **once, up front**, so every KPI in a pack covers an identical slice, and
the slice is echoed in each result's provenance. A filter naming a column that does not exist
is **not** silently ignored — it is recorded in `assumptions`.

---

## 5. The chatbot seam (Module 6.5)

`app/analytics/seam.py` **supersedes** the temporary in-module pandas fallback that shipped
inside `app/rag/router.py` in 6.5. `AnalyticsRouter` keeps the original signature —
`compute(question, filters, kb_records) -> (computed_values, source_rows)` — so
`app/rag/chat.py` required **no call-site change**. `app/rag/router.py` now re-exports the
engine-backed implementation under the same name.

The question → KPI mapping is deterministic keyword matching (`select_kpi_keys`). The LLM
neither picks the metric nor computes the number; it receives `computed_values` and narrates
them. Unavailable results are passed through too, so the chatbot can say "that can't be
computed from your data, because ..." instead of inventing a figure.

---

## 6. API

| Endpoint | Purpose |
|---|---|
| `POST /api/analytics/kpis` | Compute the full KPI pack for a source. |
| `POST /api/analytics/kpi/{key}` | Compute a single KPI. |
| `GET  /api/analytics/kpis?domain=` | List registered KPIs and their definitions. |

Endpoints are thin: they connect a source through connectors → schema → validation, then call
the engine. All arithmetic lives in `app/analytics/`.

```jsonc
// POST /api/analytics/kpis
{
  "file_path": "data/samples/challenging_pharma_sales.csv",
  "domain": "pharmacy",
  "filters": { "month": 1 },
  "include_validation": true
}
```

---

## 7. Registering a new KPI

Adding a KPI is **additive** — core code is never edited. This is the hook the pharmacy
expiry KPIs and the forecasting module will use.

```python
from app.analytics import engine, KPISpec, KPIResult, KPIFilters
from app.analytics.kpi import build_provenance

def near_expiry_value(df, filters: KPIFilters, domain: str = "") -> KPIResult:
    ...  # pure pandas; return a KPIResult with real provenance
    
engine.register(KPISpec(
    key="near_expiry_stock_value",
    name="Near-Expiry Stock Value",
    unit="PKR",
    definition="Value of stock expiring within the threshold window.",
    fn=near_expiry_value,
    domain="pharmacy",     # omit (None) for a core, domain-agnostic KPI
    tags=("money", "risk"),
))
```

A spec with `domain="pharmacy"` is returned by `list_kpis("pharmacy")` and included in
`compute_all(df, domain="pharmacy")` — and is invisible to every other domain. Re-registering
an existing key raises unless `replace=True`.

Helpers available to a new KPI: `build_provenance`, `_amount_series`, `_cogs_series`, and
`classify_transactions` in `app/analytics/kpi.py`.

---

## 8. Configuration

In `app/core/config.py` (env prefix `KONNECT_`):

| Setting | Default | Purpose |
|---|---|---|
| `analytics_currency` | `PKR` | Currency label for money KPIs. |
| `analytics_top_n` | `10` | Rows kept in top-N breakdowns. `value` stays the total across all groups. |
| `analytics_max_provenance_rows` | `500` | Cap on `source_row` ids stored per result. |

---

## 9. Scope boundary

**In 6.6:** the framework (result contract, provenance, registry, filtering, engine, seam,
API) plus the core domain-agnostic KPIs above.

**Deliberately NOT in 6.6**, and designed to slot in additively via `register`:

- Pharmacy-specific analytics (expiry value, near-expiry stock worth) — separate module.
- Forecasting and trend prediction — separate module. `revenue_by_month` is an aggregation,
  not a forecast.
