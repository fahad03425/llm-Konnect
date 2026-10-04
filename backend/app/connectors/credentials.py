"""Keep new Shopify source identities free of access tokens."""
import base64
import json
import os
import re
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.security.crypto import get_project_root, get_vault_key


def _directory() -> Path:
    base = Path(settings.storage_dir)
    if not base.is_absolute():
        base = Path(get_project_root()) / base
    return base / "source_credentials"


def save_shopify_token(shop: str, token: str) -> str:
    identity = uuid.uuid4().hex
    nonce = os.urandom(12)
    encrypted = AESGCM(get_vault_key()).encrypt(nonce, token.encode(), shop.encode())
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (identity + ".json")
    payload = json.dumps({"shop": shop, "token": base64.b64encode(nonce + encrypted).decode()})
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return identity


def read_shopify_token(identity: str, shop: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid Shopify connection identifier")
    try:
        payload = json.loads((_directory() / (identity + ".json")).read_text(encoding="utf-8"))
        if payload["shop"] != shop:
            raise ValueError("Shopify connection does not match this store")
        encrypted = base64.b64decode(payload["token"])
        return AESGCM(get_vault_key()).decrypt(encrypted[:12], encrypted[12:], shop.encode()).decode()
    except OSError as exc:
        raise ValueError("Shopify connection is unavailable. Reconnect the store.") from exc
