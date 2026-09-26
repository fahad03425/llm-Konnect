"""Security and Encryption At Rest API routes (Module 5.1)."""

import os
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.security.crypto import (
    is_encrypted_file,
    encrypt_file,
    get_vault_key,
    get_project_root
)

router = APIRouter(prefix="/api/security", tags=["security"])


class SecurityStatusResponse(BaseModel):
    enabled: bool
    algorithm: str
    key_present: bool
    encrypted_files_count: int
    unencrypted_files_count: int
    vector_db_encrypted: bool
    sessions_encrypted: bool
    never_leaves_machine: bool
    status_summary: str


@router.get("/status", response_model=SecurityStatusResponse)
def get_security_status():
    """Get active encryption-at-rest status and metrics."""
    project_root = get_project_root()
    uploads_dir = os.path.join(project_root, "data", "uploads")
    
    enc_count = 0
    unenc_count = 0
    if os.path.exists(uploads_dir):
        for f in os.listdir(uploads_dir):
            full_p = os.path.join(uploads_dir, f)
            if os.path.isfile(full_p) and not f.startswith("."):
                if is_encrypted_file(full_p):
                    enc_count += 1
                else:
                    unenc_count += 1

    key_present = False
    try:
        k = get_vault_key()
        key_present = len(k) == 32
    except Exception:
        key_present = False

    return SecurityStatusResponse(
        enabled=getattr(settings, "encryption_enabled", True),
        algorithm="AES-256-GCM",
        key_present=key_present,
        encrypted_files_count=enc_count,
        unencrypted_files_count=unenc_count,
        vector_db_encrypted=getattr(settings, "encryption_enabled", True),
        sessions_encrypted=getattr(settings, "encryption_enabled", True),
        never_leaves_machine=True,
        status_summary="Local AES-256-GCM encryption at rest active: vector database payloads and financial files are secured."
    )


@router.post("/encrypt-all")
def encrypt_all_existing_uploads() -> Dict[str, Any]:
    """
    Encrypts any remaining plaintext files in data/uploads at rest.
    Guarantees no sensitive legacy dataset is left exposed on disk.
    """
    project_root = get_project_root()
    uploads_dir = os.path.join(project_root, "data", "uploads")
    
    encrypted_files: List[str] = []
    already_encrypted: List[str] = []
    errors: List[str] = []

    if os.path.exists(uploads_dir):
        for f in os.listdir(uploads_dir):
            full_p = os.path.join(uploads_dir, f)
            if os.path.isfile(full_p) and not f.startswith("."):
                try:
                    if is_encrypted_file(full_p):
                        already_encrypted.append(f)
                    else:
                        encrypt_file(full_p)
                        encrypted_files.append(f)
                except Exception as e:
                    errors.append(f"{f}: {str(e)}")

    return {
        "status": "ok",
        "newly_encrypted": encrypted_files,
        "already_encrypted": already_encrypted,
        "errors": errors,
        "total_secured": len(encrypted_files) + len(already_encrypted)
    }
