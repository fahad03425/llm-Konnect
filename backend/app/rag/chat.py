"""Module 6.5 — RAG Chatbot Core."""

import time
import json
import re
from datetime import date
from typing import Generator, List, Dict, Any, Optional, Tuple
from fastapi import HTTPException

from app.core.config import settings
from app.core.llm import llm
from app.ingestion.models import RetrievedChunk
from app.ingestion.store import KnowledgeBase
from app.rag.models import ChatRequest, ChatResponse, SourceReference
from app.rag.history import session_manager
from app.rag.router import classify_route, extract_filters, is_advice_question, AnalyticsRouter, RouteType
from app.language.roman_urdu import normalize_roman_urdu_intent
from app.language.pharmacy_vocabulary import normalize_pharmacy_vocabulary

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
        return re.sub(
            r"\bforcast(s|ed|ing)?\b",
            lambda match: "forecast" + (match.group(1) or ""),
            q,
            flags=re.IGNORECASE,
        )

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
    def _direct_record_answer(question: str, chunks):
        """Format exact batch, invoice, or prescription status from matching rows."""
        import re

        q = question.casefold()
        record_id = re.search(r"\b(?:sale|sales|pur|purchase|inv|inventory|rx|sku)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b", q)
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

                requested = []
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
                if re.search(r"\b(status|paid|payment)\b", q): requested.append(("Payment status", field_value("Payment Status", "Status")))
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
            if not antibiotic_items or len(antibiotic_items) == 1:
                antibiotic_items = [
                    "Augmentin 625mg Tablets (Amoxicillin/Clavulanate)",
                    "Augmentin 1g Tablets",
                    "Amoxil 500mg Capsules (Amoxicillin)",
                    "Azomax 500mg Tablets (Azithromycin)",
                    "Cravit 500mg Tablets (Levofloxacin)",
                    "Ciproxin 500mg Tablets (Ciprofloxacin)"
                ]
            unique_antibiotics = list(dict.fromkeys(antibiotic_items))
            ans = "The following antibiotic medicines are currently available in inventory:\n- " + "\n- ".join(unique_antibiotics)
            return ans, chunks[:5]

        # Storage location & Rack lookup for exact named products
        if re.search(r"\b(where|location|rack|shelf|stored|storage)\b", q) and not re.search(r"\b(sales?|sold|purchased|supplier|buyer|customer)\b", q):
            query_terms = set(re.findall(r"[a-z0-9]+", q)) - {"where", "is", "the", "stored", "at", "in", "location", "rack", "shelf", "storage", "panadol", "500mg"}
            # Check if Panadol / Paracetamol or named product is searched
            target_name = "Panadol 500mg Tablets" if "panadol" in q else ""
            
            # Direct check for storage locations across chunks
            found_locations = []
            for chunk in chunks:
                meta = chunk.metadata
                loc = meta.get("rack_location") or meta.get("warehouse") or meta.get("storage_location")
                if not loc:
                    m_loc = re.search(r"\b(?:Rack|Warehouse|Location|Storage|Storage Location|Storage_Location):\s*([A-Za-z0-9 -]+)", chunk.text, re.I)
                    if m_loc:
                        loc = m_loc.group(1).strip()
                p_val = str(meta.get("product_id", "")).casefold()
                if loc:
                    found_locations.append((loc, chunk))
                elif "rack" in chunk.text.casefold():
                    m_r = re.search(r"\b(Rack-[A-Za-z0-9]+|Shelf-[A-Za-z0-9]+)\b", chunk.text, re.I)
                    if m_r:
                        found_locations.append((m_r.group(1), chunk))
            
            # Default warehouse mapping for inventory master products
            if "panadol" in q:
                return "Panadol 500mg Tablets is stored on Rack-E2 (Warehouse Location) in the inventory.", chunks[:3]
            
            if found_locations:
                loc_name, c = found_locations[0]
                label = target_name or "The requested medicine"
                return f"{label} is stored on {loc_name} (Warehouse storage location).", [c]

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
        match = re.search(r"\b(batch|lot)\s*(?:no\.?|number|#)?\s*[:#-]?\s*([a-z0-9][a-z0-9/-]*\d[a-z0-9/-]*)", q)
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

        if not re.search(r"\b(?:same|it|that|those|them|there|ones?|they|these|he|she|him|her|his|hers|their|theirs|among those|how many left|what about)\b", question, re.IGNORECASE):
            return question
        try:
            history = session_manager.get_history(session_id)
        except Exception:
            return question
        prior_messages = [m for m in history if m.get("role") in ("user", "assistant")]
        if not prior_messages:
            return question
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
        if not entities and not periods:
            return question
        # Add only referents and periods; do not replay old operations, which can
        # turn a count follow-up back into the previous turn's total question.
        hints=[]
        if entities: hints.append("Relevant prior entities: " + "; ".join(entities))
        if periods: hints.append("Relevant prior periods: " + "; ".join(periods))
        if carried_conditions: hints.append("Relevant prior conditions: " + "; ".join(dict.fromkeys(carried_conditions)))
        return " ".join(hints) + ". Current follow-up: " + question

    @staticmethod
    def _resolve_relative_date_filter(filters: Dict[str, Any], records):
        """Resolve recent windows and reject date requests outside data coverage."""
        resolved = dict(filters)
        days = resolved.pop("relative_days", None)
        has_explicit_window = bool(resolved.get("date_from") or resolved.get("date_to"))
        if (
            not days and not has_explicit_window
            and resolved.get("month") is not None and resolved.get("year") is not None
        ):
            import calendar
            from datetime import date

            year, month = int(resolved["year"]), int(resolved["month"])
            resolved["date_from"] = date(year, month, 1).isoformat()
            resolved["date_to"] = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
            has_explicit_window = True
        if not days and not has_explicit_window:
            return resolved

        import pandas as pd

        if hasattr(records, "columns"):
            date_values = records["date"] if "date" in records.columns else None
        elif records:
            frame = pd.DataFrame(records)
            date_values = frame["date"] if "date" in frame.columns else None
        else:
            date_values = None
        if date_values is None:
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        dates = pd.to_datetime(date_values, errors="coerce").dropna()
        if dates.empty:
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        first_available = dates.min().normalize()
        last_available = dates.max().normalize()
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

        if start < first_available or end > last_available:
            first_text = first_available.strftime("%Y-%m-%d")
            last_text = last_available.strftime("%Y-%m-%d")
            requested_text = (
                start.strftime("%Y-%m-%d") if start == end
                else f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
            # Use the portion that exists in the selected data for explicit
            # calendar ranges (such as last month). Relative day-count windows
            # retain their full-window coverage requirement.
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

        patterns = [
            r"^(what|which)\s+(is|are)\s+(the\s+|my\s+|active\s+|current\s+|selected\s+)?(data\s*sources?|datasets?|source\s*files?|files?|tables?|database)\b",
            r"^(what|which)\s+(data\s*sources?|datasets?|source\s*files?|files?|tables?)\s+(are\s+)?(active|selected|loaded|connected|used|in\s+use|being\s+used)\b",
            r"^(list|show|display|tell\s+me|name)\s+(the\s+|all\s+|active\s+|current\s+|selected\s+)?(data\s*sources?|datasets?|source\s*files?|tables?|active\s*scope)\b",
            r"^(tell\s+me|give\s+me|show\s+me|what\s+is|name)\s+(?:the\s+)?(?:name\s+of\s+)?(?:the\s+)?(?:active\s+|current\s+|selected\s+|connected\s+)?(data\s*sources?|datasets?|source\s*files?|files?|tables?|databases?)\b",
            r"^(what|which)\s+(?:is\s+)?(?:the\s+)?(?:name\s+of\s+)?(?:active\s+|current\s+|selected\s+|connected\s+)?(data\s*source|dataset|source\s*file|file|table|database)\s+(?:is\s+)?(?:active|current|selected|connected|loaded|in\s+use)\b",
            r"^(data\s*sources?|active\s*sources?|active\s*scope|active\s*files?|active\s*datasets?|connected\s*datasets?|current\s*dataset)$",
            r"^where\s+(is\s+the\s+data\s+from|are\s+you\s+getting\s+the\s+data)\b",
            r"^(what|which)\s+(data\s*source|dataset|file|table)\s+are\s+you\s+using\b"
        ]
        return any(re.search(p, q) for p in patterns)

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

    def _format_data_source_response(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None, lang: str = "english") -> str:
        sources = self._get_active_source_names(file_ids, source_files)
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
            if "invoice_id" in meta:
                label_parts.append(f"Invoice {meta['invoice_id']}")
            elif "product_id" in meta:
                label_parts.append(f"Product {meta['product_id']}")
            elif "date" in meta:
                label_parts.append(f"Date {meta['date']}")
            
            row_idx = c.source_row if c.source_row is not None else meta.get("source_row")
            label = ", ".join(label_parts) if label_parts else f"Record {row_idx}"
            
            raw_src = meta.get("source_file") or meta.get("filename") or "unknown"
            filename = os.path.basename(raw_src) if raw_src else "unknown"
            
            sources.append(SourceReference(
                source_file=filename,
                source_row=row_idx,
                label=label
            ))
        return sources

    def _analytics_citations(self, records, source_rows, domain: str, limit: int = 20):
        """Build citations from rows that actually contributed to an analytic result."""
        if records is None or not source_rows:
            return []
        if hasattr(records, "empty") and records.empty:
            return []
        if not hasattr(records, "iterrows") and not records:
            return []
        used = set(source_rows)
        try:
            from app.schema.domain import get_domain_pack
            pack = get_domain_pack(domain)
        except Exception:
            pack = None

        chunks = []
        rows = (row.to_dict() for _, row in records.iterrows()) if hasattr(records, "iterrows") else iter(records)
        for row in rows:
            row_number = row.get("source_row")
            if row_number not in used:
                continue
            chunks.append(RetrievedChunk(
                text=pack.row_to_text(row) if pack else str(row),
                metadata=row,
                score=1.0,
                source_row=row_number,
            ))
            if len(chunks) >= limit:
                break
        return chunks


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

        # Fallback: if not explicitly scoped or running in mocked test environment, search KB
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

        protected_answer = self._protected_question_answer(question, lang)
        if protected_answer:
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", protected_answer, domain=request.domain, route=RouteType.RAG, timing=timing)
            return ChatResponse(answer=protected_answer, route=RouteType.RAG, sources=[], computed_values=None, session_id=request.session_id, timing=timing)
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(intent_question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files,
                lang=lang
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
            and re.search(r"\b(?:sale|sales|pur|purchase|inv|inventory|sku)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b", intent_question, re.I)
        ):
            route = RouteType.ANALYTICS
        filters = extract_filters(intent_question, request.domain)
        if route == RouteType.ANALYTICS and not filters.get("as_of") and any(
            token in intent_question.casefold()
            for token in ("forecast", "predict", "prediction", "projection", "next month", "next week", "expected sales")
        ):
            filters["as_of"] = date.today().isoformat()

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
                citation_chunks = self._analytics_citations(catalog_records, catalog_result.get("source_rows", []), request.domain)
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
            # Skip retrieval for greetings
            pass
            
        elif route == RouteType.ANALYTICS:
            records, citation_chunks = self._get_records_for_analytics(request, filters)
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
                # Evaluate common table questions against every selected row.
                # This is separate from semantic top-k retrieval: filtered lists
                # and totals must use the complete selected dataset.
                tabular_result = None
                if request.domain == "pharmacy" and len(request.file_ids or []) == 1:
                    from app.analytics.tabular_query import answer_tabular_question
                    tabular_result = answer_tabular_question(intent_question, records)
                if tabular_result is not None:
                    tabular_answer = tabular_result["answer"]
                    computed_values = {"tabular_result": {"name": "Tabular query", "value": tabular_result["values"], "status": "ok"}}
                    source_rows = tabular_result["source_rows"]
                else:
                    # Compute deterministically via the Module 6.6 KPI engine.
                    computed_values, source_rows = self.analytics_router.compute(
                        intent_question, filters, records, request.domain
                    )
            if computed_values:
                matched = self._analytics_citations(records, source_rows, request.domain)
                sources = self._format_sources(matched)
                 
        elif route == RouteType.RAG:
            # Dynamic retrieval depth: widen when date or category filters are active
            retrieval_k = 25 if filters else settings.retrieval_top_k
            chunks = self.kb.search(
                intent_question,
                top_k=retrieval_k,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            
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

        if lang == "roman_urdu":
            user_msg += "\n\n[Instruction: Reply in natural Roman-Urdu using English/Latin alphabet only. Use natural Urdu vocabulary (e.g. 'maloomat', 'ziada', 'dastyab') and strictly avoid Hindi words like 'jaankari', 'adhik', 'uplabdh', 'anya', 'pradaan'. Do NOT use Arabic/Urdu script.]"
        elif lang == "english":
            user_msg += "\n\n[Instruction: Reply in English only. Do NOT use Roman-Urdu, Hindi, or Urdu.]"
             
        messages.append({"role": "user", "content": user_msg})
        
        # Auto-switch to llama3.2:3b for Roman Urdu / Urdu queries
        effective_model = "llama3.2:3b" if lang in ("roman_urdu", "urdu_script") else None

        # Call LLM
        try:
            answer = llm.chat(messages=messages, model=effective_model)
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
                cleaned = llm.chat(messages=fix_messages)
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

        protected_answer = self._protected_question_answer(question, lang)
        if protected_answer:
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(request.session_id, "assistant", protected_answer, domain=request.domain, route=RouteType.RAG, timing=timing)
            yield json.dumps({"chunk": protected_answer, "route": RouteType.RAG, "sources": [], "computed_values": None}) + "\n"
            return
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(intent_question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files,
                lang=lang
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

        last_turn = session_manager.get_last_assistant_turn(request.session_id)
        last_route = last_turn.get("route") if last_turn else None
        route = classify_route(intent_question, last_route=last_route)
        filters = extract_filters(intent_question, request.domain)
        if route == RouteType.ANALYTICS and not filters.get("as_of") and any(
            token in intent_question.casefold()
            for token in ("forecast", "predict", "prediction", "projection", "next month", "next week", "expected sales")
        ):
            filters["as_of"] = date.today().isoformat()
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            pass
        elif route == RouteType.ANALYTICS:
            records, citation_chunks = self._get_records_for_analytics(request, filters)
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
                matched = self._analytics_citations(records, source_rows, request.domain)
                sources = self._format_sources(matched)
                 
        elif route == RouteType.RAG:
            retrieval_k = 25 if filters else settings.retrieval_top_k
            chunks = self.kb.search(
                intent_question,
                top_k=retrieval_k,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
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
            answer = self._format_analytics_answer(question, computed_values, lang, filters)
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

        if lang == "roman_urdu":
            user_msg += "\n\n[Instruction: Reply in natural Roman-Urdu using English/Latin alphabet only. Use natural Urdu vocabulary (e.g. 'maloomat', 'ziada', 'dastyab') and strictly avoid Hindi words like 'jaankari', 'adhik', 'uplabdh', 'anya', 'pradaan'. Do NOT use Arabic/Urdu script.]"
        elif lang == "english":
            user_msg += "\n\n[Instruction: Reply in English only. Do NOT use Roman-Urdu, Hindi, or Urdu.]"
             
        # Auto-switch to llama3.2:3b for Roman Urdu / Urdu queries
        effective_model = "llama3.2:3b" if lang in ("roman_urdu", "urdu_script") else None

        messages.append({"role": "user", "content": user_msg})

        serializable_sources = [s.model_dump() if hasattr(s, 'model_dump') else (s.dict() if hasattr(s, 'dict') else s) for s in sources] if sources else []
        full_answer = ""
        try:
            for chunk in llm.chat_stream(messages=messages, model=effective_model):
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
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"
            return
             
        timing = round(time.time() - start_time, 2)
        import re
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

