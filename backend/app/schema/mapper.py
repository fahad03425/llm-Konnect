import re
from typing import Dict, List, Optional
from app.schema.domain import DomainPack
from app.schema.canonical import CORE_FIELDS

def _normalize_header_string(h: str) -> str:
    """Normalize a raw header string for fuzzy matching."""
    if not isinstance(h, str):
        return ""
    # Lowercase, strip spaces
    h = h.lower().strip()
    # Remove punctuation except alphanumeric and space
    h = re.sub(r'[^\w\s]', '', h)
    # Collapse multiple spaces
    h = re.sub(r'\s+', ' ', h)
    return h

def get_canonical_fields(domain_pack: Optional[DomainPack] = None) -> List[str]:
    """Get all canonical fields including domain-specific ones."""
    fields = list(CORE_FIELDS)
    if domain_pack:
        fields.extend(domain_pack.extra_fields)
    return fields

def map_headers(raw_headers: List[str], domain_pack: Optional[DomainPack] = None) -> Dict[str, str]:
    """
    Map raw headers to canonical fields using exact and fuzzy matching.
    Returns a dictionary mapping {raw_header: canonical_field}.
    """
    mapping = {}
    canonical_fields = get_canonical_fields(domain_pack)
    
    # Build synonym lookup
    synonyms: Dict[str, str] = {}
    if domain_pack:
        for canonical_field, raw_synonyms in domain_pack.header_synonyms.items():
            for syn in raw_synonyms:
                synonyms[_normalize_header_string(syn)] = canonical_field
                
    for raw_field in raw_headers:
        if not raw_field or str(raw_field).startswith("Unnamed:"):
            continue
            
        norm_raw = _normalize_header_string(str(raw_field))
        
        # 1. Exact match on canonical fields
        if norm_raw in [f.replace("_", " ") for f in canonical_fields] or norm_raw in canonical_fields:
            # find which canonical field it is
            for cf in canonical_fields:
                if norm_raw == cf or norm_raw == cf.replace("_", " "):
                    mapping[raw_field] = cf
                    break
            continue

        # 2. Synonym match
        if norm_raw in synonyms:
            mapping[raw_field] = synonyms[norm_raw]
            continue
            
        # Unmapped fields are left out of the mapping dictionary.
        # They will be carried over as extra properties if needed, or ignored depending on connector.
        
    return mapping
