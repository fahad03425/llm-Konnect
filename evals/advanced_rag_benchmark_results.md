# Advanced pharmacy RAG benchmark results

## Scope and method

Ran the 75 single-dataset questions from `LLM-KONNECT_Advanced_RAG_Benchmark.docx` against the exact three CSVs in the active knowledge base: 25 sales, 25 inventory, and 25 purchases questions. The cases were scoped to one selected dataset each. The app read each selected file as a complete structured table and used the deterministic pharmacy tabular planner for calculations and filtered lists; exact record keys were resolved against the source table and cited with source-row references.

The report also describes multi-turn and cross-dataset cases. The current runner parses and scores only the 75 single-dataset questions; those additional cases have not been scored. The supplied report was used to find and fix defects, so its questions are a development benchmark, not an independent holdout.

## Results

The before and after answer files were scored with the same updated scorer. Number scoring ignores parenthetical boundary-exclusion notes and includes planner-computed values and explicit structured list counts. It is a benchmark-fact coverage proxy, not a human-rated semantic accuracy measure.

| Measure | Before | After |
|---|---:|---:|
| Cases run | 75 | 75 |
| Exact expected-number coverage | 8.8% | 100% |
| Mean expected-number recall | 16.8% | 100% |
| Route-family match | 90.7% | 100% |
| Citations on answerable cases | 80.3% | 100% |
| Expected source-row citation recall | 39.3% | 100% |
| Expected record-ID answer recall | 0% | 100% |
| Expected missing-data abstention | 0% | 100% |

The route score is based on a simple exact-ID-versus-table-query rubric, not a full intent taxonomy. The per-case pass rate is 75/75, and the 100% metric results apply only to the 75 tested cases and these CSVs. It does not establish 100% accuracy on other data, paraphrases, multi-turn questions, remote databases, or safety-sensitive questions.

## Root causes fixed

- **Schema mapping conflated stock on hand and transaction quantity.** Inventory `Stock` is now mapped to `stock_qty`; purchase and sale quantities retain their own ledger meaning. `Sale_ID` and `Purchase_ID` map to stable transaction keys, and supplier names remain searchable.
- **Exact IDs were sent through broad analytics or vector similarity.** A single explicit record key now routes to a bounded source-row lookup. Multi-ID calculations stay in the structured planner.
- **Generic date filters intercepted source-specific dates.** The pharmacy planner now handles inventory expiry dates and purchase dates before generic sales-date logic, including ISO and day-month-year ranges.
- **Entity predicates could disappear after the first filter.** Product, category, supplier, and location values are resolved against the full selected dataset, then intersected. An empty intersection returns no records instead of broadening the query.
- **Missing named suppliers could fall through to whole-dataset totals.** Explicit absent suppliers now abstain with a clear no-records response.
- **Wording collisions selected the wrong calculation.** Examples fixed include `Unit_Cost` versus inventory value, “cost paid” versus payment status, average transaction value versus maximum sale amount, and grouped payment status versus a single Paid filter.
- **Expected answers that listed all rows were incompletely represented.** Customer product history now includes the associated sale IDs; earliest/latest purchase answers list all purchase IDs on those dates.
- **The evaluator had measurement defects.** It previously counted excluded boundary values as expected outputs, missed expected IDs in answer text, and failed to recognize valid no-record responses. Those scorer issues were corrected; they were not system improvements.

## Representative failures and fixes

| Case | Before | After |
|---|---|---|
| D2-Q12 expiry range | Generic sales analytics said no usable transaction dates | Returned the 14 inventory records expiring in the inclusive range, with citations |
| D2-Q16 below-reorder percentage | Reported 329/329 (100%) after filtering the denominator first | Reported 329/2,162 (15.22%) |
| D2-Q21 highest inventory value | Returned the highest unit cost instead | Returned INV-21204 at PKR 455,270 |
| D2-Q24 nonexistent category/supplier combination | Listed 140 supplier rows after silently dropping the category constraint | Abstained: no matching inventory records |
| D3-Q18 absent supplier | Returned total purchase value for all 1,982 purchases | Abstained for absent supplier Al-Noor Medical Wholesale |
| D3-Q20 supplier comparison | Wrong broad supplier averages, then temporarily misread “supplier was cheaper” as an unknown supplier | Scoped to Hydrocolloid Dressing: PKR 751.10 vs 720.80; Kohsar was cheaper by PKR 30.30 |
| D3-Q24 product/supplier pair with no rows | Listed 83 supplier purchases after dropping product constraint | Abstained: no matching purchase records |

## Artifacts

- `advanced_rag_benchmark_cases.json` — parsed 75-case set, separate from runtime prompts and routing logic.
- `advanced_rag_benchmark_before_answers.json` — saved baseline answers and citations.
- `advanced_rag_benchmark_before.json` — baseline scores rescored with the same scoring rules as the final run.
- `advanced_rag_benchmark_after.json` — final answers, citations, computed values, and per-case scores.
- `backend/scripts/evaluate_report_benchmark.py` — repeatable benchmark runner; accepts the report DOCX and can export the parsed cases.

## Validation and remaining gaps

- Regression tests: **149 passed** across tabular planning and schema mapping suites.
- Full report benchmark: 75/75 cases passed the defined per-case checks against active file IDs for sales, inventory, and purchases.
- No medication-safety, clinical, legal, or regulatory cases exist in this report; safety performance remains unmeasured.
- The remaining 25 described multi-turn/cross-dataset cases need parser and scoring support. This is further engineering.
- The report has been used during tuning; an independently authored, held-out benchmark is still needed. This is evaluation/data work.
- PostgreSQL, MySQL/MariaDB, and SQL Server endpoints were unavailable for this run. Dialect-specific ingestion and query behavior still require real endpoints and representative schemas. This is integration verification and possibly connector engineering.
- CSV RAG calculations cannot answer joins or analytics that require missing ledger records or relationships. Those require source completeness and a join/query-planner product design.


