import re
from datetime import date, timedelta
from typing import Dict, Any, Optional
from app.core.config import get_default_domain
from app.schema.domain import get_domain_pack

# Module 6.6 supersedes the temporary in-module pandas fallback that used to live
# here: the numeric route is now backed by the real KPI engine, which computes
# every figure deterministically and returns it with provenance. Re-exported under
# the original name so existing call sites (app/rag/chat.py) are unchanged.
from app.analytics.seam import AnalyticsRouter  # noqa: F401  (public re-export)

class RouteType:
    RAG = "rag"
    ANALYTICS = "analytics"
    CHITCHAT = "chit-chat"

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12
}

def parse_date_token(token: str, default_year: int = 2026) -> Any:
    token = token.strip().lower()
    token = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', token)
    token = re.sub(r'[^\w\s\-]', '', token).strip()
    
    # ISO date: YYYY-MM-DD
    m_iso = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', token)
    if m_iso:
        return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
    
    # Day Month [Year] e.g. "20 jan", "20 jan 2026"
    m_dm = re.match(r'^(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?$', token)
    if m_dm:
        day = int(m_dm.group(1))
        m_name = m_dm.group(2)
        yr = int(m_dm.group(3)) if m_dm.group(3) else default_year
        if m_name in MONTHS:
            return f"{yr:04d}-{MONTHS[m_name]:02d}-{day:02d}"
            
    # Month Day [Year] e.g. "jan 20", "january 20 2026"
    m_md = re.match(r'^([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?$', token)
    if m_md:
        m_name = m_md.group(1)
        day = int(m_md.group(2))
        yr = int(m_md.group(3)) if m_md.group(3) else default_year
        if m_name in MONTHS:
            return f"{yr:04d}-{MONTHS[m_name]:02d}-{day:02d}"
    return None

def classify_route(question: str, last_route: Optional[str] = None) -> str:
    """
    Deterministic rule-based intent classification.
    Route to Analytics if question asks for numbers, aggregates, margins, counts.
    Route to Chit-chat if it's a greeting.
    Default to RAG (record lookup).
    """
    q_lower = question.lower()

    if re.search(
        r"\b(sales?|revenue|transactions?|sale|bikri)\b.*\b(last|past|previous|pichlay|pichle|guzishta|aakhri)\s+\d{1,3}\s+(days?|din)\b",
        q_lower,
    ):
        return RouteType.ANALYTICS

    # Keep concrete record retrieval separate from questions asking for a KPI.
    if (
        re.search(r"\b(batch|batches|invoice|invoices|bill|bills|receipt|receipts)\b\s+#?[a-z0-9_-]*\d+[a-z0-9_-]*", q_lower)
        or (
            re.search(r"\b(invoice|invoices|bill|bills|receipt|receipts|batch|batches|record|records|ledger)\b", q_lower)
            and re.search(r"\b(show|find|fetch|list|lookup|look up|search|retrieve|pull up|details|what happened|which|dikhao|dikhaye|dikhayen|batao|bata dein|talash|dhoondo|dhundo)\b", q_lower)
            and not re.search(r"\b(total|sum|average|avg|how much|how many|count|profit|margin|forecast|predict)\b", q_lower)
        )
    ):
        return RouteType.RAG

    analytics_language = (
        r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|p&l|"
        r"revenue|sales?|turnover|takings|earnings|income|purchases?|expenses?|spend|spent|costs?|"
        r"cash flow|refunds?|returns?|units sold|quantity sold|stock sold|most|highest|lowest|top|"
        r"kitna|kitni|kitne|kul|bikri|munafa|nafa|faida|nuqsan|kharcha|aamdani|kamai|"
        r"ziada|zyada|zayada|sab se|sabse|kam stock|dawai ki sale|dawa ki sale)\b"
    )
    has_analytics_language = bool(re.search(analytics_language, q_lower))

    data_description_question = (
        re.search(r"\b(dataset|file|data source|table|tables|columns?|fields?)\b", q_lower)
        and re.search(r"\b(which|what|list|show|tell|name|available|included)\b", q_lower)
        and not re.search(r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|profit|margin)\b", q_lower)
    )
    if data_description_question:
        return RouteType.RAG

    entity_lookup = (
        re.search(r"\b(which|who|what|kis|kaun|kon|kaunsi|konsi|kon si|kiska|kis ka)\b", q_lower)
        and re.search(r"\b(supplier|vendor|customer|cashier|product|medicine|item|invoice|bill|batch|dawai|dawa|tablet|goli)\b", q_lower)
        and not re.search(r"\b(total|sum|average|avg|count|most|highest|lowest|top|best|least|margin|profit|revenue|sales amount|kitna|kitni|kitne|ziada|zyada|sab se|sabse)\b", q_lower)
    )
    if entity_lookup:
        return RouteType.RAG
    
    # Chit-chat & assistant capability keywords
    chitchat_patterns = [
        r"^(hello|hi|hey|salam|assalam|aoa)\b",
        r"\bhow are you\b",
        r"\bhow r u\b",
        r"\bkese ho\b",
        r"\bkaisa hai\b",
        r"\bwhat can you do\b",
        r"\bwho are you\b",
        r"\bthank(s| you)?\b",
        r"\bshukriya\b",
        r"\bgood (morning|afternoon|evening|night)\b",
        r"\b(how fast|kitni taizi|kitna tez|kitni tez)\b",
        r"\b(kaam kr skte|kaam kar sakte|kaam kr sakty)\b",
        r"\b(kya kr skte|kya kar sakte|madad kr skte)\b",
        r"\b(tum kon ho|tm kon ho|aap kon hain|ap kon hain|who made you)\b",
        r"\b(english\s+me\s+(ku|kyu|kyun)|english\s+mein\s+(ku|kyu|kyun)|urdu\s+me\s+bolo|roman\s+urdu)\b",
        r"\b(why\s+in\s+english|speak\s+urdu|reply\s+in\s+urdu)\b",
        r"^(assalamualaikum|assalamu\s+alaikum|asalam\s+o\s+alaikum|wa\s+alaikum\s+(assalam|salam)|walaikum\s+salam|adaab)\b",
        r"\b(kya\s+ha{1,2}l\s+(hai|hain)|kaise\s+(ho|hain)|kaisay\s+(ho|hain)|theek\s+(ho|hun|hain))\b",
        r"\b(kya\s+aap\s+madad\s+kar\s+sakte|madad\s+kar\s+saktay\s+ho|madad\s+karogi)\b",
        r"\b(aap\s+kaun\s+hain|ap\s+kaun\s+hain|tumhara\s+naam\s+kya\s+hai)\b",
        r"\b(bahut\s+shukriya|bohat\s+shukriya|shukria|meherbani|mehrbani)\b",
        r"\b(madad\s+kar\s+(saktay|sakte|dain|dein)|meri\s+madad\s+karo)\b",
        r"\b(theek\s+hun|mein\s+theek\s+hun|main\s+theek\s+hun)\b",
        r"^(greetings|hiya|howdy|what's up|whats up|good day)\b",
        r"\b(how is it going|how's it going|nice to meet you|good to see you)\b",
        r"\b(can you help me|i need help|are you there|are you online)\b",
        r"\b(thanks a lot|many thanks|much appreciated|cheers)\b",
        r"\b(what do you do|what are your capabilities|how can you help)\b",
    ]
    for pattern in chitchat_patterns:
        if re.search(pattern, q_lower) and not has_analytics_language:
            return RouteType.CHITCHAT
            
    # Confirmation / verification follow-ups: keep the previous analytics route
    if last_route == RouteType.ANALYTICS:
        confirmation_patterns = [
            r"^(are you sure|are u sure|really\??|is that (right|correct)\??|is this (right|correct)\??|recheck|confirm\??|double check\??|pakka\??|sach me\??|sach\??)\b"
        ]
        for pattern in confirmation_patterns:
            if re.search(pattern, q_lower):
                return RouteType.ANALYTICS

    # Explicit batch/record lookup patterns (prioritized to RAG)
    if (
        re.search(r"\b(batch|batches|invoice|invoices|record|records)\b\s+#?[a-zA-Z0-9\-_]*\d+[a-zA-Z0-9\-_]*", q_lower)
        or re.search(r"^(list|show|fetch|find|display)\s+(all\s+)?(batches|records|files|data)\b", q_lower)
    ):
        return RouteType.RAG

    # Explicit listing / lookup / informational patterns (prioritized over incidental keyword matches)
    lookup_patterns = [
        r"^(list|show|display|find|fetch|search|retrieve|lookup|look up|locate|open|get me|pull up|which|what|tell)\b",
        r"\b(what|which|tell)\s+(me\s+)?(the\s+)?(dataset|file|data|source|sources|table|tables|medicine|product|item|name)\b",
        r"\bnames?\b",
        r"\blist all\b",
        r"\bshow all\b",
        r"\bdata\s*source\b",
    ]
    numeric_or_inventory_guard = (
        r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|revenue|sales?|"
        r"turnover|takings|earnings|income|purchases?|expenses?|spend|spent|costs?|cash flow|p&l|"
        r"units sold|quantity sold|stock sold|"
        r"expire|expiry|expired|expiring|expire ho|expire hone|expire ho chuk|expire ho gaya|"
        r"velocity|reorder|stockout|supply|days supply|days of supply|"
        r"running below|low stock|dead stock|liquidation|kam stock|"
        r"most|highest|lowest|max|min|top|best|least|qty|quantity|"
        r"sb se|sab se|sabse|sbse|ziada|zyada|zayada|sale hwi|sale hui|dawai ki sale|dawa ki sale|"
        r"kitna|kitni|kitne|kul|bikri|munafa|nafa|faida|nuqsan|kharcha|aamdani|kamai|"
        r"bechi|biki|bikay|bikain|hwi|hui|hua|huay|huye|tha|thi|the)\b"
    )
    for pattern in lookup_patterns:
        if re.search(pattern, q_lower) and not re.search(numeric_or_inventory_guard, q_lower):
            return RouteType.RAG

    # Analytics / Numeric / Inventory Intelligence keywords
    analytics_patterns = [
        r"\btotal\b", r"\bhow much\b", r"\bhow many\b", r"\bsum\b",
        r"\baverage\b", r"\bavg\b", r"\bkitna\b", r"\bkitne\b", r"\bprofit\b",
        r"\bmargin\b", r"\bexpiring\b", r"\bexpire\b", r"\bexpiry\b", r"\bexpired\b",
        r"\bcount\b", r"\bmehngi\b", r"\bsasti\b", r"\bexpensive\b", r"\bcheap\b",
        r"\bhighest\b", r"\blowest\b", r"\bmax\b", r"\bmin\b", r"\bmost\b", r"\btop\b", r"\bbest\b",
        r"\b(top|best|largest|highest)\s+(supplier|vendor|product|medicine)\b",
        r"\b(buy|bought|purchased?)\s+(the\s+)?(most|highest|least)\b",
        r"\b(sb se|sab se|sabse|sbse)\s+(ziada|zyada|zayada|bara|barri|kam)\b",
        r"\b(ziada|zyada|zayada)\s+sale\b",
        r"\b(sale|bikri)\s+kitni\s+(hui|hwi|thi|thee)\b",
        r"\b(sale|bikri|munafa|aamdani|profit|revenue)\b.*\b(pichlay|pichle|guzishta|aakhri)\s+\d+\s+(din|days?)\b",
        r"\b(pichlay|pichle|guzishta|aakhri)\s+\d+\s+(din|days?)\b.*\b(sale|bikri|munafa|aamdani|profit|revenue)\b",
        r"\bsale\s+(hwi|hui|ha)\b",
        r"\bdawai\s+ki\s+sale\b",
        r"\bdawa\s+ki\s+sale\b",
        r"\b(kul\s+bikri|bikri\s+(kitni|ziada)|munafa|nafa|faida|nuqsan|kharch\w*|aamdani|kamai)\b",
        r"\bkitni\b", r"\bqty\b", r"\bquantity\b", r"\bturnover\b", r"\btakings\b", r"\bearnings\b", r"\bloss\b",
        r"\bpurchases?\b", r"\bexpenses?\b", r"\bspend\b", r"\bcosts?\b", r"\bcash flow\b", r"\bp&l\b",
        r"\bcash position\b", r"\bcash balance\b",
        r"\b(sales?|income)\b",
        r"\bforecast\b", r"\bpredict\b", r"\btrend\b", r"\bgrowth\b",
        r"\brevenue\b", r"\bbreakdown\b", r"\btotal sales\b", r"\bsales amount\b", r"\bsales total\b",
        r"\brow count\b", r"\bdataset size\b", r"\bnumber of rows\b", r"\bnumber of records\b",
        r"\bvelocity\b", r"\breorder\b", r"\bstockout\b", r"\bliquidat(e|ion)\b",
        r"\b(day|days) supply\b", r"\b(day|days) of supply\b", r"\brunning below\b",
        r"\blow stock\b", r"\bkam stock\b", r"\bdead stock\b", r"\bshort expiry\b",
        r"\bnear expiry\b", r"\bnear-expiry\b", r"\bkhatam hone\b", r"\bstock khatam\b",
        # Roman Urdu expiry / batch phrases
        r"\bexpire ho chuk", r"\bexpire ho gaya\b", r"\bexpire ho raha\b",
        r"\bexpire hone wali\b", r"\bexpire hone\b",
        r"\bbatch expire\b", r"\bmiyad\b", r"\bmeyad\b",
        r"\banomal(y|ies)\b", r"\boutlier(s)?\b", r"\bduplicate invoice(s)?\b",
        r"\bunusual spike(s)?\b", r"\btransaction spike(s)?\b", r"\babnormal refund\b",
        r"\bfraud\b", r"\birregularit(y|ies)\b"
    ]
    for pattern in analytics_patterns:
        if re.search(pattern, q_lower):
            return RouteType.ANALYTICS
            
    return RouteType.RAG

def extract_filters(question: str, domain: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract exact-match filters, date intervals, and inventory threshold options from the question.
    """
    effective_domain = domain or get_default_domain()
    filters: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    q_lower = question.lower()
    
    # 1. Date range detection e.g. "from 20 jan to 15 feb", "between 1st jan and 31st march", "from 2026-01-20 to 2026-02-15"
    date_token_pattern = r'(\d{1,2}(?:st|nd|rd|th)?\s+[a-zA-Z]+(?:\s+\d{4})?|[a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{4})?|\d{4}-\d{2}-\d{2})'
    range_pattern = re.compile(
        rf'(?:from|between|since)\s+{date_token_pattern}\s+(?:to|till|until|and|-)\s+{date_token_pattern}',
        re.IGNORECASE
    )
    m_range = range_pattern.search(q_lower)
    if m_range:
        d1 = parse_date_token(m_range.group(1))
        d2 = parse_date_token(m_range.group(2))
        if d1 and d2:
            filters["date_from"] = d1
            filters["date_to"] = d2
            return filters

    # Yesterday is a calendar date filter. A misspelling is corrected in
    # RAGChat._normalize_question, and also accepted here for direct callers.
    if re.search(r"\b(yesterday|yestarday)\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters
    # In Roman Urdu, past-tense sale wording disambiguates "kal" as yesterday.
    if re.search(r"\bkal\b", q_lower) and re.search(
        r"\b(sale|sales|bikri|biki|bechi)\b.*\b(hui|hwi|thi|thee|biki|bechi)\b",
        q_lower,
    ):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters

    # Relative reporting windows are resolved against the newest date in the
    # selected dataset by RAGChat, so stale datasets don't silently use today's
    # date as if they contained current records.
    m_recent_days = re.search(
        r"\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\s+(\d{1,3})\s+(?:days?|din)\b",
        q_lower,
    )
    if m_recent_days:
        filters["relative_days"] = int(m_recent_days.group(1))

    # 2. Single month detection if no range
    for m_name, m_num in MONTHS.items():
        if re.search(rf"\b{m_name}\b", q_lower):
            filters["month"] = m_num
            break

    # 3. Year detection (e.g. 2024, 2025, 2026, 2027)
    m_yr = re.search(r'\b(202[0-9])\b', q_lower)
    if m_yr:
        filters["year"] = int(m_yr.group(1))

    # 4. Expiry horizon extraction (e.g., "next 60 days", "in 30 days", "60 days", "60 din")
    m_exp_days = re.search(r'(\d{1,3})\s*(?:day|days|din|d)\b', q_lower)
    if m_exp_days and re.search(r'\b(expir|expire|expired|expiring|expiry|near|short|miyad|meyad|liquidat)\b', q_lower):
        options["expiry_days"] = int(m_exp_days.group(1))
        options["horizon_days"] = int(m_exp_days.group(1))

    # 5. Days-of-supply threshold extraction (e.g., "below a 3-day supply", "3 days supply", "3-day supply", "< 3 days")
    m_supply_days = re.search(r'\b(?:below|under|<|less than)?\s*(?:a\s*)?(\d{1,2})(?:-|\s*)(?:day|days|din)\s*(?:of\s*)?supply\b', q_lower)
    if m_supply_days:
        options["days_supply_threshold"] = float(m_supply_days.group(1))
    elif re.search(r'\b(?:below|under|<)\s*(\d{1,2})\s*(?:day|days|din)\b', q_lower):
        m_simple = re.search(r'\b(?:below|under|<)\s*(\d{1,2})\s*(?:day|days|din)\b', q_lower)
        if m_simple:
            options["days_supply_threshold"] = float(m_simple.group(1))

    # 6. Therapeutic category extraction (e.g. cardiac, heart, diabetes, antibiotics, analgesic)
    cat_keywords = {
        "cardiac": ["cardiac", "cardio", "heart", "hypertension", "bp", "blood pressure"],
        "diabetes": ["diabetes", "diabetic", "sugar", "insulin"],
        "antibiotic": ["antibiotic", "antibiotics", "infection", "anti-infective"],
        "analgesic": ["analgesic", "pain", "painkiller"],
        "respiratory": ["respiratory", "asthma"],
        "gastro": ["gastro", "antacid", "stomach"],
    }
    for cat_name, kw_list in cat_keywords.items():
        if any(re.search(rf"\b{kw}\b", q_lower) for kw in kw_list):
            filters["category"] = cat_name
            options["category"] = cat_name
            break

    if options:
        filters["options"] = options

    return filters
