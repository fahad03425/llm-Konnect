"""Operation-aware guards shared by regular and streaming chat."""
import re


def is_dataset_question_suggestion_request(question: str) -> bool:
    """Recognize requests for example questions grounded in the active scope."""
    q = (question or "").casefold().strip()
    return bool(
        re.search(r"\bwhat\s+(?:(?:other|more|else)\s+)?(?:(?:type|kind|kinds)(?:\s+of)?\s+)?questions?\s+can\s+you\s+answer\b", q)
        or re.search(
            r"\b(?:give|suggest|list|show)\s+(?:me\s+)?(?:\d{1,2}\s+)?(?:some\s+|a\s+few\s+|example\s+|sample\s+)?questions?\b"
            r".{0,100}\b(?:specific|relevant|related|based|about|from|for|to)\b.{0,50}\b(?:data|dataset|database|file|table|scope|records?)\b",
            q,
        )
        or re.search(r"\bquestions?\s+(?:i|we)\s+can\s+ask\b.{0,60}\b(?:data|dataset|database|file|table|scope)\b", q)
    )


def is_knowledge_question(question: str) -> bool:
    q = (question or "").casefold()
    # Explicit measurements take precedence over explanatory phrasing.
    if re.search(r"\b(calculate|compute|quantify|how much|how many|total|sum(?:med)?|aggregate|average|compare|percentage|percent|trend|rank|top \d+)\b", q):
        return False
    if (re.search(r"\b(tables?|columns?|fields?|schema)\b", q)
            and re.search(r"\b(describe|explain|what|which|available|exist|contain|mean)\b", q)):
        return True
    if re.search(r"\b(my|our|actual|measured|in (?:this|the|my|our) (?:data|dataset|database|store)|based on (?:the |my |our )?(?:data|records))\b", q):
        return False
    return bool(
        re.search(r"\b(define|definition|meaning|explain|what does .{0,60} mean|what is .{0,30} turnover|how (?:does|do) .{0,100}(?:affect|influence|work)|how .{0,60} calculated)\b", q)
        or (re.search(r"\b(tables?|columns?|fields?|schema)\b", q)
            and re.search(r"\b(describe|explain|what|which|available|exist|contain|mean)\b", q))
    )


def is_procedural_how_to_question(question: str) -> bool:
    """Recognize requests for application steps, distinct from data metrics."""
    q = (question or "").casefold()
    asks_how = re.search(r"\b(?:how\s+(?:do|can|should)\s+(?:i|we|you)|how\s+to|steps?\s+to|procedure|workflow|instructions?\s+for)\b", q)
    asks_action = re.search(
        r"\b(void|cancel|reverse|edit|change|create|configure|set\s+up|log\s+in|unlock|reprint|"
        r"record|sync|synchroni[sz]e|back\s*up|restore|export|import|delete|refund|close|reopen|"
        r"troubleshoot|fix|install|use)\b",
        q,
    )
    reports_symptom = re.search(
        r"\b(not working|stopped working|isn't working|is not working|won't|doesn't work|does not work|"
        r"error|issue|problem|trouble|failed|failure|keeps? (?:crashing|freezing|disconnecting))\b",
        q,
    )
    asks_troubleshooting = re.search(
        r"\b(what should (?:i|we) check|what can (?:i|we) do|how can (?:i|we) fix|how to fix|"
        r"how should (?:i|we) troubleshoot|what is wrong|how do (?:i|we) resolve)\b",
        q,
    )
    return bool((asks_how and asks_action) or (reports_symptom and asks_troubleshooting))


def analytics_request_guard(question: str):
    """Decline ambiguous or compound analyses before a generic KPI claims them."""
    q = (question or "").casefold()
    reason = None
    status = "needs_clarification"
    if re.search(r"\b(?:specific|particular) products?\b", q):
        reason = "Which product or products should I analyse, and over what date range? Please provide their names or IDs."
    elif re.search(r"\b(purchase|purchasing|buying) patterns?\b", q) and re.search(r"\b(high[- ]value|valuable) items?\b", q):
        reason = "Should I rank products by sales revenue, unit price, or purchase frequency, and for which period? Customer purchase patterns and product rankings require separate measures."
    elif re.search(r"\b(?:impact|effect|influence)\b", q) and re.search(r"\bpayment methods?\b", q):
        reason = "I can compare average transaction amounts by payment method, but that comparison does not establish cause and effect. Which date range should I compare?"
    elif re.search(r"\binventory turnover\b", q) and not is_knowledge_question(q):
        reason = "Inventory turnover requires cost of goods sold and average inventory value for the same period. Specify the period and select records containing those measures; sales totals alone cannot answer this."
        status = "unavailable"
    elif re.search(r"\bperformance\b", q) and not re.search(r"\b(sales?|revenue|units?|quantity|stock|inventory|profit|margin|receipts?|transactions?)\b", q):
        reason = "Which performance measure would you like to review: sales/revenue, units sold, profit/margin, or stock levels?"
    if reason is None:
        return None
    return {"answer": reason, "values": {"status": status}, "source_rows": []}
