# Test Report 03: Data Source Connection Wizard & Ingestion Flow

## Executive Summary
This report documents the verification of the 4-step data connector wizard (`ConnectSource.tsx`) and the data management view (`UploadedFiles.tsx`) in Module 6.9.

The desktop client abstracts all underlying Python connector scripts, schema mapper rules, and vector indexing operations into an intuitive multi-step wizard tailored for business users.

---

## Test Cases & Specifications

| Test Case | Wizard Component | Tested Workflow Feature | Status |
| :--- | :--- | :--- | :---: |
| `test_connector_types_supported_in_wizard` | `ConnectSource.tsx` | Verifies integration of CSV/Excel upload, Tally Prime ODBC/HTTP connection, and Shopify Admin API token connection. | **PASS** |
| `test_upload_zone_file_formats` | `UploadZone.tsx` | Verifies drag-and-drop file acceptance for `.csv`, `.xlsx`, and `.xls` files. | **PASS** |
| `test_preview_table_sample_rendering` | `PreviewTable.tsx` | Verifies safe rendering of first 5 sample records for raw data inspection. | **PASS** |
| `test_mapping_table_schema_confirmation` | `MappingTable.tsx` | Verifies the single user confirmation step to review or override suggested column mappings. | **PASS** |
| `test_kb_status_ingestion_progress` | `KBStatus.tsx` | Displays offline chunking progress, Sentence-Transformers vector generation, and Chroma indexing status. | **PASS** |
| `test_uploaded_files_catalog_management` | `UploadedFiles.tsx` | Verifies inventory of registered data files, database synchronization state, and un-ingestion controls. | **PASS** |

---

## 4-Step Connection Flow Architecture
1. **Step 1: Source Selection & Ingestion**:
   - Universal File Upload (CSV, XLSX).
   - Tally Prime Local Connector (HTTP/XML or ODBC extraction from running Tally instance).
   - Shopify Admin API Connector (store URL + access token).
2. **Step 2: Preview & Validation**:
   - Displays raw tabular sample records without mutating source files.
3. **Step 3: Schema Mapping Confirmation**:
   - Matches vendor headers to canonical business fields (`amount`, `cost`, `date`, `product_id`).
4. **Step 4: Offline Vector Indexing**:
   - Chunks financial documents, generates dense embeddings locally via Sentence-Transformers, and persists vectors to Chroma.
