"""Module 5.1 — Local data security (encryption at rest). Month 6.

Encrypts local vector database payloads and ingested financial files at rest,
reinforcing the system's core claim that sensitive data never leaves the machine
and is not left exposed on disk either.
"""

import os
import io
import base64
import threading
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from app.core.config import settings

# 8-byte magic header identifying LLM-Konnect AES-256-GCM encrypted data
MAGIC_HEADER = b"LLMENC01"
NONCE_LENGTH = 12  # Standard 96-bit nonce for AES-GCM
KEY_LENGTH = 32    # 256-bit key for AES-256

_key_lock = threading.Lock()
_cached_key: Optional[bytes] = None


def get_project_root() -> str:
    """Returns absolute path to the project root directory."""
    # backend/app/security/crypto.py -> backend -> project_root
    cur = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(cur)))


def _resolve_vault_key_path() -> str:
    """Resolves the absolute path to the local keyfile."""
    key_path = getattr(settings, "vault_key_path", "data/.vault_key")
    if os.path.isabs(key_path):
        return key_path
    return os.path.join(get_project_root(), key_path)


def get_vault_key() -> bytes:
    """
    Retrieves or generates the local 256-bit AES master encryption key.
    
    1. If `settings.secret_key` or env var `LLM_KONNECT_SECRET_KEY` is set,
       derives a 256-bit key using HKDF-SHA256.
    2. Otherwise, uses the persistent local keyfile (`data/.vault_key`).
       If the keyfile does not exist, a new cryptographically secure 256-bit key
       is generated and saved with owner-only permissions.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    with _key_lock:
        if _cached_key is not None:
            return _cached_key

        secret_env = os.getenv("LLM_KONNECT_SECRET_KEY") or getattr(settings, "secret_key", None)
        if secret_env:
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=KEY_LENGTH,
                salt=b"llm_konnect_vault_salt_v1",
                info=b"local_encryption_at_rest",
            )
            _cached_key = hkdf.derive(secret_env.encode("utf-8"))
            return _cached_key

        key_file = _resolve_vault_key_path()
        if os.path.exists(key_file):
            try:
                with open(key_file, "rb") as f:
                    key = f.read(KEY_LENGTH)
                    if len(key) == KEY_LENGTH:
                        _cached_key = key
                        return _cached_key
            except Exception:
                pass

        # Generate new random 256-bit key
        key = os.urandom(KEY_LENGTH)
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        try:
            # Write with owner-only read/write flags
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            mode = 0o600
            fd = os.open(key_file, flags, mode)
            with open(fd, "wb") as f:
                f.write(key)
        except Exception:
            with open(key_file, "wb") as f:
                f.write(key)

        _cached_key = key
        return _cached_key


def reset_cached_key():
    """Clear in-memory cached key (primarily for test isolation)."""
    global _cached_key
    with _key_lock:
        _cached_key = None


def is_encrypted_bytes(data: bytes) -> bool:
    """Checks if the byte sequence begins with the LLM-Konnect encryption magic header."""
    return data.startswith(MAGIC_HEADER) and len(data) >= (len(MAGIC_HEADER) + NONCE_LENGTH + 16)


def is_encrypted_file(file_path: str) -> bool:
    """Checks if a file on disk has the LLM-Konnect encryption header."""
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            header = f.read(len(MAGIC_HEADER))
            return header == MAGIC_HEADER
    except Exception:
        return False


def encrypt_bytes(plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
    """
    Encrypts bytes using AES-256-GCM.
    
    Structure: [8-byte MAGIC_HEADER] + [12-byte NONCE] + [CIPHERTEXT + 16-byte TAG]
    """
    if not getattr(settings, "encryption_enabled", True):
        return plaintext

    key = get_vault_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_LENGTH)
    ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
    return MAGIC_HEADER + nonce + ciphertext


def decrypt_bytes(data: bytes, aad: Optional[bytes] = None, allow_passthrough: bool = True) -> bytes:
    """
    Decrypts AES-256-GCM encrypted bytes with authentication.
    
    If `allow_passthrough` is True and data is not encrypted, returns data as-is.
    If corrupted or tampered with, raises `cryptography.exceptions.InvalidTag`.
    """
    if not is_encrypted_bytes(data):
        if allow_passthrough:
            return data
        raise ValueError("Data does not contain a valid LLM-Konnect encryption header")

    key = get_vault_key()
    aesgcm = AESGCM(key)
    nonce_start = len(MAGIC_HEADER)
    nonce_end = nonce_start + NONCE_LENGTH
    nonce = data[nonce_start:nonce_end]
    ciphertext = data[nonce_end:]
    return aesgcm.decrypt(nonce, ciphertext, aad)


def encrypt_string(text: str) -> str:
    """
    Encrypts a string into a safe portable envelope prefixed with `enc:`.
    """
    if not text:
        return text
    if not getattr(settings, "encryption_enabled", True):
        return text
    enc_bytes = encrypt_bytes(text.encode("utf-8"))
    b64 = base64.urlsafe_b64encode(enc_bytes).decode("ascii")
    return f"enc:{b64}"


def decrypt_string(enc_text: str, allow_passthrough: bool = True) -> str:
    """
    Decrypts an `enc:` prefixed string back to utf-8 plaintext.
    If not prefixed, returns string as-is when `allow_passthrough` is True.
    """
    if not enc_text or not isinstance(enc_text, str):
        return enc_text
    if not enc_text.startswith("enc:"):
        if allow_passthrough:
            return enc_text
        raise ValueError("String does not have 'enc:' prefix")

    b64_payload = enc_text[4:]
    try:
        raw_encrypted = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
        decrypted_bytes = decrypt_bytes(raw_encrypted, allow_passthrough=False)
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        if allow_passthrough:
            return enc_text
        raise e


def encrypt_file(file_path: str, dest_path: Optional[str] = None) -> str:
    """
    Encrypts a file on disk at rest using AES-256-GCM.
    Writes atomically using a temporary file. Returns output path.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Avoid double encryption
    if is_encrypted_file(file_path):
        if dest_path and dest_path != file_path:
            import shutil
            shutil.copy2(file_path, dest_path)
            return dest_path
        return file_path

    target_path = dest_path or file_path
    tmp_path = target_path + ".tmp_enc"

    with open(file_path, "rb") as f_in:
        plaintext = f_in.read()

    encrypted_data = encrypt_bytes(plaintext)

    with open(tmp_path, "wb") as f_out:
        f_out.write(encrypted_data)

    if os.path.exists(target_path):
        os.remove(target_path)
    os.rename(tmp_path, target_path)

    return target_path


def decrypt_file_to_bytes(file_path: str) -> bytes:
    """
    Reads an encrypted file and decrypts it directly into memory.
    Plaintext is NEVER written to disk.
    If the file is not encrypted (e.g. legacy/sample), returns raw bytes.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "rb") as f:
        data = f.read()

    return decrypt_bytes(data, allow_passthrough=True)


def decrypt_file_to_stream(file_path: str) -> io.BytesIO:
    """Returns an in-memory BytesIO stream containing decrypted file contents."""
    return io.BytesIO(decrypt_file_to_bytes(file_path))
