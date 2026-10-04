"""Normalize common Roman Urdu spellings into stable analytics/RAG intent text.

This is a lightweight intent bridge, not a translator. It keeps names, numbers,
and the user's original wording intact while canonicalizing common function,
metric, action, and time words.
"""
from __future__ import annotations

import re


def normalize_roman_urdu_intent(question: str) -> str:
    original = question or ""
    q = original.casefold().strip()
    if not q:
        return q

    # Check for Roman Urdu markers or question keywords
    if not re.search(
        r"\b(?:kya|kia|kitni|kitna|kitne|kitnay|ktni|ktna|ktne|dawai|dawa|dawayi|dawayian|dawayan|dawaiyan|mujhe|mujhy|muje|mujy|mujc|mjhe|mjy|"
        r"mene|maine|meri|mera|mere|apni|apna|apne|munafa|nafa|faida|nuqsan|nuksan|aamdani|amdani|kamai|kamaai|"
        r"kharcha|kharch|karcha|akhrajat|bikri|bikree|bechi|bechay|biki|bikay|farukht|farokht|frokt|frokht|batao|btao|bta|btaye|bataye|dikhao|dikhaye|"
        r"pichlay|pichle|guzishta|aakhri|kal|aaj|aj|parsu|parso|parson|perso|prso|prsu|tarson|tarso|trso|trsu|kaunsi|konsi|konse|kon|kin|konsa|zyada|ziada|zayada|"
        r"mein|kaise|kese|barhao|barha|badhao|mangwaya|khareeda|khareedna|maal|qareeb|qarib|"
        r"udhar|udhari|naqad|naqd|rokra|baqaya|rasid|raseed|parchi|hisab|hisaab|khata|khaata|wasooli|bachat|khasara|"
        r"mahine|mahina|maheena|tabdeeli|bunyaad|maslay|masail|dheemi|ahista|pas|paas|bohat|karun|karon|bachi|bache|bacha|baqi|tm|tum|skte|sktay|sakti|sakte|skty|skta|sakta|"
        r"pehle|chal|pari|parri|khatam|phansa|surat|halat|haal|farq|feesad|ausatan|mehngi|izafa|iqdamat|talab|dastiyabi|tawajjo|gunjaish)\b",
        q,
    ):
        return original.strip()

    # Pre-clean common typos and word variations
    q = re.sub(r"\b(?:mujc|mjhe|mjy|mujhey|muje|mujy)\b", "mujhe", q)
    q = re.sub(r"\b(?:tm|tum)\b", "you", q)
    q = re.sub(r"\b(?:skte|sktay|sakti|sakte|skty|skta|sakta)\b", "can", q)
    q = re.sub(r"\b(?:bta|btao|btaye|batao|bataye|bata|btaen|btado|bta do|bta dein)\b", "tell", q)
    q = re.sub(r"\b(?:ktni|ktna|ktne|kitnay)\b", "kitna", q)

    # In "kitna record hua" the word record is a verb phrase (how much was
    # recorded), not a request to count database records. Preserve the amount
    # intent before the countable-noun rule below runs.
    q = re.sub(
        r"\bkitna\s+(?:amount|balance|payment|paise|paisa|raqm)?\s*record\s+(?:hua|huwa|huwa hai|hua hai|howa|howa hai)\b",
        "how much was recorded",
        q,
    )

    # Number word conversion
    num_words = {
        "ek": "1", "do": "2", "teen": "3", "chaar": "4", "char": "4",
        "panch": "5", "paanch": "5", "che": "6", "chhe": "6", "saat": "7",
        "aath": "8", "nau": "9", "nou": "9", "das": "10", "dus": "10",
        "pandrah": "15", "bees": "20", "tees": "30", "chalees": "40",
        "pachaas": "50", "saath": "60", "sattar": "70", "assi": "80", "navvay": "90"
    }
    for word, digit in num_words.items():
        q = re.sub(rf"\b{word}\b", digit, q)

    # Relative time phrases
    q = re.sub(r"\b(\d+)\s+din\s+pehle\b", r"\1 days ago", q)
    q = re.sub(r"\b(?:pichlay|pichle|guzishta|guzre|guzray|aakhri|akhri)\s+(\d{1,3})\s+(?:din|dino|dino'n|days?)\b", r"last \1 days", q)
    q = re.sub(r"\b(?:aglay|agle|agley|aanay wale|anay wale)\s+(\d{1,3})\s+(?:din|dino|dino'n|days?)\b", r"next \1 days", q)
    q = re.sub(r"\b(?:pichlay|pichle|guzishta|guzre|guzray)\s+(\d{1,3})\s+(?:mahine|mahinay|mahina|maheene|months?)\b", r"last \1 months", q)
    q = re.sub(r"\b(?:pichlay|pichle|guzishta|guzre|guzray)\s+(?:mahine|mahinay|mahina|maheene|month)\b", "last month", q)
    q = re.sub(r"\b(?:is|iss|ye|iss)\s+(?:mahine|mahinay|mahina|maheene|month)\b", "this month", q)
    q = re.sub(r"\b(?:aglay|agle|agley|aanay wale|anay wale)\s+(?:mahine|mahinay|mahina|maheene|month)\b", "next month", q)
    q = re.sub(r"\b(?:pichlay|pichle|guzishta)\s+(?:haftay|hafte|hafta|week)\b", "last week", q)
    q = re.sub(r"\b(?:is|iss)\s+(?:haftay|hafte|hafta|week)\b", "this week", q)
    q = re.sub(r"\b(?:aaj|aj)\b", "today", q)
    q = re.sub(r"\b(?:parson|parso|parsu|perso|prso|prsu)\b", "day before yesterday", q)
    q = re.sub(r"\b(?:tarson|tarso|trso|trsu)\b", "three days ago", q)
    q = re.sub(r"\b(?:is|iss)\s+(?:saal|year)\b", "this year", q)

    # Handle 'Kal'
    if re.search(r"\bkal\b", q):
        if re.search(r"\b(?:hoga|hogi|honge|ga|gi|ge|karunga|karungi|karenge|milega|milegi)\b", q):
            q = re.sub(r"\bkal\b", "tomorrow", q)
        else:
            q = re.sub(r"\bkal\b", "yesterday", q)

    # Multi-word advisory and domain templates
    q = re.sub(r"\b(?:qareeb|qarib|nazdeek)\s+(?:expire|expiry|miyad|meyad)\b", "near-expiry", q)
    q = re.sub(r"\b(?:qareeb-ul-expiry|qareebi muddat)\b", "near-expiry", q)
    q = re.sub(r"\b(?:purchasing|purchase|khareedari|kharidari|khareed|kharid)\b", "purchases", q)
    q = re.sub(r"\b(?:units?|quantity|qty|miqdaar|tadaad)\b", "units", q)
    q = re.sub(r"\b(?:fast[- ]moving|fast chal|tez bikne|jaldi bikne|tezi se bikne)\b", "fast-moving", q)
    q = re.sub(r"\b(?:slow[- ]moving|slow chal|dheemi raftaar se bikne|kam bikne|ahista bikne)\b", "slow-moving", q)
    q = re.sub(r"\b(?:out of stock|stock khatam|khatam ho gai|khatam ho gae|stock nahi|zero stock|bilkul stock nahi)\b", "out of stock", q)
    q = re.sub(r"\b(?:low stock|stock kam|kam stock|thoda stock|stock kam ho)\b", "low stock", q)
    q = re.sub(r"\b(?:dead stock|paisa phansa|paisa phansa hua|stock mein phansa|paisa atka)\b", "dead stock capital tied up", q)
    q = re.sub(r"\b(?:revenue|aamdani|amdani|kamai|kamaai|kul kamai|kul aamdani)\b", "revenue", q)
    q = re.sub(r"\b(?:farukht|farokht|frokt|frokht|farokhat|frokhat|bikri|bikree|salein|sales|sale)\b", "sales", q)
    q = re.sub(r"\b(?:munafa|munafaa|nafa|faida|fayda|bachat|bacht)\b", "profit", q)
    q = re.sub(r"\b(?:nuqsan|nuksan|nuqsaan|ghata|ghataa|khasara|totta)\b", "loss", q)
    q = re.sub(r"\b(?:kharcha|kharchay|kharch|karcha|karchay|akhrajat|ikhrajat)\b", "expenses", q)
    q = re.sub(r"\b(?:laagat|lagat|maal ki qeemat|maal ki lagat|cost price|cost)\b", "cost", q)
    q = re.sub(r"\b(?:rate|qeemat|keemat|kimat|daam|price)\b", "price", q)
    q = re.sub(r"\b(?:barh rahi|barh raha|barh rha|increasing|barhti)\b", "rising increasing", q)
    q = re.sub(r"\b(?:kam ho rahi|kam ho raha|kam ho rha|decreasing|gir rahi|kamti)\b", "declining decreasing", q)
    q = re.sub(r"\b(?:overstock|overstocked|zaroorat se zyada)\b", "overstocked", q)
    q = re.sub(r"\b(?:dobara mangwani|dobara order|order karna|restock|reorder)\b", "reorder restock", q)
    q = re.sub(r"\b(?:expire hone wali|expire hongi|expire ho rahi|expiry hone wali|jaldi expire)\b", "expiring", q)
    q = re.sub(r"\b(?:expire ho chuki|expiry ho gai|expired)\b", "expired", q)
    q = re.sub(r"\b(?:supplier|suppliers|vendor|vendors)\b", "supplier", q)
    q = re.sub(r"\b(?:kitni|kitne|kitnay)\s+(?:dawaiyan|dawayian|dawayan|dawain|dawa|dawai|goliyan|units?|items?)\s+(?:bechi|bechee|bechhi|bechay|bechein|biki|bikii|bikay|bikain|sold)\b", "units sold", q)
    q = re.sub(r"\b(?:kitne|kitnay|kitni)\s+(?:stock\s+)?units?\b", "how many units", q)
    # "Kitne" asks for a count with countable nouns; preserve that intent
    # before the general quantity normalization below maps kitna to how much.
    q = re.sub(
        r"\b(?:kitna|kitne|kitnay|kitni)\s+(?=(?:(?:distinct|unique|different|alag|alagh)\s+)?(?:batches?|lots?|records?|rows?|transactions?|invoices?|bills?|receipts?|products?|items?|suppliers?|customers?|patients?|doctors?|racks?|warehouses?)\b)",
        "how many ", q,
    )
    q = re.sub(r"\b(?:bikin|bikti|bikta|biktein|biktee|bik rahi|bik raha|bik rahe|bechi|bechee|bechhi|bechay|bechayen|bechein|biki|bikii|bikay|bikain|bikayi|bikaya|sell|sold)\b", "sold", q)
    q = re.sub(r"\b(?:sab se zyada bikne|sab se ziada bikne|ziada bikne|best selling|top selling)\b", "best-selling", q)
    q = re.sub(r"\b(?:sab se kam bikne|sab se kam|least selling)\b", "slowest-selling", q)
    q = re.sub(r"\b(?:10 se kam|less than 10)\b", "fewer than 10", q)
    q = re.sub(r"\b(?:5 se kam|sirf 5|only 5)\b", "fewer than 5", q)
    q = re.sub(r"\b(?:stock value|stock ki value|stock ki qeemat|stock ki keemat|inventory ki total value)\b", "stock value", q)
    q = re.sub(r"\b(?:bill bane|bills bane|bills banay|kitne bill|kitne bills)\b", "sales invoices count", q)
    q = re.sub(r"\b(?:sab se bara bill|bara bill|biggest bill)\b", "highest value sales invoice", q)
    q = re.sub(r"\b(?:sab se choti sale|choti sale|smallest sale)\b", "lowest value sales invoice", q)
    q = re.sub(r"\b(?:ausatan|average|daily average|rozana ausatan)\b", "average daily sales", q)
    q = re.sub(r"\b(?:percent|percentage|feesad)\b", "percentage", q)
    q = re.sub(r"\b(?:compare|farq|muqabla|muqable)\b", "compare", q)
    q = re.sub(r"\b(?:trend|surat-e-haal|karobar ki halat|stock ki halat|sales ka haal|karobar ka haal)\b", "trend performance", q)

    # General replacements
    replacements = [
        (r"\b(?:me|mein)\b", "in"),
        (r"\b(?:mujhe|mujhy|muje|mujy|mujhey)\b", "me"),
        (r"\b(?:mene|maine|main ne|mein ne|me ne)\b", "I"),
        (r"\b(?:meri|mera|mere|apni|apna|apne|hamari|hamara|hamare)\b", "my"),
        (r"\b(?:kitni|kitna|kitne|kitnay)\b", "how much"),
        (r"\b(?:dawaiyan|dawayian|dawayan|dawain|dawao|dawaoon|dawaon|dawa|dawai|dawayi|dawayee)\b", "medicines"),
        (r"\b(?:barhao|barha|barhana|barhane|barhaye|badhao|badha|badhana|izafa)\b", "increase"),
        (r"\b(?:ghatao|ghatana|kam karo|kam karna|kam karne|reduce karo|kami)\b", "reduce"),
        (r"\b(?:behtar|sudhar|behtari)\b", "improve"),
        (r"\b(?:karun|karoon|karon|karu|karein|karen|karna chahiye|hona chahiye|karni chahiye)\b", "should I"),
        (r"\b(?:dikhao|dikhayo|dikhaye|dikhayen|dikhain|dikha do|dikha dein)\b", "show"),
        (r"\b(?:kaunsi|konsi|kon si|koun si|konse|kon se|kaunse|kaun se|koun se|konsa|kaunsa|kon sa|kaun sa|kin)\b", "which"),
        (r"\b(?:kis|kiska|kis ka|kiski|kis ki|kiske|kis ke)\b", "which"),
        (r"\b(?:zyada|ziada|zayada|zaida|sabse|sab se|sb se|sbse)\b", "most"),
        (r"\b(?:kya|kia)\b", "what"),
        (r"\b(?:kaise|kese|kaisay|kesay)\b", "how"),
        (r"\b(?:kab)\b", "when"),
        (r"\b(?:kahan|kaha|kidhar)\b", "where"),
        (r"\b(?:kyun|kyu|kiun)\b", "why"),
        (r"\b(?:maslay|masla|masail|problems)\b", "biggest problems"),
        (r"\b(?:iqdamat|faisla|steps|action|actions)\b", "actions recommend"),
    ]
    for pattern, replacement in replacements:
        q = re.sub(pattern, replacement, q)

    return re.sub(r"\s+", " ", q).strip()
