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

## 5a. Trend & forecasting

Domain-agnostic, registered as ordinary core KPIs. Built on the same series machinery
as `revenue_by_month`, in `app/analytics/timeseries.py` (descriptive) and
`app/analytics/forecast.py` (estimates).

### The honesty stance

A small business's data is short and noisy, and **a confident wrong forecast is worse
than no forecast**. So this module deliberately:

- prefers simple, explainable methods and only climbs to a statistical model when
  there is genuinely enough regular history;
- always returns a **range**, never a bare number;
- **refuses to forecast** when history is too short or too gappy, saying exactly how
  much data exists versus how much is needed;
- names the method and tier behind every number, so nothing looks more certain than it is.

`KPIResult` gained three optional fields for this: `method` (what produced the number),
`series` (observed history) and `forecast` (estimated future points, each with
`lower`/`upper`). A measured fact has `series` but no `forecast`.

### The tier ladder

| Tier | Method | When | Predicts? |
|---|---|---|---|
| 0 | Descriptive trend — period totals, moving average, growth %, movers | always | **no** |
| 1 | Moving-average baseline — mean of the last `w` periods | default forecast | yes |
| 2 | Exponential smoothing (Holt, level + additive trend, statsmodels) | ample regular history | yes |

**Auto-selection is deterministic**: take the highest tier whose data requirement is
met, else drop down; below the Tier-1 minimum, refuse. A Tier-2 fit that raises or
returns non-finite values **falls back to Tier 1 with a note** — never to a crash and
never to silence. Prophet and ML frameworks are out of scope (too heavy and fragile for
this data and hardware); a test asserts they are not imported.

> Tier 2 is import-guarded. `statsmodels` is declared in `requirements.txt`, but if it
> is not installed and history would have justified it, the result says so in
> `provenance.assumptions` and Tier 1 is used instead.

### The two gates before any forecast

1. **Enough periods** — `forecast_tier1_min_periods` (default 4).
2. **Enough of them observed** — `forecast_min_observed_ratio` (default 0.6).

The second gate is the important one. The shipped sales fixture has January, February
and September only: zero-filling the six-month hole would produce nine "periods" and a
plausible-looking average. Instead it is refused:

> *history is too gappy to forecast: only 3 of 9 monthly periods contain any
> transactions (33%), below the 60% minimum. The gaps are more likely to be missing
> records than genuine zero-sales periods.*

The same data at **weekly** granularity over Jan–Feb is contiguous and forecasts fine.

### Missing periods

A calendar period with no transactions is materialised as `0` — a month with no sales
genuinely earned nothing — but **only between the first and last observed period**. The
series is never extended past the data. Each point carries `observed: true|false`, and
the observed ratio drives gate 2 above.

### The uncertainty band

`lower/upper = value ± multiplier × spread × sqrt(steps ahead)`

- `spread` is the **RMSE of the method's own past errors**, measured about zero rather
  than about the mean error. This matters: on a steadily rising series a flat baseline
  is wrong by a similar amount every period, so those errors have almost no *scatter* —
  a standard deviation would report near-zero uncertainty for a forecast that is
  reliably too low. RMSE counts that systematic bias as the error it is.
- `sqrt(steps ahead)` widens the band the further out the estimate goes.
- The lower bound is **clamped at zero**; negative sales are not a thing.
- A **floor of `forecast_min_band_ratio`** (default 5% of the estimate) always applies.
  In-sample residuals systematically understate out-of-sample error: exponential
  smoothing fits a clean linear series almost perfectly, so its residuals are near zero
  and the band would otherwise collapse to zero width — total certainty about the future
  from a dozen data points. The floor makes that impossible.

This is a **volatility band, not a rigorous prediction interval**, and the `method`
string says so.

### The KPIs

| Key | Tier | Unit | What |
|---|---|---|---|
| `revenue_trend` | 0 | percent | Revenue per period + moving average; headline is period-over-period growth % |
| `units_trend` | 0 | percent | Same over quantity |
| `top_rising_products` | 0 | PKR | Biggest growers, recent window vs the one before |
| `top_declining_products` | 0 | PKR | Biggest fallers |
| `revenue_forecast` | 1–2 | PKR | Next N periods with bands |
| `demand_forecast` | 1–2 | count | Next N periods of units |
| `product_demand_forecast` | 1–2 | count | One product, from its own history (**requires `product_id`**) |

Growth % is undefined (not infinite) when the previous period was zero, and says so.
Movers report `change_pct: null` for a product with no prior-window sales — a new
seller, not an infinite riser — and sort by absolute change so ordering stays defined.

### Reference date

Injected via `KPIFilters.as_of`, exactly like the expiry pack: history is truncated
there, so trends, "recent vs prior" windows and forecasts are all reproducible.
`now()` is never read inside the maths.

### API

| Endpoint | Purpose |
|---|---|
| `POST /api/analytics/trend` | Descriptive only: series, moving average, growth |
| `POST /api/analytics/forecast` | Series + forecast points + bands + method |

Both accept `metric` (`revenue`/`units`), `granularity`, `horizon`, `product_id` and
`as_of`. Passing `product_id` to `/forecast` selects the per-product forecast.

### Chatbot

Trend/forecast intents live in the engine's core rules (they are domain-agnostic):
"forecast", "predict", "next month", "trend", "growth", "rising", "declining",
"how many will I sell", "kitni sale hogi". The pharmacy pack adds its own per-item
demand framing ("demand", "kitna mangwana", "reorder"), which routes to the
per-product forecast first.

Which product a question names is resolved **from the data**, not guessed: a question
word must match a whole word of a real `product_id` and carry at least four letters, so
"500mg" or "tab" can never match. Longest match wins, ties break alphabetically.

The analytics system prompt requires the LLM to present anything with
`"is_estimate": true` as a range ("roughly X, likely between A and B"), never to narrow
it, and to state the reason verbatim when a figure is `unavailable`. The number always
comes from code.

### Config

| Setting | Default | Purpose |
|---|---|---|
| `forecast_granularity` / `forecast_granularities` | `monthly` / daily,weekly,monthly | Period size |
| `forecast_horizon` | `3` | Periods ahead |
| `forecast_tier1_min_periods` | `4` | Below this: refuse |
| `forecast_tier2_min_periods` | `12` | Above this: try statsmodels |
| `forecast_min_observed_ratio` | `0.6` | Gappy-history gate |
| `forecast_baseline_window` | `3` | Periods averaged for the Tier-1 level |
| `forecast_band_multiplier` | `1.96` | Band width |
| `forecast_min_band_ratio` | `0.05` | Floor: band is never narrower than ±5% of the estimate |
| `forecast_enable_tier2` | `true` | Gate for the optional tier |
| `trend_moving_average_window` | `3` | Smoothing window |
| `trend_movers_window` / `trend_movers_top_n` | `1` / `5` | Movers comparison |

---

## 6a. Pharmacy expiry analytics (domain pack)

The first domain KPI pack, registered onto the same engine via
`PharmacyDomainPack.register_kpis`. Lives in `app/analytics/domains/pharmacy.py` —
**not** in engine core, which stays grep-clean of domain words (enforced by
`test_engine_core_has_no_pharmacy_vocabulary`). Only the `pharmacy` domain sees these.

### Subject rows

These read a **stock-on-hand / inventory** table. A row contributes only when it has
a usable `expiry_date`, a `quantity` greater than zero, and a value basis. Stock with
zero quantity is not "at risk" and is excluded (its value is zero anyway).

If the frame carries `invoice_id`, it looks like transactions rather than shelf stock,
and an assumption note says so — the figures then describe the rows present, which may
not be current stock.

### Value basis

Stock value is `quantity x unit_value`.

| Basis | Meaning | Default |
|---|---|---|
| `cost` | Money the pharmacy actually loses | ✅ |
| `mrp` | Revenue foregone | opt-in |

Set per call via `filters.options = {"value_basis": "mrp"}`, or globally with
`expiry_value_basis`. The basis in force appears in every result's `formula`. If the
configured basis column is absent or entirely empty, the other is used and the
substitution is recorded in `assumptions`.

### Bucket banding — **banded, not cumulative**

Each item falls in exactly one bucket, so a pharmacist can act on "these expire first".
With the default `expiry_buckets_days = [30, 60, 90]`:

| Key | Window | Unit |
|---|---|---|
| `expired_stock_value` | `days_to_expiry < 0` | PKR |
| `expiring_value_30d` | `0 <= days <= 30` | PKR |
| `expiring_value_60d` | `31 <= days <= 60` | PKR |
| `expiring_value_90d` | `61 <= days <= 90` | PKR |
| `near_expiry_total` | `0 <= days <= 90` — **cumulative** headline | PKR |
| `near_expiry_item_count` | distinct product/batch pairs, `0..90` | count |
| `expired_item_count` | distinct product/batch pairs, already expired | count |
| `expiry_by_manufacturer` | near-expiry value grouped by manufacturer/supplier | PKR |

The bucket keys are generated from config: setting `expiry_buckets_days = [15, 45]`
registers `expiring_value_15d` and `expiring_value_45d` instead.

**Boundaries** are inclusive at the top of each band: exactly 30 days is in the 30-day
bucket, exactly 60 in the 60-day bucket. Stock expiring **today** (0 days) counts as
near-expiry, not expired. The three buckets are disjoint and sum to `near_expiry_total`.

### Reference date

"Today" is injected through `KPIFilters.as_of` (`YYYY-MM-DD`) and defaults to the
current date. `datetime.now()` is never read inside the maths, so tests pin a date and
a user can ask "as of month-end". The date used appears in each result's `formula` and
in `provenance.filters`. An unparseable `as_of` falls back to today **with a note**.

### Expiry parsing and exclusions

`expiry_date` is consumed **already normalized** by the schema pipeline (which handles
`mm/yy`, `mm-yyyy` → last day of month, day-first strings, Excel serials). No formats
are re-parsed here.

Rows dropped for a missing/unparseable expiry, a missing or non-positive quantity, or a
missing unit value are **counted and reported** in `provenance.assumptions` — so a total
is never silently understated. An empty window over good data is a real `0.0` with
`status="ok"`; a missing `expiry_date` or `quantity` column is `unavailable` with a reason.

### Item breakdown

Every value KPI returns the at-risk items: `product_id`, `batch_no`, `expiry_date`,
`days_to_expiry`, `quantity`, `unit_value`, `line_value`, `source_row` — sorted **soonest
expiry first**, then product, then batch (sorted for action, not by value), capped at
`expiry_breakdown_top_n`.

### API and chatbot

`POST /api/analytics/expiry-report` returns every expiry KPI in one call, accepting
`as_of` and `value_basis`. Individual KPIs work through the normal
`POST /api/analytics/kpi/{key}`.

Expiry questions route to these KPIs through `PharmacyDomainPack.kpi_question_rules`,
checked **before** the core rules. English and Roman-Urdu triggers are supported
("expiring", "near expiry", "already expired", "kitna stock expire ho raha hai",
"khatam ho raha"). The vocabulary lives on the pack, so the chatbot and the KPI core
never learn domain nouns. As always the number comes from the KPI; the LLM only narrates it.

### Config

| Setting | Default | Purpose |
|---|---|---|
| `expiry_buckets_days` | `[30, 60, 90]` | Bucket edges; drives the registered keys. |
| `expiry_value_basis` | `cost` | Default valuation basis. |
| `expiry_breakdown_top_n` | `25` | At-risk items listed per breakdown. |

---

## 7. Registering a new KPI

Adding a KPI is **additive** — core code is never edited. This is the hook the expiry
pack uses today and the forecasting module will use next.

A domain pack implements `register_kpis`, which the engine calls **lazily**, once, the
first time it is asked for work in that domain:

```python
# app/schema/<yourdomain>.py
class GroceryDomainPack(DomainPack):
    def register_kpis(self, engine) -> None:
        from app.analytics.domains.grocery import register
        register(engine, domain=self.name)

# app/analytics/domains/grocery.py
from app.analytics.engine import KPISpec
from app.analytics.kpi import build_provenance   # provenance helper

def wastage_value(df, filters: KPIFilters, domain: str = "") -> KPIResult:
    ...  # pure pandas; return a KPIResult with real provenance

def register(engine, domain="grocery"):
    engine.register(KPISpec(
        key="wastage_value", name="Wastage Value", unit="PKR",
        definition="Value of stock written off.",
        fn=wastage_value, domain=domain, tags=("money", "risk"),
    ), replace=True)
```

A spec with a `domain` is returned by `list_kpis(domain)` and included in
`compute_all(df, domain=...)` — and is invisible to every other domain. Re-registering an
existing key raises unless `replace=True` (domain packs pass `replace=True` so reloading
is idempotent).

Add question vocabulary by overriding `kpi_question_rules` on the pack; it is consulted
before the engine's core rules, so a domain can claim its own words without any edit to
the chatbot or the engine.

Available helpers: `build_provenance`, `_amount_series`, `_cogs_series`, and
`classify_transactions` in `app/analytics/kpi.py`. Use `filters.as_of` for a reference
date and `filters.option("name")` for KPI-specific options — both land in provenance
automatically.

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

**Built:** the framework (result contract, provenance, registry, filtering, engine, seam,
API), the core domain-agnostic KPIs, trend & forecasting (§5a), and the pharmacy expiry
pack (§6a) attached through the domain hook with no core edit.

**Deliberately out of scope**, by design rather than omission:

- Prophet, ARIMA auto-search, and ML forecasting frameworks — too heavy and too fragile
  for this data volume and this hardware. The tier ladder stops at exponential smoothing.
- Any forecast on history that fails the two gates in §5a. Refusing is the feature.
