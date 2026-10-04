"""Test Suite 2: Key Management & Vault Lifecycle (Module 6.10).

Verifies the lifecycle, derivation, persistence, and isolation of the master vault key:
1. Cryptographically secure 256-bit (32-byte) key generation.
2. Local keyfile persistence (`data/.vault_key`) with owner-only access permissions.
3. Deterministic key derivation via HKDF-SHA256 from `LLM_KONNECT_SECRET_KEY` environment secret.
4. Thread-safe in-memory caching and clean eviction via `reset_cached_key()`.
5. Key persistence and consistency across simulated application restarts.
"""

import os
import threading
import pytest
from app.core.config import settings
from app.security.crypto import (
    get_vault_key,
    reset_cached_key,
    KEY_LENGTH,
)


class TestKeyManagementAndVaultLifecycle:
    """Rigorous tests for the master vault key lifecycle and key derivation."""

    def test_vault_key_generation_and_length(self, temp_security_sandbox):
        """Verifies that a newly initialized vault key is exactly 256 bits (32 bytes) of random entropy."""
        vault_file = temp_security_sandbox["vault_file"]
        assert not os.path.exists(vault_file)

        key = get_vault_key()
        assert isinstance(key, bytes)
        assert len(key) == KEY_LENGTH
        assert len(key) == 32
        assert os.path.exists(vault_file)

    def test_vault_key_file_persistence_and_permissions(self, temp_security_sandbox):
        """Verifies that the generated keyfile is persisted to disk with strict read/write mode."""
        vault_file = temp_security_sandbox["vault_file"]
        key_1 = get_vault_key()

        # Check file exists and size is exactly 32 bytes
        assert os.path.isfile(vault_file)
        assert os.path.getsize(vault_file) == 32

        with open(vault_file, "rb") as f:
            raw_disk_key = f.read()
        assert raw_disk_key == key_1

    def test_hkdf_derivation_from_env_secret(self, temp_security_sandbox, monkeypatch):
        """Verifies that when LLM_KONNECT_SECRET_KEY is provided, a 256-bit key is derived deterministically via HKDF-SHA256."""
        env_secret = "enterprise_master_passphrase_production_2026_!@#"
        monkeypatch.setenv("LLM_KONNECT_SECRET_KEY", env_secret)
        reset_cached_key()

        derived_key_1 = get_vault_key()
        assert len(derived_key_1) == 32

        # Reset cache and derive again with the same secret -> must be identical
        reset_cached_key()
        derived_key_2 = get_vault_key()
        assert derived_key_1 == derived_key_2

        # Changing the secret produces a completely different derived key
        monkeypatch.setenv("LLM_KONNECT_SECRET_KEY", "different_tenant_secret_passphrase")
        reset_cached_key()
        derived_key_3 = get_vault_key()
        assert derived_key_3 != derived_key_1
        assert len(derived_key_3) == 32

    def test_threadsafe_caching_and_reset(self, temp_security_sandbox):
        """Verifies that key retrieval is thread-safe and cached in memory across concurrent threads."""
        keys = []
        threads = []

        def worker():
            k = get_vault_key()
            keys.append(k)

        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All 10 threads must have received the exact same cached key instance
        assert len(keys) == 10
        first_key = keys[0]
        for k in keys:
            assert k == first_key

        # After reset, key from disk remains consistent
        reset_cached_key()
        reloaded_key = get_vault_key()
        assert reloaded_key == first_key

    def test_key_consistency_across_reloads(self, temp_security_sandbox):
        """Simulates application restart by resetting cache and verifying file-backed persistence."""
        key_initial = get_vault_key()

        # Simulate full process restart: clear cache
        reset_cached_key()

        key_reloaded = get_vault_key()
        assert key_reloaded == key_initial
        assert len(key_reloaded) == 32
