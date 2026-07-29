"""
Module 6.3 (Schema Mapping) — mapper.py
Pharmacy-first, offline. No LLM calls. Deterministic: same input → same output every run.

Provides:
  - suggest_mapping(): full pipeline (exact → synonym → fuzzy → value-inference)
  - apply_mapping(): re-exported from normalize.py for convenience
  - map_headers(): legacy thin wrapper (kept for backward compat with routes /normalize)
"""
import re
import difflib
from typing import Dict, List, Optional, Any, Tuple
from pydantic import BaseModel
from app.schema.domain import DomainPack
from app.schema.canonical import CORE_FIELDS


# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------

class MappingSuggestion(BaseModel):
    """Per-column suggestion from suggest_mapping."""
    source_column: str
    canonical_field: Optional[str] = None
    confidence: float = 0.0   # 0..1; <0.5 → unmapped
    reason: str = ""


class MappingProposal(BaseModel):
    """
    Serializable output of suggest_mapping, ready for the frontend
    confirmation UI or the /api/sources/preview endpoint.
    """
    columns: List[str]
    suggestions: List[MappingSuggestion]
    preview_rows: List[Dict[str, Any]]
    unmapped_fields: List[str]          # canonical fields with no source column
    required_fields_missing: List[str]  # required fields that are still unmapped


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_header_string(h: str) -> str:
    """
    Normalize a raw header string for matching.
    • lowercase + strip
    • remove punctuation (keeps Unicode letters so Urdu script survives)
    • collapse whitespace
    """
    if not isinstance(h, str):
        return ""
    h = h.lower().strip()
    # Remove punctuation (ASCII only) — keep Unicode word chars intact
    h = re.sub(r'[^\w\s]', '', h, flags=re.UNICODE)
    h = re.sub(r'\s+', ' ', h).strip()
    return h


def get_canonical_fields(domain_pack: Optional[DomainPack] = None) -> List[str]:
    """Return all canonical fields: core + domain-pack extras."""
    fields = list(CORE_FIELDS)
    if domain_pack:
        fields.extend(domain_pack.extra_fields)
    return fields


def _build_synonym_lookup(domain_pack: Optional[DomainPack]) -> Dict[str, str]:
    """
    Build {normalized_synonym: canonical_field} from the domain pack.
    Core fields themselves are added as synonyms (with underscore and space form).
    """
    lookup: Dict[str, str] = {}
    if domain_pack:
        for canonical_field, raw_synonyms in domain_pack.header_synonyms.items():
            for syn in raw_synonyms:
                norm = _normalize_header_string(syn)
                if norm:
                    lookup[norm] = canonical_field
    return lookup


def _infer_from_values(
    values: List[Any],
    canonical_fields: List[str],
) -> Tuple[Optional[str], float, str]:
    """
    Value-based heuristic: sniff a column's sample values to guess its type.
    Used only as a fallback when header matching fails.

    Returns (canonical_field | None, confidence, reason).
    All heuristics intentionally conservative (threshold >0.8 of rows).
    """
    if not values:
        return None, 0.0, ""

    non_empty = [
        str(v).strip()
        for v in values
        if v is not None and str(v).strip() not in ("", "nan", "None")
    ]
    if not non_empty:
        return None, 0.0, ""

    total = len(non_empty)

    # --- Heuristic 1: mm/yy or mm-yyyy patterns → expiry/date ---
    mmyy_re = re.compile(r'^\d{1,2}[/-]\d{2,4}$')
    date_matches = sum(1 for v in non_empty if mmyy_re.match(v))
    if date_matches / total > 0.8:
        if "expiry_date" in canonical_fields:
            return "expiry_date", 0.60, "Values match mm/yy expiry-date pattern"
        if "date" in canonical_fields:
            return "date", 0.55, "Values match mm/yy date pattern"

    # --- Heuristic 2: numeric amounts / quantities ---
    # Strip Pakistani currency symbols and separators before parsing
    _money_re = re.compile(r'(?i)(rs\.?|pkr|₨|,\s*)')
    money_count = 0
    small_int_count = 0

    for v in non_empty:
        cleaned = _money_re.sub('', v).strip().replace('/-', '').replace('/=', '')
        # Handle "(100)" → negative
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = cleaned[1:-1]
        try:
            fval = float(cleaned)
            money_count += 1
            if fval == int(fval) and 0 <= fval <= 9999:
                small_int_count += 1
        except ValueError:
            pass

    if small_int_count / total > 0.8:
        if "quantity" in canonical_fields:
            return "quantity", 0.60, "Values look like small non-negative integers"
    if money_count / total > 0.8:
        if "amount" in canonical_fields:
            return "amount", 0.55, "Values look like monetary amounts"

    # --- Heuristic 3: long alphanumeric identifiers (invoice/batch/barcode) ---
    # Must be ≥6 chars AND contain both letters and digits to avoid
    # matching plain numbers, dates, or money values
    id_re = re.compile(r'^(?=.*[A-Za-z])(?=.*\d).{6,}$')
    id_count = sum(1 for v in non_empty if id_re.match(v))
    if id_count / total > 0.8:
        if "invoice_id" in canonical_fields:
            return "invoice_id", 0.55, "Values look like alphanumeric identifiers"

    return None, 0.0, ""


def _resolve_conflicts(suggestions_map: Dict[str, MappingSuggestion]) -> None:
    """
    Guarantee one-to-one: if multiple source columns map to the same
    canonical field, keep the highest-confidence one; zero out the rest.
    Mutates suggestions_map in place.
    """
    field_to_suggs: Dict[str, List[MappingSuggestion]] = {}
    for sugg in suggestions_map.values():
        if sugg.canonical_field:
            field_to_suggs.setdefault(sugg.canonical_field, []).append(sugg)

    for field, suggs in field_to_suggs.items():
        if len(suggs) <= 1:
            continue
        # Sort descending by confidence, then alphabetically by source_column for determinism
        suggs.sort(key=lambda s: (-s.confidence, s.source_column))
        for loser in suggs[1:]:
            loser.canonical_field = None
            loser.confidence = 0.0
            loser.reason = (
                f"Conflict: '{suggs[0].source_column}' claimed '{field}' "
                f"with higher confidence ({suggs[0].confidence:.2f})"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_mapping(
    columns: List[str],
    sample_rows: List[Dict[str, Any]],
    domain_pack: Optional[DomainPack] = None,
) -> MappingProposal:
    """
    Auto-suggest a canonical mapping for the given source columns.

    Pipeline (in priority order):
      1. Exact match against canonical field names (e.g. "date" → date).
      2. Exact match against domain-pack synonym table (e.g. "qty" → quantity).
      3. Fuzzy match using difflib against canonical fields AND synonyms.
         Threshold: ≥0.75 ratio. Ties broken by score then alpha order.
      4. Value-based inference on sample_rows as a last resort.

    One canonical field is assigned to at most one source column.
    Confidence < 0.5 → suggestion is None (safer than a wrong guess).

    Args:
        columns:     Raw column names from the source.
        sample_rows: Small preview rows ({col: value}). Used for inference.
        domain_pack: Active domain pack (e.g. PharmacyDomainPack). None = core only.

    Returns:
        MappingProposal with per-column suggestions + metadata.
    """
    if not isinstance(columns, list):
        raise ValueError("columns must be a list of strings")

    canonical_fields = get_canonical_fields(domain_pack)
    synonyms = _build_synonym_lookup(domain_pack)

    # Pre-build normalized canonical targets once (avoid repeated work in loops)
    norm_canonical = [(cf, cf.replace("_", " ")) for cf in canonical_fields]

    suggestions_map: Dict[str, MappingSuggestion] = {}

    for col in columns:
        col_str = str(col) if col is not None else ""

        # Skip unnamed / blank columns
        if not col_str.strip() or col_str.startswith("Unnamed:"):
            suggestions_map[col_str] = MappingSuggestion(
                source_column=col_str,
                canonical_field=None,
                confidence=0.0,
                reason="Unnamed/blank column — skipped",
            )
            continue

        norm_raw = _normalize_header_string(col_str)
        best_field: Optional[str] = None
        best_conf: float = 0.0
        reason: str = ""

        # --- Stage 1: Exact match on canonical field names ---
        for cf, cf_spaced in norm_canonical:
            if norm_raw in (cf, cf_spaced):
                best_field = cf
                best_conf = 1.0
                reason = "Exact match with canonical field name"
                break

        # --- Stage 2: Exact match in synonym table ---
        if not best_field:
            if norm_raw in synonyms:
                best_field = synonyms[norm_raw]
                best_conf = 1.0
                reason = "Exact match with known synonym"

        # --- Stage 3: Fuzzy match ---
        if not best_field:
            best_fuzzy_field: Optional[str] = None
            best_fuzzy_score: float = 0.0

            # Check against canonical field names (underscore and space form)
            for cf, cf_spaced in norm_canonical:
                for target in (cf, cf_spaced):
                    score = difflib.SequenceMatcher(None, norm_raw, target).ratio()
                    if score > best_fuzzy_score or (
                        score == best_fuzzy_score and (best_fuzzy_field or "") > cf
                    ):
                        best_fuzzy_score = score
                        best_fuzzy_field = cf

            # Check against synonyms
            for syn, cf in synonyms.items():
                score = difflib.SequenceMatcher(None, norm_raw, syn).ratio()
                if score > best_fuzzy_score or (
                    score == best_fuzzy_score and (best_fuzzy_field or "") > cf
                ):
                    best_fuzzy_score = score
                    best_fuzzy_field = cf

            if best_fuzzy_score >= 0.75 and best_fuzzy_field:
                best_field = best_fuzzy_field
                best_conf = round(best_fuzzy_score, 4)
                reason = f"Fuzzy match (ratio={best_fuzzy_score:.2f})"

        # --- Stage 4: Value-based inference (last resort) ---
        if not best_field:
            col_values = [row.get(col_str) for row in sample_rows]
            inf_field, inf_conf, inf_reason = _infer_from_values(col_values, canonical_fields)
            if inf_field and inf_conf > best_conf:
                best_field = inf_field
                best_conf = inf_conf
                reason = inf_reason

        # --- Confidence gate: below 0.5 → leave unmapped ---
        if best_conf < 0.5:
            best_field = None
            best_conf = 0.0
            reason = "No confident match found (confidence below threshold)"

        suggestions_map[col_str] = MappingSuggestion(
            source_column=col_str,
            canonical_field=best_field,
            confidence=best_conf,
            reason=reason,
        )

    # --- Conflict resolution ---
    _resolve_conflicts(suggestions_map)

    suggestions = list(suggestions_map.values())
    mapped_canonical = {s.canonical_field for s in suggestions if s.canonical_field}
    unmapped_fields = [f for f in canonical_fields if f not in mapped_canonical]

    # Required-fields logic:
    # A transaction needs (date + amount). An inventory record needs product_id.
    # If product_id is mapped we treat as inventory → no txn fields required.
    txn_required = ["date", "amount"]
    if "product_id" in mapped_canonical:
        required_missing = []   # inventory mode — product_id is enough
    else:
        required_missing = [f for f in txn_required if f not in mapped_canonical]

    return MappingProposal(
        columns=[str(c) if c is not None else "" for c in columns],
        suggestions=suggestions,
        preview_rows=sample_rows,
        unmapped_fields=unmapped_fields,
        required_fields_missing=required_missing,
    )


def map_headers(
    raw_headers: List[str],
    domain_pack: Optional[DomainPack] = None,
) -> Dict[str, str]:
    """
    Legacy helper: returns {raw_header: canonical_field} for all columns
    that receive ANY confident suggestion (not just exact matches).
    Retained for backward compatibility with the /normalize endpoint.
    """
    proposal = suggest_mapping(raw_headers, [], domain_pack)
    return {
        s.source_column: s.canonical_field
        for s in proposal.suggestions
        if s.canonical_field is not None
    }
