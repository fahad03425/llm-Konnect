import os
import json
import hashlib
from typing import Dict, List, Optional
from pathlib import Path
from app.core.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # backend/../..

def _get_store_dir() -> Path:
    base = Path(settings.storage_dir)
    if not base.is_absolute():
        base = _PROJECT_ROOT / base
    d = base / "mapping_profiles"
    os.makedirs(d, exist_ok=True)
    return d

def source_signature(columns: List[str], connector_type: str = "") -> str:
    """
    Creates a unique hash for a source based on its column names.
    Sorted, lowercased, whitespace-stripped to ensure stable signatures.
    """
    normalized_cols = sorted([str(c).lower().strip() for c in columns if c and not str(c).startswith("Unnamed:")])
    sig_string = f"{connector_type}::" + "|".join(normalized_cols)
    return hashlib.md5(sig_string.encode('utf-8')).hexdigest()

def save_profile(signature: str, mapping: Dict[str, str], label: str = "Unknown Source") -> None:
    """
    Save a user-confirmed mapping profile.
    """
    store_dir = _get_store_dir()
    filepath = store_dir / f"{signature}.json"
    
    data = {
        "signature": signature,
        "label": label,
        "mapping": mapping
    }
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def find_profile(signature: str) -> Optional[Dict]:
    """
    Look up a mapping profile by its signature.
    Returns the profile dict if found, else None.
    """
    store_dir = _get_store_dir()
    filepath = store_dir / f"{signature}.json"
    
    if filepath.exists():
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return None

def list_profiles() -> List[Dict]:
    """
    List all saved mapping profiles.
    """
    store_dir = _get_store_dir()
    profiles = []
    
    for filename in os.listdir(store_dir):
        if filename.endswith(".json"):
            filepath = store_dir / filename
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    profiles.append(json.load(f))
            except json.JSONDecodeError:
                continue
                
    return profiles

def delete_profile(signature: str) -> bool:
    """
    Delete a mapping profile. Returns True if deleted, False if not found.
    """
    store_dir = _get_store_dir()
    filepath = store_dir / f"{signature}.json"
    
    if filepath.exists():
        os.remove(filepath)
        return True
    return False
