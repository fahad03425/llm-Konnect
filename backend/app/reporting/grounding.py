"""
Grounded narrative parsing, fact cataloging, and verification primitives.
Ensures report narratives strictly reference deterministically computed figures.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union


class GroundedNarrative(str):
    """
    A string wrapper carrying metadata about structured fact IDs and commentary.
    """
    fact_ids: List[str]
    commentary: List[str]
    error: Optional[str]

    def __new__(cls, content: str, fact_ids: Optional[List[str]] = None, commentary: Optional[List[str]] = None, error: Optional[str] = None):
        obj = super().__new__(cls, content)
        obj.fact_ids = fact_ids or []
        obj.commentary = commentary or []
        obj.error = error
        return obj


def fact_catalog(ground_truth: Any) -> Dict[str, Tuple[float, str, str]]:
    """
    Extract a normalized lookup table mapping fact_id -> (value, unit, display_name).
    """
    catalog: Dict[str, Tuple[float, str, str]] = {}

    if hasattr(ground_truth, "get_all_verifiable_numbers"):
        for entry in ground_truth.get_all_verifiable_numbers():
            key = entry[0]
            val = float(entry[1])
            unit = entry[2] if len(entry) > 2 else ""
            catalog[key] = (val, unit, key)

    kpis = getattr(ground_truth, "kpis", ground_truth if isinstance(ground_truth, dict) else {})
    if isinstance(kpis, dict):
        for k, res in kpis.items():
            val = getattr(res, "value", None) if not isinstance(res, dict) else res.get("value")
            unit = getattr(res, "unit", "") if not isinstance(res, dict) else res.get("unit", "")
            name = getattr(res, "name", k) if not isinstance(res, dict) else res.get("name", k)
            if val is not None:
                try:
                    catalog[k] = (float(val), unit or "PKR", name)
                except (ValueError, TypeError):
                    pass

    return catalog


def render(fact_ids: List[str], commentary: List[str], catalog: Dict[str, Tuple[float, str, str]]) -> str:
    """
    Render deterministic prose from verified fact IDs and optional commentary.
    """
    sections: List[str] = []
    
    fact_lines: List[str] = []
    for fid in fact_ids:
        if fid not in catalog:
            raise KeyError(f"Fact '{fid}' not found in ground truth catalog")
        val, unit, name = catalog[fid]
        if unit in ("PKR", "USD", "EUR", "GBP"):
            fact_lines.append(f"{name}: {unit} {val:,.2f}")
        elif unit == "percent":
            fact_lines.append(f"{name}: {val:.2f}%")
        else:
            fact_lines.append(f"{name}: {val:,.4g} {unit}".strip())

    if fact_lines:
        sections.append(". ".join(fact_lines) + ".")

    if commentary:
        valid_comments = [c.strip() for c in commentary if c and c.strip()]
        # The model may add qualitative interpretation, but every quantitative
        # statement must be emitted from a catalog fact ID. Otherwise a number
        # in commentary could bypass verification while the referenced fact is
        # valid, making the report appear verified despite a fabricated claim.
        if any(has_quantity(comment) for comment in valid_comments):
            raise ValueError("Narrative commentary must not contain numeric claims; cite a fact ID instead")
        if valid_comments:
            sections.append(" ".join(valid_comments))

    return "\n\n".join(sections).strip()


def has_quantity(text: str) -> bool:
    """Check if free-form text contains any numeric quantities or currency amounts."""
    if not text:
        return False
    # Catch spelled-out quantities too, so commentary cannot bypass the fact
    # catalog with a phrase such as "five percent growth".
    number_words = (
        "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
        "thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
        "million|billion|first|second|third|fourth|fifth|sixth|seventh|"
        "eighth|ninth|tenth|half|quarter"
    )
    return bool(re.search(r"\d|\b(?:" + number_words + r")\b", text, re.IGNORECASE))


def parse_response(raw_text: str, ground_truth: Any) -> Union[GroundedNarrative, str]:
    """
    Parse an LLM response (JSON or structured block) into a GroundedNarrative.
    """
    if not raw_text or not isinstance(raw_text, str):
        return GroundedNarrative("", [], [], error="Empty response")

    text = raw_text.strip()

    # Try extracting JSON object if fenced or surrounded by prose
    json_str = None
    if text.startswith("{") and text.endswith("}"):
        json_str = text
    else:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            json_str = match.group(0)

    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "fact_ids" in data:
                fact_ids = data.get("fact_ids", [])
                commentary = data.get("commentary", [])
                catalog = fact_catalog(ground_truth)
                rendered = render(fact_ids, commentary, catalog)
                return GroundedNarrative(rendered, fact_ids=fact_ids, commentary=commentary)
        except Exception as exc:
            return GroundedNarrative(text, [], [], error=str(exc))

    # Return plain string / narrative without grounding wrapper for ungrounded prose
    return text
