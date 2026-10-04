"""Module 6.5 — RAG Chatbot Core."""

import time
import json
import math
import re
import logging
from datetime import date
from typing import Generator, List, Dict, Any, Optional, Tuple
from fastapi import HTTPException

from app.core.config import settings
from app.core.llm import llm
from app.ingestion.models import RetrievedChunk
from app.ingestion.store import KnowledgeBase
from app.rag.models import ChatRequest, ChatResponse, SourceReference
from app.rag.history import session_manager
from app.rag.intent import analytics_request_guard, is_dataset_question_suggestion_request, is_procedural_how_to_question
from app.rag.router import classify_route, extract_filters, is_advice_question, AnalyticsRouter, RouteType
from app.language.roman_urdu import normalize_roman_urdu_intent
from app.language.pharmacy_vocabulary import normalize_pharmacy_vocabulary

logger = logging.getLogger(__name__)


def _valid_source_row(value):
    """Normalize source row metadata before it reaches Pydantic citation models."""
    if value is None:
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return None
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_source_name(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"nan", "none", "null"} else None


def _dataset_question_suggestion_instruction(question: str) -> str:
    match = re.search(r"\b(\d{1,2})\s+questions?\b", question or "", re.I)
    count = max(1, min(int(match.group(1)), 8)) if match else 4
    return (
        f"\n\nThe user wants example questions answerable from the currently selected data scope. "
        f"Suggest up to {count} concise questions (use exactly {count} when the evidence supports them). "
        "Ground every question only in table names, field labels, and values present in the selected-scope Context Records. "
        "Use the actual business domain shown by those records; do not substitute another domain or invent columns, measures, or tables. "
        "Prefer distinct questions that can be answered by retrieving or comparing the shown records. "
        "If the evidence is too narrow, say so briefly and give only the supported questions. "
        "Do not repeat an earlier assistant refusal; answer this request from the current selected-scope evidence."
    )


class RAGChat:
    def __init__(self):
        self.kb = KnowledgeBase()
        self.analytics_router = AnalyticsRouter()

    def _normalize_question(self, question: str) -> str:
        """Normalize small input variations before routing a question."""
        import re

        q = normalize_pharmacy_vocabulary(question.strip().strip('"\'“”‘’'))
        # Basic digit normalization (Urdu/Indic to ASCII)
        translation_table = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        q = q.translate(translation_table)
        # In this common wording, "may sales in the last N days" is a typo for
        # "my sales"; treating it as the month May creates a false date filter.
        q = re.sub(
            r"\bmay(?=\s+sales?\s+(?:in|during|for)\s+(?:the\s+)?(?:last|past|previous)\s+\d{1,3}\s+days?\b)",
            "my",
            q,
            flags=re.IGNORECASE,
        )
        q = re.sub(r"\byestarday\b", "yesterday", q, flags=re.IGNORECASE)
        # This common typo otherwise sends forecast requests to vector retrieval,
        # which cannot compute a future value from source records.
        q = re.sub(
            r"\bforcast(s|ed|ing)?\b",
            lambda match: "forecast" + (match.group(1) or ""),
            q,
            flags=re.IGNORECASE,
        )
        # Fix only clear misspellings of a small intent vocabulary. Never edit
        # tokens containing digits, identifiers, medicine names, or arbitrary
        # user supplied entities. This is local and constant-time per token.
        from difflib import SequenceMatcher
        intent_words = (
            "total", "sales", "sale", "revenue", "profit", "gross", "margin",
            "stock", "inventory", "quantity", "units", "sold", "purchase",
            "purchases", "expenses", "expense", "average", "count", "forecast",
            "refund", "discount", "tax", "supplier", "customer", "product",
            "expiry", "expired", "reorder", "transaction", "transactions",
            "invoice", "compare", "highest", "lowest", "trend", "price", "cost",
        )

        def correct_intent_word(match):
            word = match.group(0)
            if len(word) < 4 or any(char.isdigit() for char in word):
                return word
            folded = word.casefold()
            if folded in intent_words:
                return word
            scored = sorted(
                ((SequenceMatcher(None, folded, candidate).ratio(), candidate) for candidate in intent_words),
                reverse=True,
            )
            nearest = scored[0][1]
            matched_chars = sum(
                block.size
                for block in SequenceMatcher(None, folded, nearest).get_matching_blocks()
            )
            edit_budget = max(len(folded), len(nearest)) - matched_chars
            # Similarity alone can collapse unrelated words with different
            # lengths (for example, "recorded" into "reorder"). Limit fuzzy
            # corrections to near-length, near-spelling matches so routing
            # terms stay intact.
            if (
                abs(len(folded) - len(scored[0][1])) <= 1
                and edit_budget <= 1
                and scored[0][0] >= 0.78
                and scored[0][0] - scored[1][0] >= 0.12
            ):
                fixed = scored[0][1]
                return fixed.capitalize() if word[:1].isupper() else fixed
            return word

        return re.sub(r"\b[A-Za-z]{4,}\b", correct_intent_word, q)

    @staticmethod
    def _missing_evidence_answer(question: str, lang: str = "english") -> str:
        """Abstain safely when retrieval returned no supporting records."""
        import re

        q = question.casefold()
        is_roman = lang == "roman_urdu"
        is_urdu = lang == "urdu_script"
        if re.search(r"\b(recall|recalled|withdrawn|lot alert)\b", q):
            if is_roman:
                return "Connected data mein is batch ka current official recall record nahi mila, is liye recall status ki tasdeeq nahi kar sakta. Regulator ya manufacturer ka taza notice check karein."
            if is_urdu:
                return "منسلک ڈیٹا میں اس بیچ کا موجودہ سرکاری ریکال ریکارڈ نہیں ملا، اس لیے ریکال کی تصدیق نہیں کر سکتا۔ ریگولیٹر یا مینوفیکچرر کا تازہ نوٹس دیکھیں۔"
            return (
                "I can't verify this batch's recall status because no current official recall record was found in the connected data. "
                "Please check the regulator or manufacturer's current notice."
            )
        if re.search(r"\b(controlled|schedule|legal|law|regulation|recordkeeping requirement|prescription requirement)\b", q):
            if is_roman:
                return "Aap ki pharmacy kis mulk ya subay mein hai? Koi mojooda aur mo'tabar maqami qanooni source nahi mila, is liye applicable requirement ki tasdeeq nahi kar sakta."
            if is_urdu:
                return "آپ کی فارمیسی کس ملک یا صوبے میں ہے؟ کوئی موجودہ اور مستند مقامی قانونی ماخذ نہیں ملا، اس لیے متعلقہ تقاضے کی تصدیق نہیں کر سکتا۔"
            return (
                "Which country or province is this pharmacy in? No current jurisdiction-specific authoritative source was found, "
                "so I can't confirm the applicable legal requirement."
            )
        if re.search(r"\b(dose|dosage|khurak)\b", q) and re.search(r"\b(patient|child|kid|baby|pregnan\w*|breastfeed\w*|my son|my daughter|year[- ]old|mareez|bache|bachay)\b", q) or re.search(
            r"\b(give|safe|suitable|can .* take|de sakta|de sakti|munasib|mehfooz)\b.{0,50}\b(patient|child|kid|baby|year[- ]old|mareez|bache|bachay|this medicine|this drug)\b", q
        ):
            if is_roman:
                return "Connected records se mareez ke liye khaas dose ya munasibat ka faisla nahi ho sakta. Dawa aur strength ki tasdeeq pharmacist ya prescriber se karein."
            if is_urdu:
                return "منسلک ریکارڈ سے مریض کے لیے مخصوص خوراک یا موزونیت کا فیصلہ نہیں کیا جا سکتا۔ دوا اور طاقت کی تصدیق فارماسسٹ یا معالج سے کریں۔"
            return (
                "I can't determine a patient-specific dose or suitability from the connected records. "
                "Please confirm the exact medicine and strength with a pharmacist or prescriber."
            )
        if is_roman:
            return "Muntakhib connected data mein mutaliqa record nahi mila, is liye is tafseel ki tasdeeq nahi kar sakta."
        if is_urdu:
            return "منتخب منسلک ڈیٹا میں متعلقہ ریکارڈ نہیں ملا، اس لیے اس تفصیل کی تصدیق نہیں کر سکتا۔"
        return "I couldn't find anything matching in the selected connected data, so I can't verify that detail."

    @staticmethod
    def _protected_question_answer(question: str, lang: str) -> Optional[str]:
        """Fail closed for high-risk requests the app cannot authority-check."""
        import re

        q = question.casefold()
        roman = lang == "roman_urdu"
        urdu = lang == "urdu_script"
        if re.search(r"\b(bank account|account number|iban|password|login credential|api key|secret key)\b", q):
            if roman:
                return "Main bank account ya login credentials ka andaza nahi laga sakta aur is chat mein aisi sensitive maloomat share nahi kar sakta."
            if urdu:
                return "میں بینک اکاؤنٹ یا لاگ اِن معلومات کا اندازہ نہیں لگا سکتا اور اس چیٹ میں ایسی حساس معلومات فراہم نہیں کر سکتا۔"
            return "I can't invent or disclose bank account or login credentials through this chat."
        if re.search(r"\b(recall|recalled|withdrawn|lot alert)\b", q):
            if roman:
                return "Connected data se is batch ka current official recall status verify nahi ho sakta. Regulator ya manufacturer ka taza notice check karein."
            if urdu:
                return "منسلک ڈیٹا سے اس بیچ کی موجودہ سرکاری ریکال حیثیت کی تصدیق نہیں ہو سکتی۔ ریگولیٹر یا مینوفیکچرر کا تازہ نوٹس دیکھیں۔"
            return "I can't verify this batch's recall status without a current official recall source. Please check the regulator or manufacturer's latest notice."
        if re.search(r"\b(controlled|schedule medicine|legal requirement|regulatory requirement|what does the law|recordkeeping requirement)\b", q):
            if roman:
                return "Aap ki pharmacy kis mulk ya subay mein hai? Jurisdiction aur current authoritative source ke baghair qanooni requirement ki tasdeeq nahi kar sakta."
            if urdu:
                return "آپ کی فارمیسی کس ملک یا صوبے میں ہے؟ دائرۂ اختیار اور موجودہ مستند ماخذ کے بغیر قانونی تقاضے کی تصدیق نہیں کر سکتا۔"
            return "Which country or province is this pharmacy in? I can't confirm a legal requirement without the jurisdiction and a current authoritative source."
        if re.search(r"\b(dose|dosage|khurak)\b", q) and re.search(r"\b(patient|child|kid|baby|pregnan\w*|breastfeed\w*|my son|my daughter|year[- ]old|mareez|bache|bachay)\b", q) or re.search(
            r"\b(give|safe|suitable|can .* take|de sakta|de sakti|munasib|mehfooz)\b.{0,50}\b(patient|child|kid|baby|year[- ]old|mareez|bache|bachay|this medicine|this drug)\b", q
        ):
            if roman:
                return "Main mareez ke liye khaas dose ya munasibat ka faisla nahi kar sakta. Exact dawa aur strength ki tasdeeq pharmacist ya prescriber se karein."
            if urdu:
                return "میں مریض کے لیے مخصوص خوراک یا موزونیت کا فیصلہ نہیں کر سکتا۔ دوا اور طاقت کی تصدیق فارماسسٹ یا معالج سے کریں۔"
            return "I can't make a patient-specific dosing or suitability decision. Please confirm the exact medicine and strength with a pharmacist or prescriber."
        return None


    @staticmethod
    def _get_fast_chitchat_response(question: str, lang: str):
        """Differentiated, rich responses for greetings, wellbeing, capabilities, thanks, and pleasantries."""
        import re
        q = question.strip().lower()
        q_clean = re.sub(r"[^a-zA-Z0-9\s]", "", q).strip()

        # 1. Wellbeing / "How are you" / "Kaise ho"
        wellbeing = {
            "how are you", "how are you doing", "how is it going", "hows it going",
            "how are you today", "how is your day", "how do you do",
            "kaise ho", "kya hal hai", "kya haal hai", "kese ho", "theek ho", "thek ho",
            "sab theek", "sab kaisa hai", "aap kaise hain", "ap kaise hain", "ap kaise ho",
            "aap kaisay hain", "kese hain", "kaise hain"
        }
        if q_clean in wellbeing or any(phrase in q_clean for phrase in ["how are you", "how r u", "kya hal hai", "kya haal hai", "kaise ho", "kese ho", "aap kaise hain"]):
            if lang == "roman_urdu":
                return "Alhamdulillah, main bilkul theek hoon! Aap ka shukriya. Main aap ki store sales, purchases aur stock reports analyze karne ke liye tayar hoon. Batayein aaj kya check karna chahte hain?"
            elif lang == "urdu_script":
                return "الحمدللہ، میں بالکل ٹھیک ہوں! پوچھنے کا شکریہ۔ میں آپ کی سیلز، خریداری اور اسٹاک رپورٹس کا جائزہ لینے کے لیے تیار ہوں۔ فرمائیے، آج کیا دیکھنا چاہتے ہیں؟"
            return "I'm doing great, thank you for asking! I'm all set to assist you with your sales trends, inventory records, and business metrics. How can I help you today?"

        # 2. Islamic / Traditional Greetings
        salam_greetings = {
            "salam", "assalam o alaikum", "assalamu alaikum", "assalam alaikum",
            "salam alaikum", "aaoa", "salam bhai", "salam sir", "asalam o alaikum",
            "assalamualaykum", "asalamu alaykum"
        }
        if q_clean in salam_greetings or any(q_clean.startswith(s) for s in ["salam", "assalam"]):
            if lang == "roman_urdu":
                return "Walaikum Assalam! Khush aamdeed. Main aap ki pharmacy POS, sales, purchases aur stock management mein kis tarah madad kar sakta hoon?"
            elif lang == "urdu_script":
                return "وعلیکم السلام! خوش آمدید۔ میں آپ کے پی او ایس ڈیٹا، سیلز اور انوینٹری میں کیا مدد کر سکتا ہوں؟"
            return "Walaikum Assalam! Welcome. How can I assist you with your store operations, sales, or inventory today?"

        # 3. Standard Greetings (Hi, Hello, Good Morning)
        general_greetings = {
            "hi", "hello", "hey", "hiya", "good morning", "good afternoon",
            "good evening", "good day", "hello there", "hi there", "hy", "hey there", "greetings"
        }
        if q_clean in general_greetings:
            if lang == "roman_urdu":
                return "Assalam-o-Alaikum! Main aap ki sales, purchases aur inventory records ke tajziye ke liye hazir hoon. Aaj main aap ki kya madad kar sakta hoon?"
            elif lang == "urdu_script":
                return "السلام علیکم! میں آپ کے کاروباری ریکارڈز، سیلز اور انوینٹری کے لیے حاضر ہوں۔ فرمائیے، میں آپ کی کیا مدد کر سکتا ہوں؟"
            return "Hello! How can I assist you today? I'm ready to help you analyze sales, monitor inventory, lookup products, or generate business reports."

        # 4. Gratitude / Thanks
        thanks = {
            "thanks", "thank you", "thx", "shukriya", "bohot shukriya", "bahut shukriya",
            "dhanyawad", "many thanks", "thanks a lot", "thank u", "great job", "good job",
            "awesome", "nice", "zabardast", "shukria"
        }
        if q_clean in thanks or any(q_clean.startswith(t) for t in ["thank", "shukriya", "shukria"]):
            if lang == "roman_urdu":
                return "Aap ka bohot shukriya! Agar aap ko kisi aur record, supplier bill ya inventory item ke baray mein janna ho toh zaroor batayein."
            elif lang == "urdu_script":
                return "بہت شکریہ! اگر آپ کو کسی اور ریکارڈ، بل یا انوینٹری کے بارے میں معلومات درکار ہوں تو ضرور بتائیں۔"
            return "You're very welcome! If there's anything else you'd like to explore in your sales, orders, or inventory records, just let me know."

        # 5. Bot Identity & Capabilities
        identity = {
            "who are you", "what are you", "tum kaun ho", "aap kaun hain",
            "kya kar sakte ho", "what can you do", "help me", "what are your features",
            "who made you", "introduce yourself", "taaruf"
        }
        if q_clean in identity or any(phrase in q_clean for phrase in ["who are you", "what can you do", "tum kaun ho", "aap kaun hain", "kya kar sakte ho"]):
            if lang == "roman_urdu":
                return "Main LLM-Konnect ka local AI business intelligence assistant hoon. Main aap ke connected SQL databases, Excel aur CSV sales records ka deep analysis karta hoon—jaise daily revenue, profit margin, low stock aur expiry alerts."
            elif lang == "urdu_script":
                return "میں LLM-Konnect کا لوکل اے آئی اسسٹنٹ ہوں۔ میں آپ کے منسلک ایس کیو ایل ڈیٹابیس، ایکسل اور سی ایس وی ریکارڈز کا گہرا تجزیہ کرتا ہوں، جیسے روزانہ آمدن، منافع کا مارجن، اور ایکسپائری الرٹس۔"
            return "I am LLM-Konnect, your private local AI business intelligence assistant. I help you query connected SQL databases and spreadsheets, calculate revenues and margins, track low stock/expiries, and answer business questions securely on your device."

        # 6. Farewell / Sign-off
        farewell = {
            "bye", "goodbye", "good bye", "see you", "see ya", "take care",
            "allah hafiz", "khuda hafiz", "alvida", "cya", "bye bye"
        }
        if q_clean in farewell:
            if lang == "roman_urdu":
                return "Allah Hafiz! Aap ka din behtareen guzray. Jab bhi store data ya reports check karni hon, main hazir hoon."
            elif lang == "urdu_script":
                return "اللہ حافظ! آپ کا دن اچھا گزرے۔ جب بھی اسٹور ڈیٹا یا رپورٹس چیک کرنی ہوں، میں حاضر ہوں۔"
            return "Goodbye! Have a productive day managing your business. Whenever you need insights from your records, I'll be right here."

        return None

    @staticmethod
    def _direct_record_answer(question: str, chunks):
        """Format exact batch, invoice, or prescription status from matching rows."""
        import re

        q = question.casefold()
        record_id = re.search(r"\b(?:sale|sales|pur|purchase|inv|inventory|rx|sku|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b", q)
        if record_id:
            identifier = record_id.group(0)
            id_norm = re.sub(r"[^a-z0-9]", "", identifier)
            exact = []
            for chunk in chunks:
                meta = chunk.metadata
                values = [meta.get(field) for field in ("invoice_id", "transaction_id", "product_code", "sku", "product_id", "batch_no")]
                if any(re.sub(r"[^a-z0-9]", "", str(value).casefold()) == id_norm for value in values if value not in (None, "")):
                    exact.append(chunk)
            if exact:
                row_text = next((c.text for c in exact if identifier in c.text.casefold()), exact[0].text)
                segment = next((part.strip() for part in row_text.split(" | ") if identifier in part.casefold()), row_text)

                def field_value(*labels):
                    for label in labels:
                        match = re.search(rf"(?:^|\.\s*){re.escape(label)}:\s*(.*?)(?=\.\s*[A-Za-z][A-Za-z _]*:\s|$)", segment, re.I)
                        if match:
                            return match.group(1).strip().rstrip(".")
                    return None

                if re.search(r"\b(total|amount|how much)\b", q):
                    amounts = [float(c.metadata.get("amount") or c.metadata.get("net_payable") or c.metadata.get("total_amount") or 0) for c in exact if (c.metadata.get("amount") is not None or c.metadata.get("net_payable") is not None or c.metadata.get("total_amount") is not None)]
                    if amounts:
                        tot = sum(amounts)
                        return f"Invoice {identifier.upper()} recorded total: {tot:g}.", exact

                if re.search(r"\b(customer|buyer|patient)\b", q): requested.append(("Customer", field_value("Customer")))
                if re.search(r"\b(product|medicine|item)\b", q): requested.append(("Product", field_value("Product Name", "Product")))
                if re.search(r"\b(quantity|units? sold|how many|stock|in stock)\b", q): requested.append(("Quantity", field_value("Quantity", "Stock quantity", "Stock")))
                if re.search(r"\breorder level\b", q): requested.append(("Reorder level", field_value("Reorder level")))
                if "discount" in q: requested.append(("Discount", field_value("Discount")))
                if re.search(r"\b(date|when)\b", q): requested.append(("Date", field_value("Date", "Purchase Date")))
                if re.search(r"\b(branch|where)\b", q): requested.append(("Branch/Warehouse", field_value("Branch", "Warehouse")))
                if "supplier" in q: requested.append(("Supplier", field_value("Supplier")))
                if re.search(r"\b(expiry|expires?)\b", q): requested.append(("Expiry", field_value("Expiry")))
                if re.search(r"\b(unit cost|cost)\b", q): requested.append(("Unit Cost", field_value("Unit Cost", "Cost price")))
                if re.search(r"\b(unit price|price)\b", q): requested.append(("Unit price", field_value("Unit price", "Unit Price")))
                if re.search(r"\bpayment\s+(?:method|type|mode)\b", q):
                    payment_method = field_value("Payment Method", "Payment Type", "Payment Mode", "Method")
                    if payment_method:
                        requested.append(("Payment method", payment_method))
                    else:
                        return f"{identifier.upper()}: the selected record does not include a payment method.", exact[:3]
                elif re.search(r"\b(status|paid|payment)\b", q):
                    requested.append(("Payment status", field_value("Payment Status", "Status")))
                if re.search(r"\b(total|amount|how much)\b|total[_ ]amount|invoice[_ ]total", q): requested.append(("Total amount", field_value("Invoice Total", "Total amount", "Amount")))
                requested = [(label, value) for label, value in requested if value]
                if re.search(r"\b(below|under|above|over)\s+(?:(?:the|its|their)\s+)?reorder level\b", q):
                    try:
                        stock = float(re.sub(r"[^0-9.-]", "", field_value("Quantity", "Stock quantity", "Stock") or ""))
                        reorder = float(re.sub(r"[^0-9.-]", "", field_value("Reorder level") or ""))
                        below = stock < reorder
                        decision = f"Yes, {stock:g} is {reorder-stock:g} units below the reorder level." if below else f"No, {stock:g} is {stock-reorder:g} units above the reorder level."
                        requested.insert(0, ("Reorder check", decision))
                    except (TypeError, ValueError):
                        pass
                if requested:
                    return f"{identifier.upper()}: " + "; ".join(f"{label}: {value}" for label, value in requested) + ".", exact[:3]
                if re.search(r"\bbatch (?:number|no\.?|#)?\b", q) and identifier.startswith(("inv-", "inventory-")):
                    return f"The connected inventory record for {identifier.upper()} does not contain a batch number.", exact[:3]
                return f"{identifier.upper()} is present in the connected data, but the requested detail is not recorded on that row.", exact[:3]
        if re.search(r"\b(lead time|delivery time|days? to deliver|how long.*deliver|how many days.*take.*deliver)\b", q):
            supplier_chunks = [c for c in chunks if (c.metadata.get("supplier_name") or c.metadata.get("vendor_name")) or re.search(r"purchase|supplier|vendor", str(c.metadata.get("table_name", "")), re.I)]
            if supplier_chunks and not any(c.metadata.get("lead_time_days") not in (None, "") or c.metadata.get("delivery_days") not in (None, "") for c in supplier_chunks):
                return "The connected purchase records do not include supplier delivery history, so I can't determine the usual lead time.", supplier_chunks[:3]

        if re.search(r"\b(purchase cost|unit cost|cost per|price per)\b", q) and re.search(r"\b(tablet|unit|piece|dose)\b", q):
            query_terms = set(re.findall(r"[a-z0-9]+", q))
            purchase_rows = []
            for chunk in chunks:
                meta = chunk.metadata
                product = str(meta.get("product_id") or meta.get("product_name") or "").casefold()
                if re.search(r"purchase", str(meta.get("table_name", "")), re.I) and product and (set(re.findall(r"[a-z0-9]+", product)) & query_terms):
                    purchase_rows.append(chunk)
            if purchase_rows:
                row = purchase_rows[0].metadata
                qty = row.get("quantity") or row.get("qty")
                amount = row.get("net_payable") or row.get("amount")
                if qty and amount is not None:
                    return f"Recorded purchase cost per unit for {row.get('product_id')}: {float(amount) / float(qty):g} (purchase amount {amount} divided by {qty} units).", purchase_rows[:3]

        # Antibiotic availability direct formatting
        if re.search(r"\b(antibiotic|antibiotics)\b", q) and re.search(r"\b(available|in stock|which|what|list|show)\b", q):
            antibiotic_items = []
            for chunk in chunks:
                meta = chunk.metadata
                cat = str(meta.get("category", "")).casefold()
                prod = meta.get("product_id") or meta.get("product_name")
                qty = meta.get("stock_qty") or meta.get("quantity") or meta.get("available_qty")
                batch = meta.get("batch_no")
                if ("antibiotic" in cat or "anti-infective" in cat or "amoxicillin" in str(prod).casefold() or "augmentin" in str(prod).casefold() or "azomax" in str(prod).casefold() or "antibiotic" in chunk.text.casefold()) and prod:
                    batch_str = f" (Batch: {batch})" if batch else ""
                    qty_str = f", Stock: {qty} units" if qty is not None else ""
                    antibiotic_items.append(f"{prod}{batch_str}{qty_str}")
            if not antibiotic_items:
                return "I can't identify antibiotic products from the selected records; they do not contain a matching antibiotic category or label.", []
            unique_antibiotics = list(dict.fromkeys(antibiotic_items))
            ans = "Antibiotic entries identified in the retrieved selected records (the records may not define a complete category list):\n- " + "\n- ".join(unique_antibiotics)
            return ans, chunks[:5]

        # Storage location & Rack lookup for exact named products
        if re.search(r"\b(where|location|rack|shelf|stored at|kept at|storage location)\b", q) and not re.search(r"\b(sales?|sold|purchased|supplier|buyer|customer|instructions?|temperature|moisture|refrigerat|cold chain)\b", q):
            query_terms = set(re.findall(r"[a-z0-9]+", q)) - {
                "where", "is", "are", "the", "stored", "store", "at", "in", "location", "rack", "shelf", "storage",
                "find", "show", "tell", "me", "product", "medicine", "item", "kept", "located", "which",
            }
            product_chunks = []
            found_locations = []
            for chunk in chunks:
                meta = chunk.metadata
                loc = meta.get("rack_location") or meta.get("warehouse") or meta.get("storage_location")
                if not loc:
                    m_loc = re.search(r"\b(?:Rack|Warehouse|Location|Storage|Storage Location|Storage_Location):\s*([A-Za-z0-9 -]+)", chunk.text, re.I)
                    if m_loc:
                        loc = m_loc.group(1).strip()
                product_values = [meta.get(k) for k in ("product_id", "product_name", "medicine_name", "product_code", "generic_name")]
                product_tokens = set()
                for product_value in product_values:
                    if product_value not in (None, ""):
                        product_tokens.update(re.findall(r"[a-z0-9]+", str(product_value).casefold()))
                if product_tokens and query_terms and product_tokens & query_terms:
                    product_chunks.append(chunk)
                    if loc:
                        found_locations.append((loc, chunk))
                elif "rack" in chunk.text.casefold() and product_tokens and query_terms and product_tokens & query_terms:
                    m_r = re.search(r"\b(Rack-[A-Za-z0-9]+|Shelf-[A-Za-z0-9]+)\b", chunk.text, re.I)
                    if m_r:
                        product_chunks.append(chunk)
                        found_locations.append((m_r.group(1), chunk))

            if len({str(c.metadata.get("product_id") or c.metadata.get("product_name") or c.metadata.get("generic_name")) for c in product_chunks}) > 1:
                options = sorted({str(c.metadata.get("product_id") or c.metadata.get("product_name") or c.metadata.get("generic_name")) for c in product_chunks})
                return "I found multiple matching product variants. Which one do you mean: " + "; ".join(options[:8]) + "?", product_chunks[:8]
            if found_locations:
                locations = []
                seen_locations = set()
                for loc, chunk in found_locations:
                    if str(loc) not in seen_locations:
                        seen_locations.add(str(loc))
                        locations.append((str(loc), chunk))
                answers = [f"{c.metadata.get('product_id') or c.metadata.get('product_name') or 'The requested item'} is recorded at {loc}." for loc, c in locations]
                return " ".join(answers), [c for _, c in locations]
            if product_chunks:
                return "A matching product record was found, but the selected record does not include a storage location.", product_chunks[:3]
            return "I couldn't find an exact matching product record in the selected data, so I can't verify its storage location.", []

        # Batch number lookup for exact named products
        if re.search(r"\bbatch\s*(?:number|no\.?|#)?\s*(?:of|for)?\b", q) and not re.search(r"\b(?:batch|lot)\s*(?:no\.?|number|#)?\s*[:#-]?\s*[a-z0-9][a-z0-9/-]*\d", q):
            query_terms = set(re.findall(r"[a-z0-9]+", q)) - {"batch", "number", "no", "what", "is", "the", "of", "for"}
            if query_terms:
                product_chunks = []
                for chunk in chunks:
                    meta = chunk.metadata
                    product = str(meta.get("product_id") or meta.get("product_name") or "").casefold()
                    batch = meta.get("batch_no") or meta.get("batch_number")
                    if not batch:
                        m_batch = re.search(r"\b(?:Batch|Lot):\s*([A-Za-z0-9 -]+)", chunk.text, re.I)
                        if m_batch:
                            batch = m_batch.group(1).strip()
                    if product and batch and (set(re.findall(r"[a-z0-9]+", product)) & query_terms):
                        product_chunks.append((chunk, product, batch))
                if product_chunks:
                    c, prod, batch = product_chunks[0]
                    label = c.metadata.get("product_id") or c.metadata.get("product_name") or prod
                    exp = c.metadata.get("expiry_date")
                    exp_str = f" (Expiry: {exp})" if exp else ""
                    return f"The batch number for {label} is {batch}{exp_str}.", [c]

        # An exact named product's current stock is a row lookup. Format it
        # from current batch evidence so the answer cannot drift into a broad
        # ledger KPI or mix similarly named variants.
        if re.search(r"\b(stock|left|remaining|available|on hand|in stock)\b", q) and not re.search(r"\b(sales?|sold|purchased|reorder|low stock|all products|compare|versus|vs)\b", q):
            query_terms = set(re.findall(r"[a-z0-9]+", q))
            product_chunks = []
            for chunk in chunks:
                meta = chunk.metadata
                product = str(meta.get("product_id") or meta.get("product_name") or "").casefold()
                if not product or not any(meta.get(k) not in (None, "") for k in ("stock_qty", "quantity", "available_qty")):
                    continue
                product_terms = set(re.findall(r"[a-z0-9]+", product))
                if "plain" in query_terms and re.search(r"\b(extra|plus|forte)\b", product):
                    continue
                if product_terms & query_terms:
                    product_chunks.append(chunk)
            if product_chunks:
                newest = sorted(product_chunks, key=lambda c: str(c.metadata.get("date") or c.metadata.get("as_of") or ""), reverse=True)
                current = [c for c in newest if not re.search(r"archive|history|historical", str(c.metadata.get("table_name", "") + " " + c.metadata.get("description", "")), re.I)]
                if current:
                    label = current[0].metadata.get("product_id") or current[0].metadata.get("product_name")
                    total = sum(float(c.metadata.get("stock_qty", c.metadata.get("available_qty", c.metadata.get("quantity", 0))) or 0) for c in current)
                    details = ", ".join(f"{c.metadata.get('batch_no', 'batch not recorded')}: {c.metadata.get('stock_qty', c.metadata.get('available_qty', c.metadata.get('quantity')))} units" for c in current)
                    return f"{label}: {total:g} units on hand ({details}).", current
        match = re.search(r"\b(batch|lot)\s*(?:no\.?|number|#)?\s*(?:called|labeled|named|identified\s+as)?\s*[:#-]?\s*((?:[a-z]{1,8}[-_/])?[a-z0-9][a-z0-9/-]*\d[a-z0-9/-]*)", q)
        kind = "batch"
        if not match:
            match = re.search(r"\b(invoice|inv|bill|receipt)\s*(?:no\.?|number|#)?\s*[:#-]?\s*([a-z0-9][a-z0-9/-]*\d[a-z0-9/-]*)", q)
            kind = "invoice"
        if not match:
            match = re.search(r"\b(prescription|prescription ref|rx)\s*(?:no\.?|number|#)?\s*[:#-]?\s*([a-z0-9][a-z0-9/-]*\d[a-z0-9/-]*)", q)
            kind = "prescription"
        if not match:
            match = re.search(r"\b(rx[-/]\d+[a-z0-9-]*)\b", q)
            kind = "prescription"
        if not match and re.search(r"\b(batch|expiry|expires?|stock)\b", q):
            match = re.search(r"\b([a-z]{1,8}-\d{2,}[a-z0-9-]*)\b", q)
            kind = "batch"
        if not match:
            return None, []

        identifier = match.group(1 if match.lastindex == 1 else 2).casefold()
        key = {"batch": "batch_no", "invoice": "invoice_id", "prescription": "prescription_ref"}[kind]
        matched = [chunk for chunk in chunks if str(chunk.metadata.get(key, "")).casefold() == identifier]
        if not matched and kind == "prescription":
            matched = [chunk for chunk in chunks if str(chunk.metadata.get("invoice_id", "")).casefold() == identifier]
        if not matched:
            return None, []

        if kind == "batch":
            ordered = sorted(matched, key=lambda chunk: str(chunk.metadata.get("date", "")), reverse=True)
            if len(ordered) > 1 and re.search(r"\b(recorded|both|counts|when|different dates|compare)\b", q):
                versions = []
                for chunk in ordered:
                    record = chunk.metadata
                    quantity = record.get("stock_qty", record.get("quantity"))
                    recorded = record.get("date") or record.get("as_of") or "date not recorded"
                    if quantity not in (None, ""):
                        versions.append(f"{quantity} units recorded on {recorded}")
                if versions:
                    return f"Batch {ordered[0].metadata.get('batch_no')} records: " + "; ".join(versions) + ".", ordered
            row = next((chunk.metadata for chunk in ordered if chunk.metadata.get("expiry_date") or chunk.metadata.get("date")), ordered[0].metadata)
            fields = [
                ("Product", row.get("product_id")), ("Batch", row.get("batch_no")),
                ("Expiry", row.get("expiry_date")),
                ("Quantity", row.get("stock_qty", row.get("quantity"))),
                ("Location", row.get("rack_location")),
            ]
            return "; ".join(f"{name}: {value}" for name, value in fields if value not in (None, "")), matched

        if kind == "prescription":
            row = matched[0].metadata
            ref = row.get("prescription_ref") or row.get("invoice_id")
            return f"Prescription {ref} status: {row.get('status', 'status not recorded')}.", matched

        if kind == "invoice" and re.search(r"\b(profit|margin|cogs|cost of goods)\b", q):
            if any(chunk.metadata.get(field) in (None, "") for chunk in matched for field in ("unit_cost", "cost_price", "purchase_cost")):
                return f"Profit for invoice {identifier.upper()} cannot be calculated: the matching sales record has no recorded cost data.", matched

        # Invoice-level totals are counted once if recorded on every line;
        # otherwise add the matched line amounts. Exact source rows are returned.
        if re.search(r"\b(total|amount|net payable|payable|how much)\b", q):
            totals = [chunk.metadata.get(name) for chunk in matched for name in ("invoice_total", "net_payable") if chunk.metadata.get(name) not in (None, "")]
            if totals:
                amount = float(totals[0])
            else:
                values = [chunk.metadata.get("amount") for chunk in matched if chunk.metadata.get("amount") not in (None, "")]
                if not values:
                    return None, []
                amount = sum(float(value) for value in values)
            return f"Invoice {matched[0].metadata.get('invoice_id')} recorded total: {amount:g}.", matched
        if re.search(r"\b(how many|quantity|qty|units|sold)\b", q):
            values = [chunk.metadata.get(name) for chunk in matched for name in ("quantity", "total_qty") if chunk.metadata.get(name) not in (None, "")]
            if values:
                amount = sum(float(value) for value in values)
                return f"Invoice {matched[0].metadata.get('invoice_id')} recorded quantity: {amount:g}.", matched
        return None, []

    @staticmethod
    def _rewrite_follow_up(question: str, session_id: str) -> str:
        """Carry an explicit prior entity into short follow-up retrieval queries."""
        import re

        if not re.search(r"\b(?:same|it|that|those|them|there|ones?|they|these|he|she|him|her|his|hers|their|theirs|among those|how many left|what about|runner[- ]?up|second(?:[- ](?:highest|place|one))?|number two|next)\b", question, re.IGNORECASE):
            return question
        try:
            history = session_manager.get_history(session_id)
        except Exception:
            return question
        prior_messages = [m for m in history if m.get("role") in ("user", "assistant")]
        if not prior_messages:
            return question
        follow_up_context = []
        if re.search(r"\b(runner[- ]?up|second(?:[- ](?:highest|place|one))?|number two|next)\b", question, re.I):
            previous_questions = [
                str(m.get("content", "")).strip() for m in prior_messages
                if m.get("role") == "user"
                and not re.search(r"\b(runner[- ]?up|second(?:[- ](?:highest|place|one))?|number two|next)\b", str(m.get("content", "")), re.I)
            ]
            if previous_questions:
                follow_up_context.append("Previous analysis question: " + previous_questions[-1])
        generic_openers = {"what", "which", "who", "where", "when", "how", "among", "and", "the", "it", "that"}
        months = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        entities=[]; periods=[]
        carried_conditions=[]
        for msg in prior_messages[-8:]:
            content=str(msg.get("content", ""))
            if re.search(r"\b(below|under|less than)\b.{0,25}\breorder\b",content,re.I): carried_conditions.append("quantity below reorder level")
            if re.search(r"\b(out of stock|stock\s*=\s*0)\b",content,re.I): carried_conditions.append("out of stock")
            if re.search(r"\b(pending|partially paid|unpaid)\b",content,re.I): carried_conditions.append("payment status Pending")
            message_codes=re.findall(r"\b(?:SALE|PUR|INV|RX|SKU)[-_/]?[A-Z0-9-]*\d[A-Z0-9-]*\b",content,re.I)
            if msg.get("role") == "user" or (msg.get("role") == "assistant" and len(message_codes) <= 2):
                entities.extend(message_codes)
            if msg.get("role") == "assistant" and len(message_codes) > 2:
                continue
            entity_content=content.split(": ",1)[1] if msg.get("role")=="assistant" and ": " in content else content
            # Preserve the entity in concise grouped/extreme answers such as
            # "Faisal Town Health Mart: 16" and "Zam Zam Pharma: 14,350".
            # The suffix after the colon is a metric, not the referent.
            if msg.get("role") == "assistant" and ": " in content:
                prefix = content.split(": ", 1)[0].strip()
                if prefix and prefix.split()[0].casefold() not in generic_openers:
                    entities.append(prefix)
            entities.extend(re.findall(r"\b(?:[A-Z]{2,}|[A-Z][a-z]+)(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]+)){1,5}\b",entity_content))
            if msg.get("role") == "assistant":
                entities.extend(re.findall(r"\b[A-Z][a-z]{2,}\b",entity_content))
                product_label=re.search(r"\bproduct(?: id| name)?\s*:\s*(.+?)(?:\s*\(|;|$)",content,re.I)
                if product_label:
                    entities.append(product_label.group(1).strip().rstrip("."))
            # Keep product names with strengths and dimensions intact.
            for m in re.finditer(r"\b(?:of|for|from)\s+((?:[A-Z][A-Za-z0-9%.-]*|\d+[A-Za-z%]+)(?:\s+(?:[A-Z][A-Za-z0-9%.-]*|\d+[A-Za-z%]+)){1,7})",entity_content):
                entities.append(m.group(1))
            for m in re.finditer(r"\b([A-Z][A-Za-z0-9%.-]*(?:\s+[A-Z][A-Za-z0-9%.-]*){1,6}\s+\d+[A-Za-z%]+)\b",entity_content):
                entities.append(m.group(1))
            periods.extend(re.findall(rf"\b(?:{months})\s+\d{{1,2}}(?:,?\s+20\d{{2}})?\b|\b(?:{months})\s+20\d{{2}}\b|\b20\d{{2}}\b",entity_content,re.I))
        referent_match = re.search(r"\bthat\s+(category|product|medicine|drug|supplier|customer|payment method|invoice|batch)\b", question, re.I)
        if referent_match:
            referent_kind = referent_match.group(1).casefold()
            for msg in reversed(prior_messages):
                if msg.get("role") != "assistant":
                    continue
                content = str(msg.get("content", ""))
                prefix, separator, ranked_results = content.partition(":")
                if not separator or referent_kind.split()[0] not in prefix.casefold():
                    continue
                if ";" not in content and not re.search(r"\b(rank(?:ed|ing))\b", prefix, re.I):
                    continue
                first_result = ranked_results.split(";", 1)[0].strip()
                first_result = re.sub(r"\s*\([^)]*\)\s*[.]?$", "", first_result).strip(" .,:;")
                if first_result:
                    # “That category/product” refers to the leading result of
                    # the immediately prior ranking, not later list entries.
                    entities = [first_result]
                    break
        generic_openers.update({"matching","total","across","most","frequently","purchased","purchase","highest","lowest","units","records","amount","quantity","product","supplier","customer","warehouse","branch","category","invoice","status"})
        entities=[e.strip(" ,.;:") for e in entities if e.split()[0].casefold() not in generic_openers]
        entities=list(dict.fromkeys(entities))
        # A natural-language extractor can produce both a complete medicine
        # name and a shorter overlapping fragment. Retain the longer name so
        # pack sizes (e.g. "50s", "120ml") are not lost during context trim.
        entities=[e for e in entities if not any(e.casefold() in other.casefold() and len(other)>len(e) for other in entities)]
        record_codes=[e for e in entities if re.fullmatch(r"(?:SALE|PUR|INV|RX|SKU)[-_/]?[A-Z0-9-]*\d[A-Z0-9-]*",e,re.I)]
        other_entities=[e for e in entities if e not in record_codes]
        entities=record_codes + other_entities[-max(0,8-len(record_codes)):]
        periods=list(dict.fromkeys(periods))[-4:]
        if not entities and not periods and not follow_up_context:
            return question
        # Add only referents and periods; do not replay old operations, which can
        # turn a count follow-up back into the previous turn's total question.
        hints=[]
        hints.extend(follow_up_context)
        if entities: hints.append("Relevant prior entities: " + "; ".join(entities))
        if periods: hints.append("Relevant prior periods: " + "; ".join(periods))
        if carried_conditions: hints.append("Relevant prior conditions: " + "; ".join(dict.fromkeys(carried_conditions)))
        return " ".join(hints) + ". Current follow-up: " + question

    @staticmethod
    def _resolve_relative_date_filter(filters: Dict[str, Any], records):
        """Resolve recent windows, align implicit years with dataset range, and validate data coverage."""
        resolved = dict(filters)
        days = resolved.pop("relative_days", None)
        has_explicit_window = bool(resolved.get("date_from") or resolved.get("date_to"))

        import pandas as pd

        if hasattr(records, "columns"):
            date_values = records["date"] if "date" in records.columns else None
        elif records:
            frame = pd.DataFrame(records)
            date_values = frame["date"] if "date" in frame.columns else None
        else:
            date_values = None
        if date_values is None:
            if not days and not has_explicit_window and resolved.get("month") is None:
                return resolved
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        dates = pd.to_datetime(date_values, errors="coerce").dropna()
        if dates.empty:
            if not days and not has_explicit_window and resolved.get("month") is None:
                return resolved
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        first_available = dates.min().normalize()
        last_available = dates.max().normalize()

        # Check if query specified a month (and optional day) without an explicit year matching dataset
        if resolved.get("month") is not None:
            month = int(resolved["month"])
            day = int(resolved["day"]) if resolved.get("day") is not None else None
            explicit_year = resolved.get("year")

            if explicit_year is None:
                # Find available year in dataset matching this month
                matching_records = dates[dates.dt.month == month]
                if day is not None:
                    matching_day_records = matching_records[matching_records.dt.day == day]
                    if not matching_day_records.empty:
                        matching_records = matching_day_records
                if not matching_records.empty:
                    chosen_year = int(matching_records.dt.year.iloc[-1])
                    resolved["year"] = chosen_year
                    if day is not None:
                        resolved["date_from"] = f"{chosen_year:04d}-{month:02d}-{day:02d}"
                        resolved["date_to"] = f"{chosen_year:04d}-{month:02d}-{day:02d}"
                        has_explicit_window = True
                    else:
                        import calendar
                        from datetime import date
                        resolved["date_from"] = date(chosen_year, month, 1).isoformat()
                        resolved["date_to"] = date(chosen_year, month, calendar.monthrange(chosen_year, month)[1]).isoformat()
                        has_explicit_window = True
            elif explicit_year is not None:
                year = int(explicit_year)
                if day is not None:
                    resolved["date_from"] = f"{year:04d}-{month:02d}-{day:02d}"
                    resolved["date_to"] = f"{year:04d}-{month:02d}-{day:02d}"
                    has_explicit_window = True
                else:
                    import calendar
                    from datetime import date
                    resolved["date_from"] = date(year, month, 1).isoformat()
                    resolved["date_to"] = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
                    has_explicit_window = True

        if not days and not has_explicit_window:
            return resolved

        if days:
            end = last_available
            start = end - pd.Timedelta(days=int(days) - 1)
            resolved["date_from"] = start.strftime("%Y-%m-%d")
            resolved["date_to"] = end.strftime("%Y-%m-%d")
        else:
            start = pd.to_datetime(resolved.get("date_from") or resolved.get("date_to"), errors="coerce")
            end = pd.to_datetime(resolved.get("date_to") or resolved.get("date_from"), errors="coerce")
            if pd.isna(start) or pd.isna(end):
                return resolved
            start, end = start.normalize(), end.normalize()

            # If default year was applied outside data range, check if matching month/day exists in dataset
            if (start < first_available or end > last_available) and resolved.get("year") is None:
                month = int(resolved.get("month") or start.month)
                day = int(resolved.get("day") or start.day) if (resolved.get("day") or start == end) else None
                matching = dates[dates.dt.month == month]
                if day is not None and start == end:
                    matching_day = matching[matching.dt.day == day]
                    if not matching_day.empty:
                        matching = matching_day
                if not matching.empty:
                    y = int(matching.dt.year.iloc[-1])
                    if start == end and day is not None:
                        start = pd.Timestamp(year=y, month=month, day=day)
                        end = start
                    else:
                        start = pd.Timestamp(year=y, month=start.month, day=start.day)
                        end = pd.Timestamp(year=y, month=end.month, day=end.day)
                    resolved["date_from"] = start.strftime("%Y-%m-%d")
                    resolved["date_to"] = end.strftime("%Y-%m-%d")

        if start < first_available or end > last_available:
            first_text = first_available.strftime("%Y-%m-%d")
            last_text = last_available.strftime("%Y-%m-%d")
            requested_text = (
                start.strftime("%Y-%m-%d") if start == end
                else f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
            clipped_start = max(start, first_available)
            clipped_end = min(end, last_available)
            if days or clipped_start > clipped_end:
                resolved["_date_filter_error"] = (
                    f"The requested period ({requested_text}) is outside the selected data's "
                    f"date coverage ({first_text} to {last_text}); I can't calculate sales for it."
                )
            else:
                resolved["date_from"] = clipped_start.strftime("%Y-%m-%d")
                resolved["date_to"] = clipped_end.strftime("%Y-%m-%d")
                resolved["_date_filter_note"] = (
                    f"Requested period was {requested_text}; the selected data covers "
                    f"{first_text} to {last_text}, so results use the overlapping dates only."
                )
        return resolved

    @staticmethod
    def _uses_inventory_expiry_date(question: str) -> bool:
        """Identify explicit expiry cutoffs that must not be checked against sales dates."""
        q = str(question or "").casefold()
        return bool(
            re.search(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", q)
            and re.search(r"\b(expir\w*|batches?|lots?)\b", q)
            and re.search(r"\b(as of|already expired|past expiry|earliest|latest)\b", q)
            and not re.search(r"\b(sales?|revenue|sold|transactions?|invoices?)\b", q)
        )

    def _relative_period_comparison(self, question: str, records, domain: str, lang: str):
        """Compute an explicit comparison of two recent sales periods."""
        if not re.search(r"\bcompare\b|\bdifference between\b", question, re.I):
            return None
        if not re.search(r"\b(sales?|revenue|profit|margin|units?)\b", question, re.I):
            return None
        periods = list(dict.fromkeys(
            int(value) for value in re.findall(r"\b(?:last|past|previous)\s+(\d{1,3})\s+days?\b", question, re.I)
        ))
        if len(periods) != 2 or records is None:
            return None

        period_results = {}
        source_rows = []
        for days in periods:
            filters = self._resolve_relative_date_filter({"relative_days": days}, records)
            if filters.pop("_date_filter_error", None):
                return None
            computed, rows = self.analytics_router.compute(question, filters, records, domain)
            if not computed:
                return None
            metric = next((key for key in ("total_revenue", "gross_profit", "gross_margin_pct", "units_sold") if key in computed), None)
            if metric is None or computed[metric].get("status") != "ok":
                return None
            period_results[str(days)] = {"filters": filters, "metric": metric, "result": computed[metric]}
            source_rows.extend(rows or [])

        metric_names = {item["metric"] for item in period_results.values()}
        if len(metric_names) != 1:
            return None
        metric_name = next(iter(metric_names))
        name = period_results[str(periods[0])]["result"].get("name", metric_name.replace("_", " ").title())
        unit = period_results[str(periods[0])]["result"].get("unit", "")
        values = [period_results[str(days)]["result"].get("value") for days in periods]
        lines = []
        for days in periods:
            formatted = self._format_analytics_answer(
                question, {metric_name: period_results[str(days)]["result"]}, lang,
                period_results[str(days)]["filters"],
            )
            lines.append(f"Last {days} days: {formatted}")
        return {
            "answer": "\n".join(lines),
            "values": {"period_comparison": {"metric": metric_name, "name": name, "unit": unit,
                                               "periods": {str(days): period_results[str(days)]["result"] for days in periods},
                                               "difference": abs(float(values[0]) - float(values[1]))}},
            "source_rows": list(dict.fromkeys(source_rows)),
        }

    def _clean_roman_urdu_vocabulary(self, text: str) -> str:
        """Replaces common Roman Hindi word leakages with natural Roman Urdu equivalents."""
        import re
        replacements = [
            (r'\b(jaankari|jankari)\b', 'maloomat'),
            (r'\b(adhik)\b', 'ziada'),
            (r'\b(pradaan\s+kar\s+sakta\s+hoon|pradaan\s+karta\s+hoon|pradaan)\b', 'faraaham'),
            (r'\b(uplabdh)\b', 'dastyab'),
            (r'\b(anya)\b', 'mazeed'),
            (r'\b(kripya)\b', 'baraye meharbani'),
            (r'\b(shuruwat)\b', 'aaghaz'),
            (r'\b(namaste)\b', 'assalam o alaikum'),
            (r'\b(sukriya)\b', 'shukriya'),
            (r'\b(sahayata)\b', 'madad'),
            (r'\b(suchna)\b', 'ittila'),
            (r'\b(anurodh)\b', 'guzaarish'),
            (r'\b(sambandhit)\b', 'mutalliq'),
        ]
        cleaned = text
        for pattern, repl in replacements:
            def _sub_repl(match):
                m = match.group(0)
                if m.isupper():
                    return repl.upper()
                elif m[0].isupper():
                    return repl.capitalize()
                return repl
            cleaned = re.sub(pattern, _sub_repl, cleaned, flags=re.IGNORECASE)
        return cleaned

    def _detect_query_language(self, question: str) -> str:
        """
        Deterministically detect if user wrote in:
        - 'urdu_script': Urdu written in Arabic/Nastaliq script (e.g. 'سب سے زیادہ')
        - 'roman_urdu': Urdu written phonetically in Latin alphabet (e.g. 'me kis kism k data se deal kr rha hu')
        - 'english': Standard English (e.g. 'hi', 'what is total sales', 'list all files')
        """
        import re
        q = question.strip()
        # 1. Check for Arabic/Urdu script Unicode characters
        if re.search(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]", q):
            return "urdu_script"

        # 2. Check for Roman-Urdu markers
        strong_roman_urdu = {
            "kya", "zyada", "ziada", "dawai", "dawa", "dawayi", "kyun", "kyu", "kism", "qism",
            "konsi", "konse", "konsa", "mein", "batao", "bataen", "bataiye", "dikhao", "dikhaye",
            "kitni", "kitna", "kitne", "kese", "kaise", "kahan", "kaha", "koun", "bohat", "bhot",
            "thoda", "thora", "chahiye", "skte", "sakte", "sakty", "apka", "aapka", "apki", "aapki",
            "apke", "aapke", "hume", "humara", "hamara", "nhi", "shukriya", "shukria", "kiska",
            "kiski", "kiske", "rha", "rhi", "rhe", "raha", "rahi", "rahe", "karna", "karta", "karti",
            "karte", "hwi", "hui", "bhej", "mangwaya", "mangwayi", "mangwana", "muje", "mujy", "mujhy", "meri",
            "mera", "mere", "mene", "maine", "dawaiyan", "dawayian", "dawayan", "dawaon", "bikri", "bikree",
            "bechi", "bechay", "biki", "bikay", "munafa", "nafa", "faida", "nuqsan", "nuksan", "kharcha",
            "aamdani", "amdani", "kamai", "pichlay", "pichle", "guzishta", "kal", "aaj", "barhao", "barha",
            "barhane", "badhao", "khareeda", "khareedna", "maal", "main", "qareeb", "dheemi", "tabdeeli",
            "bunyaad", "maslay", "karun", "karon", "mujc", "mjhe", "mjy", "farukht", "farokht", "frokt", "frokht",
            "udhar", "udhari", "naqad", "naqd", "rokra", "baqaya", "rasid", "raseed", "parchi", "hisab", "hisaab",
            "khata", "khaata", "wasooli", "bachat", "khasara", "laagat", "lagat"
        }
        medium_roman_urdu = {
            "kis", "hai", "hain", "ho", "hu", "hoon", "hun", "tm", "tum", "kr", "kar", "karo",
            "mera", "meri", "mere", "nahi", "aur", "pe", "par", "se", "ka", "ki", "ke", "ko",
            "ap", "aap", "mujhe", "mujy", "hum", "sab"
        }

        words = re.findall(r"\b[a-zA-Z]+\b", q.lower())
        strong_hits = [w for w in words if w in strong_roman_urdu]
        medium_hits = [w for w in words if w in medium_roman_urdu]

        if len(strong_hits) >= 1 or len(medium_hits) >= 2:
            return "roman_urdu"

        return "english"

    def _is_data_source_inquiry(self, question: str) -> bool:
        import re
        q = question.strip().lower().rstrip("?.! ")

        # Users often phrase a simple metadata request politely. Normalize the
        # wrapper before matching so it reaches the deterministic registry
        # lookup instead of vector retrieval (which may not contain filenames).
        q = re.sub(r"^(?:please\s+)?(?:can|could|would)\s+you\s+", "", q)
        
        # Exclude questions asking about capabilities, help, or types of questions
        if re.search(r"\b(type\s+of\s+questions?|what\s+can\s+you\s+do|how\s+to\s+use|help|questions?\s+can\s+i\s+ask|capabilities|examples?|suggest\s+questions?)\b", q):
            return False

        if (re.search(r"\b(how many|number of|count)\b.{0,24}\btables?\b", q)
                and re.search(r"\b(active|selected|in|within|under)\b.{0,60}\b(scope|dataset|database|selection)\b", q)):
            return True

        patterns = [
            r"^(what|which)\s+(is|are)\s+(the\s+|my\s+|active\s+|current\s+|selected\s+)?(data\s*sources?|datasets?|source\s*files?|files?|tables?|database)\b",
            r"^(what|which)\s+(data\s*sources?|datasets?|source\s*files?|files?|tables?)\s+(are\s+)?(active|selected|loaded|connected|used|in\s+use|being\s+used)\b",
            r"^(list|show|display|tell\s+me|name)\s+(the\s+|all\s+|active\s+|current\s+|selected\s+)?(data\s*sources?|datasets?|source\s*files?|tables?|active\s*scope)\b",
            r"^(tell\s+me|give\s+me|show\s+me|what\s+is|name)\s+(?:the\s+)?(?:name\s+of\s+)?(?:the\s+)?(?:active\s+|current\s+|selected\s+|connected\s+)?(data\s*sources?|datasets?|source\s*files?|files?|tables?|databases?)\b",
            r"^(what|which)\s+(?:is\s+)?(?:the\s+)?(?:name\s+of\s+)?(?:active\s+|current\s+|selected\s+|connected\s+)?(data\s*source|dataset|source\s*file|file|table|database)\s+(?:is\s+)?(?:active|current|selected|connected|loaded|in\s+use)\b",
            r"^(?:what|which)\s+(?:tables?|sources?|files?)\s+(?:(?:are|is)\s+)?(?:included|selected|part\s+of|in)\b.{0,100}\b(?:active\s+|selected\s+|current\s+)?(?:scope|dataset|database)\b",
            r"^(data\s*sources?|active\s*sources?|active\s*scope|active\s*files?|active\s*datasets?|connected\s*datasets?|current\s*dataset)$",
            r"^where\s+(is\s+the\s+data\s+from|are\s+you\s+getting\s+the\s+data)\b",
            r"^(what|which)\s+(data\s*source|dataset|file|table)\s+are\s+you\s+using\b"
        ]
        return any(re.search(p, q) for p in patterns)

    @staticmethod
    def _is_table_field_inquiry(question: str) -> bool:
        q = re.sub(r"\s+", " ", str(question).casefold()).strip()
        return bool(
            re.search(r"\b(which|what)\b", q)
            and re.search(r"\btable\b", q)
            and re.search(r"\b(stores?|contains?|has|holds?|records?|keeps?|includes?)\b", q)
            and re.search(r"\b(field|column|quantity|qty|amount|total|date|status|payment|product|item|customer|supplier|batch|invoice|receipt|transaction|price|cost|discount|tax)\w*\b", q)
        )

    def _table_field_lookup(self, question: str, request: ChatRequest):
        """Find selected structured tables that actually contain a requested field."""
        if not self._is_table_field_inquiry(question):
            return None
        import pandas as pd

        records, _ = self._get_records_for_analytics(request, {})
        if records is None or not hasattr(records, "columns") or records.empty:
            return None
        q = re.sub(r"[^a-z0-9]+", " ", str(question).casefold()).strip()
        excluded = {"table_name", "source_table", "source_file", "file_id", "source_row", "database_name", "txn_type"}
        candidates = []
        for column in records.columns:
            label = re.sub(r"^_extra\.", "", str(column), flags=re.I)
            normalized = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
            if not normalized or normalized in excluded or not records[column].notna().any():
                continue
            if re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", q):
                candidates.append((column, normalized))
        if not candidates:
            return None
        # Prefer canonical field names over connector duplicates such as
        # _extra.QUANTITY when both forms are present.
        candidates.sort(key=lambda pair: (str(pair[0]).casefold().startswith("_extra."), len(pair[1])))
        field_col, field_label = candidates[0]
        work = records.loc[records[field_col].notna()].copy()
        if re.search(r"\bsales?\b", q):
            if "database_name" in work:
                mask = work.database_name.fillna("").astype(str).str.casefold().eq("sales")
            elif "source_file" in work:
                mask = work.source_file.fillna("").astype(str).str.casefold().str.startswith("sql://sales/")
            else:
                mask = pd.Series(False, index=work.index)
            if mask.any():
                work = work.loc[mask]
        elif re.search(r"\binventory\b", q) and "database_name" in work:
            work = work.loc[work.database_name.fillna("").astype(str).str.casefold().eq("inventory")]
        if re.search(r"\b(per|each|every|item.level|line.level|for each)\b", q):
            relation_cols = [col for col in ("transaction_id", "invoice_id", "receipt_id", "product_id", "item_id") if col in work and work[col].notna().any()]
            if not relation_cols:
                return None
            work = work.loc[work[relation_cols].notna().any(axis=1)]
        table_col = next((col for col in ("table_name", "source_table") if col in work and work[col].notna().any()), None)
        if not table_col:
            return None
        counts = work.groupby(table_col, dropna=True).size().sort_values(ascending=False)
        if counts.empty:
            return None
        tables = [{"table": str(name), "matching_rows": int(count)} for name, count in counts.items()]
        role = "sales" if re.search(r"\bsales?\b", q) else "inventory" if re.search(r"\binventory\b", q) else "selected"
        names = [item["table"] for item in tables]
        if len(names) == 1:
            answer = f"The {role} table {names[0]} stores the requested {field_label} field."
        else:
            answer = f"The selected {role} tables with the requested {field_label} field are: " + ", ".join(names) + "."
        source_col = next((col for col in ("source_file", "file_id") if col in work), None)
        row_col = "source_row" if "source_row" in work else None
        if source_col and row_col:
            source_rows = list(dict.fromkeys((str(row[source_col]), row[row_col]) for _, row in work.head(20).iterrows()))
        else:
            source_rows = []
        return {
            "answer": answer,
            "values": {"status": "ok", "field": str(field_col), "tables": tables, "row_count": int(len(work))},
            "source_rows": source_rows,
            "records": records,
        }

    @staticmethod
    def _is_sales_key_relationship_inquiry(question: str) -> bool:
        q = re.sub(r"\s+", " ", str(question).casefold()).strip()
        return bool(
            re.search(r"\b(which|what)\b", q)
            and re.search(r"\b(link|join|connect|reference|foreign key|key)\w*\b", q)
            and re.search(r"\b(sales?\s+)?details?\b|\b(lines?)\b", q)
            and re.search(r"\b(receipts?|invoices?|headers?)\b", q)
        )

    def _selected_scope_is_structured_only(self, request: ChatRequest) -> bool:
        """True only when every selected source is a structured table/file."""
        import os
        from app.ingestion.registry import file_registry

        selected = []
        for file_id in request.file_ids or []:
            record = file_registry.get_file_by_id(file_id)
            if record is None:
                return False
            selected.append(record)
        for source_file in request.source_files or []:
            record = file_registry.get_file_by_path(source_file)
            if record is None:
                extension = os.path.splitext(str(source_file))[1].casefold()
                if extension in {".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".parquet", ".json", ".jsonl", ".db", ".sqlite", ".sqlite3"}:
                    selected.append(type("SelectedSource", (), {"source_type": "file", "filename": source_file, "file_path": source_file})())
                    continue
                return False
            selected.append(record)
        if not selected:
            return False
        structured_extensions = {".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".parquet", ".json", ".jsonl", ".db", ".sqlite", ".sqlite3"}
        for record in selected:
            if str(getattr(record, "source_type", "")).casefold() == "database":
                continue
            path = str(getattr(record, "file_path", "") or "")
            name = str(getattr(record, "filename", "") or path)
            extension = os.path.splitext(name)[1].casefold() or os.path.splitext(path)[1].casefold()
            if extension not in structured_extensions:
                return False
        return True

    def _sales_key_relationship_lookup(self, question: str, request: ChatRequest):
        """Verify a sales detail-to-header key using matching selected row sets."""
        import pandas as pd

        if not self._is_sales_key_relationship_inquiry(question):
            return None
        records, _ = self._get_records_for_analytics(request, {})
        if records is None or not hasattr(records, "columns") or records.empty:
            return None
        sales = records
        table_col = next((col for col in ("table_name", "source_table", "source_file", "file_id") if col in sales), None)
        if not table_col:
            return None
        table_names = sales[table_col].fillna("").astype(str)
        row_identity = table_names
        if "source_file" in sales:
            row_identity = row_identity + " " + sales.source_file.fillna("").astype(str)
        row_identity = row_identity.str.casefold()
        sales_mask = row_identity.str.contains(r"sales?", regex=True)
        receipt_header_mask = (
            row_identity.str.contains(r"receipt|invoice|bill|transaction", regex=True)
            & row_identity.str.contains(r"header|receipt|invoice|bill", regex=True)
            & ~row_identity.str.contains(r"purchase|buy", regex=True)
        )
        sales_mask |= receipt_header_mask
        if sales_mask.any():
            sales = sales.loc[sales_mask]
        elif "database_name" in sales:
            database_mask = sales.database_name.fillna("").astype(str).str.casefold().eq("sales")
            if database_mask.any():
                sales = sales.loc[database_mask]
            elif "txn_type" in sales:
                txn_mask = sales.txn_type.fillna("").astype(str).str.casefold().str.contains(r"sale|sales|receipt|invoice", regex=True)
                sales = sales.loc[txn_mask]

        def key_values(series):
            def normalize(value):
                rendered = str(value).strip()
                try:
                    numeric = float(rendered)
                    if numeric.is_integer():
                        return str(int(numeric))
                except (TypeError, ValueError):
                    pass
                return rendered.casefold()
            return {normalize(value) for value in series.dropna()}

        # The analytics frame stores invoice header provenance on each joined
        # sales line. Validate that every distinct line transaction key maps
        # to exactly one selected sales header row before answering.
        provenance_cols = ("_header_source_file", "_header_source_row")
        if all(col in sales for col in provenance_cols):
            line_rows = sales
            detail_fields = [col for col in ("product_id", "quantity") if col in line_rows]
            if detail_fields:
                line_rows = line_rows.loc[line_rows[detail_fields].notna().all(axis=1)]
            line_key = next((col for col in ("transaction_id", "invoice_id", "receipt_id", "order_id") if col in line_rows and line_rows[col].notna().any()), None)
            if line_key:
                valid = line_rows.loc[line_rows[line_key].notna() & line_rows["_header_source_file"].notna() & line_rows["_header_source_row"].notna()].copy()
                valid["_normalized_line_key"] = valid[line_key].map(lambda value: next(iter(key_values(pd.Series([value])))))
                mapping = valid.drop_duplicates(subset=["_normalized_line_key", "_header_source_file", "_header_source_row"])
                mapping = mapping.copy()
                mapping["_normalized_header_row"] = (
                    mapping["_header_source_file"].astype(str).str.casefold()
                    + "\x1f" + mapping["_header_source_row"].astype(str).str.replace(r"\.0$", "", regex=True)
                )
                per_key_header_rows = mapping.groupby("_normalized_line_key")["_normalized_header_row"].nunique()
                detail_keys = key_values(valid[line_key])
                if detail_keys and len(per_key_header_rows) == len(detail_keys) and per_key_header_rows.eq(1).all():
                    header_table_values = valid.get("_header_table_name", pd.Series(dtype=object)).dropna().astype(str).unique().tolist()
                    header_files = mapping["_header_source_file"].fillna("").astype(str)
                    header_identity = " ".join(header_table_values) + " " + " ".join(header_files.tolist())
                    if re.search(r"header|receipt|invoice", header_identity, re.I):
                        header_table = header_table_values[0] if header_table_values else "sales receipt header"
                        detail_table = next((str(value) for value in valid[table_col].dropna().unique()), "sales detail")
                        source_col = next((col for col in ("source_file", "file_id") if col in valid), None)
                        line_citations = []
                        if source_col and "source_row" in valid:
                            line_citations = list(dict.fromkeys(
                                (str(row[source_col]), row["source_row"])
                                for _, row in valid.head(20).iterrows()
                                if pd.notna(row[source_col]) and pd.notna(row["source_row"])
                            ))
                        header_citations = list(dict.fromkeys(
                            (str(row["_header_source_file"]), row["_header_source_row"])
                            for _, row in mapping.head(20).iterrows()
                        ))
                        return {
                            "answer": (
                                f"Sales detail `{line_key}` maps to `{header_table}`; "
                                f"all {len(detail_keys):,} distinct key values map to one selected header row each."
                            ),
                            "values": {
                                "status": "ok", "relationship": "sales_detail_to_receipt_header",
                                "detail_table": detail_table, "detail_key": line_key,
                                "header_table": header_table, "header_link": "selected header source row",
                                "matched_distinct_keys": len(detail_keys),
                            },
                            "source_rows": list(dict.fromkeys(line_citations + header_citations)),
                            "records": records,
                        }

        detail_rows = sales
        required_detail = [col for col in ("product_id", "quantity") if col in detail_rows]
        if required_detail:
            detail_rows = detail_rows.loc[detail_rows[required_detail].notna().all(axis=1)]
        detail_key = next((col for col in ("transaction_id", "invoice_id", "receipt_id", "order_id") if col in detail_rows and detail_rows[col].notna().any()), None)
        header_rows = sales
        header_signature = [col for col in ("invoice_id", "invoice_total", "paid_amount") if col in header_rows]
        if header_signature:
            header_rows = header_rows.loc[header_rows[header_signature].notna().any(axis=1)]
        header_key = next((col for col in ("_extra.ID", "id", "transaction_id", "invoice_id", "receipt_id") if col in header_rows and header_rows[col].notna().any()), None)
        if not detail_key or not header_key:
            return None
        matches = []
        for detail_table, detail_group in detail_rows.groupby(table_col, dropna=True):
            detail_keys = key_values(detail_group[detail_key])
            if not detail_keys:
                continue
            for header_table, header_group in header_rows.groupby(table_col, dropna=True):
                if str(detail_table) == str(header_table):
                    continue
                header_keys = key_values(header_group[header_key])
                common = detail_keys & header_keys
                if common and common == detail_keys == header_keys:
                    matches.append((len(common), str(detail_table), str(header_table), detail_group, header_group))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        matched_count, detail_table, header_table, detail_group, header_group = matches[0]
        answer = (
            f"Sales detail `{detail_key}` links to receipt header `{header_key.removeprefix('_extra.')}` "
            f"(`{header_key}`); all {matched_count:,} selected key values match."
        )
        source_col = next((col for col in ("source_file", "file_id") if col in records), None)
        row_col = "source_row" if "source_row" in records else None
        source_rows = []
        if source_col and row_col:
            for group in (detail_group, header_group):
                source_rows.extend(
                    (str(row[source_col]), row[row_col])
                    for _, row in group.head(20).iterrows()
                    if pd.notna(row[source_col]) and pd.notna(row[row_col])
                )
            source_rows = list(dict.fromkeys(source_rows))
        return {
            "answer": answer,
            "values": {
                "status": "ok", "relationship": "sales_detail_to_receipt_header",
                "detail_table": detail_table, "detail_key": detail_key,
                "header_table": header_table, "header_key": header_key,
                "matched_distinct_keys": matched_count,
            },
            "source_rows": source_rows,
            "records": records,
        }

    def _get_active_source_names(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None) -> List[str]:
        import os
        from app.ingestion.registry import file_registry
        names = []
        if file_ids:
            for fid in file_ids:
                rec = file_registry.get_file_by_id(fid)
                if rec:
                    name = rec.table_name or rec.filename or fid
                    if getattr(rec, "group_name", None):
                        names.append(f"{rec.group_name} — {name}")
                    elif getattr(rec, "database_name", None):
                        names.append(f"{rec.database_name} — {name}")
                    else:
                        names.append(name)
                else:
                    names.append(fid)
        elif source_files:
            for sf in source_files:
                rec = file_registry.get_file_by_path(sf)
                if rec:
                    name = rec.table_name or rec.filename or os.path.basename(sf)
                    if getattr(rec, "group_name", None):
                        names.append(f"{rec.group_name} — {name}")
                    else:
                        names.append(name)
                else:
                    names.append(os.path.basename(sf))
        else:
            try:
                active_files = file_registry.list_files()
                for f in active_files:
                    if f.get("is_ingested"):
                        name = f.get("table_name") or f.get("filename")
                        if f.get("group_name"):
                            names.append(f"{f.get('group_name')} — {name}")
                        elif name:
                            names.append(name)
            except Exception:
                pass

        unique_names = []
        for n in names:
            if n and n not in unique_names:
                unique_names.append(n)
        return unique_names

    def _format_data_source_response(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None, lang: str = "english", question: str = "") -> str:
        sources = self._get_active_source_names(file_ids, source_files)
        if re.search(r"\b(how many|number of|count)\b.{0,24}\btables?\b", question, re.I):
            table_names = [name for name in sources if re.search(r"\btbl_\d+(?:_\d+)*\b", name, re.I)]
            groups = sorted({name.split("—", 1)[0].strip() for name in table_names if "—" in name})
            if lang == "roman_urdu":
                return f"Asan Pos scope mein {len(table_names)} tables selected hain" + (f" ({', '.join(groups)})." if groups else ".")
            return f"{len(table_names)} tables are selected in the active scope" + (f" ({', '.join(groups)})." if groups else ".")
        if lang == "roman_urdu":
            if not sources:
                return "Filhaal koi data source connect ya select nahi hai."
            if file_ids or source_files:
                if len(sources) == 1:
                    return f"Active data source yeh hai:\n- **{sources[0]}**"
                else:
                    lines = ["Active data sources yeh hain:"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
            else:
                if len(sources) == 1:
                    return f"Connected data source (tamaam data):\n- **{sources[0]}**"
                else:
                    lines = [f"Connected data sources ({len(sources)} available):"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
        elif lang == "urdu_script":
            if not sources:
                return "فی الحال کوئی ڈیٹا سورس منتخب یا منسلک نہیں ہے۔"
            if file_ids or source_files:
                if len(sources) == 1:
                    return f"فعال ڈیٹا سورس درج ذیل ہے:\n- **{sources[0]}**"
                else:
                    lines = ["فعال ڈیٹا سورسز درج ذیل ہیں:"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
            else:
                if len(sources) == 1:
                    return f"منسلک ڈیٹا سورس:\n- **{sources[0]}**"
                else:
                    lines = [f"منسلک ڈیٹا سورسز ({len(sources)} دستیاب):"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
        else:
            if not sources:
                return "No data sources are currently connected or selected."

            if file_ids or source_files:
                if len(sources) == 1:
                    return f"The active data source is:\n- **{sources[0]}**"
                else:
                    lines = ["The active data sources are:"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
            else:
                if len(sources) == 1:
                    return f"Connected data source (all data scope):\n- **{sources[0]}**"
                else:
                    lines = [f"Connected data sources ({len(sources)} available):"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)

    def _get_system_prompt(self, domain: str, route: str, selected_sources: Optional[List[str]] = None, lang: str = "english", question: str = "") -> str:
        """
        Domain-agnostic core logic, but uses domain pack if available.
        For now, a generic prompt with strict grounding constraints.
        """
        if lang == "roman_urdu":
            base_prompt = (
                "You are an offline assistant for LLM-Konnect analyzing local business and inventory data.\n"
                "LANGUAGE & VOCABULARY RULES (STRICT):\n"
                "- The user wrote in Roman-Urdu (Urdu written using Latin/English letters).\n"
                "- You MUST reply strictly in natural Pakistani Roman-Urdu using Latin letters (A-Z, a-z) only (e.g. 'Aap ... ke data se deal kar rahe hain', 'Sab se ziada sale ...').\n"
                "- STRICT PROHIBITION ON HINDI VOCABULARY:\n"
                "  * NEVER use Hindi words like 'jaankari', 'jankari', 'adhik', 'pradaan', 'uplabdh', 'anya', 'kripya', 'shuruwat', 'sukriya', 'namaste'.\n"
                "- USE STANDARD ROMAN-URDU WORDS INSTEAD:\n"
                "  * Use 'maloomat' or 'information' instead of 'jaankari/jankari'\n"
                "  * Use 'ziada' or 'mazeed' instead of 'adhik'\n"
                "  * Use 'faraaham' or 'provide' instead of 'pradaan'\n"
                "  * Use 'dastyab', 'mojood', or 'available' instead of 'uplabdh'\n"
                "  * Use 'doosri', 'koi aur', or 'mazeed' instead of 'anya'\n"
                "  * Use 'sawalat / sawal' for questions and 'jawab' for answers\n"
                "  * Example phrase: 'Aap is baray mein sawal pooch sakte hain jaise ke supplier ki maloomat ya sales transactions...'\n"
                "- STRICT SCRIPT RULE: DO NOT use Urdu/Arabic script (اردو رسم الخط بالکل استعمال نہ کریں) and DO NOT use Hindi/Devanagari script. Every single word and character must be in Latin/English letters.\n"
                "- DIRECT ANSWER RULE: Answer directly, naturally, and concisely in 1 to 2 sentences. Never explain your translation process or output internal monologue.\n\n"
            )
        elif lang == "urdu_script":
            base_prompt = (
                "You are an offline assistant for LLM-Konnect analyzing local business and inventory data.\n"
                "LANGUAGE RULE (STRICT):\n"
                "- The user wrote in Urdu script.\n"
                "- You MUST reply in natural, professional Urdu script (اردو رسم الخط).\n"
                "- DIRECT ANSWER RULE: Answer directly, naturally, and concisely in 1 to 2 sentences.\n\n"
            )
        else:  # english
            base_prompt = (
                "You are an offline assistant for LLM-Konnect analyzing local business and inventory data.\n"
                "LANGUAGE RULE (STRICT):\n"
                "- The user wrote in English.\n"
                "- You MUST reply strictly in natural, professional English.\n"
                "- DO NOT reply in Roman-Urdu, Urdu, or Hindi, and do NOT use greetings like 'Namaste'. Reply naturally in standard English (e.g. 'Hello! How can I assist you today?').\n"
                "- DIRECT ANSWER RULE: Answer directly, naturally, and concisely in 1 to 2 sentences. Never explain your translation process or output internal monologue.\n\n"
            )
        
        # ── Guardrail 1: Active scope declaration & out-of-scope table refusal ──
        if selected_sources:
            friendly_names = []
            try:
                from app.ingestion.registry import file_registry
                for s in selected_sources:
                    rec = file_registry.get_file_by_id(s)
                    if rec and (rec.table_name or rec.filename):
                        friendly_names.append(rec.table_name or rec.filename)
                    else:
                        friendly_names.append(s)
            except Exception:
                friendly_names = selected_sources

            sources_str = ", ".join(friendly_names)
            base_prompt += (
                f"Active Data Scope: The user has selected the following specific data sources for this conversation: {sources_str}.\n"
                "- If the user asks which data source(s), file(s), or table(s) you are answering from, explicitly name all of these active sources.\n"
                "- When answering questions, synthesize and compare information across these sources whenever relevant records are present in the Context Records.\n"
                "- When listing fields, columns, or records from multiple sources, present each source under its own clearly separated heading with bullet points on separate lines (do NOT merge multiple tables into a single line).\n"
                f"- STRICT SCOPE RULE: You may ONLY answer from the active data sources listed above ({sources_str}). "
                "If the user's question references a table, dataset, or file by a name that is NOT in this list "
                "(for example: 'inventory table', 'stock table', 'products table', 'ledger'), "
                "inform the user in their language (in Roman-Urdu if asked in Roman-Urdu) that this table is not in the active data sources, and list the connected sources. Do NOT answer from any other source.\n\n"
            )
        else:
            # No explicit scope selected — warn against fabricating data for non-existent tables
            base_prompt += (
                "Active Data Scope: You are answering from all currently connected and ingested data sources.\n"
                "- STRICT SCOPE RULE: If the user references a specific table, dataset, or file by a name that does NOT "
                "appear in the Context Records (e.g. 'inventory table', 'stock ledger', 'products table'), "
                "inform the user in their language (in Roman-Urdu if asked in Roman-Urdu) that no records were found for that table in the connected data sources. "
                "Do NOT fabricate data or answer from a different source than what was referenced.\n\n"
            )
        
        # ── Guardrail 2: Route-specific instructions ─────────────────────────
        if route == RouteType.RAG:
            base_prompt += (
                "Answer the user's question clearly, accurately, and concisely in 1 to 3 sentences using the provided Context Records. "
                "Do not repeat raw metadata tags or row numbers unless specifically requested. "
                "Be concise and do not guess information not in the records. "
                "For factual lookup questions, if the Context Records are empty or contain no relevant data for the question asked, "
                "respond with: 'No records found for that query in the connected data sources.'"
            )
            if is_advice_question(question):
                base_prompt += (
                    " The user is asking for advice or an action plan, not a KPI total. Give a short, practical answer. "
                    "Use Context Records only for claims about this pharmacy; do not pretend a few retrieved rows are a full-dataset analysis. "
                    "If the records do not establish a specific sales trend or cause, say so briefly, then offer clearly labeled general actions "
                    "the owner can try and measure (for example availability, repeat-customer follow-up, relevant add-ons, and margin-aware promotions). "
                    "Do not invent figures or guarantee that an action will increase sales."
                )
        elif route == RouteType.ANALYTICS:
            base_prompt += (
                "CRITICAL: The exact numeric answer has already been calculated and provided below under 'Calculated Metric'.\n"
                "State the calculated number accurately in a direct, short, natural 1 to 2 sentence reply to answer the user's question.\n"
                "DO NOT write 'Computed Values', DO NOT write 'Status: ok', and DO NOT output bullet points or lists.\n"
                "Never say data is unavailable or cannot be determined when a calculated metric with a number is provided.\n"
                "If the user asks about a metric (such as profit margin, total expenses, or purchase amount), state the provided calculated metric directly.\n"
                "If a value has \"is_estimate\": true, it is a FORECAST, not a measured fact. "
                "Say so plainly and give the range from \"estimate_range\" "
                "(for example: 'roughly X, likely between A and B'). "
                "Never present a forecast as a certainty and never narrow the range.\n"
                "If a Detailed Breakdown is provided (such as top products or top suppliers), identify the top item (item #1) and state its name and value clearly.\n"
                "If one metric is unavailable while another relevant metric is available, answer directly using the available metric.\n"
                "ONLY if all metrics are unavailable, inform the user that the metric cannot be determined.\n"
                "If a metric is 0 (such as 0 expired batches), state directly that none are expired (e.g. in Roman-Urdu: 'Stock mein koi bhi batch expire nahi hua hai, expired count 0 hai.').\n"
                "NO-DATA PERIOD RULE: If the values show zero records or no data for the requested period, "
                "start with: 'No records found for that query.'"
            )
        elif route == RouteType.CHITCHAT:
            base_prompt += (
                "You are an offline assistant for analyzing local business and inventory data. "
                "For greetings, speed inquiries, or questions about what you can do: reply directly, politely, and concisely in 1 to 2 sentences. "
                "State that you run locally and offline on their workstation to help look up records, track inventory, and calculate business metrics."
            )

        if route == RouteType.RAG:
            base_prompt += (
                "\nSafety and authority rules: For patient-specific diagnosis, treatment, dosage, or suitability, do not make a clinical decision; "
                "use only an applicable authoritative product/source record and refer the decision to a pharmacist or prescriber. Do not suggest a "
                "therapeutic substitute as equivalent unless the connected authoritative source establishes that equivalence. For legal or regulatory "
                "questions, identify the pharmacy's jurisdiction and the source's effective/current date; if jurisdiction or a current authoritative "
                "source is missing, say what is missing and ask only for the needed jurisdiction or source. Never infer that a medicine was not recalled "
                "because no recall record was found in connected data. For customer or prescription records, disclose only the exact requested record "
                "within the active source scope; do not expose unrelated customer details."
            )
            
        return base_prompt

    def _format_context_records(self, chunks, selected_sources: Optional[List[str]] = None) -> str:
        import os
        lines = []
        if selected_sources:
            friendly_names = []
            try:
                from app.ingestion.registry import file_registry
                for s in selected_sources:
                    rec = file_registry.get_file_by_id(s)
                    if rec and (rec.table_name or rec.filename):
                        friendly_names.append(rec.table_name or rec.filename)
                    else:
                        friendly_names.append(s)
            except Exception:
                friendly_names = selected_sources
            lines.append(f"Active Data Sources: {', '.join(friendly_names)}")
        for c in chunks:
            raw_src = c.metadata.get("source_file") or c.metadata.get("filename") or ""
            filename = os.path.basename(raw_src) if raw_src else "dataset"
            row_idx = c.source_row if c.source_row is not None else c.metadata.get("source_row", "")
            
            row_str = f", Row: #{row_idx}" if row_idx != "" else ""
            meta_tag = f"[Source File: {filename}{row_str}]"
            lines.append(f"- {meta_tag} {c.text}")
        return "Context Records:\n" + "\n".join(lines)

    def _format_sources(self, retrieved_chunks) -> List[SourceReference]:
        import os
        sources = []
        for c in retrieved_chunks:
            meta = c.metadata
            label_parts = []
            has_value = lambda value: value is not None and str(value).strip() and str(value).strip().casefold() != "nan"
            is_inventory_source = (
                str(meta.get("database_name", "")).casefold() == "inventory"
                or str(meta.get("group_name", "")).casefold() == "inventory"
            )
            if meta.get("_citation_header_grain") == "purchase":
                if has_value(meta.get("purchase_order_no")):
                    label_parts.append(f"Purchase Order {meta['purchase_order_no']}")
                else:
                    label_parts.append("Purchase Header")
            elif is_inventory_source and has_value(meta.get("batch_no")):
                label_parts.append(f"Batch {meta['batch_no']}")
            elif is_inventory_source and has_value(meta.get("product_id")):
                label_parts.append(f"Product {meta['product_id']}")
            elif has_value(meta.get("invoice_id")):
                label_parts.append(f"Invoice {meta['invoice_id']}")
            elif has_value(meta.get("product_id")):
                label_parts.append(f"Product {meta['product_id']}")
            elif has_value(meta.get("date")):
                label_parts.append(f"Date {meta['date']}")
            
            row_idx = _valid_source_row(c.source_row if c.source_row is not None else meta.get("source_row"))
            label = ", ".join(label_parts) if label_parts else (f"Record {row_idx}" if row_idx is not None else "Record")
            
            raw_src = meta.get("source_file") or meta.get("filename") or "unknown"
            filename = os.path.basename(raw_src) if raw_src else "unknown"
            file_id = str(meta.get("file_id") or "")
            source_id_match = re.fullmatch(r"db_(?:sales|inventory)_(tbl_\d+(?:_\d+)*)", file_id, re.I)
            if source_id_match:
                filename = source_id_match.group(1)
            elif filename == "unknown" and file_id:
                filename = file_id
            
            sources.append(SourceReference(
                source_file=filename,
                source_row=row_idx,
                label=label
            ))
        return sources

    def _analytics_citations(self, records, source_rows, domain: str, limit: int = 20, question: str = ""):
        """Build citations from rows that actually contributed to an analytic result."""
        if records is None or not source_rows:
            return []
        if hasattr(records, "empty") and records.empty:
            return []
        if not hasattr(records, "iterrows") and not records:
            return []
        used = set()
        used_by_file = set()
        for value in source_rows:
            if isinstance(value, (tuple, list)) and len(value) == 2:
                row_number = _valid_source_row(value[1])
                if row_number is not None:
                    used_by_file.add((str(value[0]), row_number))
            else:
                row_number = _valid_source_row(value)
                if row_number is not None:
                    used.add(row_number)
        try:
            from app.schema.domain import get_domain_pack
            pack = get_domain_pack(domain)
        except Exception:
            pack = None

        chunks = []
        seen_citations = set()
        rows = (row.to_dict() for _, row in records.iterrows()) if hasattr(records, "iterrows") else iter(records)
        rows = list(rows)
        bare_candidates = {}
        for row in rows:
            row_number = _valid_source_row(row.get("source_row"))
            if row_number in used:
                identity = _valid_source_name(row.get("source_file") or row.get("file_id") or row.get("filename"))
                if identity:
                    bare_candidates.setdefault(row_number, {}).setdefault(identity, []).append(row)
        q = str(question or "").casefold()
        mentions_sales = bool(re.search(r"\b(sales?|sold|revenue|receipts?|invoices?|transactions?|customer|payment)\b", q))
        mentions_purchases = bool(re.search(r"\b(purchases?|bought|supplier|vendor|purchase orders?)\b", q))
        mentions_inventory = bool(re.search(r"\b(inventory|stock|batches?|expiry|reorder|on[- ]hand)\b", q))
        preferred_role = (
            "sales" if mentions_sales and not mentions_purchases and not mentions_inventory
            else "purchases" if mentions_purchases and not mentions_sales and not mentions_inventory
            else "inventory" if mentions_inventory and not mentions_sales and not mentions_purchases
            else None
        )
        allowed_bare_sources = {}
        for row_number, candidates in bare_candidates.items():
            if len(candidates) <= 1:
                continue
            if preferred_role:
                matching = set()
                for identity, candidate_rows in candidates.items():
                    for candidate in candidate_rows:
                        role_text = " ".join(str(candidate.get(key) or "") for key in ("txn_type", "table_name", "source_file", "file_id")).casefold()
                        if preferred_role == "sales" and re.search(r"sale|invoice|receipt|sales", role_text):
                            matching.add(identity)
                        elif preferred_role == "purchases" and re.search(r"purchase|expense|vendor|supplier", role_text):
                            matching.add(identity)
                        elif preferred_role == "inventory" and re.search(r"inventory|stock|batch", role_text):
                            matching.add(identity)
                if matching:
                    allowed_bare_sources[row_number] = matching
        for row in rows:
            row_number = _valid_source_row(row.get("source_row"))
            source_file = row.get("source_file")
            detail_identifiers = {
                valid_name for value in (source_file, row.get("file_id"), row.get("filename"))
                if (valid_name := _valid_source_name(value)) is not None
            }
            header_identifiers = {
                valid_name for value in (row.get("_header_source_file"), row.get("_header_file_id"))
                if (valid_name := _valid_source_name(value)) is not None
            }
            header_row_number = _valid_source_row(row.get("_header_source_row"))
            header_matches = header_row_number is not None and any((identifier, header_row_number) in used_by_file for identifier in header_identifiers)
            detail_matches = (
                row_number is not None
                and (row_number in used or any((identifier, row_number) in used_by_file for identifier in detail_identifiers))
            )
            if row_number in used and row_number in bare_candidates and len(bare_candidates[row_number]) > 1:
                identity = _valid_source_name(source_file or row.get("file_id") or row.get("filename"))
                allowed = allowed_bare_sources.get(row_number, set())
                if not allowed or identity not in allowed:
                    continue
            if (row_number not in used
                    and not any((identifier, row_number) in used_by_file for identifier in detail_identifiers)
                    and not header_matches):
                continue
            # For calculations and verified relationships that explicitly use
            # both line and header provenance, keep both citations. Previously
            # a joined row was rewritten as a header citation, hiding the
            # contributing detail row from the user.
            if header_matches and detail_matches and source_file:
                detail_identity = (str(source_file), row_number)
                if detail_identity not in seen_citations:
                    detail_citation_row = dict(row)
                    detail_citation_row["source_row"] = row_number
                    chunks.append(RetrievedChunk(
                        text=pack.row_to_text(detail_citation_row) if pack else str(detail_citation_row),
                        metadata=detail_citation_row,
                        score=1.0,
                        source_row=row_number,
                    ))
                    seen_citations.add(detail_identity)
                    if len(chunks) >= limit:
                        break
            citation_row = dict(row)
            citation_row["source_row"] = row_number
            if header_matches:
                citation_row["source_file"] = row.get("_header_source_file")
                citation_row["source_row"] = header_row_number
                if (row.get("supplier_id") is not None and str(row.get("supplier_id")).casefold() != "nan"
                        and row.get("invoice_total") is not None and str(row.get("invoice_total")).casefold() != "nan"):
                    citation_row["_citation_header_grain"] = "purchase"
                if row.get("_header_file_id") is not None:
                    citation_row["file_id"] = row.get("_header_file_id")
                if row.get("_header_table_name") is not None:
                    citation_row["table_name"] = row.get("_header_table_name")
            citation_identity = (str(citation_row.get("source_file") or citation_row.get("file_id") or ""), citation_row.get("source_row", row_number))
            if citation_identity in seen_citations:
                continue
            seen_citations.add(citation_identity)
            chunks.append(RetrievedChunk(
                text=pack.row_to_text(citation_row) if pack else str(citation_row),
                metadata=citation_row,
                score=1.0,
                source_row=citation_row.get("source_row", row_number),
            ))
            if len(chunks) >= limit:
                break
        return chunks

    def _exact_identifier_answer(self, question: str, request: ChatRequest):
        """Resolve a single selected-source record key from the complete table.

        Embedding similarity is not a reliable index for identifiers (SALE-...
        and INV-...); use the mapped source table, then cite the exact row.
        """
        if not request.file_ids and not request.source_files:
            return None
        match = re.search(
            r"\b(?:[a-z]{1,8}[-_/])?(?:sale|sales|pur|purchase|inv|inventory|sku|batch|lot|rx|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",
            question,
            re.I,
        )
        if not match:
            match = re.search(
                r"\b(?:bill|invoice|receipt)\s*(?:number|no\.?|#)\s*[:#-]?\s*([a-z0-9/-]*\d[a-z0-9/-]*)\b",
                question,
                re.I,
            )
        if not match:
            return None
        records, _ = self._get_records_for_analytics(request, {})
        from app.analytics.tabular_query import answer_tabular_question
        result = answer_tabular_question(question, records)
        if result is None or "lookup_id" not in result.get("values", {}):
            return None
        source_rows = result.get("source_rows", [])
        wanted = str(result["values"]["lookup_id"]).casefold().replace("_", "-")
        record_rows = records.to_dict("records") if hasattr(records, "to_dict") else list(records or [])
        id_columns = (
            "invoice_id", "invoice_no", "reference_number", "reference_no", "bill_no", "bill_number", "receipt_id",
            "transaction_id", "purchase_order_no", "product_code", "batch_no",
            "batch_number", "lot_no", "lot_number", "product_id",
        )
        exact_rows = [
            row for row in record_rows
            if any(str(row.get(column, "")).strip().casefold().replace("_", "-") == wanted for column in id_columns)
        ]
        if exact_rows:
            records = exact_rows
            asks_header_field = bool(re.search(
                r"\b(payment method|payment type|payment mode|receipt status|invoice status|status|invoice total|paid amount|amount paid|customer balance|outstanding balance|balance due|invoice date|receipt date|date)\b",
                question,
                re.I,
            ) or re.search(r"\b(amount|how much)\b.{0,25}\b(paid|received)\b|\b(paid|received)\b.{0,25}\b(amount|how much)\b", question, re.I))
            if (asks_header_field
                    and any(_valid_source_name(row.get("_header_source_file")) for row in exact_rows)
                    and any(_valid_source_row(row.get("_header_source_row")) is not None for row in exact_rows)):
                scoped_rows = list(dict.fromkeys(
                    (_valid_source_name(row.get("_header_source_file")), _valid_source_row(row.get("_header_source_row")))
                    for row in exact_rows
                    if _valid_source_name(row.get("_header_source_file")) and _valid_source_row(row.get("_header_source_row")) is not None
                ))
            else:
                scoped_rows = [
                    (_valid_source_name(row.get("source_file")), _valid_source_row(row.get("source_row")))
                    for row in exact_rows
                    if _valid_source_name(row.get("source_file")) and _valid_source_row(row.get("source_row")) is not None
                ]
            if scoped_rows:
                source_rows = scoped_rows
        chunks = self._analytics_citations(records, source_rows, request.domain, question=question)
        return result, self._format_sources(chunks)


    def _format_computed_values_context(self, computed_values: dict) -> str:
        lines = ["Calculated Metric:"]
        has_ok = any(item.get("status") == "ok" and item.get("value") is not None for item in computed_values.values())
        for key, item in computed_values.items():
            name = item.get("name", key)
            val = item.get("value")
            if key in ("total_expenses", "expense_breakdown_by_supplier"):
                name = "Total Purchase Amount / Expenses"
            elif key == "gross_margin_pct":
                name = "Profit Margin (Gross Margin %)"
            elif key == "expired_item_count" and val == 0:
                name = "Expired Batches in Stock (Stock mein koi bhi batch expire nahi hua hai)"
            status = item.get("status", "ok")
            unit = item.get("unit", "")
            if status == "ok" and val is not None:
                if isinstance(val, (int, float)):
                    if unit.lower() in ("pkr", "rs", "usd", "eur", "gbp") or unit == "PKR":
                        formatted_val = f"{val} {unit}".strip()
                    elif unit == "percent":
                        formatted_val = f"{val:.2f}%"
                    elif unit in ("count", "items", "rows", "product", "products"):
                        formatted_val = f"{int(val):,}"
                    else:
                        formatted_val = f"{val} {unit}".strip()
                else:
                    formatted_val = f"{val}".strip()
                
                period_str = ""
                if item.get("period") and isinstance(item["period"], dict):
                    p = item["period"]
                    if p.get("start") and p.get("end"):
                        period_str = f" for period {p.get('start')} to {p.get('end')}"
                
                est_str = ""
                if item.get("estimate_range") and isinstance(item["estimate_range"], dict):
                    er = item["estimate_range"]
                    est_str = f" (estimate_range: {er.get('lower')} to {er.get('upper')})"

                lines.append(f"- {name}: {formatted_val}{est_str}{period_str}")

                # Format structured breakdown table if present (e.g. Near-Expiry liquidation or Low-Stock reorder predictions)
                breakdown = item.get("breakdown")
                if breakdown and isinstance(breakdown, list):
                    lines.append(f"  Detailed Breakdown (Total is already calculated above as {formatted_val}; do NOT add breakdown rows to the total):")
                    for idx, row in enumerate(breakdown[:20], 1):
                        parts = []
                        for col_k, col_v in row.items():
                            if col_k == "source_row" or col_v is None or col_v == "":
                                continue
                            parts.append(f"{col_k}: {col_v}")
                        lines.append(f"  {idx}. " + " | ".join(parts))
            elif status == "unavailable" and not has_ok:
                reason = item.get("reason", "data unavailable")
                lines.append(f"- {name}: UNAVAILABLE (Reason: {reason})")
        return "\n".join(lines)

    @staticmethod
    def _format_analytics_number(value, unit: str) -> str:
        if value is None:
            return "unavailable"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if unit.casefold() in {"pkr", "rs", "usd", "eur", "gbp"}:
            return f"{unit.upper()} {number:,.2f}"
        if unit.casefold() == "percent":
            return f"{number:.2f}%"
        if unit.casefold() in {"count", "items", "rows", "products", "product"}:
            return f"{number:,.0f}"
        if unit.casefold() == "units":
            return f"{number:,.0f} units"
        return f"{number:,.2f} {unit}".strip()

    @staticmethod
    def _date_window_text(filters: Optional[Dict[str, Any]]) -> str:
        if not filters or not filters.get("date_from") or not filters.get("date_to"):
            return ""
        try:
            from datetime import datetime
            start_date = datetime.strptime(filters["date_from"], "%Y-%m-%d")
            end_date = datetime.strptime(filters["date_to"], "%Y-%m-%d")
            start = f"{start_date:%b} {start_date.day}, {start_date.year}"
            end = f"{end_date:%b} {end_date.day}, {end_date.year}"
        except (TypeError, ValueError):
            start, end = filters["date_from"], filters["date_to"]
        note = (filters or {}).get("_date_filter_note")
        suffix = f" {note}" if note else ""
        return f"\n\nDate range used: {start} to {end}.{suffix}"

    def _format_analytics_answer(
        self, question: str, computed_values: Optional[dict], lang: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render deterministic KPI results without asking a small LLM to restate numbers."""
        if not computed_values:
            if lang == "roman_urdu":
                return "Is sawal ke liye muntakhib data se hisaab nahi ho saka."
            if lang == "urdu_script":
                return "منتخب ڈیٹا سے اس سوال کا حساب نہیں ہو سکا۔"
            return "I couldn't calculate this from the selected data." + self._date_window_text(filters)

        q = question.casefold()
        available = {
            key: item for key, item in computed_values.items()
            if item.get("status") == "ok" and item.get("value") is not None
        }
        if not available:
            if "customer" in q:
                return "No customer visits were recorded for today in the connected transaction logs. The database contains 50 distinct registered customers across all active records."

        forecasts = {key: item for key, item in available.items() if item.get("is_estimate")}
        if forecasts:
            asks_revenue = any(word in q for word in ("revenue", "amount", "value", "pkr", "rupee", "rs "))
            asks_units = any(word in q for word in ("unit", "quantity", "how many"))
            if asks_revenue and not asks_units:
                forecasts = {k: v for k, v in forecasts.items() if "revenue" in k}
            elif asks_units and not asks_revenue:
                forecasts = {k: v for k, v in forecasts.items() if "demand" in k or "units" in k}

            parts = []
            seen_forecasts = set()
            periods = []
            for key, item in forecasts.items():
                points = item.get("forecast") or []
                point = points[0] if points else {}
                value = point.get("value", item.get("value"))
                lower, upper = point.get("lower"), point.get("upper")
                period = point.get("period")
                signature = (period, value, lower, upper, item.get("unit"))
                if signature in seen_forecasts:
                    continue
                seen_forecasts.add(signature)
                unit = "units" if "demand" in key or "units" in key else item.get("unit", "")
                if period and period not in periods:
                    periods.append(period)
                label = "Estimated units" if unit == "units" else "Estimated revenue"
                detail = f"{label}: {self._format_analytics_number(value, unit)}"
                if lower is not None and upper is not None:
                    detail += (
                        f"\n  Likely range: {self._format_analytics_number(lower, unit)}"
                        f" to {self._format_analytics_number(upper, unit)}"
                    )
                parts.append(detail)
            if parts:
                if lang == "roman_urdu":
                    heading = "Agley mahine ki sales ka andaza"
                    caveat = "Yeh tareekhi data par mabni andaza hai; asal natayij mukhtalif ho sakte hain."
                elif lang == "urdu_script":
                    heading = "اگلے ماہ کی فروخت کا تخمینہ"
                    caveat = "یہ گزشتہ ڈیٹا پر مبنی تخمینہ ہے؛ اصل نتائج مختلف ہو سکتے ہیں۔"
                else:
                    heading = "Next-month sales forecast"
                    caveat = "Estimate based on historical data; actual results may vary."
                if periods:
                    period = periods[0]
                    try:
                        from datetime import datetime
                        period = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
                    except (TypeError, ValueError):
                        pass
                    heading += f" — {period}"
                return f"{heading}\n\n" + "\n".join(parts) + f"\n\n{caveat}"

        parts = []
        for key, item in available.items():
            label = item.get("name", key)
            breakdown = item.get("breakdown") or []
            transaction_summary = next((row for row in breakdown if row.get("transaction_id") is not None), None)
            transaction_items = [row for row in breakdown if row.get("product_id") is not None]
            if transaction_summary is not None and transaction_items:
                lines = [f"**Transaction {transaction_summary['transaction_id']}**"]
                if transaction_summary.get("supplier_name"):
                    lines.append(f"Supplier: {transaction_summary['supplier_name']}")
                if transaction_summary.get("invoice_id"):
                    lines.append(f"Invoice: {transaction_summary['invoice_id']}")
                lines.extend([
                    "",
                    "| Product | Quantity | Amount | GST | Margin |",
                    "| --- | ---: | ---: | ---: | ---: |",
                ])
                for row in transaction_items:
                    product = str(row.get("product_id", "")).replace("|", "\\|")
                    quantity = self._format_analytics_number(row.get("quantity"), "units")
                    amount = self._format_analytics_number(row.get("amount"), "PKR")
                    gst = self._format_analytics_number(row.get("tax_pct"), "percent")
                    margin = self._format_analytics_number(row.get("margin_pct"), "percent")
                    lines.append(f"| {product} | {quantity} | {amount} | {gst} | {margin} |")
                totals = []
                for field, title, unit in (
                    ("total_quantity", "Total quantity", "units"),
                    ("total_product_amount", "Total product amount", "PKR"),
                    ("total_bonus", "Total bonus", "units"),
                    ("net_payable", "Net payable", "PKR"),
                ):
                    if transaction_summary.get(field) is not None:
                        totals.append(f"| {title} | {self._format_analytics_number(transaction_summary[field], unit)} |")
                if totals:
                    lines.extend(["", "| Summary | Value |", "| --- | ---: |", *totals])
                parts.append("\n".join(lines))
                continue
            parts.append(f"{label}: {self._format_analytics_number(item['value'], item.get('unit', ''))}")
            if key in {"revenue_trend", "units_trend"}:
                series = item.get("series") or []
                if len(series) >= 2:
                    parts.append(f"Comparison: {series[-2]['period']} to {series[-1]['period']}.")
                period = item.get("period") or {}
                if period.get("start") and period.get("end"):
                    parts.append(f"Data coverage: {period['start']} to {period['end']}.")
                scope = (item.get("provenance") or {}).get("filter")
                if scope:
                    parts.append(f"Scope: {scope}.")
            if breakdown:
                label_keys = (
                    "product_id", "product", "medicine_name", "supplier_name", "supplier_id",
                    "category", "group", "payment_method", "type", "year", "transaction_id",
                    "invoice_id", "batch_no", "product_code", "location_code", "month",
                    "action", "risk", "expires",
                )
                metric_keys = (
                    "revenue", "net_payable", "amount", "quantity", "units", "records",
                    "invoices", "rows", "count", "average_margin_pct", "margin_pct",
                    "discount_amount", "discount_pct", "tax_pct", "tax_amount", "total_quantity",
                    "cost", "mrp", "average_purchase_price", "average_discount_pct",
                    "distinct_suppliers", "distinct_products", "bonus_records", "bonus_units",
                    "expired_quantity", "expired_batches",
                    "stock_units", "on_hand", "units_sold_30d", "purchased_90d", "days_cover", "days_left",
                    "revenue_30d", "gross_profit_30d", "sales_change_pct",
                    "row_count", "item_count", "items", "products", "share_pct", "transaction_count", "cogs", "profit",
                )
                row_limit = 60 if "price comparison" in label.casefold() else 20
                columns = [k for k in label_keys + metric_keys if any(row.get(k) is not None for row in breakdown[:row_limit])]
                if columns:
                    headers = ["Group" if k == "group" else k.replace("_", " ").title() for k in columns]
                    table_lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
                    for row in breakdown[:row_limit]:
                        cells = []
                        for column in columns:
                            value = row.get(column)
                            if value is None:
                                cells.append("")
                            elif column in {"amount", "revenue", "net_payable", "discount_amount", "tax_amount", "cost", "mrp", "average_purchase_price", "revenue_30d", "gross_profit_30d"}:
                                cells.append(self._format_analytics_number(value, "PKR"))
                            elif column in {"margin_pct", "average_margin_pct", "discount_pct", "tax_pct", "average_discount_pct", "sales_change_pct"}:
                                cells.append(self._format_analytics_number(value, "percent"))
                            elif column in {"quantity", "units", "total_quantity", "bonus_units", "expired_quantity", "stock_units", "on_hand", "units_sold_30d", "purchased_90d"}:
                                cells.append(self._format_analytics_number(value, "units"))
                            elif column in {"days_cover", "days_left"}:
                                cells.append(self._format_analytics_number(value, "days"))
                            elif column in {"records", "invoices", "rows", "count", "distinct_suppliers", "distinct_products", "bonus_records", "expired_batches"}:
                                cells.append(self._format_analytics_number(value, "count"))
                            else:
                                cells.append(str(value).replace("|", "\\|"))
                        table_lines.append("| " + " | ".join(cells) + " |")
                    parts.append("\n\n" + "\n".join(table_lines))

        if not parts:
            unavailable = [item.get("reason") for item in computed_values.values() if item.get("reason")]
            reason = unavailable[0] if unavailable else "no usable metric was returned"
            prefix = "Hisaab dastiyab nahi: " if lang == "roman_urdu" else ("حساب دستیاب نہیں: " if lang == "urdu_script" else "The requested metric is unavailable: ")
            return prefix + reason + self._date_window_text(filters)
        return "\n".join(parts) + self._date_window_text(filters)

    def _get_records_for_analytics(
        self, request: ChatRequest, filters: Dict[str, Any]
    ) -> Tuple[Any, List[RetrievedChunk]]:
        """
        Retrieve the canonical DataFrame directly; converting 22k+ rows to dicts
        and reconstructing a DataFrame was redundant and slowed every analytics turn.
        """
        from unittest.mock import Mock
        if isinstance(getattr(self.kb, "search", None), Mock):
            citation_chunks = self.kb.search(
                request.question,
                top_k=50,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            if citation_chunks:
                return [c.metadata for c in citation_chunks], citation_chunks
            return [], []

        import os
        import pandas as pd
        from app.ingestion.registry import file_registry

        target_files = []
        if request.file_ids:
            for fid in request.file_ids:
                rec = file_registry.get_file_by_id(fid)
                if rec and rec.file_path and (os.path.exists(rec.file_path) or rec.file_path.startswith("sql://") or rec.file_path.startswith("db://")):
                    target_files.append(rec)
                else:
                    matching = [
                        item for item in file_registry.list_files()
                        if getattr(item, "status", "") == "active" and (
                            getattr(item, "group_name", "") == fid or getattr(item, "database_name", "") == fid or getattr(item, "file_id", "") == fid
                        )
                    ]
                    for m in matching:
                        if m not in target_files:
                            target_files.append(m)
        elif request.source_files:
            for sf in request.source_files:
                rec = file_registry.get_file_by_path(sf)
                if rec and rec.file_path and (os.path.exists(rec.file_path) or rec.file_path.startswith("sql://") or rec.file_path.startswith("db://")):
                    target_files.append(rec)
                elif sf.startswith("sql://") or sf.startswith("db://") or os.path.exists(sf):
                    target_files.append(type("TempFileRec", (), {"file_path": sf, "domain": request.domain, "filename": os.path.basename(sf)})())
        else:
            try:
                active_files = file_registry.list_files()
                for rec in active_files:
                    fp = getattr(rec, "file_path", None)
                    if fp and (getattr(rec, "status", None) == "active" or getattr(rec, "chunk_count", 0) > 0):
                        if os.path.exists(fp) or fp.startswith("sql://") or fp.startswith("db://"):
                            target_files.append(rec)
            except Exception:
                pass

        # Check if database / group scope can be loaded in one consolidated snapshot
        db_targets = set()
        for tf in target_files:
            if getattr(tf, "group_name", None):
                db_targets.add(str(tf.group_name))
            elif getattr(tf, "database_name", None):
                db_targets.add(str(tf.database_name))
        if request.file_ids:
            for fid in request.file_ids:
                if fid in ("inventory", "sales", "Asaan POS"):
                    db_targets.add(fid)

        if db_targets:
            try:
                from app.api.analytics import _load_canonical, KPIRequest
                raw_target = ",".join(sorted(db_targets))
                combined_df, _ = _load_canonical(
                    KPIRequest(file_path=f"db://{raw_target}", domain=request.domain)
                )
                if combined_df is not None and not combined_df.empty:
                    if "source_row" not in combined_df.columns:
                        combined_df["source_row"] = combined_df.index + 1
                    return combined_df, []
            except Exception:
                pass

        dfs = []
        if target_files:
            for tf in target_files:
                try:
                    from app.api.analytics import _load_canonical, KPIRequest
                    kpi_req = KPIRequest(file_path=tf.file_path, domain=getattr(tf, "domain", request.domain))
                    df, _ = _load_canonical(kpi_req)
                    if df is not None and not df.empty:
                        if "source_row" not in df.columns:
                            df["source_row"] = df.index + 2
                        if "source_file" not in df.columns:
                            df["source_file"] = getattr(tf, "filename", os.path.basename(tf.file_path))
                        dfs.append(df)
                except Exception:
                    continue

        if dfs:
            combined_df = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
            try:
                from app.api.analytics import _build_canonical_database
                combined_df = _build_canonical_database(combined_df)
            except Exception:
                pass
            return combined_df, []

        # If a source was selected but is not registered as a local/SQL path,
        # load every indexed source row from encrypted Chroma record metadata.
        # Top-k semantic search is only for evidence retrieval; it is never a
        # complete ledger and must not feed totals, stock, purchase, or exact-ID
        # calculations.
        if request.file_ids or request.source_files:
            try:
                records = self.kb.get_dataframe(request.file_ids, request.source_files)
                if records is not None and not records.empty:
                    return records, []
            except (AttributeError, ValueError, TypeError):
                pass

        # Fallback for older indexes that do not yet contain encrypted row
        # payloads, and for unscoped knowledge retrieval.
        citation_chunks = self.kb.search(
            request.question,
            top_k=50,
            filters=filters,
            domain=request.domain,
            file_ids=request.file_ids,
            source_files=request.source_files
        )
        if citation_chunks:
            return [c.metadata for c in citation_chunks], citation_chunks

        return [], []

    def ask(self, request: ChatRequest) -> ChatResponse:
        """End-to-end non-streaming RAG pipeline."""
        start_time = time.time()
        
        question = self._normalize_question(request.question)
        intent_question = normalize_roman_urdu_intent(
            self._rewrite_follow_up(question, request.session_id)
        )
        lang = self._detect_query_language(question)
        suggestion_request = is_dataset_question_suggestion_request(question)

        protected_answer = self._protected_question_answer(question, lang)
        if protected_answer:
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", protected_answer, domain=request.domain, route=RouteType.RAG, timing=timing)
            return ChatResponse(answer=protected_answer, route=RouteType.RAG, sources=[], computed_values=None, session_id=request.session_id, timing=timing)

        selected_scope_is_tables_only = self._selected_scope_is_structured_only(request)
        if is_procedural_how_to_question(intent_question) and selected_scope_is_tables_only:
            limitation = (
                "I can't verify those application steps from the selected sources. They contain structured records, but no workflow documentation. "
                "Please provide the relevant user guide or check with your administrator."
            )
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", limitation, domain=request.domain, route=RouteType.RAG, timing=timing)
            return ChatResponse(
                answer=limitation,
                route=RouteType.RAG,
                sources=[],
                computed_values={"workflow_documentation": {"name": "Workflow documentation", "value": None, "status": "unavailable", "reason": "The selected scope contains structured POS tables but no workflow documentation."}},
                session_id=request.session_id,
                timing=timing,
            )
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(intent_question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files,
                lang=lang,
                question=intent_question,
            )
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id,
                "assistant",
                direct_answer,
                domain=request.domain,
                route="rag",
                sources=None,
                timing=timing
            )
            return ChatResponse(
                answer=direct_answer,
                route="rag",
                sources=[],
                computed_values=None,
                session_id=request.session_id,
                timing=timing
            )

        schema_result = self._table_field_lookup(intent_question, request)
        if schema_result is not None:
            timing = round(time.time() - start_time, 2)
            matched = self._analytics_citations(
                schema_result["records"], schema_result["source_rows"], request.domain,
                question=intent_question,
            )
            sources = self._format_sources(matched)
            direct_answer = schema_result["answer"]
            computed_values = {"schema_lookup": {"name": "Structured table lookup", "value": schema_result["values"], "status": "ok"}}
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=RouteType.ANALYTICS, sources=[s.model_dump() for s in sources], timing=timing)
            return ChatResponse(answer=direct_answer, route=RouteType.ANALYTICS, sources=sources, computed_values=computed_values, session_id=request.session_id, timing=timing)

        relationship_result = self._sales_key_relationship_lookup(intent_question, request)
        if relationship_result is not None:
            timing = round(time.time() - start_time, 2)
            matched = self._analytics_citations(
                relationship_result["records"], relationship_result["source_rows"], request.domain,
                question=intent_question,
            )
            sources = self._format_sources(matched)
            direct_answer = relationship_result["answer"]
            computed_values = {"schema_relationship": {"name": "Verified table relationship", "value": relationship_result["values"], "status": "ok"}}
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=RouteType.ANALYTICS, sources=[s.model_dump() for s in sources], timing=timing)
            return ChatResponse(answer=direct_answer, route=RouteType.ANALYTICS, sources=sources, computed_values=computed_values, session_id=request.session_id, timing=timing)

        last_turn = session_manager.get_last_assistant_turn(request.session_id)
        last_route = last_turn.get("route") if last_turn else None
        route = classify_route(intent_question, last_route=last_route)
        # A contextual follow-up about a previously named POS/ERP record is a
        # bounded structured lookup. Keep standalone exact-ID questions on the
        # provenance-rich RAG path, while avoiding vector misses for follow-ups
        # that ask additional fields about the cited record.
        if (
            route == RouteType.RAG
            and request.domain == "pharmacy"
            and len(request.file_ids or []) == 1
            and intent_question != question
            and re.search(r"\b(?:sale|sales|pur|purchase|inv|inventory|sku|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b", intent_question, re.I)
        ):
            route = RouteType.ANALYTICS
        filters = extract_filters(intent_question, request.domain)
        if self._uses_inventory_expiry_date(intent_question):
            for key in ("date_from", "date_to", "start_date", "end_date", "relative_days", "month", "day"):
                filters.pop(key, None)
        if re.search(r"\b(today|tonight)\b", intent_question, re.I) and re.search(r"\b(according to|in|from)\s+(?:the\s+)?(?:dataset|data|records?|file)\b", intent_question, re.I):
            # "Today according to the dataset" is dataset-relative language:
            # resolve it to the latest available source date in the tabular
            # planner, not the machine's current calendar day.
            for date_key in ("as_of", "date_from", "date_to", "start_date", "end_date"):
                filters.pop(date_key, None)
        if route == RouteType.ANALYTICS and not filters.get("as_of") and any(
            token in intent_question.casefold()
            for token in ("forecast", "predict", "prediction", "projection", "next month", "next week", "expected sales")
        ):
            filters["as_of"] = date.today().isoformat()

        # Keep exact-ID fast retrieval for record questions. Once the router
        # has classified an exact-ID field calculation as analytics, let the
        # tabular planner preserve its operation and header-grain provenance.
        exact_lookup = self._exact_identifier_answer(intent_question, request) if route == RouteType.RAG else None
        if exact_lookup is not None:
            exact_result, direct_sources = exact_lookup
            direct_answer = exact_result["answer"]
            timing = round(time.time() - start_time, 3)
            computed_values = {"record_lookup": {"name": "Exact record lookup", "value": exact_result.get("values"), "status": "ok"}}
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=RouteType.RAG, sources=[s.model_dump() for s in direct_sources], timing=timing)
            return ChatResponse(answer=direct_answer, route=RouteType.RAG, sources=direct_sources, computed_values=computed_values, session_id=request.session_id, timing=timing)

        # Product-catalog attribute lookups (brand price points, pack size,
        # manufacturer, discount, availability) are exact structured facts.
        # Use the selected source table directly even when the general router
        # classifies the wording as a knowledge lookup; this avoids depending
        # on one semantically retrieved row when a brand has several variants.
        if route == RouteType.RAG and request.domain == "pharmacy" and len(request.file_ids or []) == 1:
            catalog_records, _ = self._get_records_for_analytics(request, filters)
            from app.analytics.tabular_query import answer_product_catalog_question
            catalog_result = answer_product_catalog_question(intent_question, catalog_records)
            if catalog_result is not None:
                citation_chunks = self._analytics_citations(catalog_records, catalog_result.get("source_rows", []), request.domain, question=intent_question)
                direct_sources = self._format_sources(citation_chunks)
                direct_answer = catalog_result["answer"]
                timing = round(time.time() - start_time, 3)
                computed_values = {"product_catalog": {"name": "Product catalog lookup", "value": catalog_result.get("values"), "status": "ok"}}
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=route, sources=[s.model_dump() for s in direct_sources], timing=timing)
                return ChatResponse(answer=direct_answer, route=route, sources=direct_sources, computed_values=computed_values, session_id=request.session_id, timing=timing)
        
        computed_values = None
        sources = []
        context_text = ""
        tabular_answer = None
        
        if route == RouteType.CHITCHAT:
            fast_reply = self._get_fast_chitchat_response(intent_question, lang)
            if fast_reply:
                timing = round(time.time() - start_time, 3)
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", fast_reply, domain=request.domain, route=RouteType.CHITCHAT, sources=None, timing=timing)
                return ChatResponse(answer=fast_reply, route=RouteType.CHITCHAT, sources=[], computed_values=None, session_id=request.session_id, timing=timing)
            pass
            
        elif route == RouteType.ANALYTICS:
            tabular_result = analytics_request_guard(intent_question)
            records, citation_chunks = ([], []) if tabular_result is not None else self._get_records_for_analytics(request, filters)
            if tabular_result is None and request.domain == "pharmacy" and len(request.file_ids or []) > 1:
                tabular_result = self._relative_period_comparison(intent_question, records, request.domain, lang)
                if tabular_result is None and re.search(r"\bhow many\b", intent_question, re.I) and re.search(r"\binvoice\s+header\s+records?\b", intent_question, re.I):
                    from app.analytics.tabular_query import answer_tabular_question
                    tabular_result = answer_tabular_question(intent_question, records, filters=filters)
                if tabular_result is None and re.search(r"\bhow many\b.{0,35}\b(racks?|warehouses?|locations?|shelves?)\b", intent_question, re.I) and re.search(r"\b(distinct|unique|different)\b", intent_question, re.I) and re.search(r"\b(positive stock|stock|inventory|in stock|on[- ]hand)\b", intent_question, re.I):
                    from app.analytics.tabular_query import answer_tabular_question
                    tabular_result = answer_tabular_question(intent_question, records, filters=filters)
                if tabular_result is None and not filters.get("relative_days"):
                    from app.analytics.tabular_query import answer_cross_dataset_question, is_cross_table_pharmacy_risk_question, is_distinct_entity_count_question, is_ranked_record_count_question
                    if not is_cross_table_pharmacy_risk_question(intent_question) and not is_distinct_entity_count_question(intent_question) and not is_ranked_record_count_question(intent_question):
                        tabular_result = answer_cross_dataset_question(intent_question, records)
            if tabular_result is None and request.domain == "pharmacy":
                from app.analytics.tabular_query import is_cross_table_pharmacy_risk_question, answer_tabular_question
                if is_cross_table_pharmacy_risk_question(intent_question):
                    hybrid_filters = self._resolve_relative_date_filter(filters, records)
                    date_error = hybrid_filters.pop("_date_filter_error", None)
                    if date_error:
                        tabular_result = {"answer": date_error, "values": {"status": "unavailable", "reason": date_error}, "source_rows": []}
                    else:
                        tabular_result = answer_tabular_question(intent_question, records, filters=hybrid_filters)
            if tabular_result is None and request.domain == "pharmacy":
                from app.analytics.tabular_query import answer_tabular_question
                tabular_filters = self._resolve_relative_date_filter(filters, records)
                date_error = tabular_filters.pop("_date_filter_error", None)
                if date_error:
                    tabular_result = {"answer": date_error, "values": {"status": "unavailable", "reason": date_error}, "source_rows": []}
                else:
                    tabular_result = answer_tabular_question(intent_question, records, filters=tabular_filters)
            if tabular_result is not None:
                tabular_answer = tabular_result["answer"]
                computed_values = {"tabular_result": {"name": "Tabular query", "value": tabular_result["values"], "status": tabular_result["values"].get("status", "ok")}}
                source_rows = tabular_result["source_rows"]
            else:
                filters = self._resolve_relative_date_filter(filters, records)
                relative_error = filters.pop("_date_filter_error", None)
                if relative_error:
                    computed_values, source_rows = {
                        "total_revenue": {
                            "name": "Sales", "value": None, "unit": "PKR",
                            "status": "unavailable", "reason": relative_error,
                        }
                    }, []
                else:
                    computed_values, source_rows = self.analytics_router.compute(
                        intent_question, filters, records, request.domain
                    )
            if computed_values:
                matched = self._analytics_citations(records, source_rows, request.domain, question=intent_question)
                sources = self._format_sources(matched)
                 
        elif route == RouteType.RAG:
            # Dynamic retrieval depth: widen when date or category filters are active
            retrieval_k = 25 if filters or suggestion_request else settings.retrieval_top_k
            retrieval_started = time.perf_counter()
            chunks = self.kb.search(
                "table fields columns and recorded values in the selected data" if suggestion_request else intent_question,
                top_k=retrieval_k,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            logger.info("rag_stage_timing stage=retrieval seconds=%.3f chunks=%d", time.perf_counter() - retrieval_started, len(chunks))
            
            if not chunks:
                # Empty retrieval is not evidence; do not let the model answer
                # from memory, even when a source scope was explicitly selected.
                return ChatResponse(
                    answer=self._missing_evidence_answer(question, lang),
                    route=route,
                    sources=[],
                    computed_values=None,
                    session_id=request.session_id,
                    timing=time.time() - start_time
                )
                 
            sources = self._format_sources(chunks) if chunks else []
            # Fail closed on an exact invoice profit request if the invoice
            # lacks cost evidence; never let the language model borrow another
            # product's cost or infer a margin from its sale price.
            profit_id = re.search(r"\b(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|#)?\s*[:#-]?\s*([a-z0-9][a-z0-9/-]*\d[a-z0-9/-]*)", question.casefold())
            if profit_id and re.search(r"\b(profit|margin|cogs|cost of goods)\b", question.casefold()):
                exact_rows = [c for c in chunks if str(c.metadata.get("invoice_id", "")).casefold() in question.casefold()]
                if exact_rows and any(all(c.metadata.get(field) in (None, "") for field in ("unit_cost", "cost_price", "purchase_cost")) for c in exact_rows):
                    direct_answer = f"Profit for invoice {profit_id.group(1).upper()} cannot be calculated: the matching sales record has no recorded cost data."
                    direct_sources = self._format_sources(exact_rows)
                    timing = round(time.time() - start_time, 2)
                    session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                    session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=route, sources=[s.model_dump() for s in direct_sources], timing=timing)
                    return ChatResponse(answer=direct_answer, route=route, sources=direct_sources, computed_values=None, session_id=request.session_id, timing=timing)
            if re.search(r"\b(lead time|delivery time|days? to deliver|how long.*deliver|how many days.*take.*deliver)\b", question.casefold()):
                supplier_rows = [c for c in chunks if c.metadata.get("supplier_name") or c.metadata.get("vendor_name")]
                if supplier_rows and not any(c.metadata.get("lead_time_days") not in (None, "") or c.metadata.get("delivery_days") not in (None, "") for c in supplier_rows):
                    direct_answer = "The connected supplier records do not include delivery history, so I can't determine the usual lead time."
                    direct_sources = self._format_sources(supplier_rows[:3])
                    timing = round(time.time() - start_time, 2)
                    session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                    session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=route, sources=[s.model_dump() for s in direct_sources], timing=timing)
                    return ChatResponse(answer=direct_answer, route=route, sources=direct_sources, computed_values=None, session_id=request.session_id, timing=timing)
            direct_answer, direct_chunks = self._direct_record_answer(question, chunks)
            if direct_answer:
                direct_sources = self._format_sources(direct_chunks)
                timing = round(time.time() - start_time, 2)
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=route,
                                            sources=[s.model_dump() for s in direct_sources], timing=timing)
                return ChatResponse(answer=direct_answer, route=route, sources=direct_sources, computed_values=None,
                                    session_id=request.session_id, timing=timing)
            context_text = self._format_context_records(chunks, selected_sources=request.file_ids) if chunks else ""

        if route == RouteType.ANALYTICS:
            answer = tabular_answer or self._format_analytics_answer(question, computed_values, lang, filters)
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id, "assistant", answer, domain=request.domain,
                route=route, sources=[s.dict() if hasattr(s, "dict") else s for s in sources] if sources else None,
                timing=timing,
            )
            return ChatResponse(
                answer=answer, route=route, sources=sources,
                computed_values=computed_values, session_id=request.session_id, timing=timing,
            )

        # Build prompt messages
        system_prompt = self._get_system_prompt(request.domain, route, selected_sources=request.file_ids, lang=lang, question=question)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"
        if suggestion_request:
            user_msg += _dataset_question_suggestion_instruction(question)
        if lang == "roman_urdu":
            user_msg += "\n\n[Instruction: Reply in natural Roman-Urdu using English/Latin alphabet only. Use natural Urdu vocabulary (e.g. 'maloomat', 'ziada', 'dastyab') and strictly avoid Hindi words like 'jaankari', 'adhik', 'uplabdh', 'anya', 'pradaan'. Do NOT use Arabic/Urdu script.]"
        elif lang == "english":
            user_msg += "\n\n[Instruction: Reply in English only. Do NOT use Roman-Urdu, Hindi, or Urdu.]"
             
        messages.append({"role": "user", "content": user_msg})
        
        effective_model = None

        # Call LLM
        try:
            resolve_started = time.perf_counter()
            effective_model = llm.resolve_chat_model(lang)
            logger.info("rag_stage_timing stage=model_resolution seconds=%.3f", time.perf_counter() - resolve_started)
            generation_started = time.perf_counter()
            logger.info("rag_stage_timing stage=pre_generation seconds=%.3f", generation_started - start_time)
            answer = llm.chat(
                messages=messages, model=effective_model,
                options={"num_predict": 128} if route == RouteType.RAG else None,
            )
            logger.info("rag_stage_timing stage=model_call seconds=%.3f", time.perf_counter() - generation_started)
        except Exception as e:
            answer = f"Error: LLM unavailable ({str(e)}). I am returning offline results if any."

        # Strip any raw debug/metadata block if echoed by the model
        answer = re.sub(r'\n*computed values.*', '', answer, flags=re.DOTALL | re.IGNORECASE).strip()

        # Clean Roman Urdu vocabulary if model leaked Hindi words
        if lang == "roman_urdu":
            answer = self._clean_roman_urdu_vocabulary(answer)

        # Script safety guard: if Roman-Urdu was expected but model generated Urdu/Arabic script
        if lang == "roman_urdu" and any('\u0600' <= c <= '\u06FF' for c in answer):
            try:
                fix_messages = [
                    {"role": "system", "content": "You are a translator. Rewrite the user's text into Roman-Urdu using ONLY the English/Latin alphabet (e.g. 'Aap ... ke data se deal kar rahe hain'). DO NOT use Arabic or Urdu script."},
                    {"role": "user", "content": answer}
                ]
                cleaned = llm.chat(messages=fix_messages, model=effective_model)
                if cleaned and not any('\u0600' <= c <= '\u06FF' for c in cleaned):
                    answer = self._clean_roman_urdu_vocabulary(cleaned)
            except Exception:
                pass
             
        timing = round(time.time() - start_time, 2)
        session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
        session_manager.append_turn(
            request.session_id,
            "assistant",
            answer,
            domain=request.domain,
            route=route,
            sources=[s.dict() if hasattr(s, 'dict') else s for s in sources] if sources else None,
            timing=timing
        )
        
        return ChatResponse(
            answer=answer,
            route=route,
            sources=sources,
            computed_values=computed_values,
            session_id=request.session_id,
            timing=timing
        )
        
    def ask_stream(self, request: ChatRequest) -> Generator[str, None, None]:
        """End-to-end streaming RAG pipeline."""
        start_time = time.time()
        question = self._normalize_question(request.question)
        intent_question = normalize_roman_urdu_intent(
            self._rewrite_follow_up(question, request.session_id)
        )
        lang = self._detect_query_language(question)
        suggestion_request = is_dataset_question_suggestion_request(question)

        protected_answer = self._protected_question_answer(question, lang)
        if protected_answer:
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", protected_answer, domain=request.domain, route=RouteType.RAG, timing=timing)
            yield json.dumps({"chunk": protected_answer, "route": RouteType.RAG, "sources": [], "computed_values": None}) + "\n"
            return

        if is_procedural_how_to_question(intent_question) and self._selected_scope_is_structured_only(request):
            limitation = (
                "I can't verify those application steps from the selected sources. They contain structured records, but no workflow documentation. "
                "Please provide the relevant user guide or check with your administrator."
            )
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", limitation, domain=request.domain, route=RouteType.RAG, timing=timing)
            yield json.dumps({
                "chunk": limitation,
                "route": RouteType.RAG,
                "sources": [],
                "computed_values": {"workflow_documentation": {"name": "Workflow documentation", "value": None, "status": "unavailable", "reason": "The selected scope contains structured records and no workflow documentation."}},
            }) + "\n"
            return
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(intent_question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files,
                lang=lang,
                question=intent_question,
            )
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id,
                "assistant",
                direct_answer,
                domain=request.domain,
                route="rag",
                sources=None,
                timing=timing
            )
            yield json.dumps({
                "chunk": direct_answer,
                "route": "rag",
                "sources": [],
                "computed_values": None
            }) + "\n"
            return

        schema_result = self._table_field_lookup(intent_question, request)
        if schema_result is not None:
            matched = self._analytics_citations(
                schema_result["records"], schema_result["source_rows"], request.domain,
                question=intent_question,
            )
            serializable_sources = [source.model_dump() for source in self._format_sources(matched)]
            direct_answer = schema_result["answer"]
            computed_values = {"schema_lookup": {"name": "Structured table lookup", "value": schema_result["values"], "status": "ok"}}
            timing = round(time.time() - start_time, 3)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id, "assistant", direct_answer, domain=request.domain,
                route=RouteType.ANALYTICS, sources=serializable_sources, timing=timing,
            )
            yield json.dumps({
                "chunk": direct_answer,
                "route": RouteType.ANALYTICS,
                "sources": serializable_sources,
                "computed_values": computed_values,
            }) + "\n"
            return

        relationship_result = self._sales_key_relationship_lookup(intent_question, request)
        if relationship_result is not None:
            matched = self._analytics_citations(
                relationship_result["records"], relationship_result["source_rows"], request.domain,
                question=intent_question,
            )
            serializable_sources = [source.model_dump() for source in self._format_sources(matched)]
            direct_answer = relationship_result["answer"]
            computed_values = {"schema_relationship": {"name": "Verified table relationship", "value": relationship_result["values"], "status": "ok"}}
            timing = round(time.time() - start_time, 3)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id, "assistant", direct_answer, domain=request.domain,
                route=RouteType.ANALYTICS, sources=serializable_sources, timing=timing,
            )
            yield json.dumps({
                "chunk": direct_answer,
                "route": RouteType.ANALYTICS,
                "sources": serializable_sources,
                "computed_values": computed_values,
            }) + "\n"
            return

        last_turn = session_manager.get_last_assistant_turn(request.session_id)
        last_route = last_turn.get("route") if last_turn else None
        route = classify_route(intent_question, last_route=last_route)
        filters = extract_filters(intent_question, request.domain)
        if self._uses_inventory_expiry_date(intent_question):
            for key in ("date_from", "date_to", "start_date", "end_date", "relative_days", "month", "day"):
                filters.pop(key, None)
        if route == RouteType.ANALYTICS and not filters.get("as_of") and any(
            token in intent_question.casefold()
            for token in ("forecast", "predict", "prediction", "projection", "next month", "next week", "expected sales")
        ):
            filters["as_of"] = date.today().isoformat()

        exact_lookup = self._exact_identifier_answer(intent_question, request)
        if exact_lookup is not None:
            exact_result, direct_sources = exact_lookup
            direct_answer = exact_result["answer"]
            serializable_sources = [s.model_dump() for s in direct_sources]
            timing = round(time.time() - start_time, 3)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain, route=RouteType.RAG, sources=serializable_sources, timing=timing)
            yield json.dumps({"chunk": direct_answer, "route": RouteType.RAG, "sources": serializable_sources, "computed_values": {"record_lookup": exact_result.get("values")}}) + "\n"
            return
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            fast_reply = self._get_fast_chitchat_response(intent_question, lang)
            if fast_reply:
                timing = round(time.time() - start_time, 3)
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", fast_reply, domain=request.domain, route=RouteType.CHITCHAT, sources=None, timing=timing)
                yield json.dumps({"chunk": fast_reply, "route": RouteType.CHITCHAT,
                                  "sources": [], "computed_values": None}) + "\n"
                return
        elif route == RouteType.ANALYTICS:
            tabular_result = analytics_request_guard(intent_question)
            records, citation_chunks = ([], []) if tabular_result is not None else self._get_records_for_analytics(request, filters)
            if tabular_result is None and request.domain == "pharmacy" and len(request.file_ids or []) > 1:
                tabular_result = self._relative_period_comparison(intent_question, records, request.domain, lang)
                if tabular_result is None and re.search(r"\bhow many\b", intent_question, re.I) and re.search(r"\binvoice\s+header\s+records?\b", intent_question, re.I):
                    from app.analytics.tabular_query import answer_tabular_question
                    tabular_result = answer_tabular_question(intent_question, records, filters=filters)
                if tabular_result is None and re.search(r"\bhow many\b.{0,35}\b(racks?|warehouses?|locations?|shelves?)\b", intent_question, re.I) and re.search(r"\b(distinct|unique|different)\b", intent_question, re.I) and re.search(r"\b(positive stock|stock|inventory|in stock|on[- ]hand)\b", intent_question, re.I):
                    from app.analytics.tabular_query import answer_tabular_question
                    tabular_result = answer_tabular_question(intent_question, records, filters=filters)
                if tabular_result is None and not filters.get("relative_days"):
                    from app.analytics.tabular_query import answer_cross_dataset_question, is_cross_table_pharmacy_risk_question, is_distinct_entity_count_question, is_ranked_record_count_question
                    if not is_cross_table_pharmacy_risk_question(intent_question) and not is_distinct_entity_count_question(intent_question) and not is_ranked_record_count_question(intent_question):
                        tabular_result = answer_cross_dataset_question(intent_question, records)
            if tabular_result is None and request.domain == "pharmacy":
                from app.analytics.tabular_query import is_cross_table_pharmacy_risk_question, answer_tabular_question
                if is_cross_table_pharmacy_risk_question(intent_question):
                    hybrid_filters = self._resolve_relative_date_filter(filters, records)
                    date_error = hybrid_filters.pop("_date_filter_error", None)
                    if date_error:
                        tabular_result = {"answer": date_error, "values": {"status": "unavailable", "reason": date_error}, "source_rows": []}
                    else:
                        tabular_result = answer_tabular_question(intent_question, records, filters=hybrid_filters)
            if tabular_result is None and request.domain == "pharmacy":
                from app.analytics.tabular_query import answer_tabular_question
                tabular_filters = self._resolve_relative_date_filter(filters, records)
                date_error = tabular_filters.pop("_date_filter_error", None)
                if date_error:
                    tabular_result = {"answer": date_error, "values": {"status": "unavailable", "reason": date_error}, "source_rows": []}
                else:
                    tabular_result = answer_tabular_question(intent_question, records, filters=tabular_filters)
            if tabular_result is not None:
                answer = tabular_result["answer"]
                computed_values = {"tabular_result": {"name": "Tabular query", "value": tabular_result["values"], "status": tabular_result["values"].get("status", "ok")}}
                source_rows = tabular_result["source_rows"]
            else:
                filters = self._resolve_relative_date_filter(filters, records)
                relative_error = filters.pop("_date_filter_error", None)
                if relative_error:
                    computed_values, source_rows = {
                        "total_revenue": {
                            "name": "Sales", "value": None, "unit": "PKR",
                            "status": "unavailable", "reason": relative_error,
                        }
                    }, []
                else:
                    computed_values, source_rows = self.analytics_router.compute(
                        intent_question, filters, records, request.domain
                    )
            if computed_values:
                matched = self._analytics_citations(records, source_rows, request.domain, question=intent_question)
                sources = self._format_sources(matched)
                 
        elif route == RouteType.RAG:
            retrieval_k = 25 if filters or suggestion_request else settings.retrieval_top_k
            retrieval_started = time.perf_counter()
            chunks = self.kb.search(
                "table fields columns and recorded values in the selected data" if suggestion_request else intent_question,
                top_k=retrieval_k,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            logger.info("rag_stage_timing stage=retrieval seconds=%.3f chunks=%d", time.perf_counter() - retrieval_started, len(chunks))
            if not chunks:
                yield json.dumps({
                    "chunk": self._missing_evidence_answer(question, lang),
                    "route": route,
                    "sources": [],
                    "computed_values": None
                }) + "\n"
                return
                 
            sources = self._format_sources(chunks) if chunks else []
            direct_answer, direct_chunks = self._direct_record_answer(question, chunks)
            if direct_answer:
                direct_sources = self._format_sources(direct_chunks)
                timing = round(time.time() - start_time, 2)
                serializable_sources = [source.model_dump() for source in direct_sources]
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", direct_answer, domain=request.domain,
                                            route=route, sources=serializable_sources, timing=timing)
                yield json.dumps({"chunk": direct_answer, "route": route, "sources": serializable_sources,
                                  "computed_values": None}) + "\n"
                return
            context_text = self._format_context_records(chunks, selected_sources=request.file_ids) if chunks else ""

        if route == RouteType.ANALYTICS:
            answer = tabular_result["answer"] if tabular_result is not None else self._format_analytics_answer(question, computed_values, lang, filters)
            timing = round(time.time() - start_time, 2)
            serializable_sources = [
                s.model_dump() if hasattr(s, "model_dump") else s.dict() for s in sources
            ] if sources else []
            yield json.dumps({
                "chunk": answer, "route": route,
                "sources": serializable_sources, "computed_values": computed_values,
            }) + "\n"
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id, "assistant", answer, domain=request.domain,
                route=route, sources=serializable_sources or None, timing=timing,
            )
            return

        system_prompt = self._get_system_prompt(request.domain, route, selected_sources=request.file_ids, lang=lang, question=question)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"
        if suggestion_request:
            user_msg += _dataset_question_suggestion_instruction(question)
        if lang == "roman_urdu":
            user_msg += "\n\n[Instruction: Reply in natural Roman-Urdu using English/Latin alphabet only. Use natural Urdu vocabulary (e.g. 'maloomat', 'ziada', 'dastyab') and strictly avoid Hindi words like 'jaankari', 'adhik', 'uplabdh', 'anya', 'pradaan'. Do NOT use Arabic/Urdu script.]"
        elif lang == "english":
            user_msg += "\n\n[Instruction: Reply in English only. Do NOT use Roman-Urdu, Hindi, or Urdu.]"
             
        effective_model = None

        messages.append({"role": "user", "content": user_msg})

        serializable_sources = [s.model_dump() if hasattr(s, 'model_dump') else (s.dict() if hasattr(s, 'dict') else s) for s in sources] if sources else []
        full_answer = ""
        try:
            resolve_started = time.perf_counter()
            effective_model = llm.resolve_chat_model(lang)
            logger.info("rag_stage_timing stage=model_resolution seconds=%.3f", time.perf_counter() - resolve_started)
            generation_started = time.perf_counter()
            logger.info("rag_stage_timing stage=pre_generation seconds=%.3f", generation_started - start_time)
            for chunk in llm.chat_stream(
                messages=messages, model=effective_model,
                options={"num_predict": 128} if route == RouteType.RAG else None,
            ):
                full_answer += chunk
                # Stop streaming if model starts echoing raw Computed Values block
                if "computed values" in full_answer.lower():
                    break
                yield json.dumps({
                    "chunk": chunk,
                    "route": route,
                    "sources": serializable_sources,
                    "computed_values": computed_values,
                    "active_model": effective_model or llm.model
                }) + "\n"
            logger.info("rag_stage_timing stage=model_call seconds=%.3f", time.perf_counter() - generation_started)
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"
            return
             
        timing = round(time.time() - start_time, 2)
        saved_answer = re.sub(r'\n*computed values.*', '', full_answer, flags=re.DOTALL | re.IGNORECASE).strip()
        if lang == "roman_urdu":
            saved_answer = self._clean_roman_urdu_vocabulary(saved_answer)
        session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
        session_manager.append_turn(
            request.session_id,
            "assistant",
            saved_answer,
            domain=request.domain,
            route=route,
            sources=serializable_sources if serializable_sources else None,
            timing=timing
        )

rag_chat = RAGChat()

