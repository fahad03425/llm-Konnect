"""Schema-grounded offline intent planning for tabular questions.

This module translates natural language into a small, explicit calculation plan
using the selected DataFrame's actual columns and values. It intentionally
prefers a clarification/unsupported result to executing a guessed calculation.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd


_ROLE_ALIASES = {
    "supplier": ("supplier", "vendor", "distributor", "provider"),
    "product": ("product", "medicine", "drug", "item", "sku"),
    "category": ("category", "class", "group", "therapeuticclass"),
    "branch": ("branch", "store", "location", "warehouse", "site"),
    "status": ("status", "paymentstatus", "paymentstate"),
    "transaction": ("purchaseorderno", "purchaseid", "transactionid", "invoiceid", "orderno", "receiptid", "billno"),
    "date": ("date", "purchasedate", "invoicedate", "transactiondate", "orderdate", "createdat"),
    "quantity": ("quantity", "qty", "units", "unitssold", "qtysold", "qtyordered", "receivedqty"),
    "unit_cost": ("cost", "unitcost", "costperunit", "unitprice", "purchaseprice", "priceperunit"),
    "amount": ("invoicetotal", "totalamount", "amount", "netpayable", "purchasevalue", "lineamount", "spend", "expenditure", "revenue", "salesvalue"),
}

_WORDS = {
    "supplier": r"\b(suppliers?|vendors?|distributors?|providers?)\b",
    "product": r"\b(products?|medicines?|drugs?|items?|skus?)\b",
    "category": r"\b(categories|category|classes|class|groups|group)\b",
    "branch": r"\b(branches|branch|stores?|locations?|warehouses?|sites?)\b",
    "transaction": r"\b(transactions?|invoices?|bills?|orders?|purchase ids?|purchase orders?|receipts?)\b",
    "date": r"\b(dates?|days?|months?|years?|time|over time|histor(?:y|ical)|trend)\b",
}


def _norm(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    return normalized.removeprefix("extra")


def _resolve_roles(frame: pd.DataFrame) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for col in frame.columns:
        normalized = _norm(col)
        for role, aliases in _ROLE_ALIASES.items():
            if any(normalized == alias or normalized.endswith(alias) or normalized.startswith(alias) for alias in aliases):
                if frame[col].notna().any():
                    roles.setdefault(role, []).append(col)
    # Prefer canonical fields to metadata copies and generic source columns.
    for role, cols in roles.items():
        cols.sort(key=lambda c: (str(c).startswith("_extra."), len(str(c))))
    return roles


def _requested_roles(q: str) -> list[str]:
    present = [(role, re.search(pattern, q)) for role, pattern in _WORDS.items()]
    found = [(role, match.start()) for role, match in present if match]
    return [role for role, _ in sorted(found, key=lambda x: x[1])]


def _role_from_word(word: str) -> str | None:
    """Map a natural-language entity mention to its canonical schema role."""
    w=word.casefold()
    if w.startswith(("supplier","vendor","distributor","provider")): return "supplier"
    if w.startswith(("product","medicine","drug","item","sku")): return "product"
    if w.startswith(("categor","class","group")): return "category"
    if w.startswith(("branch","store","location","warehouse","site")): return "branch"
    if w.startswith(("transaction","invoice","bill","order","receipt","purchase")): return "transaction"
    return None


def _number_limit(q: str, default: int = 10) -> int:
    m = re.search(r"\b(?:top|first|bottom|last)\s+(\d+)\b", q)
    if m:
        return max(1, min(100, int(m.group(1))))
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
    m = re.search(r"\btop\s+(one|two|three|four|five|ten)\b", q)
    return words[m.group(1)] if m else default


def _aggregation(q: str, roles: dict[str, list[str]]) -> tuple[str, str | None, str] | None:
    # The most explicit measure phrase wins; entity counts are distinct from
    # row counts and monetary sums are distinct from unit price/quantity.
    if re.search(r"\b(average|avg|mean)\b", q):
        if re.search(r"\baverage order size\b",q) and not re.search(r"\b(quantity|units?|amount|value|spend|money|cost)\b",q):
            return None
        if re.search(r"\b(unit\s+)?(cost|price)\b", q):
            return ("mean", "unit_cost", "average unit cost")
        if re.search(r"\b(invoice|purchase|order|transaction)\b", q) and re.search(r"\b(value|amount|total|size)\b", q):
            return ("mean", "amount", "average invoice amount")
        if re.search(r"\b(quantity|qty|units?)\b", q):
            return ("mean", "quantity", "average quantity")
        return None
    if re.search(r"\b(paid|unpaid|outstanding|settled)\b",q) and re.search(r"\b(invoices?|transactions?|purchase orders?|bills?|receipts?)\b",q):
        if re.search(r"\b(percent(?:age)?|share|proportion)\b",q): return ("share","transaction","share of transactions")
        if not re.search(r"\b(total|value|amount|spend|spent|expenditure)\b",q) and re.search(r"\b(how many|number of|count|most|fewest|highest|lowest|which|what|currently have)\b",q): return ("nunique","transaction","invoice count")
    if re.search(r"\b(overly dependent|dependence|dependency|concentrat(?:ed|ion))\b",q):
        return ("share","amount","supplier concentration")
    # Modal supplier is a frequency question, distinct from supplier variety.
    if re.search(r"\b(mainly|most frequently|most often)\b", q) and re.search(r"\bsuppliers?\b", q) and re.search(r"\b(products?|categor(?:y|ies)|branch(?:es)?)\b", q):
        return ("mode", "supplier", "most frequent supplier")
    if re.search(r"\b(variation|varies|vary|fluctuat|volatility)\b", q):
        target = "unit_cost" if re.search(r"\b(cost|price)\b", q) else "amount" if re.search(r"\b(amount|spend|expenditure|value)\b", q) else None
        return ("variation", target, "price variation") if target else None
    if re.search(r"\b(median|middle)\b", q):
        target = "quantity" if re.search(r"\b(quantity|qty|units?)\b", q) else "amount" if re.search(r"\b(amount|spend|value|cost|price)\b", q) else None
        return ("median", target, "median") if target else None
    if re.search(r"\b(distinct|unique|different|variety|range of|multiple suppliers?|multiple branches?|more than one supplier|only one supplier|single supplier)\b", q) and not re.search(r"\b(unit\s+cost|unit\s+price|average\s+cost|cheapest|lowest\s+average|paying more|price comparison)\b",q) and not (re.search(r"\b(paying|pay|costs?|prices?)\b",q) and re.search(r"\bsame product\b",q)):
        if re.search(r"\bproduct categor(?:y|ies)\b",q) and "category" in roles:
            return ("nunique","category","distinct category count")
        entity = "category" if re.search(r"\bproduct categor(?:y|ies)\b",q) else next((role for role in ("category", "product", "supplier", "branch", "transaction") if re.search(_WORDS[role], q)), None)
        if entity:
            return ("nunique", entity, f"distinct {entity} count")
    if re.search(r"\b(how many|number of|count|frequency|frequently|most often|fewest|least often)\b", q):
        if re.search(r"\b(how many|number of|count)\b", q) and not re.search(r"\b(each|per|by|grouped)\b", q) and not re.search(r"\b(invoice|transaction|purchase order|receipt|bill|order)s?\b",q):
            entity = "category" if re.search(r"\bproduct categor(?:y|ies)\b",q) else next((role for role in ("category", "product", "supplier", "branch", "transaction") if re.search(_WORDS[role], q)), None)
            if entity and re.search(r"\b(different|distinct|unique|many|number of|how many)\b", q):
                return ("nunique", entity, f"distinct {entity} count")
        if re.search(r"\b(invoice|transaction|purchase order|receipt|bill|order)s?\b", q):
            return ("nunique", "transaction", "transaction count")
        if re.search(r"\b(times|instances|records|rows|orders)\b", q):
            return ("count", None, "row count")
        if re.search(r"\b(products?|items?|medicines?|suppliers?|vendors?|categories|branches)\b", q) and re.search(r"\b(each|per|by|most|fewest|highest|lowest|top|bottom)\b", q):
            return ("count", None, "record frequency")
        if re.search(r"\b(different|distinct|unique)\b", q):
            return ("nunique", None, "distinct count")
        return ("count", None, "row count")
    if re.search(r"\b(most|fewest|highest|lowest|largest|smallest)\b",q) and re.search(r"\b(invoices?|transactions?|purchase orders?|bills?|receipts?)\b",q) and re.search(r"\b(suppliers?|vendors?|products?|categories|branches)\b",q):
        return ("nunique","transaction","transaction frequency")
    if re.search(r"\b(quantity|qty|units?)\b", q) and not re.search(r"\bunit\s+(?:costs?|prices?)\b",q):
        if re.search(r"\bwhich\s+(?:purchase|invoice|transaction|order)\b.{0,60}\b(had|with)\b",q) and re.search(r"\b(highest|largest|greatest|maximum|lowest|smallest|least|minimum)\b",q):
            return ("max" if re.search(r"\b(highest|largest|greatest|maximum)\b",q) else "min","quantity","purchase quantity")
        if re.search(r"\b(average|mean|avg)\b", q): return ("mean", "quantity", "average quantity")
        return ("sum", "quantity", "total quantity")
    if re.search(r"\bsame products?\b",q) and re.search(r"\b(branch|branches)\b",q) and re.search(r"\b(pay|paying|cost|price)\b",q):
        return ("mean","unit_cost","branch unit cost comparison")
    if re.search(r"\b(unit\s+costs?|unit\s+prices?|cost\s+per\s+unit|purchase\s+prices?|prices?)\b", q):
        if re.search(r"\b(average|mean|avg)\b", q): return ("mean", "unit_cost", "average unit cost")
        if re.search(r"\b(highest|largest|greatest|maximum|max|most expensive|lowest|smallest|least|minimum|min|top|bottom)\b",q): return ("max" if re.search(r"\b(highest|largest|greatest|maximum|max|most expensive|top)\b",q) else "min","unit_cost","unit cost")
        return ("mean", "unit_cost", "unit price")
    if re.search(r"\b(total|overall|combined)\b", q) and re.search(r"\b(purchase cost|purchasing cost|procurement cost|spending|spend\w*|spent|expenditure|budget)\b",q) and not re.search(r"\b(unit|per unit|percent(?:age)?|share|proportion)\b",q):
        return ("sum", "amount", "total purchase expenditure")
    if re.search(r"\b(highest|largest|greatest|maximum|max|lowest|smallest|least|minimum|min)\b", q) and re.search(r"\b(invoice|purchase order|purchase invoice)\b", q):
        return ("max" if re.search(r"\b(highest|largest|greatest|maximum|max)\b", q) else "min", "amount", "invoice amount")
    if re.search(r"\b(highest|largest|greatest|maximum|max|lowest|smallest|least|minimum|min|most expensive)\b", q) and re.search(r"\b(unit cost|unit price|cost|price)\b", q):
        return ("max" if re.search(r"\b(highest|largest|greatest|maximum|max|most expensive)\b", q) else "min", "unit_cost", "unit cost")
    if re.search(r"\b(overly dependent|dependence|dependency|concentrat(?:ed|ion))\b", q):
        return ("share", "amount", "supplier concentration")
    if re.search(r"\b(category|categories)\b",q) and re.search(r"\b(most|highest|largest|fewest|lowest)\b",q) and re.search(r"\b(products?|items?|medicines?)\b",q):
        return ("nunique","product","distinct products by category")
    if re.search(r"\b(percent(?:age)?|share|proportion)\b", q):
        if re.search(r"\b(paid|unpaid|settled|outstanding)\b", q) and re.search(r"\b(invoice|transaction|order|record)\b", q):
            return ("share", "transaction", "share of transactions")
        if re.search(r"\b(invoice|purchase|purchasing|spend\w*|spent|expenditure|amount|value|procurement)\b", q): return ("share", "amount", "share of amount")
        if re.search(r"\b(invoice|transaction|order|records?)\b", q): return ("share", "transaction", "share of records")
        return None
    if re.search(r"\b(amount|money|value|spend\w*|spent|expenditure|purchase value|procurement|invoice value|invoice total|revenue|sales value|cost|budget|purchasing)\b", q):
        return ("sum", "amount", "total amount")
    if re.search(r"\b(highest|largest|greatest|maximum|max|most expensive|lowest|smallest|least|minimum|min|top|bottom|rank|ranking)\b", q):
        # Ranking without a stated measure must not silently default to value.
        return None
    return None


def _filter_mask(frame: pd.DataFrame, q: str, roles: dict[str, list[str]]) -> tuple[pd.Series, dict[str, Any], str | None]:
    mask = pd.Series(True, index=frame.index)
    desc: dict[str, Any] = {}
    # Match explicit categorical values from the selected scope. A value only
    # becomes a filter if the question includes it verbatim and uniquely.
    for col in frame.columns:
        s = frame[col]
        if s.nunique(dropna=True) > 100 or pd.api.types.is_numeric_dtype(s):
            continue
        vals = [str(v) for v in s.dropna().unique() if len(str(v).strip()) >= 3]
        matches = [v for v in vals if re.search(rf"(?<![a-z0-9]){re.escape(v.casefold())}(?![a-z0-9])", q)]
        if len(matches) == 1:
            mask &= s.astype(str).str.casefold().eq(matches[0].casefold())
            desc[str(col)] = matches[0]
        elif len(matches) > 1:
            # Multi-value comparisons are left for explicit paired-group plans.
            desc[str(col)] = matches
    # State terms map to values actually present in the status column.
    status_col = (roles.get("status") or [None])[0]
    if status_col:
        values = [str(v) for v in frame[status_col].dropna().unique()]
        low = {v.casefold(): v for v in values}
        if re.search(r"\b(unpaid|outstanding|not paid|overdue)\b", q):
            paid = low.get("paid")
            if paid:
                mask &= frame[status_col].astype(str).str.casefold().ne(paid.casefold())
                desc[status_col] = "not " + paid
            else:
                return mask, desc, "The data does not define an unambiguous paid status."
        elif re.search(r"\b(paid|settled)\b", q):
            paid = next((v for v in values if re.fullmatch(r"paid|settled|complete(?:d)?", v, re.I)), None)
            if paid:
                mask &= frame[status_col].astype(str).str.casefold().eq(paid.casefold())
                desc[status_col] = paid
    return mask, desc, None


def _scalar(value: Any) -> Any:
    if pd.isna(value): return None
    if hasattr(value, "item"): return value.item()
    return value


def plan_and_answer_offline(question: str, frame: pd.DataFrame) -> dict[str, Any] | None:
    """Plan and execute an unambiguous tabular intent, entirely offline.

    Returns None only when the question is outside tabular analytics. For
    analytical language that cannot be mapped safely, returns a clarification
    response so a lower-confidence legacy matcher cannot emit a wrong answer.
    """
    if frame is None or frame.empty:
        return None
    from app.language.roman_urdu import normalize_roman_urdu_intent
    q = re.sub(r"\s+", " ", normalize_roman_urdu_intent(str(question or "")).casefold()).strip()
    if not q:
        return None
    if re.search(r"\b(?:invoice|receipt|bill|transaction|purchase|order|sale|sales|sku|batch|lot)\s*(?:number|no\.?|#|[-_/])?\s*[a-z0-9/-]*\d[a-z0-9/-]*\b", q) and re.search(r"\b(show|find|lookup|details?|what (?:was|is) (?:in|on)|which products?)\b", q):
        return None
    roles = _resolve_roles(frame)
    requested = _requested_roles(q)
    aggregation = _aggregation(q, roles)
    explicit_relationship=bool(re.search(r"\b(multiple suppliers?|multiple branches?|only one supplier|single supplier|widest range|wide variety|most products?|most branches?)\b",q))
    relationship = bool(re.search(r"\b(which|what|list|show|for each)\b",q) and re.search(r"\b(provide|suppl(?:y|ies|ied)|purchas(?:e|ed|es|ing) from|buy(?:ing)? from|bought from|multiple suppliers?|multiple branches?|only one supplier|single supplier|widest range)\b",q) and len(requested)>=2 and (explicit_relationship or not re.search(r"\b(quantity|qty|units?|unit cost|unit price|amount|money|spend|spent|expenditure|cost|price|average|mean|percentage|percent|share)\b",q)))
    has_data_intent = bool(re.search(r"\b(total|sum|average|avg|mean|median|how many|count|number of|percent|percentage|share|highest|largest|greatest|most|lowest|smallest|least|fewest|widest|multiple|single|only one|top|bottom|rank|frequent|per|each|by|over time|trend|become|increased|decreased|increase|decrease|paid|unpaid|earliest|latest|recent|first|oldest|newest|dependent|dependence|dependency|concentration|paying|more|same|unusually|high|driving)\b", q))
    if not has_data_intent:
        return None
    # An offline parser must not turn a compound decision request into the
    # first scalar it recognizes. Ask one focused question when the requested
    # clauses require an unstated ranking rule or business threshold.
    if re.search(r"\b(unusually|large outstanding|biggest impact|cost[- ]saving|reduce procurement costs|mainly buying|driving that spending)\b",q):
        return _decline("clarification","This request combines a ranking with a business threshold or a second attribution. Please specify the threshold and whether to rank by amount, quantity, or purchase frequency.",{"planner":"offline_schema_intent","requested_roles":requested,"reason":"unstated_threshold_or_multi_measure"})
    if (re.search(r"\band\s+(?:what|which)\s+",q) and re.search(r"\b(supplier|product|branch|category)",q)
            and not (re.search(r"\bsame products?\b",q) and re.search(r"\b(unit\s+cost|unit\s+price|paying|cheaper)\b",q))):
        return _decline("clarification","This asks for more than one result. Which measure should rank the related products or entities: spend, quantity, or purchase frequency?",{"planner":"offline_schema_intent","requested_roles":requested,"reason":"compound_request_needs_measure"})
    if re.search(r"\bfrequently purchased\b",q) and re.search(r"\b(best price|cheapest|lowest cost)\b",q):
        return _decline("clarification","What purchase-frequency threshold should define a frequently purchased product?",{"planner":"offline_schema_intent","requested_roles":requested,"reason":"undefined_frequency_threshold"})
    # Phrases used only to identify the entity are not metric measures.
    if re.search(r"\b(earliest|latest|most recent|oldest|newest)\b", q) and "date" in roles and not (re.search(r"\b(cost|price|expensive|cheaper)\b",q) and re.search(r"\b(change|increas|decreas|between)\b",q)):
        date_col = roles["date"][0]
        dates = pd.to_datetime(frame[date_col], errors="coerce")
        valid = dates.dropna()
        if valid.empty: return _decline("unsupported", "The selected data has no valid dates to identify the requested record.", {"planner":"offline_schema_intent","date_field":date_col})
        target = valid.max() if re.search(r"\b(latest|most recent|newest)\b", q) else valid.min()
        hit = dates.eq(target)
        return _result(frame.loc[hit], {"op":"date_extreme","field":date_col,"direction":"max" if hit.any() and target==valid.max() else "min"}, f"{target:%Y-%m-%d}", f"The {'latest' if target==valid.max() else 'earliest'} recorded date is {target:%Y-%m-%d}.", "date", hit)
    # Time comparisons aggregate observations by month before comparing entity
    # values; this prevents a single row on an endpoint date from defining the
    # trend. Amount trends use summed spend, while unit-price trends use means.
    if (re.search(r"\b(over time|become more expensive|increased|increase|risen|rise|price change)\b", q)
            and re.search(r"\b(cost\w*|price\w*|expensive)\b", q)
            and not re.search(r"\b(historically cheaper|lowest average|offers? the lowest|same products? from)\b", q)):
        unit_measure=bool(re.search(r"\b(unit\s+cost|unit\s+price|unit prices?|per[- ]unit|more expensive)\b",q))
        value_role="unit_cost" if unit_measure else "amount"
        if not roles.get("date") or not roles.get(value_role):
            return _decline("unsupported", f"The selected data needs a date and {'unit-price' if unit_measure else 'amount'} field to compare the requested trend.", {"planner":"offline_schema_intent","roles":roles,"measure_role":value_role})
        entity = next((r for r in ("product","supplier","category","branch") if r in requested), None)
        group_col = (roles.get(entity) or [None])[0] if entity else None
        if not group_col:
            return _decline("clarification", "Which entity should I compare over time (product, supplier, category, or branch)?", {"planner":"offline_schema_intent","roles":roles})
        date_col, value_col = roles["date"][0], roles[value_role][0]
        work=frame[[group_col,date_col,value_col]].copy(); work[date_col]=pd.to_datetime(work[date_col],errors="coerce"); work[value_col]=pd.to_numeric(work[value_col],errors="coerce"); work=work.dropna()
        if work.empty: return _decline("unsupported",f"There are no valid dated {'prices' if unit_measure else 'amounts'} in the selected data.",{"planner":"offline_schema_intent"})
        work["_period"]=work[date_col].dt.to_period("M")
        aggregate="mean" if unit_measure else "sum"
        monthly=work.groupby([group_col,"_period"],dropna=True)[value_col].agg(aggregate).reset_index().sort_values([group_col,"_period"])
        rows=[]
        for key, group in monthly.groupby(group_col,dropna=True):
            first,last=group.iloc[0],group.iloc[-1]
            rows.append({entity:key,"first_period":str(first["_period"]),"first_value":float(first[value_col]),"last_period":str(last["_period"]),"last_value":float(last[value_col]),"change":float(last[value_col]-first[value_col])})
        out=pd.DataFrame(rows).sort_values("change",ascending=False)
        if re.search(r"\b(become more expensive|largest increase|largest increase|increased the most|largest increase)\b",q):out=out[out["change"].gt(0)]
        if "most" in q or "largest" in q or "highest" in q or "increased" in q or "increase" in q: out=out.head(_number_limit(q,5))
        detail_rows=out.to_dict("records")
        answer="; ".join(f"{row.get(entity)}: {row['first_value']:,.2f} in {row['first_period']} to {row['last_value']:,.2f} in {row['last_period']} (change {row['change']:+,.2f})" for row in detail_rows)
        return _result(frame, {"op":"monthly_trend_first_last","group_by":group_col,"date":date_col,"measure":value_col,"aggregation":aggregate,"period":"month"}, detail_rows, answer or "No positive dated changes were available.", "trend", pd.Series(True,index=frame.index))
    if aggregation is None and "transaction" in requested and re.search(r"\b(most|highest|largest|frequent|often)\b",q) and any(r in requested for r in ("supplier","product","category","branch")) and not re.search(r"\b(average|avg|mean|order size)\b",q):
        group_role=next(r for r in requested if r in ("supplier","product","category","branch")); metric_role="transaction"; op="nunique"; metric_label="transaction frequency"
        aggregation=(op,metric_role,metric_label)
    if aggregation is None and relationship:
        explicit_scope=re.search(r"\b(?:each|every|per|by)\s+(supplier|vendor|distributor|product|medicine|drug|item|category|branch|store|warehouse)s?\b",q)
        scope_word=explicit_scope.group(1) if explicit_scope else None
        scope_role={"vendor":"supplier","distributor":"supplier","medicine":"product","drug":"product","item":"product","store":"branch","warehouse":"branch"}.get(scope_word,scope_word)
        subject=scope_role or next((r for r in ("supplier","product","category","branch") if r in requested and re.search(rf"\b(which|what)\s+{r}s?\b",q)),None)
        subject=subject or next((r for r in requested if r in ("supplier","product","category","branch")),None)
        target=next((r for r in requested if r!=subject and r in ("supplier","product","category","branch")),None)
        if re.search(r"\b(widest range|wide(?:st)? variety|most products?)\b",q) and "product" in requested: target="product"
        if re.search(r"\b(most branches?|widest branch coverage)\b",q) and "branch" in requested: target="branch"
        if subject and target:
            group_role=subject; metric_role=target
            op="count" if re.search(r"\b(frequen|often|most times|most commonly)\b",q) else "nunique"
            metric_label=f"related {target} frequency" if op=="count" else f"related distinct {target}"
            aggregation=(op,metric_role,metric_label)
    if aggregation is None:
        return _decline("clarification", "I recognized a data question but couldn't safely identify its measure. Please specify what to total, count, average, or compare.", {"planner":"offline_schema_intent","requested_roles":requested,"available_roles":{k:v for k,v in roles.items()}})
    op, metric_role, metric_label = aggregation
    # Subject/grouping: in rankings use the entity being ranked; in breakdowns
    # honor each/per/by followed by a field term. Do not infer a grouping when
    # the sentence only refers to an entity as the measure population.
    group_role=None
    entity_words=r"(suppliers?|vendors?|distributors?|products?|medicine|drug|item|categor(?:y|ies)|group|branch(?:es)?|stores?|warehouse|transaction|invoice|order)"
    group_match=re.search(rf"\b(?:by|per|each|every|among|across)\s+(?:each\s+)?{entity_words}\b",q)
    rank_match=re.search(rf"\b(?:which|what)\s+{entity_words}\b",q)
    top_match=re.search(rf"\b(?:top|bottom)\s+(?:\d+|one|two|three|four|five|ten)\s+{entity_words}\b",q)
    if group_match or rank_match or top_match:
        word=(group_match or rank_match or top_match).group(1)
        group_role=_role_from_word(word)
        if group_match and rank_match and op!="mode":
            ranked_word=rank_match.group(1)
            group_role=_role_from_word(ranked_word)
    if re.search(r"\bproduct categor(?:y|ies)\b",q) and "category" in roles and re.search(r"\b(each|every|per|by)\b",q) and not re.search(r"\b(products?|items?)\s+by\b",q):
        group_role="category"
    if re.search(r"\b(dependen(?:t|ce|cy)|concentrat(?:ed|ion))\b",q) and "supplier" in roles:
        group_role="supplier"
    elif not (group_match or rank_match or top_match) and re.search(r"\b(each|every|by|per)\b",q):
        group_role=next((r for r in requested if r in ("supplier","product","category","branch","transaction")),None)
    # For questions that ask products supplied by each supplier, use a distinct
    # relationship plan rather than searching question words as entity values.
    if relationship and op != "mode":
        scope=re.search(r"\b(?:each|every|per|by)\s+(supplier|vendor|distributor|product|medicine|drug|item|category|branch|store|warehouse)s?\b",q)
        rank_subject=re.search(r"\b(?:which|what)\s+(supplier|vendor|distributor|product|medicine|drug|item|category|branch|store|warehouse)s?\b",q)
        raw_subject=scope.group(1) if scope else rank_subject.group(1) if rank_subject else None
        subject_map={"vendor":"supplier","distributor":"supplier","medicine":"product","drug":"product","item":"product","store":"branch","warehouse":"branch"}
        rel_subject=subject_map.get(raw_subject,raw_subject) if raw_subject else group_role
        rel_target=next((r for r in requested if r!=rel_subject and r in ("supplier","product","category","branch")),None)
        if re.search(r"\b(widest range|wide(?:st)? variety|most products?)\b",q) and "product" in requested: rel_target="product"
        if re.search(r"\b(most branches?|widest branch coverage)\b",q) and "branch" in requested: rel_target="branch"
        if rel_subject and rel_target:
            group_role=rel_subject; metric_role=rel_target
            op="count" if re.search(r"\b(frequen|often|most times|most commonly)\b",q) else "nunique"
            metric_label=f"related {rel_target} frequency" if op=="count" else f"related distinct {rel_target}"
    mask, filters, filter_error = _filter_mask(frame,q,roles)
    if filter_error:return _decline("clarification",filter_error,{"planner":"offline_schema_intent","filters":filters})
    work=frame.loc[mask].copy()
    # Relationship questions with both entities are ambiguous about direction
    # unless the grammatical predicate names the relation's subject.
    if relationship and len([r for r in requested if r in roles])<2:
        return _decline("clarification","The requested relationship needs two populated entity fields in the selected data.",{"planner":"offline_schema_intent","requested_roles":requested})
    metric_col=None
    if metric_role and metric_role not in ("transaction",): metric_col=(roles.get(metric_role) or [None])[0]
    if op in ("sum","mean","median","min","max","share","variation") and metric_col is None and not (op=="share" and metric_role=="transaction"):
        return _decline("unsupported",f"The selected data has no populated field for {metric_label}.",{"planner":"offline_schema_intent","metric_role":metric_role,"available_roles":roles})
    # Price comparison between suppliers/branches must preserve the product
    # key, then compare like with like rather than averaging unrelated rows.
    compare_text=re.search(r"\b(same product|same products|for products purchased from multiple suppliers|historically cheaper suppliers?)\b",q)
    if compare_text and roles.get("product") and roles.get("unit_cost") and (roles.get("branch") or roles.get("supplier")):
        entity_role="branch" if re.search(r"\b(branch|branches)\b",q) else "supplier"
        if roles.get(entity_role):
            product_col=roles["product"][0]; entity_col=roles[entity_role][0]; price_col=roles["unit_cost"][0]
            costs=work.dropna(subset=[product_col,entity_col]).copy(); costs[price_col]=pd.to_numeric(costs[price_col],errors="coerce"); costs=costs.dropna(subset=[price_col])
            matrix=costs.groupby([product_col,entity_col],dropna=True)[price_col].mean().rename("average_unit_cost").reset_index()
            multi=matrix.groupby(product_col)[entity_col].transform("nunique").gt(1); matrix=matrix.loc[multi]
            if re.search(r"\b(lowest|cheapest|best price|cheaper)\b",q):
                winners=matrix.groupby(product_col)["average_unit_cost"].transform("min"); matrix=matrix.loc[matrix["average_unit_cost"].eq(winners)]
            matrix=matrix.sort_values([product_col,"average_unit_cost",entity_col],kind="stable")
            result=[{"product":_scalar(row[product_col]),entity_role:_scalar(row[entity_col]),"average_unit_cost":float(row["average_unit_cost"])} for _,row in matrix.iterrows()]
            answer="; ".join(f"{row['product']} / {row[entity_role]}: {row['average_unit_cost']:,.2f}" for row in result)
            return _result(costs,{"op":"average_price_by_product_and_entity","group_by":[product_col,entity_col],"field":price_col,"entity_role":entity_role,"filter_products_with_multiple_entities":True,"choose_lowest":bool(re.search(r"\b(lowest|cheapest|best price|cheaper)\b",q)),"filters":filters},result,answer or "No products were purchased from multiple entities in the selected data.","average unit cost",pd.Series(True,index=costs.index))
    # Resolve measure/operator distinctions from wording.
    if op=="sum" and re.search(r"\b(highest|largest|greatest|most|lowest|smallest|least|top|bottom)\b",q): op="sum"
    ascending=bool(re.search(r"\b(lowest|smallest|least|fewest|bottom|min)\b",q))
    limit=_number_limit(q,1 if rank_match and not rank_match.group(1).endswith("s") else (5 if top_match else 10))
    # Multi-dimensional breakdown: identify the explicitly repeated dimension
    # ("for each branch/category") as the parent and rank/list the other named
    # entity within it. This preserves both requested slots in the plan.
    explicit_groups=[]
    for match in re.finditer(r"\b(?:for\s+)?(?:each|every|per|by)\s+(suppliers?|vendors?|distributors?|products?|medicine|drug|item|categor(?:y|ies)|branch(?:es)?|stores?|warehouse)\b",q):
        word=match.group(1)
        role=("supplier" if word.startswith(("supplier","vendor","distributor")) else "product" if word.startswith(("product","medicine","drug","item")) else "category" if word.startswith("categor") else "branch")
        if role in roles and role not in explicit_groups: explicit_groups.append(role)
    if re.search(r"\bproduct categor(?:y|ies)\b",q):
        explicit_groups=[r for r in explicit_groups if r!="product"]
    # Detect header-grain monetary fields repeated across line rows by using
    # the data's own transaction key and observed cardinalities. Deduplicate
    # only when the amount is invariant within every repeated transaction and
    # all requested grouping dimensions are single-valued at that grain.
    # If a header total cannot be allocated to a requested line dimension,
    # decline instead of assigning the whole invoice amount to every item.
    grain_note=None
    transaction_col=(roles.get("transaction") or [None])[0]
    if metric_role=="amount" and metric_col and transaction_col and transaction_col in work:
        valid_tx=work.dropna(subset=[transaction_col,metric_col])
        if valid_tx[transaction_col].duplicated().any():
            amount_counts=valid_tx.groupby(transaction_col)[metric_col].nunique(dropna=True)
            if amount_counts.le(1).all():
                group_fields=[]
                for role in [*explicit_groups,group_role]:
                    col=(roles.get(role) or [None])[0] if role else None
                    if col and col not in group_fields:group_fields.append(col)
                dimensions=valid_tx.groupby(transaction_col)[group_fields].nunique(dropna=True) if group_fields else None
                if dimensions is not None and dimensions.gt(1).any().any():
                    return _decline("clarification","The amount field is repeated at transaction level, but a requested grouping dimension has multiple values within a transaction. A line-item amount field or an allocation rule is needed for this breakdown.",{"planner":"offline_schema_intent","reason":"header_amount_cannot_be_allocated_to_line_dimension","transaction_field":transaction_col,"amount_field":metric_col,"group_fields":group_fields})
                keys=[transaction_col,*group_fields,metric_col]
                work=valid_tx.drop_duplicates(subset=keys,keep="first").copy()
                grain_note={"grain":"transaction_header","transaction_field":transaction_col,"deduplicated":True,"group_fields":group_fields}
            elif amount_counts.gt(1).any():
                # Varying amounts may be genuine line totals. Retain line grain.
                grain_note={"grain":"line_or_mixed","transaction_field":transaction_col,"deduplicated":False}
    if len(explicit_groups)==1 and group_role and group_role!=explicit_groups[0] and metric_col is not None and op in {"sum","mean","min","max","count","nunique"}:
        parent_role=explicit_groups[0]; child_role=group_role
        parent=(roles.get(parent_role) or [None])[0]; child=(roles.get(child_role) or [None])[0]
        if parent and child:
            if op=="count": values=work.groupby([parent,child],dropna=True).size().rename("value").reset_index()
            elif op=="nunique": values=work.groupby([parent,child],dropna=True)[metric_col].nunique().rename("value").reset_index()
            else:
                numeric=pd.to_numeric(work[metric_col],errors="coerce")
                values=work.assign(_metric=numeric).groupby([parent,child],dropna=True)["_metric"].agg({"sum":"sum","mean":"mean","min":"min","max":"max"}[op]).rename("value").reset_index()
            if re.search(r"\b(most|highest|largest|lowest|smallest|least|fewest)\b",q):
                is_low=bool(re.search(r"\b(lowest|smallest|least|fewest)\b",q))
                extreme=values.groupby(parent)["value"].transform("min" if is_low else "max")
                values=values.loc[values["value"].eq(extreme)]
            values=values.sort_values([parent,"value",child],ascending=[True,False,True],kind="stable")
            result=[{parent_role:_scalar(row[parent]),child_role:_scalar(row[child]),"value":_scalar(row["value"])} for _,row in values.iterrows()]
            answer="; ".join(f"{row[parent_role]} → {row[child_role]}: {row['value']:,.2f}" if isinstance(row["value"],(int,float)) else f"{row[parent_role]} → {row[child_role]}: {row['value']}" for row in result)
            return _result(work,{"op":"grouped_pair_aggregate","group_by":[parent,child],"field":metric_col,"aggregation":op,"within_group_extreme":bool(re.search(r"\b(most|highest|largest|lowest|smallest|least|fewest)\b",q)),"filters":filters},result,answer,metric_label,pd.Series(True,index=work.index))
    if op=="count" and metric_role:
        count_col=(roles.get(metric_role) or [None])[0]
        if count_col: op="nunique"
    if relationship and group_role and metric_role and re.search(r"\b(which|what|list|show)\b",q) and not re.search(r"\b(most|widest|highest|largest|lowest|smallest|multiple|only one|single|count|how many|number|frequency|frequently|mainly|top|bottom|average|mean|total|amount|cost|spend|quantity|units?)\b",q):
        parent=(roles.get(group_role) or [None])[0]; child=(roles.get(metric_role) or [None])[0]
        if parent and child:
            links=work.dropna(subset=[parent,child]).groupby(parent,dropna=True)[child].agg(lambda s: sorted({str(v) for v in s.dropna()}))
            values={str(k):v for k,v in links.items()}
            answer="; ".join(f"{k}: {', '.join(v)}" for k,v in values.items())
            return _result(work,{"op":"distinct_relationship","group_by":parent,"related_field":child,"filters":filters},values,answer,metric_label,pd.Series(True,index=work.index))
    # Return a most-frequent related entity for each parent (e.g. the modal
    # supplier per product). Frequency is calculated from row occurrences,
    # while the result itself is an entity value.
    if op == "mode" and group_role and metric_role:
        parent=(roles.get(group_role) or [None])[0]; child=(roles.get(metric_role) or [None])[0]
        if not parent or not child:
            return _decline("unsupported", "The selected data is missing one of the relationship fields needed for this comparison.", {"planner":"offline_schema_intent","group_role":group_role,"metric_role":metric_role,"available_roles":roles})
        pairs=work.dropna(subset=[parent,child]).groupby([parent,child],dropna=True).size().rename("frequency").reset_index()
        maxes=pairs.groupby(parent)["frequency"].transform("max")
        winners=pairs.loc[pairs["frequency"].eq(maxes)].sort_values([parent,child],kind="stable")
        result=[{group_role:_scalar(row[parent]),metric_role:_scalar(row[child]),"frequency":int(row["frequency"])} for _,row in winners.iterrows()]
        answer="; ".join(f"{row[group_role]}: {row[metric_role]} ({row['frequency']} purchases)" for row in result)
        return _result(work,{"op":"most_frequent_related_entity","group_by":parent,"entity":child,"tie_policy":"return_all","filters":filters},result,answer,metric_label,pd.Series(True,index=work.index))
    if group_role:
        group_col=(roles.get(group_role) or [None])[0]
        if not group_col:
            return _decline("unsupported",f"The selected data has no populated {group_role} field to group by.",{"planner":"offline_schema_intent","group_role":group_role,"available_roles":roles})
        val_col=metric_col
        if op=="count": grouped=work.groupby(group_col,dropna=True).size()
        elif op=="nunique":
            if val_col is None and metric_role: val_col=(roles.get(metric_role) or [None])[0]
            if val_col is None: return _decline("clarification","Which field should be counted distinctly within each group?",{"planner":"offline_schema_intent","group_by":group_col})
            grouped=work.groupby(group_col,dropna=True)[val_col].nunique()
        elif op=="share":
            if metric_role=="transaction": series=work.groupby(group_col)[(roles.get("transaction") or [group_col])[0]].nunique()
            else: series=pd.to_numeric(work[val_col],errors="coerce").groupby(work[group_col]).sum()
            denom=series.sum(); grouped=series.div(denom).mul(100) if denom else series*0
        elif op=="variation":
            grouped=work.groupby(group_col,dropna=True)[val_col].agg(lambda s: pd.to_numeric(s,errors="coerce").max()-pd.to_numeric(s,errors="coerce").min())
        else:
            series=pd.to_numeric(work[val_col],errors="coerce")
            grouped=series.groupby(work[group_col]).agg({"sum":"sum","mean":"mean","median":"median","min":"min","max":"max"}[op])
        if relationship and op=="nunique" and re.search(r"\bmultiple branches?\b",q): grouped=grouped[grouped.gt(1)]
        if relationship and op=="nunique" and re.search(r"\bmultiple suppliers?|more than one supplier\b",q): grouped=grouped[grouped.gt(1)]
        if relationship and op=="nunique" and re.search(r"\b(?:only one|single) supplier\b",q): grouped=grouped[grouped.eq(1)]
        ranked=bool(re.search(r"\b(highest|largest|greatest|most|lowest|smallest|least|fewest|widest|top|bottom|rank|ranking)\b",q))
        grouped=grouped.sort_values(ascending=ascending,kind="stable") if ranked or op=="share" else grouped
        if ranked: grouped=grouped.head(limit)
        results=[{group_col:_scalar(k),"value":_scalar(v)} for k,v in grouped.items()]
        answer="; ".join(f"{x[group_col]}: {x['value']:,.2f}" if isinstance(x["value"],(float,int)) else f"{x[group_col]}: {x['value']}" for x in results)
        if re.search(r"\b(overly dependent|dependence|dependency|concentrat(?:ed|ion))\b",q):
            answer += " These are supplier shares of total spend; the data does not define a policy threshold for calling dependence excessive."
        return _result(work,{"op":op,"group_by":group_col,"field":val_col,"filters":filters,"limit":limit if ranked else None},results,answer or "No matching rows.",metric_label,pd.Series(True,index=work.index))
    if op=="count":
        value=int(len(work)); detail={"op":"count_rows","filters":filters}
    elif op=="nunique":
        target=metric_col or ((roles.get(metric_role) or [None])[0] if metric_role else None)
        if target is None:
            # entity named in question, e.g. "how many products".
            target=next(((roles.get(r) or [None])[0] for r in requested if roles.get(r)),None)
        if target is None:return _decline("clarification","Which entity should I count distinctly?",{"planner":"offline_schema_intent","requested_roles":requested})
        value=int(work[target].nunique(dropna=True)); detail={"op":"count_distinct","field":target,"filters":filters}
    elif op=="share" and metric_role=="transaction":
        status_col=(roles.get("status") or [None])[0]
        if status_col is None:return _decline("unsupported","The selected data has no status field for a paid-invoice percentage.",{"planner":"offline_schema_intent"})
        paid=frame[status_col].astype(str).str.casefold().eq("paid")
        if re.search(r"\b(unpaid|outstanding|not paid)\b",q):paid=~paid
        value=float(paid.sum()/len(frame)*100) if len(frame) else 0.0
        detail={"op":"share","entity":"transaction","status_field":status_col,"filters":filters}
    else:
        col=metric_col
        if col is None:return _decline("clarification","Which numeric field should I use for this calculation?",{"planner":"offline_schema_intent","metric_role":metric_role})
        series=pd.to_numeric(work[col],errors="coerce").dropna()
        if op=="share":
            original=pd.to_numeric(frame[col],errors="coerce").sum()
            filtered=pd.to_numeric(work[col],errors="coerce").sum()
            value=float(filtered/original*100) if original else 0.0
        elif op=="sum": value=float(series.sum())
        elif op=="mean": value=float(series.mean())
        elif op=="median": value=float(series.median())
        elif op=="variation": value=float(series.max()-series.min())
        else:
            value=float(series.max() if not ascending else series.min())
            if re.search(r"\b(purchase|invoice|transaction|record|order)\b", q):
                extreme_idx=series.idxmax() if not ascending else series.idxmin()
                row=work.loc[extreme_idx]
                detail_fields=[c for c in [*(roles.get("transaction") or []),*(roles.get("date") or []),*(roles.get("supplier") or []),*(roles.get("product") or []),*(roles.get("category") or []),*(roles.get("quantity") or []),*(roles.get("unit_cost") or []),*(roles.get("amount") or []),*(roles.get("status") or []),*(roles.get("branch") or [])] if c in work.columns]
                record={str(c):_scalar(row[c]) for c in dict.fromkeys(detail_fields) if pd.notna(row[c])}
                if "source_row" in work.columns: record["source_row"]=_scalar(row["source_row"])
                return _result(work.loc[[extreme_idx]],{"op":op,"field":col,"record":record,"filters":filters},record,f"Matching purchase record: {record}",metric_label,pd.Series(True,index=[extreme_idx]))
        detail={"op":op,"field":col,"filters":filters}
    text=f"{metric_label.title()}: {value:,.2f}." if isinstance(value,(int,float)) else str(value)
    return _result(work,detail,value,text,metric_label,pd.Series(True,index=work.index))


def _decline(status: str, reason: str, diagnostic: dict[str, Any]) -> dict[str, Any]:
    return {"answer":reason,"values":{"status":status,"intent_plan":diagnostic},"source_rows":[]}


def _result(frame: pd.DataFrame, plan: dict[str, Any], value: Any, answer: str, metric: str, mask: pd.Series) -> dict[str, Any]:
    rows=frame.loc[mask] if len(mask)==len(frame) else frame
    ids=rows["source_row"].dropna().tolist() if "source_row" in rows else (rows.index+1).tolist()
    return {"answer":answer,"values":{"status":"ok","metric":metric,"value":value,"intent_plan":{"planner":"offline_schema_intent_v1",**plan,"rows":int(len(rows))}},"source_rows":ids}
