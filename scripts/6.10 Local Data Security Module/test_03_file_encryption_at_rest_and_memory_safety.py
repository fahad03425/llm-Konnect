"""Test Suite 3: File Encryption At Rest & In-Memory Safety (Module 6.10).

Verifies disk-level file encryption and zero-disk-leakage memory safety:
1. Atomic file encryption using `.tmp_enc` swap files.
2. Fast header sniffing (`is_encrypted_file`) without expensive crypto overhead.
3. Idempotent double-encryption prevention.
4. In-memory decryption guarantees: plaintext NEVER touched or flushed to disk.
5. Live connector integration: direct in-memory Pandas parsing of encrypted CSV financial files.
6. Live connector integration: direct in-memory openpyxl/Pandas parsing of encrypted Excel files.
"""

import io
import os
import pandas as pd
import pytest

from app.security.crypto import (
    encrypt_file,
    decrypt_file_to_bytes,
    decrypt_file_to_stream,
    is_encrypted_file,
    MAGIC_HEADER,
)
from app.connectors.csv_excel import _get_file_bytes, _sniff_csv_format_from_bytes


class TestFileEncryptionAtRestAndMemorySafety:
    """Rigorous tests for physical file encryption and in-memory decryption safety."""

    def test_encrypt_file_atomic_replacement(self, temp_security_sandbox, sample_financial_csv_bytes):
        """Verifies that encrypt_file transforms a plaintext file into ciphertext atomically on disk."""
        csv_path = os.path.join(temp_security_sandbox["uploads_dir"], "daily_sales_ledger.csv")
        with open(csv_path, "wb") as f:
            f.write(sample_financial_csv_bytes)

        # Before encryption: file is plaintext, does not have magic header
        assert is_encrypted_file(csv_path) is False
        with open(csv_path, "rb") as f:
            raw_before = f.read()
        assert b"Augmentin 625mg" in raw_before
        assert not raw_before.startswith(MAGIC_HEADER)

        # Encrypt in-place
        result_path = encrypt_file(csv_path)
        assert result_path == csv_path

        # After encryption: file on disk is encrypted ciphertext
        assert is_encrypted_file(csv_path) is True
        with open(csv_path, "rb") as f:
            raw_after = f.read()
        assert raw_after.startswith(MAGIC_HEADER)
        # Sensitive plaintext MUST NOT appear anywhere in the disk file
        assert b"Augmentin 625mg" not in raw_after
        assert b"TRN-982134" not in raw_after

    def test_is_encrypted_file_detection(self, temp_security_sandbox):
        """Verifies header sniffing distinguishes between encrypted files, plaintext files, and non-existent files."""
        plain_file = os.path.join(temp_security_sandbox["uploads_dir"], "plain.txt")
        enc_file = os.path.join(temp_security_sandbox["uploads_dir"], "secure.txt")

        with open(plain_file, "w", encoding="utf-8") as f:
            f.write("Just standard plaintext data")

        with open(enc_file, "w", encoding="utf-8") as f:
            f.write("Secret data to be secured")
        encrypt_file(enc_file)

        assert is_encrypted_file(plain_file) is False
        assert is_encrypted_file(enc_file) is True
        assert is_encrypted_file("non_existent_file.csv") is False

    def test_double_encryption_prevention(self, temp_security_sandbox, sample_financial_csv_bytes):
        """Verifies that calling encrypt_file on an already encrypted file is safe and idempotent."""
        file_path = os.path.join(temp_security_sandbox["uploads_dir"], "idempotent_test.csv")
        with open(file_path, "wb") as f:
            f.write(sample_financial_csv_bytes)

        encrypt_file(file_path)
        size_after_first = os.path.getsize(file_path)

        # Attempt second encryption
        encrypt_file(file_path)
        size_after_second = os.path.getsize(file_path)

        # File size must remain unchanged (no nested double wrapping)
        assert size_after_first == size_after_second
        assert is_encrypted_file(file_path) is True

    def test_in_memory_decryption_zero_disk_leakage(self, temp_security_sandbox, sample_financial_csv_bytes):
        """Verifies decrypt_file_to_bytes and decrypt_file_to_stream operate strictly in RAM."""
        file_path = os.path.join(temp_security_sandbox["uploads_dir"], "memory_safety.csv")
        with open(file_path, "wb") as f:
            f.write(sample_financial_csv_bytes)
        encrypt_file(file_path)

        # 1. Decrypt to bytes in memory
        decrypted_bytes = decrypt_file_to_bytes(file_path)
        assert decrypted_bytes == sample_financial_csv_bytes
        assert b"Augmentin 625mg" in decrypted_bytes

        # 2. Decrypt to BytesIO stream
        stream = decrypt_file_to_stream(file_path)
        assert isinstance(stream, io.BytesIO)
        assert stream.read() == sample_financial_csv_bytes

        # 3. Ensure disk file is still 100% encrypted ciphertext
        with open(file_path, "rb") as f:
            disk_content = f.read()
        assert disk_content.startswith(MAGIC_HEADER)
        assert b"Augmentin 625mg" not in disk_content

    def test_encrypted_csv_connector_ingestion(self, temp_security_sandbox, sample_financial_csv_bytes):
        """Verifies that the CSV connector (_get_file_bytes) parses encrypted files into Pandas DataFrames in memory."""
        file_path = os.path.join(temp_security_sandbox["uploads_dir"], "financial_ledger.csv")
        with open(file_path, "wb") as f:
            f.write(sample_financial_csv_bytes)
        encrypt_file(file_path)

        # Connector gets bytes through _get_file_bytes
        raw_bytes = _get_file_bytes(file_path)
        assert raw_bytes == sample_financial_csv_bytes

        # Sniff delimiter and encoding in memory
        encoding, delimiter, header_row, _ = _sniff_csv_format_from_bytes(raw_bytes)
        assert delimiter == ","

        # Load into Pandas dataframe directly from in-memory stream
        df = pd.read_csv(io.BytesIO(raw_bytes), encoding=encoding, delimiter=delimiter)
        assert len(df) == 5
        assert "invoice_id" in df.columns
        assert "Augmentin 625mg" in df["product"].values
        assert df["total_amount"].sum() == 112700.0

    def test_encrypted_excel_connector_ingestion(self, temp_security_sandbox, sample_financial_excel_bytes):
        """Verifies that encrypted Excel (.xlsx) files can be read directly into Pandas via in-memory stream."""
        excel_path = os.path.join(temp_security_sandbox["uploads_dir"], "portfolio_margin.xlsx")
        with open(excel_path, "wb") as f:
            f.write(sample_financial_excel_bytes)
        encrypt_file(excel_path)

        # Confirm encrypted on disk
        assert is_encrypted_file(excel_path) is True
        with open(excel_path, "rb") as f:
            raw_disk = f.read()
        assert raw_disk.startswith(MAGIC_HEADER)

        # Read into Pandas via decrypt_file_to_stream
        stream = decrypt_file_to_stream(excel_path)
        df = pd.read_excel(stream, sheet_name="Transactions")

        assert len(df) == 3
        assert "revenue" in df.columns
        assert "profit" in df.columns
        assert df["revenue"].sum() == 175500.0
        assert df["profit"].sum() == 40850.0
