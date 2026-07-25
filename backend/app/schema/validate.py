import pandas as pd
from typing import List, Dict, Any

from app.schema.domain import Problem, get_domain_pack
from app.schema.canonical import validate_core_rules

def validate(normalized: pd.DataFrame, domain: str = "pharmacy") -> List[Problem]:
    """
    Validates a normalized DataFrame row by row.
    Combines core domain-agnostic rules with domain-specific rules from the active DomainPack.
    Returns a list of all identified problems across the dataset.
    """
    all_problems = []
    
    try:
        domain_pack = get_domain_pack(domain)
    except ValueError:
        domain_pack = None

    # Replace pandas NaN/NaT with None so validation rules have an easier time
    # This creates a list of dictionaries where missing values are genuinely None
    records = normalized.replace({pd.NA: None, pd.NaT: None}).to_dict(orient="records")
    
    for i, row in enumerate(records):
        # We use source_row if available to give meaningful index feedback, otherwise just the df index
        row_idx = row.get("source_row", i + 1)
        
        # 1. Core Rules
        core_problems = validate_core_rules(row, row_idx)
        all_problems.extend(core_problems)
        
        # If the row is completely empty, skip domain rules
        if any(p.message == "Row is completely empty" for p in core_problems):
            continue
            
        # 2. Domain Pack Rules
        if domain_pack:
            domain_problems = domain_pack.validate_row(row, row_idx)
            all_problems.extend(domain_problems)
            
    return all_problems
