"""Auditable pharmacy vocabulary for intent normalization.

Aliases are deliberately attached to a narrow concept. For example, low stock
and reorder suggestions share inventory vocabulary but are not rewritten to the
same intent: they can require different thresholds or demand history.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, Tuple


# concept -> (domain area, canonical term, expressions)
PHARMACY_VOCABULARY: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "inventory.on_hand": ("inventory", "on hand stock", ("stock on hand", "on-hand", "current stock", "stock levels", "how much stock", "stock_qty", "qty on hand", "on hand qty", "available stock", "remaining stock", "stock bacha", "kitna stock", "how many are left", "how many left", "more stock", "highest stock", "stock scene")),
    "inventory.low": ("inventory", "low stock", ("low stock", "low in stock", "running low", "getting low", "what's running low", "what is running low", "nearly out", "almost out", "kam stock", "stock kam", "stock low")),
    "inventory.out": ("inventory", "out of stock", ("out of stock", "stockout", "stock-out", "zero stock", "no stock", "completely out", "oos", "stock khatam")),
    "inventory.reorder_point": ("inventory", "reorder point", ("reorder point", "reorder level", "minimum stock", "par level", "threshold stock")),
    "inventory.reorder_recommendation": ("inventory", "reorder recommendation", ("what should i reorder", "what to reorder", "what should we reorder", "what needs reordering", "what should i order", "replenishment recommendation", "order today", "reorder suggestions")),
    "inventory.stockout_risk": ("inventory", "stockout risk", ("may sell out soon", "might run out", "could run out", "likely to run out", "at risk of stockout", "items may sell out soon")),
    "inventory.expiry": ("inventory", "expiry date", ("expiry", "expiration", "expires", "expiring", "near expiry", "short dated", "short-dated", "expiry date", "miyad")),
    "inventory.batch": ("inventory", "batch number", ("batch", "batches", "lot number", "lot no", "batch no", "batch number", "find lot")),
    "inventory.damage": ("inventory", "damaged goods", ("damaged goods", "damaged stock", "damaged packs", "broken pack", "leaking", "write off", "write-off")),
    "inventory.return": ("inventory", "stock return", ("supplier return", "return to supplier", "goods returned", "returned stock", "units did we return", "return anything")),
    "inventory.adjustment": ("inventory", "stock adjustment", ("stock adjustment", "adjust stock", "inventory adjustment", "stock correction", "adjusted stock")),
    "purchasing.po": ("purchasing", "purchase order", ("purchase order", "po", "po number", "po #", "po-", "open po", "order to supplier")),
    "purchasing.price": ("purchasing", "supplier price", ("supplier price", "vendor price", "buying price", "purchase price", "purchases price", "landed cost", "charged", "cheapest price", "charged for")),
    "purchasing.lead_time": ("purchasing", "supplier lead time", ("lead time", "delivery time from supplier", "how long to arrive", "how long does", "usually take to deliver", "supplier turnaround")),
    "purchasing.invoice": ("purchasing", "supplier invoice", ("supplier invoice", "vendor invoice", "purchase invoice", "distributor bill", "find invoice", "show invoice", "invoice from", "invoice for")),
    "purchasing.payment": ("purchasing", "supplier payment", ("supplier payment", "pay supplier", "amount due to supplier", "payable to supplier", "supplier balance", "owe suppliers", "still owe suppliers")),
    "purchasing.shortage": ("purchasing", "order shortage", ("short shipped", "shortage on order", "missing from delivery", "quantity short", "come up short", "shortage on order")),
    "purchasing.alternative": ("purchasing", "product alternative", ("alternative product", "substitute product", "equivalent brand", "other brand", "use instead of", "instead of")),
    "finance.sales": ("sales_finance", "sales revenue", ("sales", "sale", "sold", "sell", "revenue", "turnover", "takings", "gross sales", "how much did we sell", "bikri", "farukht", "frokt")),
    "finance.invoice_total": ("sales_finance", "invoice total", ("invoice total", "total invoice", "inv total", "inv #")),
    "finance.purchases": ("sales_finance", "purchase spend", ("purchases", "purchase spend", "total purchases", "how much did we purchase", "purchasing spend")),
    "finance.margin": ("sales_finance", "margin", ("margin", "gross margin", "markup", "profit margin")),
    "finance.profit": ("sales_finance", "profit", ("profit", "net profit", "after refunds", "munafa", "nafa", "faida")),
    "finance.discount": ("sales_finance", "discount", ("discount", "markdown", "price reduction")),
    "finance.refund": ("sales_finance", "refund", ("refund", "refunds", "money back", "sale reversal", "customer return")),
    "finance.tax": ("sales_finance", "tax", ("tax", "gst", "vat", "sales tax")),
    "finance.cash_flow": ("sales_finance", "cash flow", ("cash flow", "cash position", "cash in and out", "liquidity", "cover payroll")),
    "finance.product_performance": ("sales_finance", "product performance", ("best seller", "best-selling", "top selling", "selling fastest", "fastest", "slow moving", "slow-moving", "dead stock", "product performance", "what did we sell")),
    "customer.prescription": ("customers_prescriptions", "prescription status", ("prescription status", "rx status", "prescription ready", "is my prescription ready", "is prescription", "prescription rx")),
    "customer.refill": ("customers_prescriptions", "refill request", ("refill", "repeat prescription", "repeat rx")),
    "customer.history": ("customers_prescriptions", "customer history", ("customer history", "purchase history", "patient history", "what did customer")),
    "customer.payer": ("customers_prescriptions", "payer coverage", ("insurance", "payer", "copay", "co-pay", "coverage")),
    "customer.delivery": ("customers_prescriptions", "delivery status", ("delivery status", "where is my delivery", "order tracking", "delivered yet", "has order", "been delivered")),
    "operations.staff": ("operations", "staffing", ("staffing", "staff rota", "shift schedule", "who is working", "who is on", "evening shift")),
    "operations.hours": ("operations", "opening hours", ("opening hours", "business hours", "when do we open", "closing time", "are we open")),
    "operations.report": ("operations", "report", ("report", "daily report", "audit report")),
    "operations.audit": ("operations", "audit trail", ("audit trail", "audit log", "who changed", "change history", "who approved", "who changed the")),
    "medicine.generic": ("medicines", "generic name", ("generic name", "generic", "what's the generic", "whats the generic", "active ingredient", "salt", "molecule")),
    "medicine.brand": ("medicines", "brand name", ("brand name", "trade name", "brand")),
    "medicine.strength": ("medicines", "strength", ("strength", "dose strength", "microgram", "dose")),
    "medicine.form": ("medicines", "dosage form", ("dosage form", "tablet", "caps", "capsule", "syrup", "cream", "injection")),
    "medicine.pack": ("medicines", "pack size", ("pack size", "pack of", "strip size", "units per pack", "one strip", "in one strip")),
    "compliance.controlled": ("compliance_safety", "controlled medicine", ("controlled medicine", "controlled drug", "schedule medicine", "narcotic")),
    "compliance.records": ("compliance_safety", "recordkeeping requirement", ("recordkeeping", "record keeping", "retention period", "records retention", "keep prescription records")),
    "compliance.storage": ("compliance_safety", "medicine storage", ("storage temperature", "what temperature", "at what temperature", "cold chain", "refrigeration", "store this medicine", "be stored", "safe for this patient")),
    "compliance.recall": ("compliance_safety", "product recall", ("product recall", "drug recall", "recall notice", "recalled batch", "was batch recalled", "was batch", "batch recalled")),
    "compliance.jurisdiction": ("compliance_safety", "jurisdictional rule", ("legal requirement", "regulatory requirement", "is it legal", "what does the law require", "tell me the law", "law for storing")),
}


def _patterns() -> Iterable[tuple[str, re.Pattern[str]]]:
    for concept, (_, _, aliases) in PHARMACY_VOCABULARY.items():
        for alias in aliases:
            # Keep expressions atomic and allow ordinary punctuation/spacing.
            escaped = re.escape(alias).replace(r"\ ", r"\s+").replace(r"\'", "['’]?")
            yield concept, re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.IGNORECASE)


_VOCAB_PATTERNS = tuple(_patterns())


def match_pharmacy_concepts(text: str) -> list[str]:
    """Return distinct concepts explicitly expressed in text, longest matches first."""
    q = (text or "").casefold()
    found = []
    for concept, pattern in _VOCAB_PATTERNS:
        if concept not in found and pattern.search(q):
            found.append(concept)
    if re.search(r"\b(?:invoice|inv\s*#?\s*\d+)[^.!?]{0,30}\b(?:total|amount)\b", q) and "finance.invoice_total" not in found:
        found.append("finance.invoice_total")
    if re.search(r"\b\d{1,4}\s?mg\b", q) and "medicine.strength" not in found:
        found.append("medicine.strength")
    return found


def normalize_pharmacy_vocabulary(text: str) -> str:
    """Expand selected common shorthand/typos while preserving the user's words."""
    q = text or ""
    substitutions = (
        (r"\b(?:qty|qnty)\b", "quantity"), (r"\b(?:exp|expir|expiryy)\b", "expiry"),
        (r"\b(?:reorder|re-order|re\s+order)\b", "reorder"),
        # Preserve a brand name such as Panadol; only correct misspellings of
        # the generic ingredient. Brand and generic may have different packs.
        (r"\bparacetmol\b", "paracetamol"),
        (r"\b(?:amoxycillin|amoxcillin)\b", "amoxicillin"),
        (r"\b(?:inv\.?|invc)\s*#?\s*(\d+)", r"invoice \1"),
        (r"\b(?:po)\s*#\s*([a-z0-9-]+)", r"purchase order \1"),
    )
    for pattern, replacement in substitutions:
        q = re.sub(pattern, replacement, q, flags=re.IGNORECASE)
    return q
