# Module 6.10: Local Data Security Module — File Catalog

## Executive Overview
The **Local Data Security Module** (Module 6.10) enforces zero-knowledge local confidentiality across the LLM-Konnect platform. It encrypts all local vector database payloads, document chunks, session conversations, external connector credentials, and ingested financial files at rest. This reinforces the system's core guarantee: **sensitive business and financial data never leaves the local machine, and is never left exposed in plaintext on the physical storage drive.**

---

## 1. Core Cryptographic Engine & Primitives

### `backend/app/security/crypto.py`
- **Role**: Primary cryptographic engine providing authenticated symmetric encryption at rest.
- **Algorithm**: **AES-256-GCM** (Galois/Counter Mode) via `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
- **Specification**:
  - **Key Size**: 256 bits (32 bytes) generated via `os.urandom` or derived using **HKDF-SHA256**.
  - **Nonce**: 96-bit (12-byte) cryptographically secure random initialization vector per encryption operation.
  - **Magic Header**: 8-byte signature `LLMENC01` identifying encrypted data envelopes.
  - **Authentication Tag**: 128-bit (16-byte) GHASH tag guaranteeing confidentiality and tamper detection.
- **Key Functions**:
  - `get_vault_key()`: Retrieves or generates the master vault encryption key with thread-safe caching and permission locking (`0o600`).
  - `encrypt_bytes(plaintext, aad)`: Formats output as `[MAGIC_HEADER] + [NONCE] + [CIPHERTEXT] + [TAG]`.
  - `decrypt_bytes(data, aad, allow_passthrough)`: Authenticates and decrypts ciphertext; raises `InvalidTag` if tampered.
  - `encrypt_string(text)` / `decrypt_string(enc_text)`: Safe base64-encoded string envelopes prefixed with `enc:` for JSON/DB fields.
  - `encrypt_file(file_path, dest_path)`: Atomic in-place or copied disk file encryption using temporary swap files.
  - `decrypt_file_to_bytes(file_path)`: Reads encrypted disk files into RAM; **never writes plaintext back to disk**.
  - `decrypt_file_to_stream(file_path)`: In-memory `io.BytesIO` stream for direct pandas/openpyxl parsing.
  - `is_encrypted_file(file_path)` / `is_encrypted_bytes(data)`: Fast header sniffing without expensive decryption.

### `backend/app/security/__init__.py`
- **Role**: Clean export interface exposing encryption/decryption primitives to the rest of the backend architecture.

---

## 2. API Endpoints & Administrative Interfaces

### `backend/app/api/security.py`
- **Role**: FastAPI administrative and telemetry router (`/api/security`).
- **Endpoints**:
  - `GET /api/security/status`: Returns current security posture, encryption status (`AES-256-GCM`), vault key presence, counts of encrypted vs plaintext files in storage, and verification of local isolation (`never_leaves_machine: True`).
  - `POST /api/security/encrypt-all`: Scans all files in `data/uploads/` and encrypts any remaining legacy plaintext files in-place with atomic verification.

### `backend/app/api/files.py`
- **Role**: Secure ingestion entrypoint.
- **Security Logic**:
  - Automatically encrypts all incoming uploaded files immediately upon ingestion using `encrypt_bytes()`.
  - Files are written to disk directly in encrypted form, ensuring no unencrypted window on disk.

---

## 3. Storage, Vector Database & Session Protection

### `backend/app/ingestion/store.py`
- **Role**: Local Chroma vector database and SQLite document chunk store.
- **Security Logic**:
  - Vector document chunks are enveloped in `encrypt_string()` before storage in Chroma collections.
  - Original raw record metadata (`record_json`) is encrypted at rest using `encrypt_string()`.
  - Vector embeddings are generated in-memory using local Sentence-Transformers and indexed directly.

### `backend/app/ingestion/sync_worker.py`
- **Role**: Real-time database sync worker.
- **Security Logic**:
  - Re-indexed document records and raw JSON snapshots are wrapped in `encrypt_string()` before being written to persistent storage.

### `backend/app/ingestion/registry.py`
- **Role**: Master file registry and metadata catalog.
- **Security Logic**:
  - External database connection strings (ODBC, SQLite, PostgreSQL) are encrypted at rest with `encrypt_string()`.

### `backend/app/rag/history.py`
- **Role**: Conversational session history storage (`data/sessions/`).
- **Security Logic**:
  - Chat history JSON files are encrypted at rest using `encrypt_bytes()`.
  - Sessions are read via `decrypt_bytes()` directly into application memory; disk files remain ciphertext.

---

## 4. Connector Security & Credential Vault

### `backend/app/connectors/credentials.py`
- **Role**: Dedicated credential storage vault for third-party integrations (Shopify Admin API).
- **Security Logic**:
  - Stores access tokens using AES-256-GCM with the shop domain as Authenticated Additional Data (AAD).
  - Saved in `data/storage/source_credentials/` with strict file permissions (`0o600`).

### `backend/app/connectors/csv_excel.py` & `backend/app/connectors/tally.py`
- **Role**: Financial file parsers (CSV, TSV, XLSX, XLS, Tally XML).
- **Security Logic**:
  - Uses `decrypt_file_to_bytes()` and `_sniff_csv_format_from_bytes()` to parse data entirely in RAM.
  - Plaintext data is never written back to disk or swapped out to temporary cache files.

---

## 5. Configuration & Air-Gapped Isolation

### `backend/app/core/config.py`
- **Role**: Global system settings.
- **Security Controls**:
  - `encryption_enabled`: Master switch controlling encryption at rest (default `True`).
  - `vault_key_path`: Location of the local master key file (`data/.vault_key`).
  - Air-gapped isolation flags: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1` preventing any outbound internet network calls.
