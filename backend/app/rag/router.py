import re
from datetime import date, timedelta
from typing import Dict, Any, Optional
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
    Route to Chit-chat if it's a greeting or casual capability/identity question.
    Route to Analytics if question asks for numbers, aggregates, margins, counts.
    Default to RAG (record lookup).
    """
    # Normalize pharmacy shorthand before applying the distinct analytics vs
    # record-lookup rules. Keep the original for language-sensitive patterns.
    normalized_question = normalize_pharmacy_vocabulary(question or "")
    original_lower = normalized_question.casefold()
    q_lower = normalize_roman_urdu_intent(normalized_question).casefold()

    analytics_language = (
        r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|discount|gst|tax|p&l|"
        r"revenue|sales?|turnover|takings|earnings|income|purchases?|expenses?|spend|spent|costs?|"
        r"cash flow|refunds?|returns?|units sold|quantity sold|stock sold|most|highest|lowest|top|trend|performance|"
        r"kitna|kitni|kitne|kul|bikri|bikree|farukht|farokht|frokt|frokht|munafa|nafa|faida|nuqsan|nuksan|kharcha|aamdani|kamai|"
        r"udhar|udhari|naqad|naqd|rokra|baqaya|rasid|raseed|parchi|hisab|hisaab|khata|khaata|wasooli|bachat|khasara|laagat|lagat|adaigi|"
        r"ziada|zyada|zayada|sab se|sabse|kam stock|dawai ki sale|dawa ki sale)\b"
    )
    has_analytics_language = bool(re.search(analytics_language, q_lower) or re.search(analytics_language, original_lower))

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
    if re.search(r"\b(lead time|delivery time|days? to deliver|how long.*deliver|how many days.*take.*deliver)\b", q_lower):
        return RouteType.RAG
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
        r"restock|foran|reorder(?:ed)?|out of stock|low stock|running low|remaining|overstocked|overstock|"
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
        r"\b(what|which|who|how|show|list|compare|give|based on|when|has|have|are|is|were|kya|konsi|kaunsi|kon\s+si|kin|kis|kaun|kon|mera|meri|mere|konse|kaunse|kon\s+se)\b",
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
        and not re.search(r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|profit|margin|highest|lowest|largest|smallest|greatest|top|price|amount|purchase|expenditure|percentage|percent)\b", q_lower)
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
        r"\b(total|sum|average|avg|how much|how many|count|forecast|predict|margin|profit|loss|revenue|sales?|"
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
        r"\btotal\b", r"\bhow much\b", r"\bhow many\b", r"\bsum\b",
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
    Extract exact-match filters, date intervals, and inventory threshold options from the question.
    """
    effective_domain = domain or get_default_domain()
    filters: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    q_lower = normalize_roman_urdu_intent(question).casefold()

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
    if re.search(r"\b(this|current)\s+week\b", q_lower):
        today = date.today()
        start_of_week = today - timedelta(days=today.weekday())
        filters["date_from"] = start_of_week.isoformat()
        filters["date_to"] = today.isoformat()
        return filters

    # Last week / Previous week
    if re.search(r"\b(last|previous|prior)\s+week\b", q_lower):
        today = date.today()
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        filters["date_from"] = start_of_last_week.isoformat()
        filters["date_to"] = end_of_last_week.isoformat()
        return filters

    # Yesterday is a calendar date filter. A misspelling is corrected in
    # RAGChat._normalize_question, and also accepted here for direct callers.
    if re.search(r"\b(yesterday|yestarday)\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters
    if re.search(r"\b(today|today's|todays)\b", q_lower):
        today = date.today().isoformat()
        filters["date_from"] = today
        filters["date_to"] = today
        return filters
    if re.search(r"\b(this|current)\s+month\b|\bmonth\s+to\s+date\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(day=1).isoformat()
        filters["date_to"] = today.isoformat()
    if re.search(r"\b(last|previous|prior)\s+(?:calendar\s+)?month\b", q_lower):
        first_this_month = date.today().replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        filters["date_from"] = last_month_end.replace(day=1).isoformat()
        filters["date_to"] = last_month_end.isoformat()
        return filters
    if re.search(r"\b(this|current)\s+year\b|\byear\s+to\s+date\b", q_lower):
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
    elif re.search(r"\b(?:last|past|previous)\s+(\d{1,2})\s+months?\b", q_lower):
        m_m = re.search(r"\b(?:last|past|previous)\s+(\d{1,2})\s+months?\b", q_lower)
        filters["relative_days"] = int(m_m.group(1)) * 30

    # Cash and Credit are payment labels, not sale/purchase transaction types.
    # Count matching rows directly instead of passing them to the sales counter.
    has_cash = bool(re.search(r"\bcash\b", q_lower))
    has_credit = bool(re.search(r"\bcredit\b", q_lower))
    if has_cash != has_credit and re.search(
        r"\b(transaction|transactions|row|rows|payment|payments|type)\b", q_lower
    ):
        filters["payment_label"] = "Credit" if has_credit else "Cash"

    # 2. Single month detection if no range
    expiry_as_of = bool(
        re.search(r"\b(expired|expiry|expiring|expires?)\b", q_lower)
        and re.search(r"\b(by|as of|on or before)\b", q_lower)
    )
    if not expiry_as_of:
        for m_name, m_num in MONTHS.items():
            if re.search(rf"\b{m_name}\b", q_lower):
                filters["month"] = m_num
                break

    # 3. Year detection (e.g. 2024, 2025, 2026, 2027)
    years = set(re.findall(r'\b(202[0-9])\b', q_lower))
    m_yr = re.search(r'\b(202[0-9])\b', q_lower) if len(years) == 1 else None
    if m_yr and expiry_as_of:
        m_yr = None
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

    if options:
        filters["options"] = options

    return filters
