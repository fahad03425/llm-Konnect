"""
Patch date awareness across router.py and chat.py.
"""
import re

# 1. Patch router.py
with open("app/rag/router.py", "r", encoding="utf-8") as f:
    router_content = f.read()

old_months_block = """MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12
}

def parse_date_token(token: str, default_year: int = 2026) -> Any:
    token = token.strip().lower()
    token = re.sub(r'(\\d+)(st|nd|rd|th)', r'\\1', token)
    token = re.sub(r'[^\\w\\s\\-]', '', token).strip()
    
    # ISO date: YYYY-MM-DD
    m_iso = re.match(r'^(\\d{4})-(\\d{1,2})-(\\d{1,2})$', token)
    if m_iso:
        return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
    
    # Day Month [Year] e.g. "20 jan", "20 jan 2026"
    m_dm = re.match(r'^(\\d{1,2})\\s+([a-z]+)(?:\\s+(\\d{4}))?$', token)
    if m_dm:
        day = int(m_dm.group(1))
        m_name = m_dm.group(2)
        yr = int(m_dm.group(3)) if m_dm.group(3) else default_year
        if m_name in MONTHS:
            return f"{yr:04d}-{MONTHS[m_name]:02d}-{day:02d}"
            
    # Month Day [Year] e.g. "jan 20", "january 20 2026"
    m_md = re.match(r'^([a-z]+)\\s+(\\d{1,2})(?:\\s+(\\d{4}))?$', token)
    if m_md:
        m_name = m_md.group(1)
        day = int(m_md.group(2))
        yr = int(m_md.group(3)) if m_md.group(3) else default_year
        if m_name in MONTHS:
            return f"{yr:04d}-{MONTHS[m_name]:02d}-{day:02d}"
    return None"""

new_months_block = """MONTH_ALIASES = {
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

def parse_month_token(word: Optional[str]) -> Optional[int]:
    \"\"\"Extract month number (1-12) from word with typo-tolerance and phonetic aliases.\"\"\"
    if not word:
        return None
    import difflib
    w = word.strip().lower()
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return None
    for m_num, aliases in MONTH_ALIASES.items():
        if w in aliases:
            return m_num
    all_aliases = [alias for aliases in MONTH_ALIASES.values() for alias in aliases]
    candidates = difflib.get_close_matches(w, all_aliases, n=1, cutoff=0.74)
    if candidates:
        matched_alias = candidates[0]
        for m_num, aliases in MONTH_ALIASES.items():
            if matched_alias in aliases:
                return m_num
    return None

def parse_date_token(token: str, default_year: int = 2026) -> Any:
    \"\"\"Parse any date string (ISO, DMY, MDY, conversational, with typos).\"\"\"
    token = token.strip().lower()
    token = re.sub(r'(\\d+)(st|nd|rd|th)', r'\\1', token)
    token = re.sub(r'[^\\w\\s\\-\\/\.]', '', token).strip()
    
    # ISO date: YYYY-MM-DD or YYYY/MM/DD
    m_iso = re.match(r'^(\\d{4})[/-](\\d{1,2})[/-](\\d{1,2})$', token)
    if m_iso:
        yr, mo, dy = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            return f"{yr:04d}-{mo:02d}-{dy:02d}"

    # Formatted numeric date: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    m_dmy = re.match(r'^(\\d{1,2})[/.-](\\d{1,2})[/.-](\\d{4})$', token)
    if m_dmy:
        d, m, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if m > 12 and d <= 12:
            d, m = m, d
        if 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"
    
    # Day Month [Year] e.g. "20 jan", "20 jan 2026", "15 septembr", "15th of sep 2024"
    m_dm = re.match(r'^(\\d{1,2})(?:\\s+of|\\s+tarikh|\\s+tareekh|\\s+tareeq)?\\s+([a-z]+)(?:\\s+(\\d{4}))?$', token)
    if m_dm:
        day = int(m_dm.group(1))
        m_num = parse_month_token(m_dm.group(2))
        yr = int(m_dm.group(3)) if m_dm.group(3) else default_year
        if m_num and 1 <= day <= 31:
            return f"{yr:04d}-{m_num:02d}-{day:02d}"
            
    # Month Day [Year] e.g. "jan 20", "january 20 2026", "septembr 15 2024"
    m_md = re.match(r'^([a-z]+)\\s+(\\d{1,2})(?:\\s+(\\d{4}))?$', token)
    if m_md:
        m_num = parse_month_token(m_md.group(1))
        day = int(m_md.group(2))
        yr = int(m_md.group(3)) if m_md.group(3) else default_year
        if m_num and 1 <= day <= 31:
            return f"{yr:04d}-{m_num:02d}-{day:02d}"
    return None"""

# Normalize line endings
router_content = router_content.replace("\r\n", "\n")
old_months_block = old_months_block.replace("\r\n", "\n")
assert old_months_block in router_content, "old_months_block not found in router.py"
router_content = router_content.replace(old_months_block, new_months_block, 1)

# 2. Patch extract_filters in router.py
old_extract_filters = """def extract_filters(question: str, domain: Optional[str] = None) -> Dict[str, Any]:
    \"\"\"
    Extract exact-match filters, date intervals, and inventory threshold options from the question.
    \"\"\"
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
        if any(re.search(rf"\\b{kw}\\b", q_lower) for kw in kw_list):
            filters["category"] = cat_name
            options["category"] = cat_name
            filters["options"] = options
            break
    
    # 1. Date range detection e.g. "from 20 jan to 15 feb", "between 1st jan and 31st march", "from 2026-01-20 to 2026-02-15"
    date_token_pattern = r'(\\d{1,2}(?:st|nd|rd|th)?\\s+[a-zA-Z]+(?:\\s+\\d{4})?|[a-zA-Z]+\\s+\\d{1,2}(?:st|nd|rd|th)?(?:\\s+\\d{4})?|\\d{4}-\\d{2}-\\d{2})'
    range_pattern = re.compile(
        rf'(?:from|between|since)\\s+{date_token_pattern}\\s+(?:to|till|until|and|-)\\s+{date_token_pattern}',
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
    m_n_days_ago = re.search(r"\\b(\\d{1,3})\\s+(?:days?|din)\\s+ago\\b", q_lower)
    if m_n_days_ago:
        n_days = int(m_n_days_ago.group(1))
        n_days_ago_date = (date.today() - timedelta(days=n_days)).isoformat()
        filters["date_from"] = n_days_ago_date
        filters["date_to"] = n_days_ago_date
        return filters

    # Parsu / Parso / Day before yesterday (T-2)
    if re.search(r"\\b(day before yesterday|parsu|parso|parson|perso|prso|prsu)\\b", q_lower):
        day_before_yesterday = (date.today() - timedelta(days=2)).isoformat()
        filters["date_from"] = day_before_yesterday
        filters["date_to"] = day_before_yesterday
        return filters

    # Tarson / Tarso / Three days ago (T-3)
    if re.search(r"\\b(three days ago|3 days ago|tarson|tarso|trso|trsu)\\b", q_lower):
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        filters["date_from"] = three_days_ago
        filters["date_to"] = three_days_ago
        return filters

    # This week / Current week
    if re.search(r"\\b(this|current)\\s+week\\b", q_lower):
        today = date.today()
        start_of_week = today - timedelta(days=today.weekday())
        filters["date_from"] = start_of_week.isoformat()
        filters["date_to"] = today.isoformat()
        return filters

    # Last week / Previous week
    if re.search(r"\\b(last|previous|prior)\\s+week\\b", q_lower):
        today = date.today()
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        filters["date_from"] = start_of_last_week.isoformat()
        filters["date_to"] = end_of_last_week.isoformat()
        return filters

    # Yesterday is a calendar date filter. A misspelling is corrected in
    # RAGChat._normalize_question, and also accepted here for direct callers.
    if re.search(r"\\b(yesterday|yestarday)\\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters
    if re.search(r"\\b(today|today's|todays)\\b", q_lower):
        today = date.today().isoformat()
        filters["date_from"] = today
        filters["date_to"] = today
        return filters
    if re.search(r"\\b(this|current)\\s+month\\b|\\bmonth\\s+to\\s+date\\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(day=1).isoformat()
        filters["date_to"] = today.isoformat()
    if re.search(r"\\b(last|previous|prior)\\s+(?:calendar\\s+)?month\\b", q_lower):
        first_this_month = date.today().replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        filters["date_from"] = last_month_end.replace(day=1).isoformat()
        filters["date_to"] = last_month_end.isoformat()
        return filters
    if re.search(r"\\b(this|current)\\s+year\\b|\\byear\\s+to\\s+date\\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(month=1, day=1).isoformat()
        filters["date_to"] = today.isoformat()
        return filters
    # In Roman Urdu, past-tense sale or inquiry wording resolves "kal" as yesterday.
    if re.search(r"\\bkal\\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters

    # Relative reporting windows are resolved against the newest date in the
    # selected dataset by RAGChat, so stale datasets don't silently use today's
    # date as if they contained current records.
    m_recent_days = re.search(
        r"\\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\\s+(\\d{1,3})\\s+(?:days?|din)\\b",
        q_lower,
    )
    if m_recent_days:
        filters["relative_days"] = int(m_recent_days.group(1))
    elif re.search(r"\\b(?:last|past|previous)\\s+(\\d{1,2})\\s+months?\\b", q_lower):
        m_m = re.search(r"\\b(?:last|past|previous)\\s+(\\d{1,2})\\s+months?\\b", q_lower)
        filters["relative_days"] = int(m_m.group(1)) * 30

    # Cash and Credit are payment labels, not sale/purchase transaction types.
    # Count matching rows directly instead of passing them to the sales counter.
    has_cash = bool(re.search(r"\\bcash\\b", q_lower))
    has_credit = bool(re.search(r"\\bcredit\\b", q_lower))
    if has_cash != has_credit and re.search(
        r"\\b(transaction|transactions|row|rows|payment|payments|type)\\b", q_lower
    ):
        filters["payment_label"] = "Credit" if has_credit else "Cash"

    # 2. Single month detection if no range
    expiry_as_of = bool(
        re.search(r"\\b(expired|expiry|expiring|expires?)\\b", q_lower)
        and re.search(r"\\b(by|as of|on or before)\\b", q_lower)
    )
    if not expiry_as_of:
        for m_name, m_num in MONTHS.items():
            if re.search(rf"\\b{m_name}\\b", q_lower):
                filters["month"] = m_num
                break

    # 3. Year detection (e.g. 2024, 2025, 2026, 2027)
    years = set(re.findall(r'\\b(202[0-9])\\b', q_lower))
    m_yr = re.search(r'\\b(202[0-9])\\b', q_lower) if len(years) == 1 else None
    if m_yr and expiry_as_of:
        m_yr = None
    if m_yr:
        filters["year"] = int(m_yr.group(1))

    # 4. Expiry horizon extraction (e.g., "next 60 days", "in 30 days", "60 days", "60 din")
    m_exp_days = re.search(r'(\\d{1,3})\\s*(?:day|days|din|d)\\b', q_lower)
    if m_exp_days and re.search(r'\\b(expir|expire|expired|expiring|expiry|near|short|miyad|meyad|liquidat)\\b', q_lower):
        options["expiry_days"] = int(m_exp_days.group(1))
        options["horizon_days"] = int(m_exp_days.group(1))

    # 5. Days-of-supply threshold extraction (e.g., "below a 3-day supply", "3 days supply", "3-day supply", "< 3 days")
    m_supply_days = re.search(r'\\b(?:below|under|<|less than)?\\s*(?:a\\s*)?(\\d{1,2})(?:-|\\s*)(?:day|days|din)\\s*(?:of\\s*)?supply\\b', q_lower)
    if m_supply_days:
        options["days_supply_threshold"] = float(m_supply_days.group(1))
    elif re.search(r'\\b(?:below|under|<)\\s*(\\d{1,2})\\s*(?:day|days|din)\\b', q_lower):
        m_simple = re.search(r'\\b(?:below|under|<)\\s*(\\d{1,2})\\s*(?:day|days|din)\\b', q_lower)
        if m_simple:
            options["days_supply_threshold"] = float(m_simple.group(1))

    if options:
        filters["options"] = options

    return filters"""

new_extract_filters = """def extract_filters(question: str, domain: Optional[str] = None) -> Dict[str, Any]:
    \"\"\"
    Extract exact-match filters, single calendar dates, date intervals, and inventory threshold options from the question.
    Typo-tolerant and aware of all standard, abbreviated, Roman Urdu, and colloquial date notations.
    \"\"\"
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
        if any(re.search(rf"\\b{kw}\\b", q_lower) for kw in kw_list):
            filters["category"] = cat_name
            options["category"] = cat_name
            filters["options"] = options
            break

    # 1. Date range detection e.g. "from 20 jan to 15 feb", "between 1st jan and 31st march", "from 2026-01-20 to 2026-02-15", "15 se 20 sep tak"
    date_token_pattern = r'(\\d{1,2}(?:st|nd|rd|th)?(?:\\s+of)?\\s+[a-zA-Z]+(?:\\s+\\d{4})?|[a-zA-Z]+\\s+\\d{1,2}(?:st|nd|rd|th)?(?:\\s+\\d{4})?|\\d{4}[/-]\\d{1,2}[/-]\\d{1,2}|\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{4})?)'
    range_pattern = re.compile(
        rf'(?:from|between|since|se)\\s+{date_token_pattern}\\s+(?:to|till|until|and|-|se|tak)\\s+{date_token_pattern}',
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
    m_iso = re.search(r'\\b(\\d{4})[/-](\\d{1,2})[/-](\\d{1,2})\\b', q_lower)
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
    m_dmy = re.search(r'\\b(\\d{1,2})[/.-](\\d{1,2})[/.-](\\d{4})\\b', q_lower)
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
    m_dm = re.search(r'\\b(\\d{1,2})(?:st|nd|rd|th)?(?:\\s+of|\\s+tarikh|\\s+tareekh|\\s+tareeq|\\s+tarekh)?\\s+([a-zA-Z]+)(?:\\s*,?\\s*(\\d{4}))?\\b', q_lower)
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
    m_md = re.search(r'\\b([a-zA-Z]+)\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:\\s*,?\\s*(\\d{4}))?\\b', q_lower)
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

    # Quarters: Q1, Q2, Q3, Q4, First quarter, etc.
    if re.search(r'\\b(q1|1st\\s+quarter|first\\s+quarter)\\b', q_lower):
        filters["date_from"] = "2026-01-01"
        filters["date_to"] = "2026-03-31"
        return filters
    if re.search(r'\\b(q2|2nd\\s+quarter|second\\s+quarter)\\b', q_lower):
        filters["date_from"] = "2026-04-01"
        filters["date_to"] = "2026-06-30"
        return filters
    if re.search(r'\\b(q3|3rd\\s+quarter|third\\s+quarter)\\b', q_lower):
        filters["date_from"] = "2026-07-01"
        filters["date_to"] = "2026-09-30"
        return filters
    if re.search(r'\\b(q4|4th\\s+quarter|fourth\\s+quarter)\\b', q_lower):
        filters["date_from"] = "2026-10-01"
        filters["date_to"] = "2026-12-31"
        return filters

    # Arbitrary N days ago e.g. "4 days ago", "char din pehle"
    m_n_days_ago = re.search(r"\\b(\\d{1,3})\\s+(?:days?|din)\\s+ago\\b", q_lower)
    if m_n_days_ago:
        n_days = int(m_n_days_ago.group(1))
        n_days_ago_date = (date.today() - timedelta(days=n_days)).isoformat()
        filters["date_from"] = n_days_ago_date
        filters["date_to"] = n_days_ago_date
        return filters

    # Parsu / Parso / Day before yesterday (T-2)
    if re.search(r"\\b(day before yesterday|parsu|parso|parson|perso|prso|prsu)\\b", q_lower):
        day_before_yesterday = (date.today() - timedelta(days=2)).isoformat()
        filters["date_from"] = day_before_yesterday
        filters["date_to"] = day_before_yesterday
        return filters

    # Tarson / Tarso / Three days ago (T-3)
    if re.search(r"\\b(three days ago|3 days ago|tarson|tarso|trso|trsu)\\b", q_lower):
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        filters["date_from"] = three_days_ago
        filters["date_to"] = three_days_ago
        return filters

    # This week / Current week
    if re.search(r"\\b(this|current|is|iss)\\s+(?:week|hafte|hafta)\\b", q_lower):
        today = date.today()
        start_of_week = today - timedelta(days=today.weekday())
        filters["date_from"] = start_of_week.isoformat()
        filters["date_to"] = today.isoformat()
        return filters

    # Last week / Previous week
    if re.search(r"\\b(last|previous|prior|pichlay|pichle|guzashta)\\s+(?:week|hafte|hafta)\\b", q_lower):
        today = date.today()
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        filters["date_from"] = start_of_last_week.isoformat()
        filters["date_to"] = end_of_last_week.isoformat()
        return filters

    # Yesterday is a calendar date filter (typo-tolerant).
    if re.search(r"\\b(yesterday|yestarday|yesteday|yesturday|yest)\\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters
    if re.search(r"\\b(today|today's|todays|todday|tooday|aaj)\\b", q_lower):
        today = date.today().isoformat()
        filters["date_from"] = today
        filters["date_to"] = today
        return filters
    if re.search(r"\\b(this|current|is|iss)\\s+(?:month|mahine|mahina)\\b|\\bmonth\\s+to\\s+date\\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(day=1).isoformat()
        filters["date_to"] = today.isoformat()
    if re.search(r"\\b(last|previous|prior|pichlay|pichle|guzashta)\\s+(?:calendar\\s+)?(?:month|mahine|mahina)\\b", q_lower):
        first_this_month = date.today().replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        filters["date_from"] = last_month_end.replace(day=1).isoformat()
        filters["date_to"] = last_month_end.isoformat()
        return filters
    if re.search(r"\\b(this|current|is|iss)\\s+(?:year|saal)\\b|\\byear\\s+to\\s+date\\b", q_lower):
        today = date.today()
        filters["date_from"] = today.replace(month=1, day=1).isoformat()
        filters["date_to"] = today.isoformat()
        return filters
    # In Roman Urdu, past-tense sale or inquiry wording resolves "kal" as yesterday.
    if re.search(r"\\bkal\\b", q_lower):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        filters["date_from"] = yesterday
        filters["date_to"] = yesterday
        return filters

    # Relative reporting windows are resolved against the newest date in the
    # selected dataset by RAGChat, so stale datasets don't silently use today's
    # date as if they contained current records.
    m_recent_days = re.search(
        r"\\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\\s+(\\d{1,3})\\s+(?:days?|din)\\b",
        q_lower,
    )
    if m_recent_days:
        filters["relative_days"] = int(m_recent_days.group(1))
    elif re.search(r"\\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\\s+(\\d{1,2})\\s+(?:months?|mahine|mahina)\\b", q_lower):
        m_m = re.search(r"\\b(?:last|past|previous|pichlay|pichle|guzishta|aakhri)\\s+(\\d{1,2})\\s+(?:months?|mahine|mahina)\\b", q_lower)
        filters["relative_days"] = int(m_m.group(1)) * 30

    # Cash and Credit are payment labels, not sale/purchase transaction types.
    # Count matching rows directly instead of passing them to the sales counter.
    has_cash = bool(re.search(r"\\bcash\\b", q_lower))
    has_credit = bool(re.search(r"\\bcredit\\b", q_lower))
    if has_cash != has_credit and re.search(
        r"\\b(transaction|transactions|row|rows|payment|payments|type)\\b", q_lower
    ):
        filters["payment_label"] = "Credit" if has_credit else "Cash"

    # 3. Single month detection with typo recovery if no single date matched
    expiry_as_of = bool(
        re.search(r"\\b(expired|expiry|expiring|expires?)\\b", q_lower)
        and re.search(r"\\b(by|as of|on or before)\\b", q_lower)
    )
    if not expiry_as_of:
        # Search for month names or typos in words
        words = re.findall(r'[a-zA-Z]+', q_lower)
        for w in words:
            if len(w) >= 3:
                m_num = parse_month_token(w)
                if m_num and w not in ["may", "march"] or (w in ["march"] and not re.search(r"\\b(march|marchh)\\s+forward\\b", q_lower)) or (w == "may" and not re.search(r"\\bmay\\s+(?:sales?|i|we|it)\\b", q_lower)):
                    filters["month"] = m_num
                    break

    # 4. Year detection (e.g. 2024, 2025, 2026, 2027)
    years = set(re.findall(r'\\b(202[0-9])\\b', q_lower))
    m_yr = re.search(r'\\b(202[0-9])\\b', q_lower) if len(years) == 1 else None
    if m_yr and expiry_as_of:
        m_yr = None
    if m_yr:
        filters["year"] = int(m_yr.group(1))

    # 5. Expiry horizon extraction (e.g., "next 60 days", "in 30 days", "60 days", "60 din")
    m_exp_days = re.search(r'(\\d{1,3})\\s*(?:day|days|din|d)\\b', q_lower)
    if m_exp_days and re.search(r'\\b(expir|expire|expired|expiring|expiry|near|short|miyad|meyad|liquidat)\\b', q_lower):
        options["expiry_days"] = int(m_exp_days.group(1))
        options["horizon_days"] = int(m_exp_days.group(1))

    # 6. Days-of-supply threshold extraction (e.g., "below a 3-day supply", "3 days supply", "3-day supply", "< 3 days")
    m_supply_days = re.search(r'\\b(?:below|under|<|less than)?\\s*(?:a\\s*)?(\\d{1,2})(?:-|\\s*)(?:day|days|din)\\s*(?:of\\s*)?supply\\b', q_lower)
    if m_supply_days:
        options["days_supply_threshold"] = float(m_supply_days.group(1))
    elif re.search(r'\\b(?:below|under|<)\\s*(\\d{1,2})\\s*(?:day|days|din)\\b', q_lower):
        m_simple = re.search(r'\\b(?:below|under|<)\\s*(\\d{1,2})\\s*(?:day|days|din)\\b', q_lower)
        if m_simple:
            options["days_supply_threshold"] = float(m_simple.group(1))

    if options:
        filters["options"] = options

    return filters"""

old_extract_filters = old_extract_filters.replace("\r\n", "\n")
assert old_extract_filters in router_content, "old_extract_filters not found in router.py"
router_content = router_content.replace(old_extract_filters, new_extract_filters, 1)

with open("app/rag/router.py", "w", encoding="utf-8") as f:
    f.write(router_content)

print("router.py patched successfully!")
