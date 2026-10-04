# Test Documentation Report: 01 — Header Mapping & Synonym Proposal Engine

## 1. Test Suite Identification
- **Module ID**: Module 6.3 (Schema Mapping and Validation Module)
- **Test File**: [`test_01_header_mapping_and_synonyms.py`](./test_01_header_mapping_and_synonyms.py)
- **Target Implementation File**: [`backend/app/schema/mapper.py`](../../backend/app/schema/mapper.py)
- **Primary Functions Tested**:
  - `suggest_mapping(columns: List[str], domain_pack: Optional[DomainPack]) -> MappingProposal`
  - `_normalize_header_string(h: str) -> str`
  - `_build_synonym_lookup(domain_pack: Optional[DomainPack]) -> Dict[str, str]`
  - `get_canonical_fields(domain_pack: Optional[DomainPack]) -> List[str]`

---

## 2. Tested Business Requirements & Logic
1. **Deterministic Header Normalization**:
   - Strips non-alphanumeric punctuation while maintaining Unicode word characters so Urdu script (`دوا کا نام`) survives uncorrupted.
   - Splits camelCase (`MedicineName` → `medicine name`) and snake_case (`unit_cost_price` → `unit cost price`) to maximize vocabulary overlap with domain synonyms.
2. **Domain-Aware Synonym Lookups**:
   - Evaluates domain-specific vocabulary without slow, non-deterministic LLM calls.
   - Pharmacy synonyms: maps `"MRP"`, `"Sale Rate"` → `unit_price`; `"Item Description"`, `"Medicine"` → `product_id`; `"Batch No"` → `batch_no`; `"Exp Date"` → `expiry_date`.
   - E-Commerce synonyms: maps Shopify export headers (`Lineitem name`, `Lineitem quantity`, `Total Price`, `Lineitem sku`) to canonical e-commerce attributes.
3. **Structured Mapping Proposal Generation**:
   - Emits a Pydantic `MappingProposal` containing individual column `MappingSuggestion` objects with confidence scores (0.0 to 1.0) and human-readable matching reasons.
4. **Missing Required Fields Auditing**:
   - Immediately flags required canonical fields that failed to find an appropriate match in the source data (e.g. flagging missing `date` or `amount`), allowing the frontend confirmation UI to prompt the operator before bad data is ingested.
5. **Opaque Column Isolation**:
   - Arbitrary, unrecognizable column headers with low similarity remain unmapped (`canonical_field=None`, `confidence < 0.5`) for manual human review rather than being erroneously assigned.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_normalize_header_strings` | CamelCase, snake_case, Urdu, punctuation | Uniform lowercased spaced words with Unicode intact | **PASS** |
| `test_exact_canonical_core_field_matching` | Direct core canonical headers | Maps with 1.0 confidence and 0 missing required | **PASS** |
| `test_pharmacy_domain_synonym_matching` | Pharmacy POS headers (MRP, Exp Date, Batch) | Correctly maps all 8 headers to pharmacy canonical fields | **PASS** |
| `test_ecommerce_domain_synonym_matching` | Shopify export headers (Lineitem, Order ID) | Maps headers to e-commerce canonical fields | **PASS** |
| `test_missing_required_fields_detection` | Incomplete header set missing Date & Amount | Surfaces missing required fields in `required_fields_missing` | **PASS** |
| `test_unmapped_opaque_columns_remain_for_review` | Opaque unrecognized column names | Retains column as unmapped (`canonical_field=None`) | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.3 Schema Mapping and Validation Module/test_01_header_mapping_and_synonyms.py" -v`
- **Assurance**: Verified with standard and localized pharmacy and e-commerce column variations.
