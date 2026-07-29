# Schema Mapping (Module 6.3)

The Schema Mapping module provides a domain-agnostic, deterministic engine to map arbitrary columns from raw data exports to a well-defined canonical schema.

## Architecture and Boundaries

Code computes. The LLM narrates. A verifier checks. 
Mapping is strictly rule-based, touching none of the LLM. 

### 1. Connectors (Module 6.2)
Connectors (like `CSVConnector`, `ExcelConnector`) read files and return raw `pandas.DataFrame`s. They detect formatting (delimiters, encoding, headers, sheet names), but they **do not coerce data types or perform any schema matching**. 

### 2. Schema Mapping (Module 6.3 - This Module)
The mapping engine (`backend/app/schema/mapper.py` and `backend/app/schema/normalize.py`) takes raw data, maps columns to canonical fields, and converts data types.
*   **`suggest_mapping`**: Proposes a mapping based on exact matches, known synonyms, fuzzy matching, and value-based heuristics (sniffing rows for dates, money, integer fields). Returns a JSON-serializable `MappingProposal`.
*   **`apply_mapping`**: Takes a mapping and a raw DataFrame. It renames columns to their canonical field names. By default, it uses `keep_extras=True` to retain unmapped columns using an `_extra.<name>` prefix. It centralizes all data coercion (e.g. money parsing, date parsing, Urdu digits).

### 3. Domain Packs
To support various industries (e.g. Pharmacy, Grocery), mapping uses domain packs (`backend/app/schema/domain.py`).
A `DomainPack` provides:
- Extra schema fields (e.g. `expiry_date`, `mrp`).
- Synonyms (e.g. `میعاد`, `exp. date` -> `expiry_date`).
- Domain-specific validation rules.

## Adding a New Domain Pack (e.g., Grocery)
To add a new niche, you do **not** modify `mapper.py`. Instead:
1. Create a new file (e.g. `backend/app/schema/grocery.py`).
2. Subclass `DomainPack`.
3. Provide the `name`, `extra_fields`, `header_synonyms`, and `validate_row`.
4. Register it via `registry.register(GroceryDomainPack())`.

## Profile Persistence
Mapping decisions are stored in local JSON files by `backend/app/schema/profile.py`. It uses a signature hash of the column set to remember mappings.
