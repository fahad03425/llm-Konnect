# Data connector fixes and dummy connection tests

## What was corrected

1. Network sources now work through preview, mapping, normalization, validation,
   cleaning and knowledge-base ingestion. They are no longer rejected merely
   because their URLs are not local files. Registry identities retain their URLs.
2. Shopify respects the selected resource: orders, products or reviews.
3. Product cost comes from inventory unit cost. A comparison selling price is
   retained separately and is never treated as purchase cost. Unavailable costs
   remain unknown and produce a warning.
4. Refunds attach to their actual order lines. Shipping is counted once per order.
   Refunds without identifiable lines are recorded separately rather than being
   divided arbitrarily between products.
5. Review retrieval uses GraphQL metaobjects and reports permission errors.
   Missing ratings remain unknown. This requires a review metaobject definition
   and `read_metaobjects`; it does not automatically read every third-party review
   app. The connector's `review_type` option selects the definition; CSV is the
   fallback for reviews stored elsewhere.
6. Shopify requests have timeouts, bounded rate-limit retries and pagination
   checks. Pagination cannot forward the token to another host. The default API
   version is 2026-07; a different version served by Shopify produces a warning.
   Order imports warn about the usual 60-day history restriction.
7. New UI connections store Shopify tokens encrypted in the local storage
   directory, using the existing vault key. Source URLs contain a connection ID.
   Existing token-bearing URLs still work for compatibility; they are not
   automatically migrated. Keep the vault key when moving the installation.
8. Tally XML export errors are explicit. Tally ODBC is available through
   `tally+odbc://YourDSN`, with an optional URL-encoded `query` parameter. It requires
   the Tally ODBC driver and a configured DSN. Only a single SELECT query is
   accepted, and connections are opened read-only and closed after extraction.
   The default query reads ledger names and closing balances; use a suitable
   collection query and field mapping for the financial dataset you need.
9. Malformed CSV rows raise errors instead of being silently skipped. Invalid
   Excel sheet names report available sheets. Excel `.xls` support declares
   `xlrd` as a dependency.

## Run the repeatable dummy tests

From the project root in PowerShell:

```powershell
& ./scripts/test-backend.ps1 -TestArgs @(
    'tests/test_connector_http_integration.py',
    'tests/test_connector_regressions.py',
    'tests/test_connectors.py',
    'tests/test_ecommerce_shopify_connector.py',
    '-q', '--tb=short'
)
```

The permanent runner selects a working Python runtime and supports provisioning
an isolated test environment with `-Python`. It does not replace the application's
virtual environment.

The HTTP tests start temporary servers on random loopback ports, issue real
requests, and close the servers afterward. No Shopify account, token, running
Tally company or internet access is required. Dummy Shopify tests exercise
authentication failure, rate limits, product pagination and cost retrieval,
line refunds, shipping, review pagination and missing review permissions.
Dummy Tally tests exercise HTTP XML extraction and company/export errors.

The complete import-wizard tests call the actual application routes and use an
isolated registry and storage directory. Only the final knowledge-base sink is
mocked, so they do not certify embedding generation. ODBC tests use a dummy
driver to verify the query, read-only connection and cleanup; they do not connect
to a real Tally driver.

## Verification result

On this change, 37 backend tests passed across connectors, the HTTP import flow,
registry/scoping and file APIs. Frontend TypeScript checking and the production
build passed. Dependency deprecation warnings and the frontend bundle-size
warning remain informational.

Live Shopify permissions, real review-app storage and an installed Tally company
and ODBC driver still need validation in the deployment environment. Dummy tests
verify application behavior but do not establish universal live compatibility.
