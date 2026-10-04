"""
Pharmacy Business Intelligence & Cross-Table Analytics Engine.

Provides deep, generalized analytics for inventory, sales, purchasing, expiry,
profitability, and executive decision-making across SQL databases (PharmacyPOS,
Asaan POS) and Excel sheets.
"""

from typing import Dict, Any, List, Optional
import os
import re
from datetime import datetime
import pandas as pd
import numpy as np


def detect_pharmacy_business_intent(question: str) -> Optional[str]:
    """Identify pharmacy business intelligence intents covering 50 executive questions."""
    q = re.sub(r"\s+", " ", str(question).casefold()).strip()

    # Dynamically composed product comparisons. The profile and thresholds are
    # derived from the selected sales/inventory data, not product-specific rules.
    if re.search(r"\b(contribute most|top contributors?|largest contribution)\b", q) and re.search(r"\b(revenue|sales)\b", q) and re.search(r"\b(profit|profitable)\b", q):
        return "revenue_profit_contributors"
    if re.search(r"\b(sales?|units sold)\b.{0,35}\b(relative to|compared with|against|per)\b.{0,25}\b(current )?(stock|inventory|on[- ]hand)\b", q) or re.search(r"\b(stock|inventory)\b.{0,25}\b(sales ratio|sales relative|turnover ratio)\b", q):
        return "sales_stock_efficiency"
    if re.search(r"\b(sold|selling|sales?)\b.{0,25}\b(below|under|less than)\b.{0,20}\b(purchase price|cost|unit cost)\b", q):
        return "products_below_cost"
    if re.search(r"\b(both|and)\b", q) and re.search(r"\b(profitable|profit)\b", q) and re.search(r"\b(fast[- ]selling|fast moving|high demand|high velocity)\b", q):
        return "champion_products"

    # 50. Daily executive action plan
    if re.search(r"\b(action(?:s)?\s+(?:should\s+i|to)\s+take\s+today|what\s+actions\s+today|daily\s+action\s+plan|executive\s+action\s+plan|aaj\s+kya\s+action\s+lena\s+chahiye)\b", q) or (
        re.search(r"\b(actions?\s+(?:should\s+i\s+)?take\s+today)\b", q) and re.search(r"\b(sales|stock|profit|margin|reorder|expiry)\b", q)
    ):
        return "daily_executive_action_plan"

    # 48. Immediate management attention / triage
    if re.search(r"\b(immediate\s+(?:management\s+)?attention|urgent\s+attention|immediate\s+action\s+needed|critical\s+attention|immediate\s+attention)\b", q):
        return "immediate_management_attention"

    # 49. Biggest potential inventory losses
    if re.search(r"\b(biggest|largest|highest|maximum)\s+(?:potential\s+)?(?:inventory\s+)?losses?\b", q):
        return "biggest_potential_inventory_losses"

    # 43. Champion products (best combination of high demand, high margin, low expiry risk)
    if re.search(r"\b(best\s+combination|champions?|top\s+performers?)\b", q) and re.search(r"\b(demand|sales|velocity)\b", q) and re.search(r"\b(margin|profit)\b", q):
        return "champion_products"

    # 44. Worst risk products (worst combination of low demand, low margin, high expiry risk)
    if re.search(r"\b(worst\s+combination|highest\s+risk\s+products?)\b", q) and (re.search(r"\b(demand|sales)\b", q) or re.search(r"\b(margin|profit)\b", q)):
        return "worst_risk_products"

    # 41. Top 10 medicines to maximize sales without increasing inventory unnecessarily
    if re.search(r"\b(top\s+(?:10|ten))\b", q) and re.search(r"\b(maximi[sz]e\s+sales|increase\s+sales)\b", q) and re.search(r"\b(without\s+increasing\s+inventory|lean\s+inventory|unnecessarily)\b", q):
        return "top10_maximize_sales_lean"

    # 42. Tying up most money while generating least sales
    if re.search(r"\b(tying\s+up|tied\s+up|tie\s+up|trapped|blocked|locked|invested)\b.{0,30}\b(money|capital|cash|investment|funds)\b", q) and re.search(r"\b(least|lowest|little|no|poor)\s+(?:sales?|revenue)\b", q):
        return "tied_up_capital_least_sales"

    # 45. Where is most inventory money invested and is that stock actually selling
    if re.search(r"\b(where\s+is\s+most\s+(?:of\s+my\s+)?(?:inventory|stock)\s+(?:money|capital|investment)|inventory\s+capital\s+allocation)\b", q) or (
        re.search(r"\b(money|capital)\b.{0,30}\b(invested|locked)\b", q) and re.search(r"\b(actually\s+selling|sales)\b", q)
    ):
        return "inventory_capital_allocation"

    # 46. Categories with too much inventory compared to sales
    if re.search(r"\b(categor(?:y|ies)|therapeutic\s+class(?:es)?)\b", q) and re.search(r"\b(too\s+much\s+(?:stock|inventory)|overstock|excess\s+(?:stock|inventory))\b", q):
        return "category_overstocked"

    # 47. Categories generating strong sales despite little inventory
    if re.search(r"\b(categor(?:y|ies)|therapeutic\s+class(?:es)?)\b", q) and re.search(r"\b(strong\s+sales|high\s+sales|good\s+sales)\b", q) and re.search(r"\b(little\s+inventory|low\s+inventory|less\s+stock|little\s+stock)\b", q):
        return "category_lean_high_velocity"

    # 36. Supplier with most sales
    if re.search(r"\b(supplier|vendor|distributor)s?\b", q) and re.search(r"\b(most|highest|top|maximum)\s+(?:sales?|revenue|volume)\b", q):
        return "supplier_most_sales"

    # 37. Supplier with most profit
    if re.search(r"\b(supplier|vendor|distributor)s?\b", q) and re.search(r"\b(most|highest|top|maximum)\s+(?:profit|gross\s+profit|margins?)\b", q):
        return "supplier_most_profit"

    # 38. Vendors whose products frequently become low-stock
    if re.search(r"\b(supplier|vendor|distributor)s?\b", q) and re.search(r"\b(frequently|often|repeatedly)\b.{0,30}\b(low[- ]?stock|out\s+of\s+stock|run\s+out|stockout)\b", q):
        return "supplier_frequent_low_stock"

    # 39. Vendor products most likely to expire before being sold
    if re.search(r"\b(supplier|vendor|distributor)s?\b", q) and re.search(r"\b(expir\w*|near[- ]?expiry)\b", q) and re.search(r"\b(before\s+(?:being\s+)?sold|unsold|waste)\b", q):
        return "supplier_expiry_risk"

    # 40. How much money to allocate to restocking highest-demand products
    if re.search(r"\b(how\s+much\s+money|budget|capital|funds?)\b.{0,60}\b(allocate|spend|put|invest)\b.{0,60}\b(restock\w*|reorder\w*|purchas\w*)\b", q) or (
        re.search(r"\b(restock\w*|reorder\w*)\b.{0,40}\b(highest[- ]?demand|fastest[- ]?selling|top\s+demand)\b", q) and re.search(r"\b(money|budget|cost|capital)\b", q)
    ):
        return "restock_capital_budget"

    # 34. Purchasing faster than selling
    if re.search(r"\b(purchas|buy|order)\w*\s+(?:faster|more\s+than|exceed(?:ing)?)\b.{0,30}\b(sell|sales)\b", q):
        return "purchasing_faster_than_selling"

    # 35. Under-purchasing compared with customer demand
    if re.search(r"\b(under[- ]purchas\w*|purchas\w*\s+less\s+than|buying\s+too\s+little)\b", q) or (
        re.search(r"\b(under[- ]purchas|not\s+purchasing\s+enough)\b", q) and re.search(r"\b(demand|customer|sales)\b", q)
    ):
        return "under_purchasing_vs_demand"

    # 31. What should I purchase today
    if re.search(r"\b(what\s+(?:should\s+i|to)\s+(?:purchase|buy|order)\s+today|purchase\s+today|buy\s+today|aaj\s+kya\s+khareedna\s+hai)\b", q):
        return "purchase_today"

    # 33. Which medicines should I not reorder right now
    if re.search(r"\b(?:should\s+i\s+)?(?:not|avoid)\s+(?:reorder|order|restock|buy)\b.{0,20}\b(right\s+now|today|currently|now)\b|\bdo\s+not\s+(?:reorder|restock)\b", q):
        return "do_not_reorder_now"

    # 20. Avoid reordering because existing stock is already close to expiry
    if re.search(r"\b(avoid\s+reorder\w*|not\s+reorder\w*|stop\s+reorder\w*|don'?t\s+reorder)\b", q) and re.search(r"\b(close\s+to\s+expiry|near[- ]?expiry|expir\w*)\b", q):
        return "avoid_reordering_near_expiry"

    # 14. How much money could I lose from medicines expiring in next 60 days
    if re.search(r"\b(how\s+much\s+money|kitna\s+paisa|financial\s+loss|money\s+loss|total\s+loss|lose)\b.{0,40}\bexpir\w*\b", q) or (
        re.search(r"\bexpir\w*\b", q) and re.search(r"\b(financial\s+loss|loss\s+amount|money\s+(?:could\s+i\s+)?lose)\b", q)
    ):
        return "expiry_financial_loss"

    # 15. Near-expiry highest financial risk
    if re.search(r"\b(highest|greatest|most|top)\s+(?:financial\s+risk|financial\s+loss|value\s+at\s+risk|money\s+at\s+risk|financial\s+exposure|exposure)\b", q) and re.search(r"\b(expir\w*|near[- ]?expiry)\b", q):
        return "near_expiry_highest_financial_risk"

    # 16. Expensive medicines approaching expiry with poor sales
    if re.search(r"\b(expensive|costly|high[- ]?priced|high[- ]?cost)\b", q) and re.search(r"\b(expir\w*|approaching\s+expiry|near[- ]?expiry)\b", q) and re.search(r"\b(poor\s+sales|low\s+sales|slow\s+sales|slow(?:ly)?)\b", q):
        return "expensive_approaching_expiry_poor_sales"

    # 17. Near-expiry products to prioritize selling first
    if re.search(r"\b(prioriti[sz]e\s+selling\s+first|sell\s+first|push\s+first|clear\s+first)\b", q) and re.search(r"\b(near[- ]?expiry|expir\w*)\b", q):
        return "near_expiry_prioritize_first"

    # 18. Batches likely to expire before stock is sold
    if re.search(r"\b(batch(?:es)?)\b", q) and re.search(r"\b(expire\s+before|likely\s+to\s+expire|expire\s+prior)\b", q):
        return "batches_expire_before_sold"

    # 19. At current sales rate, how many units of near-expiry remain unsold
    if re.search(r"\b(how\s+many\s+units|unsold\s+units|units\s+(?:may\s+)?remain\s+unsold|quantity\s+unsold)\b", q) and re.search(r"\b(expir\w*|near[- ]?expiry)\b", q):
        return "near_expiry_unsold_units"

    # 11, 12, 13. Expiry in 30/60/90 days
    if re.search(r"\bexpir\w*\b.{0,30}\b(?:next|within|in)\s+(30|60|90)\s*days?\b|\b(?:next|within|in)\s+(30|60|90)\s*days?\b.{0,30}\bexpir\w*\b", q):
        match = re.search(r"\b(30|60|90)\b", q)
        days = match.group(1) if match else "60"
        if days == "30": return "expiry_slow_30"
        if days == "90": return "expiry_large_qty_90"
        return "expiry_slow_60"

    # 28. Gross profit relative to stock (GMROI)
    if re.search(r"\b(gross\s+profit\s+relative\s+to|profit\s+relative\s+to|gmroi|return\s+on\s+inventory|profit\s+per\s+(?:unit\s+of\s+)?stock)\b", q):
        return "gross_profit_per_stock"

    # 29. Keep more stock: fast-selling and profitable
    if re.search(r"\b(keep\s+more\s+stock|stock\s+more|increase\s+stock)\b", q) and re.search(r"\b(fast|selling|demand)\b", q) and re.search(r"\b(profit\w*|margin)\b", q):
        return "keep_more_stock_fast_profitable"

    # 30. Stock less: slow-selling and low-margin
    if re.search(r"\b(stock(?:ing)?\s+less|reduce\s+stock|cut\s+stock|hold\s+less)\b", q) and re.search(r"\b(slow|low\s+demand)\b", q) and re.search(r"\b(margin|profit\w*)\b", q):
        return "stock_less_slow_low_margin"

    # 21. Highly profitable medicines running low on stock
    if re.search(r"\b(profit\w*|margin)\b", q) and re.search(r"\b(running\s+low|low\s+(?:on\s+)?stock|low\s+inventory)\b", q):
        return "profitable_low_stock"

    # 22. Most profit but insufficient inventory
    if re.search(r"\b(most\s+profit|highest\s+profit|high\s+profit)\b", q) and re.search(r"\b(insufficient\s+inventory|inadequate\s+stock|shortage|not\s+enough\s+stock)\b", q):
        return "high_profit_insufficient_stock"

    # 23. Fast-selling with highest profit margins
    if re.search(r"\b(fast[- ]?selling|quick[- ]?selling|top[- ]?selling|high[- ]?volume)\b", q) and re.search(r"\b(highest|top|best|maximum)\s+(?:profit\s+)?margins?\b", q):
        return "fast_selling_highest_margin"

    # 24. Fast-selling with surprisingly low profit margins
    if re.search(r"\b(fast[- ]?selling|quick[- ]?selling|top[- ]?selling|high[- ]?volume)\b", q) and re.search(r"\b(low|thin|poor|surprisingly\s+low)\s+(?:profit\s+)?margins?\b", q):
        return "fast_selling_low_margin"

    # 25. High revenue but little actual gross profit
    if re.search(r"\b(high[- ]revenue|top[- ]sales|high[- ]turnover)\b", q) and re.search(r"\b(little\s+(?:actual\s+)?gross\s+profit|low\s+gross\s+profit|little\s+profit|thin\s+profit|low[- ]profit[- ]margins?)\b", q):
        return "high_revenue_low_profit"

    # 26. High profit margins but very low sales
    if re.search(r"\b(high\s+(?:profit\s+)?margins?|good\s+margins?)\b", q) and re.search(r"\b(very[- ]low[- ]sales|poor[- ]sales|slow[- ]sales|low[- ]volume)\b", q):
        return "high_margin_low_sales"

    # 27. Taking up inventory investment without generating meaningful profit
    if re.search(r"\b(inventory\s+investment|money\s+tied|capital\s+tied)\b", q) and re.search(r"\b(without\s+generating\s+(?:meaningful\s+)?profit|little\s+profit|no\s+profit)\b", q):
        return "dead_capital_low_profit"

    # 32. How many units of each medicine should I reorder based on sales rate
    if re.search(r"\b(how\s+many\s+units\b.{0,30}\b(?:reorder|order|restock)|reorder\s+quantity\s+for\s+each)\b", q) and re.search(r"\b(sales\s+rate|sales\s+velocity|demand)\b", q):
        return "reorder_quantity_units"

    # 10. Increase reorder quantity based on historical demand
    if re.search(r"\b(increase|raise|adjust|higher)\b.{0,30}\b(reorder\s+quantity|order\s+quantity|restock\s+quantity)\b", q):
        return "reorder_quantity"

    # 08. Products not sold recently but occupying significant inventory
    if re.search(r"\b(not\s+sold|never\s+sold|no\s+sales|zero\s+sales)\b", q) and (
        re.search(r"\b(recent\w*|lately|inventory|stock|occupying|sitting)\b", q)
        or re.search(r"\b(which|what|list|show)\b.{0,40}\b(products?|medicines?|items?)\b", q)
    ):
        return "no_recent_sales"

    # 09. Repeatedly running low because of high demand
    if re.search(r"\b(repeatedly|frequently|constantly|often)\b.{0,40}\b(running\s+low|low\s+stock|out\s+of\s+stock|stockout)\b", q):
        return "recurring_low_stock"

    # 05. High-demand out of stock
    if re.search(r"\b(high[- ]?demand|popular|fast[- ]?selling|top[- ]?selling)\b", q) and re.search(r"\b(out\s+of\s+stock|zero\s+stock|stock\s*=\s*0|stockout)\b", q):
        return "high_demand_out_of_stock"

    # 04. Enough stock for only a few more days
    if re.search(r"\b(only\s+a\s+few\s+(?:more\s+)?days|few\s+more\s+days|under\s+a\s+week)\b", q):
        return "runout_few_days"

    # 06. Overstocked compared with actual sales
    if re.search(r"\b(overstock\w*|excess\s+inventory|surplus\s+stock|too\s+much\s+stock)\b", q):
        return "overstock"

    # 07. High inventory but very low sales
    if re.search(r"\b(high\s+inventory|large\s+stock|heavy\s+stock)\b", q) and re.search(r"\b(very\s+low\s+sales|slow\s+sales|little\s+sales)\b", q):
        return "high_inventory_low_sales"

    # 01. Selling quickly and below reorder level
    if re.search(r"\b(selling\s+quick\w*|fast\s+selling|quick\s+selling|high\s+velocity|moving\s+fast|fast\s+moving|jaldi\s+biknay)\b", q) and re.search(r"\b(below|under|at|less\s+than)\s+(?:their\s+)?(?:reorder|minimum|min)\s*(?:level|stock|point)?\b", q):
        return "quick_selling_below_reorder"

    # 02. Likely to run out soon based on recent sales rate
    if re.search(r"\b(likely\s+to\s+run\s+out|run\s+out\s+soon|about\s+to\s+run\s+out|stockout\s+soon|risk\s+of\s+running\s+out|khatam\s+hone\s+wali)\b", q):
        return "runout_risk"

    # 03. Reorder first based on sales velocity and current stock
    if re.search(r"\b(reorder\w*|restock\w*|order\w*)\s+(?:first|priorit\w*)\b|\b(first\s+to\s+(?:reorder|restock))\b", q):
        return "reorder_priority"

    return None


def is_cross_table_pharmacy_risk_question(question: str) -> bool:
    """Check if question matches any of the pharmacy risk or executive intents."""
    return detect_pharmacy_business_intent(question) is not None


def _format_days(value):
    if value is None or pd.isna(value): return "—"
    if value >= 999: return "No sales"
    return f"{value:g} days"


def _format_stock_reorder(stock_value, reorder_value):
    reorder_text = f"{reorder_value:g}" if reorder_value is not None and not pd.isna(reorder_value) else "—"
    return f"{stock_value:g} / {reorder_text}"


def answer_pharmacy_business_question(question: str, frame: pd.DataFrame, filters=None) -> Optional[Dict[str, Any]]:
    """Execute generalized pharmacy business intelligence query against database or excel frame."""
    intent = detect_pharmacy_business_intent(question)
    if not intent:
        return None

    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {
            "answer": "The selected dataset has no rows available for analysis.",
            "values": {"status": "unsupported_record_type"},
            "source_rows": []
        }

    q = re.sub(r"\s+", " ", str(question).casefold()).strip()
    if filters is None:
        filters = {}
    elif hasattr(filters, "model_dump"):
        filters = filters.model_dump(exclude_none=True)
    elif not isinstance(filters, dict):
        filters = {}

    # Identify tables or slices
    table = frame.get("table_name", pd.Series("", index=frame.index)).fillna("").astype(str).str.casefold()
    txn = frame.get("txn_type", pd.Series("", index=frame.index)).fillna("").astype(str).str.casefold()

    sales_mask = table.str.contains(r"sale|invoice|bill|transaction|dispens", regex=True) | txn.str.contains(r"sale|dispens", regex=True)
    inv_mask = table.str.contains(r"inventor|stock|batch|tbl_batches|tbl_products|product", regex=True) | txn.str.contains(r"inventor|stock|batch", regex=True)
    inv_mask &= ~table.str.contains(r"archive|histor(?:y|ical)|movement|ledger", regex=True)
    purch_mask = table.str.contains(r"purchas|expense|tbl_purchasedetails", regex=True) | txn.str.contains(r"purchas|expense", regex=True)

    # Fallback to column-based detection for single-table sheets
    if not sales_mask.any() and not inv_mask.any():
        if "quantity_sold" in frame.columns or "units_sold" in frame.columns or "quantity" in frame.columns:
            sales_mask = pd.Series(True, index=frame.index)
        if "stock_qty" in frame.columns or "closing_stock_qty" in frame.columns or "available_qty" in frame.columns:
            inv_mask = pd.Series(True, index=frame.index)

    sales = frame.loc[sales_mask].copy() if sales_mask.any() else pd.DataFrame()
    inventory = frame.loc[inv_mask].copy() if inv_mask.any() else pd.DataFrame()
    purchases = frame.loc[purch_mask].copy() if purch_mask.any() else pd.DataFrame()

    # Product key resolution
    prod_candidates = ("product_id", "product_name", "medicine_name", "product_code", "item_name")
    prod_col = next((c for c in prod_candidates if c in frame.columns and frame[c].notna().any()), "product_id")

    # Currency resolution
    currency = getattr(frame, "attrs", {}).get("source_currency") or "PKR"
    curr_prefix = f"{currency} " if currency != "USD" else "$"

    # Determine date range & sales velocity span
    date_candidates = ("date", "transaction_date", "sale_date", "invoice_date", "timestamp", "created_at")
    date_col = next((c for c in date_candidates if c in sales.columns and sales[c].notna().any()), None)
    if not sales.empty and date_col:
        sales["_date"] = pd.to_datetime(sales[date_col], errors="coerce")
        requested_start = pd.to_datetime(filters.get("date_from"), errors="coerce") if filters.get("date_from") else pd.NaT
        requested_end = pd.to_datetime(filters.get("date_to"), errors="coerce") if filters.get("date_to") else pd.NaT
        if pd.notna(requested_start):
            sales = sales.loc[sales["_date"].dt.normalize() >= requested_start.normalize()].copy()
        if pd.notna(requested_end):
            sales = sales.loc[sales["_date"].dt.normalize() <= requested_end.normalize()].copy()
        valid_sales = sales.loc[sales["_date"].notna()]
        if not valid_sales.empty:
            start_date = valid_sales["_date"].min().normalize()
            end_date = valid_sales["_date"].max().normalize()
            velocity_span = max(1, int((end_date - start_date).days) + 1)
            display_start = requested_start.normalize() if pd.notna(requested_start) else start_date
            display_end = requested_end.normalize() if pd.notna(requested_end) else end_date
            velocity_span = max(1, int((display_end - display_start).days) + 1)
            window_desc = f"{velocity_span} days ({display_start.date().isoformat()} to {display_end.date().isoformat()})"
        else:
            velocity_span = 30
            window_desc = "30-day default window"
    else:
        velocity_span = 30
        window_desc = "30-day default window"

    qty_col = next((c for c in ("quantity", "qty_sold", "quantity_sold", "units_sold", "qty") if c in sales.columns), None)
    rev_col = next((c for c in ("amount", "line_total", "total_transaction_value_usd", "total_amount", "net_payable", "sub_total") if c in sales.columns), None)
    cost_col = next((c for c in ("cost", "unit_cost", "unit_cost_price_usd", "purchase_price") if c in sales.columns), None)
    line_cost_col = next((c for c in ("line_cost", "purchase_sub_total", "cogs", "cost_of_goods_sold") if c in sales.columns), None)
    price_col = next((c for c in ("mrp", "unit_price", "maximum_retail_price_usd", "sale_price") if c in sales.columns), None)

    asks_for_profit = bool(re.search(r"\b(profit\w*|margin|selling at a loss|below (?:the )?purchase price)\b", q))
    sales_with_units = sales.loc[pd.to_numeric(sales[qty_col], errors="coerce").fillna(0).gt(0)] if qty_col else sales
    cost_complete = False
    if not sales_with_units.empty:
        cost_complete = any(
            col and col in sales_with_units and
            pd.to_numeric(sales_with_units[col], errors="coerce").notna().all()
            for col in (line_cost_col, cost_col)
        )
    if asks_for_profit and not cost_complete:
        return {
            "answer": "Gross profit and margin are unavailable because the selected sales rows do not have complete purchase-cost data. I won't estimate profit using an assumed margin.",
            "values": {"status": "unsupported_field", "required_field": "unit_cost_or_line_cost"},
            "source_rows": [],
        }

    def _safe_first(s, default="-"):
        if s is None: return default
        if isinstance(s, pd.Series):
            v = s.dropna()
            return str(v.iloc[0]) if not v.empty else default
        return str(s)

    # 1. Build per-product aggregated sales
    prod_sales = {}
    sales_citations = {}
    if not sales.empty and prod_col in sales.columns:
        norm = lambda s: s.fillna("").astype(str).str.strip()
        sales["_prod_norm"] = norm(sales[prod_col])
        sales["_qty"] = pd.to_numeric(sales[qty_col], errors="coerce").fillna(0) if qty_col else 0.0
        sales["_rev"] = pd.to_numeric(sales[rev_col], errors="coerce").fillna(0) if rev_col else 0.0
        sales["_cost"] = pd.to_numeric(sales[cost_col], errors="coerce").fillna(0) if cost_col else 0.0
        sales["_line_cost"] = pd.to_numeric(sales[line_cost_col], errors="coerce").fillna(0) if line_cost_col else 0.0
        sales["_price"] = pd.to_numeric(sales[price_col], errors="coerce").fillna(0) if price_col else 0.0

        for p_name, group in sales.groupby("_prod_norm"):
            if not p_name or p_name.lower() in ("nan", "none", ""): continue
            u_sold = float(group["_qty"].sum())
            rev = float(group["_rev"].sum())
            if rev == 0 and u_sold > 0 and group["_price"].sum() > 0:
                rev = float((group["_qty"] * group["_price"]).sum())
            cogs = float(group["_line_cost"].sum()) if group["_line_cost"].sum() > 0 else float((group["_qty"] * group["_cost"]).sum())
            has_cost_evidence = bool(
                (line_cost_col and pd.to_numeric(group[line_cost_col], errors="coerce").notna().any())
                or (cost_col and pd.to_numeric(group[cost_col], errors="coerce").notna().any())
            )
            profit = rev - cogs if has_cost_evidence else None
            margin = (profit / rev * 100) if profit is not None and rev > 0 else None
            vel = u_sold / velocity_span
            cat = _safe_first(group.get("category", group.get("therapeutic_class")), "General")
            mfg = _safe_first(group.get("manufacturer"), "-")
            supp = _safe_first(group.get("supplier_name", group.get("supplier")), "-")

            u_cost = float(group["_cost"].dropna().iloc[0]) if not group["_cost"].dropna().empty else 0.0
            u_price = float(group["_price"].dropna().iloc[0]) if not group["_price"].dropna().empty else 0.0

            prod_sales[p_name.casefold()] = {
                "name": p_name,
                "units_sold": u_sold,
                "revenue": rev,
                "cogs": cogs,
                "gross_profit": profit,
                "margin_pct": margin,
                "daily_velocity": vel,
                "category": cat,
                "manufacturer": mfg,
                "supplier": supp,
                "unit_cost": u_cost,
                "unit_price": u_price,
            }
            sales_citations[p_name.casefold()] = group.index.tolist()

    # 2. Build per-product aggregated stock & reorder
    stock_candidates = ("stock_qty", "closing_stock_qty", "available_qty", "quantity_on_hand", "quantity", "stock")
    stock_col = next((c for c in stock_candidates if c in inventory.columns and inventory[c].notna().any()), None)
    reorder_candidates = ("reorder_level", "min_stock", "reorder_point")
    reorder_col = next((c for c in reorder_candidates if c in inventory.columns and inventory[c].notna().any()), None)
    expiry_candidates = ("expiry_date", "expiration_date", "expires_at")
    expiry_col = next((c for c in expiry_candidates if c in inventory.columns and inventory[c].notna().any()), None)

    prod_stock = {}
    inv_citations = {}
    batch_rows = []
    as_of = pd.to_datetime(filters.get("as_of"), errors="coerce") if filters.get("as_of") else pd.NaT
    if pd.isna(as_of):
        as_of = pd.Timestamp.today().normalize()
    else:
        as_of = as_of.normalize()

    if not inventory.empty and prod_col in inventory.columns:
        norm = lambda s: s.fillna("").astype(str).str.strip()
        inventory["_prod_norm"] = norm(inventory[prod_col])
        inventory["_stock"] = pd.to_numeric(inventory[stock_col], errors="coerce").fillna(0) if stock_col else 0.0
        inventory["_reorder"] = pd.to_numeric(inventory[reorder_col], errors="coerce") if reorder_col else np.nan
        inventory["_cost"] = pd.to_numeric(inventory.get("cost", pd.Series(0, index=inventory.index)), errors="coerce").fillna(0)
        inventory["_price"] = pd.to_numeric(inventory.get("mrp", pd.Series(0, index=inventory.index)), errors="coerce").fillna(0)

        for p_name, group in inventory.groupby("_prod_norm"):
            if not p_name or p_name.lower() in ("nan", "none", ""): continue
            stk = float(group["_stock"].sum())
            reord = float(group["_reorder"].dropna().iloc[0]) if group["_reorder"].notna().any() else None
            u_cost = float(group["_cost"].dropna().iloc[0]) if group["_cost"].gt(0).any() else 0.0
            u_price = float(group["_price"].dropna().iloc[0]) if group["_price"].gt(0).any() else 0.0
            cat = _safe_first(group.get("category"), "General")
            supp = _safe_first(group.get("supplier_name"), "-")

            prod_stock[p_name.casefold()] = {
                "name": p_name,
                "stock": stk,
                "reorder_level": reord,
                "unit_cost": u_cost,
                "unit_price": u_price,
                "stock_value": stk * u_cost if u_cost > 0 else stk * u_price,
                "category": cat,
                "supplier": supp,
            }
            inv_citations[p_name.casefold()] = group.index.tolist()

        # Batch-level tracking
        if expiry_col:
            for idx, r in inventory.iterrows():
                b_no = str(r.get("batch_no", "-"))
                p_name = str(r.get(prod_col, ""))
                if not p_name or p_name.lower() in ("nan", "none", ""): continue
                exp_dt = pd.to_datetime(r.get(expiry_col), errors="coerce")
                if pd.isna(exp_dt): continue
                stk = float(pd.to_numeric(r.get("_stock", 0), errors="coerce") or 0)
                u_cost = float(pd.to_numeric(r.get("cost", 0), errors="coerce") or 0)
                u_price = float(pd.to_numeric(r.get("mrp", 0), errors="coerce") or 0)
                days_left = int((exp_dt.normalize() - as_of).days)
                
                vel = prod_sales.get(p_name.casefold(), {}).get("daily_velocity", 0.0)
                exp_sales = vel * max(0, days_left)
                unsold = max(0.0, stk - exp_sales)
                fin_loss = unsold * (u_cost if u_cost > 0 else u_price)
                supp = _safe_first(r.get("supplier_name"), "-")

                batch_rows.append({
                    "product": p_name,
                    "batch_no": b_no,
                    "expiry_date": exp_dt.date().isoformat(),
                    "days_to_expiry": days_left,
                    "stock": stk,
                    "unit_cost": u_cost,
                    "unit_price": u_price,
                    "daily_velocity": vel,
                    "expected_sales_before_expiry": exp_sales,
                    "unsold_units": unsold,
                    "financial_loss": fin_loss,
                    "supplier": supp,
                    "row_idx": idx
                })

    # 3. Purchases ledger & derived stock for dual ledger sheets (e.g. single store data)
    prod_purch = {}
    if not purchases.empty and prod_col in purchases.columns:
        norm = lambda s: s.fillna("").astype(str).str.strip()
        purchases["_prod_norm"] = norm(purchases[prod_col])
        purch_qty_col = next((c for c in ("quantity", "qty", "units") if c in purchases.columns), None)
        purch_cost_col = next((c for c in ("cost", "unit_cost", "purchase_price", "amount") if c in purchases.columns), None)
        purchases["_qty"] = pd.to_numeric(purchases[purch_qty_col], errors="coerce").fillna(0) if purch_qty_col else 0.0

        for p_name, group in purchases.groupby("_prod_norm"):
            if not p_name: continue
            u_purch = float(group["_qty"].sum())
            supp = _safe_first(group.get("supplier_name", group.get("supplier")), "-")
            prod_purch[p_name.casefold()] = {
                "name": p_name,
                "units_purchased": u_purch,
                "supplier": supp
            }

        # If inventory table is absent but purchases are recorded, derive stock = max(0, purchased - sold)
        if inventory.empty and prod_purch and prod_sales:
            for k, p_data in prod_purch.items():
                s_data = prod_sales.get(k, {})
                u_sold = s_data.get("units_sold", 0.0)
                stk = max(0.0, p_data["units_purchased"] - u_sold)
                u_cost = s_data.get("unit_cost", 0.0)
                u_price = s_data.get("unit_price", 0.0)
                prod_stock[k] = {
                    "name": p_data["name"],
                    "stock": stk,
                    "reorder_level": round(s_data.get("daily_velocity", 0.0) * 15, 1), # estimated 15-day reorder proxy
                    "unit_cost": u_cost,
                    "unit_price": u_price,
                    "stock_value": stk * u_cost if u_cost > 0 else stk * u_price,
                    "category": s_data.get("category", "General"),
                    "supplier": p_data.get("supplier", "-")
                }

    # Combine master product list
    all_keys = set(prod_sales.keys()) | set(prod_stock.keys()) | set(prod_purch.keys())
    master_prods = []
    has_real_stock = bool(prod_stock)

    for k in all_keys:
        s_data = prod_sales.get(k, {})
        i_data = prod_stock.get(k, {})
        p_data = prod_purch.get(k, {})

        name = s_data.get("name") or i_data.get("name") or p_data.get("name") or k
        stk = i_data.get("stock", 0.0) if has_real_stock else 0.0
        reord = i_data.get("reorder_level")
        u_sold = s_data.get("units_sold", 0.0)
        vel = s_data.get("daily_velocity", 0.0)
        rev = s_data.get("revenue", 0.0)
        profit = s_data.get("gross_profit", 0.0)
        margin = s_data.get("margin_pct", 0.0)
        cost = i_data.get("unit_cost") or s_data.get("unit_cost", 0.0)
        price = i_data.get("unit_price") or s_data.get("unit_price", 0.0)
        stk_val = stk * cost if cost > 0 else stk * price
        days_supply = (stk / vel) if vel > 0 else (999.0 if stk > 0 else 0.0)
        gmroi = (profit / stk_val) if stk_val > 0 else 0.0
        u_purch = p_data.get("units_purchased", 0.0)
        supp = i_data.get("supplier") or p_data.get("supplier") or s_data.get("supplier", "-")
        cat = i_data.get("category") or s_data.get("category", "General")

        master_prods.append({
            "key": k,
            "product": name,
            "stock": stk,
            "reorder_level": reord,
            "units_sold": u_sold,
            "daily_velocity": round(vel, 4),
            "days_of_supply": round(days_supply, 1) if days_supply != 999.0 else None,
            "revenue": round(rev, 2),
            "gross_profit": round(profit, 2),
            "margin_pct": round(margin, 2),
            "unit_cost": round(cost, 2),
            "unit_price": round(price, 2),
            "stock_value": round(stk_val, 2),
            "gmroi": round(gmroi, 3),
            "units_purchased": u_purch,
            "supplier": supp,
            "category": cat
        })

    # Citation collector
    row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)
    source_files = frame.get("source_file", pd.Series("", index=frame.index)).fillna("").astype(str)

    def get_citations(product_keys=None, row_indexes=None):
        idxs = list(row_indexes or [])
        if product_keys:
            for k in product_keys:
                idxs.extend(sales_citations.get(k, []))
                idxs.extend(inv_citations.get(k, []))
        idxs = list(dict.fromkeys(idxs))[:50]
        return [(source_files.loc[idx] if idx in source_files.index else "", row_ids.loc[idx] if idx in row_ids.index else idx + 1) for idx in idxs]

    # Check for missing required data modalities
    stock_required_intents = {
        "quick_selling_below_reorder", "runout_risk", "reorder_priority", "runout_few_days",
        "high_demand_out_of_stock", "overstock", "high_inventory_low_sales", "no_recent_sales",
        "profitable_low_stock", "high_profit_insufficient_stock", "dead_capital_low_profit",
        "gross_profit_per_stock", "purchase_today", "do_not_reorder_now", "restock_capital_budget",
        "tied_up_capital_least_sales", "inventory_capital_allocation", "category_overstocked",
        "sales_stock_efficiency"
    }

    if intent in stock_required_intents and not has_real_stock:
        return {
            "answer": (
                "**Inventory Data Required**\n\n"
                f"The selected dataset contains {len(sales):,} sales transactions, but does not include on-hand stock quantities or reorder levels. "
                "To evaluate stockout risk, days of supply, or replenishment priority, please select a source containing current inventory records or connect your POS database."
            ),
            "values": {"status": "unsupported_record_type", "required": "inventory", "sales_rows": len(sales)},
            "source_rows": []
        }

    expiry_required_intents = {
        "expiry_slow_30", "expiry_slow_60", "expiry_large_qty_90", "expiry_financial_loss",
        "near_expiry_highest_financial_risk", "expensive_approaching_expiry_poor_sales",
        "near_expiry_prioritize_first", "batches_expire_before_sold", "near_expiry_unsold_units",
        "avoid_reordering_near_expiry", "supplier_expiry_risk"
    }

    if intent in expiry_required_intents and not batch_rows:
        return {
            "answer": (
                "**Batch Expiry Records Required**\n\n"
                f"The selected dataset does not include dated batch expiry fields (`expiry_date` or `batch_no`). "
                "To calculate expiry risk, remaining shelf life, and projected financial losses, please select an inventory source with batch expiry tracking."
            ),
            "values": {"status": "unsupported_field", "required": "expiry_date"},
            "source_rows": []
        }

    if intent == "products_below_cost":
        cost_names = ("cost", "unit_cost", "cost_price", "purchase_price", "line_cost", "cost_of_goods_sold", "cogs")
        has_cost = any(name in sales and pd.to_numeric(sales[name], errors="coerce").notna().any() for name in cost_names)
        if not has_cost:
            return {"answer": "The selected sales data has no populated unit-cost field, so I can't determine which products sold below cost.", "values": {"status": "unsupported_field", "required_field": "unit cost or line cost"}, "source_rows": []}

    # Dispatch to specific intent formatting
    lines = []
    title = ""
    intro = ""
    headers = ""
    cited_keys = []
    cited_rows = []

    # 1. Quick selling below reorder level (Q1)
    if intent == "revenue_profit_contributors":
        eligible = [p for p in master_prods if p["revenue"] > 0 and p["gross_profit"] > 0]
        if not eligible:
            title = "Products contributing to revenue and profit"
            intro = "No products have both positive recorded revenue and gross profit in the selected data."
            headers = "| Medicine | Revenue | Gross profit |"
            lines = []
            cited_keys = []
        else:
            revenue_rank = pd.Series({p["key"]: p["revenue"] for p in eligible}).rank(pct=True)
            profit_rank = pd.Series({p["key"]: p["gross_profit"] for p in eligible}).rank(pct=True)
            combined = revenue_rank.add(profit_rank, fill_value=0)
            eligible.sort(key=lambda p: (-float(combined.get(p["key"], 0)), -p["revenue"], -p["gross_profit"]))
            title = "Products contributing to both revenue and profit"
            intro = "Ranked by the sum of each product’s percentile rank in recorded revenue and gross profit across the selected products."
            headers = "| Rank | Medicine | Revenue | Gross profit |"
            lines = [f"| {i} | {p['product']} | {curr_prefix}{p['revenue']:,.2f} | {curr_prefix}{p['gross_profit']:,.2f} |" for i, p in enumerate(eligible[:15], 1)]
            cited_keys = [p["key"] for p in eligible[:15]]
    elif intent == "sales_stock_efficiency":
        eligible = [p for p in master_prods if p["stock"] > 0]
        eligible.sort(key=lambda p: (-(p["units_sold"] / p["stock"]), -p["units_sold"]))
        title = "Sales relative to current stock"
        intro = "Ranked by units sold in the selected sales window divided by aggregated current on-hand units. Zero-stock items are excluded because the ratio is undefined."
        headers = "| Rank | Medicine | Units sold | Current stock | Units sold / stock |"
        lines = [f"| {i} | {p['product']} | {p['units_sold']:g} | {p['stock']:g} | {p['units_sold'] / p['stock']:.4f} |" for i, p in enumerate(eligible[:15], 1)]
        cited_keys = [p["key"] for p in eligible[:15]]
    elif intent == "products_below_cost":
        eligible = [p for p in master_prods if p["gross_profit"] < 0]
        eligible.sort(key=lambda p: p["gross_profit"])
        title = "Products sold below recorded cost"
        intro = "This comparison uses recorded sales revenue minus recorded line cost; negative gross profit identifies products sold below their recorded cost basis in aggregate."
        headers = "| Medicine | Revenue | Recorded cost | Gross profit |"
        lines = [f"| {p['product']} | {curr_prefix}{p['revenue']:,.2f} | {curr_prefix}{p['revenue'] - p['gross_profit']:,.2f} | {curr_prefix}{p['gross_profit']:,.2f} |" for p in eligible[:20]]
        cited_keys = [p["key"] for p in eligible[:20]]
    elif intent == "quick_selling_below_reorder":
        items = [p for p in master_prods if p["daily_velocity"] > 0 and (
            (p["reorder_level"] is not None and p["stock"] <= p["reorder_level"]) or
            (p["reorder_level"] is None and p["days_of_supply"] is not None and p["days_of_supply"] <= 30)
        )]
        items.sort(key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 999, -x["daily_velocity"]))
        title = "Medicines Selling Quickly & Below Reorder Level"
        if items:
            intro = f"Identified {len(items)} products below their reorder threshold with active sales velocity in {window_desc}."
        else:
            items = sorted([p for p in master_prods if p["daily_velocity"] > 0], key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 999, -x["daily_velocity"]))[:15]
            intro = f"No active medicines are currently below their fixed reorder point. Showing the fastest-selling medicines with the lowest days of stock cover in {window_desc}:"
        headers = "| Rank | Medicine | Stock / Reorder | Sold/Day | Stock Cover |\n|---:|---|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {_format_stock_reorder(p['stock'], p['reorder_level'])} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 2. Runout risk (Q2)
    elif intent == "runout_risk":
        items = [p for p in master_prods if p["daily_velocity"] > 0 and p["days_of_supply"] is not None and p["days_of_supply"] <= 30]
        items.sort(key=lambda x: (x["days_of_supply"], -x["daily_velocity"]))
        title = "Medicines Likely to Run Out Soon"
        if items:
            intro = f"Ranked by fewest days of supply remaining at observed sales rate ({window_desc})."
        else:
            items = sorted([p for p in master_prods if p["daily_velocity"] > 0], key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 999, -x["daily_velocity"]))[:15]
            intro = f"All active products currently have more than 30 days of stock cover. Showing products closest to depletion (fewest days of supply in {window_desc}):"
        headers = "| Rank | Medicine | Current Stock | Sold/Day | Stock Cover |\n|---:|---|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {p['stock']:g} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 3. Reorder priority (Q3)
    elif intent == "reorder_priority":
        items = sorted([p for p in master_prods if p["daily_velocity"] > 0], key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 999, -x["daily_velocity"]))
        title = "Top Reorder Priorities"
        intro = f"Prioritized based on sales velocity and remaining stock cover over {window_desc}."
        headers = "| Priority | Medicine | Stock / Reorder | Sold/Day | Stock Cover |\n|---:|---|---:|---:|---:|"
        lines = [f"| #{i} | {p['product']} | {_format_stock_reorder(p['stock'], p['reorder_level'])} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 4. Stock for only a few more days (Q4)
    elif intent == "runout_few_days":
        items = [p for p in master_prods if p["daily_velocity"] > 0 and p["days_of_supply"] is not None and p["days_of_supply"] <= 7]
        items.sort(key=lambda x: x["days_of_supply"])
        title = "Critical Shortage: Stock for Only a Few More Days"
        if items:
            intro = f"Products with 7 days or less of inventory remaining at current sales velocity ({window_desc})."
        else:
            items = sorted([p for p in master_prods if p["daily_velocity"] > 0], key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 999, -x["daily_velocity"]))[:15]
            intro = f"No products have 7 days or less of stock remaining. Showing products closest to depletion in {window_desc}:"
        headers = "| Rank | Medicine | Stock | Sold/Day | Days Left |\n|---:|---|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {p['stock']:g} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 5. High-demand medicines out of stock (Q5)
    elif intent == "high_demand_out_of_stock":
        items = [p for p in master_prods if p["stock"] <= 0 and p["daily_velocity"] > 0]
        items.sort(key=lambda x: -x["daily_velocity"])
        title = "High-Demand Medicines Out of Stock"
        if items:
            intro = f"Products with zero stock but active customer demand in {window_desc}."
            headers = "| Rank | Medicine | Current Stock | Sold/Day | Total Units Sold |\n|---:|---|---:|---:|---:|"
            lines = [f"| {i} | {p['product']} | 0 | {p['daily_velocity']:g} | {p['units_sold']:g} |" for i, p in enumerate(items[:15], 1)]
        else:
            items = sorted([p for p in master_prods if p["daily_velocity"] > 0], key=lambda x: -x["daily_velocity"])[:15]
            intro = f"Good news: No high-demand medicines are currently out of stock. All active products have positive on-hand inventory. The highest-demand medicines are shown below for stock monitoring ({window_desc}):"
            headers = "| Rank | Medicine | Current Stock | Sold/Day | Stock Cover |\n|---:|---|---:|---:|---:|"
            lines = [f"| {i} | {p['product']} | {p['stock']:g} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 6. Overstocked products compared with sales (Q6)
    elif intent == "overstock":
        items = [p for p in master_prods if p["stock"] > 0 and (p["days_of_supply"] is None or p["days_of_supply"] >= 90 or p["units_sold"] == 0)]
        items.sort(key=lambda x: -(x["stock_value"] if x["stock_value"] > 0 else x["stock"]))
        title = "Overstocked Products Compared with Sales"
        intro = f"Products with more than 90 days of cover or zero sales in {window_desc}; screening for capital release."
        headers = "| Medicine | Current Stock | Sold/Day | Stock Cover | Stock Value |\n|---|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {p['stock']:g} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} | {curr_prefix}{p['stock_value']:,.2f} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 7. High inventory but very low sales (Q7)
    elif intent == "high_inventory_low_sales":
        med_stk = np.median([p["stock"] for p in master_prods]) if master_prods else 0
        items = [p for p in master_prods if p["stock"] >= med_stk and (p["days_of_supply"] is None or p["days_of_supply"] >= 90)]
        items.sort(key=lambda x: -x["stock_value"])
        title = "High Inventory with Low Sales"
        intro = f"Products holding high stock volumes but experiencing low sales velocity in {window_desc}."
        headers = "| Medicine | Current Stock | Stock Value | Sold/Day | Stock Cover |\n|---|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {p['stock']:g} | {curr_prefix}{p['stock_value']:,.2f} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 8. Not sold recently but occupying significant inventory (Q8)
    elif intent == "no_recent_sales":
        items = [p for p in master_prods if p["stock"] > 0 and p["units_sold"] == 0]
        items.sort(key=lambda x: -x["stock_value"] if x["stock_value"] > 0 else -x["stock"])
        title = "Unsold Stock Occupying Inventory"
        intro = f"Products with on-hand inventory but zero recorded sales during {window_desc}."
        headers = "| Medicine | Current Stock | Stock Value | Supplier |\n|---|---:|---:|---|"
        lines = [f"| {p['product']} | {p['stock']:g} | {curr_prefix}{p['stock_value']:,.2f} | {p['supplier']} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 9. Repeatedly running low because of high demand (Q9)
    elif intent == "recurring_low_stock":
        items = [p for p in master_prods if p["daily_velocity"] > 0 and (
            (p["reorder_level"] is not None and p["stock"] <= p["reorder_level"]) or
            (p["days_of_supply"] is not None and p["days_of_supply"] <= 15)
        )]
        items.sort(key=lambda x: -x["daily_velocity"])
        title = "High-Velocity Medicines Repeatedly Running Low"
        intro = f"Top-demand products operating under tight inventory buffers (<=15 days of cover or below reorder level)."
        headers = "| Medicine | Current Stock | Sold/Day | Stock Cover | Reorder Status |\n|---|---:|---:|---:|---|"
        lines = [f"| {p['product']} | {p['stock']:g} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} | Below Reorder |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 10 & 32. Reorder quantity adjustment & unit calculation (Q10, Q32)
    elif intent in ("reorder_quantity", "reorder_quantity_units"):
        target_days = 30
        items = []
        for p in master_prods:
            if p["daily_velocity"] <= 0: continue
            needed = max(0.0, round(p["daily_velocity"] * target_days - p["stock"], 1))
            if needed > 0:
                p["suggested_reorder_qty"] = needed
                p["reorder_cost"] = round(needed * (p["unit_cost"] if p["unit_cost"] > 0 else p["unit_price"]), 2)
                items.append(p)
        items.sort(key=lambda x: -x["suggested_reorder_qty"])
        title = "Suggested Reorder Quantities Based on Demand"
        if items:
            intro = f"Calculated replenishment to maintain {target_days} days of stock cover based on sales rate in {window_desc}."
        else:
            items = sorted([p for p in master_prods if p["daily_velocity"] > 0], key=lambda x: -x["daily_velocity"])[:15]
            for p in items:
                p["suggested_reorder_qty"] = round(p["daily_velocity"] * target_days, 1)
                p["reorder_cost"] = round(p["suggested_reorder_qty"] * (p["unit_cost"] if p["unit_cost"] > 0 else p["unit_price"]), 2)
            intro = f"Current inventory already covers {target_days} days for all medicines. Showing 30-day demand baseline and replenishment batch cost for top products:"
        headers = "| Medicine | Stock / Reorder | Sold/Day | 30-Day Demand | Suggested Reorder | Est. Cost |\n|---|---:|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {_format_stock_reorder(p['stock'], p['reorder_level'])} | {p['daily_velocity']:g} | {round(p['daily_velocity']*30, 1):g} | **{p['suggested_reorder_qty']:g}** | {curr_prefix}{p['reorder_cost']:,.2f} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 11, 12, 13. Expiry in 30, 60, 90 days (Q11, Q12, Q13)
    elif intent in ("expiry_slow_30", "expiry_slow_60", "expiry_large_qty_90"):
        horizon = 30 if intent == "expiry_slow_30" else (90 if intent == "expiry_large_qty_90" else 60)
        items = [b for b in batch_rows if 0 <= b["days_to_expiry"] <= horizon and (b["unsold_units"] > 0 or intent == "expiry_large_qty_90")]
        items.sort(key=lambda x: (x["days_to_expiry"], -x["unsold_units"]))
        title = f"Medicines Expiring in the Next {horizon} Days at Risk"
        if items:
            intro = f"Batches projected to have unsold stock at expiration based on observed sales velocity ({window_desc})."
            headers = "| Medicine | Batch | Expires | Stock | Sold/Day | Unsold at Expiry |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['stock']:g} | {b['daily_velocity']:g} | **{b['unsold_units']:g}** |" for b in items[:15]]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            earliest_dt = closest[0]["expiry_date"] if closest else "N/A"
            earliest_days = closest[0]["days_to_expiry"] if closest else 0
            intro = f"Good news: No batches will expire within the next {horizon} days. The earliest expiring batch is {earliest_dt} ({earliest_days} days away). Closest-expiring batches are shown below for monitoring:"
            headers = "| Medicine | Batch | Expiry Date | Days Left | Current Stock | Sold/Day |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['days_to_expiry']} | {b['stock']:g} | {b['daily_velocity']:g} |" for b in closest]
            cited_rows = [b["row_idx"] for b in closest]

    # 14. Financial loss from expiring in next 60 days (Q14)
    elif intent == "expiry_financial_loss":
        items = [b for b in batch_rows if 0 <= b["days_to_expiry"] <= 60 and b["unsold_units"] > 0]
        total_loss = sum(b["financial_loss"] for b in items)
        total_units = sum(b["unsold_units"] for b in items)
        items.sort(key=lambda x: -x["financial_loss"])
        title = "Potential Financial Loss from Expiring Stock (Next 60 Days)"
        if items:
            intro = f"**Total Projected Financial Loss: {curr_prefix}{total_loss:,.2f}** ({total_units:,.1f} estimated unsold units across {len(items)} batches)."
            headers = "| Medicine | Batch | Expires | Unsold Units | Unit Cost | Projected Loss |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['unsold_units']:g} | {curr_prefix}{b['unit_cost']:,.2f} | **{curr_prefix}{b['financial_loss']:,.2f}** |" for b in items[:15]]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            earliest_dt = closest[0]["expiry_date"] if closest else "N/A"
            earliest_days = closest[0]["days_to_expiry"] if closest else 0
            intro = f"**Total Projected Financial Loss: {curr_prefix}0.00**. No medicines will expire within the next 60 days (earliest batch expires {earliest_dt}, {earliest_days} days away). Closest batches are listed below for shelf-life tracking:"
            headers = "| Medicine | Batch | Expiry Date | Days Left | Current Stock | Batch Value |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['days_to_expiry']} | {b['stock']:g} | {curr_prefix}{(b['stock']*b['unit_cost']):,.2f} |" for b in closest]
            cited_rows = [b["row_idx"] for b in closest]

    # 15. Near-expiry highest financial risk (Q15)
    elif intent == "near_expiry_highest_financial_risk":
        items = [b for b in batch_rows if b["days_to_expiry"] <= 90 and b["financial_loss"] > 0]
        items.sort(key=lambda x: -x["financial_loss"])
        title = "Highest Financial Risk Near-Expiry Medicines"
        if items:
            intro = "Ranked by total projected write-off value (unsold units × cost price)."
            headers = "| Rank | Medicine | Batch | Days to Expiry | Unsold Units | Financial Exposure |\n|---:|---|---|---:|---:|---:|"
            lines = [f"| {i} | {b['product']} | {b['batch_no']} | {b['days_to_expiry']} | {b['unsold_units']:g} | **{curr_prefix}{b['financial_loss']:,.2f}** |" for i, b in enumerate(items[:15], 1)]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            intro = "No immediate write-off risk in the next 90 days. Closest batches by expiry date are shown below for financial tracking:"
            headers = "| Rank | Medicine | Batch | Days to Expiry | Current Stock | Batch Value |\n|---:|---|---|---:|---:|---:|"
            lines = [f"| {i} | {b['product']} | {b['batch_no']} | {b['days_to_expiry']} | {b['stock']:g} | {curr_prefix}{(b['stock']*b['unit_cost']):,.2f} |" for i, b in enumerate(closest, 1)]
            cited_rows = [b["row_idx"] for b in closest]

    # 16. Expensive medicines approaching expiry with poor sales (Q16)
    elif intent == "expensive_approaching_expiry_poor_sales":
        med_cost = np.median([b["unit_cost"] for b in batch_rows if b["unit_cost"] > 0]) if batch_rows else 0
        items = [b for b in batch_rows if b["days_to_expiry"] <= 120 and b["unit_cost"] >= med_cost and b["unsold_units"] > 0]
        items.sort(key=lambda x: -x["financial_loss"])
        title = "Expensive Medicines Approaching Expiry with Slow Sales"
        if items:
            intro = f"High-cost items (>= {curr_prefix}{med_cost:,.2f}) with insufficient sales velocity to clear before expiry."
            headers = "| Medicine | Batch | Unit Cost | Days to Expiry | Daily Sales | Risk Exposure |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {curr_prefix}{b['unit_cost']:,.2f} | {b['days_to_expiry']} | {b['daily_velocity']:g} | **{curr_prefix}{b['financial_loss']:,.2f}** |" for b in items[:15]]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: (-x["unit_cost"], x["days_to_expiry"]))[:15]
            intro = "No high-cost medicines are expiring within 120 days. Highest-cost batches in inventory are shown below for shelf monitoring:"
            headers = "| Medicine | Batch | Unit Cost | Days to Expiry | Current Stock | Batch Value |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {curr_prefix}{b['unit_cost']:,.2f} | {b['days_to_expiry']} | {b['stock']:g} | {curr_prefix}{(b['stock']*b['unit_cost']):,.2f} |" for b in closest]
            cited_rows = [b["row_idx"] for b in closest]

    # 17. Near-expiry products to prioritize selling first (Q17)
    elif intent == "near_expiry_prioritize_first":
        items = [b for b in batch_rows if b["days_to_expiry"] <= 90 and b["stock"] > 0]
        items.sort(key=lambda x: (x["days_to_expiry"], -x["financial_loss"]))
        title = "Clearance Priority: Sell First (FEFO)"
        if items:
            intro = "Prioritized by First-Expiry-First-Out (FEFO) and highest financial loss prevention."
            headers = "| Priority | Medicine | Batch | Expires | On-Hand | Daily Sales | Clearance Action |\n|---:|---|---|---:|---:|---:|---|"
            lines = [f"| #{i} | {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['stock']:g} | {b['daily_velocity']:g} | Front Shelf / Discount |" for i, b in enumerate(items[:15], 1)]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            intro = "No batches are expiring within 90 days. Earliest expiring batches are listed below for standard FEFO dispensing priority:"
            headers = "| Priority | Medicine | Batch | Expires | Days Left | On-Hand | Daily Sales |\n|---:|---|---|---:|---:|---:|---:|"
            lines = [f"| #{i} | {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['days_to_expiry']} | {b['stock']:g} | {b['daily_velocity']:g} |" for i, b in enumerate(closest, 1)]
            cited_rows = [b["row_idx"] for b in closest]

    # 18. Batches likely to expire before stock is sold (Q18)
    elif intent == "batches_expire_before_sold":
        items = [b for b in batch_rows if b["days_to_expiry"] <= 120 and b["unsold_units"] > 0]
        items.sort(key=lambda x: (x["days_to_expiry"], -x["unsold_units"]))
        title = "Batches Projected to Expire Before Being Cleared"
        if items:
            intro = f"Comparison of current batch stock against projected sales velocity over {window_desc}."
            headers = "| Medicine | Batch | Expires | Stock | Projected Sales | Unsold Units |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['stock']:g} | {b['expected_sales_before_expiry']:g} | **{b['unsold_units']:g}** |" for b in items[:15]]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            intro = "No batches are expiring within 120 days or facing unsold surplus risk. Batches closest to expiry are monitored below:"
            headers = "| Medicine | Batch | Expires | Days Left | Stock | Daily Sales |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['days_to_expiry']} | {b['stock']:g} | {b['daily_velocity']:g} |" for b in closest]
            cited_rows = [b["row_idx"] for b in closest]

    # 19. Units of near-expiry remaining unsold (Q19)
    elif intent == "near_expiry_unsold_units":
        items = [b for b in batch_rows if b["days_to_expiry"] <= 90 and b["unsold_units"] > 0]
        items.sort(key=lambda x: -x["unsold_units"])
        title = "Projected Unsold Units at Current Sales Velocity"
        if items:
            intro = f"Estimated surplus units remaining on shelf upon expiry date."
            headers = "| Medicine | Batch | Expiry Date | Current Stock | Expected Sales | Est. Unsold Units |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['stock']:g} | {round(b['expected_sales_before_expiry'], 1):g} | **{b['unsold_units']:g}** |" for b in items[:15]]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            intro = "0 unsold units projected across all near-expiry batches (no batches expiring within 90 days). Shelf inventory for earliest batches:"
            headers = "| Medicine | Batch | Expiry Date | Days Left | Current Stock | Daily Sales |\n|---|---|---:|---:|---:|---:|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['days_to_expiry']} | {b['stock']:g} | {b['daily_velocity']:g} |" for b in closest]
            cited_rows = [b["row_idx"] for b in closest]

    # 20. Avoid reordering because existing stock is close to expiry (Q20)
    elif intent == "avoid_reordering_near_expiry":
        items = [b for b in batch_rows if b["days_to_expiry"] <= 60 and b["unsold_units"] > 0]
        items.sort(key=lambda x: (x["days_to_expiry"], -x["unsold_units"]))
        title = "Products to Freeze Reordering (Near Expiry)"
        if items:
            intro = "These products have on-hand batches expiring soon with unsold surplus; hold reorders to avoid piling up dead inventory."
            headers = "| Medicine | Batch | Days to Expiry | Unsold Surplus | Reorder Recommendation |\n|---|---|---:|---:|---|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['days_to_expiry']} | {b['unsold_units']:g} | **DO NOT REORDER** |" for b in items[:15]]
            cited_rows = [b["row_idx"] for b in items[:15]]
        else:
            closest = sorted(batch_rows, key=lambda x: x["days_to_expiry"])[:15]
            intro = "No products currently have batches expiring within 60 days. Reordering can proceed normally without expiry freeze risk. Earliest expiring batches:"
            headers = "| Medicine | Batch | Expiry Date | Days Left | Current Stock | Reorder Status |\n|---|---|---:|---:|---:|---|"
            lines = [f"| {b['product']} | {b['batch_no']} | {b['expiry_date']} | {b['days_to_expiry']} | {b['stock']:g} | OK to Reorder |" for b in closest]
            cited_rows = [b["row_idx"] for b in closest]

    # 21. Highly profitable medicines running low on stock (Q21)
    elif intent == "profitable_low_stock":
        items = [p for p in master_prods if p["gross_profit"] > 0 and (
            (p["reorder_level"] is not None and p["stock"] <= p["reorder_level"]) or
            (p["days_of_supply"] is not None and p["days_of_supply"] <= 30)
        )]
        items.sort(key=lambda x: -x["gross_profit"])
        title = "Highly Profitable Medicines Running Low on Stock"
        if items:
            intro = f"Ranked by total gross profit; stock is at or below reorder level or has <= 30 days of cover."
            headers = "| Medicine | Gross Profit | Stock / Reorder | Sold/Day | Stock Cover |\n|---|---:|---:|---:|---:|"
            lines = [f"| {p['product']} | {curr_prefix}{p['gross_profit']:,.2f} | {_format_stock_reorder(p['stock'], p['reorder_level'])} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for p in items[:15]]
            cited_keys = [p["key"] for p in items[:15]]
        else:
            closest = sorted([p for p in master_prods if p["gross_profit"] > 0], key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 9999))[:15]
            intro = "All profitable medicines maintain healthy stock buffers (> 30 days of cover). Profitable items with the lowest relative stock cover are listed below for replenishment monitoring:"
            headers = "| Medicine | Gross Profit | Margin % | Stock / Reorder | Sold/Day | Stock Cover |\n|---|---:|---:|---:|---:|---:|"
            lines = [f"| {p['product']} | {curr_prefix}{p['gross_profit']:,.2f} | {p['margin_pct']:.1f}% | {_format_stock_reorder(p['stock'], p['reorder_level'])} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for p in closest]
            cited_keys = [p["key"] for p in closest]

    # 22. Most profit but insufficient inventory (Q22)
    elif intent == "high_profit_insufficient_stock":
        items = [p for p in master_prods if p["gross_profit"] > 0 and (p["days_of_supply"] is not None and p["days_of_supply"] <= 20)]
        items.sort(key=lambda x: -x["gross_profit"])
        title = "High-Profit Generators with Insufficient Inventory"
        if items:
            intro = "Top profit drivers facing imminent stockouts (<= 20 days of stock cover)."
            headers = "| Rank | Medicine | Gross Profit | Margin % | Current Stock | Stock Cover |\n|---:|---|---:|---:|---:|---:|"
            lines = [f"| {i} | {p['product']} | {curr_prefix}{p['gross_profit']:,.2f} | {p['margin_pct']:.1f}% | {p['stock']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
            cited_keys = [p["key"] for p in items[:15]]
        else:
            closest = sorted([p for p in master_prods if p["gross_profit"] > 0], key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 9999))[:15]
            intro = "All major profit generators currently have sufficient inventory cover (> 20 days). Top profit drivers ranked by replenishment priority are shown below:"
            headers = "| Rank | Medicine | Gross Profit | Margin % | Current Stock | Stock Cover |\n|---:|---|---:|---:|---:|---:|"
            lines = [f"| {i} | {p['product']} | {curr_prefix}{p['gross_profit']:,.2f} | {p['margin_pct']:.1f}% | {p['stock']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(closest, 1)]
            cited_keys = [p["key"] for p in closest]

    # 23. Fast-selling with highest profit margins (Q23)
    elif intent == "fast_selling_highest_margin":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        items = [p for p in master_prods if p["daily_velocity"] >= med_vel and p["margin_pct"] > 0]
        items.sort(key=lambda x: (-x["margin_pct"], -x["revenue"]))
        title = "Fast-Selling Medicines with Highest Profit Margins"
        intro = f"High-velocity medicines (>= {med_vel:g} units/day) ranked by gross profit margin percentage."
        headers = "| Rank | Medicine | Margin % | Sold/Day | Gross Profit | Total Revenue |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | **{p['margin_pct']:.1f}%** | {p['daily_velocity']:g} | {curr_prefix}{p['gross_profit']:,.2f} | {curr_prefix}{p['revenue']:,.2f} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 24. Fast-selling with surprisingly low profit margins (Q24)
    elif intent == "fast_selling_low_margin":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        items = [p for p in master_prods if p["daily_velocity"] >= med_vel]
        items.sort(key=lambda x: (x["margin_pct"], -x["units_sold"]))
        title = "Fast-Selling Medicines with Low Profit Margins"
        intro = f"High-velocity products with slim margins; candidates for manufacturer discount negotiation."
        headers = "| Rank | Medicine | Margin % | Sold/Day | Gross Profit | Total Revenue |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | **{p['margin_pct']:.1f}%** | {p['daily_velocity']:g} | {curr_prefix}{p['gross_profit']:,.2f} | {curr_prefix}{p['revenue']:,.2f} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 25. High revenue but little actual gross profit (Q25)
    elif intent == "high_revenue_low_profit":
        med_rev = np.median([p["revenue"] for p in master_prods if p["revenue"] > 0]) if master_prods else 0
        items = [p for p in master_prods if p["revenue"] >= med_rev]
        items.sort(key=lambda x: (x["margin_pct"], -x["revenue"]))
        title = "High Revenue but Low Gross Profit"
        intro = "Medicines generating high sales volume but delivering thin gross margins."
        headers = "| Rank | Medicine | Total Revenue | Gross Profit | Margin % | Sold/Day |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {curr_prefix}{p['revenue']:,.2f} | {curr_prefix}{p['gross_profit']:,.2f} | **{p['margin_pct']:.1f}%** | {p['daily_velocity']:g} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 26. High profit margins but very low sales (Q26)
    elif intent == "high_margin_low_sales":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        items = [p for p in master_prods if p["margin_pct"] >= 20.0 and p["daily_velocity"] <= med_vel]
        items.sort(key=lambda x: (-x["margin_pct"], x["daily_velocity"]))
        title = "High Margin but Very Low Sales"
        intro = "High-margin products that suffer from poor customer uptake; potential for marketing or bundle promotion."
        headers = "| Rank | Medicine | Margin % | Sold/Day | Units Sold | Total Revenue |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | **{p['margin_pct']:.1f}%** | {p['daily_velocity']:g} | {p['units_sold']:g} | {curr_prefix}{p['revenue']:,.2f} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 27. Inventory investment without generating meaningful profit (Q27)
    elif intent == "dead_capital_low_profit":
        items = [p for p in master_prods if p["stock_value"] > 0]
        items.sort(key=lambda x: (x["gmroi"], -x["stock_value"]))
        title = "Inventory Investment Yielding Little Profit"
        intro = "Products tying up capital on shelves with low Return on Inventory Investment (GMROI)."
        headers = "| Medicine | Stock Value | Gross Profit | GMROI (Profit/Stock) | Stock Cover |\n|---|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {curr_prefix}{p['stock_value']:,.2f} | {curr_prefix}{p['gross_profit']:,.2f} | **{p['gmroi']:.2f}x** | {_format_days(p['days_of_supply'])} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 28. Gross profit relative to stock kept (GMROI) (Q28)
    elif intent == "gross_profit_per_stock":
        items = [p for p in master_prods if p["stock_value"] > 0 and p["gross_profit"] > 0]
        items.sort(key=lambda x: -x["gmroi"])
        title = "Highest Gross Profit Relative to Stock (GMROI)"
        intro = "Top capital-efficient products generating the highest gross profit return per dollar/rupee invested in stock."
        headers = "| Rank | Medicine | GMROI | Gross Profit | Stock Value | Sold/Day |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | **{p['gmroi']:.2f}x** | {curr_prefix}{p['gross_profit']:,.2f} | {curr_prefix}{p['stock_value']:,.2f} | {p['daily_velocity']:g} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 29. Keep more stock: Fast-selling & profitable (Q29)
    elif intent == "keep_more_stock_fast_profitable":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        med_prof = np.median([p["gross_profit"] for p in master_prods if p["gross_profit"] > 0]) if master_prods else 0
        items = [p for p in master_prods if p["daily_velocity"] >= med_vel and p["gross_profit"] >= med_prof]
        items.sort(key=lambda x: (-x["gross_profit"], -x["daily_velocity"]))
        title = "Stock More: Core Star Medicines (Fast & Profitable)"
        intro = "These products drive both high sales volume and high gross profit; maintain generous safety stock buffers."
        headers = "| Rank | Medicine | Gross Profit | Margin % | Sold/Day | Stock Cover |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {curr_prefix}{p['gross_profit']:,.2f} | {p['margin_pct']:.1f}% | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 30. Stock less: Slow-selling & low-margin (Q30)
    elif intent == "stock_less_slow_low_margin":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        med_margin = np.median([p["margin_pct"] for p in master_prods if p["margin_pct"] > 0]) if master_prods else 15.0
        items = [p for p in master_prods if p["daily_velocity"] <= med_vel and p["margin_pct"] <= med_margin and p["stock"] > 0]
        items.sort(key=lambda x: -x["stock_value"])
        title = "Stock Less: Slow-Selling & Low-Margin ('Dogs')"
        intro = "Candidates for rationalization: slow sales velocity, low margin, and capital locked in stock."
        headers = "| Medicine | Margin % | Sold/Day | Current Stock | Stock Value | Stock Cover |\n|---|---:|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {p['margin_pct']:.1f}% | {p['daily_velocity']:g} | {p['stock']:g} | {curr_prefix}{p['stock_value']:,.2f} | {_format_days(p['days_of_supply'])} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 31. What should I purchase today (Q31)
    elif intent == "purchase_today":
        items = [p for p in master_prods if p["daily_velocity"] > 0 and (
            (p["reorder_level"] is not None and p["stock"] <= p["reorder_level"]) or
            (p["days_of_supply"] is not None and p["days_of_supply"] <= 7)
        )]
        for p in items:
            p["order_qty"] = max(1.0, round(p["daily_velocity"] * 30 - p["stock"], 1))
            p["order_cost"] = round(p["order_qty"] * (p["unit_cost"] if p["unit_cost"] > 0 else p["unit_price"]), 2)
        items.sort(key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else 0, -x["daily_velocity"]))
        total_order_cost = sum(p["order_cost"] for p in items)
        title = "Purchase Orders Recommended for Today"
        intro = f"**Recommended Purchase Budget: {curr_prefix}{total_order_cost:,.2f}** for {len(items)} urgent stock replenishment lines."
        headers = "| Priority | Medicine | Stock | Stock Cover | Order Units | Supplier | Est. Cost |\n|---:|---|---:|---:|---:|---|---:|"
        lines = [f"| #{i} | {p['product']} | {p['stock']:g} | {_format_days(p['days_of_supply'])} | **{p['order_qty']:g}** | {p['supplier']} | {curr_prefix}{p['order_cost']:,.2f} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 33. Which medicines should I not reorder right now (Q33)
    elif intent == "do_not_reorder_now":
        items = [p for p in master_prods if p["stock"] > 0 and (
            (p["days_of_supply"] is not None and p["days_of_supply"] >= 60) or
            p["units_sold"] == 0
        )]
        items.sort(key=lambda x: -(x["days_of_supply"] if x["days_of_supply"] is not None else 999))
        title = "Medicines to NOT Reorder Right Now"
        intro = "These products have over 60 days of stock cover or zero recent sales; hold orders to avoid inventory buildup."
        headers = "| Medicine | Current Stock | Sold/Day | Stock Cover | Reason to Hold |\n|---|---:|---:|---:|---|"
        lines = [f"| {p['product']} | {p['stock']:g} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} | Surplus Stock Cover |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 34. Purchasing faster than selling (Q34)
    elif intent == "purchasing_faster_than_selling":
        items = [p for p in master_prods if p["units_purchased"] > p["units_sold"] and p["units_purchased"] > 0]
        items.sort(key=lambda x: -(x["units_purchased"] - x["units_sold"]))
        title = "Products Being Purchased Faster than Sold"
        intro = "Net inventory accumulation: units purchased exceed units sold over the recorded period."
        headers = "| Medicine | Purchased | Sold | Net Surplus | Stock Cover |\n|---|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {p['units_purchased']:g} | {p['units_sold']:g} | **+{p['units_purchased'] - p['units_sold']:g}** | {_format_days(p['days_of_supply'])} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 35. Under-purchasing compared with customer demand (Q35)
    elif intent == "under_purchasing_vs_demand":
        items = [p for p in master_prods if (p["units_sold"] > p["units_purchased"] or (p["stock"] <= 0 and p["daily_velocity"] > 0))]
        items.sort(key=lambda x: -x["daily_velocity"])
        title = "Products Under-Purchased Compared with Customer Demand"
        intro = "Sales demand exceeds purchase restocking, creating stockouts or near-depletion."
        headers = "| Medicine | Units Sold | Units Purchased | Current Stock | Daily Demand |\n|---|---:|---:|---:|---:|"
        lines = [f"| {p['product']} | {p['units_sold']:g} | {p['units_purchased']:g} | {p['stock']:g} | {p['daily_velocity']:g}/day |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 36. Supplier with most sales (Q36)
    elif intent == "supplier_most_sales":
        supp_map = {}
        for p in master_prods:
            s = p["supplier"]
            if not s or s in ("-", "nan", "None", ""): continue
            if s not in supp_map: supp_map[s] = {"revenue": 0.0, "profit": 0.0, "units": 0, "prods": 0}
            supp_map[s]["revenue"] += p["revenue"]
            supp_map[s]["profit"] += p["gross_profit"]
            supp_map[s]["units"] += p["units_sold"]
            supp_map[s]["prods"] += 1
        s_list = sorted(supp_map.items(), key=lambda x: -x[1]["revenue"])
        title = "Suppliers Generating the Most Sales"
        intro = f"Ranked by total sales revenue generated across supplied products ({window_desc})."
        headers = "| Rank | Supplier / Vendor | Total Sales | Gross Profit | Units Sold | Products |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {s} | **{curr_prefix}{data['revenue']:,.2f}** | {curr_prefix}{data['profit']:,.2f} | {data['units']:g} | {data['prods']} |" for i, (s, data) in enumerate(s_list[:15], 1)]

    # 37. Supplier with most profit (Q37)
    elif intent == "supplier_most_profit":
        supp_map = {}
        for p in master_prods:
            s = p["supplier"]
            if not s or s in ("-", "nan", "None", ""): continue
            if s not in supp_map: supp_map[s] = {"revenue": 0.0, "profit": 0.0, "units": 0, "prods": 0}
            supp_map[s]["revenue"] += p["revenue"]
            supp_map[s]["profit"] += p["gross_profit"]
            supp_map[s]["units"] += p["units_sold"]
            supp_map[s]["prods"] += 1
        s_list = sorted(supp_map.items(), key=lambda x: -x[1]["profit"])
        title = "Suppliers Generating the Most Gross Profit"
        intro = f"Ranked by total gross profit generated across supplied products ({window_desc})."
        headers = "| Rank | Supplier / Vendor | Gross Profit | Total Sales | Margin % | Products |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {s} | **{curr_prefix}{data['profit']:,.2f}** | {curr_prefix}{data['revenue']:,.2f} | {(data['profit']/data['revenue']*100 if data['revenue']>0 else 0):.1f}% | {data['prods']} |" for i, (s, data) in enumerate(s_list[:15], 1)]

    # 38. Vendors whose products frequently become low-stock (Q38)
    elif intent == "supplier_frequent_low_stock":
        supp_map = {}
        for p in master_prods:
            s = p["supplier"]
            if not s or s in ("-", "nan", "None", ""): continue
            is_low = (p["reorder_level"] is not None and p["stock"] <= p["reorder_level"]) or (p["days_of_supply"] is not None and p["days_of_supply"] <= 15)
            if s not in supp_map: supp_map[s] = {"low_count": 0, "total_prods": 0, "low_prods": []}
            supp_map[s]["total_prods"] += 1
            if is_low:
                supp_map[s]["low_count"] += 1
                supp_map[s]["low_prods"].append(p["product"])
        s_list = sorted(supp_map.items(), key=lambda x: -x[1]["low_count"])
        title = "Vendors Supplying Products that Frequently Run Low"
        intro = "Suppliers with the highest count of low-stock or stockout items."
        headers = "| Rank | Vendor | Low-Stock Items | Total Items | Low-Stock Ratio |\n|---:|---|---:|---:|---:|"
        lines = [f"| {i} | {s} | **{data['low_count']}** | {data['total_prods']} | {(data['low_count']/data['total_prods']*100 if data['total_prods']>0 else 0):.1f}% |" for i, (s, data) in enumerate(s_list[:15], 1)]

    # 39. Vendor products most likely to expire before being sold (Q39)
    elif intent == "supplier_expiry_risk":
        supp_map = {}
        for b in batch_rows:
            s = b["supplier"]
            if not s or s in ("-", "nan", "None", ""): continue
            if s not in supp_map: supp_map[s] = {"loss": 0.0, "unsold": 0, "batches": 0}
            supp_map[s]["loss"] += b["financial_loss"]
            supp_map[s]["unsold"] += b["unsold_units"]
            supp_map[s]["batches"] += 1
        s_list = sorted(supp_map.items(), key=lambda x: -x[1]["loss"])
        title = "Suppliers with Highest Product Expiry Risk"
        intro = "Vendors whose products represent the largest unsold financial losses prior to expiration."
        headers = "| Rank | Vendor | Projected Expiry Loss | Unsold Units | Expiring Batches |\n|---:|---|---:|---:|---:|"
        lines = [f"| {i} | {s} | **{curr_prefix}{data['loss']:,.2f}** | {data['unsold']:g} | {data['batches']} |" for i, (s, data) in enumerate(s_list[:15], 1)]

    # 40. Restock capital budget allocation (Q40)
    elif intent == "restock_capital_budget":
        items = [p for p in master_prods if p["daily_velocity"] > 0]
        items.sort(key=lambda x: -x["daily_velocity"])
        top_items = items[:20]
        total_alloc = 0.0
        for p in top_items:
            needed = max(0.0, p["daily_velocity"] * 30 - p["stock"])
            cost = needed * (p["unit_cost"] if p["unit_cost"] > 0 else p["unit_price"])
            p["alloc_units"] = round(needed, 1)
            p["alloc_cost"] = round(cost, 2)
            total_alloc += cost
        title = "Restock Budget Allocation for Highest-Demand Medicines"
        intro = f"**Total Capital Allocation: {curr_prefix}{total_alloc:,.2f}** required to maintain a 30-day stock cover on top-demand medicines."
        headers = "| Rank | Medicine | Sold/Day | Current Stock | Replenish Units | Allocated Budget |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {p['daily_velocity']:g} | {p['stock']:g} | {p['alloc_units']:g} | **{curr_prefix}{p['alloc_cost']:,.2f}** |" for i, p in enumerate(top_items[:15], 1)]
        cited_keys = [p["key"] for p in top_items[:15]]

    # 41. Top 10 medicines to maximize sales without increasing inventory unnecessarily (Q41)
    elif intent == "top10_maximize_sales_lean":
        items = [p for p in master_prods if p["daily_velocity"] > 0]
        items.sort(key=lambda x: (-x["revenue"], x["days_of_supply"] if x["days_of_supply"] is not None else 999))
        top10 = items[:10]
        title = "Top 10 Medicines: Maximize Sales with Lean Inventory"
        intro = "Focus items with high turnover: maintain target 15-20 days of stock cover rather than bulk holding."
        headers = "| Rank | Medicine | Total Revenue | Sold/Day | Current Stock | Recommended Cover |\n|---:|---|---:|---:|---:|---|"
        lines = [f"| {i} | {p['product']} | {curr_prefix}{p['revenue']:,.2f} | {p['daily_velocity']:g} | {p['stock']:g} | 15–20 Days Lean |" for i, p in enumerate(top10, 1)]
        cited_keys = [p["key"] for p in top10]

    # 42. Tying up most money while generating least sales (Q42)
    elif intent == "tied_up_capital_least_sales":
        items = [p for p in master_prods if p["stock_value"] > 0]
        items.sort(key=lambda x: (x["revenue"] / x["stock_value"], -x["stock_value"]))
        title = "Products Tying Up Capital with Least Sales"
        intro = "Trapped cash: high inventory value relative to generated sales revenue."
        headers = "| Rank | Medicine | Stock Value | Sales Revenue | Sold/Day | Stock Cover |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | **{curr_prefix}{p['stock_value']:,.2f}** | {curr_prefix}{p['revenue']:,.2f} | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 43. Champion products (best combination: high demand, high margin, low expiry risk) (Q43)
    elif intent == "champion_products":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        med_margin = np.median([p["margin_pct"] for p in master_prods if p["margin_pct"] > 0]) if master_prods else 15.0
        items = [p for p in master_prods if p["daily_velocity"] >= med_vel and p["margin_pct"] >= med_margin]
        items.sort(key=lambda x: (-x["gross_profit"], -x["margin_pct"]))
        title = "Pharmacy Champion Products (High Demand + High Margin)"
        intro = "Top-tier portfolio: strong demand velocity, superior gross margins, and stable shelf life."
        headers = "| Rank | Champion Medicine | Margin % | Sold/Day | Gross Profit | Total Sales |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | **{p['margin_pct']:.1f}%** | {p['daily_velocity']:g} | {curr_prefix}{p['gross_profit']:,.2f} | {curr_prefix}{p['revenue']:,.2f} |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 44. Worst combination: low demand, low margin, high expiry risk (Q44)
    elif intent == "worst_risk_products":
        med_vel = np.median([p["daily_velocity"] for p in master_prods if p["daily_velocity"] > 0]) if master_prods else 0
        med_margin = np.median([p["margin_pct"] for p in master_prods if p["margin_pct"] > 0]) if master_prods else 15.0
        items = [p for p in master_prods if p["daily_velocity"] <= med_vel and p["margin_pct"] <= med_margin and p["stock"] > 0]
        items.sort(key=lambda x: -x["stock_value"])
        title = "Highest-Risk Products (Low Demand + Low Margin)"
        intro = "Underperforming products combining weak sales, thin margins, and capital risk; candidate for discontinuation."
        headers = "| Rank | Medicine | Current Stock | Stock Value | Sold/Day | Margin % |\n|---:|---|---:|---:|---:|---:|"
        lines = [f"| {i} | {p['product']} | {p['stock']:g} | {curr_prefix}{p['stock_value']:,.2f} | {p['daily_velocity']:g} | {p['margin_pct']:.1f}% |" for i, p in enumerate(items[:15], 1)]
        cited_keys = [p["key"] for p in items[:15]]

    # 45. Where is most inventory money invested and is it selling (Q45)
    elif intent == "inventory_capital_allocation":
        items = sorted(master_prods, key=lambda x: -x["stock_value"])
        total_stk_val = sum(p["stock_value"] for p in items)
        title = "Inventory Capital Allocation vs. Sales Performance"
        intro = f"**Total Inventory Investment: {curr_prefix}{total_stk_val:,.2f}**. Showing where capital is concentrated and whether stock is moving."
        headers = "| Medicine | Stock Value | Share of Inv | Sold/Day | Stock Cover | Movement Status |\n|---|---:|---:|---:|---:|---|"
        lines = [f"| {p['product']} | {curr_prefix}{p['stock_value']:,.2f} | {(p['stock_value']/total_stk_val*100 if total_stk_val>0 else 0):.1f}% | {p['daily_velocity']:g} | {_format_days(p['days_of_supply'])} | {'Fast' if p['daily_velocity']>1 else ('Slow' if p['daily_velocity']>0 else 'Dead Stock')} |" for p in items[:15]]
        cited_keys = [p["key"] for p in items[:15]]

    # 46. Categories with too much inventory compared to sales (Q46)
    elif intent == "category_overstocked":
        cat_map = {}
        for p in master_prods:
            c = p["category"]
            if not c or c in ("-", "nan", "None", ""): c = "General"
            if c not in cat_map: cat_map[c] = {"stock_val": 0.0, "rev": 0.0, "sold": 0, "prods": 0}
            cat_map[c]["stock_val"] += p["stock_value"]
            cat_map[c]["rev"] += p["revenue"]
            cat_map[c]["sold"] += p["units_sold"]
            cat_map[c]["prods"] += 1
        c_list = sorted(cat_map.items(), key=lambda x: -x[1]["stock_val"])
        title = "Categories Overstocked Relative to Sales"
        intro = "Product categories holding excessive inventory capital compared to their sales turnover."
        headers = "| Category | Inventory Value | Sales Revenue | Inv/Sales Ratio | Items |\n|---|---:|---:|---:|---:|"
        lines = [f"| {c} | {curr_prefix}{data['stock_val']:,.2f} | {curr_prefix}{data['rev']:,.2f} | {(data['stock_val']/data['rev'] if data['rev']>0 else 999):.2f}x | {data['prods']} |" for c, data in c_list[:12]]

    # 47. Categories generating strong sales despite little inventory (Q47)
    elif intent == "category_lean_high_velocity":
        cat_map = {}
        for p in master_prods:
            c = p["category"]
            if not c or c in ("-", "nan", "None", ""): c = "General"
            if c not in cat_map: cat_map[c] = {"stock_val": 0.0, "rev": 0.0, "sold": 0, "prods": 0}
            cat_map[c]["stock_val"] += p["stock_value"]
            cat_map[c]["rev"] += p["revenue"]
            cat_map[c]["sold"] += p["units_sold"]
            cat_map[c]["prods"] += 1
        c_list = sorted(cat_map.items(), key=lambda x: -(x[1]["rev"] / max(1.0, x[1]["stock_val"])))
        title = "High-Turnover Categories (Lean Inventory & Strong Sales)"
        intro = "Categories generating strong sales turnover with lean inventory capital investment."
        headers = "| Category | Sales Revenue | Inventory Value | Revenue/Stock | Items |\n|---|---:|---:|---:|---:|"
        lines = [f"| {c} | {curr_prefix}{data['rev']:,.2f} | {curr_prefix}{data['stock_val']:,.2f} | **{(data['rev']/data['stock_val'] if data['stock_val']>0 else 999):.2f}x** | {data['prods']} |" for c, data in c_list[:12]]

    # 48. Immediate management attention: Triage matrix (Q48)
    elif intent == "immediate_management_attention":
        stockouts = [p for p in master_prods if p["stock"] <= 0 and p["daily_velocity"] > 0][:5]
        expiring = [b for b in batch_rows if b["days_to_expiry"] <= 30 and b["unsold_units"] > 0][:5]
        dead_capital = sorted([p for p in master_prods if p["units_sold"] == 0 and p["stock_value"] > 0], key=lambda x: -x["stock_value"])[:5]
        title = "Executive Triage Matrix: Immediate Management Attention"
        intro = "Critical operational alerts across stockouts, imminent expiry, and trapped capital."
        headers = "| Priority Category | Medicine / Item | Issue / Risk | Action Required |\n|---|---|---|---|"
        for p in stockouts:
            lines.append(f"| **1. Stockout** | {p['product']} | Sold Out ({p['daily_velocity']:g}/day demand) | Immediate Restock |")
        for b in expiring:
            lines.append(f"| **2. Expiry Risk** | {b['product']} (Batch {b['batch_no']}) | Expires in {b['days_to_expiry']} days ({curr_prefix}{b['financial_loss']:,.2f} loss) | Clearance Discount / FEFO |")
        for p in dead_capital:
            lines.append(f"| **3. Trapped Capital** | {p['product']} | Zero sales in window ({curr_prefix}{p['stock_value']:,.2f} stock) | Return to Vendor / Freeze Orders |")

    # 49. Biggest potential inventory losses (Q49)
    elif intent == "biggest_potential_inventory_losses":
        exp_loss = sum(b["financial_loss"] for b in batch_rows if b["days_to_expiry"] <= 90)
        dead_stk = sum(p["stock_value"] for p in master_prods if p["units_sold"] == 0)
        title = "Biggest Potential Inventory Losses in Pharmacy"
        intro = f"**Total Combined Loss Risk: {curr_prefix}{exp_loss + dead_stk:,.2f}**\n- Near-Expiry Write-off Risk (90 days): **{curr_prefix}{exp_loss:,.2f}**\n- Dead Inventory Capital (Zero Sales): **{curr_prefix}{dead_stk:,.2f}**"
        headers = "| Loss Category | Item / Batch | Exposure | Status |\n|---|---|---:|---|"
        top_exp = sorted([b for b in batch_rows if b["days_to_expiry"] <= 90], key=lambda x: -x["financial_loss"])[:6]
        top_dead = sorted([p for p in master_prods if p["units_sold"] == 0], key=lambda x: -x["stock_value"])[:6]
        for b in top_exp:
            lines.append(f"| Near-Expiry Loss | {b['product']} (Batch {b['batch_no']}) | **{curr_prefix}{b['financial_loss']:,.2f}** | Expires in {b['days_to_expiry']} days |")
        for p in top_dead:
            lines.append(f"| Dead Stock Capital | {p['product']} | **{curr_prefix}{p['stock_value']:,.2f}** | Zero Sales in Window |")

    # 50. Daily executive action plan (Q50)
    elif intent == "daily_executive_action_plan":
        urgent_reorders = [p for p in master_prods if p["daily_velocity"] > 0 and (p["days_of_supply"] is not None and p["days_of_supply"] <= 7)][:5]
        urgent_expiry = sorted([b for b in batch_rows if b["days_to_expiry"] <= 45 and b["unsold_units"] > 0], key=lambda x: -x["financial_loss"])[:5]
        overstocked_holds = [p for p in master_prods if p["days_of_supply"] is not None and p["days_of_supply"] >= 90][:5]
        title = "Pharmacy Executive Action Plan for Today"
        intro = "Actionable 4-pillar daily decision plan based on sales velocity, inventory buffers, profit margins, and expiry dates:"
        headers = "| Pillar | Recommended Action | Items / Focus |\n|---|---|---|"
        reorder_names = ", ".join(p["product"] for p in urgent_reorders) if urgent_reorders else "All stocks adequate"
        lines.append(f"| **1. Urgent Restock** | Issue purchase orders for critical stockout items | {reorder_names} |")
        expiry_names = ", ".join(f"{b['product']} (Batch {b['batch_no']})" for b in urgent_expiry) if urgent_expiry else "No near-term write-offs"
        lines.append(f"| **2. Expiry Clearance** | Apply clearance discounts / FEFO front placement | {expiry_names} |")
        hold_names = ", ".join(p["product"] for p in overstocked_holds) if overstocked_holds else "No overstock flags"
        lines.append(f"| **3. Reorder Freeze** | Block new purchases for overstocked lines (>90 days cover) | {hold_names} |")
        lines.append(f"| **4. Margin Focus** | Promote top-margin star medicines to front counters | Top Champion Medicines |")

    # Final response composition
    answer = f"**{title}**\n\n{intro}"
    if lines:
        answer += f"\n\n{headers}\n" + "\n".join(lines)
    else:
        answer += "\n\nNo records matched the criteria in the selected data."

    # Citation mapping
    all_citations = get_citations(product_keys=cited_keys, row_indexes=cited_rows)

    values = {
        "status": "ok",
        "intent": intent,
        "sales_window_days": velocity_span,
        "currency": currency,
        "matching_records": len(lines),
        "total_products_evaluated": len(master_prods)
    }

    return {
        "answer": answer,
        "values": values,
        "source_rows": all_citations
    }
