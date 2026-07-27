# Data Connectors

This document explains how to add new data sources to LLM-Konnect. The connector architecture is designed to be highly extensible without modifying core ingestion code.

## 1. Adding a New Domain Pack
A **Domain Pack** isolates all industry-specific vocabulary, schema extensions, and validation logic. We started with `pharmacy`, but expanding to `grocery` or `electronics` is straightforward.

**Steps:**
1. Create a new file in `backend/app/schema/` (e.g., `grocery.py`).
2. Create a class inheriting from `DomainPack` (imported from `app.schema.domain`).
3. Implement the required properties:
   - `name`: Returns the unique string identifier (e.g., `"grocery"`).
   - `extra_fields`: Returns a list of canonical fields unique to this domain (e.g., `["weight", "brand", "is_perishable"]`).
   - `header_synonyms`: Returns a dictionary mapping canonical fields (core + extra) to raw header names found in typical exports.
4. Implement `validate_row(row: dict, index: int) -> List[Problem]`:
   - Add domain-specific validation (e.g., warning if perishable items don't have an expiration).
5. At the bottom of the file, register your pack: `registry.register(GroceryDomainPack())`.
6. Import your new module in `backend/app/schema/__init__.py` to ensure it registers on startup.

## 2. Adding a New Local Pharmacy Software Profile
The `LocalDBConnector` (in `connectors/tally.py`) currently reads raw tables or executes raw SQL queries against SQLite or MS Access databases.

To support a new local POS system out-of-the-box (e.g., "InstaCare"):
1. Create a configuration file or mapping dictionary that maps the vendor's database schema to our canonical fields. 
2. Instead of forcing the user to write SQL, you can add a method to `LocalDBConnector` or a new factory function `create_instacare_connector(db_path)` that abstracts the specific `JOIN` queries needed to flatten their relational tables (e.g., `sales` joined with `sale_items` and `products`) into our expected flat transaction rows.
3. No core connector logic needs to change.

## 3. Creating a New Connector Type
If you need to connect to something other than CSV, Excel, JSON, SQLite, Access, or Shopify (e.g., a direct PostgreSQL connection or a different third-party API):
1. Create a new file in `backend/app/connectors/`.
2. Inherit from `app.connectors.base.Connector`.
3. Implement `fetch()`, `preview()`, `describe()`, and `capabilities()`.
4. Update `detect_connector(path)` in `base.py` if your connector should be auto-detected from a file extension.
