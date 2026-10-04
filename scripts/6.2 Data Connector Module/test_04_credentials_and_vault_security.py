"""Test Suite 04: Credential Vault & At-Rest Token Security.

Module: Module 6.2 — Data Connector Module
Target File: backend/app/connectors/credentials.py (functions: save_shopify_token, read_shopify_token)
Scope:
- Verifies AES-GCM encryption of store credentials at rest.
- Verifies authenticated associated data (AAD) binding to the specific store subdomain.
- Verifies rejection of mismatched store requests (cross-store token isolation).
- Verifies identifier format validation and graceful error reporting for missing credentials.
"""

import json
from pathlib import Path
import pytest
from app.connectors.credentials import save_shopify_token, read_shopify_token


class TestCredentialsVaultSecurity:
    """Verifies cryptographic protection of API keys and store access tokens."""

    def test_save_and_read_token_roundtrip(self, temp_vault_dir):
        """Tokens are encrypted on disk and decrypted accurately for the matching store."""
        shop = "lahore-pharma-direct"
        raw_token = "shpat_9876543210abcdef"

        conn_id = save_shopify_token(shop, raw_token)
        assert isinstance(conn_id, str)
        assert len(conn_id) == 32  # 32-character hex UUID

        # Verify disk file is encrypted and does not contain cleartext token
        cred_file = temp_vault_dir / "source_credentials" / f"{conn_id}.json"
        assert cred_file.exists()
        payload = json.loads(cred_file.read_text(encoding="utf-8"))
        assert payload["shop"] == shop
        assert raw_token not in json.dumps(payload)  # Never stored in cleartext

        # Decrypt
        decrypted = read_shopify_token(conn_id, shop)
        assert decrypted == raw_token

    def test_cross_store_token_hijack_prevention(self, temp_vault_dir):
        """A token cannot be accessed if requested by a different store name (AAD mismatch)."""
        legit_shop = "karachi-care"
        raw_token = "shpat_secret_token_111"

        conn_id = save_shopify_token(legit_shop, raw_token)

        # Attempt to read token pretending to be another store
        with pytest.raises(ValueError) as exc:
            read_shopify_token(conn_id, "malicious-competing-store")
        assert "does not match this store" in str(exc.value)

    def test_invalid_connection_identifier_format(self):
        """Rejects malformed or suspicious connection IDs (path traversal prevention)."""
        with pytest.raises(ValueError) as exc:
            read_shopify_token("../../../etc/passwd", "my-store")
        assert "Invalid Shopify connection identifier" in str(exc.value)

    def test_missing_credential_file_handling(self, temp_vault_dir):
        """Surfaces clear reconnection guidance when a credential file is deleted or missing."""
        missing_id = "0123456789abcdef0123456789abcdef"
        with pytest.raises(ValueError) as exc:
            read_shopify_token(missing_id, "my-store")
        assert "Reconnect the store" in str(exc.value)
