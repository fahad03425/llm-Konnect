import re
from typing import Dict, Any, Tuple, Optional
import pandas as pd
from app.schema.domain import get_domain_pack

class RouteType:
    RAG = "rag"
    ANALYTICS = "analytics"
    CHITCHAT = "chit-chat"

def classify_route(question: str) -> str:
    """
    Deterministic rule-based intent classification.
    Route to Analytics if question asks for numbers, aggregates, margins.
    Route to Chit-chat if it's a greeting.
    Default to RAG (record lookup).
    """
    q_lower = question.lower()
    
    # Chit-chat keywords
    chitchat_patterns = [
        r"^(hello|hi|hey|salam|assalam)\b",
        r"what can you do",
        r"who are you"
    ]
    for pattern in chitchat_patterns:
        if re.search(pattern, q_lower):
            return RouteType.CHITCHAT
            
    # Analytics / Numeric keywords
    analytics_patterns = [
        r"\btotal\b", r"\bhow much\b", r"\bhow many\b", r"\bsum\b",
        r"\baverage\b", r"\bkitna\b", r"\bkitne\b", r"\bprofit\b",
        r"\bmargin\b", r"\bexpiring\b", r"\bexpire\b"
    ]
    for pattern in analytics_patterns:
        if re.search(pattern, q_lower):
            return RouteType.ANALYTICS
            
    return RouteType.RAG

def extract_filters(question: str, domain: str = "pharmacy") -> Dict[str, Any]:
    """
    Extract exact-match filters from the question using the domain pack's vocabulary.
    This helps improve retrieval precision.
    (Currently a simplistic rule-based extraction).
    """
    filters = {}
    q_lower = question.lower()
    
    try:
        pack = get_domain_pack(domain)
        # Extract month if present
        months = {
            "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
            "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
            "november": 11, "nov": 11, "december": 12, "dec": 12
        }
        for m_name, m_num in months.items():
            if re.search(rf"\b{m_name}\b", q_lower):
                filters["month"] = m_num
                break
                
        # In a real system, we'd also extract product_id, supplier_id etc. here based on known lists.
        # But without an exact ontology, month/year is the safest hard filter to extract deterministically.
        
    except ValueError:
        pass
        
    return filters

class AnalyticsRouter:
    """
    Seam for Module 6.6 (Analytics Engine).
    Provides a deterministic pandas fallback for common aggregates to ensure the LLM
    never computes numbers itself.
    """
    def compute(self, question: str, filters: Dict[str, Any], kb_records: list) -> Tuple[Optional[Dict[str, Any]], list]:
        """
        TODO (Module 6.6): Replace this fallback with the real Analytics Engine that queries
        the canonical DataFrame directly, rather than relying on Chroma metadata.
        
        For now, this fallback loads the metadata from the retrieved chunks (or a passed list of records)
        into a DataFrame and computes simple aggregates.
        
        Returns:
            computed_values: dict of computed aggregates
            sources: list of source_row indices used
        """
        if not kb_records:
            return None, []
            
        df = pd.DataFrame(kb_records)
        computed = {}
        sources = df.get("source_row", pd.Series(dtype=int)).dropna().astype(int).tolist()
        
        q_lower = question.lower()
        
        # Simple fallback logic based on keywords
        if "total" in q_lower or "sum" in q_lower or "kitna" in q_lower:
            if "amount" in df.columns:
                computed["total_amount"] = float(df["amount"].sum())
            elif "quantity" in df.columns:
                computed["total_quantity"] = float(df["quantity"].sum())
                
        elif "average" in q_lower:
            if "amount" in df.columns:
                computed["average_amount"] = float(df["amount"].mean())
                
        elif "expiring" in q_lower or "expire" in q_lower:
            if "expiry_date" in df.columns:
                # Count how many records have an expiry date
                computed["expiring_count"] = int(df["expiry_date"].notna().sum())
                
        if not computed and len(df) > 0:
            computed["record_count"] = len(df)
            
        return computed, sources
