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

def source_signature(columns: List[str], connector_type: str = "", domain: str = "", source: str = "") -> str:
    """
    Creates a unique hash for a source based on its column names.
    Sorted, lowercased, whitespace-stripped to ensure stable signatures.
    """
    normalized_cols = sorted([str(c).lower().strip() for c in columns if c and not str(c).startswith("Unnamed:")])
    sig_string = f"{connector_type}::" + "|".join(normalized_cols)
    if domain or source:
        sig_string += f"::{domain}::{source}"
    return hashlib.md5(sig_string.encode('utf-8')).hexdigest()

def save_profile(signature: str, mapping: Dict[str, str], label: str = "Unknown Source") -> None:
    """
    Save a user-confirmed mapping profile.
    """
    store_dir = _get_store_dir()
    filepath = store_dir / f"{signature}.json"
    
    data = {
        "signature": signature,
        "mapping_version": 2,
        "label": label,
        "mapping": mapping
    }
    
    import tempfile
    descriptor, temporary = tempfile.mkstemp(dir=store_dir, suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(temporary, filepath)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)

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


def compatible_mapping(mapping: Dict[str, str], columns: List[str]) -> Dict[str, str]:
    """Rebind saved keys to this source's casing without guessing changed headers."""
    lookup = {}
    for column in columns:
        lookup.setdefault(str(column).strip().casefold(), []).append(column)
    result = {}
    for source, target in mapping.items():
        matches = lookup.get(str(source).strip().casefold(), [])
        if len(matches) != 1:
            raise ValueError(f"Saved mapping column '{source}' is missing or ambiguous. Review the mapping again.")
        result[matches[0]] = target
    return result


def source_identity(path: str, sheet: str = "", query: str = "") -> str:
    """Exclude URI credentials while keeping store/resource identity and local paths."""
    from urllib.parse import urlsplit, parse_qs, urlencode
    if "://" in path:
        parsed = urlsplit(path)
        options = parse_qs(parsed.query)
        safe = {key: options[key][0] for key in ("resource", "review_type", "query", "company", "company_name") if key in options}
        host = parsed.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        if parsed.port is not None:
            host += f':{parsed.port}'
        path = f"{parsed.scheme}://{host}{parsed.path}?{urlencode(safe)}"
    else:
        path = os.path.normcase(os.path.abspath(path))
    return f"{path}::{sheet or ''}::{query or ''}"


def sql_signature(columns, connector_type, domain, connection_string, table):
    # Connection strings can contain passwords; persist only their digest.
    import re
    match = re.fullmatch(r'\s*select\s+\*\s+from\s+[\["`]?([\w ]+?)[\]"`]?\s*;?\s*', table, re.I)
    if match:
        table = match.group(1).strip()
    identity = hashlib.sha256(connection_string.encode()).hexdigest() + '::' + table
    return source_signature([c for c in columns if c not in ('source_row', 'source_connector')], connector_type, domain, identity)


def confirmed_mapping(signature, frame, domain):
    from app.schema.normalize import validate_mapping
    profile = find_profile(signature)
    if not profile:
        return None
    try:
        mapping = compatible_mapping(profile['mapping'], list(frame.columns))
        validate_mapping(frame, mapping, domain)
        if profile.get("mapping_version", 0) >= 2:
            return mapping  # Preserve deliberate, explicitly confirmed overrides.
        from app.schema.mapper import _normalize_header_string, suggest_mapping
        from app.schema.domain import get_domain_pack
        protected_targets = {"quantity", "amount", "cost", "invoice_id", "unit_price"}
        proposal = suggest_mapping(list(frame.columns), frame.head(50).to_dict("records"), get_domain_pack(domain))
        suggestions = {item.source_column: item for item in proposal.suggestions}
        for source, target in mapping.items():
            name = _normalize_header_string(source)
            import re
            if target in protected_targets:
                if re.search(r"\b(?:active|disable|disabled|flag|index|mode)\b", name):
                    return None
                if target in {"quantity", "amount", "cost", "unit_price"} and re.search(r"\b(?:id|code|number)\b", name):
                    return None
                if target == "invoice_id" and (name in {"p orig name", "unit name", "store id", "address"} or "product name" in name):
                    return None
            suggested = suggestions.get(source)
            if target in protected_targets and (not suggested or suggested.canonical_field is None):
                return None
            if suggested and suggested.confidence == 1.0 and suggested.canonical_field and suggested.canonical_field != target:
                return None
        return mapping
    except (ValueError, KeyError):
        return None
