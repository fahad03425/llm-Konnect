# Pharmacy owner intent evaluation

## What was measured

`backend/tests/fixtures/pharmacy_owner_holdout.json` is the owner-question holdout. It has 57 cases covering inventory, purchasing, sales/finance, customers/prescriptions, operations, product attributes, compliance, and multi-part or adversarial requests. Each row records intended concept(s), route, expected evidence, and answer behavior. Keep this file out of routing anchors and prompt examples.

Run the deterministic route and vocabulary pass with:

```powershell
.\.test-python313\python.exe backend\scripts\evaluate_pharmacy_holdout.py
.\.test-python313\python.exe backend\scripts\evaluate_pharmacy_retrieval.py
```

The script writes `backend/pharmacy_holdout_results.json`. `intent_accuracy` means every annotated vocabulary concept was recognized; `route_accuracy` means the request reached its annotated broad route. These are not answer accuracy scores.

## Iterations

| Run | Route | Vocabulary concepts | Notes |
|---|---:|---:|---|
| Initial pass | 32/56 (57.1%) | 17/56 (30.4%) | Broad lexical route rules sent staff, prescription, and policy lookups to analytics; vocabulary coverage was fragmented. |
| First repair | 52/56 (92.9%) | 45/56 (80.4%) | Added operation-specific vocabulary and route precedence. Errors exposed missing shorthand, product attributes, and conflicting-source handling. |
| Final deterministic pass | 57/57 (100%) | 57/57 (100%) | Added narrowly scoped aliases and separated purchase totals, invoice totals, and sales; corrected inventory comparison, audit, and safety route precedence. |

The final 100% applies only to route and concept labels in this 57-case set. The set was inspected during iterative failure analysis; it is not an untouched external benchmark and does not establish broad real-world coverage. Three explicitly paired paraphrase groups had 3/3 matching routes and concepts; this small result does not measure answer consistency.

The holdout run also reports paraphrase consistency for repeated intent labels. It measures consistent route decisions across same-intent examples, not consistent answer wording or facts.

## Retrieval ranking experiment

`backend/tests/fixtures/pharmacy_retrieval_eval.json` is a separate eight-query check over the project's sample inventory CSV. `backend/scripts/evaluate_pharmacy_retrieval.py` runs the production row serializer, embedder, in-memory Chroma, and `KnowledgeBase.search`; the collection is ephemeral and does not touch the user's saved knowledge base.

| Run | Recall@5 | MRR@5 | nDCG@5 |
|---|---:|---:|---:|
| Before exact-ID reranking | 8/8 (1.00) | 0.823 | 0.866 |
| After candidate expansion and metadata ID reranking | 8/8 (1.00) | 1.000 | 1.000 |

The two failures before reranking were exact batch queries: `B2201` ranked 3rd and `G8801` ranked 4th. Embeddings matched the pharmacy topic but did not prioritize exact identifiers. Candidate expansion plus exact metadata matching moved those records to rank 1. This is a small, project-sample retrieval experiment and does not measure retrieval on the empty live KB or prove ranking quality across arbitrary pharmacy datasets.

## End-to-end metrics and limits

`backend/scripts/evaluate_pharmacy_answers_live.py` runs 18 case-level questions against an ephemeral Chroma collection with synthetic inventory, purchases, invoices, a prescription queue, and conflicting dated batch snapshots. It uses the configured local Ollama model and scopes RAG and analytics to the fixture so the saved workspace KB cannot contaminate the result. Cases are separate from prompt/routing examples, but were authored during implementation; this is a small development holdout, not an independent benchmark. Metrics are reported in `backend/pharmacy_answer_live_results.json`.

| Metric | Baseline before answer-path changes | Latest run |
|---|---:|---:|
| Overall case success | 12/18 (66.7%) | 18/18 (100%) |
| Factual answer accuracy | 9/12 (75.0%) | 12/12 (100%) |
| Missing-data abstention | 1/3 (33.3%) | 3/3 (100%) |
| Safety-sensitive answers | 5/5 (100%) | 5/5 (100%) |
| Forbidden-claim control | 18/18 (100%) | 18/18 (100%) |
| Citation case accuracy / source recall | 9/13 (69.2%) | 13/13 (100%) |
| Retrieval Recall@5 / MRR@5 / nDCG@5 | 100% / 0.737 / 0.804 | 100% / 0.756 / 0.818 |
| A01/A02 paraphrase consistency | failed | passed |

An initial unscoped run read the saved sample POS data instead of the synthetic holdout, so it was discarded. The baseline shown here is the first corrected, fixture-scoped run. Fixes then routed exact inventory lookups to source rows, formatted current batch stock and unit costs deterministically, and abstained on missing supplier lead time and invoice cost data. Retrieval ranking remained at full recall, while rank order varied because several semantically similar inventory rows appear in the top five. The final 18/18 result is measured on a tiny synthetic set with exact-match checks, not an independent accuracy estimate.

## Relational database ingestion check

`backend/tests/test_sql_database_ingest.py`, `backend/tests/test_sql_external_integration.py`, and `backend/tests/test_tabular_query.py` passed **15 focused local cases; 4 live-endpoint cases skipped**. The broader latest ingestion, schema-validation, and SQL suite passed **45 cases; 4 live-endpoint cases skipped**. Local tests verify URL scheme normalization and SQLAlchemy PostgreSQL/MySQL dialect construction, quoted table names containing spaces, complete reads, row totals/previews, API ingestion of discovered tables, row metadata, credential encryption/masking, injection-shaped table-name rejection, deterministic calculations over complete canonical frames, and merge behavior with missing IDs. Four opt-in smoke cases create, discover, read, preview, count, and clean up a scratch table on PostgreSQL, MySQL, MariaDB, and SQL Server when `KONNECT_TEST_*_URL` is configured.

### Ingestion strategy and missing-field warnings

Pharmacy ledger and SQL database ingestion now defaults to **row-by-row**. Each source row remains independently retrievable with its own identifiers and citation metadata. The previous UI default selected merge/grouping and the database UI explicitly sent `strategy: merge`. For CSVs or tables without a usable invoice/group key, the old merge loop could place unrelated null-key records in one chunk and retain only the first row's metadata. Merge now falls back to row-by-row if no usable key exists and emits rows with missing keys separately. Intentional merge remains available for adjacent line items that share a real invoice/bill key; row-by-row is still safer for direct item lookups and row-level citations.

The MRP and DRAP validation notices describe **missing source fields**. They do not prevent available columns and rows from being ingested, and they do not prove a product is unregistered. The assistant must abstain from a requested MRP or registration claim when the connected source does not contain that evidence. Supplying these fields requires better source data; choosing the correct regulator requires a product-level jurisdiction decision.

This is a connector/ingestion check, not a live cross-database accuracy benchmark. No PostgreSQL, MySQL/MariaDB, or SQL Server endpoint and credentials were available in the workspace, so remote driver connections, permissions, dialect-specific edge cases, and data completeness against external systems remain unverified. The SQL Server ODBC driver is also absent from this runtime. URL normalization does not prove that a driver can connect, and answer accuracy over external sources remains unmeasured.

### Report dataset end-to-end runs

The supplied `LLM-KONNECT_RAG_Questions_Answers_Solutions.docx` contains three uploaded ledgers (sales 2,327 rows, inventory 2,162 rows, purchases 1,982 rows), 45 single-turn cases, and nine multi-turn scenarios (29 turns). `backend/scripts/evaluate_report_benchmark.py` and `backend/scripts/evaluate_report_followups.py` exercise those source records through the application. The report examples were used to locate and fix failures, so these are development-set measurements, not an independent estimate of performance on new pharmacies.

| Measurement | Before report-driven repairs | Latest report run |
|---|---:|---:|
| Single-turn exact expected-number overlap (34 numeric cases) | 6/34 (17.6%) | 34/34 (100%) |
| Single-turn mean expected-number recall | 20.3% | 100% |
| Expected broad route family | Earlier route implementation | 44/45 (97.8%) |
| Answerable citation coverage | Not measured consistently | 100% |
| Expected source-row recall for answerable record cases | Not measured consistently | 100% |
| Missing-data abstention cases | Not measured consistently | 100% |
| Follow-up numeric overlap (26 numeric turns) | 24/26 (92.3%) in the first complete scenario run | 26/26 (100%) |
| Follow-up citation coverage (29 turns) | 28/29 (96.6%) | 29/29 (100%) |

Latest machine-readable results are `backend/report_final_v4.json` and `backend/report_followup_final_v9.json`; the initial report baseline is retained in `backend/report_benchmark_results.json`, and the first complete follow-up baseline is `backend/report_followup_final_v3.json`. Numeric overlap checks expected number strings; it does **not** prove full semantic correctness. The report evaluators also record route family, citations, expected evidence rows, and abstention, but do not independently judge every name, qualifier, or explanation. No safety-sensitive questions are present in the report. Multi-turn improvements include retaining the customer/product referents (including pack sizes), carrying branch/product constraints, routing exact contextual record follow-ups through deterministic row filtering, and keeping supplier/status results attached to their citations.

| Requested measure | Result |
|---|---|
| Intent accuracy | 57/57 (100%) on this iterated route/concept set |
| Retrieval recall/ranking | Sample inventory: Recall@5 8/8, MRR@5 1.000, nDCG@5 1.000 after reranking |
| Factual answer accuracy / source support | 12/12 (100%) answerable facts correct; citation case/source recall 13/13 (100%) |
| Missing-data behavior | 3/3 (100%) explicit abstention cases |
| Paraphrases | A01/A02 same-product stock variants both passed |
| Safety-sensitive answers | 5/5 (100%) on this synthetic holdout |

The checked-in examples are mainly inventory and POS data. They do not establish prescription state, insurance coverage, staff rosters, delivery tracking, audit changes, supplier lead times/payables, or current Pakistan regulatory rules. A vector match cannot fill those knowledge gaps. Legal and clinical responses must wait for an applicable authoritative, current source and the pharmacy jurisdiction; patient records also require scoped authorization.

## Remaining gaps by cause

- **Source data:** the Pakistan price/availability catalog is now indexed, but it does not contain operational prescription, payer, delivery, staff, audit, supplier invoice/payment/lead-time, or product recall records. Supply those records before measuring those answers. Provide jurisdiction-specific, authoritative, current legal and product-label sources.
- **Product decisions:** configure the pharmacy jurisdiction, access rules for patient/customer lookups, and what constitutes a reorder recommendation (threshold only versus forecast, demand window, and supplier lead time).
- **Further engineering:** build a larger blind, independently authored answer benchmark; add semantic scoring for names, filters, dates, explanations, citations, abstentions, adversarial guessing, safety responses, and paraphrase consistency; benchmark top-k ranking across large relational ledgers; evaluate follow-ups and configured model/provider combinations. Current report metrics are a development set and must not be generalized to unseen pharmacies' connected data.

The current holdouts and user report were authored or inspected during implementation; they are not independently authored. The requested larger independent answer-level benchmark remains outstanding. Run a blind set with expected source IDs and scoring rules against representative authorized data and the deployment model before claiming production factual accuracy.

## Representative root causes and fixes

- “Show sales records for invoice …” was sent to analytics because `sales` overpowered the record request. Exact records now take precedence unless the user asks for an aggregate.
- “What should I reorder?” and “Which items may sell out soon?” were treated as generic lookup or stock level questions. They now have separate concepts because recommendations require demand, threshold, and time-window evidence that a snapshot does not provide.
- Prescription, staff, legal, and product-attribute questions were caught by broad words such as “medicine”, “who”, and “how”. Route precedence now distinguishes record/knowledge lookups from calculations and comparisons.
- “Total purchases” was at risk of collapsing into “sales”; purchases and invoice totals have separate concepts.
- Short follow-ups now carry relevant prior products, customers, branches, periods, and exact record IDs into the correct deterministic or retrieval path. The supplied 29-turn session set passes its recorded number and citation checks; it remains a tuned report set and needs an independent follow-up evaluation.

The row-based planner improves exact counts, grouped totals, filters, extrema, and citations for one selected table. It does not infer unsupported joins or replace a database-native query planner for arbitrary multi-table questions. Cross-table business metrics still need a schema-aware SQL planner, safe join definitions, query limits, and evaluation on representative connected schemas.

## Existing regression suite

`backend/tests/test_product_catalog.py`, `backend/tests/test_schema_mapping.py`, `backend/tests/test_schema_mapping_hard.py`, and `backend/tests/test_validation.py` passed **172 focused cases** after the catalog repairs. `backend/tests/test_rag.py` includes a 100-question Roman Urdu routing regression and passed **130/130** in the prior regression run. `backend/tests/test_sql_database_ingest.py` and `backend/tests/test_sql_external_integration.py` passed **10 local cases**, with **4 endpoint checks skipped** because no URLs were available. These code checks complement, but do not replace, the small answer-level evaluation above.

The default pytest temp directory was inaccessible under the managed Windows sandbox; specifying a workspace-local basetemp resolved it.

## Pakistan pharmaceutical catalog: schema and answer-path repair

The user-supplied `pharmacy_dataset_rag_evaluation_report.md` reports 73/100 (73.5%) on 100 questions. Its root-cause discussion correctly points at catalog schema mapping and product lookup, but its “ground truth” reads the raw cells as if every row followed the headers. The workbook has 1,630 physical rows, of which 1,627 have a product name, 503 unique product names, and 182 distinct company strings. It includes no strength column, so the report's attribution of the three Zestril price points to 5/10/20 mg strengths is not supported by this workbook.

### Root causes found

- Before the correction, the catalog mapper treated `Price_After` as `quantity`, and did not preserve `Price_before`, the percent-valued `Discount` (“10% Off”), and `Availability` as separate canonical facts. That made price, discount, and stock-status questions semantically wrong even when retrieval found the correct row.
- 667 nonblank records have inconsistent cell placement: a stock label (`Available`, `Sold Out`, or `Add to cart`) is in `Pack_Size`, while the package description is in `Availability`. The mapper itself recognized the headers; the workbook rows were shifted. The importer now swaps only when the `Pack_Size` value is a recognized stock label and the other cell is not. “Add to cart” is preserved verbatim and treated as a purchasable/available status for availability calculations.
- Three rows are blank. Catalog calculations ignore blank product rows while preserving workbook row numbers for citations.
- The supplied 100-case scorer is not an independent factual judge. It awards some queries as correct when any citation or a broad keyword is present, and some hard-coded expected availability facts count only the unshifted cells. It also labels “this medicine/company” questions as contextual without ensuring the preceding turn exists. For that reason the legacy score is reported only as a comparable smoke metric, not as factual accuracy.

### Changes

- Catalog headers now map to distinct canonical `original_price`, `discounted_price`, `discount_pct`, `availability`, `manufacturer`, and `pack_size` fields. Percent strings parse numerically, catalog price does not become quantity, and row text embeds the evidence fields with corrected percent formatting.
- Deterministic full-table catalog queries now handle product details, counts with row/name units, price filters/extrema/top-N/ties, discount and availability aggregation, pack filters, company/product comparisons, and source-row citations. Ambiguous absent entities are clarified; likely one-token product typos are corrected only at a high similarity threshold and disclosed; unsupported strength-specific questions abstain; relative “expensive” and “affordable” are explicitly compared with the catalog-wide mean discounted price.
- Reindexed the active source (`file_ae2e68f26f42`) using row strategy and upserted the corrected row metadata/text in place. The active KB still contains 1,630 source-row chunks; blank product rows are excluded only from catalog math.

### Evaluation run

`backend/scripts/evaluate_product_catalog_100.py` reruns the supplied 100 questions through `RAGChat` against the active workbook. Results are in `backend/reports/product_catalog_100_results.json`. Each case gets a unique isolated session to prevent stale follow-up context from contaminating reruns.

| Measure | Latest result | Interpretation |
|---|---:|---|
| Legacy keyword heuristic | 90/100 | Comparable smoke score; not verified factual accuracy. The report's 73.5% baseline is likewise a weak rubric and uses incorrect raw availability assumptions. |
| Citation coverage for answerable cases | 81/81 | Citations were returned; citation relevance/source-row precision was not independently scored. |
| Missing-referent clarification | 19/19 | Contextless product/company questions requested the missing entity rather than guessing. |
| Supplemental development probes | 3/3 | Transparent typo correction, missing-strength abstention, and comparison of both named products. These probes were authored during repair and are not independent. |
| Intent, independently verified factual accuracy, safety, paraphrase consistency | Not measured | Requires blind annotated cases and a separate answer-level judge/review protocol. |
| Vector retrieval recall/ranking | Not measured by this run | Catalog calculations scan the selected table; semantic retrieval requires an independently labeled relevance set. |

Representative repaired facts: Zestril returns three distinct price/pack variants across nine source rows (PKR 202→182, 388→350, 748→673, all 10%); 664 of 1,627 nonblank product records are explicitly purchasable (537 labeled `Available`, 127 labeled `Add to cart`) and 963 are labeled `Sold Out`. The answer distinguishes record-level and unique-product counts; e.g., 199 distinct product names have at least one purchasable record. These counts differ from the report's 457/503 because it did not account for shifted cells. They are catalog availability labels, not live pharmacy inventory. The report's 139 “no discount” rows include the three blank rows; the valid named-product count with no recorded discount is 136.

Representative failures caught during evaluation: “What is the highest product price?” and “Show the 10 most expensive products” initially fell through to a generic list or returned one row; the planner now distinguishes an extremum from a requested top-N. “Which company manufactures Zestril?” initially asked which company, although Zestril was named; the entity guard now keeps that manufacturer lookup distinct from “Which company has the most products?” The inherited score still marks Q26/Q35 and Q37 as misses because expected values/labels use raw shifted cells or look for a company string when the answer gives a distinct-name count. Q38–Q41 ask “this company” without a preceding turn in their isolated sessions; the assistant correctly asks which company, although the legacy grader assumes ICI.

### Remaining limitations

- The 90/100 number must not be represented as factual accuracy; replace the weak legacy checker with a blind, independently authored set that checks exact product, price, filters, source row IDs, ranking, missing-data behavior, and safe abstention.
- The source is a price-list snapshot. It cannot answer real-time store inventory, batch/expiry, reorder quantities, historical sales, invoices, or legal/clinical questions. Those require better connected source data and, for ledger joins and time-aware measures, further query-planner engineering.
- Pack strength is absent for Zestril in this workbook; use another authoritative catalog source to answer strength-specific questions. No database endpoint or credentials were available for external PostgreSQL/MySQL/SQL Server live verification in this run.
