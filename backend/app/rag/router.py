import re
from datetime import date, timedelta
from typing import Dict, Any, Optional
from app.rag.intent import is_dataset_question_suggestion_request, is_knowledge_question, is_procedural_how_to_question
from app.core.config import get_default_domain
from app.schema.domain import get_domain_pack
from app.language.roman_urdu import normalize_roman_urdu_intent
from app.language.pharmacy_vocabulary import normalize_pharmacy_vocabulary, match_pharmacy_concepts

# Module 6.6 supersedes the temporary in-module pandas fallback that used to live
# here: the numeric route is now backed by the real KPI engine, which computes
# every figure deterministically and returns it with provenance. Re-exported under
# the original name so existing call sites (app/rag/chat.py) are unchanged.
from app.analytics.seam import AnalyticsRouter  # noqa: F401  (public re-export)

class RouteType:
    RAG = "rag"
    ANALYTICS = "analytics"
    CHITCHAT = "chit-chat"


def is_advice_question(question: str) -> bool:
    """Recognize requests for a decision or recommendation, not a KPI value."""
    q = question.casefold().strip()
    action = r"(increase|improve|grow|boost|raise|expand|reduce|lower|cut|manage|attract|retain|promote|optimi[sz]e|fix|prevent|reorder|order|restock|purchase|stock)"
    business_goal = r"(sales?|revenue|profit|margin|cash\s*flow|stock|inventory|business|customers?|medicine|products?)"
    return bool(
        re.search(r"\b(recommend(?:ation|ations)?|suggest(?:ion|ions)?|advice|strategy|strategies|what should i|what should we|ways to)\b", q)
        or re.search(r"\b(which|what)\s+(?:medicines?|products?|items?)\b.{0,100}\b(?:should\s+(?:i|we)|karun|karein|chahiye)\b", q)
        or re.search(r"\bwhat\s+(?:changes|actions|steps|problems|are the biggest problems|biggest problems)\b.{0,140}\b(?:should\s+(?:i|we)|affecting|affect|doing|karun|karein|chahiye)\b", q)
        or re.search(r"\b(?:not keeping enough|too much stock|overstocked|slow[- ]moving|about to expire|high[- ]demand)\b", q)
        or (
            re.search(r"\bhow\s+(?:can|could|do)\s+(?:i|we|my\s+store|our\s+store)\b", q)
            and re.search(rf"\b{action}\b", q)
        )
        or (
            re.search(rf"\b{action}\b", q)
            and re.search(rf"\b{business_goal}\b", q)
            and re.search(r"\b(my|our|the|store|business|pharmacy)\b", q)
        )
        or (re.search(r"\b(karun|karein|chahiye)\b", q) and re.search(rf"\b{business_goal}\b", q))
        or (re.search(r"\b(tabdeeli|tabdeelian)\b", q) and re.search(r"\b(inventory|stock|sales?|profit|karobar|pharmacy)\b", q))
        or (re.search(r"\b(affect|affecting)\b", q) and re.search(r"\b(sales?|profit|karobar|pharmacy|maslay|problems?)\b", q))
    )

MONTH_ALIASES = {
    1: ["january", "jan", "januray", "janury", "janurary", "jann", "janwari", "janwary", "janwry"],
    2: ["february", "feb", "febuary", "februrary", "febrary", "feburary", "feburay", "farwari", "febwari"],
    3: ["march", "mar", "marchh", "marc", "maarch", "maarc"],
    4: ["april", "apr", "aprl", "appril", "aprail", "aprel"],
    5: ["may", "mai", "maey"],
    6: ["june", "jun", "junn", "joon"],
    7: ["july", "jul", "julyy", "julai", "joolai"],
    8: ["august", "aug", "agust", "augest", "agst", "agast", "augst"],
    9: ["september", "sep", "sept", "septembr", "sepember", "septembar", "septm", "sitambar", "sitambr", "septemba"],
    10: ["october", "oct", "octomber", "octobr", "octb", "aktubar", "aktoobar", "octoba"],
    11: ["november", "nov", "novmber", "novembar", "novm", "navambar", "navambr", "novemba"],
    12: ["december", "dec", "decembr", "decembar", "decm", "disambar", "disambr", "decemba"],
}

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12
}

NON_MONTH_WORDS = {
    "day", "days", "din", "dino", "month", "months", "mahina", "mahine", "mahiney",
    "year", "years", "saal", "week", "weeks", "hafte", "hafta", "sale", "sales",
    "tareekh", "tarikh", "tareeq", "tarekh", "date", "dates", "item", "items",
    "unit", "units", "bill", "bills", "order", "orders", "rupee", "rupees", "pkr", "rs"
}

def parse_month_token(word: Optional[str]) -> Optional[int]:
    """Extract month number (1-12) from word with typo-tolerance and phonetic aliases."""
    if not word:
        return None
    import difflib
    w = word.strip().lower()
    w = re.sub(r"[^a-z]", "", w)
    if not w or w in NON_MONTH_WORDS:
        return None
    for m_num, aliases in MONTH_ALIASES.items():
        if w in aliases:
            return m_num
    if len(w) >= 4:
        all_aliases = [alias for aliases in MONTH_ALIASES.values() for alias in aliases if len(alias) >= 4]
        candidates = difflib.get_close_matches(w, all_aliases, n=1, cutoff=0.78)
        if candidates:
            matched_alias = candidates[0]
            for m_num, aliases in MONTH_ALIASES.items():
                if matched_alias in aliases:
                    return m_num
    return None

def parse_date_token(token: str, default_year: int = 2026) -> Any:
    """Parse any date string (ISO, DMY, MDY, conversational, with typos)."""
    token = token.strip().lower()
    token = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', token)
    token = re.sub(r'[^\w\s\-\/\.]', '', token).strip()
    
    # ISO date: YYYY-MM-DD or YYYY/MM/DD
    m_iso = re.match(r'^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$', token)
    if m_iso:
        yr, mo, dy = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            return f"{yr:04d}-{mo:02d}-{dy:02d}"

    # Formatted numeric date: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    m_dmy = re.match(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$', token)
    if m_dmy:
        d, m, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if m > 12 and d <= 12:
            d, m = m, d
        if 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"
    
    # Day Month [Year] e.g. "20 jan", "20 jan 2026", "15 septembr", "15th of sep 2024"
    m_dm = re.match(r'^(\d{1,2})(?:\s+of|\s+tarikh|\s+tareekh|\s+tareeq)?\s+([a-z]+)(?:\s+(\d{4}))?$', token)
    if m_dm:
        day = int(m_dm.group(1))
        m_num = parse_month_token(m_dm.group(2))
        yr = int(m_dm.group(3)) if m_dm.group(3) else default_year
        if m_num and 1 <= day <= 31:
            return f"{yr:04d}-{m_num:02d}-{day:02d}"
            
    # Month Day [Year] e.g. "jan 20", "january 20 2026", "septembr 15 2024"
    m_md = re.match(r'^([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?$', token)
    if m_md:
        m_num = parse_month_token(m_md.group(1))
        day = int(m_md.group(2))
        yr = int(m_md.group(3)) if m_md.group(3) else default_year
        if m_num and 1 <= day <= 31:
            return f"{yr:04d}-{m_num:02d}-{day:02d}"
    return None

def classify_route(question: str, last_route: Optional[str] = None) -> str:
    """
    Deterministic rule-based intent classification.
    Route to Chit-chat if it's a greeting or casual capability/identity question.
    Route to Analytics if question asks for numbers, aggregates, margins, counts.
    Default to RAG (record lookup).
    """
    # Example-question requests ask for grounded retrieval guidance, even when
    # they include a number such as "give me 4 questions". They are not KPI asks.
    if is_dataset_question_suggestion_request(question):
        return RouteType.RAG
    if is_procedural_how_to_question(question):
        return RouteType.RAG

    # An exact invoice key plus a numeric tax field is a bounded analytics
    # lookup even when the wording is phrased as "was any tax recorded".
    exact_invoice_tax_lookup = (
        re.search(r"\b(?:invoice|receipt|bill)\s*(?:no\.?|number|#)?\s*[:#-]?\s*[a-z0-9/-]*\d[a-z0-9/-]*\b", question, re.I)
        and re.search(r"\b(tax|gst|vat)\b", question, re.I)
        and re.search(r"\b(recorded|charged|amount|value|how much|any)\b", question, re.I)
    )
    if exact_invoice_tax_lookup:
        return RouteType.ANALYTICS

    # Whole-scope on-hand calculations need structured inventory rows even
    # when the wording resembles a descriptive lookup request.
    early_question = normalize_pharmacy_vocabulary(question or "").casefold()
    if (
        re.search(r"\b(total|combined|sum|overall)\b", early_question)
        and re.search(r"\b(stock|inventory|on[- ]hand|quantity|units?)\b", early_question)
        and re.search(r"\b(across|all|selected|product|item|sku)\b", early_question)
    ):
        return RouteType.ANALYTICS

    # Normalize pharmacy shorthand before applying the distinct analytics vs
    # record-lookup rules. Keep the original for language-sensitive patterns.
    if is_knowledge_question(question):
        return RouteType.RAG

    normalized_question = normalize_pharmacy_vocabulary(question or "")
    original_lower = normalized_question.casefold()
    q_lower = normalize_roman_urdu_intent(normalized_question).casefold()

    # Questions about what can be asked are grounded in the active dataset:
    # derive examples from its retrieved schema/records instead of generic chat.
    if re.search(
        r"\bwhat (?:type|kind|kinds) of questions? can you answer\b|"
        r"\bwhat questions? can you answer\b|\bwhat can you help (?:me )?with\b|"
        r"\b(?:give|suggest|list|show) me (?:(?:some|a few|example|sample) )?questions?\b.{0,100}\b(?:ask|related to|about|based on|from)\b",
        q_lower,
    ):
        return RouteType.RAG

    clean_q = re.sub(r"\b(sales?\s+records?|sales?\s+invoices?|sales?\s+bills?|sales?\s+receipts?|all\s+sales\s+records)\b", "record", q_lower)
    clean_orig = re.sub(r"\b(sales?\s+records?|sales?\s+invoices?|sales?\s+bills?|sales?\s+receipts?|all\s+sales\s+records)\b", "record", original_lower)

    analytics_language = (
        r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|discount|gst|tax|p&l|"
        r"revenue|sales?|turnover|takings|earnings|income|purchases?|expenses?|spend|spent|costs?|"
        r"cash flow|refunds?|returns?|units sold|quantity sold|stock sold|most|highest|lowest|top|trend|performance|"
        r"more expensive|cheaper|price change|price trend|cost trend|increas\w*|decreas\w*|savings?|save costs?|dependen\w*|"
        r"kitna|kitni|kitne|kul|bikri|bikree|farukht|farokht|frokt|frokht|munafa|nafa|faida|nuqsan|nuksan|kharcha|aamdani|kamai|"
        r"udhar|udhari|naqad|naqd|rokra|baqaya|rasid|raseed|parchi|hisab|hisaab|khata|khaata|wasooli|bachat|khasara|laagat|lagat|adaigi|"
        r"ziada|zyada|zayada|sab se|sabse|kam stock|dawai ki sale|dawa ki sale)\b"
    )
    has_analytics_language = bool(re.search(analytics_language, clean_q) or re.search(analytics_language, clean_orig))

    # Historical price movement and savings questions are data calculations,
    # even when phrasing contains no conventional KPI noun or comparison word.
    if re.search(r"\b(over time|histor(?:y|ical)|price movement|became? more expensive|got more expensive|cheaper|increas\w*|decreas\w*)\b", q_lower) and re.search(r"\b(price|cost|expensive|purchase|supplier|product)\b", q_lower):
        return RouteType.ANALYTICS

    # A direct inquiry for a named product price (e.g. "Panadol ki qeemat batao", "Price of Panadol")
    if re.search(r"\b(qeemat|keemat|kimat|price|rate)\b", q_lower) and not re.search(r"\b(average|avg|total|highest|lowest|margin|cost|profit|trend|breakdown|purchase|purchases|purchasing|sale|sales|sold|last time|pichli baar|kab)\b", q_lower):
        if re.search(r"\b(panadol|brufen|disprin|augmentin|arinate|rigix|cardivas|calamox|dawa|dawai|medicine|product)\b", q_lower):
            return RouteType.RAG

    # Full-table inventory operations need the structured planner even when
    # the wording is an item/location request rather than a KPI noun.
    if re.search(r"\b(below|under|equal to|exactly equal to)\b.{0,35}\breorder\b|\b(each|every|per) warehouse\b.{0,60}\b(below|reorder|stock)\b|\bexpire\w* between\b", q_lower):
        return RouteType.ANALYTICS
    # A single explicit record key denotes a bounded row lookup even when the
    # question contains record identifiers. Metric operations such as invoice
    # totals and discounts still use analytics while preserving that exact ID.
    #
    # Accept both compact and conversational forms: INV-123 and "invoice 123".
    record_ids = re.findall(
        r"\b(?:sale|sales|pur|purchase|inv|invoice|bill|receipt|inventory|sku|batch|lot|rx)(?:[-_/]|\s+(?:number|no\.?|#)?\s*)[a-z0-9-]*\d[a-z0-9-]*\b",
        clean_q,
    )
    if len(set(record_ids)) == 1:
        return RouteType.ANALYTICS if has_analytics_language else RouteType.RAG

    if re.search(r"\b(list all|show me all)\s+sales?\s+records?\b", q_lower):
        return RouteType.RAG

    if re.search(r"\b(lead[- ]time|delivery time|days? to deliver|take to deliver|how long.{0,40}(?:take|deliver|arrive))\b", q_lower):
        return RouteType.ANALYTICS
    if re.search(r"\b(suppliers?|vendors?|distributors?)\b", q_lower) and re.search(r"\b(bonus|free quantity|free units?)\b", q_lower):
        return RouteType.ANALYTICS
    if re.search(r"\b(same one|same item|it|that one|those|them)\b", q_lower) and re.search(r"\b(how many|how much|left|remaining|stock)\b", q_lower):
        return RouteType.ANALYTICS

    # Full-table inventory operations need the structured planner even when
    # the wording is an item/location request rather than a KPI noun. Apply
    # after exact-ID handling so "find lot B-99" remains a bounded lookup.
    if re.search(r"\b(below|under|equal to|exactly equal to)\b.{0,35}\breorder\b|\b(each|every|per) warehouse\b.{0,60}\b(below|reorder|stock)\b|\bexpire\w* between\b", q_lower):
        return RouteType.ANALYTICS
    if re.search(r"\b(batch|batches|lot|lots|rack|shelf|shelves|stock|inventory)\b", q_lower) and re.search(r"\b(show|list|find|which|where|how many|count|expire|expiry|reorder|stock)\b", q_lower):
        return RouteType.ANALYTICS

    # Chit-chat & assistant capability keywords (checked when not asking for metric data or specific entities)
    chitchat_patterns = [
        r"^(hello|hi|hey|salam|assalam|aoa)[\s,!.]*$",
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
        r"\b(aap ka|apka|tumhara|tmhara)\s+(?:kya\s+)?naam\b",
        r"\b(what is your name|whats your name)\b",
        r"\b(kya kaam krte|kya kaam karte)\b",
        r"\b(english\s+me\s+(ku|kyu|kyun)|english\s+mein\s+(ku|kyu|kyun)|urdu\s+me\s+bolo|roman\s+urdu)\b",
        r"\b(kya|tum|aap)\b.{0,60}\b(urdu|roman\s+urdu)\b.{0,60}\b(baat|bol|reply|sakte|skte|kar)\b",
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
    has_domain_entity = bool(re.search(r"\b(supplier|vendor|customer|cashier|product|medicine|item|invoice|bill|batch|dawai|dawa|tablet|goli|panadol|records?)\b", q_lower))
    if not has_analytics_language and not has_domain_entity:
        if any(re.search(p, q_lower) for p in chitchat_patterns) or any(re.search(p, original_lower) for p in chitchat_patterns):
            return RouteType.CHITCHAT

    # Location lookups are structured inventory facts and should use the full
    # selected table, not an embedding-ranked handful of product variants.
    if (re.search(r"\b(racks?|shelves|shelf|bins?|stock locations?)\b", q_lower) and re.search(r"\b(where|which|located|stored|holds?|kept)\b", q_lower)) or re.search(r"\bwhere\b.{0,80}\b(stored|located|kept|placed)\b", q_lower):
        return RouteType.ANALYTICS

    # Structured business measurements should not fall through to semantic
    # top-k retrieval just because an entity such as branch, pharmacist, batch,
    # or payment method is the grammatical subject. This vocabulary-based
    # guard is deliberately general across file and SQL schemas.
    structured_subject = re.search(
        r"\b(transactions?|sales?|revenue|amount|value|units?|quantity|products?|medicines?|drugs?|"
        r"therapeutic classes?|manufacturers?|branches?|months?|days?|prices?|cost|margin|profit|"
        r"prescriptions?|otc|payment methods?|cash|insurance|pharmacists?|doctors?|batches?|lots?|"
        r"racks?|shelves|locations?)\b", q_lower,
    )
    structured_operation = re.search(
        r"\b(total|sum|average|avg|how much|how many|count|number of|most|highest|lowest|top|bottom|"
        r"fewest|least|largest|smallest|compare|trend|frequently|often|each|per|by|revenue|sales|"
        r"price|cost|margins?|profits?|percentage|percent|share|value|sold|selling|stored|associated|generated)\b", q_lower,
    )
    unspecified_entity = re.search(r"\b(particular|specific|this|that)\s+(?:medicine|product|drug|batch|rack)\b", q_lower)
    if not is_advice_question(q_lower) and ((structured_subject and structured_operation) or unspecified_entity):
        return RouteType.ANALYTICS

    # Prefer the operation asked for over a broad noun. Owner questions about
    # policy, workflow, customers, staff, and medicine attributes are record or
    # knowledge lookups even when words like "stock", "medicine", or "how" occur.
    concepts = set(match_pharmacy_concepts(normalized_question))
    named_stock_count = re.search(
        r"\bhow many\s+(?:(?:units|tablets|packs|doses)\s+of\s+)?([a-z][a-z0-9-]+)", q_lower
    )
    generic_subjects = {"medicine", "medicines", "drugs", "products", "items", "batches", "units", "tablets", "packs"}
    direct_product_stock = (
        named_stock_count and named_stock_count.group(1) not in generic_subjects
        and re.search(r"\b(left|remaining|available|on hand|in stock)\b", q_lower)
    ) or re.search(r"\bleft of (?:plain )?(?!the\b|stock\b|medicine\b|product\b|item\b)([a-z][a-z0-9-]+)", q_lower)
    if re.search(r"\b(total stock|combined stock)\b",q_lower):
        return RouteType.ANALYTICS
    if direct_product_stock:
        if re.search(r"\b(total stock|combined stock|across|warehouse|location)\b", q_lower):
            return RouteType.ANALYTICS
        return RouteType.RAG
    record_ids = re.findall(r"\b(?:sale|sales|pur|purchase|inv|inventory|rx|sku)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b", q_lower)
    if len(set(record_ids)) > 1 and re.search(r"\b(total|combined|sum|value|compare|across|stock|cost)\b", q_lower):
        return RouteType.ANALYTICS
    if re.search(r"\b(total|combined|sum|how many)\b.{0,35}\b(quantity|units?|purchases?|sales?)\b", q_lower) and re.search(r"\b(bought|purchased|sold|did they|did he|did she|did we|did i)\b", q_lower):
        return RouteType.ANALYTICS
    if re.search(r"\b(lead time|delivery time|days? to deliver|how long.{0,40}(?:take|deliver|arrive)|how many days.*take.*deliver)\b", q_lower):
        return RouteType.ANALYTICS
    if re.search(r"\b[a-z]{1,8}-\d{2,}[a-z0-9-]*\b", q_lower) and re.search(r"\b(batch|expiry|expires?|stock)\b", q_lower):
        return RouteType.RAG
    if len(set(record_ids)) == 1 and not re.search(r"\b(compare|versus|\bvs\b|difference|combined value|total stock across|sum of)\b", q_lower):
        return RouteType.RAG
    if re.search(r"\bcustomer\s+[a-z][a-z'-]+\s+[a-z][a-z'-]+\b", q_lower) and re.search(r"\b(list every|all (?:sales|purchases)|what products?|what did .* (?:buy|purchase))\b", q_lower) and not re.search(r"\b(total|sum|average|avg|how much)\b", q_lower):
        # Full customer history is a filtered table query; vector top-k can omit
        # transactions and must not be used to answer a complete-list request.
        return RouteType.ANALYTICS
    if (
        named_stock_count and named_stock_count.group(1) not in generic_subjects
        and re.search(r"\b(left|remaining|available|on hand|in stock)\b", q_lower)
        and not re.search(r"\b(compare|versus|vs|highest|lowest|top|total stock|all products)\b", q_lower)
    ):
        return RouteType.RAG
    if re.search(r"\b(batch number|batch no|lot number)\s+(?:of|for)\b", q_lower):
        return RouteType.RAG
    if re.search(r"\bwhere is\b.{0,40}\b(stored|located|kept|placed|rack)\b", q_lower) or re.search(r"\b(which|what)\s+(?:rack|shelf|warehouse)\b", q_lower):
        return RouteType.RAG
    if concepts:
        if re.search(r"\b(batch|lot)\b", q_lower) and re.search(r"\b(which|what|when|expires?|expire|expir(?:y|ing))\b",q_lower) and not re.search(r"\b[A-Z]{1,8}-\d+\b",question or "",re.I):
            return RouteType.ANALYTICS
        if re.search(r"\b(which|list|show|all|every|how many|count|more items?)\b", q_lower) and re.search(r"\b(out of stock|below (?:(?:the|their) )?reorder|under (?:(?:the|their) )?reorder|expires? on or before|items? expire)", q_lower):
            return RouteType.ANALYTICS
        # Exact record identifiers point to a bounded source lookup. Do not let
        # generic words such as "expiry", "how many", or "invoice total"
        # redirect a batch/invoice lookup to an unrelated whole-ledger KPI.
        exact_record_id = re.search(
            r"\b(?:batch|lot|invoice|inv|bill|receipt|prescription|rx)\s*(?:no\.?|number|#)?\s*[:#-]?\s*[a-z0-9/-]*\d[a-z0-9/-]*\b",
            q_lower,
        )
        if exact_record_id:
            return RouteType.RAG

        # A named product's direct on-hand question is a record lookup. Broad
        # low-stock lists and comparisons still use full-dataset analytics.
        named_stock_count = re.search(
            r"\bhow many\s+(?:(?:units|tablets|packs|doses)\s+of\s+)?([a-z][a-z0-9-]+)", q_lower
        )
        generic_subjects = {"medicine", "medicines", "drugs", "products", "items", "batches", "units", "tablets", "packs"}
        if (
            "inventory.on_hand" in concepts and named_stock_count
            and named_stock_count.group(1) not in generic_subjects
            and re.search(r"\b(left|remaining|available|on hand|in stock)\b", q_lower)
            and not re.search(r"\b(compare|versus|vs|highest|lowest|top|total stock|all products)\b", q_lower)
        ):
            return RouteType.RAG

        metric_operator = re.search(
            r"\b(total|sum|average|avg|how much|how many|count|most|highest|lowest|top|best|least|trend|compare|cheapest|fastest|slowest|daily|report|rising|increasing|decreasing|falling|expir(?:y|es|ing)?|expires?)\b|\b(?:more|most|higher|highest)\s+stock\b",
            q_lower,
        )
        # Explicitly requested source records take precedence over a metric noun
        # such as "sales" in "show the sales records for invoice 43821".
        explicit_record_list = re.search(
            r"\b(show|find|fetch|look up|lookup|search|retrieve|pull up|list|open)\b.{0,80}\b(invoice|invoices|record|records|rows?|receipts?)\b",
            q_lower,
        )
        named_invoice = re.search(r"\b(invoice|bill|receipt)\s*(?:no\.?|number|#)?\s*[:#-]?\s*[a-z0-9/-]*\d", q_lower)
        named_batch_supplier = re.search(r"\b(supplier|vendor)\b.{0,50}\b(batch|lot)\s+[a-z0-9-]+", q_lower)
        catalog_question = re.search(r"\bwhat products? do i sell\b", q_lower)
        if named_batch_supplier or ((explicit_record_list or named_invoice or catalog_question) and not metric_operator):
            return RouteType.RAG
    if re.search(r"\b(conflict|conflicting|disagree|different reports?|report\s+[a-z]\s+says?)\b", q_lower):
        return RouteType.RAG

    # Questions choosing what to replenish need joined demand and stock facts,
    # even when they omit KPI words such as "sales velocity".
    if re.search(r"\b(what|which)\b.{0,45}\b(restock|reorder|re-stock|re-order)\b.{0,35}\b(first|next|now|priorit(?:y|ies))\b|\bshould i\s+(?:restock|reorder|re-stock|re-order)\b", q_lower):
        return RouteType.ANALYTICS

        # Product attributes remain lookups unless the owner asks to compare or
        # aggregate them (e.g. strength is context in a price/stock comparison).
        if any(c.startswith("medicine.") for c in concepts) and metric_operator and (
            concepts & {"purchasing.price", "inventory.on_hand", "inventory.low", "inventory.out", "inventory.stockout_risk"}
            or re.search(r"\b(compare|most|highest|lowest|top|best|least|trend)\b", q_lower)
        ):
            return RouteType.ANALYTICS
        if any(c.startswith("medicine.") for c in concepts) and metric_operator and concepts & {
            "finance.sales", "finance.margin", "finance.profit", "finance.product_performance",
            "finance.purchases", "finance.invoice_total",
        }:
            return RouteType.ANALYTICS
        if any(c.startswith(("compliance.", "customer.", "operations.", "medicine.")) for c in concepts):
            # A daily report is a computed view when its requested content is a
            # measurable KPI; workflow and audit reports remain document lookups.
            if not ("operations.report" in concepts and "finance.sales" in concepts and metric_operator):
                return RouteType.RAG
        if "inventory.batch" in concepts and not re.search(
            r"\b(expir(?:y|es|ing)?|expires?|earliest|latest|how many|count|total|multiple|active|which|what)\b", q_lower
        ):
            return RouteType.RAG
        if "inventory.damage" in concepts or "inventory.adjustment" in concepts:
            return RouteType.RAG
        if "inventory.return" in concepts and not re.search(r"\b(how many|how much|total|count|sum)\b", q_lower):
            return RouteType.RAG
        if "purchasing.shortage" in concepts or "purchasing.alternative" in concepts:
            return RouteType.RAG
        if "purchasing.po" in concepts and not re.search(r"\b(total|sum|how many|how much|count)\b", q_lower):
            return RouteType.RAG
        if "purchasing.invoice" in concepts and not re.search(r"\b(total|sum|how many|how much|count|purchases last)\b", q_lower):
            return RouteType.RAG
        if "purchasing.price" in concepts:
            if re.search(r"\b(cheapest|lowest|highest|most|compare|price[s]? for|how much|rank|best price|increase|increasing|decrease|decreasing|rise|rising|falling|trend|barh|izafa|ziada)\b", q_lower):
                return RouteType.ANALYTICS
            return RouteType.RAG
        if "finance.refund" in concepts and re.search(r"\b(list|show|find|which|pull up)\b", q_lower):
            return RouteType.RAG
        if concepts & {
            "inventory.on_hand", "inventory.low", "inventory.out", "inventory.expiry",
            "inventory.reorder_recommendation", "inventory.stockout_risk",
            "inventory.return", "finance.sales", "finance.margin", "finance.profit",
            "finance.purchases", "finance.discount", "finance.refund", "finance.tax", "finance.cash_flow",
            "finance.product_performance", "purchasing.payment", "purchasing.lead_time",
        }:
            return RouteType.ANALYTICS
        if "inventory.reorder_point" in concepts or "purchasing.price" in concepts or "purchasing.invoice" in concepts or "purchasing.po" in concepts:
            return RouteType.RAG

    # A request for a course of action is not a request for a sales/profit
    # total. Route it to grounded language generation before broad POS metric
    # vocabulary such as "sales" can claim it.
    if is_advice_question(q_lower):
        # Pharmacy owner recommendations need full-table calculations (demand,
        # current quantity, expiry, and cost) rather than top-k text retrieval.
        if re.search(r"\b(sales?|revenue|profit|profitabilit(?:y|ies)|margin|stock|inventory|medicine|medicines|product|products|expiry|expire|pharmacy|business|karobar|amadni|kamai|performance)\b", q_lower):
            return RouteType.ANALYTICS
        return RouteType.RAG

    # Explicit record retrieval should remain semantic RAG. The POS analytics
    # vocabulary below is intentionally broad, so protect invoice/batch lookups
    # and simple entity questions before applying it.
    if (
        (re.search(r"\b(show|find|fetch|look up|lookup|search|retrieve|pull up|list)\b", q_lower) and re.search(
            r"\b(invoice|invoices|bill|bills|receipt|receipts|record|records|row|rows|godown|godowns|location|locations|warehouse|warehouses)\b", q_lower
        ))
        or re.search(r"\b(kis bill|which bill|konsay bill|kon se bill|kaun se bill|kis invoice|which invoice)\s+(?:mein|in)\b", q_lower)
    ) and not re.search(r"\b(total|sum|average|avg|how much|how many|count|highest|lowest|largest|smallest|most|top 10|top 5|top|profit|margin|revenue|sales amount|forecast|predict|expiry|expire|expired|expiring|stock|inventory|deliver|delivery|fastest|reliable|stop purchasing|reorder|restock|frequency|frequently|occur|occurs|batch|batches)\b", q_lower):
        return RouteType.RAG
    if re.search(r"\b(which|who|what|kis)\s+(supplier|vendor|customer|cashier)\b", q_lower) and not re.search(
        r"\b(top|most|highest|lowest|cheapest|amount|value|purchase value|how much|count|total|recently|"
        r"fastest|fast|deliver|delivery|reliable|stop|avoid|supplied|supply|last|order|milti|li thi|aya tha|se li)\b", q_lower
    ):
        return RouteType.RAG

    # Named-record questions should retrieve the record, while questions that
    # ask for a total/rank/forecast should use deterministic analytics. Keep an
    # incidental word such as "sold" or "purchases" from hijacking a specific
    # bill lookup or a named customer's purchase-history question.
    exact_record = re.search(
        r"\b(?:invoice|bill|receipt)\s*(?:no\.?|number|#)?\s*[:#-]?\s*[a-z0-9/-]*\d[a-z0-9/-]*\b",
        q_lower,
    )
    aggregate_ask = re.search(
        r"\b(total|sum|average|avg|how much|how many|count|highest|lowest|largest|smallest|top|profit|margin|"
        r"revenue|forecast|predict|percentage|percent|compare|trend|most|least|frequently|frequency|increase|decrease|last|when)\b", q_lower,
    )
    exact_record_value = re.search(
        r"\b(amount|net payable|payable|price|quantity|qty|tax|gst|discount|paid|balance|"
        r"total amount|total|how much|how many)\b", q_lower,
    )
    if exact_record and not (aggregate_ask or exact_record_value):
        return RouteType.RAG
    if (re.search(r"\bdid\s+(?:we|[a-z]+\s+[a-z]+)\b", q_lower) and re.search(r"\b(purchase|purchases|buy|bought|return|returns)\b", q_lower)
            and not aggregate_ask):
        return RouteType.RAG
    if re.search(r"\bdid\s+[a-z]+\s+make\s+any\s+purchases\b", q_lower):
        return RouteType.RAG

    if re.search(r"\bunit\s+price\s+of\b", q_lower) and not aggregate_ask:
        return RouteType.RAG

    # Pharmacy POS questions describe measurable operations even when they do not
    # say "total", "how much", or "count" (for example, "which batches expire
    # first" or "what is running low"). Keep these on deterministic analytics.
    pos_metric = re.search(
        r"\b(sales?|sold|revenue|turnover|takings|earnings|stock|inventory|"
        r"profit|profitabilit(?:y|ies)|loss|margin|discount|cost|price|rate|mehngi|daam|qeemat|"
        r"tax|gst|expiry|expire|expired|expiring|bonus|reorder|"
        r"restock|foran|reorder(?:ed)?|out of stock|low stock|running low|remaining|overstocked|overstock|understocked|understock|stored|kept|located|"
        r"run out|running out|order today|consider ordering|fast-selling|fastest-moving|slowest-moving|"
        r"best-selling|top-selling|sold together|sales history|stock value|"
        r"fast[- ]moving|slow[- ]moving|capital tied up|dead stock|below 10|fewer than 10|fewer than 5|units remaining|"
        r"medicine sold|medicines sold|demand|consistently|consistent|last paid|last pay|last time|"
        r"farukht|farokht|frokt|frokht|bikri|bikree|munafa|nafa|faida|nuqsan|nuksan|kharcha|aamdani|kamai|galla|"
        r"baqaya|wasooli|bachat|khasara|laagat|lagat|purchase|purchases|purchasing|purchased|kharidari|khareedari|khareed|khareeda|khareedi|"
        r"batch|batches|active batches|multiple batches|supplied|supplier|suppliers|supply|location code|frequently|frequency|occur|occurs|occurring|"
        r"bill|bills|invoices?|parchi|rasid|receipts?|"
        r"karobar|business|performance|trend|behtar|behtari|kami|chalega|chalay ga|pari|parri|bachi|bache|khatam|iqdamat|masail|masla|tawajjo|dastiyabi)\b",
        q_lower,
    )
    pos_question_form = re.search(
        r"\b(what|which|who|how|where|show|list|compare|give|based on|when|has|have|are|is|were|kya|konsi|kaunsi|kon\s+si|kin|kis|kaun|kon|mera|meri|mere|konse|kaunse|kon\s+se)\b",
        q_lower,
    )
    if pos_metric and pos_question_form:
        return RouteType.ANALYTICS
    if (
        re.search(r"\b(konsi|kaunsi|kon\s+si|kin|kis)\b", q_lower)
        and re.search(r"\b(medicine|medicines|dawai|dawa|product|products)\b", q_lower)
        and re.search(r"\b(expiry|expire|expired|expiring|expires?|miyad|meyad|batch|batches|stock|order|biki|bikne|demand|cost|rate|price)\b", q_lower)
    ):
        return RouteType.ANALYTICS

    # Exact lookups in the pharmacy purchase ledger are deterministic analytics;
    # semantic retrieval alone can confuse neighboring transaction records.
    if (
        re.search(r"\btransaction\s*(?:no\.?|number|#)?\s*\d{3,}\b", q_lower)
        and re.search(r"\b(products?|supplier|net payable|invoice|amount|quantity|qty|bonus|purchased|included)\b", q_lower)
    ):
        return RouteType.ANALYTICS
    if re.search(r"\b(do we have|is there|does the dataset have|are there any)\b.{0,40}\b(product|transaction|transactions|client)\b", q_lower) and not aggregate_ask:
        return RouteType.RAG
    if re.search(r"\b(product code|invoice)\s+[a-z0-9-]*\d+\b", q_lower) and re.search(
        r"\b(quantity|amount|net payable|purchase|present|exist|dataset)\b", q_lower
    ):
        return RouteType.ANALYTICS
    if re.search(r"\b(record|records)\b", q_lower) and re.search(
        r"\b(largest|highest|biggest|max(?:imum)?)\b.{0,35}\b(discount|amount|gst|tax)\b|"
        r"\b(discount|amount|gst|tax)\b.{0,35}\b(largest|highest|biggest|max(?:imum)?)\b", q_lower
    ):
        return RouteType.ANALYTICS
    if re.search(r"\bbatches?\b", q_lower) and re.search(r"expiry|expires?|shelf life", q_lower):
        return RouteType.ANALYTICS

    if re.search(r"\b(transaction types?|product groups?|earliest[- ]expiring)\b", q_lower) or re.search(
        r"\b(earliest|latest)\s+(invoice|transaction)\s+date\b", q_lower
    ):
        return RouteType.ANALYTICS

    if re.search(
        r"\b(sales?|sell|sold|revenue|transactions?|sale|bikri|farukht|farokht)\b.*\b(last|past|previous|pichlay|pichle|guzishta|aakhri)\s+\d{1,3}\s+(days?|din)\b",
        q_lower,
    ):
        return RouteType.ANALYTICS

    # Keep concrete record retrieval separate from questions asking for a KPI.
    if (
        re.search(r"\b(batch|batches|invoice|invoices|bill|bills|receipt|receipts)\b\s+#?[a-z0-9_-]*\d+[a-z0-9_-]*", q_lower)
        or (
            re.search(r"\b(invoice|invoices|bill|bills|receipt|receipts|batch|batches|record|records|ledger|transaction|transactions|godown|godowns|location|locations)\b", q_lower)
            and re.search(r"\b(show|find|fetch|list|lookup|look up|search|retrieve|pull up|details|what happened|which|dikhao|dikhaye|dikhayen|batao|bata dein|talash|dhoondo|dhundo)\b", q_lower)
            and not re.search(r"\b(total|sum|average|avg|how much|how many|count|highest|lowest|largest|smallest|amount|discount|gst|tax|profit|margin|forecast|predict)\b", q_lower)
        )
    ):
        return RouteType.RAG

    analytics_language = (
        r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|discount|gst|tax|p&l|"
        r"revenue|sales?|turnover|takings|earnings|income|purchases?|expenses?|spend|spent|costs?|"
        r"cash flow|refunds?|returns?|units sold|quantity sold|stock sold|most|highest|lowest|top|trend|performance|"
        r"kitna|kitni|kitne|kul|bikri|bikree|farukht|farokht|frokt|frokht|munafa|nafa|faida|nuqsan|nuksan|kharcha|aamdani|kamai|"
        r"udhar|udhari|naqad|naqd|rokra|baqaya|rasid|raseed|parchi|hisab|hisaab|khata|khaata|wasooli|bachat|khasara|laagat|lagat|adaigi|"
        r"ziada|zyada|zayada|sab se|sabse|kam stock|dawai ki sale|dawa ki sale)\b"
    )
    has_analytics_language = bool(re.search(analytics_language, q_lower))

    data_description_question = (
        re.search(r"\b(dataset|file|data source|table|tables|columns?|fields?)\b", q_lower)
        and re.search(r"\b(which|what|list|show|tell|name|available|included)\b", q_lower)
        and not re.search(r"\b(total|sum(?:med)?|aggregate|average|avg|how much|how many|count|forecast|predict|profit|margin|highest|lowest|largest|smallest|greatest|top|price|amount|purchase|expenditure|percentage|percent)\b", q_lower)
    )
    if data_description_question:
        return RouteType.RAG

    entity_lookup = (
        re.search(r"\b(which|who|what|kis|kaun|kon|kaunsi|konsi|kon si|kiska|kis ka)\b", q_lower)
        and re.search(r"\b(supplier|vendor|customer|cashier|product|medicine|item|invoice|bill|batch|dawai|dawa|tablet|goli)\b", q_lower)
        and not re.search(r"\b(total|sum|average|avg|count|most|highest|lowest|largest|greatest|top|best|least|margin|profit|revenue|sales amount|kitna|kitni|kitne|ziada|zyada|sab se|sabse|percentage|percent|variety|multiple suppliers|different suppliers|expenditure|price|discount|gst|tax|quantity|units|fastest|reorder|stock|delivery|reliable|fake|counterfeit|stop)\b", q_lower)
    )
    if entity_lookup:
        return RouteType.RAG
    # Confirmation / verification follow-ups: keep the previous analytics route
    if True:
        confirmation_patterns = [
            r"^(are you sure|are u sure|really\??|is that (right|correct)\??|is this (right|correct)\??|recheck|confirm\??|double check\??|pakka\??|sach me\??|sach\??)\b"
        ]
        for pattern in confirmation_patterns:
            if re.search(pattern, q_lower):
                return RouteType.ANALYTICS if last_route == RouteType.ANALYTICS else RouteType.RAG

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
        r"\b(total|sum(?:med)?|aggregate|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|revenue|sales?|"
        r"turnover|takings|earnings|income|purchases?|expenses?|spend|spent|costs?|cash flow|p&l|"
        r"units sold|quantity sold|stock sold|"
        r"expire|expiry|expired|expiring|expire ho|expire hone|expire ho chuk|expire ho gaya|percentage|percent|"
        r"velocity|reorder|stockout|supply|days supply|days of supply|"
        r"running below|low stock|dead stock|liquidation|kam stock|"
        r"most|highest|lowest|max|min|top|best|least|qty|quantity|multiple suppliers|different suppliers|variety|fastest|delivery|reliable|fake|counterfeit|stop purchasing|"
        r"sb se|sab se|sabse|sbse|ziada|zyada|zayada|sale hwi|sale hui|dawai ki sale|dawa ki sale|"
        r"kitna|kitni|kitne|kul|bikri|munafa|nafa|faida|nuqsan|kharcha|aamdani|kamai|"
        r"bechi|biki|bikay|bikain|hwi|hui|hua|huay|huye|tha|thi|the|"
        r"fast[- ]moving|slow[- ]moving|out of stock|low stock|current stock|reorder|"
        r"capital tied up|expiring|batch|batches|generic salt|brand|together|"
        r"should i|increasing|decreasing|percentage change|compared with|which day|time period)\b"
    )
    for pattern in lookup_patterns:
        if re.search(pattern, q_lower) and not re.search(numeric_or_inventory_guard, q_lower):
            return RouteType.RAG

    # Analytics / Numeric / Inventory Intelligence keywords
    analytics_patterns = [
        r"\btotal\b", r"\bhow much\b", r"\bhow many\b", r"\bsum(?:med)?\b", r"\baggregate\b",
        r"\baverage\b", r"\bavg\b", r"\bkitna\b", r"\bkitne\b", r"\bprofit\b",
        r"\bmargin\b", r"\bexpiring\b", r"\bexpire\b", r"\bexpiry\b", r"\bexpired\b",
        r"\bcount\b", r"\bmehngi\b", r"\bsasti\b", r"\bexpensive\b", r"\bcheap\b",
        r"\bhighest\b", r"\blowest\b", r"\blargest\b", r"\bsmallest\b", r"\bgreatest\b", r"\bmax\b", r"\bmin\b", r"\bmost\b", r"\btop\b", r"\bbest\b",
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
        r"\bpercentage\b", r"\bpercent\b", r"\bvariety\b", r"\bdifferent suppliers?\b", r"\bmultiple suppliers?\b",
        r"\bmost frequently\b", r"\bpurchase price\b", r"\bsale price\b", r"\blocation code\b",
        r"\bshelf life\b", r"\bselling fastest\b", r"\bfastest\b", r"\breliable\b", r"\bfake\b", r"\bcounterfeit\b",
        r"\bstop purchasing\b", r"\bwhy did purchases\b",
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
            
    # 5. Semantic Vector Embedding Fallback (handles arbitrary unseen phrasing, Roman Urdu, & typos in 5ms)
    try:
        from app.rag.semantic_router import SemanticIntentRouter
        semantic_match = SemanticIntentRouter.get_instance().match(question, threshold=0.72)
        if semantic_match:
            return semantic_match["route"]
    except Exception:
        pass

    return RouteType.RAG

def extract_filters(question: str, domain: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract exact-match filters, single calendar dates, date intervals, and inventory threshold options from the question.
    Typo-tolerant and aware of all standard, abbreviated, Roman Urdu, and colloquial date notations.
    """
    effective_domain = domain or get_default_domain()
    filters: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    q_lower = normalize_roman_urdu_intent(question).casefold()

    # An explicit expiry cutoff is a field predicate, not a sales transaction
    # date filter. Leave the cutoff in the question for the inventory planner.
    if (re.search(r"\b(expiry|expire|expires|expired|expiring)\b", q_lower)
            and re.search(r"\b(before|prior to|earlier than|after|later than|on or before|by)\b", q_lower)
            and re.search(r"\b20\d{2}\b", q_lower)
            and not re.search(r"\b(sales?|revenue|transactions?|invoices?)\b", q_lower)):
        if options:
            filters["options"] = options
        return filters

    # Apply categorical intent before any date-window early return so compound
    # questions such as "cardiac medicines this month" keep both filters.
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
            filters["options"] = options
            break

    # 1. Date range detection e.g. "from 20 jan to 15 feb", "between 1st jan and 31st march", "from 2026-01-20 to 2026-02-15", "15 se 20 sep tak"
    date_token_pattern = r'(\d{1,2}(?:st|nd|rd|th)?(?:\s+of)?\s+[a-zA-Z]+(?:\s+\d{4})?|[a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{4})?|\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}[./-]\d{1,2}(?:[./-]\d{4})?)'
    range_pattern = re.compile(
        rf'(?:from|between|since|se)\s+{date_token_pattern}\s+(?:to|till|until|and|-|se|tak)\s+{date_token_pattern}',
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

    # 2. Single Explicit Calendar Date Detection (Day + Month + Year or Day + Month)
    # Formatted numeric ISO: YYYY-MM-DD
    m_iso = re.search(r'\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b', q_lower)
    if m_iso:
        yr, mo, dy = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            filters["year"] = yr
            filters["month"] = mo
            filters["day"] = dy
            filters["date_from"] = f"{yr:04d}-{mo:02d}-{dy:02d}"
            filters["date_to"] = f"{yr:04d}-{mo:02d}-{dy:02d}"
            return filters

    # Formatted numeric date: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    m_dmy = re.search(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b', q_lower)
    if m_dmy:
        dy, mo, yr = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if mo > 12 and dy <= 12: # Handle MM/DD/YYYY
            dy, mo = mo, dy
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            filters["year"] = yr
            filters["month"] = mo
            filters["day"] = dy
            filters["date_from"] = f"{yr:04d}-{mo:02d}-{dy:02d}"
            filters["date_to"] = f"{yr:04d}-{mo:02d}-{dy:02d}"
            return filters

    # Day Month [Year] e.g. "15 september", "15th september 2024", "15 sep", "15th of septembr"
    m_dm = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of|\s+tarikh|\s+tareekh|\s+tareeq|\s+tarekh)?\s+([a-zA-Z]+)(?:\s*,?\s*(\d{4}))?\b', q_lower)
    if m_dm:
        m_num = parse_month_token(m_dm.group(2))
        day = int(m_dm.group(1))
        if m_num and 1 <= day <= 31:
            filters["month"] = m_num
            filters["day"] = day
            yr = int(m_dm.group(3)) if m_dm.group(3) else None
            if yr:
                filters["year"] = yr
                filters["date_from"] = f"{yr:04d}-{m_num:02d}-{day:02d}"
                filters["date_to"] = f"{yr:04d}-{m_num:02d}-{day:02d}"
            else:
                filters["date_from"] = f"2026-{m_num:02d}-{day:02d}"
                filters["date_to"] = f"2026-{m_num:02d}-{day:02d}"
            return filters

    # Month Day [Year] e.g. "september 15", "september 15th, 2024", "sep 15", "septembr 15"
    m_md = re.search(r'\b([a-zA-Z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?\b', q_lower)
    if m_md:
        m_num = parse_month_token(m_md.group(1))
        day = int(m_md.group(2))
        if m_num and 1 <= day <= 31:
            filters["month"] = m_num
            filters["day"] = day
            yr = int(m_md.group(3)) if m_md.group(3) else None
            if yr:
                filters["year"] = yr
                filters["date_from"] = f"{yr:04d}-{m_num:02d}-{day:02d}"
                filters["date_to"] = f"{yr:04d}-{m_num:02d}-{day:02d}"
            else:
                filters["date_from"] = f"2026-{m_num:02d}-{day:02d}"
                filters["date_to"] = f"2026-{m_num:02d}-{day:02d}"
            return filters

    # Explicit day in Roman Urdu (e.g. "15 tareekh ko kitni sale thi", "25 tarikh")
    m_tareekh = re.search(r'\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:tareekh|tarikh|tareeq|tarekh)\b', q_lower)
    if m_tareekh:
        day_val = int(m_tareekh.group(1))
        if 1 <= day_val <= 31:
            filters["day"] = day_val

    # Quarters: Q1, Q2, Q3, Q4, First quarter, etc.
    if re.search(r'\b(q1|1st\s+quarter|first\s+quarter)\b', q_lower):
        filters["date_from"] = "2026-01-01"
        filters["date_to"] = "2026-03-31"
        return filters
    if re.search(r'\b(q2|2nd\s+quarter|second\s+quarter)\b', q_lower):
        filters["date_from"] = "2026-04-01"
        filters["date_to"] = "2026-06-30"
        return filters
    if re.search(r'\b(q3|3rd\s+quarter|third\s+quarter)\b', q_lower):
        filters["date_from"] = "2026-07-01"
        filters["date_to"] = "2026-09-30"
        return filters
    if re.search(r'\b(q4|4th\s+quarter|fourth\s+quarter)\b', q_lower):
        filters["date_from"] = "2026-10-01"
        filters["date_to"] = "2026-12-31"
        return filters

    # Arbitrary N days ago e.g. "4 days ago", "char din pehle"
    m_n_days_ago = re.search(r"\b(\d{1,3})\s+(?:days?|din)\s+ago\b", q_lower)
    if m_n_days_ago:
        n_days = int(m_n_days_ago.group(1))
        n_days_ago_date = (date.today() - timedelta(days=n_days)).isoformat()
        filters["date_from"] = n_days_ago_date
        filters["date_to"] = n_days_ago_date
        return filters

    # Parsu / Parso / Day before yesterday (T-2)
    if re.search(r"\b(day before yesterday|parsu|parso|parson|perso|prso|prsu)\b", q_lower):
        day_before_yesterday = (date.today() - timedelta(days=2)).isoformat()
        filters["date_from"] = day_before_yesterday
        filters["date_to"] = day_before_yesterday
        return filters

    # Tarson / Tarso / Three days ago (T-3)
    if re.search(r"\b(three days ago|3 days ago|tarson|tarso|trso|trsu)\b", q_lower):
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        filters["date_from"] = three_days_ago
        filters["date_to"] = three_days_ago
        return filters

    # This week / Current week
    if re.search(r"\b(this|current|is|iss)\s+(?:week|hafte|hafta)\b", q_lower):
        today = date.today()
        start_of_week = today - timedelta(days=today.weekday())
        filters["date_from"] = start_of_week.isoformat()
        filters["date_to"] = today.isoformat()
        return filters

    # Last week / Previous week
    if re.search(r"\b(last|previous|prior|pichlay|pichle|guzashta)\s+(?:week|hafte|hafta)\b", q_lower):
        today = date.today()
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        filters["date_from"] = start_of_last_week.isoformat()
        filters["date_to"] = end_of_last_week.isoformat()
        return filters

    # Yesterday is a calendar date filter (typo-tolerant).
    if re.search(r"\b(yesterday|yestarday|yesteday|yesturday|yest)\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters
    if re.search(r"\b(today|today's|todays|todday|tooday|aaj)\b", q_lower):
        today = date.today().isoformat()
        filters["date_from"] = today
        filters["date_to"] = today
        return filters
    if re.search(r"\b(this|current|is|iss)\s+(?:month|mahine|mahina)\b|\bmonth\s+to\s+date\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(day=1).isoformat()
        filters["date_to"] = today.isoformat()
    if re.search(r"\b(last|previous|prior|pichlay|pichle|guzashta)\s+(?:calendar\s+)?(?:month|mahine|mahina)\b", q_lower):
        first_this_month = date.today().replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        filters["date_from"] = last_month_end.replace(day=1).isoformat()
        filters["date_to"] = last_month_end.isoformat()
        return filters
    if re.search(r"\b(this|current|is|iss)\s+(?:year|saal)\b|\byear\s+to\s+date\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(month=1, day=1).isoformat()
        filters["date_to"] = today.isoformat()
        return filters
    # In Roman Urdu, past-tense sale or inquiry wording resolves "kal" as yesterday.
    if re.search(r"\bkal\b", q_lower):
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
    elif re.search(r"\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\s+(\d{1,2})\s+(?:months?|mahine|mahina)\b", q_lower):
        m_m = re.search(r"\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\s+(\d{1,2})\s+(?:months?|mahine|mahina)\b", q_lower)
        filters["relative_days"] = int(m_m.group(1)) * 30

    # Cash and Credit are payment labels, not sale/purchase transaction types.
    # Count matching rows directly instead of passing them to the sales counter.
    has_cash = bool(re.search(r"\bcash\b", q_lower))
    has_credit = bool(re.search(r"\bcredit\b", q_lower))
    if has_cash != has_credit and re.search(
        r"\b(transaction|transactions|row|rows|payment|payments|type)\b", q_lower
    ):
        filters["payment_label"] = "Credit" if has_credit else "Cash"

    # 3. Single month detection with typo recovery if no single date matched
    expiry_as_of = bool(
        re.search(r"\b(expired|expiry|expiring|expires?)\b", q_lower)
        and re.search(r"\b(by|as of|on or before)\b", q_lower)
    )
    if not expiry_as_of:
        # Search for month names or typos in words
        words = re.findall(r'[a-zA-Z]+', q_lower)
        for w in words:
            if len(w) >= 3:
                m_num = parse_month_token(w)
                if m_num and w not in ["may", "march"] or (w in ["march"] and not re.search(r"\b(march|marchh)\s+forward\b", q_lower)) or (w == "may" and not re.search(r"\bmay\s+(?:sales?|i|we|it)\b", q_lower)):
                    filters["month"] = m_num
                    break

    # 4. Year detection (e.g. 2024, 2025, 2026, 2027)
    years = set(re.findall(r'\b(202[0-9])\b', q_lower))
    m_yr = re.search(r'\b(202[0-9])\b', q_lower) if len(years) == 1 else None
    if m_yr and expiry_as_of:
        m_yr = None
    if m_yr:
        filters["year"] = int(m_yr.group(1))

    # 5. Expiry horizon extraction (e.g., "next 60 days", "in 30 days", "60 days", "60 din")
    m_exp_days = re.search(r'(\d{1,3})\s*(?:day|days|din|d)\b', q_lower)
    if m_exp_days and re.search(r'\b(expir|expire|expired|expiring|expiry|near|short|miyad|meyad|liquidat)\b', q_lower):
        options["expiry_days"] = int(m_exp_days.group(1))
        options["horizon_days"] = int(m_exp_days.group(1))

    # 6. Days-of-supply threshold extraction (e.g., "below a 3-day supply", "3 days supply", "3-day supply", "< 3 days")
    m_supply_days = re.search(r'\b(?:below|under|<|less than)?\s*(?:a\s*)?(\d{1,2})(?:-|\s*)(?:day|days|din)\s*(?:of\s*)?supply\b', q_lower)
    if m_supply_days:
        options["days_supply_threshold"] = float(m_supply_days.group(1))
    elif re.search(r'\b(?:below|under|<)\s*(\d{1,2})\s*(?:day|days|din)\b', q_lower):
        m_simple = re.search(r'\b(?:below|under|<)\s*(\d{1,2})\s*(?:day|days|din)\b', q_lower)
        if m_simple:
            options["days_supply_threshold"] = float(m_simple.group(1))

    if options:
        filters["options"] = options

    return filters
