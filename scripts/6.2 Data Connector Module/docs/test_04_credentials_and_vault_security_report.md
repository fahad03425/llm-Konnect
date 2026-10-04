# Test Documentation Report: 04 — Credential Vault & At-Rest Token Security

## 1. Test Suite Identification
- **Module ID**: Module 6.2 (Data Connector Module)
- **Test File**: [`test_04_credentials_and_vault_security.py`](./test_04_credentials_and_vault_security.py)
- **Target Implementation File**: [`backend/app/connectors/credentials.py`](../../backend/app/connectors/credentials.py)
- **Primary Functions Tested**:
  - `save_shopify_token(shop: str, token: str) -> str`
  - `read_shopify_token(identity: str, shop: str) -> str`

---

## 2. Tested Business Requirements & Logic
1. **At-Rest Token Encryption**:
   - Store access tokens (`shpat_...`) are high-privilege credentials that grant access to customers, sales, and products.
   - Credentials must never be logged or stored in plain text on disk or in source URL signatures.
   - `save_shopify_token` generates a 32-character hexadecimal UUID, encrypts the token using AES-256-GCM via the system's root vault key, and saves the nonce + ciphertext bundle.
2. **Authenticated Associated Data (AAD) Binding**:
   - The store's canonical subdomain is passed as Authenticated Associated Data (AAD) during AES-GCM encryption.
   - If an attacker or compromised module attempts to use a stolen connection ID with a different store name, authentication tag verification fails immediately, blocking cross-store token misuse.
3. **Path Traversal & Injection Defense**:
   - Strict regex validation (`^[0-9a-f]{32}$`) on the connection identifier ensures directory traversal sequences (e.g. `../../`) are rejected before disk access.
4. **Resilient Failure Handling**:
   - If a credential file is absent or inaccessible, the function returns a clean, user-friendly exception advising the merchant to reconnect their store.

---

## 3. Test Cases Summary

| Test Case Name | Objective / Scenario | Expected Outcome | Status |
| :--- | :--- | :--- | :---: |
| `test_save_and_read_token_roundtrip` | Save & load token lifecycle | Encrypted on disk; decrypted accurately | **PASS** |
| `test_cross_store_token_hijack_prevention` | Attempting to access token with mismatched store | Cryptographic rejection (`ValueError`) | **PASS** |
| `test_invalid_connection_identifier_format` | Path traversal attempt in connection ID | Immediate regex rejection | **PASS** |
| `test_missing_credential_file_handling` | Deleted or missing credential file | Helpful exception advising store reconnection | **PASS** |

---

## 4. Verification & Audit Trail
- **Execution Command**: `python -m pytest "scripts/6.2 Data Connector Module/test_04_credentials_and_vault_security.py" -v`
- **Assurance**: Validates AES-GCM encryption standards with temp directories isolated per test execution.
