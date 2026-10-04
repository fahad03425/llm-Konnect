# Test Report 04: Vector DB & Data Store Payload Encryption

## Module Summary
- **Module ID**: 6.10 Local Data Security Module
- **Component**: Vector DB & Secondary Persistence Protection (`backend/app/ingestion/store.py`, `backend/app/rag/history.py`, `backend/app/connectors/credentials.py`)
- **Focus**: Multi-tier data protection covering Chroma vector store chunks, row metadata, conversational history, and third-party connector credentials.

---

## Technical Specifications Tested

### 1. Vector Database Chunk Encryption
Chroma vector database collections persist embeddings along with raw chunk text and metadata. To guarantee privacy:
- The chunk text payload is encrypted using `encrypt_string()` before being passed to Chroma.
- The high-dimensional embedding vectors are derived in-memory and stored numerically.
- If the Chroma SQLite database (`chroma.sqlite3`) is directly inspected, chunk documents appear as `enc:<base64>` ciphertext.

### 2. Canonical Row Metadata Protection
During semantic indexing, the original canonical record (`record_json`) is attached to chunk metadata:
- Encrypted using `encrypt_string()` to prevent leakage of unretrieved columns (such as patient identifiers, customer credit card tokens, or internal notes).
- Only decrypted on-demand when compiling citations or provenance tooltips.

### 3. Conversational Session History Encryption
User chats with the RAG assistant contain proprietary financial inquiries and strategic deliberations:
- `ChatHistoryManager` serializes session structures into JSON and encrypts them via `encrypt_bytes()` before disk writes.
- File headers on `data/sessions/<session_id>.json` contain `LLMENC01`. Plaintext strings never touch storage.

### 4. Credential Vault with AAD Context Binding
Third-party access tokens (e.g., Shopify Admin API private tokens) are protected in a dedicated vault:
- **Cipher**: AES-256-GCM.
- **Nonce**: 12-byte random nonce stored alongside ciphertext.
- **AAD**: Bound to the store's domain name (e.g., `store.myshopify.com`).
- **Isolation**: Stored under `data/storage/source_credentials/<uuid>.json` with `0o600` access permissions.
- Prevents cross-store token replay attacks.

```
[ Shopify API Token ] + [ Shop Domain AAD ]
              |
              v (AES-256-GCM Encryption)
+------------------------------------------------------------+
| JSON Vault Payload:                                        |
| {                                                          |
|   "shop": "my-pharmacy.myshopify.com",                     |
|   "token": "base64(Nonce[12] + Ciphertext + Tag[16])"      |
| }                                                          |
+------------------------------------------------------------+
```

---

## Test Cases Executed

| # | Test Function | Purpose / Validation Target | Result |
| :---: | :--- | :--- | :---: |
| 1 | `test_chroma_chunk_text_payload_encryption` | Validates vector chunk text encryption and roundtrip retrieval. | **PASS** |
| 2 | `test_metadata_record_json_encryption` | Validates raw JSON metadata encryption in vector store attributes. | **PASS** |
| 3 | `test_rag_session_history_encryption_at_rest` | Validates encrypted conversation turns in `data/sessions/`. | **PASS** |
| 4 | `test_shopify_credentials_vault_encryption` | Validates credential encryption with domain-bound AAD. | **PASS** |
| 5 | `test_connection_string_registry_encryption` | Validates database passwords encrypted in the ingestion registry. | **PASS** |
| 6 | `test_sqlite_chunk_storage_payload_protection` | Validates zero plaintext exposure in disk SQLite chunk database files. | **PASS** |

---

## Security Guarantees Confirmed
1. **Zero Exposure in Vector Indexes**: Database administrators cannot inspect SQLite tables to harvest client data.
2. **Conversation Confidentiality**: Executive Q&A logs cannot be harvested from disk without the master vault key.
3. **Credential Integrity**: Third-party API credentials cannot be extracted or hijacked across stores.
