# Test Report 02: Key Management & Vault Lifecycle

## Module Summary
- **Module ID**: 6.10 Local Data Security Module
- **Component**: Key Management & Derivation (`backend/app/security/crypto.py`)
- **Focus**: Master key generation, disk persistence, permission locking, HKDF-SHA256 derivation, thread safety, and lifecycle isolation.

---

## Technical Specifications Tested

### 1. Master Vault Key Generation
The master key is generated dynamically using `os.urandom(32)`, yielding 256 bits of cryptographic entropy:
- Saved to local storage at `data/.vault_key`.
- On Unix/Linux/macOS platforms, created using `os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` ensuring owner-only read/write access.
- On Windows platforms, file permissions restrict non-system users.

### 2. Deterministic Key Derivation via HKDF-SHA256
When an enterprise secret is provided via environment variable `LLM_KONNECT_SECRET_KEY` or `settings.secret_key`:
- The engine uses **HKDF** (HMAC-based Key Derivation Function) with SHA-256.
- **Salt**: `b"llm_konnect_vault_salt_v1"`.
- **Info Context**: `b"local_encryption_at_rest"`.
- **Output Length**: 32 bytes (256 bits).
- Enables enterprise customers to inject their own external key management secrets without persisting a raw key file.

```
+---------------------------------------+
|  Environment Secret: LLM_KONNECT_...  |
+---------------------------------------+
                   |
                   v
+---------------------------------------+
|   HKDF-Extract (SHA-256 + Salt v1)    |
+---------------------------------------+
                   |
                   v
+---------------------------------------+
|  HKDF-Expand (Info: encryption_rest)  |
+---------------------------------------+
                   |
                   v
+---------------------------------------+
|    256-bit AES Master Vault Key       |
+---------------------------------------+
```

### 3. Thread-Safe In-Memory Key Caching
To avoid expensive disk reads on high-frequency chunk ingestion:
- The master key is cached in module-level `_cached_key`.
- Protected by `threading.Lock()` (`_key_lock`) to prevent race conditions during cold start.
- `reset_cached_key()` provides complete isolation between testing environments.

---

## Test Cases Executed

| # | Test Function | Purpose / Validation Target | Result |
| :---: | :--- | :--- | :---: |
| 1 | `test_vault_key_generation_and_length` | Validates 256-bit length (32 bytes) of newly created vault keys. | **PASS** |
| 2 | `test_vault_key_file_persistence_and_permissions` | Validates keyfile creation and disk persistence. | **PASS** |
| 3 | `test_hkdf_derivation_from_env_secret` | Validates deterministic HKDF-SHA256 derivation from enterprise secrets. | **PASS** |
| 4 | `test_threadsafe_caching_and_reset` | Validates concurrent multithreaded key access and cache eviction. | **PASS** |
| 5 | `test_key_consistency_across_reloads` | Validates persistence and consistent reload across application restarts. | **PASS** |

---

## Security Guarantees Confirmed
1. **Entropy Standards**: Minimum 256 bits of CSPRNG entropy.
2. **Access Control**: Owner-only key permissions protect the keyfile from unprivileged local processes.
3. **Enterprise Integration**: Support for external secret injection via HKDF-SHA256 without architectural changes.
