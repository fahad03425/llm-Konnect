"""Test Suite 1: Core AES-256-GCM Cryptographic Engine (Module 6.10).

Verifies the foundational cryptographic primitives:
1. AES-256-GCM authenticated encryption and decryption roundtrip for bytes.
2. Binary envelope format: 8-byte magic header (LLMENC01), 12-byte random nonce, and 16-byte tag.
3. Cryptographic tamper detection (InvalidTag on modified ciphertext, tag, or nonce).
4. Authenticated Additional Data (AAD) binding and integrity verification.
5. Portable string envelope formatting ('enc:<urlsafe_base64>').
6. Passthrough tolerance for unencrypted legacy data and strict-mode rejection.
"""

import pytest
from cryptography.exceptions import InvalidTag

from app.security.crypto import (
    encrypt_bytes,
    decrypt_bytes,
    encrypt_string,
    decrypt_string,
    is_encrypted_bytes,
    MAGIC_HEADER,
    NONCE_LENGTH,
    KEY_LENGTH,
)


class TestAesGcmCryptographicEngine:
    """Rigorous tests for the AES-256-GCM cryptographic primitives."""

    def test_encrypt_decrypt_bytes_roundtrip(self, sample_financial_csv_bytes):
        """Verifies lossless roundtrip encryption and decryption of raw binary data."""
        encrypted = encrypt_bytes(sample_financial_csv_bytes)
        assert encrypted != sample_financial_csv_bytes
        assert len(encrypted) > len(sample_financial_csv_bytes)

        decrypted = decrypt_bytes(encrypted)
        assert decrypted == sample_financial_csv_bytes
        assert b"Augmentin 625mg" in decrypted
        assert b"TRN-982134" in decrypted

    def test_magic_header_and_nonce_structure(self):
        """Verifies binary envelope adheres strictly to [MAGIC_HEADER(8)] + [NONCE(12)] + [CIPHERTEXT + TAG(16)]."""
        payload = b"Confidential financial statement Q1-2026"
        encrypted = encrypt_bytes(payload)

        # 1. Header verification
        assert encrypted.startswith(MAGIC_HEADER)
        assert len(MAGIC_HEADER) == 8
        assert encrypted[:8] == b"LLMENC01"

        # 2. Nonce verification
        nonce = encrypted[8:8 + NONCE_LENGTH]
        assert len(nonce) == 12
        assert is_encrypted_bytes(encrypted) is True

        # 3. Minimum length check: Header(8) + Nonce(12) + Tag(16) = 36 bytes min overhead
        expected_min_len = 8 + 12 + len(payload) + 16
        assert len(encrypted) == expected_min_len

        # 4. Randomized nonces across successive calls
        encrypted_2 = encrypt_bytes(payload)
        nonce_2 = encrypted_2[8:8 + NONCE_LENGTH]
        assert nonce != nonce_2
        assert encrypted != encrypted_2

    def test_tamper_detection_invalid_tag(self):
        """Verifies that flipping any bit in the ciphertext or tag raises InvalidTag."""
        plaintext = b"Sensitive payroll figure: PKR 4,500,000"
        encrypted = bytearray(encrypt_bytes(plaintext))

        # Tamper with the last byte (part of the 16-byte authentication tag)
        encrypted[-1] ^= 0xFF

        with pytest.raises(InvalidTag):
            decrypt_bytes(bytes(encrypted), allow_passthrough=False)

        # Tamper with ciphertext body (byte index 25)
        encrypted_2 = bytearray(encrypt_bytes(plaintext))
        encrypted_2[25] ^= 0x01

        with pytest.raises(InvalidTag):
            decrypt_bytes(bytes(encrypted_2), allow_passthrough=False)

    def test_authenticated_additional_data_aad(self):
        """Verifies that Authenticated Additional Data (AAD) binds ciphertext to its context."""
        plaintext = b"Customer Account Balance: PKR 1,250,000"
        tenant_aad = b"tenant_pharmacy_lahore_main"

        # Encrypt with tenant context
        encrypted = encrypt_bytes(plaintext, aad=tenant_aad)

        # Decrypt with matching AAD succeeds
        decrypted = decrypt_bytes(encrypted, aad=tenant_aad)
        assert decrypted == plaintext

        # Decrypt with mismatched AAD fails immediately
        imposter_aad = b"tenant_retail_karachi_branch"
        with pytest.raises(InvalidTag):
            decrypt_bytes(encrypted, aad=imposter_aad, allow_passthrough=False)

    def test_encrypt_decrypt_string_envelope(self):
        """Verifies string encryption creates 'enc:' prefixed urlsafe base64 envelopes."""
        secret_text = "Tally ODBC Connection: Driver={Tally};Server=127.0.0.1;Port=9000;Pass=Secr3t!"
        envelope = encrypt_string(secret_text)

        assert envelope.startswith("enc:")
        assert "Secr3t" not in envelope
        assert "Tally" not in envelope

        decrypted = decrypt_string(envelope)
        assert decrypted == secret_text

        # Empty/None handling
        assert encrypt_string("") == ""
        assert decrypt_string("") == ""
        assert decrypt_string(None) is None

    def test_passthrough_behavior_unencrypted_data(self):
        """Verifies that non-encrypted legacy bytes and strings pass through safely or raise in strict mode."""
        legacy_bytes = b"Unencrypted legacy CSV header,id,amount"
        legacy_string = "Plaintext comment note"

        # Safe passthrough mode (default)
        assert decrypt_bytes(legacy_bytes, allow_passthrough=True) == legacy_bytes
        assert decrypt_string(legacy_string, allow_passthrough=True) == legacy_string

        # Strict mode raises error on unencrypted data
        with pytest.raises(ValueError, match="does not contain a valid LLM-Konnect encryption header"):
            decrypt_bytes(legacy_bytes, allow_passthrough=False)

        with pytest.raises(ValueError, match="does not have 'enc:' prefix"):
            decrypt_string(legacy_string, allow_passthrough=False)
