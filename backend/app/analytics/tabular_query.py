"""Conservative, schema-driven execution for questions over connected tabular data.

This is intentionally deterministic: it operates on the complete selected frame,
returns contributing source rows, and declines unsupported operations.
"""
from __future__ import annotations
import re
import difflib
import logging
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd

logger = logging.getLogger(__name__)


def _format_money(value):
    """Round display values from their decimal text, avoiding float tie drift."""
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:,.2f}"


def _catalog_available_mask(series):
    """Recognize explicit availability labels in pharmacy catalog exports."""
    values = series.fillna("").astype(str).str.strip().str.casefold()
    return values.isin({"available", "in stock", "low stock", "add to cart"})


def is_distinct_entity_count_question(question: str) -> bool:
    """Detect scalar unique-entity counts that must precede ranking answers."""
    q = re.sub(r"\s+", " ", str(question).casefold()).strip()
    return bool(re.search(
        r"\b(?:how many|number of|count(?: of)?)\s+(?:(?:the|all)\s+)?"
        r"(?:distinct|unique|different)\s+(?:products?|medicines?|drugs?|manufacturers?|companies|doctors?|physicians?)\b",
        q,
    ))


def is_ranked_record_count_question(question: str) -> bool:
    """Detect ranked transaction-count asks that must bypass value rankings."""
    q = re.sub(r"\s+", " ", str(question).casefold()).strip()
    return bool(
        re.search(r"\b(how many|number of|count)\b", q)
        and re.search(r"\b(receipts?|invoices?|transactions?)\b", q)
        and re.search(r"\b(highest|largest|most|fewest|least)\b", q)
        and not re.search(r"\bat least\b", q)
    )


def _answer_line_quantity_median(question: str, frame: pd.DataFrame):
    """Compute a median over recorded quantity values at the requested line grain."""
    q = re.sub(r"\s+", " ", str(question).casefold()).strip()
    if not (
        re.search(r"\b(median|middle value)\b", q)
        and re.search(r"\b(quantity|qty|units?)\b", q)
        and re.search(r"\b(lines?|details?|individual|per item|each item)\b", q)
    ):
        return None
    work = frame
    if re.search(r"\bsales?\b", q) and "txn_type" in work:
        sales_mask = work.txn_type.fillna("").astype(str).str.casefold().str.contains(
            r"sale|return|refund|invoice|bill|transaction|dispens", regex=True
        )
        if sales_mask.any():
            work = work.loc[sales_mask]
    elif re.search(r"\binventory\b", q) and "txn_type" in work:
        inventory_mask = work.txn_type.fillna("").astype(str).str.casefold().str.contains(r"inventory|stock|batch|purchase", regex=True)
        if inventory_mask.any():
            work = work.loc[inventory_mask]
    field = next((column for column in ("quantity", "quantity_sold", "qty_sold", "units_sold", "total_qty") if column in work), None)
    if field is None:
        return None
    quantities = pd.to_numeric(work[field], errors="coerce")
    valid = quantities.notna()
    if "product_id" in work and re.search(r"\b(sales?|inventory|detail|line|item)\b", q):
        product_present = work.product_id.notna()
        if (valid & product_present).any():
            valid &= product_present
    matched = work.loc[valid]
    quantities = pd.to_numeric(matched[field], errors="coerce")
    if quantities.empty:
        return None
    median = float(quantities.median())
    population = "sales detail line" if re.search(r"\bsales?\b", q) else "inventory line" if re.search(r"\binventory\b", q) else "detail line"
    source_file_col = next((column for column in ("source_file", "file_id") if column in matched), None)
    row_col = "source_row" if "source_row" in matched else None
    if source_file_col and row_col:
        source_rows = list(dict.fromkeys((str(row[source_file_col]), row[row_col]) for _, row in matched.iterrows()))
    else:
        source_rows = matched.index.tolist()
    return {
        "answer": f"Median quantity per {population}: {median:g} units.",
        "values": {"status": "ok", "metric": "median_quantity_per_line", "field": str(field), "median": median, "line_count": int(len(quantities))},
        "source_rows": source_rows,
    }


def _answer_named_product_quantity_total(question: str, frame: pd.DataFrame):
    """Sum sales-line quantities for one explicitly named product."""
    q = re.sub(r"\s+", " ", str(question).casefold()).strip()
    if not (
        re.search(r"\b(sales?|sold)\b", q)
        and re.search(r"\b(units?|quantity|qty)\b", q)
        and re.search(r"\b(total|sum|how many|how much|kul|kitni|kitne|kitna)\b", q)
        and not re.search(r"\b(amount|revenue|sales value|profit|margin)\b", q)
    ):
        return None
    product_col = next((column for column in ("product_id", "product_name", "medicine_name", "item_name", "product", "item") if column in frame and frame[column].notna().any()), None)
    if product_col is None:
        return None
    work = frame
    if "txn_type" in work:
        sales_mask = work.txn_type.fillna("").astype(str).str.casefold().str.contains(
            r"sale|return|refund|invoice|bill|transaction|dispens", regex=True
        )
        if sales_mask.any():
            work = work.loc[sales_mask]
    names = [str(value).strip() for value in work[product_col].dropna().unique() if str(value).strip()]
    matches = [name for name in names if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", q)]
    if len(matches) > 1:
        return None
    if not matches:
        # A product-specific quantity request must never fall through to the
        # all-sales quantity aggregate. Recognize the named phrase either
        # after a conventional preposition or before a sales clause (including
        # Roman Urdu forms such as "<product> ki sales mein ...").
        phrase = None
        for pattern in (r"\b(?:for|of|about)\s+(.+?)(?=\s+(?:in|during|between|from|sold|sales?)\b|[?.!,]|$)",):
            found = re.search(pattern, q)
            if found:
                phrase = found.group(1).strip()
                break
        if phrase is None:
            before_sales = re.split(r"\b(?:sales?|sold)\b", q, maxsplit=1)[0]
            tokens = re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", before_sales)
            generic = {
                "what", "which", "how", "many", "much", "is", "are", "the", "a", "an", "of", "for", "in", "on", "to", "from",
                "total", "sum", "quantity", "qty", "unit", "units", "kul", "kitni", "kitne", "kitna", "ki", "ka", "ke", "mein", "record", "hui", "hain",
            }
            named = [token for token in tokens if token not in generic]
            if named:
                phrase = " ".join(named)
        if phrase:
            quantity_col = next((column for column in ("quantity", "quantity_sold", "qty_sold", "units_sold") if column in work), None)
            return {
                "answer": f"I couldn't find a sales record for `{phrase}` in the selected data. Check the product name or provide its ID.",
                "values": {"status": "no_matching_product", "product_query": phrase, "product_field": product_col, "quantity_field": quantity_col},
                "source_rows": [],
            }
        return None
    quantity_col = next((column for column in ("quantity", "quantity_sold", "qty_sold", "units_sold") if column in work), None)
    if quantity_col is None:
        return None
    matched = work.loc[work[product_col].astype(str).str.casefold().eq(matches[0].casefold())].copy()
    quantities = pd.to_numeric(matched[quantity_col], errors="coerce")
    valid = quantities.notna()
    matched = matched.loc[valid]
    quantities = quantities.loc[valid]
    if quantities.empty:
        return None
    total = float(quantities.sum())
    source_col = next((column for column in ("source_file", "file_id") if column in matched), None)
    if source_col and "source_row" in matched:
        source_rows = list(dict.fromkeys((str(row[source_col]), row["source_row"]) for _, row in matched.iterrows()))
    else:
        source_rows = matched.index.tolist()
    return {
        "answer": f"Total quantity sold for {matches[0]}: {total:g} units across {len(matched)} sales lines.",
        "values": {"status": "ok", "product": matches[0], "total_quantity_sold": total, "sales_line_count": int(len(matched)), "quantity_field": quantity_col},
        "source_rows": source_rows,
    }


def _fuzzy_catalog_product(question: str, product_names):
    """Conservative single-token typo recovery for product names only."""
    stopwords = {
        "what", "which", "who", "where", "when", "how", "much", "many", "is", "are", "the", "a", "an",
        "of", "for", "from", "in", "on", "to", "me", "my", "show", "list", "find", "give", "tell", "about",
        "price", "cost", "available", "availability", "company", "manufacturer", "product", "products", "medicine",
        "medicines", "drug", "drugs", "pack", "size", "discount", "discounted", "original", "current", "currently",
        "pkr", "rs", "pk", "and", "or", "with", "without", "has", "have", "does", "do", "it", "this", "that",
    }
    tokens = [t.casefold() for t in re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", question) if len(t) >= 4 and t.casefold() not in stopwords]
    if not tokens:
        return None
    names = list(dict.fromkeys(str(v) for v in product_names if pd.notna(v)))
    candidates = []
    for name in names:
        name_tokens = [t.casefold() for t in re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", name) if len(t) >= 4]
        if not name_tokens:
            continue
        score = max((difflib.SequenceMatcher(None, query_token, name_token).ratio() for query_token in tokens for name_token in name_tokens), default=0.0)
        if score >= 0.88:
            candidates.append((score, name))
    candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
    if not candidates:
        return None
    top_score = candidates[0][0]
    tied = [name for score, name in candidates if top_score - score < 0.04]
    if len(tied) > 1:
        return {"ambiguous": tied[:5]}
    return {"name": candidates[0][1], "score": top_score}


def _answer_schema_aggregate(question: str, frame: pd.DataFrame, row_ids: pd.Series):
    """Plan common group/aggregate questions from the fields actually present.

    This path is deliberately schema-driven: intent words select a dimension
    and measure, while all values and calculations come from the complete
    filtered DataFrame. It prevents a high/low question from degrading to a
    sample of semantically retrieved rows.
    """
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    if frame.empty:
        return None
    # Forecasts must use the forecast engine, not historical aggregation.
    if re.search(r"\b(forecast|predict(?:ion)?|project(?:ion)?|will\s+(?:i|we|it)\s+sell|next\s+(?:month|week)|agle\s+(?:mahine|month))\b", q):
        return None
    if is_distinct_entity_count_question(q):
        distinct_field = next((
            column for column, pattern in (
                ("product_id", r"\b(products?|medicines?|drugs?)\b"),
                ("manufacturer", r"\b(manufacturers?|companies)\b"),
                ("doctor_name", r"\b(doctors?|physicians?)\b"),
            ) if column in frame and re.search(pattern, q)
        ), None)
        if distinct_field:
            count = int(frame[distinct_field].dropna().astype(str).nunique())
            entity_name = "products" if distinct_field == "product_id" else "manufacturers" if distinct_field == "manufacturer" else "doctors"
            return {
                "answer": f"{count:,} distinct {entity_name} are recorded.",
                "values": {"status": "ok", "distinct_count": count, f"distinct_{entity_name}": count, "field": distinct_field},
                "source_rows": row_ids.loc[frame.index].dropna().tolist(),
            }
    if re.search(r"\b(discount|discounted)\b", q) and re.search(r"\b(which|what|list|show|kin|kon si|konsi)\b", q):
        return None
    categorical_share_request = bool(
        re.search(r"\b(percent(?:age)?|share|proportion)\b", q)
        and re.search(r"\b(payment method|payment type|payment mode|category|manufacturer|branch)\b", q)
    )
    # Inventory and purchase rows have different quantity/value semantics.
    # Keep this sales-performance aggregator away from schemas that carry
    # explicit stock or purchase-order identifiers.
    if ("stock_qty" in frame.columns or "purchase_order_no" in frame.columns) and not categorical_share_request:
        return None
    if categorical_share_request and "txn_type" in frame:
        sale_rows = frame.txn_type.fillna("").astype(str).str.casefold().str.contains(r"sale|invoice|receipt", regex=True)
        if sale_rows.any():
            frame = frame.loc[sale_rows].copy()
            row_ids = row_ids.loc[frame.index]
    # Let the established inventory/customer and multi-measure planners handle
    # these shapes; this sales aggregation path must not consume their fields.
    if re.search(r"\b(total|combined) stock\b|\bon[- ]hand stock\b", q):
        return None
    if re.search(r"\b(customer|client)\s+[a-z][a-z'-]+\s+[a-z][a-z'-]+\b", q) and "customer_id" not in frame:
        return None
    if re.search(r"\b(quantity|units?)\b.{0,50}\b(and|plus)\b.{0,50}\b(sales amount|revenue|amount)\b", q) and not re.search(r"\b(both|best[- ]performing|high[- ]selling)\b", q):
        return None
    if re.search(r"\b(or|versus|\bvs\b|compare|greater|higher)\b", q):
        for col in ("branch", "warehouse", "product_id", "manufacturer", "category", "supplier_id"):
            if col not in frame:
                continue
            mentions=[str(v) for v in frame[col].dropna().unique() if len(str(v))>2 and str(v).casefold() in q]
            if len(set(mentions))>=2:
                return None
    currency = frame.attrs.get("source_currency") if hasattr(frame, "attrs") else None
    currency_suffix = f" {currency}" if currency and currency != "MIXED" else ""
    def money_suffix(label):
        return currency_suffix if re.search(r"revenue|sales|price|margin|profit|cost|amount|value", label, re.I) else ""
    source_transaction_col = next((c for c in ("transaction_id", "invoice_id", "order_id", "receipt_id") if c in frame.columns), None)
    source_transaction_count = int(frame[source_transaction_col].nunique()) if source_transaction_col else len(frame)

    aliases = [
        ("category", r"\b(therapeutic class(?:es)?|drug class(?:es)?|categories|category|classes?)\b"),
        ("manufacturer", r"\b(manufacturer(?:s)?(?:['’]s)?|pharma compan(?:y|ies)|companies)\b"),
        ("branch", r"\b(pharmacy branch(?:es)?|branch(?:es)?)\b"),
        ("payment_method", r"\b(payment methods?|payment types?|payment modes?)\b"),
        ("pharmacist_name", r"\b(pharmacists?|dispensing staff)\b"),
        ("doctor_name", r"\b(prescribing doctors?|doctors?|physicians?)\b"),
        ("product_id", r"\b(products?|medicines?|drugs?|items?)\b"),
        ("batch_no", r"\b(batches|batch|lots?|lot numbers?)\b"),
        ("rack_location", r"\b(racks?|shelves|locations?)\b"),
    ]
    dim_candidates = {
        "category": ("category", "therapeutic_class", "drug_class"),
        "manufacturer": ("manufacturer", "company", "supplier_name"),
        "branch": ("branch", "branch_name", "pharmacy_branch", "warehouse"),
        "payment_method": ("payment_method", "payment_type", "payment_mode"),
        "pharmacist_name": ("pharmacist_name", "attending_pharmacist", "cashier_name"),
        "doctor_name": ("doctor_name", "prescribing_doctor", "prescriber"),
        "product_id": ("product_id", "product_name", "medicine_name", "item_name", "product_code"),
        "batch_no": ("batch_no", "batch_number", "lot_no", "lot_number"),
        "rack_location": ("rack_location", "stock_location_rack", "warehouse", "location"),
    }
    dimensions = []
    for label, pattern in aliases:
        match = re.search(pattern, q)
        if not match:
            continue
        col = next((candidate for candidate in dim_candidates[label] if candidate in frame.columns), None)
        if col and col not in [item[0] for item in dimensions]:
            dimensions.append((col, label, match.start()))
    dimensions.sort(key=lambda item: item[2])
    dimensions = [(col, label) for col, label, _ in dimensions]

    # Entity words can describe a filtered population rather than an output
    # breakdown ("AstraZeneca products"). Avoid accidental product grouping.
    explicit_breakdown = bool(re.search(r"\b(by|per|each|for each|at each|across|compare|top|bottom)\b", q))
    named_manufacturer = next((str(v) for v in frame.get("manufacturer", pd.Series(dtype=str)).dropna().unique()
                               if len(str(v)) > 2 and str(v).casefold() in q), None)
    if named_manufacturer and not explicit_breakdown:
        wants_product_list = bool(re.search(r"\b(which|what|list|show) products?\b", q) and not re.search(r"\b(revenue|sales|total|average|price|margin|profit|number|count)\b", q))
        if not wants_product_list:
            dimensions = [(col, label) for col, label in dimensions if label != "product_id"]
    # A total/overall question measures the filtered rows as a whole. Words
    # such as "products" in "total revenue from prescription products" do not
    # request a per-product breakdown.
    if not explicit_breakdown and re.search(r"\b(how much|total|overall|combined)\b", q) and re.search(r"\b(revenue|sales|amount|value)\b", q):
        dimensions = [(col, label) for col, label in dimensions if label != "product_id"]
    if re.search(r"\bhow many units?\b", q) and not re.search(r"\b(by|each|per|top|which|most|fewest|least)\b", q):
        dimensions = [(col, label) for col, label in dimensions if label != "product_id"]
    if re.search(r"\b(total|combined|sum)\b.{0,40}\b(units?|quantity)\b", q) and re.search(r"\b(across all|all products|all medicines|all items)\b", q):
        dimensions = [(col, label) for col, label in dimensions if label != "product_id"]
    count_products_by_manufacturer = bool(
        any(label == "manufacturer" for _, label in dimensions)
        and re.search(r"\b(number|count)\b.{0,30}\bproducts?\b|\blargest number of products\b", q)
    )
    if count_products_by_manufacturer:
        dimensions = [(col, label) for col, label in dimensions if label != "product_id"]
    if any(label == "manufacturer" for _, label in dimensions) and any(label == "product_id" for _, label in dimensions) and not re.search(r"\b(each|per|at each|for each)\b", q) and re.search(r"\bmanufacturer['’]s products?\b", q):
        dimensions = [(col, label) for col, label in dimensions if label != "product_id"]

    # The dimension asked for as the answer's subject outranks incidental
    # dimensions in phrases like "products on Rack-A" or "manufacturer's
    # products". Preserve both dimensions for explicit nested requests.
    target_dimension = next((label for label, pattern in (
        ("branch", r"\bwhich (?:pharmacy )?branches?\b"),
        ("manufacturer", r"\bwhich manufacturers?\b"),
        ("rack_location", r"\bwhich racks?\b"),
        ("product_id", r"\bwhich products?\b"),
    ) if re.search(pattern, q)), None)
    nested_requested = bool(re.search(r"\b(each|per|at each|for each)\b", q))
    if target_dimension and not nested_requested:
        dimensions = [(col, label) for col, label in dimensions if label == target_dimension or (label == "_time_group")]

    # "by branch, product, class..." is a report request for separate
    # breakdowns, not a cartesian product across every selected dimension.
    if re.search(r"\boverall summary\b|\bsummary of sales performance\b", q):
        requested = [
            ("branch", ("branch", "branch_name")),
            ("product_id", ("product_id", "product_name", "medicine_name")),
            ("category", ("category", "therapeutic_class")),
            ("manufacturer", ("manufacturer", "company")),
            ("payment_method", ("payment_method", "payment_type")),
        ]
        amount = next((c for c in ("amount", "invoice_total", "sales_amount") if c in frame), None)
        if amount:
            pieces, payload, cited = [], {}, []
            for label, candidates in requested:
                col = next((c for c in candidates if c in frame), None)
                if not col:
                    continue
                sums = pd.to_numeric(frame[amount], errors="coerce").groupby(frame[col]).sum().sort_values(ascending=False).head(5)
                payload[label] = {str(k): float(v) for k, v in sums.items()}
                pieces.append(label.replace("_", " ").title() + ": " + ", ".join(f"{k} ({v:,.2f}{currency_suffix})" for k, v in sums.items()))
                cited.extend(row_ids.loc[frame[frame[col].isin(sums.index)].index].tolist())
            if pieces:
                return {"answer": "Sales performance summary: " + "; ".join(pieces) + ".", "values": payload, "source_rows": cited}

    # Apply explicit entity and type filters before aggregating.
    df = frame.copy()
    # A named categorical share needs its denominator from the full eligible
    # population; applying the entity filter first would make the result 100%.
    if re.search(r"\b(percent(?:age)?|share|proportion)\b", q) and re.search(r"\b(payment method|payment type|payment mode|category|manufacturer|branch)\b", q):
        share_col = next((column for column in ("payment_method", "payment_type", "payment_mode", "category", "therapeutic_class", "manufacturer", "branch", "branch_name") if column in frame and frame[column].notna().any()), None)
        if share_col:
            named_values = [
                str(value) for value in frame[share_col].dropna().astype(str).unique()
                if len(str(value).strip()) > 2 and re.search(rf"(?<!\w){re.escape(str(value).casefold())}(?!\w)", q)
            ]
            if len(named_values) == 1:
                population = frame
                if "txn_type" in population and population.txn_type.fillna("").astype(str).str.contains(r"sale|invoice|receipt", case=False, regex=True).any():
                    population = population.loc[population.txn_type.fillna("").astype(str).str.contains(r"sale|invoice|receipt", case=False, regex=True)]
                population = population.loc[population[share_col].notna()]
                transaction_col = next((column for column in ("invoice_id", "transaction_id", "order_id", "receipt_id") if column in population and population[column].notna().any()), None)
                has_header_provenance = (
                    "_header_source_file" in population and "_header_source_row" in population
                    and population["_header_source_file"].notna().any()
                    and population["_header_source_row"].notna().any()
                )
                if has_header_provenance:
                    population = population.loc[population["_header_source_file"].notna() & population["_header_source_row"].notna()]
                    header_keys = ["_header_source_file", "_header_source_row"]
                    denominator = int(population[header_keys].drop_duplicates().shape[0])
                    matching = population.loc[population[share_col].astype(str).str.casefold().eq(named_values[0].casefold())]
                    numerator = int(matching[header_keys].drop_duplicates().shape[0])
                elif transaction_col:
                    denominator = int(population[transaction_col].nunique())
                    matching = population.loc[population[share_col].astype(str).str.casefold().eq(named_values[0].casefold())]
                    numerator = int(matching[transaction_col].nunique())
                else:
                    denominator = int(len(population))
                    matching = population.loc[population[share_col].astype(str).str.casefold().eq(named_values[0].casefold())]
                    numerator = int(len(matching))
                if denominator:
                    percentage = numerator / denominator * 100
                    if ("_header_source_file" in matching and "_header_source_row" in matching
                            and matching["_header_source_file"].notna().any()):
                        header_rows = matching.loc[matching["_header_source_file"].notna() & matching["_header_source_row"].notna()].drop_duplicates(subset=["_header_source_file", "_header_source_row"])
                        citations = [(str(item["_header_source_file"]), item["_header_source_row"]) for _, item in header_rows.iterrows()]
                    elif "source_file" in matching and "source_row" in matching:
                        citations = list(dict.fromkeys((str(item["source_file"]), item["source_row"]) for _, item in matching.iterrows() if pd.notna(item["source_file"]) and pd.notna(item["source_row"])))
                    else:
                        citations = row_ids.loc[matching.index].dropna().tolist()
                    return {
                        "answer": f"{named_values[0]} accounts for {percentage:.2f}% of recorded transactions ({numerator:,} of {denominator:,}).",
                        "values": {"status": "ok", "category": share_col, "value": named_values[0], "numerator": numerator, "denominator": denominator, "percentage": percentage},
                        "source_rows": citations,
                    }
    compare_payment_types = bool(re.search(r"\b(compare|versus|\bvs\b|cash and insurance|insurance and cash|cash and credit|credit and cash)\b", q) and re.search(r"\b(cash|credit)\b", q) and re.search(r"\b(insurance|credit|cash)\b", q))
    compare_prescription_types = bool(re.search(r"\b(compare|versus|\bvs\b|prescription and otc|otc and prescription)\b", q) and "prescription" in q and "otc" in q)
    for col in ("manufacturer", "branch", "payment_method", "pharmacist_name", "doctor_name", "product_id", "product_code", "batch_no", "rack_location", "category"):
        if col not in df.columns:
            continue
        if col == "payment_method" and compare_payment_types:
            continue
        values = sorted((str(v) for v in frame[col].dropna().unique() if str(v).strip()), key=len, reverse=True)
        match = next((value for value in values if len(value) > 2 and value.casefold() in q), None)
        if not match and col in ("pharmacist_name", "doctor_name"):
            query_tokens = set(re.findall(r"[a-z]+", q))
            def person_match(value):
                tokens = [t for t in re.findall(r"[a-z]+", value.casefold()) if t not in {"dr", "rph", "pharmd", "pharmacist", "endocrinologist", "cardiologist", "pulmonologist", "physician", "consultant"}]
                return len(tokens) >= 2 and all(token in query_tokens for token in tokens)
            match = next((value for value in values if person_match(value)), None)
        if match:
            df = df[df[col].astype(str).str.casefold().eq(match.casefold())]

    prescription_col = next((c for c in ("prescription_required", "schedule_flag", "prescription_ref") if c in df.columns), None)
    if prescription_col and not compare_prescription_types and re.search(r"\b(otc|over[- ]the[- ]counter)\b", q):
        flags = df[prescription_col].fillna("").astype(str).str.casefold()
        df = df[flags.str.contains(r"otc|non.?prescription|no\b|false|\boff\b", regex=True)]
    elif prescription_col and not compare_prescription_types and re.search(r"\b(prescriptions?|required prescription|prescription[- ]required|rx)\b", q):
        flags = df[prescription_col].fillna("").astype(str).str.casefold()
        df = df[flags.str.contains(r"prescription|required|yes|true|\bon\b", regex=True) & ~flags.str.contains(r"otc|no\b|false", regex=True)]
    if "payment_method" in df and not compare_payment_types and re.search(r"\binsurance\b", q):
        df = df[df.payment_method.astype(str).str.contains("insurance", case=False, na=False)]
    elif "payment_method" in df and not compare_payment_types and re.search(r"\bcash\b", q):
        df = df[df.payment_method.astype(str).str.contains("cash", case=False, na=False)]
    if compare_payment_types and "payment_method" in df:
        df = df[df.payment_method.astype(str).str.contains(r"cash|insurance|credit", case=False, na=False, regex=True)]

    if df.empty:
        return {"answer": "No matching records were found in the selected data.", "values": {"matched_records": 0}, "source_rows": []}

    # Clarification is safer than silently using a whole-file total when a
    # query explicitly refers to an unnamed medicine.
    if re.search(r"\b(particular|specific) medicine\b", q) and not re.search(r"\b[a-z][a-z0-9+-]*\s+\d+(?:mg|mcg|ml)\b", q):
        return {"answer": "Which medicine do you mean? Please provide its name so I can look up the matching records.", "values": {"status": "needs_product_context"}, "source_rows": []}

    # Identify a time grain when the question asks for a daily/monthly series.
    time_grain = None
    if "date" in df and re.search(r"\b(day|daily|each day|by day|per day)\b", q):
        time_grain = "day"
    elif "date" in df and re.search(r"\b(monthly|each month|by month|per month|over time|trend)\b", q) and not re.search(r"\b(this month|current month|last month|previous month|is mahine|pichle mahine)\b", q):
        time_grain = "month"
    if "date" in df and re.search(r"\b(today|latest day|most recent day|this month|current month|last month|previous month|is mahine|pichle mahine)\b", q):
        dates = pd.to_datetime(df.date, errors="coerce")
        latest = dates.max()
        if pd.notna(latest):
            if re.search(r"\b(last month|previous month|pichle mahine)\b", q):
                period = latest.normalize().replace(day=1) - pd.Timedelta(days=1)
                df = df.loc[(dates.dt.month == period.month) & (dates.dt.year == period.year)]
            elif re.search(r"\b(this month|current month|is mahine)\b", q):
                df = df.loc[(dates.dt.month == latest.month) & (dates.dt.year == latest.year)]
            else:
                df = df.loc[dates.dt.date == latest.date()]
    elif "date" in df and re.search(r"\b(over time|trend)\b", q) and time_grain is None:
        time_grain = "month"
    if time_grain:
        dt = pd.to_datetime(df.date, errors="coerce")
        df = df.loc[dt.notna()].copy()
        df["_time_group"] = dt.loc[df.index].dt.strftime("%Y-%m-%d" if time_grain == "day" else "%Y-%m")
        dimensions = [("_time_group", time_grain)] + dimensions

    if compare_payment_types and "payment_method" in df and not any(c == "payment_method" for c, _ in dimensions):
        dimensions.insert(0, ("payment_method", "payment_method"))
    if compare_prescription_types and prescription_col and not any(c == prescription_col for c, _ in dimensions):
        dimensions.insert(0, (prescription_col, "prescription_type"))

    # A distinct-entity question counts the named column; it must not group the
    # same column into one row per entity and report 1 for every row.
    distinct_field = next((c for c, pattern in (("product_id", r"\b(medicines?|drugs?|products?)\b"), ("manufacturer", r"\b(manufacturers?|companies)\b"), ("doctor_name", r"\b(doctors?|physicians?)\b")) if c in df and re.search(pattern, q)), None)
    if distinct_field and re.search(r"\b(different|distinct|unique)\b", q) and not re.search(r"\b(units?|quantity|volume)\b", q):
        count = int(df[distinct_field].dropna().astype(str).nunique())
        return {"answer": f"{count:,} distinct {distinct_field.replace('_', ' ')} values are recorded.", "values": {"distinct_count": count, "field": distinct_field}, "source_rows": row_ids.loc[df.index].dropna().tolist()}

    if not dimensions and re.search(r"\b(highest|lowest|top|bottom|by|per|each|most|fewest|least|compare)\b", q) and not re.search(r"\btransactions?\b", q):
        return None

    metric_col = next((c for c in ("amount", "invoice_total", "total_transaction_value", "sales_amount", "transaction_value") if c in df.columns and pd.to_numeric(df[c], errors="coerce").notna().any()), None)
    qty_col = next((c for c in ("quantity", "quantity_sold", "qty_sold", "units_sold", "total_qty") if c in df.columns and pd.to_numeric(df[c], errors="coerce").notna().any()), None)
    cost_col = next((c for c in ("cost", "unit_cost", "unit_cost_price") if c in df.columns and pd.to_numeric(df[c], errors="coerce").notna().any()), None)
    mrp_col = next((c for c in ("mrp", "maximum_retail_price", "retail_price") if c in df.columns and pd.to_numeric(df[c], errors="coerce").notna().any()), None)
    id_col = next((c for c in ("transaction_id", "invoice_id", "order_id", "receipt_id") if c in df.columns), None)

    q_count = bool(re.search(r"\b(how many|number of|count|transactions?|records?)\b", q))
    q_frequency = bool(re.search(r"\b(most often|most frequently|frequent(?:ly)?|commonly|appears most)\b", q))
    q_average = bool(re.search(r"\b(average|avg|mean)\b", q))
    q_margin = bool(re.search(r"\b(margin|profit|markup)\b", q) or ("cost" in q and "retail price" in q and "difference" in q))
    q_mrp = bool(re.search(r"\b(mrp|maximum retail|retail price)\b", q))
    q_cost = bool(re.search(r"\b(cost price|unit cost|purchase cost)\b", q))
    q_quantity = bool(re.search(r"\b(units?|quantity|volume|most sold|top[- ]selling|fast[- ]selling)\b", q))
    q_revenue = bool(re.search(r"\b(revenue|sales|sales value|transaction values?|amount)\b", q))
    if re.search(r"\b(performs?|performed) best\b", q):
        q_revenue = True
    q_percentage = bool(re.search(r"\b(percent(?:age)?|share|proportion)\b", q))

    if q_margin and cost_col and mrp_col and "product_id" in df and re.search(r"\b(each|every) products?\b", q) and not re.search(r"\b(total|overall|by class|by manufacturer)\b", q):
        work = df.copy()
        work["_unit_cost"] = pd.to_numeric(work[cost_col], errors="coerce")
        work["_unit_mrp"] = pd.to_numeric(work[mrp_col], errors="coerce")
        grouped_margin = work.groupby("product_id", dropna=True).agg(unit_cost=("_unit_cost", "mean"), mrp=("_unit_mrp", "mean"))
        grouped_margin["margin_per_unit"] = grouped_margin.mrp - grouped_margin.unit_cost
        grouped_margin["margin_pct_of_mrp"] = grouped_margin.margin_per_unit.div(grouped_margin.mrp.where(grouped_margin.mrp.ne(0))).mul(100)
        lines = "; ".join(f"{name}: cost {row.unit_cost:,.2f}{currency_suffix}, MRP {row.mrp:,.2f}{currency_suffix}, margin {row.margin_per_unit:,.2f}{currency_suffix} ({row.margin_pct_of_mrp:.1f}% of MRP)" for name, row in grouped_margin.iterrows())
        return {"answer": "Estimated per-unit margin by product (MRP minus unit cost): " + lines + ".", "values": {"products": grouped_margin.reset_index().to_dict("records")}, "source_rows": row_ids.loc[work.index].dropna().tolist()}

    if time_grain == "day" and q_average and q_revenue and metric_col and re.search(r"\b(daily|per day|each day)\b", q):
        daily = pd.to_numeric(df[metric_col], errors="coerce").groupby(pd.to_datetime(df.date, errors="coerce").dt.strftime("%Y-%m-%d")).sum().dropna()
        if not daily.empty:
            value = float(daily.mean())
            return {"answer": f"Average daily sales revenue: {value:,.2f}{currency_suffix} across {len(daily):,} dates.", "values": {"average_daily_sales": value, "days": len(daily)}, "source_rows": row_ids.loc[df.index].dropna().tolist()}

    # Questions combining sales volume and margin/revenue need both measures;
    # collapsing them to whichever keyword appears first loses the intent.
    composite = bool("product_id" in df and qty_col and ("amount" in df or metric_col) and cost_col and mrp_col and re.search(r"\b(both|also|and|but|while|with)\b", q) and re.search(r"\b(quantity|volume|selling|sales)\b", q) and re.search(r"\b(revenue|margins?|profits?)\b", q))
    if composite:
        work = df.copy()
        work["_qty"] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0)
        work["_revenue"] = pd.to_numeric(work[metric_col], errors="coerce").fillna(0)
        work["_unit_margin"] = pd.to_numeric(work[mrp_col], errors="coerce") - pd.to_numeric(work[cost_col], errors="coerce")
        grouped = work.groupby("product_id", dropna=True).agg(units=("_qty", "sum"), revenue=("_revenue", "sum"), unit_margin=("_unit_margin", "mean"))
        if re.search(r"\blow\w* margins?\b|\brelatively low margins?\b", q):
            grouped = grouped[grouped.unit_margin <= grouped.unit_margin.quantile(.5)]
        if re.search(r"\bstrong margins?\b", q):
            grouped = grouped[grouped.unit_margin >= grouped.unit_margin.quantile(.5)]
        selected = grouped.sort_values(["units", "revenue"], ascending=False).head(10)
        details = "; ".join(f"{name}: {int(row.units):,} units, sales {row.revenue:,.2f}{currency_suffix}, estimated margin/unit {row.unit_margin:,.2f}{currency_suffix}" for name, row in selected.iterrows())
        chosen = work[work.product_id.isin(selected.index)]
        return {"answer": "Product performance across quantity, revenue, and estimated margin: " + details + ".", "values": {"products": selected.reset_index().to_dict("records")}, "source_rows": row_ids.loc[chosen.index].dropna().tolist()}

    if not dimensions and re.search(r"\b(highest|lowest)\b", q) and re.search(r"\btransactions?\b", q) and metric_col:
        amounts = pd.to_numeric(df[metric_col], errors="coerce")
        chosen = df.loc[amounts.nlargest(5).index if "highest" in q else amounts.nsmallest(5).index]
        id_name = id_col or "transaction_id"
        details = []
        for _, row in chosen.iterrows():
            label = str(row.get(id_name, row.get("source_row", "record")))
            if "date" in chosen and pd.notna(row.get("date")):
                label += f" on {row['date']}"
            details.append(f"{label}: {float(row[metric_col]):,.2f}{currency_suffix}")
        return {"answer": "Transactions by value: " + "; ".join(details) + ".", "values": {"transactions": details}, "source_rows": row_ids.loc[chosen.index].dropna().tolist()}

    if q_percentage and not q_quantity and re.search(r"\btransactions?\b", q):
        measure_label, aggregate = "transactions", "nunique" if id_col else "size"
    elif count_products_by_manufacturer and "product_id" in df:
        df = df.copy(); df["_metric"] = df["product_id"]
        measure_label, aggregate = "distinct products", "nunique"
    elif q_margin and cost_col and mrp_col:
        df = df.copy()
        df["_metric"] = pd.to_numeric(df[mrp_col], errors="coerce") - pd.to_numeric(df[cost_col], errors="coerce")
        measure_label = "estimated gross margin per unit (MRP minus unit cost)"
        aggregate = "mean"
        if re.search(r"\b(total|gross margin|gross profit)\b", q) and re.search(r"\b(class|manufacturer|branch|product)\b", q):
            df["_metric"] = df["_metric"] * pd.to_numeric(df[qty_col], errors="coerce").fillna(0) if qty_col else df["_metric"]
            measure_label = "estimated gross margin value (MRP minus unit cost, multiplied by units sold)"
            aggregate = "sum"
    elif q_mrp and mrp_col:
        df = df.copy()
        df["_metric"] = pd.to_numeric(df[mrp_col], errors="coerce")
        measure_label = "average maximum retail price" if q_average else ("maximum retail price" if re.search(r"\b(highest|lowest|maximum|minimum)\b", q) else "average maximum retail price")
        aggregate = "mean" if q_average or ("average" in q and "price" in q) else ("min" if re.search(r"\b(lowest|minimum)\b", q) else "max" if re.search(r"\b(highest|maximum)\b", q) else "mean")
    elif q_cost and cost_col:
        df = df.copy(); df["_metric"] = pd.to_numeric(df[cost_col], errors="coerce")
        measure_label, aggregate = "unit cost", "mean"
    elif q_quantity and qty_col:
        df = df.copy(); df["_metric"] = pd.to_numeric(df[qty_col], errors="coerce")
        measure_label, aggregate = "units sold", "sum"
    elif (q_count or q_frequency) and re.search(r"\b(transactions?|visits?|sales?|often|frequent(?:ly)?|commonly|appears)\b", q) and not q_quantity and not q_average and (not q_revenue or (q_count and re.search(r"\b(how many|number of|count)\b", q) and not re.search(r"\b(revenue|amount|transaction value|sales value|total sales|total revenue)\b", q))):
        measure_label, aggregate = "transactions", "nunique" if id_col else "size"
    elif q_average and metric_col:
        df = df.copy(); df["_metric"] = pd.to_numeric(df[metric_col], errors="coerce")
        measure_label, aggregate = "average transaction value", "mean"
    elif q_revenue and metric_col:
        df = df.copy(); df["_metric"] = pd.to_numeric(df[metric_col], errors="coerce")
        measure_label, aggregate = "sales revenue", "sum"
    elif q_count and re.search(r"\b(product|medicine|drug|manufacturer|company|doctor)\b", q):
        measure_label, aggregate = "distinct records", "nunique"
    elif qty_col:
        df = df.copy(); df["_metric"] = pd.to_numeric(df[qty_col], errors="coerce")
        measure_label, aggregate = "units sold", "sum"
    elif metric_col:
        df = df.copy(); df["_metric"] = pd.to_numeric(df[metric_col], errors="coerce")
        measure_label, aggregate = "sales revenue", "sum"
    else:
        return None

    # Include original numeric columns for derived margin and transaction count.
    entity_cols = [c for c, _ in dimensions]
    unique_measure = "product_id" if measure_label == "distinct products" else next((c for c in ("product_id", "product_code", "doctor_name", "manufacturer") if c in df), None)
    if not entity_cols:
        if aggregate == "nunique":
            count_col = id_col if measure_label == "transactions" and id_col else unique_measure
            if not count_col:
                return None
            value = int(df[count_col].nunique())
        elif aggregate == "size":
            value = int(len(df))
        else:
            nums = pd.to_numeric(df["_metric"], errors="coerce").dropna()
            if nums.empty:
                return None
            if time_grain and aggregate == "mean":
                daily = pd.to_numeric(df["_metric"], errors="coerce").groupby(df["_time_group"]).sum()
                value = float(daily.mean())
                measure_label = f"average {time_grain} sales revenue"
            else:
                value = float(getattr(nums, aggregate)())
        if q_percentage:
            value = float(value) / max(1, source_transaction_count) * 100
            measure_label = "share of transactions"
        answer = f"{measure_label.title()}: {value:,.2f}{'%' if q_percentage else money_suffix(measure_label)}."
        return {"answer": answer, "values": {"measure": measure_label, "value": value, "denominator_transactions": source_transaction_count if q_percentage else None}, "source_rows": row_ids.loc[df.index].dropna().tolist()}

    if aggregate == "nunique":
        count_col = id_col if measure_label == "transactions" and id_col else unique_measure
        if not count_col:
            return None
        grouped = df.groupby(entity_cols, dropna=True)[count_col].nunique()
    elif aggregate == "size":
        grouped = df.groupby(entity_cols, dropna=True).size()
    else:
        grouped = df.groupby(entity_cols, dropna=True)["_metric"].agg(aggregate).dropna()
    if grouped.empty:
        return None
    result = grouped.rename("value").reset_index()
    if q_percentage and measure_label == "transactions":
        result["value"] = result["value"].astype(float).div(max(1, source_transaction_count)).mul(100)
        measure_label = "share of transactions"

    # Two-dimensional questions (e.g. best class at each branch) retain the
    # requested parent dimension and rank children within that parent.
    nested = len(entity_cols) >= 2 and re.search(r"\b(each|per|at each|for each|by each)\b", q)
    ascending = bool(re.search(r"\b(lowest|fewest|least|smallest|bottom|slow[- ]moving|declining|low sales|not selling|not sold)\b", q))
    top_match = re.search(r"\btop\s+(\d+|one|two|three|four|five|ten)\b", q)
    word_numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
    top_n = int(top_match.group(1)) if top_match and top_match.group(1).isdigit() else (word_numbers.get(top_match.group(1), 5) if top_match else 5)
    if nested:
        parent_pos = re.search(r"\b(?:at each|for each|by each|per|each)\s+(?:pharmacy\s+)?(branch(?:es)?|manufacturers?|therapeutic class(?:es)?|classes|doctors?|prescribing doctors?|racks?|products?)\b", q)
        label_alias = {"branch": "branch", "branches": "branch", "manufacturer": "manufacturer", "manufacturers": "manufacturer", "class": "category", "classes": "category", "therapeutic class": "category", "therapeutic classes": "category", "doctor": "doctor_name", "doctors": "doctor_name", "prescribing doctor": "doctor_name", "prescribing doctors": "doctor_name", "rack": "rack_location", "racks": "rack_location", "product": "product_id", "products": "product_id"}
        parent_label = label_alias.get(parent_pos.group(1) if parent_pos else "")
        parent = next((c for c, label in dimensions if label == parent_label), entity_cols[0])
        child = next(c for c in entity_cols if c != parent)
        dimensions = [(parent, next(label for col,label in dimensions if col == parent)), (child, next(label for col,label in dimensions if col == child))]
        grouped_rows = []
        for parent_value, part in result.groupby(parent, sort=True):
            part = part.sort_values("value", ascending=ascending).head(top_n if top_match or "top" in q else 1)
            if len(part) and len(result) > len(part):
                boundary = part["value"].iloc[-1]
                all_part = result[result[parent].eq(parent_value)]
                part = all_part[all_part["value"].le(boundary) if ascending else all_part["value"].ge(boundary)].sort_values("value", ascending=ascending)
            grouped_rows.append(f"{parent_value}: " + ", ".join(f"{r[child]} ({float(r['value']):,.2f}{money_suffix(measure_label)})" for _, r in part.iterrows()))
        selected = result
        answer = f"{measure_label.title()} by {dimensions[0][1]} and {dimensions[1][1]}: " + "; ".join(grouped_rows)
        value_payload = result.to_dict("records")
    elif entity_cols:
        total_group_count = len(result)
        truncated_groups = False
        result = result.sort_values("value", ascending=ascending, kind="stable")
        rank_query = bool(top_match or re.search(r"\b(highest|lowest|most|fewest|least|smallest|largest|best|worst|top|bottom)\b", q))
        if rank_query:
            plural_group = bool(re.search(r"\b(products|medicines|manufacturers|classes|branches|doctors)\b", q))
            limit = top_n if top_match else (5 if plural_group and (ascending or re.search(r"\b(top|highest|lowest|most|fewest|least|largest|smallest)\b", q)) else 1)
            chosen = result.head(limit)
            tie_count = 0
            if len(chosen):
                boundary = chosen["value"].iloc[-1]
                ties = result[result["value"].eq(boundary)]
                tie_count = len(ties)
                # Preserve all tied results at the cutoff when manageable.
                # Very large tie sets (e.g. per-batch transaction values) are
                # summarized with examples so answers remain usable.
                if tie_count <= 20:
                    result = result[result["value"].le(boundary) if ascending else result["value"].ge(boundary)]
                else:
                    result = chosen
            else:
                result = chosen
        else:
            tie_count = 0
        if not rank_query and total_group_count > 100:
            result = result.sort_values("value", ascending=False, kind="stable").head(30)
            truncated_groups = True
        selected = result
        label = dimensions[-1][1]
        answer = f"{measure_label.title()} by {label}: " + "; ".join(f"{r[label] if label in r else r[entity_cols[-1]]}: {float(r['value']):,.2f}{'%' if measure_label == 'share of transactions' else money_suffix(measure_label)}" for _, r in result.iterrows())
        if tie_count > len(result):
            answer += f" ({tie_count:,} results tie at the cutoff; showing {len(result)})."
        if truncated_groups:
            cutoff=result["value"].iloc[-1] if len(result) else None
            cutoff_ties=int(pd.to_numeric(grouped,errors="coerce").eq(cutoff).sum()) if cutoff is not None else 0
            answer += f" Showing 30 of {total_group_count:,} groups sorted by value; {cutoff_ties:,} groups tie at the cutoff. Ask for a specific batch or another filter to narrow the results."
            value_payload = {"results": result.to_dict("records"), "total_groups": total_group_count, "showing": len(result), "cutoff_ties": cutoff_ties, "truncated": True}
        else:
            value_payload = result.to_dict("records")
    else:
        value = float(grouped.iloc[0]) if len(grouped) == 1 else float(grouped.sum())
        selected = df
        answer = f"{measure_label.title()}: {value:,.2f}{money_suffix(measure_label)}."
        value_payload = {measure_label.replace(" ", "_"): value}

    selected_rows = selected
    if entity_cols:
        mask = pd.Series(False, index=df.index)
        for _, r in selected.iterrows():
            current = pd.Series(True, index=df.index)
            for col in entity_cols:
                current &= df[col].astype(str).eq(str(r[col]))
            mask |= current
        selected_rows = df.loc[mask]
    elif aggregate == "nunique" and id_col:
        selected_rows = df
    refs = row_ids.loc[selected_rows.index].dropna().tolist()
    return {"answer": answer, "values": {"measure": measure_label, "results": value_payload}, "source_rows": refs}


def answer_product_catalog_question(question: str, frame):
    """Deterministic, source-row-grounded answers for product price catalogs."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if not {"product_id", "original_price", "discounted_price"}.issubset(frame.columns):
        return None

    q = question.casefold()
    df = frame.copy()
    # A few XLSX exports retain trailing/embedded blank lines as rows. They
    # are source artifacts, not product records; ignore them for catalog
    # counts and denominators while retaining original source_row citations.
    df = df[df["product_id"].notna() & df["product_id"].astype(str).str.strip().ne("")].copy()
    if df.empty:
        return None
    source_rows = df["source_row"] if "source_row" in df else pd.Series(df.index + 1, index=df.index)
    all_source_rows = source_rows
    product_col = "product_id"
    company_col = next((c for c in ("manufacturer", "company", "supplier_name") if c in df), None)
    availability_col = "availability" if "availability" in df else ("status" if "status" in df else None)
    discount_col = "discount_pct" if "discount_pct" in df else None
    before_col, after_col = "original_price", "discounted_price"
    before = pd.to_numeric(df[before_col], errors="coerce")
    after = pd.to_numeric(df[after_col], errors="coerce")
    discount = pd.to_numeric(df[discount_col], errors="coerce") if discount_col else pd.Series(float("nan"), index=df.index)
    df["_catalog_before"] = before
    df["_catalog_after"] = after
    df["_catalog_savings"] = before - after
    df["_catalog_discount"] = discount

    # Resolve only explicitly named product/company values. A vague "this
    # medicine" with no prior referent is a clarification request.
    named_product = next((str(v) for v in sorted(df[product_col].dropna().unique(), key=lambda v: len(str(v)), reverse=True)
                          if len(str(v).strip()) > 2 and str(v).casefold() in q), None)
    fuzzy_product = None
    if not named_product:
        fuzzy_product = _fuzzy_catalog_product(q, df[product_col].dropna().unique())
        if fuzzy_product and "ambiguous" in fuzzy_product:
            return {"answer": "I found several similar catalog names: " + ", ".join(fuzzy_product["ambiguous"]) + ". Which product do you mean?", "values": {"status": "ambiguous_product_name", "candidates": fuzzy_product["ambiguous"]}, "source_rows": []}
        if fuzzy_product:
            named_product = fuzzy_product["name"]
    if not named_product and re.search(r"\b(this|that|same|it)\s+(?:medicine|product|item)\b", q):
        return {"answer": "Which medicine or product do you mean? Please provide its name so I can check the matching catalog records.", "values": {"status": "needs_product_context"}, "source_rows": []}
    if re.search(r"\b(?:a specific|some)\s+company\b", q) and not any(
        str(v).casefold() in q for v in frame.get("manufacturer", pd.Series(dtype=str)).dropna().astype(str).unique()
    ):
        return {"answer": "Which pharmaceutical company should I use? Please provide its name so I can filter the catalog.", "values": {"status": "needs_company_context"}, "source_rows": []}
    product_comparison = bool(re.search(r"\b(compare|versus|\bvs\b|difference between)\b", q) and re.search(r"\b(price|prices|cost|product|medicine)\b", q))
    company_comparison = bool(product_comparison and re.search(r"\b(company|companies|manufacturer|manufacturers)\b", q))
    if product_comparison and not company_comparison:
        mentioned_products = [
            str(name) for name in sorted(df[product_col].dropna().unique(), key=lambda value: len(str(value)), reverse=True)
            if len(str(name).strip()) > 2 and str(name).casefold() in q
        ]
        mentioned_products = list(dict.fromkeys(mentioned_products))
        if len(mentioned_products) >= 2:
            comparison_parts = []
            comparison_rows = []
            comparison_company_col = next((c for c in ("manufacturer", "company", "supplier_name") if c in df), None)
            for name in mentioned_products[:2]:
                part = df[df[product_col].astype(str).str.casefold().eq(name.casefold())]
                distinct = part.drop_duplicates(subset=[c for c in (product_col, comparison_company_col, "pack_size", before_col, after_col, availability_col) if c and c in part])
                entries = []
                for _, row in distinct.head(10).iterrows():
                    details = []
                    if "pack_size" in row and pd.notna(row.get("pack_size")): details.append(f"pack {row['pack_size']}")
                    if pd.notna(row.get(before_col)): details.append(f"original PKR {float(row[before_col]):,.2f}")
                    if pd.notna(row.get(after_col)): details.append(f"discounted PKR {float(row[after_col]):,.2f}")
                    if availability_col and pd.notna(row.get(availability_col)): details.append(str(row[availability_col]))
                    entries.append("; ".join(details))
                comparison_parts.append(f"{name}: " + " | ".join(entries))
                comparison_rows.append(part)
            compared = pd.concat(comparison_rows).drop_duplicates()
            return {"answer": "Catalog comparison: " + " || ".join(comparison_parts), "values": {"products": comparison_parts}, "source_rows": source_rows.loc[compared.index].tolist()}
    if named_product:
        df = df[df[product_col].astype(str).str.casefold() == named_product.casefold()]

    company_name = None
    if company_col:
        compare_requested = bool(re.search(r"\b(compare|versus|\bvs\b|difference between)\b", q) and re.search(r"\b(companies|company|manufacturers|manufacturer)\b", q))
        mentioned_companies=[str(v) for v in frame[company_col].dropna().unique() if len(str(v))>3 and str(v).casefold() in q]
        if compare_requested:
            if len(mentioned_companies)<2:
                return {"answer":"Which two pharmaceutical companies should I compare? Please provide both names.","values":{"status":"needs_comparison_entities"},"source_rows":[]}
            selected=[]; lines=[]
            for name in mentioned_companies[:2]:
                part=frame[frame[company_col].astype(str).str.casefold()==name.casefold()]
                prices=pd.to_numeric(part[after_col],errors="coerce").dropna()
                lines.append(f"{name}: {len(part):,} records, average discounted price PKR {prices.mean():,.2f}")
                selected.append(part)
            combined=pd.concat(selected).drop_duplicates()
            return {"answer":"; ".join(lines),"values":{"comparison":lines},"source_rows":source_rows.loc[combined.index].tolist()}
        company_name = next((str(v) for v in sorted(frame[company_col].dropna().unique(), key=lambda v: len(str(v)), reverse=True)
                             if len(str(v).strip()) > 3 and str(v).casefold() in q), None)
        if company_name:
            df = df[df[company_col].astype(str).str.casefold() == company_name.casefold()]
        elif re.search(r"\b(this|that|same)\s+company\b", q) and not named_product:
            return {"answer": "Which pharmaceutical company do you mean? Please provide its name.", "values": {"status": "needs_company_context"}, "source_rows": []}

    requested_strength = re.search(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|g|ml|iu)\b", q, re.I)
    if requested_strength and named_product:
        strength_text = re.sub(r"\s+", "", requested_strength.group(0)).casefold().replace("µ", "u")
        strength_fields = [c for c in ("strength", "dosage_strength", "product_strength") if c in df.columns]
        row_text = df[product_col].fillna("").astype(str).str.casefold().str.replace(r"\s+", "", regex=True).str.replace("µ", "u", regex=False)
        strength_supported = row_text.str.contains(re.escape(strength_text), regex=True)
        for field in strength_fields:
            strength_supported |= df[field].fillna("").astype(str).str.casefold().str.replace(r"\s+", "", regex=True).str.contains(re.escape(strength_text), regex=True)
        if not strength_supported.any():
            return {
                "answer": f"The selected catalog has records for {named_product}, but it does not identify a {requested_strength.group(0)} strength. I can't confirm a strength-specific price or availability from this data.",
                "values": {"status": "unsupported_strength", "product": named_product, "requested_strength": requested_strength.group(0)},
                "source_rows": source_rows.loc[df.index].tolist(),
            }

    company_aggregate = bool(company_col and re.search(r"\b(company|companies|manufacturer|manufacturers)\b",q) and not company_name)
    availability_aggregate = bool(re.search(r"\b(percentage|proportion|percent|rate)\b", q) and re.search(r"\bavailable|availability\b", q))
    if availability_col and not (company_aggregate and re.search(r"\b(available|availability)\b",q)) and not availability_aggregate:
        avail = df[availability_col].fillna("").astype(str).str.strip().str.casefold()
        if re.search(r"\b(sold\s*out|unavailable|not\s+available|out of stock)\b", q):
            df = df[avail.isin({"sold out", "unavailable", "not available", "out of stock"})]
        elif re.search(r"\bavailable\b", q):
            df = df[_catalog_available_mask(df[availability_col])]
    discount_aggregate = bool(company_aggregate and re.search(r"\b(discount|discounted)\b",q) and re.search(r"\b(percentage|proportion|percent|most|highest|company|companies)\b",q))
    if discount_col and re.search(r"\b(no|without|zero|0%)\s+discount\b|\bwithout any discount\b", q):
        df = df[df["_catalog_discount"].fillna(0).le(0)]
    elif discount_col and not discount_aggregate and re.search(r"\b(discount(?:ed)?|offer|saving)\b", q) and re.search(r"\b(with|have|has|having|discounted|biggest|largest|highest|best)\b", q):
        df = df[df["_catalog_discount"].fillna(0).gt(0)]
        pct_match = re.search(r"\b(\d+(?:\.\d+)?)\s*%", q)
        if pct_match:
            df = df[df["_catalog_discount"].eq(float(pct_match.group(1)))]

    # Price basis: "original/list/before" means the listed price; otherwise
    # answer against the current after-discount price and name that basis.
    original_basis = bool(re.search(r"\b(original|list|before discount|price before)\b", q))
    price_col = "_catalog_before" if original_basis else "_catalog_after"
    price_label = "original price" if original_basis else "discounted price"
    range_match = re.search(r"\bbetween\s+(?:pkr\s*)?([\d,]+)\s+and\s+(?:pkr\s*)?([\d,]+)", q)
    lower_match = re.search(r"\b(?:less than|under|below|cheaper than)\s+(?:pkr\s*)?([\d,]+)", q)
    upper_match = re.search(r"\b(?:more than|over|above|cost(?:ing)? more than|greater than)\s+(?:pkr\s*)?([\d,]+)", q)
    if range_match:
        lo, hi = (float(x.replace(",", "")) for x in range_match.groups())
        df = df[df[price_col].between(lo, hi, inclusive="both")]
    elif lower_match:
        df = df[df[price_col].lt(float(lower_match.group(1).replace(",", "")))]
    elif upper_match:
        df = df[df[price_col].gt(float(upper_match.group(1).replace(",", "")))]

    # Relative wording has no universal pharmacy price cutoff. For this
    # catalog, interpret "expensive/high-priced" as above its mean discounted
    # price and state that operational definition in the response.
    relative_expensive = bool(re.search(r"\b(expensive|high[- ]priced|high cost)\b", q) and not re.search(r"\b(most expensive|highest priced|maximum price|highest price)\b", q))
    relative_cheap = bool(re.search(r"\b(inexpensive|affordable|cheap|cheaper|less expensive)\b", q) and not re.search(r"\b(company|companies|manufacturer|manufacturers)\b", q))
    if (relative_expensive or relative_cheap) and not (range_match or lower_match or upper_match):
        relative_threshold = float(after.mean())
        if relative_cheap:
            df = df[df[price_col].lt(relative_threshold)]
        else:
            df = df[df[price_col].gt(relative_threshold)]

    pack_match=re.search(r"\b(\d+\s*x\s*\d+(?:['’]s)?)\b",q)
    if pack_match and "pack_size" in df:
        wanted=re.sub(r"[^a-z0-9]","",pack_match.group(1).casefold()).removesuffix("s")
        actual=df.pack_size.fillna("").astype(str).str.casefold().str.replace(r"[^a-z0-9]","",regex=True).str.replace(r"s$","",regex=True)
        df=df[actual.eq(wanted)]

    if not len(df):
        subject = named_product or company_name or "the requested filters"
        return {"answer": f"No matching product records were found for {subject} in the selected data.", "values": {"matched_records": 0}, "source_rows": []}

    def cited_rows(part):
        return source_rows.loc[part.index].tolist()

    def product_lines(part, limit=30, include_prices=True):
        lines=[]
        cols=[c for c in (product_col, company_col, "pack_size", availability_col) if c and c in part]
        display=part.drop_duplicates(subset=[c for c in (product_col, company_col, "pack_size", before_col, after_col, availability_col) if c and c in part])
        for _, row in display.head(limit).iterrows():
            label=str(row[product_col])
            if company_col and pd.notna(row.get(company_col)): label += f" - {row[company_col]}"
            details=[]
            if "pack_size" in row and pd.notna(row.get("pack_size")): details.append(f"pack {row['pack_size']}")
            if availability_col and pd.notna(row.get(availability_col)): details.append(str(row[availability_col]))
            if include_prices:
                if pd.notna(row.get(before_col)): details.append(f"original PKR {float(row[before_col]):,.2f}".rstrip("0").rstrip("."))
                if pd.notna(row.get(after_col)): details.append(f"discounted PKR {float(row[after_col]):,.2f}".rstrip("0").rstrip("."))
                if pd.notna(row.get("_catalog_discount")) and float(row["_catalog_discount"]) > 0: details.append(f"{float(row['_catalog_discount']):g}% discount")
                if pd.notna(row.get("_catalog_savings")) and float(row["_catalog_savings"]) > 0: details.append(f"saves PKR {float(row['_catalog_savings']):,.2f}".rstrip("0").rstrip("."))
            lines.append(label + (" (" + "; ".join(details) + ")" if details else ""))
        if len(display)>limit: lines.append(f"Showing {limit} of {len(display)} matching product records.")
        return lines

    # Named product detail returns every distinct source price point; product
    # labels can repeat for different strengths when the source omits strength.
    if named_product and re.search(r"\b(price|cost|available|availability|company|manufactur|pack|discount|detail|medicine|product)\b",q):
        lines=product_lines(df, limit=20)
        variant_count=len(df.drop_duplicates(subset=[c for c in (product_col,company_col,"pack_size",before_col,after_col,availability_col) if c and c in df]))
        match_note = f"I interpreted the product name as {named_product}. " if fuzzy_product else ""
        return {"answer": match_note + f"Catalog records for {named_product}: {variant_count} distinct price/pack variants across {len(df)} source rows. " + "; ".join(lines), "values": {"matched_records": len(df), "price_pack_variants":variant_count, "product": named_product, "fuzzy_name_match": bool(fuzzy_product)}, "source_rows": cited_rows(df)}

    if company_aggregate and company_col and re.search(r"\b(percentage|proportion|percent)\b",q) and re.search(r"\bavailable|availability\b",q):
        rows=[]
        for name,part in df.groupby(company_col,dropna=True):
            available=int(_catalog_available_mask(part[availability_col]).sum()) if availability_col else 0
            rows.append((str(name),available,len(part),available/max(1,len(part))*100,part))
        rows.sort(key=lambda r:(r[3],r[1]),reverse=True)
        top_n_match=re.search(r"\btop\s+(\d+)\b",q); limit=int(top_n_match.group(1)) if top_n_match else (1 if re.search(r"\b(which|highest|most)\b",q) else 10)
        chosen=rows[:limit]; source=pd.concat([r[4] for r in chosen]) if chosen else df.iloc[0:0]
        return {"answer":"; ".join(f"{name}: {n_avail}/{n_total} rows available ({pct:.2f}%)" for name,n_avail,n_total,pct,_ in chosen),"values":{"company_availability":{r[0]:r[3] for r in chosen}},"source_rows":cited_rows(source)}

    if company_col and discount_col and re.search(r"\b(percentage|proportion|percent)\b",q) and re.search(r"\bdiscount",q):
        rows=[]
        for name,part in df.groupby(company_col,dropna=True):
            discounted=part["_catalog_discount"].fillna(0).gt(0)
            rows.append((str(name),int(discounted.sum()),len(part),float(discounted.mean()*100),part))
        rows.sort(key=lambda r:(r[3],r[1]),reverse=True)
        top_n_match=re.search(r"\btop\s+(\d+)\b",q); limit=int(top_n_match.group(1)) if top_n_match else (1 if re.search(r"\b(which|highest|most)\b",q) else 10)
        chosen=rows[:limit]; source=pd.concat([r[4] for r in chosen]) if chosen else df.iloc[0:0]
        return {"answer":"; ".join(f"{name}: {n_disc}/{n_total} records discounted ({pct:.2f}%)" for name,n_disc,n_total,pct,_ in chosen),"values":{"company_discount_share":{r[0]:r[3] for r in chosen}},"source_rows":cited_rows(source)}

    if re.search(r"\b(average|avg|mean)\s+(?:medicine|product)?\s*price\b",q) and not original_basis and not re.search(r"\b(after discount|discounted)\b",q) and not company_aggregate:
        return {"answer":f"Average original price: PKR {before.mean():,.2f}; average discounted price: PKR {after.mean():,.2f}.","values":{"average_original_price":float(before.mean()),"average_discounted_price":float(after.mean())},"source_rows":cited_rows(df)}

    if re.search(r"\b(percentage|percent)\b",q) and re.search(r"\bavailable|availability\b",q) and availability_col:
        count=int(_catalog_available_mask(df[availability_col]).sum())
        available_names=int(df.loc[_catalog_available_mask(df[availability_col]),product_col].nunique())
        all_names=int(df[product_col].nunique())
        return {"answer":f"{count:,} of {len(df):,} product records are marked purchasable ({count/max(1,len(df))*100:.2f}%); {available_names:,} of {all_names:,} distinct product names have at least one purchasable record ({available_names/max(1,all_names)*100:.2f}%).", "values":{"available_records":count,"available_record_pct":count/max(1,len(df))*100,"available_distinct_products":available_names,"all_distinct_products":all_names,"available_product_pct":available_names/max(1,all_names)*100},"source_rows":all_source_rows.tolist()}

    if discount_col and re.search(r"\b(highest|most|maximum|top)\b",q) and re.search(r"\bdiscount\b",q) and not re.search(r"\b(saving|reduction|amount saved|price cut)\b",q):
        local_discount = df["_catalog_discount"]
        maximum=float(local_discount.max()) if local_discount.notna().any() else 0.0
        tied=df[local_discount.eq(maximum)] if maximum>0 else df.iloc[0:0]
        sample=product_lines(tied,limit=10,include_prices=True)
        return {"answer":f"The highest recorded discount rate is {maximum:g}%, shared by {len(tied):,} records. Examples: "+"; ".join(sample),"values":{"highest_discount_pct":maximum,"records_at_highest":len(tied)},"source_rows":cited_rows(tied)}

    if re.search(r"\b(above|higher than|greater than)\b.{0,40}\baverage\b|\bcompared with (?:the )?(?:dataset'?s )?average\b",q):
        avg=float(after.mean()); above=df[df["_catalog_after"].gt(avg)]
        lines=product_lines(above.sort_values("_catalog_after",ascending=False),limit=30,include_prices=True)
        return {"answer":f"Dataset average discounted price is PKR {avg:,.2f}; {len(above):,} records are above it. "+"; ".join(lines),"values":{"average_discounted_price":avg,"records_above_average":len(above)},"source_rows":cited_rows(above)}

    if re.search(r"\b(top\s+\d+\s+products?\s+by\s+discount|top\s+products?\s+by\s+discount)\b",q) and discount_col:
        pct_values=sorted(discount.dropna().unique(),reverse=True)
        if len(pct_values)==1:
            top_n_match=re.search(r"\btop\s+(\d+)\b",q); limit=int(top_n_match.group(1)) if top_n_match else 10
            selected=df[discount.eq(pct_values[0])].sort_values("_catalog_savings",ascending=False).head(limit)
            lines=product_lines(selected,limit=limit,include_prices=True)
            return {"answer":f"All products with a stated discount are tied at {pct_values[0]:g}%; here are the {len(selected)} highest monetary savings among that tie: "+"; ".join(lines),"values":{"discount_rate_tie":pct_values[0],"products":lines},"source_rows":cited_rows(selected)}

    if re.search(r"\b(discount|discounted)\b",q) and re.search(r"\b(average|avg|mean|percentage|percent|how many|count|number of)\b",q) and not re.search(r"\b(price|cost)\b",q) and not company_aggregate:
        count=int(discount.gt(0).sum()); unique=int(df.loc[discount.gt(0),product_col].nunique())
        if re.search(r"\b(average|avg|mean)\b",q):
            weighted=float(discount.fillna(0).mean()); present=discount[discount.gt(0)]
            return {"answer":f"Average discount is {weighted:.2f}% when records without a discount count as 0%; the mean among {len(present):,} records with a stated discount is {present.mean():.2f}%.","values":{"average_discount_pct_all":weighted,"average_discount_pct_discounted":float(present.mean())},"source_rows":all_source_rows.tolist()}
        return {"answer":f"{count:,} source records ({unique:,} distinct product names) have a recorded discount ({count/max(1,len(df))*100:.2f}% of nonblank product records).","values":{"discounted_records":count,"distinct_products":unique,"percentage":count/max(1,len(df))*100},"source_rows":source_rows.loc[df.index[discount.gt(0)]].tolist()}

    if "pack_size" in df and re.search(r"\b(pack sizes?|pack options?)\b",q) and not named_product:
        counts=df.groupby(product_col).pack_size.nunique(dropna=True)
        selected_names=counts[counts>1].index
        if re.search(r"\b(multiple|different|another|more than one)\b",q):
            part=df[df[product_col].isin(selected_names)]
            lines=[f"{name}: {', '.join(sorted(set(part.loc[part[product_col].eq(name),'pack_size'].dropna().astype(str))))}" for name in selected_names[:30]]
            return {"answer":f"{len(selected_names)} product names have multiple pack sizes: "+"; ".join(lines),"values":{"products_with_multiple_packs":int(len(selected_names))},"source_rows":cited_rows(part)}

    if re.search(r"\b(price|prices)\b",q) and re.search(r"\b(same|similar|common|frequent)\b",q):
        counts=df.groupby("_catalog_after")[product_col].agg(["size","nunique"]).sort_values(["size","nunique"],ascending=False).head(10)
        lines=[f"PKR {price:,.2f}: {int(r['size'])} records, {int(r['nunique'])} product names" for price,r in counts.iterrows()]
        return {"answer":"Most frequently shared discounted price points: "+"; ".join(lines),"values":{"common_prices":lines},"source_rows":cited_rows(df[df["_catalog_after"].isin(counts.index)])}

    if re.search(r"\b(cheapest|lowest priced|least expensive|most affordable|most expensive|highest priced|maximum price|lowest price|highest price)\b", q) or re.search(r"\b(highest|maximum|max)\s+(?:product|medicine|item)\s+price\b", q):
        ascending = bool(re.search(r"\b(cheapest|lowest|least|affordable)\b", q))
        ranking = df.sort_values(price_col, ascending=ascending, na_position="last")
        nmatch = re.search(r"\btop\s+(\d+)\b|\b(\d+)\s+(?:most expensive|cheapest|least expensive)\b",q)
        ranked = ranking.dropna(subset=[price_col])
        limit=int(next(group for group in nmatch.groups() if group)) if nmatch else 1
        # Include all ties for a singular extremum; otherwise the first row in
        # source order would be presented as uniquely cheapest/most expensive.
        if not nmatch and not ranked.empty:
            extreme = ranked.iloc[0][price_col]
            selected = ranked[ranked[price_col].eq(extreme)]
        else:
            selected = ranked.head(limit)
        lines=[f"{r[product_col]}: PKR {float(r[price_col]):,.2f}".rstrip("0").rstrip(".") for _,r in selected.iterrows()]
        qualifier = f" ({len(selected)} tied records)" if not nmatch and len(selected) > 1 else ""
        return {"answer": f"{('Cheapest' if ascending else 'Most expensive')} by {price_label}{qualifier}: " + "; ".join(lines), "values": {"ranked_products": lines, "tie_count": len(selected)}, "source_rows": cited_rows(selected)}

    if re.search(r"\b(biggest price reductions?|largest discounts?|biggest savings?|largest savings?|most money (?:saved|save)|largest monetary saving|top products by discount|top products by reduction)\b", q):
        nmatch=re.search(r"\btop\s+(\d+)\b",q); limit=int(nmatch.group(1)) if nmatch else 10
        selected=df.sort_values("_catalog_savings",ascending=False).dropna(subset=["_catalog_savings"]).head(limit)
        lines=[f"{r[product_col]}: saves PKR {float(r['_catalog_savings']):,.2f} (PKR {float(r[before_col]):,.2f} to PKR {float(r[after_col]):,.2f})" for _,r in selected.iterrows()]
        return {"answer":"Products with the largest recorded price reductions: "+"; ".join(lines),"values":{"top_savings":lines},"source_rows":cited_rows(selected)}

    if re.search(r"\b(difference|gap)\b", q) and re.search(r"\b(original|before)\b", q) and re.search(r"\b(discounted|after)\b", q) and not named_product and not re.search(r"\b(large|larger|significant|significantly|substantial)\b", q):
        savings = df["_catalog_savings"].dropna()
        mean_saving = float(savings.mean()) if not savings.empty else 0.0
        total_reduction = float(savings.sum()) if not savings.empty else 0.0
        return {
            "answer": f"Across {len(savings):,} source records, the mean listed reduction from original to discounted price is PKR {mean_saving:,.2f}; the sum of listed reductions is PKR {total_reduction:,.2f}. These are catalog comparisons, not a pharmacy sales/profit calculation.",
            "values": {"mean_listed_reduction": mean_saving, "sum_listed_reductions": total_reduction, "records": len(savings)},
            "source_rows": cited_rows(df),
        }

    if re.search(r"\b(large|larger|significant|significantly|substantial)\b", q) and re.search(r"\b(difference|cheaper after discount|price reduction|discount)\b", q):
        selected = df.sort_values("_catalog_savings", ascending=False).dropna(subset=["_catalog_savings"]).head(10)
        lines = [f"{r[product_col]}: saves PKR {float(r['_catalog_savings']):,.2f} (PKR {float(r[before_col]):,.2f} to PKR {float(r[after_col]):,.2f})" for _, r in selected.iterrows()]
        return {"answer": "Largest listed original-to-discounted price gaps (ranked; 'large' has no stated cutoff): " + "; ".join(lines), "values": {"top_savings": lines, "ranking_only": True}, "source_rows": cited_rows(selected)}

    if re.search(r"\b(outlier|unusually high|unusually low|review.*price|price.*review)\b",q):
        high=df.nlargest(5,"_catalog_after"); low=df.nsmallest(5,"_catalog_after")
        lines=["Highest: "+"; ".join(f"{r[product_col]} PKR {float(r['_catalog_after']):,.2f}" for _,r in high.iterrows()),"Lowest: "+"; ".join(f"{r[product_col]} PKR {float(r['_catalog_after']):,.2f}" for _,r in low.iterrows())]
        selected=pd.concat([high,low]).drop_duplicates()
        return {"answer":"Price points to review (highest and lowest in this dataset): "+". ".join(lines),"values":{"highest":lines[0],"lowest":lines[1]},"source_rows":cited_rows(selected)}

    # A catalog can contain multiple rows for one brand/pack or price point.
    # Return both row count and distinct product count to make that unit clear.
    if re.search(r"\b(how many|count|number of)\b", q):
        if re.search(r"\b(companies|manufacturers|pharmaceutical companies)\b", q) and company_col:
            count=int(df[company_col].dropna().astype(str).nunique())
            return {"answer":f"{count:,} distinct pharmaceutical companies in the selected records.","values":{"company_count":count,"matching_records":len(df)},"source_rows":cited_rows(df)}
        products=int(df[product_col].dropna().astype(str).nunique())
        unit="distinct product names" if re.search(r"\b(products?|medicines?)\b",q) else "matching source records"
        return {"answer": f"{products:,} {unit} across {len(df):,} matching source records.", "values": {"matching_records":len(df),"distinct_products":products}, "source_rows": cited_rows(df)}

    if re.search(r"\b(compare|versus|\bvs\b|difference between)\b",q) and company_col and not company_name and re.search(r"\b(compan(?:y|ies)|manufacturer|manufacturers)\b",q):
        mentioned=[str(v) for v in frame[company_col].dropna().unique() if len(str(v))>3 and str(v).casefold() in q]
        if len(mentioned)<2:
            return {"answer":"Which two pharmaceutical companies should I compare? Please provide both names.","values":{"status":"needs_comparison_entities"},"source_rows":[]}

    # Dataset-level catalog measures and company summaries.
    if re.search(r"\b(average|avg|mean|median|summari[sz]e|overview|patterns?)\b",q):
        relevant=df
        rows=[]
        if re.search(r"\b(median)\b",q):
            rows.append(f"Median original price: PKR {relevant['_catalog_before'].median():,.2f}".rstrip("0").rstrip("."))
            rows.append(f"Median discounted price: PKR {relevant['_catalog_after'].median():,.2f}".rstrip("0").rstrip("."))
        elif re.search(r"\b(discount|discount rate|discount percentage)\b",q) and not re.search(r"\b(price|cost|saving)\b",q):
            present=relevant[relevant["_catalog_discount"].gt(0)]["_catalog_discount"]
            weighted=relevant["_catalog_discount"].fillna(0).mean()
            rows.append(f"Mean discount including records without a discount as 0%: {weighted:.2f}%".rstrip("0").rstrip("."))
            if len(present): rows.append(f"Mean among records with a stated discount: {present.mean():.2f}%".rstrip("0").rstrip("."))
        elif re.search(r"\b(company|manufacturer|each company|by company)\b",q) and company_col:
            grouped=relevant.groupby(company_col,dropna=True)["_catalog_after"].mean().sort_values(ascending=False)
            if re.search(r"\b(lowest|cheapest|least)\b",q): grouped=grouped.sort_values(ascending=True)
            top_n_match=re.search(r"\btop\s+(\d+)\b",q); limit=int(top_n_match.group(1)) if top_n_match else (len(grouped) if re.search(r"\beach\b",q) else (1 if re.search(r"\b(which|highest|lowest|most|least)\b",q) else 10))
            rows=[f"{name}: PKR {value:,.2f}" for name,value in grouped.head(limit).items()]
        else:
            col="_catalog_before" if original_basis or "original" in q else "_catalog_after"
            rows.append(f"Average {('original' if col=='_catalog_before' else 'discounted')} product price: PKR {relevant[col].mean():,.2f}".rstrip("0").rstrip("."))
            if re.search(r"\b(summarize|summary|overview|patterns?)\b",q):
                total=len(relevant); avail_count=int(_catalog_available_mask(relevant[availability_col]).sum()) if availability_col else None
                discounted=int(relevant["_catalog_discount"].fillna(0).gt(0).sum()) if discount_col else None
                rows.extend([f"{relevant[product_col].nunique():,} distinct product names across {total:,} source rows"])
                if avail_count is not None: rows.append(f"{avail_count:,} rows marked Available ({avail_count/max(1,total)*100:.2f}%)")
                if discounted is not None: rows.append(f"{discounted:,} rows with a recorded discount")
        return {"answer": "; ".join(rows), "values": {"summary":rows}, "source_rows": cited_rows(relevant)}

    if company_col and re.search(r"\bboth\b",q) and re.search(r"\b(availability|available)\b",q) and re.search(r"\b(competitive|price|cost)\b",q):
        stats=df.groupby(company_col,dropna=True).agg(
            records=(product_col,"size"), products=(product_col,"nunique"), average_price=("_catalog_after","mean")
        )
        available=df.assign(_is_available=_catalog_available_mask(df[availability_col])).groupby(company_col,dropna=True)["_is_available"].mean().mul(100) if availability_col else pd.Series(0.0,index=stats.index)
        stats["availability_pct"]=available
        overall_availability=float(_catalog_available_mask(df[availability_col]).mean()*100) if availability_col else 0.0
        overall_price=float(after.mean())
        chosen=stats[(stats["availability_pct"]>overall_availability)&(stats["average_price"]<overall_price)].sort_values(["availability_pct","average_price"],ascending=[False,True]).head(10)
        rows=[f"{name}: {r['availability_pct']:.2f}% available, average discounted price PKR {r['average_price']:,.2f} ({int(r['products'])} distinct product names)" for name,r in chosen.iterrows()]
        selected=df[df[company_col].isin(chosen.index)]
        answer=("Using above the catalog-wide availability rate and below the catalog-wide average discounted price as the comparison criteria, qualifying companies: "+"; ".join(rows)) if rows else "No company is both above the catalog-wide availability rate and below the catalog-wide average discounted price."
        return {"answer":answer,"values":{"availability_threshold_pct":overall_availability,"price_threshold":overall_price,"companies":rows},"source_rows":cited_rows(selected)}

    if company_col and re.search(r"\b(most|top|highest|largest|best|competitive|cheaper|cheapest|availability)\b",q) and re.search(r"\b(companies|company|manufacturers|manufacturer|variety|available)\b",q):
        grouped=df.groupby(company_col,dropna=True).agg(
            records=(product_col,"size"), products=(product_col,"nunique"), average_price=("_catalog_after","mean")
        )
        if availability_col:
            availability=df.assign(_is_available=_catalog_available_mask(df[availability_col])).groupby(company_col,dropna=True)["_is_available"].agg(["sum","count"])
            grouped["available_rows"]=availability["sum"]
            grouped["availability_pct"]=availability["sum"].div(availability["count"]).mul(100)
            available_products=df.loc[_catalog_available_mask(df[availability_col])].groupby(company_col,dropna=True)[product_col].nunique()
            grouped["available_products"]=available_products.reindex(grouped.index,fill_value=0)
        if discount_col:
            discounted_products=df.loc[df["_catalog_discount"].fillna(0).gt(0)].groupby(company_col,dropna=True)[product_col].nunique()
            grouped["discounted_products"]=discounted_products.reindex(grouped.index,fill_value=0)
        if re.search(r"\b(variety|most products|largest variety)\b",q):
            grouped=grouped.sort_values(["products","records"],ascending=[False,False])
        elif re.search(r"\bmost discounted products?\b",q) and "discounted_products" in grouped:
            grouped=grouped.sort_values(["discounted_products","records"],ascending=[False,False])
        elif re.search(r"\b(most available|greatest number of available)\b",q) and not re.search(r"\b(proportion|percentage|percent|availability rate)\b",q):
            grouped=grouped.sort_values(["available_products","available_rows"],ascending=[False,False])
        elif re.search(r"\b(cheaper|cheapest|competitive|lowest average)\b",q):
            grouped=grouped.sort_values(["average_price"],ascending=True)
        elif "availability" in q or "available" in q:
            grouped=grouped.sort_values(["availability_pct","available_rows"],ascending=[False,False])
        else:
            grouped=grouped.sort_values(["records","products"],ascending=[False,False])
        top_n_match=re.search(r"\btop\s+(\d+)\b",q); limit=int(top_n_match.group(1)) if top_n_match else (1 if re.search(r"\b(which|most|highest|lowest|cheapest|cheaper)\b",q) else 10)
        rows=[]
        for name,r in grouped.head(limit).iterrows():
            rows.append(f"{name}: {int(r['records'])} source rows, {int(r['products'])} distinct product names, average discounted price PKR {r['average_price']:,.2f}"+(f", {int(r['available_products'])} distinct names available ({int(r['available_rows'])} rows; {r['availability_pct']:.2f}%)" if "available_products" in r else "")+(f", {int(r['discounted_products'])} distinct names with discounts" if "discounted_products" in r else ""))
        used_names=grouped.head(limit).index
        selected=df[df[company_col].isin(used_names)]
        return {"answer":"Company catalog comparison: "+"; ".join(rows),"values":{"companies":rows},"source_rows":cited_rows(selected)}

    # Generic filtered list response.
    if re.search(r"\b(show|list|which|products?|medicines?|available)\b",q):
        lines=product_lines(df,limit=30,include_prices=True)
        relative_note = f"Using PKR {float(after.mean()):,.2f} (catalog mean discounted price) as the threshold for '{'expensive' if relative_expensive else 'affordable'}: " if (relative_expensive or relative_cheap) and not (range_match or lower_match or upper_match) else ""
        return {"answer": relative_note + f"{len(df):,} matching rows ({df[product_col].nunique():,} distinct product names): " + "; ".join(lines), "values":{"matching_records":len(df),"distinct_products":int(df[product_col].nunique()),"relative_price_threshold":float(after.mean()) if relative_expensive or relative_cheap else None},"source_rows":cited_rows(df)}
    return None


def _apply_explicit_date_filters(question: str, frame: pd.DataFrame, filters=None) -> pd.DataFrame:
    """Apply resolved date bounds to dated transaction rows, preserving stock snapshots."""
    if not isinstance(frame, pd.DataFrame) or frame.empty or not filters:
        return frame
    start = pd.to_datetime(filters.get("date_from"), errors="coerce") if filters.get("date_from") else pd.NaT
    end = pd.to_datetime(filters.get("date_to"), errors="coerce") if filters.get("date_to") else pd.NaT
    if pd.isna(start) and pd.isna(end):
        return frame
    date_col = next((name for name in ("date", "transaction_date", "sale_date", "invoice_date", "timestamp", "created_at") if name in frame), None)
    if not date_col:
        return frame
    dates = pd.to_datetime(frame[date_col], errors="coerce").dt.normalize()
    start = start.normalize() if pd.notna(start) else dates.min()
    end = end.normalize() if pd.notna(end) else dates.max()
    in_window = dates.between(start, end, inclusive="both")
    q = re.sub(r"\s+", " ", str(question).casefold())
    wants_purchase = bool(re.search(r"\b(purchas(?:e|es|ed|ing)|bought|vendor payable|supplier payable|owe|owed|payable)\b", q))
    wants_sales = bool(re.search(r"\b(sales?|sold|revenue|transactions?|invoices?|bills?|receipts?|profit|margin|discount|cash|customer)\b", q))
    if wants_purchase and not wants_sales:
        target_pattern = r"purchase|expense|vendor"
    elif wants_sales:
        target_pattern = r"sale|return|refund|invoice|bill|transaction|dispens"
    else:
        target_pattern = r"sale|return|refund|invoice|bill|transaction|dispens|purchase|expense|vendor"

    if "txn_type" in frame and frame["txn_type"].notna().any():
        kind = frame["txn_type"].fillna("").astype(str).str.casefold()
        transaction_rows = kind.str.contains(r"sale|return|refund|invoice|bill|transaction|dispens|purchase|expense|vendor", regex=True)
        targeted_rows = kind.str.contains(target_pattern, regex=True)
        if transaction_rows.any():
            keep = ~targeted_rows | in_window
            return frame.loc[keep].copy()
    if "table_name" in frame:
        table = frame["table_name"].fillna("").astype(str).str.casefold()
        transaction_rows = table.str.contains(r"sale|return|refund|invoice|bill|transaction|dispens|purchase|expense|vendor", regex=True)
        targeted_rows = table.str.contains(target_pattern, regex=True)
        if transaction_rows.any():
            return frame.loc[~targeted_rows | in_window].copy()
    return frame.loc[in_window].copy()


def answer_tabular_question(question: str, frame, filters=None):
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if frame.attrs.get("incomplete_source"):
        return {"answer": "This selected source was indexed before complete structured rows were stored, so I can't safely calculate or verify this from the retrieved sample. Re-ingest the source to enable full-data answers.", "values": {"status": "incomplete_source", "requires_reingestion": True}, "source_rows": []}
    # Use an offline schema-grounded plan before the legacy heuristic cascade.
    # A confident plan executes against the complete frame; a recognized but
    # under-specified request returns a clarification instead of falling into
    # a keyword branch that could compute a different metric.
    from app.analytics.offline_query_intent import plan_and_answer_offline
    planned_result = plan_and_answer_offline(question, frame)
    if planned_result is not None:
        return planned_result
    # Stable IDs are an exact relational lookup. Handle them before catalog
    # matching, filters, or broad aggregates so nearby rows cannot win by
    # semantic similarity and a single sale never becomes a whole-file KPI.
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)
    dynamic_result = _answer_schema_composition(question, frame, row_ids)
    if dynamic_result is not None:
        return dynamic_result
    # Do not turn a requested breakdown into a plausible-looking zero when
    # the selected schema has no populated field for that dimension. Match
    # common connector spellings so the same guard works for SQL, CSV, and
    # spreadsheet headers.
    dimension_specs = (
        ("payment method", r"\bpayment\s*(?:method|type|mode)s?\b", {"paymentmethod", "paymenttype", "paymentmode", "tendertype", "tender", "payment"}),
        ("branch", r"\bbranches?\b", {"branch", "branchname", "pharmacybranch", "location", "warehouse"}),
        ("terminal or register", r"\b(?:terminals?|registers?)\b", {"terminal", "terminalid", "register", "registerid", "posid"}),
        ("cashier", r"\bcashiers?\b", {"cashier", "cashiername", "cashieruser", "attendingpharmacist"}),
        ("doctor", r"\b(?:doctors?|physicians?)\b", {"doctor", "doctorname", "physician", "prescriber"}),
        ("manufacturer", r"\b(?:manufacturers?|companies)\b", {"manufacturer", "company", "companyname"}),
    )
    normalized_columns = {
        re.sub(r"[^a-z0-9]", "", str(column).casefold().removeprefix("_extra.")): column
        for column in frame.columns
    }
    for label, pattern, aliases in dimension_specs:
        if re.search(pattern, q) and re.search(r"\b(by|per|each|most|fewest|highest|largest|rank|count|number of|how many)\b", q):
            available = [normalized_columns[name] for name in aliases if name in normalized_columns and frame[normalized_columns[name]].notna().any()]
            if not available:
                return {
                    "answer": f"I can't calculate this breakdown because the selected records have no populated {label} field.",
                    "values": {"status": "missing_required_field", "requested_dimension": label, "required_field_aliases": sorted(aliases)},
                    "source_rows": [],
                }
    named_quantity_total = _answer_named_product_quantity_total(question, frame)
    if named_quantity_total is not None:
        return named_quantity_total
    customer_total_request = bool(
        re.search(r"\b(total|combined|sum)\b", q)
        and re.search(r"\b(invoice total|sales total|sales amount|revenue|receipts?)\b", q)
    )
    if customer_total_request:
        customer_col = next((col for col in ("customer_name", "_extra.CUSTOMER", "customer", "client_name") if col in frame and frame[col].notna().any()), None)
        amount_col = next((col for col in ("invoice_total", "sales_total", "total_amount", "amount") if col in frame and frame[col].notna().any()), None)
        if customer_col and amount_col:
            known_names = [str(value).strip() for value in frame[customer_col].dropna().unique() if len(str(value).split()) >= 2 and re.search(r"[a-z]", str(value), re.I)]
            matched_name = next((name for name in sorted(known_names, key=len, reverse=True) if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", q)), None)
            if matched_name:
                work = frame.loc[frame[customer_col].astype(str).str.casefold().eq(matched_name.casefold())].copy()
                header_file = next((col for col in ("_header_source_file", "source_file") if col in work and work[col].notna().any()), None)
                header_row = next((col for col in ("_header_source_row", "source_row") if col in work and work[col].notna().any()), None)
                if header_file and header_row:
                    sales_mask = work[header_file].fillna("").astype(str).str.casefold().str.contains(r"(?:^|[/_])sales?(?:[/_]|$)", regex=True)
                    if sales_mask.any():
                        work = work.loc[sales_mask]
                    work = work.loc[work[header_file].notna() & work[header_row].notna()].drop_duplicates([header_file, header_row])
                    citations = list(dict.fromkeys((str(row[header_file]), row[header_row]) for _, row in work.iterrows()))
                else:
                    invoice_col = next((col for col in ("invoice_id", "receipt_id", "transaction_id") if col in work and work[col].notna().any()), None)
                    if invoice_col:
                        work = work.drop_duplicates([invoice_col])
                    citations = row_ids.loc[work.index].tolist()
                total = float(pd.to_numeric(work[amount_col], errors="coerce").fillna(0).sum())
                return {"answer": f"Combined invoice total for {matched_name}: {_format_money(total)}.", "values": {"status": "ok", "customer": matched_name, "total_sales": total, "invoice_count": int(len(work)), "sales_total_field": amount_col}, "source_rows": citations}
    # Count distinct sales headers by an explicitly requested POS terminal or
    # register. Preserve raw connector fields such as _extra.TERMINAL_ID.
    terminal_grouping = bool(
        re.search(r"\b(which|what|list|show|count|how many|number of)\b", q)
        and re.search(r"\b(terminal|register)s?\b", q)
        and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
    )
    if terminal_grouping:
        terminal_col = next((col for col in ("terminal_id", "terminal", "register_id", "register", "_extra.TERMINAL_ID", "_extra.REGISTER_ID") if col in frame and frame[col].notna().any()), None)
        receipt_col = next((col for col in ("invoice_id", "receipt_id", "transaction_id", "order_id") if col in frame and frame[col].notna().any()), None)
        if terminal_col and receipt_col:
            grouped = frame.loc[frame[terminal_col].notna() & frame[receipt_col].notna()].copy()
            source_file_col = next((col for col in ("_header_source_file", "source_file", "file_id") if col in grouped and grouped[col].notna().any()), None)
            if source_file_col:
                sales_mask = grouped[source_file_col].fillna("").astype(str).str.casefold().str.contains(r"(?:^|[/_])sales?(?:[/_]|$)", regex=True)
                if sales_mask.any():
                    grouped = grouped.loc[sales_mask]
            grain_cols = [col for col in ("_header_source_file", "_header_source_row") if col in grouped and grouped[col].notna().any()]
            if len(grain_cols) == 2:
                grouped = grouped.drop_duplicates(subset=grain_cols)
            else:
                grouped = grouped.drop_duplicates(subset=[receipt_col])
            counts = (
                grouped.groupby(terminal_col, dropna=True).size()
                if len(grain_cols) == 2
                else grouped.groupby(terminal_col, dropna=True)[receipt_col].nunique()
            ).sort_values(ascending=False)
            if not counts.empty:
                label = "terminal" if "terminal" in terminal_col.casefold() else "register"
                details = [{label: str(name), "receipts": int(count)} for name, count in counts.items()]
                answer = f"Sales receipts by {label}: " + "; ".join(f"{item[label]}: {item['receipts']}" for item in details) + "."
                if len(grain_cols) == 2:
                    citations = list(dict.fromkeys((str(row[grain_cols[0]]), row[grain_cols[1]]) for _, row in grouped.iterrows()))
                else:
                    citations = row_ids.loc[grouped.index].tolist()
                receipt_count = len(grouped) if len(grain_cols) == 2 else int(grouped[receipt_col].nunique())
                return {"answer": answer, "values": {"status": "ok", f"receipts_by_{label}": details, "receipt_count": receipt_count}, "source_rows": citations}
    if re.search(r"\bhow many\b", q) and re.search(r"\binvoice\s+header\s+records?\b", q):
        header_file_col = "_header_source_file" if "_header_source_file" in frame else "source_file" if "source_file" in frame else None
        header_row_col = "_header_source_row" if "_header_source_row" in frame else "source_row" if "source_row" in frame else None
        header_frame = frame
        if header_file_col and header_row_col:
            file_names = frame[header_file_col].fillna("").astype(str).str.casefold()
            sales_header_mask = file_names.str.contains(r"(?:^|[/_])sales?(?:[/_]|$)", regex=True)
            if sales_header_mask.any():
                header_frame = frame.loc[sales_header_mask]
            header_frame = header_frame.loc[header_frame[header_file_col].notna() & header_frame[header_row_col].notna()].drop_duplicates([header_file_col, header_row_col])
            citations = list(dict.fromkeys((str(row[header_file_col]), row[header_row_col]) for _, row in header_frame.iterrows()))
        else:
            table_col = next((col for col in ("table_name", "source_table") if col in frame), None)
            if table_col:
                header_mask = frame[table_col].fillna("").astype(str).str.contains(r"invoice.*header|header.*invoice", case=False, regex=True)
                if header_mask.any():
                    header_frame = frame.loc[header_mask]
            citations = row_ids.loc[header_frame.index].dropna().tolist()
        return {"answer": f"{len(header_frame):,} invoice header records are present in the selected sales data.", "values": {"status": "ok", "invoice_header_records": int(len(header_frame))}, "source_rows": citations}
    if (re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(?:sales?\s+)?(?:receipt|transaction|invoice|bill)\s+headers?\b", q)
            and re.search(r"\binvoice\s+discount\b", q)
            and re.search(r"\b(greater than zero|above zero|positive|nonzero|more than zero|over zero)\b", q)):
        discount_col = next((col for col in ("invoice_discount", "header_discount") if col in frame and frame[col].notna().any()), None)
        if discount_col:
            work = frame.loc[frame[discount_col].notna()].copy()
            header_file = next((col for col in ("_header_source_file", "source_file") if col in work and work[col].notna().any()), None)
            header_row = next((col for col in ("_header_source_row", "source_row") if col in work and work[col].notna().any()), None)
            if header_file and header_row:
                sales_mask = work[header_file].fillna("").astype(str).str.casefold().str.contains(r"(?:^|[/_])sales?(?:[/_]|$)", regex=True)
                if sales_mask.any():
                    work = work.loc[sales_mask]
                work = work.loc[work[header_file].notna() & work[header_row].notna()].drop_duplicates([header_file, header_row])
                citations = list(dict.fromkeys((str(row[header_file]), row[header_row]) for _, row in work.iterrows()))
            else:
                id_col = next((col for col in ("invoice_id", "receipt_id", "transaction_id", "bill_no") if col in work and work[col].notna().any()), None)
                if id_col:
                    work = work.drop_duplicates([id_col])
                citations = row_ids.loc[work.index].tolist()
            values = pd.to_numeric(work[discount_col], errors="coerce")
            positive = work.loc[values.gt(0)]
            if header_file and header_row:
                citations = list(dict.fromkeys((str(row[header_file]), row[header_row]) for _, row in positive.iterrows()))
            else:
                citations = row_ids.loc[positive.index].tolist()
            count = int(len(positive))
            return {"answer": f"{count:,} sales receipt headers have an invoice discount greater than zero.", "values": {"status": "ok", "sales_headers_with_invoice_discount": count, "discount_field": discount_col}, "source_rows": citations}
    if (re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(balance|amount due|receivable|owed|owing)\b", q)
            and re.search(r"\b(positive|above zero|greater than zero|nonzero|outstanding|due)\b", q)
            and re.search(r"\b(receipts?|transactions?|invoices?|bills?|headers?)\b", q)):
        balance_col = next((col for col in ("outstanding_balance", "balance_due", "amount_due", "customer_balance", "receivable_balance") if col in frame and frame[col].notna().any()), None)
        if balance_col:
            id_col = next((col for col in ("invoice_id", "receipt_id", "transaction_id", "bill_no") if col in frame and frame[col].notna().any()), None)
            if id_col:
                work = frame.loc[frame[balance_col].notna() & frame[id_col].notna()].copy()
                grain_cols = [col for col in ("_header_source_file", "_header_source_row") if col in work]
                if len(grain_cols) != 2:
                    grain_cols = [col for col in ("source_file", "source_row") if col in work]
                if len(grain_cols) == 2:
                    work = work.drop_duplicates(subset=grain_cols)
                work["_balance_value"] = pd.to_numeric(work[balance_col], errors="coerce")
                positive = work.loc[work["_balance_value"].gt(0)]
                if len(grain_cols) == 2:
                    citations = list(dict.fromkeys((str(row[grain_cols[0]]), row[grain_cols[1]]) for _, row in positive.iterrows()))
                else:
                    citations = row_ids.loc[positive.index].tolist()
                return {"answer": f"{len(positive):,} of {len(work):,} receipt headers have a positive recorded balance due.", "values": {"status": "ok", "receipts_with_positive_balance": int(len(positive)), "receipt_headers_checked": int(len(work)), "balance_field": balance_col}, "source_rows": citations}
    if (re.search(r"\bhow many\b.{0,35}\b(racks?|warehouses?|locations?|shelves?)\b|\b(?:count|number of)\b.{0,35}\b(racks?|warehouses?|locations?|shelves?)\b", q)
            and re.search(r"\b(distinct|unique|different)\b", q)
            and not re.search(r"\b(products?|medicines?|items?)\b", q)
            and re.search(r"\b(positive stock|stock|inventory|in stock|on[- ]hand)\b", q)):
        location_col = next((col for col in ("rack_location", "rack", "warehouse", "location", "shelf") if col in frame and frame[col].notna().any()), None)
        stock_col = next((col for col in ("stock_qty", "quantity", "available_qty", "on_hand") if col in frame and frame[col].notna().any()), None)
        if location_col and stock_col:
            work = frame.copy()
            if "database_name" in work:
                inventory_mask = work.database_name.fillna("").astype(str).str.casefold().eq("inventory")
                if inventory_mask.any():
                    work = work.loc[inventory_mask]
            if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in work:
                purchase_ids = work.purchase_order_no.fillna("").astype(str).str.strip()
                work = work.loc[purchase_ids.eq("")]
            work = work.loc[pd.to_numeric(work[stock_col], errors="coerce").gt(0) & work[location_col].notna()]
            locations = work.drop_duplicates(subset=[location_col])
            names = locations[location_col].astype(str).tolist()
            citations = [(str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row")) for _, row in locations.iterrows() if pd.notna(row.get("source_row"))]
            return {"answer": f"{len(names):,} distinct rack/location names have positive current stock.", "values": {"status": "ok", "positive_stock_locations": int(len(names)), "locations": names}, "source_rows": citations}
    if re.search(r"\b(forecast|predict(?:ion)?|project(?:ion)?|will\s+(?:i|we|it)\s+sell|next\s+(?:month|week)|agle\s+(?:mahine|month))\b", q):
        return None

    def has_values(*columns):
        return any(column in frame and frame[column].notna().any() for column in columns)

    frame = _apply_explicit_date_filters(question, frame, filters)
    row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)

    table_names = frame.get("table_name", pd.Series(dtype=str)).fillna("").astype(str).str.casefold()
    txn_types = frame.get("txn_type", pd.Series(dtype=str)).fillna("").astype(str).str.casefold()
    has_inventory_rows = (
        has_values("stock_qty", "closing_stock_qty", "available_qty")
        or table_names.str.contains(r"inventory|stock|batch|product[_ ]?master|tbl[_ ]products", regex=True).any()
        or txn_types.str.contains(r"inventory|stock", regex=True).any()
        or (has_values("quantity") and has_values("reorder_level", "expiry_date", "rack_location", "warehouse")
            and not has_values("invoice_id", "transaction_id", "purchase_order_no"))
    )
    has_purchase_rows = (
        has_values("purchase_order_no")
        or (has_values("supplier_id", "supplier_name") and has_values("invoice_total", "net_payable"))
        or table_names.str.contains(r"purchase|supplier invoice", regex=True).any()
        or txn_types.str.contains(r"purchase|expense", regex=True).any()
    )

    # A complete table is not automatically evidence for every pharmacy
    # question. Fail closed when the requested record type or measure is absent;
    # otherwise the generic aggregate fallback can turn a stock/expiry question
    # into an unrelated sales total.
    asks_expiry = bool(re.search(r"\b(expir\w*|near[- ]?expiry|expired|qareeb[- ]ul[- ]expiry|meyad|miyad)\b", q))
    asks_batch = bool(re.search(r"\b(batch(?:es)?|lot(?:s)?)\b", q))
    asks_on_hand = bool(
        re.search(r"\b(on[- ]hand|low[- ]stock|out[- ]of[- ]stock|reorder|restock|stock level|stock left|stock remaining|current stock|inventory value|stock value|stock in hand)\b", q)
        or (re.search(r"\b(stock|inventory)\b", q) and not re.search(r"\b(stock|inventory)\s+(?:sold|purchase|purchased|movement|turnover)\b", q))
    )
    asks_profit = bool(re.search(r"\b(profit|profitability|margin|munafa|faida|gross profit|net profit)\b", q))
    asks_cash_credit = bool(re.search(r"\b(cash\s+(?:sale|sales)|credit\s+(?:sale|sales)|cash\s+vs\s+credit|cash\s+and\s+credit|credit\s+and\s+cash|udhaar|naqad)\b", q))
    asks_purchase = bool(
        re.search(r"\b(purchase|purchases|purchasing|purchased|mangwai|khareed)\b", q)
        or (re.search(r"\b(supplier|suppliers|vendor|vendors|distributor|distributors)\b", q)
            and re.search(r"\b(most|highest|top|purchas|buy|bought|order|supply|supplied|bonus|quantity|how much|kitna|kitni)\b", q))
        or re.search(r"\b(supply|supplied|deliveries|delivery|lead[- ]time|received from|aayi|aaya)\b", q)
    )
    sale_history = has_values("invoice_id", "transaction_id", "amount", "quantity", "unit_price")
    cost_fields = ("cost", "unit_cost", "cost_price", "cogs", "cost_of_goods")
    sales_cost_rows = frame
    if "table_name" in frame:
        sales_mask = frame.table_name.fillna("").astype(str).str.contains(r"sales?.*detail|invoice.*detail|bill.*detail", case=False, regex=True)
        if sales_mask.any():
            sales_cost_rows = frame.loc[sales_mask]
    elif "txn_type" in frame and frame.txn_type.fillna("").astype(str).str.contains(r"sale|dispens", case=False, regex=True).any():
        sales_cost_rows = frame.loc[frame.txn_type.fillna("").astype(str).str.contains(r"sale|dispens", case=False, regex=True)]
    if asks_expiry and not has_values("expiry_date"):
        return {"answer": "The selected data has no expiry-date field, so I can't identify expired or near-expiry medicines from it.", "values": {"status": "unsupported_field", "required_field": "expiry_date"}, "source_rows": []}
    if asks_batch and not has_values("batch_no", "batch_number", "lot_no", "lot_number"):
        return {"answer": "The selected data has no batch or lot identifiers, so I can't list or identify medicine batches.", "values": {"status": "unsupported_field", "required_field": "batch_no"}, "source_rows": []}
    if asks_on_hand and not has_inventory_rows:
        return {"answer": "The selected data contains no on-hand inventory records. Its transaction quantity cannot be treated as current stock; connect or select an inventory source to answer this.", "values": {"status": "unsupported_record_type", "required_record_type": "inventory"}, "source_rows": []}
    if asks_expiry and re.search(r"\b(stock|quantity|units|value|worth|cost)\b", q) and not has_inventory_rows:
        return {"answer": "Expiry dates alone do not show how much stock is currently on hand. Select inventory or batch-stock records with on-hand quantities to calculate expired stock or its value.", "values": {"status": "unsupported_record_type", "required_record_type": "inventory"}, "source_rows": []}
    if asks_profit and sale_history and not any(c in sales_cost_rows and sales_cost_rows[c].notna().any() for c in cost_fields):
        evidence = pd.Series(False, index=frame.index)
        id_match = re.search(r"\b(?:sale|sales|inv|invoice|bill|receipt)[-_/ ]?[a-z0-9-]*\d[a-z0-9-]*\b", q)
        if id_match:
            wanted_id = id_match.group(0).replace(" ", "").replace("_", "-").casefold()
            for col in ("invoice_id", "transaction_id", "receipt_id", "bill_no", "bill_number"):
                if col in frame:
                    evidence |= frame[col].fillna("").astype(str).str.casefold().str.replace(r"\.0+$", "", regex=True).eq(wanted_id)
        if not evidence.any():
            product_col = next((c for c in ("product_id", "product_name", "medicine_name", "product_code") if c in frame), None)
            if product_col:
                products = sorted((str(v) for v in frame[product_col].dropna().unique() if str(v).strip()), key=len, reverse=True)
                named = next((value for value in products if re.search(rf"(?<!\w){re.escape(value.casefold())}(?!\w)", q)), None)
                if named:
                    evidence = frame[product_col].astype(str).str.casefold().eq(named.casefold())
        return {"answer": "The purchase cost is not recorded for these sales, so I can't determine gross profit or margin reliably.", "values": {"status": "unsupported_field", "required_field": "cost"}, "source_rows": row_ids.loc[evidence].tolist()}
    if asks_cash_credit and not has_values("payment_method", "payment_type", "payment_mode", "payment_status", "status"):
        return {"answer": "The selected sales data has no payment method or payment status, so I can't split sales into cash and credit.", "values": {"status": "unsupported_field", "required_field": "payment_method"}, "source_rows": []}
    if re.search(r"\b(recall|recalled|recall notice)\b", q) and not has_values("recall_status", "is_recalled", "recall_date", "recall_notice"):
        return {"answer": "I can't verify a recall from the selected records because they contain no recall status or recall notice data. Check the applicable current regulator or manufacturer recall source.", "values": {"status": "unsupported_field", "required_field": "recall_status"}, "source_rows": []}
    customer_history_intent = bool(re.search(r"\b(last time|last purchase|latest purchase|history|purchased?|bought|orders?|dispensed)\b", q))
    named_customer_lookup = bool(re.search(r"\bcustomer\s+[a-z][a-z'-]+(?:\s+[a-z][a-z'-]+)+", q) and customer_history_intent and has_values("customer_id", "customer_name", "customer_alias"))
    inventory_supplier_filter = has_inventory_rows and bool(re.search(r"\b(supplied by|supplier|vendor|manufacturer)\b", q)) and bool(re.search(r"\b(which|list|show|find|items?|products?|medicines?|category|therapeutic)\b", q))
    if asks_purchase and not has_purchase_rows and not named_customer_lookup and not inventory_supplier_filter and not re.search(r"\b(?:price|cost)\s+(?:of|for)\b", q):
        return {"answer": "The selected data has no purchase-order or purchase-ledger records, so I can't report purchases or rank suppliers by purchasing.", "values": {"status": "unsupported_record_type", "required_record_type": "purchases"}, "source_rows": []}

    exact_match = re.search(
        r"\b(?:[a-z]{1,8}[-_/])?(?:sale|sales|pur|purchase|inv|inventory|sku|batch|lot|rx|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",
        q,
    )
    document_match = re.search(
        r"\b(?:bill|invoice|receipt)\s*(?:number|no\.?|#)\s*[:#-]?\s*([a-z0-9/-]*\d[a-z0-9/-]*)\b",
        q,
    )
    batch_match = re.search(
        r"\b(?:batch|lot)\s*(?:number|no\.?|#)?\s*(?:called|labeled|named|identified\s+as)?\s*[:#-]?\s*((?:[a-z]{1,8}[-_/])?[a-z0-9-]*\d[a-z0-9-]*)\b",
        q,
    )
    bare_code_match = re.search(r"\b([a-z]{1,8}[-_/]\d+[a-z0-9-]*)\b", q, re.I)
    all_ids = list(dict.fromkeys(re.findall(
        r"\b(?:[a-z]{1,8}[-_/])?(?:sale|sales|pur|purchase|inv|inventory|sku|batch|lot|rx|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",
        q,
    )))
    if len(all_ids) > 1 and re.search(r"\b(value|worth|cost|combined|total)\b", q):
        wanted_ids = {value.replace("_", "-").casefold() for value in all_ids}
        lookup_cols = [c for c in ("product_code", "transaction_id", "purchase_order_no", "invoice_id", "product_id", "batch_no") if c in frame]
        matched = pd.Series(False, index=frame.index)
        for col in lookup_cols:
            matched |= frame[col].fillna("").astype(str).str.casefold().isin(wanted_ids)
        selected = frame.loc[matched]
        if len(selected) < len(wanted_ids):
            found = set()
            for col in lookup_cols: found |= set(selected[col].dropna().astype(str).str.casefold())
            missing = sorted(wanted_ids - found)
            if missing:
                return {"answer": "I couldn't calculate the combined value because these identifiers were not found in the selected data: " + ", ".join(x.upper() for x in missing) + ".", "values": {"status": "missing_record", "missing_ids": missing}, "source_rows": []}
        if "stock_qty" in selected and "cost" in selected:
            value = (pd.to_numeric(selected.stock_qty, errors="coerce") * pd.to_numeric(selected.cost, errors="coerce")).sum()
            row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)
            return {"answer": f"Combined inventory value (stock × unit cost): {value:,.2f}.", "values": {"inventory_value": float(value), "record_count": len(selected), "record_ids": sorted(wanted_ids)}, "source_rows": row_ids.loc[selected.index].tolist()}
    if not exact_match and not document_match and not batch_match and bare_code_match:
        batch_values = [c for c in ("batch_no", "batch_number", "lot_no", "lot_number") if c in frame]
        if any(frame[col].fillna("").astype(str).str.casefold().eq(bare_code_match.group(1).replace("_", "-").casefold()).any() for col in batch_values):
            batch_match = bare_code_match
    if exact_match or document_match or batch_match:
        wanted = (exact_match.group(0) if exact_match else document_match.group(1) if document_match else batch_match.group(1)).replace("_", "-").casefold()
        id_cols = [c for c in ("invoice_id", "invoice_no", "invoiceno", "reference_number", "reference_no", "bill_no", "bill_number", "bill_id", "billno", "receipt_id", "receipt_no", "receipt_number", "transaction_id", "purchase_order_no", "product_code", "batch_no", "product_id") if c in frame]
        matched = pd.Series(False, index=frame.index)
        def normalize_key(value):
            text = str(value).strip().casefold()
            return re.sub(r"\.0+$", "", text) if re.fullmatch(r"\d+\.0+", text) else text
        for col in id_cols:
            matched |= frame[col].fillna("").map(normalize_key).eq(normalize_key(wanted))
        # Some POS ledgers expose the receipt label as REC-00031 while the
        # structured line rows retain only transaction_id=31. Resolve that
        # formatted alias only after exact source identifiers have failed.
        if not matched.any() and re.fullmatch(r"rec-\d+", wanted) and "transaction_id" in frame:
            numeric_transaction_id = str(int(wanted.rsplit("-", 1)[1]))
            matched |= frame["transaction_id"].fillna("").map(normalize_key).eq(numeric_transaction_id)
        if not matched.any():
            return {
                "answer": f"No record with identifier {wanted.upper()} was found in the selected data.",
                "values": {"matched_records": 0, "lookup_id": wanted.upper()},
                "source_rows": [],
            }
        matched_rows = frame.loc[matched]
        if (re.search(r"\b(invoice|receipt|bill)\b", q)
                and re.search(r"\b(tax|gst|vat)\b", q)):
            tax_col = next((col for col in ("invoice_tax", "invoice_tax_amount", "tax_amount") if col in matched_rows and matched_rows[col].notna().any()), None)
            if tax_col:
                tax_invoice_key = next((col for col in ("transaction_id", "invoice_id", "invoice_no", "receipt_id", "bill_no") if col in matched_rows and matched_rows[col].notna().any()), None)
                header_rows = matched_rows.drop_duplicates(subset=[tax_invoice_key]) if tax_invoice_key else matched_rows.iloc[:1]
                tax_values = pd.to_numeric(header_rows[tax_col], errors="coerce").dropna()
                if not tax_values.empty:
                    tax_value = float(tax_values.iloc[0])
                    header_file_col = "_header_source_file" if "_header_source_file" in header_rows and header_rows._header_source_file.notna().any() else None
                    header_row_col = "_header_source_row" if "_header_source_row" in header_rows and header_rows._header_source_row.notna().any() else None
                    if header_file_col and header_row_col:
                        cited_rows = list(dict.fromkeys(
                            (str(row[header_file_col]), row[header_row_col])
                            for _, row in header_rows.iterrows()
                            if pd.notna(row[header_file_col]) and pd.notna(row[header_row_col])
                        ))
                    elif "source_file" in header_rows and "source_row" in header_rows:
                        cited_rows = [(str(row["source_file"]), row["source_row"]) for _, row in header_rows.iterrows() if pd.notna(row["source_row"])]
                    else:
                        cited_rows = row_ids.loc[header_rows.index].tolist()
                    return {"answer": f"Invoice {wanted.upper()}: recorded invoice tax {tax_value:,.2f}.", "values": {"lookup_id": wanted.upper(), "invoice_tax": tax_value, "tax_field": tax_col, "matched_records": int(len(header_rows))}, "source_rows": cited_rows}
        if (re.search(r"\b(invoice|receipt|bill)\b", q)
                and re.search(r"\b(quantity|qty|units?|how many)\b", q)):
            quantity_col = next((col for col in ("quantity", "qty", "qty_sold", "units_sold") if col in matched_rows), None)
            product_col = next((col for col in ("product_id", "product_name", "medicine_name", "product_code") if col in matched_rows), None)
            quantity_rows = matched_rows
            product_name = None
            if product_col:
                product_names = sorted((str(value) for value in matched_rows[product_col].dropna().unique()), key=len, reverse=True)
                product_name = next((name for name in product_names if name.casefold() in q), None)
                if product_name:
                    quantity_rows = matched_rows.loc[matched_rows[product_col].astype(str).str.casefold().eq(product_name.casefold())]
            if quantity_col:
                quantities = pd.to_numeric(quantity_rows[quantity_col], errors="coerce").dropna()
                if not quantities.empty:
                    total_quantity = float(quantities.sum())
                    item_text = f" for {product_name}" if product_name else ""
                    return {
                        "answer": f"Invoice {wanted.upper()}: recorded quantity{item_text} is {total_quantity:g} across {len(quantities)} line item(s).",
                        "values": {"matched_records": int(len(quantities)), "lookup_id": wanted.upper(), "product": product_name, "total_quantity": total_quantity},
                        "source_rows": row_ids.loc[quantities.index].tolist(),
                    }
        if (
            re.search(r"\b(invoice|receipt|bill)\b", q)
            and re.search(r"\b(paid|payment|received)\b", q)
            and re.search(r"\b(amount|how much)\b", q)
        ):
            paid_col = next((col for col in ("paid_amount", "amount_paid", "amount_received") if col in matched_rows and matched_rows[col].notna().any()), None)
            if paid_col:
                invoice_key = next((col for col in ("invoice_id", "invoice_no", "receipt_id", "bill_no", "transaction_id") if col in matched_rows), None)
                header_rows = matched_rows.drop_duplicates(subset=[invoice_key]) if invoice_key else matched_rows.iloc[:1]
                paid_values = pd.to_numeric(header_rows[paid_col], errors="coerce").dropna()
                header_file_col = "_header_source_file" if "_header_source_file" in header_rows and header_rows._header_source_file.notna().any() else None
                header_row_col = "_header_source_row" if "_header_source_row" in header_rows and header_rows._header_source_row.notna().any() else None
                if paid_values.empty or not header_file_col or not header_row_col:
                    return {
                        "answer": f"A paid amount with invoice-header provenance is not available for {wanted.upper()} in the selected records.",
                        "values": {"status": "unsupported_field", "lookup_id": wanted.upper(), "required_field": "paid amount with invoice header source"},
                        "source_rows": [],
                    }
                total_paid = float(paid_values.sum())
                cited_rows = [
                    (str(row[header_file_col]), row[header_row_col])
                    for _, row in header_rows.iterrows()
                    if pd.notna(row[header_file_col]) and pd.notna(row[header_row_col])
                ]
                return {
                    "answer": f"Invoice {wanted.upper()}: recorded amount paid {total_paid:,.2f}.",
                    "values": {"matched_records": int(len(header_rows)), "lookup_id": wanted.upper(), "amount_paid": total_paid, "paid_amount_field": paid_col},
                    "source_rows": cited_rows,
                }
        if (
            re.search(r"\b(invoice|receipt|bill)\b", q)
            and re.search(r"\b(total|amount|revenue|value|how much)\b", q)
            and not re.search(r"\b(quantity|qty|units?)\b", q)
            and not re.search(r"\b(discount\w*|products?|items?|medicines?|drugs?)\b", q)
        ):
            amount_col = next((col for col in ("amount", "invoice_total", "net_payable", "sales_subtotal") if col in matched_rows), None)
            if amount_col:
                line_amounts = pd.to_numeric(matched_rows[amount_col], errors="coerce").dropna()
                if not line_amounts.empty:
                    total_amount = float(line_amounts.sum())
                    answer = f"Invoice {wanted.upper()}: total recorded sales amount {total_amount:,.2f} across {len(line_amounts)} line items."
                    result_values = {"matched_records": int(len(line_amounts)), "lookup_id": wanted.upper(), "total_sales_amount": total_amount}
                    if "source_file" in matched_rows:
                        cited_rows = [
                            (str(matched_rows.loc[idx].get("source_file") or ""), row_ids.loc[idx])
                            for idx in line_amounts.index
                        ]
                    else:
                        cited_rows = row_ids.loc[line_amounts.index].tolist()
                    return {"answer": answer, "values": result_values, "source_rows": cited_rows}
        if ("discount" in q and re.search(r"\b(invoice|receipt|bill)\b", q)
                and "invoice_discount" in matched_rows):
            headers = matched_rows.loc[pd.to_numeric(matched_rows.invoice_discount, errors="coerce").notna()]
            if not headers.empty:
                invoice_discount = float(pd.to_numeric(headers.invoice_discount, errors="coerce").iloc[0])
                return {
                    "answer": f"Invoice {wanted.upper()}: total recorded discount {invoice_discount:,.2f}.",
                    "values": {"matched_records": 1, "lookup_id": wanted.upper(), "invoice_discount": invoice_discount},
                    "source_rows": row_ids.loc[headers.index].tolist(),
                }
        if "discount" in q and re.search(r"\b(invoice|receipt|bill)\b", q):
            line_discount_col = next((col for col in ("discount", "discount_amount") if col in matched_rows), None)
            if line_discount_col:
                line_discounts = pd.to_numeric(matched_rows[line_discount_col], errors="coerce").dropna()
                if not line_discounts.empty:
                    total_discount = float(line_discounts.sum())
                    return {
                        "answer": f"Invoice {wanted.upper()}: total discount across matching lines {total_discount:,.2f}.",
                        "values": {"matched_records": int(len(line_discounts)), "lookup_id": wanted.upper(), "total_discount": total_discount},
                        "source_rows": row_ids.loc[line_discounts.index].tolist(),
                    }
        batch_id_cols = [c for c in ("batch_no", "batch_number", "lot_no", "lot_number") if c in frame]
        is_batch_lookup = bool(batch_match) or bool(bare_code_match and any(frame[col].fillna("").astype(str).str.casefold().eq(wanted).any() for col in batch_id_cols))
        if is_batch_lookup:
            if "table_name" in matched_rows and not re.search(r"\b(archive|archived|history|historical|previous|recorded counts?)\b", q):
                current_rows = matched_rows.loc[~matched_rows.table_name.astype(str).str.contains(r"archive|histor(?:y|ical)|old", case=False, regex=True)]
                if not current_rows.empty:
                    matched_rows = current_rows
                    matched = pd.Series(frame.index.isin(matched_rows.index), index=frame.index)
            detail_cols = [c for c in ("product_id", "product_name", "batch_no", "expiry_date", "stock_qty", "quantity", "reorder_level", "unit_price", "cost", "rack_location", "warehouse", "date") if c in matched_rows]
            details = []
            for _, item in matched_rows[detail_cols].drop_duplicates().iterrows():
                fields = [f"{col.replace('_', ' ').title()}: {item[col]}" for col in detail_cols if pd.notna(item[col])]
                matched_row = matched_rows.loc[item.name] if item.name in matched_rows.index else None
                purchase_linked_quantity = bool(
                    matched_row is not None and "purchase_order_no" in matched_row
                    and pd.notna(matched_row.get("purchase_order_no"))
                    and str(matched_row.get("purchase_order_no")).strip()
                )
                stock = next((item[col] for col in ("stock_qty", "available_qty", "closing_stock_qty") if col in item and pd.notna(item[col])), None)
                if stock is None and not purchase_linked_quantity and "quantity" in item and pd.notna(item["quantity"]):
                    stock = item["quantity"]
                if (stock is not None and "reorder_level" in item and pd.notna(item["reorder_level"])
                        and re.search(r"\b(how many|number of|difference|much)\b", q)):
                    delta = abs(float(stock) - float(item["reorder_level"]))
                    state = "above" if float(stock) > float(item["reorder_level"]) else "below" if float(stock) < float(item["reorder_level"]) else "exactly at"
                    fields.insert(0, f"Reorder comparison: {delta:g} units {state} reorder level")
                details.append("; ".join(fields))
            values = {"matched_records": int(matched.sum()), "lookup_id": wanted.upper(), "records": matched_rows[detail_cols].to_dict("records")}
            purchase_linked = (
                "purchase_order_no" in matched_rows
                and matched_rows.purchase_order_no.fillna("").astype(str).str.strip().ne("").any()
            )
            if re.search(r"\b(how many|number of|difference|much)\b", q) and "reorder_level" in matched_rows and not purchase_linked:
                stock_col = next((col for col in ("stock_qty", "available_qty", "closing_stock_qty", "quantity") if col in matched_rows), None)
                if stock_col:
                    values["reorder_difference"] = abs(float(matched_rows.iloc[0][stock_col]) - float(matched_rows.iloc[0]["reorder_level"]))
            if "source_file" in matched_rows and "source_row" in matched_rows:
                exact_citations = [
                    (str(row.get("source_file")), row.get("source_row"))
                    for _, row in matched_rows.iterrows()
                    if pd.notna(row.get("source_file")) and pd.notna(row.get("source_row"))
                ]
            else:
                exact_citations = row_ids.loc[matched_rows.index].tolist()
            return {
                "answer": f"Batch {wanted.upper()}: " + " | ".join(details) + ".",
                "values": values,
                "source_rows": exact_citations,
            }
        # An invoice/bill key may cover multiple line items. Return every
        # matching product line and cite each contributing source row rather
        # than selecting an arbitrary first row from the invoice.
        if re.search(r"\b(products?|items?|medicines?|drugs?|which medicines?|kaun kaun si|dawa\w*|dawai\w*|goli\w*|tablet\w*)\b", q) and any(re.search(rf"\b{word}\b", q) for word in ("invoice", "bill", "receipt")):
            product_col = next((c for c in ("product_id", "medicine_name", "product_name", "description") if c in matched_rows), None)
            if product_col:
                details = []
                grouped = matched_rows.groupby(product_col, dropna=True)
                for name, part in grouped:
                    quantity = pd.to_numeric(part.get("quantity", pd.Series(dtype=float)), errors="coerce").sum()
                    details.append(f"{name} (quantity {quantity:g})" if "quantity" in part else str(name))
                id_text = str(wanted).upper()
                return {
                    "answer": f"{id_text} contains: " + "; ".join(details) + ".",
                    "values": {"matched_records": int(matched.sum()), "lookup_id": id_text, "products": details},
                    "source_rows": row_ids.loc[matched_rows.index].tolist(),
                }
        row = matched_rows.iloc[0]
        if re.search(r"\b(ready to dispense|can .* dispense|may .* dispense|okay to dispense|ok to dispense)\b", q):
            status = next((str(row[col]) for col in ("status", "prescription_status", "refill_status") if col in row and pd.notna(row[col])), None)
            description = str(row.get("description", ""))
            if status or description:
                recorded = status or description
                not_ready = bool(re.search(r"\b(awaiting|pending|not approved|not authorized|on hold|rejected)\b", recorded, re.I))
                answer = (f"{wanted.upper()} has recorded status: {recorded}. The prescriber authorization is still pending; "
                          "this record makes no clinical-suitability or dispensing determination." if not_ready
                          else f"{wanted.upper()}: recorded status is {recorded}. The connected record alone does not establish clinical suitability or dispensing approval.")
                return {"answer": answer, "values": {"matched_records": int(matched.sum()), "lookup_id": wanted.upper(), "status": status}, "source_rows": row_ids.loc[matched_rows.index].tolist()}
        row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)
        requested = []
        if re.search(r"\bpayment\s+(?:method|type|mode)\b", q):
            method_value = next((row[col] for col in ("payment_method", "payment_type", "payment_mode") if col in row and pd.notna(row[col])), None)
            if method_value is None:
                source_rows = row_ids.loc[matched_rows.index].tolist()
                return {"answer": f"{wanted.upper()}: the selected record does not include a payment method.", "values": {"status": "unsupported_field", "matched_records": int(matched.sum()), "lookup_id": wanted.upper(), "required_field": "payment_method"}, "source_rows": source_rows}
            requested.append(("Payment method", method_value))
        if re.search(r"\b(counter sale|over[- ]the[- ]counter sale|sale channel|fulfillment method|delivery method|ship(?:ping)? method)\b", q):
            channel_value = next((row[col] for col in ("ship_via", "_extra.SHIP_VIA", "sale_channel", "fulfillment_method") if col in row and pd.notna(row[col]) and str(row[col]).strip()), None)
            if channel_value is not None:
                requested.append(("Recorded sale channel", channel_value))
        specs = (
            (r"customer|client|buyer", "Customer", ("customer_id", "customer_name")),
            (r"product|item|medicine|drug", "Product", ("product_id", "medicine_name")),
            (r"category|class", "Category", ("category",)),
            (r"quantity|units?|qty", "Quantity", ("quantity", "stock_qty", "total_qty")),
            (r"stock|on hand|available", "Stock", ("stock_qty", "quantity")),
            (r"reorder", "Reorder level", ("reorder_level",)),
            (r"discount", "Discount", ("discount", "discount_amount")),
            (r"date|when", "Date", ("date", "purchase_date")),
            (r"branch|warehouse|location|where", "Branch/Warehouse", ("branch", "warehouse", "rack_location")),
            (r"supplier|vendor", "Supplier", ("supplier_name", "supplier_id")),
            (r"expir\w*", "Expiry", ("expiry_date",)),
            (r"unit cost|cost", "Unit cost", ("cost", "unit_cost")),
            (r"unit price|price", "Unit price", ("unit_price",)),
            (r"status|paid", "Payment status", ("status",)),
            (r"customer balance|balance due|outstanding balance|receivable balance", "Customer balance", ("customer_balance", "balance_due", "outstanding_balance", "amount_due")),
            (r"amount|total", "Total amount", ("amount", "invoice_total", "net_payable")),
        )
        for pattern, label, columns in specs:
            if not re.search(rf"\b(?:{pattern})\b", q):
                continue
            value = next((row[col] for col in columns if col in row and pd.notna(row[col])), None)
            if value is not None:
                requested.append((label, value))
        if re.search(r"\breorder\b", q):
            stock_value = next((row[col] for col in ("stock_qty", "quantity") if col in row and pd.notna(row[col])), None)
            reorder_value = row.get("reorder_level")
            if stock_value is not None and pd.notna(reorder_value):
                stock_num, reorder_num = float(stock_value), float(reorder_value)
                state = "below" if stock_num < reorder_num else "above" if stock_num > reorder_num else "exactly at"
                difference = abs(stock_num - reorder_num)
                if re.search(r"\b(how many|number of|difference|much)\b", q):
                    comparison = f"{difference:g} units {state} reorder level"
                else:
                    comparison = f"Stock {state} reorder level ({stock_num:g} vs {reorder_num:g})"
                requested.insert(0, ("Reorder comparison", comparison))
        if not requested:
            skip = {"source_row", "source_file", "source_connector"}
            requested = [(str(col).replace("_", " ").title(), row[col]) for col in frame.columns if col not in skip and pd.notna(row[col])]
        source_row = row_ids.loc[row.name]
        if ("_header_source_file" in matched_rows and "_header_source_row" in matched_rows
                and matched_rows["_header_source_file"].notna().any()):
            header_rows = matched_rows.loc[
                matched_rows["_header_source_file"].notna() & matched_rows["_header_source_row"].notna()
            ].drop_duplicates(subset=["_header_source_file", "_header_source_row"])
            source_citations = [
                (str(item["_header_source_file"]), item["_header_source_row"])
                for _, item in header_rows.iterrows()
            ]
        elif "source_file" in matched_rows and "source_row" in matched_rows:
            source_citations = list(dict.fromkeys(
                (str(item["source_file"]), item["source_row"])
                for _, item in matched_rows.iterrows()
                if pd.notna(item["source_file"]) and pd.notna(item["source_row"])
            ))
        else:
            source_citations = [source_row]
        result_values = {"matched_records": int(matched.sum()), "lookup_id": wanted.upper()}
        stock_value = next((row[col] for col in ("stock_qty", "quantity") if col in row and pd.notna(row[col])), None)
        if (re.search(r"\b(how many|number of|difference|much)\b", q)
                and stock_value is not None and "reorder_level" in row and pd.notna(row["reorder_level"])):
            result_values["reorder_difference"] = abs(float(stock_value) - float(row["reorder_level"]))
        return {
            "answer": f"{wanted.upper()}: " + "; ".join(f"{label}: {value}" for label, value in requested) + ".",
            "values": result_values,
            "source_rows": source_citations,
        }

    hybrid_result = _answer_cross_table_pharmacy_risk(question, frame, filters=filters)
    if hybrid_result is not None:
        return hybrid_result

    customer_result = _answer_customer_question(question, frame)
    if customer_result is not None:
        return customer_result

    # Inventory and batch questions have a different row grain from the POS
    # sales ledger. Resolve them before generic sales/purchase aggregators can
    # consume incidental words such as "which" or "product".
    inventory = _answer_inventory_question(question, frame)
    if inventory is not None:
        return inventory

    purchase = _answer_purchase_question(question, frame)
    if purchase is not None:
        return purchase

    line_median = _answer_line_quantity_median(question, frame)
    if line_median is not None:
        return line_median

    # A normalized POS snapshot has distinct purchase-header, purchase-line,
    # invoice-line, and batch row grains. Use its row-aware planner for delivery
    # history and purchase-cost questions before generic sales fallbacks run.
    if "table_name" in frame and re.search(r"\b(lead[- ]time|delivery time|how long .*deliver|days? .*deliver|usually take to deliver|purchase cost|last purchase|last bought|purchase price)\b", q):
        try:
            from app.analytics.domains.pharmacy_pos import analyze_pos_question, is_pos_snapshot
            if is_pos_snapshot(frame):
                from app.analytics.filters import KPIFilters
                result = analyze_pos_question(frame, question, KPIFilters())
                if result.status != "ok" or result.value is None:
                    reason = result.reason or "The requested purchase or delivery metric is unavailable in the selected data."
                    return {"answer": f"{result.name} is unavailable: {reason}", "values": {"status": "unavailable", "reason": reason, "metric": result.key}, "source_rows": result.provenance.source_rows}
                if result.breakdown:
                    rows = [", ".join(f"{key.replace('_', ' ').title()}: {value}" for key, value in row.items()) for row in result.breakdown]
                    answer = f"{result.name}: " + "; ".join(rows)
                else:
                    answer = f"{result.name}: {result.value} {result.unit}".strip()
                return {"answer": answer, "values": {"status": "ok", "metric": result.key, "value": result.value, "unit": result.unit, "breakdown": result.breakdown}, "source_rows": result.provenance.source_rows}
        except (ImportError, AttributeError, TypeError, ValueError):
            pass

    # Transaction and invoice ledgers have a reliable row grain and should use
    # the schema-aware aggregate planner before generic list fallbacks inspect
    # incidental words such as "which" or "month".
    # Give the sales planner first chance: the generic aggregate planner can
    # otherwise mistake category/supplier words for an inventory ranking.
    if is_distinct_entity_count_question(question):
        distinct_frame = frame
        if re.search(r"\b(sales?|sold|receipts?|transactions?)\b", question, re.I) and "txn_type" in frame:
            sales_rows = frame.txn_type.fillna("").astype(str).str.casefold().str.contains(
                r"sale|return|refund|invoice|bill|transaction|dispens", regex=True
            )
            if sales_rows.any():
                distinct_frame = frame.loc[sales_rows].copy()
        source_rows = distinct_frame["source_row"] if "source_row" in distinct_frame else pd.Series(distinct_frame.index + 1, index=distinct_frame.index)
        distinct_result = _answer_schema_aggregate(question, distinct_frame, source_rows)
        if distinct_result is not None:
            return distinct_result
    if (
        re.search(r"\b(how many|number of|count)\b", question, re.I)
        and re.search(r"\b(receipts?|invoices?|transactions?)\b", question, re.I)
        and re.search(r"\b(highest|largest|most|fewest|least)\b", question, re.I)
        and not re.search(r"\bat least\b", question, re.I)
    ):
        count_frame = frame
        if re.search(r"\bsales?\b", question, re.I) and "txn_type" in frame:
            sales_rows = frame.txn_type.fillna("").astype(str).str.casefold().str.contains(
                r"sale|return|refund|invoice|bill|transaction|dispens", regex=True
            )
            if sales_rows.any():
                count_frame = frame.loc[sales_rows].copy()
        empty_inventory_fields = [
            column for column in ("stock_qty", "purchase_order_no")
            if column in count_frame and not count_frame[column].notna().any()
        ]
        if empty_inventory_fields:
            count_frame = count_frame.drop(columns=empty_inventory_fields)
        if "_header_source_file" in count_frame and "_header_source_row" in count_frame:
            source_rows = count_frame.apply(
                lambda row: (row["_header_source_file"], row["_header_source_row"])
                if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
                else None,
                axis=1,
            )
        else:
            source_rows = count_frame["source_row"] if "source_row" in count_frame else pd.Series(count_frame.index + 1, index=count_frame.index)
        grouped_count = _answer_schema_aggregate(question, count_frame, source_rows)
        if grouped_count is not None:
            return grouped_count
    # Resolve remaining compositional group-size requests before generic sales
    # and whole-frame count fallbacks can collapse them to an unrelated KPI.
    from app.analytics.schema_query import answer_schema_query
    planned_result = answer_schema_query(question, frame)
    if planned_result is not None:
        return planned_result
    sale = _answer_sales_question(question, frame, filters=filters)
    if sale is not None:
        return sale
    if has_values("transaction_id", "invoice_id", "receipt_id", "order_id"):
        structured_sales = _answer_schema_aggregate(question, frame, row_ids)
        if structured_sales is not None:
            return structured_sales

    catalog_result = answer_product_catalog_question(question, frame)
    if catalog_result is not None:
        return catalog_result
    # A consolidated POS frame has several row grains. If the tabular planner
    # cannot answer, hand control to the registered domain KPI planner, which
    # applies POS-specific grain and relationship rules. Never aggregate the
    # mixed frame directly as though it were one ledger.
    txn_types_for_guard = frame.get("txn_type", pd.Series(dtype=str)).dropna().astype(str).str.casefold().unique()
    if len(txn_types_for_guard) > 1 and re.search(r"\b(sales?|revenue|transaction|product|medicine|inventory|stock|expiry|batch|profit|margin|purchase|vendor|supplier|discount|tax|payment|returned|return|refund|reorder|attention|money|value|quantity|units?)\b", q):
        table_values = sorted(frame["table_name"].dropna().astype(str).unique()) if "table_name" in frame else []
        logger.info(
            "tabular_query_handoff reason=mixed_grain_unhandled txn_types=%s tables=%s question=%r",
            sorted(txn_types_for_guard), table_values, question,
        )
        return None
    # A grouped database may contain sales, stock, catalog, and purchase rows
    # in one canonical frame. Keep ordinary sales aggregates on sales records;
    # cross-table inventory questions have already been handled above.
    sales_intent = bool(re.search(r"\b(sales?|sold|revenue|transactions?|invoices?|bills?|receipts?)\b", question, re.I))
    if sales_intent and "txn_type" in frame:
        kinds = frame["txn_type"].fillna("").astype(str).str.casefold()
        sales_rows = kinds.str.contains(r"sale|return|refund|invoice|bill|transaction|dispens", regex=True)
        if sales_rows.any():
            frame = frame.loc[sales_rows].copy()
    q = question.casefold()
    df = frame.copy()
    rows = set()
    if "source_row" in df:
        row_ids = df["source_row"]
    else:
        row_ids = pd.Series(df.index + 1, index=df.index)

    # Date filters, including natural month/year mentions and explicit dates.
    date_col = next((c for c in (("expiry_date", "date") if "expir" in q else ("date", "expiry_date")) if c in df), None)
    if date_col and (re.search(r"\b(on|during|in|from|between|before|after|year|expir\w*)\b", q) or (re.search(r"\b20\d{2}\b",q) and re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",q))):
        dates = pd.to_datetime(df[date_col], errors="coerce")
        iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", q)
        iso_dates = re.findall(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", q)
        month_names = {"january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,"may":5,"june":6,"jun":6,"july":7,"jul":7,"august":8,"aug":8,"september":9,"sep":9,"october":10,"oct":10,"november":11,"nov":11,"december":12,"dec":12}
        month = next((n for name,n in month_names.items() if re.search(rf"\b{name}\b",q)),None)
        year = re.search(r"\b(20\d{2})\b", q)
        natural_range=re.search(r"\b([a-z]+)\s+(\d{1,2})\s+(?:to|through|and|-)\s+(?:[a-z]+\s+)?(\d{1,2})\s*,?\s*(20\d{2})\b",q)
        if natural_range and natural_range.group(1) in month_names:
            mm=month_names[natural_range.group(1)]
            start=pd.Timestamp(int(natural_range.group(4)),mm,int(natural_range.group(2)))
            end=pd.Timestamp(int(natural_range.group(4)),mm,int(natural_range.group(3)))
            df=df.loc[(dates.dt.normalize()>=start)&(dates.dt.normalize()<=end)]
        elif len(iso_dates) >= 2:
            start=pd.Timestamp(*map(int,iso_dates[0])); end=pd.Timestamp(*map(int,iso_dates[1]))
            df=df.loc[(dates.dt.normalize()>=start)&(dates.dt.normalize()<=end)]
        elif iso and re.search(r"\b(on or before|before|by|on or earlier)\b",q):
            target=pd.Timestamp(int(iso.group(1)),int(iso.group(2)),int(iso.group(3)))
            df=df.loc[dates.dt.normalize()<=target]
        elif iso:
            target = pd.Timestamp(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            df = df.loc[dates.dt.date == target.date()]
        elif month and year:
            df = df.loc[(dates.dt.month == month) & (dates.dt.year == int(year.group(1)))]
        elif year:
            df = df.loc[dates.dt.year == int(year.group(1))]
        elif month:
            df = df.loc[dates.dt.month == month]

    if "quantity" in df and re.search(r"\b(out of stock|completely out of stock|zero stock|stock\s*=\s*0)\b",q):
        df=df[pd.to_numeric(df.quantity,errors="coerce")==0]
    if "quantity" in df and "reorder_level" in df and re.search(r"\b(below|under|less than)\s+(?:(?:the|their)\s+)?reorder",q):
        df=df[pd.to_numeric(df.quantity,errors="coerce")<pd.to_numeric(df.reorder_level,errors="coerce")]

    customer_match = re.search(r"\b(?:customer|client)\s+([a-z][a-z'-]+\s+[a-z][a-z'-]+)", q) if customer_history_intent else None
    if customer_match:
        name=customer_match.group(1).strip()
        customer_col=next((c for c in ("customer_id","customer_name","customer_alias") if c in df),None)
        if not customer_col:
            return {"answer":"The selected data does not contain a customer identity field, so I can't verify this customer's records.","values":{"status":"unsupported_field","matched_records":0},"source_rows":[]}
        matching=df[customer_col].fillna("").astype(str).str.casefold().eq(name.casefold())
        if not matching.any():
            return {"answer":f"No records were found for customer {name} in the selected data.","values":{"matched_records":0},"source_rows":[]}
        df=df.loc[matching]

    structured = _answer_schema_aggregate(question, df, row_ids)
    if structured is not None:
        return structured

    # Compare two explicitly named entities before ordinary entity filtering
    # reduces the table to just one side of the comparison.
    compare_phrase=bool(re.search(r"\b(which|who|what)\b.{0,70}\b(more|higher|greater|greater total|sold more|compare|versus|vs)\b",q))
    if compare_phrase:
        ccol=None; vals=[]
        for candidate in ("branch","warehouse","product_id","supplier_id","category"):
            if candidate in df:
                found=[str(v) for v in frame[candidate].dropna().unique() if len(str(v))>3 and str(v).casefold() in q]
                if len(found)>=2:
                    ccol=candidate; vals=found; break
        if ccol:
            if len(vals)>=2:
                is_below_reorder=bool(re.search(r"\b(below|under|less than)\s+(?:(?:the|their)\s+)?reorder",q))
                measure="__count__" if is_below_reorder else ("quantity" if re.search(r"\b(stock|units?|quantity|sold more)\b",q) else next((c for c in ("amount","invoice_total") if c in df),None))
                if measure:
                    out=[]
                    for name in vals[:2]:
                        part=df[df[ccol].astype(str).str.casefold()==name.casefold()]
                        if measure=="__count__":
                            valid=part[pd.to_numeric(part.quantity,errors="coerce")<pd.to_numeric(part.reorder_level,errors="coerce")]
                            total=len(valid); nums=pd.Series(valid.index)
                        else:
                            nums=pd.to_numeric(part[measure],errors="coerce").dropna(); total=nums.sum()
                        out.append((name,total,part,len(nums)))
                    first,second=out
                    answer="; ".join(f"{name}: {total:,.0f} ({count} matching of {len(frame[frame[ccol].astype(str).str.casefold()==name.casefold()])} records)" for name,total,_,count in out)
                    answer+=f". {max(out,key=lambda x:x[1])[0]} is higher by {abs(first[1]-second[1]):,.0f}."
                    src=[x for _,_,part,_ in out for x in row_ids.loc[part.index].tolist()]
                    return {"answer":answer,"values":{"comparison":{name:total for name,total,_,_ in out}},"source_rows":src}

    # Match explicit dimension values with longest-first matching to disambiguate names.
    dim_cols = [c for c in ("product_id","product_code","customer_id","supplier_id","branch","warehouse","category","status") if c in df]
    for col in dim_cols:
        if col == "status" and not re.search(r"\b(status|payment status|paid status|were paid|is paid|unpaid|partially paid|pending)\b",q):
            continue
        values = sorted((str(v) for v in df[col].dropna().unique() if str(v).strip()), key=len, reverse=True)
        hit = next((v for v in values if len(v) > 2 and re.search(rf"(?<!\w){re.escape(v.casefold())}(?!\w)",q)),None)
        if hit:
            df = df[df[col].astype(str).str.casefold() == hit.casefold()]
    # A named but absent customer/supplier must not fall back to all-file totals.
    for col, marker in (("customer_id", r"\bcustomer\s+([a-z][a-z'-]+\s+[a-z][a-z'-]+)"), ("supplier_id", r"\bsupplier\s+([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+)?)")):
        named = re.search(marker, q)
        if col == "customer_id" and not customer_history_intent:
            named = None
        stop_names={"had the","did they","does the","made the","supplied the","supplies the","provided the","is it","was it","has the","with the","from the","to the"}
        if named and named.group(1).casefold() in stop_names:
            named=None
        if named and col in frame and not any(str(v).casefold() in q for v in frame[col].dropna().unique()):
            return {"answer":f"No records were found for {named.group(1)} in the selected data.","values":{"matched_records":0},"source_rows":[]}

    # Numeric predicates are applied to the explicitly named field before a
    # list/count is formed (e.g. discount >= 2,250 or stock below reorder level).
    pred = re.search(r"\b(at least|greater than or equal to|more than|over|above|>=|greater than|less than|below|under|<=)\s*(?:rs\.?\s*)?([\d,]+(?:\.\d+)?)", q)
    named_pred = re.search(r"\b(discount|stock|quantity|amount|invoice[_ ]total|unit[_ ]cost)\b.{0,14}?(?:of|is|>=)?\s*([\d,]+(?:\.\d+)?)\s*(?:pkr|rs\.?|rupees?)?\s*(?:or more|and above|and up)", q)
    field = next((name for name, pats in (("discount",("discount",)),("quantity",("quantity","units sold")),("amount",("amount","revenue","sales")),("stock",("stock",)),("cost",("unit cost","cost"))) if any(p in q for p in pats)),None)
    if named_pred:
        field = {"invoice_total":"amount","unit_cost":"cost"}.get(named_pred.group(1).replace(" ","_"),named_pred.group(1))
    if (pred or named_pred) and field:
        col=field if field in df else {"amount":"invoice_total","stock":"quantity"}.get(field)
        if col in df:
            vals=pd.to_numeric(df[col],errors="coerce")
            op=pred.group(1).casefold() if pred else ""
            is_low=op in ("less than","below","under","<=")
            threshold=float((pred.group(2) if pred else named_pred.group(2)).replace(",",""))
            strict=op in ("greater than","more than","over","above")
            df=df[vals < threshold if is_low else vals > threshold if strict else vals <= threshold if op=="<=" else vals >= threshold]
    if not len(df):
        return {"answer":"No matching records were found in the selected data.","values":{"matched_records":0},"source_rows":[]}
    if re.search(r"\b(batch|lot)\b",q) and not any(c in frame for c in ("batch_no","batch_number","lot_no","lot_number")):
        if "expiry_date" in df and df.expiry_date.notna().any() and re.search(r"\b(expir|expires?|first|earliest)\b",q):
            exp=pd.to_datetime(df.expiry_date,errors="coerce")
            exp = exp.dropna()
            if exp.empty:
                return {"answer":"Expiry dates are not usable for the matching inventory records, so I can't identify the next batch to sell.","values":{"status":"unsupported_field","required_field":"expiry_date"},"source_rows":[]}
            idx=exp.idxmin(); row=df.loc[idx]
            record_id=row.get("product_code",row.get("product_id","inventory record"))
            return {"answer":f"No batch number is recorded. The earliest-expiring matching inventory record is {record_id}, expiring {exp.loc[idx].date()}.","values":{"expiry_date":str(exp.loc[idx].date()),"record_id":record_id,"status":"missing_batch_field"},"source_rows":[row_ids.loc[idx]]}
        return {"answer":"The selected records do not contain batch or lot identifiers, so I can't identify which batch expires first.","values":{"status":"missing_batch_field"},"source_rows":[]}

    if (pred or named_pred) and field and re.search(r"\b(which|list|show|how many|count)\b",q):
        id_col=next((c for c in ("invoice_id","transaction_id","product_code","product_id") if c in df),None)
        ids=df[id_col].dropna().astype(str).tolist() if id_col else []
        return {"answer":f"{len(df)} matching records"+(": "+", ".join(ids[:30]) if ids else "")+".","values":{"matched_records":len(df)},"source_rows":row_ids.loc[df.index].tolist()}

    # Multiple explicit inventory IDs indicate a bounded value calculation.
    identifiers=re.findall(r"\b(?:sale|sales|pur|purchase|inv|inventory|sku|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",q)
    if len(set(identifiers))>1:
        id_col=next((c for c in ("product_code","invoice_id","transaction_id","product_id") if c in frame),None)
        if id_col and re.search(r"\b(value|worth|cost|combined|total)\b",q) and "quantity" in frame and "cost" in frame:
            wanted={x.replace("_","-").casefold() for x in identifiers}
            chosen=frame[frame[id_col].astype(str).str.casefold().isin(wanted)]
            if len(chosen):
                value=(pd.to_numeric(chosen.quantity,errors="coerce")*pd.to_numeric(chosen.cost,errors="coerce")).sum()
                return {"answer":f"Combined inventory value: {value:,.0f}.","values":{"inventory_value":value,"matched_records":len(chosen)},"source_rows":row_ids.loc[chosen.index].tolist()}

    # Exact record identifier selection (never infer from a partial identifier).
    id_match = re.search(r"\b(?:sale|sales|pur|purchase|inv|inventory|sku|rec|receipt|invoice)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",q)
    if id_match:
        wanted=id_match.group(0).replace("_","-").casefold()
        id_cols=[c for c in ("invoice_id","transaction_id","product_code","product_id") if c in df]
        m=pd.Series(False,index=df.index)
        for c in id_cols: m |= df[c].astype(str).str.casefold().eq(wanted)
        df=df[m]
        if not len(df): return {"answer":f"No record with identifier {wanted.upper()} was found in the selected data.","values":{"matched_records":0},"source_rows":[]}
        row=df.iloc[0]
        requested=[]
        for pat,label,cols in ((r"customer","Customer",("customer_id",)),(r"product|item|medicine","Product",("product_id",)),(r"quantity|units|qty","Quantity",("quantity",)),(r"discount","Discount",("discount",)),(r"date","Date",("date",)),(r"branch|location|warehouse","Branch/Warehouse",("branch","warehouse")),(r"supplier|vendor","Supplier",("supplier_id",)),(r"expiry","Expiry",("expiry_date",)),(r"cost|unit cost","Unit Cost",("cost",)),(r"price","Unit Price",("unit_price",)),(r"status|paid|payment","Payment Status",("status",)),(r"amount|total|bill","Total Amount",("amount","invoice_total"))):
            if re.search(rf"\b{pat}\b",q): requested.append((label,next((row[c] for c in cols if c in row and pd.notna(row[c])),None)))
        if not requested: requested=[(str(c),row[c]) for c in df.columns if c not in ("source_row","source_file") and pd.notna(row[c])][:8]
        answer="; ".join(f"{label}: {value}" for label,value in requested if value is not None)
        return {"answer":answer,"values":{"matched_records":len(df)},"source_rows":row_ids.loc[df.index].tolist()}

    # Explicit comparisons compute each named entity over the same filtered rows.
    if re.search(r"\b(which|who|what).{0,30}\b(more|higher|greater|greater total|sold more|compare|versus|vs)\b",q) or re.search(r"\b(which|who)\s+\w+\s+had the greater\b",q):
        compare_col=next((c for c in ("branch","product_id","supplier_id","category") if c in frame),None)
        if compare_col and re.search(r"\b(branch|location|product|medicine|supplier|vendor|category|units?)\b",q):
            vals=[str(v) for v in frame[compare_col].dropna().unique() if str(v).casefold() in q]
            if len(vals)>=2:
                measure="quantity" if re.search(r"\b(units?|quantity|sold more)\b",q) else ("amount" if "amount" in frame else "invoice_total")
                if measure in frame:
                    out=[]
                    for name in vals[:2]:
                        subset=frame[frame[compare_col].astype(str).str.casefold()==name.casefold()]
                        total=pd.to_numeric(subset[measure],errors="coerce").sum()
                        out.append((name,total,subset))
                    winner=max(out,key=lambda x:x[1])
                    answer="; ".join(f"{name}: {value:,.0f}" for name,value,_ in out)+f". {winner[0]} is higher."
                    src=[]
                    for _,_,part in out: src.extend(row_ids.loc[part.index].tolist())
                    return {"answer":answer,"values":{"comparison":{name:value for name,value,_ in out}},"source_rows":src}

    if re.search(r"\b(earliest|latest|first|most recent)\b",q) and any(c in frame for c in ("date","expiry_date")):
        dcol="expiry_date" if "expiry_date" in frame and (
            "expir" in q or (re.search(r"\b(batch|batches|lot|lots)\b", q) and re.search(r"\b(first|earliest|sold first)\b", q))
        ) else "date"
        ds=pd.to_datetime(frame[dcol],errors="coerce")
        if not ds.notna().any():
            required = "expiry_date" if dcol == "expiry_date" else "date"
            return {"answer": f"The selected records do not contain usable {required.replace('_', ' ')} values, so I can't determine which record comes first.", "values": {"status": "unsupported_field", "required_field": required}, "source_rows": []}
        idxs=[]
        if re.search(r"\b(earliest|first)\b",q): idxs.append(("Earliest",ds.idxmin()))
        if re.search(r"\b(latest|most recent)\b",q): idxs.append(("Latest",ds.idxmax()))
        if not idxs: idxs.append(("Recorded",ds.idxmin() if re.search(r"\bearliest\b",q) else ds.idxmax()))
        parts=[]; result={}; src=[]
        for label,idx in idxs:
            row=frame.loc[idx]; rid=row.get("invoice_id",row.get("product_code","record")); when=str(ds.loc[idx].date())
            details=[str(row[c]) for c in ("customer_id","product_id","branch","warehouse") if c in row and pd.notna(row[c])]
            if "amount" in row and pd.notna(row["amount"]): details.append(f"amount {row['amount']:,.0f}")
            parts.append(f"{label}: {rid} on {when}"+(f" ({'; '.join(details)})" if details else ""))
            result[label.casefold()]={"date":when,"record_id":rid}; src.append(row_ids.loc[idx])
        return {"answer":"; ".join(parts)+".","values":result,"source_rows":src}

    # "Highest/lowest" asks for an extremum over the already-filtered rows.
    if re.search(r"\b(highest|largest|most|most often|bought most|sold the most|most units|least|lowest|smallest)\b",q):
        if re.search(r"\b(single purchase|highest purchase|largest purchase|highest invoice total|highest bill)\b",q):
            amount_col=next((c for c in ("amount","invoice_total") if c in df and pd.to_numeric(df[c],errors="coerce").notna().any()),None)
            if amount_col:
                idx=pd.to_numeric(df[amount_col],errors="coerce").idxmax(); row=df.loc[idx]
                bits=[f"{c.replace('_',' ').title()}: {row[c]}" for c in ("invoice_id","customer_id","product_id","supplier_id","branch","status") if c in row and pd.notna(row[c])]
                bits.append(f"Amount: {float(row[amount_col]):,.0f}")
                return {"answer":"Highest single purchase: "+"; ".join(bits)+".","values":{"amount":float(row[amount_col])},"source_rows":[row_ids.loc[idx]]}
        if re.search(r"\b(which|what)\s+product\b.{0,50}\b(most often|bought most)\b",q) and "product_id" in df:
            counts=df.groupby("product_id",dropna=True).size().sort_values(ascending=False)
            if len(counts):
                name=str(counts.index[0]); part=df[df.product_id.astype(str)==name]
                return {"answer":f"Most frequently purchased product: {name} ({int(counts.iloc[0])} purchases).","values":{"count":int(counts.iloc[0]),"product":name},"source_rows":row_ids.loc[part.index].tolist()}
        group_col=next((c for c,words in (("warehouse",("warehouse",)),("branch",("branch",)),("category",("category",)),("supplier_id",("supplier","vendor")),("product_id",("product",))) if c in frame and any(w in q for w in words)),None)
        if re.search(r"\b(lowest|least|smallest)\b",q) and "quantity" in df and re.search(r"\b(stock|quantity|units?)\b",q) and not group_col:
            idx=pd.to_numeric(df.quantity,errors="coerce").idxmin(); row=df.loc[idx]
            rid=row.get("product_code",row.get("invoice_id",row.get("product_id","record")))
            return {"answer":f"{rid}: {row.get('product_id','')} has the lowest stock ({row.quantity:g} units).","values":{"lowest_stock":float(row.quantity)},"source_rows":[row_ids.loc[idx]]}
        if group_col:
            if "reorder_level" in frame and re.search(r"\b(item|items|count|most)\b",q):
                eligible=df[pd.to_numeric(df.quantity,errors="coerce")<pd.to_numeric(df.reorder_level,errors="coerce")] if "quantity" in df else df
                counts=eligible.groupby(group_col,dropna=True).size().sort_values(ascending=bool(re.search(r"\b(least|lowest|smallest)\b",q)))
                if len(counts):
                    name=str(counts.index[0]); part=eligible[eligible[group_col].astype(str)==name]
                    return {"answer":f"{name} has the most items below reorder level ({int(counts.iloc[0])}).","values":{"group":name,"count":int(counts.iloc[0])},"source_rows":row_ids.loc[part.index].tolist()}
            metric="quantity" if "quantity" in frame and re.search(r"\b(stock|units?|quantity|sold|holds|purchase)\b",q) else next((c for c in ("amount","invoice_total") if c in frame),None)
            if metric:
                sums=pd.to_numeric(df[metric],errors="coerce").groupby(df[group_col]).sum().sort_values(ascending=bool(re.search(r"\b(least|lowest|smallest)\b",q)))
                if len(sums):
                    name=str(sums.index[0]); part=df[df[group_col].astype(str)==name]
                    return {"answer":f"{name}: {sums.iloc[0]:,.0f} ({len(part)} records).","values":{"group":name,"value":float(sums.iloc[0])},"source_rows":row_ids.loc[part.index].tolist()}

    # Numeric aggregates; select the requested measure, not an unrelated default KPI.
    list_like=bool(re.search(r"\b(list|show all|show me all|which|every|all sales|all purchases)\b",q))
    if not list_like and re.search(r"\b(total|sum|average|avg|mean|how much|how many|spend|amount|revenue|sales|combined|invoice[_ ]total|unit[_ ]cost)\b",q) and any(c in df for c in ("amount","invoice_total","quantity","discount","cost")):
        labels=[]; values={}
        for label, patterns, col in (("Total quantity",("quantity","units sold","how many units","total quantity","total stock","stock","on hand"),"quantity"),("Total amount",("amount","revenue","sales","spend","how much","purchase value","invoice_total","invoice total","combined"),"amount")):
            if any(p in q for p in patterns):
                actual=col if col in df and pd.to_numeric(df[col],errors="coerce").notna().any() else ("invoice_total" if col=="amount" and "invoice_total" in df else None)
                if actual:
                    nums=pd.to_numeric(df[actual],errors="coerce").dropna()
                    val=nums.sum()
                    if float(val).is_integer(): val=int(val)
                    values[label.lower().replace(" ","_")]=val; labels.append(f"{label}: {val:,}")
        if re.search(r"\b(average|avg|mean)\b",q):
            col="cost" if re.search(r"\b(unit[_ ]cost|cost paid|cost per)\b",q) and "cost" in df else ("amount" if "amount" in df else ("invoice_total" if "invoice_total" in df else None))
            if col:
                avg=pd.to_numeric(df[col],errors="coerce").mean()
                if pd.notna(avg):
                    if float(avg).is_integer(): avg=int(avg)
                    values["average"]=avg; labels.append(f"Average unit cost: {avg:,.2f}".rstrip("0").rstrip("."))
        if re.search(r"\b(how many|count|number of|purchases?|purchased)\b",q):
            labels.append(f"Matching records: {len(df)}"); values["matched_records"]=len(df)
        elif len(df)>1 and (len(dim_cols) or date_col):
            labels.append(f"Across {len(df)} records")
        if labels:
            return {"answer":"; ".join(labels)+".","values":values,"source_rows":row_ids.loc[df.index].tolist()}
    if re.search(r"\b(phone|telephone|contact number|email|contact details)\b",q) and not any(c in frame for c in ("phone","phone_number","telephone","email","contact_number")):
        return {"answer":"The selected data does not contain the requested contact field, so I can’t provide it.","values":{"status":"unsupported_field"},"source_rows":[]}
    if re.search(r"\b(how many|count|number of|list|show me|which|all|every)\b",q):
        if "inventory item" in q and "product_id" in df and not re.search(r"\b(record|row|batch)\b",q):
            count=int(df.product_id.dropna().astype(str).nunique())
            return {"answer":f"{count} distinct inventory items.","values":{"distinct_items":count},"source_rows":row_ids.loc[df.index].tolist()}
        if re.search(r"\b(list|show me|which|all|every)\b",q):
            col=next((c for c in ("invoice_id","product_code","product_id","supplier_id","customer_id") if c in df),None)
            if col:
                vals=df[col].dropna().astype(str).drop_duplicates().tolist()[:30]
                return {"answer":f"{len(df)} matching records: " + "; ".join(vals),"values":{"matched_records":len(df)},"source_rows":row_ids.loc[df.index].tolist()}
        return {"answer":f"{len(df)} matching records.","values":{"matched_records":len(df)},"source_rows":row_ids.loc[df.index].tolist()}
    measure = None
    if re.search(r"\b(stock|on hand|inventory|units left|quantity)\b",q): measure="quantity"
    elif re.search(r"\b(discount)\b",q): measure="discount"
    elif re.search(r"\b(unit cost|cost per unit|average cost)\b",q): measure="cost"
    elif re.search(r"\b(unit price|price)\b",q): measure="unit_price"
    elif re.search(r"\b(amount|revenue|sales|spend|purchase|total|value|paid)\b",q): measure="amount"
    if measure and measure not in df:
        alt={"amount":("invoice_total",),"quantity":("stock",),"cost":("unit_cost",)}.get(measure,())
        measure=next((c for c in alt if c in df),measure)
    if measure and measure in df:
        nums=pd.to_numeric(df[measure],errors="coerce").dropna()
        if len(nums):
            if re.search(r"\b(average|avg|mean)\b",q): value=float(nums.mean()); label=f"Average {measure.replace('_',' ')}"
            else: value=float(nums.sum()); label=f"Total {measure.replace('_',' ')}"
            if value.is_integer(): value=int(value)
            return {"answer":f"{label}: {value:,}.","values":{label.lower().replace(' ','_'):value},"source_rows":row_ids.loc[df.loc[nums.index].index].tolist()}
    return None


def _current_pharmacy_question(question: str) -> str:
    """Use the newest user wording when chat follow-up context was appended."""
    text = str(question)
    marker = re.search(r"\bcurrent follow-up\s*:\s*", text, re.I)
    if not marker:
        return text
    current = text[marker.end():].strip()
    previous = re.search(r"\bprevious analysis question\s*:\s*(.*?)\.\s*current follow-up\s*:", text, re.I | re.S)
    if previous and re.search(r"\b(next|second|runner[- ]?up|number two)\b", current, re.I):
        prior = previous.group(1).casefold()
        dimension = re.search(r"\bby\s+(rack|warehouse|location|branch|category|manufacturer|product)\b", prior)
        if re.search(r"\b(count|counts|how many|number of)\b", prior) and dimension:
            condition = " below reorder" if re.search(r"\b(below|under|less than)\b.{0,25}\breorder\b", prior) else ""
            current += f"; continue the same{condition} counts by {dimension.group(1)}, selecting the next distinct count and including ties"
    return current


def _pharmacy_inventory_demand_intent(question: str):
    """Map demand/stock questions to a calculation supported by joined data."""
    q = re.sub(r"\s+", " ", _current_pharmacy_question(question).casefold()).strip()
    if re.search(r"\b(profit(?:able|ability)?|profitable|margin)\b", q) and re.search(r"\b(stockout|out of stock|going out of stock|risk|reorder|low stock)\b", q):
        return None
    reorder_words = r"\b(reorder|re-order|restock|order(?:ing)? more|buy more)\b"
    demand_words = r"\b(demand|fast|quick|selling|sell(?:ing)?|velocity|popular|high[- ]?demand|frequent)\b"
    if re.search(r"\b(repeatedly|repeated|keep|keeps|kept|constantly|frequently|often)\b.{0,50}\b(low|running low|out of stock|stockout|reorder)\b|\b(repeatedly|constantly|frequently)\b.{0,40}\b(reorder|stockout|low stock)\b", q):
        return "recurring_low_stock"
    if re.search(r"\b(increase|raise|adjust|set|improve|larger|higher)\b.{0,35}\b(reorder|re-order|restock|order)\b.{0,25}\b(quantity|level|point|amount|size)\b|\b(reorder|re-order|restock|order)\b.{0,30}\b(quantity|level|point|amount|size)\b.{0,30}\b(increase|raise|adjust|historical demand|sales|larger|higher)\b", q):
        return "reorder_quantity"
    if re.search(r"\b(not sold|never sold|no sales|zero sales|no recorded sales)\b", q) and re.search(r"\b(recent|lately|inventory|stock|occupying|sitting|unsold|period|days?|weeks?|months?)\b", q):
        return "no_recent_sales"
    future_stockout = bool(re.search(
        r"\b(may|might|could|will|likely to|about to|expected to|projected to)\b.{0,35}\b(go(?:ing)? out of stock|run(?:ning)? out of stock|stockout soon)\b",
        q,
    ))
    if future_stockout or re.search(r"\b(at risk|risk of|chance of)\b.{0,35}\b(stockout|stock out|running out|run out|going out of stock)\b", q):
        return "runout_risk"
    current_stockout = bool(re.search(r"\b(out of stock|zero stock|stock\s*=\s*0|stockout)\b", q)) and not future_stockout
    if current_stockout and re.search(demand_words, q):
        return "high_demand_out_of_stock"
    low_stock_vs_demand = (
        re.search(r"\b(low|little|limited|declining)\b.{0,30}\b(remaining\s+)?(stock|inventory|quantity)\b", q)
        and re.search(r"\b(sales?|sell(?:ing)?|demand|velocity|quick(?:ly)?|fast)\b", q)
    )
    if low_stock_vs_demand:
        return "runout_risk"
    if re.search(r"\b(overstock(?:ed)?|too much stock|excess(?:ive)? inventory|high inventory|large inventory)\b", q) or (
        re.search(r"\b(high|large|significant)\b.{0,20}\b(stock|inventory)\b", q)
        and re.search(r"\b(low|little|very low|slow|actual)\b.{0,20}\b(sales?|demand|velocity)\b", q)
    ):
        return "overstock"
    if re.search(r"\b(run out|running out|stockout|stock out|days? of (?:stock|supply|cover)|stock cover|only.{0,20}days? (?:left|of stock|supply)|few more days|shortly|soon|(?:less than|under|below)\s+(?:a\s+)?week(?:'s)?\s+(?:of\s+)?(?:stock|supply|cover))\b", q):
        return "runout_risk"
    if (re.search(reorder_words, q) or re.search(r"\b(below|under|less than)\b.{0,25}\b(minimum|reorder)\s+(?:stock|level|point)\b", q)) and re.search(demand_words + r"|\b(first|prioriti[sz]e|below.{0,20}(?:reorder|minimum)|reorder.{0,20}level)\b", q):
        return "reorder_priority"
    return None


def is_cross_table_pharmacy_risk_question(question: str) -> bool:
    """Identify pharmacy questions that require a sales/inventory product join or intelligence analysis."""
    try:
        from app.analytics.pharmacy_intelligence import is_cross_table_pharmacy_risk_question as _pi_check
        if _pi_check(question):
            return True
    except Exception:
        pass
    q = re.sub(r"\s+", " ", _current_pharmacy_question(question).casefold()).strip()
    demand_intent = _pharmacy_inventory_demand_intent(question)
    reorder_request = demand_intent == "reorder_priority"
    expiry_slow_request = bool(
        re.search(r"\b(expir\w*|near[- ]?expiry)\b", q)
        and re.search(r"\b(slow(?:ly)?|poor sales|low sales|no sales|clear|sell(?:ing)?|velocity)\b", q)
    )
    profit_risk_request = bool(
        re.search(r"\b(profit(?:able|ability)?|profitable|margin)\b", q)
        and re.search(r"\b(stockout|out of stock|going out of stock|risk|reorder|low stock)\b", q)
    )
    return demand_intent is not None or expiry_slow_request or profit_risk_request


def _semantic_columns(frame: pd.DataFrame, names):
    """Find canonical or preserved raw columns by normalized business name."""
    wanted = {re.sub(r"[^a-z0-9]+", "", str(name).casefold()) for name in names}
    found = []
    for column in frame.columns:
        label = re.sub(r"^_extra\.", "", str(column), flags=re.I)
        normalized = re.sub(r"[^a-z0-9]+", "", label.casefold())
        if normalized in wanted:
            found.append(column)
    return found



def _answer_schema_composition(question: str, frame: pd.DataFrame, row_ids: pd.Series):
    """Answer high-confidence inventory/payment compositions from live field roles.

    Field aliases and row roles are discovered from the selected frame. Batch rows
    are reduced to product grain for stock thresholds; dated expiry/value questions
    remain at batch grain. The helper deliberately declines when a requested role
    or measure cannot be established.
    """
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    def col(aliases):
        return next((name for name in _semantic_columns(frame, aliases) if frame[name].notna().any()), None)
    def cites(part):
        return row_ids.loc[part.index].dropna().tolist()
    def truth(series):
        nums = pd.to_numeric(series, errors="coerce")
        words = series.fillna("").astype(str).str.strip().str.casefold()
        return nums.gt(0) | words.isin({"true", "yes", "y", "returned", "return", "refunded", "refund", "visible", "1"})

    # Scope sources by populated provenance values when possible, then by
    # independent inventory field signatures for a single CSV/XLSX table.
    table_col = col(("table_name", "source_table", "sheet_name"))
    db_col = col(("database_name", "source_group", "data_group"))
    table_values = frame[table_col].fillna("").astype(str).str.casefold() if table_col else pd.Series("", index=frame.index)
    db_values = frame[db_col].fillna("").astype(str).str.casefold() if db_col else pd.Series("", index=frame.index)
    inventory_tokens = r"inventory|inventor|stock|batch|product.?master|on.?hand"
    excluded_tokens = r"purchase|archive|histor|movement|ledger|transfer"
    inventory_mask = pd.Series(False, index=frame.index)
    if db_col and db_values.str.contains(inventory_tokens, regex=True).any():
        inventory_mask = db_values.str.contains(inventory_tokens, regex=True)
    elif table_col and table_values.str.contains(inventory_tokens, regex=True).any():
        inventory_mask = table_values.str.contains(inventory_tokens, regex=True)
    else:
        expiry_sig = col(("expiry_date", "expiration_date", "expires_at", "expiry"))
        reorder_sig = col(("reorder_level", "minimum_stock", "reorder_point", "min_stock", "restock_level"))
        stock_sig = col(("stock_qty", "on_hand", "quantity_on_hand", "current_stock", "available_qty", "stock_on_hand"))
        if not stock_sig and expiry_sig and reorder_sig:
            stock_sig = col(("quantity", "qty", "units"))
        signature = [x for x in (expiry_sig, reorder_sig) if x]
        if signature:
            # Expiry/reorder metadata are strong row-role signals. Quantity alone
            # is not: sales and purchase detail rows also carry a quantity.
            inventory_mask = frame[signature].notna().any(axis=1)
            if stock_sig:
                inventory_mask &= frame[stock_sig].notna()
            else:
                quantity_sig = col(("quantity", "qty", "units"))
                if quantity_sig:
                    inventory_mask &= frame[quantity_sig].notna()
        elif stock_sig:
            inventory_mask = frame[stock_sig].notna()
        if table_col and table_values.str.contains(excluded_tokens, regex=True).any():
            inventory_mask &= ~table_values.str.contains(excluded_tokens, regex=True)
    if table_col and table_values.str.contains(excluded_tokens, regex=True).any():
        inventory_mask &= ~table_values.str.contains(excluded_tokens, regex=True)
    if db_col and db_values.str.contains(r"purchase|sales|expense", regex=True).any():
        inventory_mask &= ~db_values.str.contains(r"purchase|sales|expense", regex=True)
    inventory = frame.loc[inventory_mask].copy()
    purchase_col = col(("purchase_order_no", "purchase_order_id", "po_number"))
    if purchase_col and not inventory.empty:
        po = inventory[purchase_col].fillna("").astype(str).str.strip()
        inventory = inventory.loc[po.eq("")]

    # Header-level payment measures: select populated payment/header rows and
    # collapse repeated invoice headers before summing.
    paid_col = col(("paid_amount", "amount_paid", "amount_received", "payment_received", "received_amount", "paid_total"))
    invoice_col = col(("invoice_total", "receipt_total", "transaction_total", "total_amount", "sales_total", "grand_total"))
    balance_col = col(("outstanding_balance", "balance_due", "amount_due", "customer_balance", "receivable_balance", "balance"))
    method_col = col(("payment_method", "payment_type", "payment_mode", "tender_type", "tender"))
    payment_question = bool(re.search(r"\b(received|money from customers|amount paid|paid percentage|percentage of sales.*paid|cash sales?.{0,20}non[- ]?cash|non[- ]?cash sales?)\b", q))
    balance_question = bool(re.search(r"\b(outstanding|unpaid|amount due|balance due|receivable|owed|still due|still owing)\b", q))
    if payment_question or balance_question:
        required = [c for c in (paid_col, invoice_col, balance_col, method_col) if c]
        if required:
            work = frame.loc[frame[required].notna().any(axis=1)].copy()
            if table_col and table_values.str.contains(r"sales|invoice|bill|receipt|transaction", regex=True).any():
                sales_mask = table_values.str.contains(r"sales|invoice|bill|receipt|transaction", regex=True)
                if (sales_mask & work.index.to_series().map(lambda i: bool(inventory_mask.loc[i]))).any():
                    pass
                if sales_mask.any():
                    work = work.loc[sales_mask.loc[work.index]]
            id_col = col(("invoice_id", "receipt_id", "transaction_id", "bill_id", "order_id", "invoice_number", "receipt_number"))
            source_identity = next((c for c in ("database_name", "source_file", "file_id") if c in work and work[c].notna().any()), None)
            if id_col:
                work = work.loc[work[id_col].notna()]
                grain = [source_identity, id_col] if source_identity else [id_col]
                work = work.drop_duplicates(grain, keep="first")
            elif all(c in work for c in ("_header_source_file", "_header_source_row")):
                work = work.drop_duplicates(["_header_source_file", "_header_source_row"], keep="first")
            amount_col = paid_col or invoice_col
            if balance_question:
                if balance_col:
                    vals = pd.to_numeric(work[balance_col], errors="coerce").fillna(0)
                    measure = "recorded outstanding balance"
                elif paid_col and invoice_col:
                    vals = (pd.to_numeric(work[invoice_col], errors="coerce") - pd.to_numeric(work[paid_col], errors="coerce")).clip(lower=0).fillna(0)
                    measure = "invoice total minus amount paid"
                else:
                    vals = None
                if vals is not None:
                    total = float(vals.sum())
                    return {"answer": f"Total {measure}: {total:,.2f} across {int(vals.gt(0).sum())} receipts with a balance due.", "values": {"status": "ok", "total_outstanding_balance": total, "receipts_checked": len(work), "balance_field": balance_col or "invoice total minus amount paid"}, "source_rows": cites(work)}
            if re.search(r"\b(cash sales?.{0,20}non[- ]?cash|non[- ]?cash sales?)\b", q) and method_col:
                amount = invoice_col or paid_col
                if amount:
                    values = pd.to_numeric(work[amount], errors="coerce").fillna(0)
                    methods = work[method_col].fillna("Unknown").astype(str).str.strip()
                    cash = methods.str.contains(r"\bcash\b", case=False, regex=True) & ~methods.str.contains(r"credit|non.?cash", case=False, regex=True)
                    cash_total = float(values.loc[cash].sum())
                    noncash_total = float(values.loc[~cash].sum())
                    return {"answer": f"Cash sales: {cash_total:,.2f}; non-cash sales: {noncash_total:,.2f}.", "values": {"status": "ok", "cash_sales": cash_total, "non_cash_sales": noncash_total, "payment_field": method_col, "amount_field": amount}, "source_rows": cites(work)}
            if paid_col and re.search(r"\b(percent(?:age)?|what proportion)\b", q):
                paid = float(pd.to_numeric(work[paid_col], errors="coerce").fillna(0).sum())
                gross = float(pd.to_numeric(work[invoice_col], errors="coerce").fillna(0).sum()) if invoice_col else 0.0
                if gross > 0:
                    pct = paid / gross * 100
                    return {"answer": f"{pct:.2f}% of recorded invoice totals has been paid ({paid:,.2f} of {gross:,.2f}).", "values": {"status": "ok", "paid_amount": paid, "invoice_total": gross, "paid_percentage": pct}, "source_rows": cites(work)}
            if paid_col and re.search(r"\b(received|money from customers|amount paid)\b", q):
                total = float(pd.to_numeric(work[paid_col], errors="coerce").fillna(0).sum())
                return {"answer": f"Total recorded payments received: {total:,.2f} across {len(work)} receipts.", "values": {"status": "ok", "amount_received": total, "receipt_count": len(work), "payment_field": paid_col}, "source_rows": cites(work)}

    # Return flags are interpreted by values, independent of connector spelling.
    returned_col = col(("returned", "is_returned", "return_flag", "was_returned", "returned_item", "refunded"))
    if returned_col and re.search(r"\b(returned|returns|refunded|refunds)\b", q) and re.search(r"\b(which|what|how many|count|products|items|often|most)\b", q):
        mask = truth(frame[returned_col])
        returned = frame.loc[mask].copy()
        product_col = col(("product_name", "medicine_name", "item_name", "product", "item", "product_id"))
        if product_col and not returned.empty:
            counts = returned.groupby(product_col, dropna=True).size().sort_values(ascending=False)
            details = [{"product": str(k), "returned_lines": int(v)} for k, v in counts.items()]
            answer = "Returned products: " + "; ".join(f"{x['product']} ({x['returned_lines']})" for x in details[:20]) + "."
        else:
            details = []
            answer = f"No returned items are recorded ({int(mask.sum())} matching rows)." if not mask.any() else f"{int(mask.sum())} returned rows are recorded."
        return {"answer": answer, "values": {"status": "ok", "returned_lines": int(mask.sum()), "products": details, "return_field": returned_col}, "source_rows": cites(returned if not returned.empty else frame)}

    if inventory.empty:
        return None
    stock_col = col(("stock_qty", "closing_stock_qty", "available_qty", "on_hand", "current_stock", "current_quantity", "quantity_on_hand", "stock_on_hand", "inventory_quantity", "stock_balance", "quantity", "qty"))
    if not stock_col or not inventory[stock_col].notna().any():
        return None
    product_key = next((c for c in _semantic_columns(inventory, ("product_id", "product_code", "sku", "barcode", "product_name", "medicine_name", "item_name", "product", "item")) if inventory[c].notna().any()), None)
    product_name = next((c for c in _semantic_columns(inventory, ("product_name", "medicine_name", "item_name", "product", "item", "description")) if inventory[c].notna().any()), product_key)
    reorder_col = col(("reorder_level", "minimum_stock", "reorder_point", "min_stock", "minimum_quantity", "restock_level", "low_stock_threshold"))
    qty = pd.to_numeric(inventory[stock_col], errors="coerce")
    if product_key:
        inventory = inventory.loc[qty.notna()].copy()
        inventory["_dynamic_stock"] = pd.to_numeric(inventory[stock_col], errors="coerce")
        aggregation = {"_dynamic_stock": "sum"}
        if reorder_col:
            inventory["_dynamic_reorder"] = pd.to_numeric(inventory[reorder_col], errors="coerce")
            aggregation["_dynamic_reorder"] = "max"
        stock_by_product = inventory.groupby(product_key, dropna=True, sort=False).agg(aggregation)
        names = inventory.groupby(product_key, dropna=True, sort=False)[product_name].agg(lambda x: str(x.dropna().iloc[0]) if x.notna().any() else str(x.name)) if product_name else pd.Series({k: str(k) for k in stock_by_product.index})
    else:
        return None
    expiry_col = col(("expiry_date", "expiration_date", "expires_at", "expiry"))
    expiry_question = bool(re.search(r"\b(expir\w*|near[- ]?expiry|expired)\b", q))
    horizon_match = re.search(r"\b(?:next|within|coming|in|over the next)\s+(\d{1,3})\s*(?:days?|din)\b", q)
    if expiry_question:
        # Cross-metric expiry forecasts remain in the sales/inventory engine.
        if re.search(r"\b(sales?|sold|velocity|unsold|slow|poor sales|low sales|profit|margin|risk of remaining)\b", q):
            return None
        if not expiry_col:
            return {"answer": "The selected inventory has no populated expiry-date field.", "values": {"status": "unsupported_field", "required_field": "expiry_date"}, "source_rows": []}
        query_name_col = product_name or product_key
        if query_name_col:
            product_names = [str(value).strip() for value in inventory[query_name_col].dropna().unique()]
            exact_names = [value for value in product_names if len(value) >= 3 and re.search(rf"(?<!\w){re.escape(value.casefold())}(?!\w)", q)]
            if exact_names:
                chosen_name = max(exact_names, key=len)
                inventory = inventory.loc[inventory[query_name_col].astype(str).str.casefold().eq(chosen_name.casefold())].copy()
        expiries = pd.to_datetime(inventory[expiry_col], errors="coerce").dt.normalize()
        if (product_key and re.search(r"\b(multiple|different|various|more than one)\b", q)
                and re.search(r"\b(batch|batches|expiry dates?|expiration dates?)\b", q)):
            inventory["_dynamic_expiry"] = expiries
            distinct_dates = inventory.dropna(subset=[product_key, "_dynamic_expiry"]).groupby(product_key)["_dynamic_expiry"].nunique()
            matching_keys = distinct_dates.index[distinct_dates.gt(1)]
            products = [str(names.get(key, key)) for key in matching_keys]
            matching_rows = inventory.loc[inventory[product_key].isin(matching_keys)]
            answer = f"{len(products)} products have batches with different expiry dates: " + (", ".join(products) if products else "none") + "."
            return {"answer": answer, "values": {"status": "ok", "product_count": len(products), "products": products, "expiry_field": expiry_col}, "source_rows": cites(matching_rows)}
        today = pd.Timestamp.today().normalize()
        if re.search(r"\b(expired|already past|past expiry)\b", q):
            selected_mask = expiries.lt(today)
            horizon_days = None
        elif horizon_match:
            horizon_days = int(horizon_match.group(1))
            selected_mask = expiries.between(today, today + pd.Timedelta(days=horizon_days), inclusive="both")
        elif re.search(r"\b(near[- ]?expiry|expiring soon|approaching expiry)\b", q):
            return {"answer": "What expiry horizon should I use (for example, the next 30, 60, or 90 days)?", "values": {"status": "clarification", "required_parameter": "expiry_horizon_days"}, "source_rows": []}
        else:
            return None
        selected = inventory.loc[selected_mask & pd.to_numeric(inventory[stock_col], errors="coerce").gt(0)].copy()
        selected["_expiry"] = expiries.loc[selected.index]
        if re.search(r"\b(highest|most|largest|maximum)\b", q) and re.search(r"\b(stock|quantity|units?)\b", q):
            selected["_dynamic_quantity"] = pd.to_numeric(selected[stock_col], errors="coerce").fillna(0)
            totals = selected.groupby(product_key, dropna=True)["_dynamic_quantity"].sum().sort_values(ascending=False)
            if not totals.empty:
                top_value = float(totals.iloc[0])
                top_keys = totals.index[totals.eq(top_value)]
                products = [str(names.get(key, key)) for key in top_keys]
                return {"answer": f"Highest near-expiry stock quantity is {top_value:g} units for " + ", ".join(products) + f" within the requested {horizon_days}-day horizon.", "values": {"status": "ok", "maximum_quantity": top_value, "products": products, "horizon_days": horizon_days, "quantity_field": stock_col}, "source_rows": cites(selected.loc[selected[product_key].isin(top_keys)])}
        cost_request = bool(re.search(r"\b(cost|purchase value|at cost)\b", q))
        retail_request = bool(re.search(r"\b(retail|mrp|selling price|worth|retail value)\b", q))
        asks_value = bool(re.search(r"\b(total|how much|value|worth|money|cost|price)\b", q))
        if asks_value:
            price_col = col(("unit_cost", "cost_price", "purchase_price", "trade_price", "cost")) if cost_request else None
            if not cost_request or retail_request:
                price_col = col(("mrp", "retail_price", "selling_price", "unit_price", "price"))
            if not price_col:
                return {"answer": "The selected inventory has no populated unit-cost or retail-price field for this value calculation.", "values": {"status": "unsupported_field", "required_field": "unit cost" if cost_request else "retail price"}, "source_rows": []}
            total = float((pd.to_numeric(selected[stock_col], errors="coerce") * pd.to_numeric(selected[price_col], errors="coerce")).sum())
            label = "cost" if cost_request and not retail_request else "retail"
            period = f"the next {horizon_days} days" if horizon_days is not None else "already expired items"
            return {"answer": f"Recorded {label} value of positive-stock items expiring in {period}: {total:,.2f}.", "values": {"status": "ok", "value": total, "value_basis": label, "unit_price_field": price_col, "quantity_field": stock_col, "horizon_days": horizon_days, "matching_batches": len(selected)}, "source_rows": cites(selected)}
        if selected.empty:
            return {"answer": "No positive-stock inventory batches match that expiry period.", "values": {"status": "ok", "matching_batches": 0, "horizon_days": horizon_days}, "source_rows": []}
        display_col = product_name or product_key
        batch_col = col(("batch_no", "batch_number", "lot_no", "lot_number"))
        rack_col = col(("rack_location", "rack", "warehouse", "location", "shelf"))
        selected = selected.sort_values("_expiry")
        details = []
        for _, row in selected.head(100).iterrows():
            details.append({"product": str(row.get(display_col, "inventory item")), "expiry_date": row["_expiry"].date().isoformat(), "stock": float(pd.to_numeric(pd.Series([row[stock_col]]), errors="coerce").iloc[0]), "batch": row.get(batch_col) if batch_col else None, "location": row.get(rack_col) if rack_col else None})
        body = "; ".join(f"{x['product']} ({x['stock']:g} units, expires {x['expiry_date']}{', batch ' + str(x['batch']) if x['batch'] else ''}{', ' + str(x['location']) if x['location'] else ''})" for x in details)
        return {"answer": f"{len(selected)} positive-stock batches match the expiry period: {body}{' (first 100 shown)' if len(selected)>100 else ''}.", "values": {"status": "ok", "matching_batches": len(selected), "matching_products": int(selected[product_key].nunique()), "horizon_days": horizon_days, "batches": details}, "source_rows": cites(selected)}

    inventory_question = bool(re.search(r"\b(stock|inventory|on[- ]hand|reorder|restock|in stock|out of stock|low stock|understock|medicine|product)\b", q))
    if not inventory_question or re.search(r"\b(sales?|revenue|sold|purchase|purchased|profit|margin)\b", q):
        return None
    show_on_pos = col(("show_on_pos", "visible_on_pos", "pos_visible", "sellable", "available_for_sale"))
    if show_on_pos and re.search(r"\b(hidden|visible|shown|show on pos|available on pos)\b", q):
        visible = truth(inventory[show_on_pos])
        hidden = inventory.loc[~visible]
        return {"answer": f"{int((~visible).sum())} inventory records are hidden from POS; {int(visible.sum())} are marked visible.", "values": {"status": "ok", "hidden_records": int((~visible).sum()), "visible_records": int(visible.sum()), "visibility_field": show_on_pos}, "source_rows": cites(hidden if not hidden.empty else inventory)}
    name_text_col = product_name or product_key
    named = None
    if name_text_col:
        available_names = [str(v) for v in inventory[name_text_col].dropna().unique()]
        matches = [v for v in available_names if len(v.strip()) >= 3 and re.search(rf"(?<!\w){re.escape(v.casefold())}(?!\w)", q)]
        if matches:
            named = max(matches, key=len)
            inventory = inventory.loc[inventory[name_text_col].astype(str).str.casefold().eq(named.casefold())].copy()
            inventory["_dynamic_stock"] = pd.to_numeric(inventory[stock_col], errors="coerce")
            if reorder_col:
                inventory["_dynamic_reorder"] = pd.to_numeric(inventory[reorder_col], errors="coerce")
            stock_by_product = inventory.groupby(product_key, dropna=True, sort=False).agg(aggregation)
            names = inventory.groupby(product_key, dropna=True, sort=False)[product_name].agg(lambda x: str(x.dropna().iloc[0]) if x.notna().any() else str(x.name)) if product_name else pd.Series({k: str(k) for k in stock_by_product.index})
    batch_id_col = col(("batch_no", "batch_number", "lot_no", "lot_number"))
    location_col = col(("rack_location", "rack", "warehouse", "location", "shelf"))
    if named is not None and (re.search(r"\b(batch|batches|lot|lots)\b", q) or re.search(r"\b(where|rack|location|stored|shelf|warehouse)\b", q)):
        details = []
        for _, row in inventory.iterrows():
            details.append({"batch": row.get(batch_id_col) if batch_id_col else None, "stock": float(pd.to_numeric(pd.Series([row[stock_col]]), errors="coerce").iloc[0]), "expiry_date": str(row.get(expiry_col)) if expiry_col and pd.notna(row.get(expiry_col)) else None, "location": row.get(location_col) if location_col else None})
        fields = [f"{item['batch'] or 'batch'}: {item['stock']:g} units" + (f", expires {item['expiry_date']}" if item['expiry_date'] else "") + (f", {item['location']}" if item['location'] else "") for item in details]
        return {"answer": f"{named}: " + ("; ".join(fields) if fields else "no current batch records were found") + ".", "values": {"status": "ok", "product": named, "batches": details, "batch_count": len(details)}, "source_rows": cites(inventory)}
    if re.search(r"\b(total quantity|total number of units|quantity of inventory|units? of inventory|total stock)\b", q) and re.search(r"\b(total|available|current|inventory|stock)\b", q):
        total = float(pd.to_numeric(inventory[stock_col], errors="coerce").fillna(0).sum())
        return {"answer": f"Total current on-hand inventory: {total:,.0f} units across {len(stock_by_product)} products.", "values": {"status": "ok", "total_stock_units": total, "product_count": len(stock_by_product), "quantity_field": stock_col}, "source_rows": cites(inventory)}
    if re.search(r"\b(value|worth|cost|retail|mrp|inventory value|stock value)\b", q) and re.search(r"\b(total|combined|how much|value|worth)\b", q):
        value_col = col(("unit_cost", "cost_price", "purchase_price", "trade_price", "cost")) if re.search(r"\b(cost|purchase value|at cost)\b", q) else col(("mrp", "retail_price", "selling_price", "unit_price", "price"))
        if value_col:
            total = float((pd.to_numeric(inventory[stock_col], errors="coerce") * pd.to_numeric(inventory[value_col], errors="coerce")).sum())
            basis = "cost" if re.search(r"\b(cost|purchase value|at cost)\b", q) else "retail"
            return {"answer": f"Total current inventory {basis} value (on-hand quantity × recorded {basis} price): {total:,.2f}.", "values": {"status": "ok", "inventory_value": total, "basis": basis, "quantity_field": stock_col, "price_field": value_col, "inventory_records": len(inventory)}, "source_rows": cites(inventory)}
    if re.search(r"\b(how many|count|number of)\b", q) and re.search(r"\b(products?|medicines?|items?)\b", q):
        positive = stock_by_product["_dynamic_stock"].gt(0)
        zero = stock_by_product["_dynamic_stock"].le(0)
        if re.search(r"\b(out of stock|zero stock|no stock)\b", q):
            chosen = stock_by_product.loc[zero]
            return {"answer": f"{len(chosen)} distinct products have no current stock.", "values": {"status": "ok", "product_count": int(len(chosen)), "products": names.loc[chosen.index].astype(str).tolist()}, "source_rows": cites(inventory.loc[inventory[product_key].isin(chosen.index)])}
        chosen = positive if re.search(r"\b(in stock|available stock|with stock)\b", q) else pd.Series(True, index=stock_by_product.index)
        return {"answer": f"{int(chosen.sum())} distinct products have positive current stock." if chosen is positive else f"{int(chosen.sum())} distinct products are recorded in current inventory.", "values": {"status": "ok", "product_count": int(chosen.sum()), "positive_stock_products": int(positive.sum()), "products_checked": len(stock_by_product)}, "source_rows": cites(inventory)}
    if re.search(r"\b(out of stock|zero stock|no stock)\b", q) and re.search(r"\b(which|what|list|show|products?|medicines?)\b", q):
        zero_keys = stock_by_product.index[stock_by_product["_dynamic_stock"].le(0)]
        zero_names = [str(names.get(key, key)) for key in zero_keys]
        return {"answer": f"{len(zero_names)} products have no current stock: " + (", ".join(zero_names) if zero_names else "none") + ".", "values": {"status": "ok", "product_count": len(zero_names), "products": zero_names, "grain": "product"}, "source_rows": cites(inventory.loc[inventory[product_key].isin(zero_keys)])}
    if reorder_col and re.search(r"\b(low stock|understock|below (?:the )?reorder|reorder|restock)\b", q):
        stock = stock_by_product["_dynamic_stock"]
        threshold = stock_by_product.get("_dynamic_reorder", pd.Series(float("nan"), index=stock_by_product.index))
        strict_below = bool(re.search(r"\b(below|under)\b.{0,20}\b(?:the )?reorder\b", q))
        low = (stock.lt(threshold) if strict_below else stock.le(threshold)) & threshold.notna()
        selected_products = stock_by_product.loc[low].copy()
        if re.search(r"\b(how much|quantity|units|suggest|should i|should we)\b", q):
            selected_products["suggested_order"] = (selected_products["_dynamic_reorder"] - selected_products["_dynamic_stock"]).clip(lower=0)
        rows = []
        for key, row in selected_products.iterrows():
            entry = {"product": str(names.get(key, key)), "stock": float(row["_dynamic_stock"]), "reorder_level": float(row["_dynamic_reorder"])}
            if "suggested_order" in row:
                entry["suggested_order"] = float(row["suggested_order"])
            rows.append(entry)
        body = "; ".join(f"{r['product']}: {r['stock']:g}/{r['reorder_level']:g}" + (f", order {r['suggested_order']:g}" if 'suggested_order' in r else "") for r in rows[:50])
        label = "suggested quantities to reach the recorded reorder level" if rows and "suggested_order" in rows[0] else "products at or below their recorded reorder level"
        answer = f"{len(rows)} {label}: {body}." if rows else "No products are at or below a populated reorder level."
        return {"answer": answer, "values": {"status": "ok", "products": rows, "product_count": len(rows), "grain": "product", "aggregation": "sum batch stock; maximum populated reorder level"}, "source_rows": cites(inventory.loc[inventory[product_key].isin(selected_products.index)])}
    if re.search(r"\b(highest|most|largest|max(?:imum)?)\b.{0,35}\b(stock|quantity|inventory)\b", q):
        idx = stock_by_product["_dynamic_stock"].idxmax()
        return {"answer": f"Highest current stock is {float(stock_by_product.loc[idx, '_dynamic_stock']):g} units for {names.get(idx, idx)} (aggregated across batches).", "values": {"status": "ok", "product": str(names.get(idx, idx)), "stock": float(stock_by_product.loc[idx, "_dynamic_stock"]), "grain": "product"}, "source_rows": cites(inventory.loc[inventory[product_key].eq(idx)])}
    if named is not None:
        total = float(stock_by_product["_dynamic_stock"].sum())
        return {"answer": f"Current on-hand stock for {named}: {total:g} units across {len(inventory)} batches.", "values": {"status": "ok", "product": named, "stock": total, "batches": int(len(inventory))}, "source_rows": cites(inventory)}
    if re.search(r"\b(each|every|all)\b", q) and re.search(r"\b(product|medicine|item)s?\b", q) and re.search(r"\b(stock|quantity|units?)\b", q):
        records = [{"product": str(names.get(k, k)), "stock": float(v)} for k, v in stock_by_product["_dynamic_stock"].sort_values(ascending=False).items()]
        return {"answer": "Current stock by product (batch quantities summed): " + "; ".join(f"{x['product']}: {x['stock']:g}" for x in records[:100]) + (" (first 100 shown)." if len(records)>100 else "."), "values": {"status": "ok", "grain": "product", "products": records, "product_count": len(records)}, "source_rows": cites(inventory)}
    return None

def _answer_cross_table_pharmacy_risk(question: str, frame: pd.DataFrame, filters=None):
    """Join sales history to current inventory for evidence-based pharmacy risk questions.

    Joins use exact normalized product identifiers/names only. This intentionally
    abstains when the selected sources do not share a reliable product key.
    """
    q = re.sub(r"\s+", " ", _current_pharmacy_question(question).casefold()).strip()
    # An explicit earliest/latest batch lookup is a deterministic inventory
    # ranking request. Do not reinterpret it as a sales-velocity expiry forecast.
    if (re.search(r"\b(earliest|latest|first|most recent)\b", q)
            and re.search(r"\b(expir\w*|batch(?:es)?|lots?)\b", q)
            and not re.search(r"\b(unsold|sales velocity|project(?:ed|ion)|likely to expire before sold)\b", q)):
        return None
    if (re.search(r"\bas of\s+20\d{2}-\d{1,2}-\d{1,2}\b", q)
            and re.search(r"\b(expir\w*|batch(?:es)?|lots?)\b", q)
            and not re.search(r"\b(sales?|sold|revenue|transactions?)\b", q)):
        return None
    explicit_expiry_count = (
        re.search(r"\b(expir\w*|batches?)\b", q)
        and re.search(r"\b(before|prior to|earlier than|after|later than|on or before|by)\b", q)
        and re.search(r"\b20\d{2}\b", q)
        and re.search(r"\b(how many|count|number of)\b", q)
        and not re.search(r"\b(sold|sales?|demand|velocity|unsold|clear(?:ed|ance)?)\b", q)
    )
    if explicit_expiry_count:
        return None
    try:
        from app.analytics.pharmacy_intelligence import answer_pharmacy_business_question, is_cross_table_pharmacy_risk_question as _pi_check
        if _pi_check(question):
            intel_res = answer_pharmacy_business_question(question, frame, filters=filters)
            if intel_res is not None:
                return intel_res
    except Exception:
        pass
    q = re.sub(r"\s+", " ", _current_pharmacy_question(question).casefold()).strip()
    demand_intent = _pharmacy_inventory_demand_intent(question)
    reorder_request = demand_intent == "reorder_priority"
    # Plain inventory reorder thresholds are handled by the inventory planner;
    # this planner only applies when the question relates stock to sales or demand.
    if reorder_request and not re.search(r"\b(sales?|sold|demand|velocity|stockout|out of stock|run[- ]?out|history|recently|recent sales)\b", q):
        return None
    inventory_demand_request = demand_intent in {
        "runout_risk", "high_demand_out_of_stock", "overstock", "no_recent_sales",
        "recurring_low_stock", "reorder_quantity",
    }
    expiry_slow_request = bool(re.search(r"\b(expir\w*|near[- ]?expiry)\b", q) and re.search(r"\b(slow(?:ly)?|poor sales|low sales|no sales|clear|sell(?:ing)?|velocity)\b", q))
    profit_risk_request = bool(re.search(r"\b(profit(?:able|ability)?|profitable|margin)\b", q) and re.search(r"\b(stockout|out of stock|going out of stock|risk|reorder|low stock)\b", q))
    if not (reorder_request or inventory_demand_request or expiry_slow_request or profit_risk_request):
        return None

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {"answer": "The selected data has no complete rows for a sales and inventory comparison.", "values": {"status": "unsupported_record_type"}, "source_rows": []}

    table = frame.get("table_name", pd.Series("", index=frame.index)).fillna("").astype(str).str.casefold()
    txn = frame.get("txn_type", pd.Series("", index=frame.index)).fillna("").astype(str).str.casefold()
    sales_table = table.str.contains(r"sale|invoice|bill|transaction|dispens", regex=True)
    inventory_table = table.str.contains(r"inventor|stock|batch|products?|product[_ ]?master|tbl[_ ]?10", regex=True)
    inventory_table &= ~table.str.contains(r"archive|histor(?:y|ical)|movement|ledger", regex=True)
    sales_txn = txn.str.contains(r"sale|dispens", regex=True)
    inventory_txn = txn.str.contains(r"inventor|stock|batch", regex=True)
    qty_col = next((c for c in ("quantity", "qty_sold", "units_sold", "sold_qty", "quantity_sold", "units", "qty") if c in frame), None)
    if qty_col is None:
        qty_col = next(iter(_semantic_columns(frame, ("quantity", "qty_sold", "sold_qty", "quantity_sold", "units_sold", "units", "qty", "dispensed_qty"))), None)
    stock_aliases = (
        "stock_qty", "closing_stock_qty", "available_qty", "on_hand", "quantity",
        "current_stock", "current_quantity", "available_stock", "qty_on_hand", "quantity_on_hand",
        "stock_on_hand", "onhand_qty", "inventory_quantity", "stock_balance", "balance_qty",
        "closing_qty", "closing_quantity", "physical_stock", "total_stock",
    )
    stock_cols = list(dict.fromkeys([c for c in stock_aliases if c in frame] + _semantic_columns(frame, stock_aliases)))
    stock_signal_cols = [c for c in stock_cols if re.sub(r"[^a-z0-9]+", "", str(c).casefold().removeprefix("_extra.")) not in {"quantity", "qty"}]

    if sales_table.any():
        sales_mask = sales_table & (sales_txn | ~txn.str.len().gt(0))
    elif sales_txn.any():
        sales_mask = sales_txn
    else:
        sale_id = frame.get("invoice_id", frame.get("transaction_id", pd.Series(None, index=frame.index)))
        sales_mask = sale_id.notna()
    if inventory_table.any():
        inventory_mask = inventory_table
    elif inventory_txn.any():
        inventory_mask = inventory_txn
    elif stock_signal_cols:
        inventory_mask = pd.Series(False, index=frame.index)
        for col in stock_signal_cols:
            inventory_mask |= frame[col].notna()
    else:
        inventory_mask = pd.Series(False, index=frame.index)

    sales = frame.loc[sales_mask].copy()
    inventory = frame.loc[inventory_mask].copy()
    if sales.empty or inventory.empty:
        missing = "sales transactions" if sales.empty else "current inventory records"
        return {"answer": f"This comparison needs both dated sales transactions and current inventory rows; the selected data has no {missing}.", "values": {"status": "unsupported_record_type", "required_record_type": missing}, "source_rows": []}
    if not qty_col:
        return {"answer": "The selected sales data has no sold-quantity field, so I can't calculate sales velocity.", "values": {"status": "unsupported_field", "required_field": "quantity"}, "source_rows": []}
    if not stock_cols:
        return {"answer": "The selected inventory data has no current on-hand quantity field.", "values": {"status": "unsupported_field", "required_field": "stock_qty"}, "source_rows": []}

    key_candidates = (
        ("product_code", "product_code"), ("product_code", "barcode"), ("product_code", "bar_code"),
        ("barcode", "barcode"), ("barcode", "bar_code"), ("bar_code", "barcode"), ("bar_code", "bar_code"),
        ("sku", "sku"), ("sku", "product_code"),
        ("product_id", "product_id"), ("product_name", "product_name"),
        ("medicine_name", "medicine_name"), ("item_name", "item_name"),
        ("product_id", "product_name"), ("product_name", "product_id"),
        ("product_id", "medicine_name"), ("product_name", "medicine_name"),
    )
    key_pair = None
    for sale_key, inventory_key in key_candidates:
        if sale_key not in sales or inventory_key not in inventory:
            continue
        norm = lambda s: s.fillna("").astype(str).str.casefold().str.replace(r"[^a-z0-9]+", "", regex=True)
        sale_keys = set(norm(sales[sale_key])) - {""}
        inventory_keys = set(norm(inventory[inventory_key])) - {""}
        overlap = sale_keys & inventory_keys
        sale_raw = sales[sale_key].fillna("").astype(str).str.strip().str.casefold()
        inventory_raw = inventory[inventory_key].fillna("").astype(str).str.strip().str.casefold()
        sale_norm = norm(sales[sale_key])
        inventory_norm = norm(inventory[inventory_key])
        sale_ambiguous = {
            key for key, indexes in sale_norm.groupby(sale_norm).groups.items()
            if key in overlap and sale_raw.loc[indexes].nunique() > 1
        }
        inventory_ambiguous = {
            key for key, indexes in inventory_norm.groupby(inventory_norm).groups.items()
            if key in overlap and inventory_raw.loc[indexes].nunique() > 1
        }
        if overlap & (sale_ambiguous | inventory_ambiguous):
            continue
        # Integer IDs from separate databases are local surrogate keys, not
        # proof of a cross-database product relationship. Only trust these
        # across sources when the database identity is the same.
        if overlap and sale_key == inventory_key == "product_id" and all(value.isdigit() for value in overlap):
            if "database_name" in frame:
                sale_dbs = set(frame.loc[sales.index, "database_name"].dropna().astype(str))
                inv_dbs = set(frame.loc[inventory.index, "database_name"].dropna().astype(str))
                if sale_dbs and inv_dbs and sale_dbs.isdisjoint(inv_dbs):
                    continue
        if overlap:
            key_pair = (sale_key, inventory_key)
            break
    if key_pair is None:
        return {"answer": "Sales and inventory are available, but they do not share an exact product code, barcode, SKU, or product name. I can't safely join them to calculate product-level risk.", "values": {"status": "missing_join_key", "required_join_key": ["product_code", "barcode", "sku", "product_id/product_name"]}, "source_rows": []}

    sale_key, inventory_key = key_pair
    norm = lambda value: re.sub(r"[^a-z0-9]+", "", str(value).casefold())
    sales["_join_product"] = sales[sale_key].map(norm)
    inventory["_join_product"] = inventory[inventory_key].map(norm)
    sales["_qty"] = pd.to_numeric(sales[qty_col], errors="coerce").fillna(0)
    inventory["_stock"] = pd.Series(float("nan"), index=inventory.index)
    for stock_field in stock_cols:
        inventory["_stock"] = inventory["_stock"].combine_first(pd.to_numeric(inventory[stock_field], errors="coerce"))
    date_candidates = ("date", "transaction_date", "sale_date", "invoice_date", "invoice_datetime", "sold_at", "created_at", "transaction_datetime", "receipt_date", "bill_date")
    date_col = next((c for c in date_candidates if c in sales), None)
    if date_col is None:
        date_col = next(iter(_semantic_columns(sales, date_candidates)), None)
    sales["_date"] = pd.to_datetime(sales[date_col], errors="coerce") if date_col else pd.NaT
    as_of = pd.Timestamp.today().normalize()
    dated_sales = sales.loc[sales["_date"].notna() & sales["_date"].dt.normalize().le(as_of)]
    filters = filters or {}
    requested_start = pd.to_datetime(filters.get("date_from"), errors="coerce") if filters.get("date_from") else pd.NaT
    requested_end = pd.to_datetime(filters.get("date_to"), errors="coerce") if filters.get("date_to") else pd.NaT
    if (
        pd.isna(requested_start) and pd.isna(requested_end)
        and demand_intent in {"runout_risk", "no_recent_sales", "high_demand_out_of_stock", "overstock", "reorder_quantity"}
        and re.search(r"\b(recent|recently|lately|current|actual)\b", q)
    ):
        available_end = dated_sales["_date"].max().normalize() if not dated_sales.empty else pd.NaT
        if not pd.isna(available_end):
            requested_end = available_end
            requested_start = available_end - pd.Timedelta(days=29)
    if not pd.isna(requested_start):
        dated_sales = dated_sales.loc[dated_sales["_date"].dt.normalize().ge(requested_start.normalize())]
    if not pd.isna(requested_end):
        dated_sales = dated_sales.loc[dated_sales["_date"].dt.normalize().le(requested_end.normalize())]
    if dated_sales.empty:
        if not pd.isna(requested_start) or not pd.isna(requested_end):
            start_label = requested_start.date().isoformat() if not pd.isna(requested_start) else "earliest available"
            end_label = requested_end.date().isoformat() if not pd.isna(requested_end) else "latest available"
            return {
                "answer": f"There are no dated sales transactions in the selected period ({start_label} to {end_label}), so sales velocity and profit cannot be calculated for it.",
                "values": {"status": "no_sales_in_period", "sales_date_from": None if pd.isna(requested_start) else start_label, "sales_date_to": None if pd.isna(requested_end) else end_label},
                "source_rows": [],
            }
        velocity_span = 0
        daily_units = {}
        window_start = window_end = None
    else:
        start_date = requested_start.normalize() if not pd.isna(requested_start) else dated_sales["_date"].min().normalize()
        end_date = requested_end.normalize() if not pd.isna(requested_end) else dated_sales["_date"].max().normalize()
        window_start, window_end = start_date.date().isoformat(), end_date.date().isoformat()
        velocity_span = max(1, int((end_date - start_date).days) + 1)
        sold = dated_sales.groupby("_join_product")["_qty"].sum()
        daily_units = (sold / velocity_span).to_dict()
    window_description = (
        f"{velocity_span} days ({window_start} to {window_end})" if window_start
        else "no matching dated sales"
    )
    # All product-level sales metrics below must use the same filtered set as
    # the velocity denominator; otherwise totals/profit could leak older rows.
    sales = dated_sales.copy()
    row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)
    source_files = frame.get("source_file", pd.Series("", index=frame.index)).fillna("").astype(str)

    def citations(indexes):
        return [(source_files.loc[idx], row_ids.loc[idx]) for idx in dict.fromkeys(indexes)]

    product_col = next((c for c in (inventory_key, "product_id", "product_name", "medicine_name", "product_code") if c in inventory), inventory_key)
    inventory_rows = []
    for key, group in inventory.groupby("_join_product", sort=False):
        stock = group["_stock"].dropna()
        if stock.empty:
            continue
        display = str(group[product_col].dropna().iloc[0]) if group[product_col].notna().any() else key
        reorder_candidates = (
            "reorder_level", "min_stock", "reorder_point", "minimum_stock", "minimum_stock_level",
            "min_stock_level", "min_level", "minimum_level", "reorder_threshold", "low_stock_threshold",
            "minimum_on_hand", "min_qty_on_hand", "minimum_quantity_on_hand", "restock_level",
            "restock_point", "reorder_at", "rop", "min_qty",
        )
        reorder_col = next((c for c in _semantic_columns(group, reorder_candidates) if group[c].notna().any()), None)
        reorder = float(pd.to_numeric(group[reorder_col], errors="coerce").dropna().iloc[0]) if reorder_col else None
        inventory_rows.append({"key": key, "name": display, "stock": float(stock.sum()), "reorder": reorder, "rows": group})
    matched_sales = sales.groupby("_join_product", sort=False)
    cited, details = [], []

    def format_days(value):
        return f"{value:g} days" if value is not None else "unknown"

    def format_stock_reorder(stock_value, reorder_value):
        reorder_text = f"{reorder_value:g}" if reorder_value is not None else "unavailable"
        return f"{stock_value:g} / {reorder_text}"

    if inventory_demand_request:
        if demand_intent == "recurring_low_stock":
            return {
                "answer": "I can calculate current stock risk, but this source only provides current inventory snapshots. To verify products that repeatedly run low, I need dated historical stock snapshots or low-stock/stockout event records.",
                "values": {"status": "insufficient_history", "required_evidence": ["dated inventory snapshots", "low-stock or stockout events"]},
                "source_rows": [],
            }

        if demand_intent == "no_recent_sales":
            for item in inventory_rows:
                if item["stock"] <= 0:
                    continue
                group = matched_sales.get_group(item["key"]) if item["key"] in matched_sales.groups else None
                units = float(group["_qty"].sum()) if group is not None else 0.0
                if units > 0:
                    continue
                details.append({"product": item["name"], "stock": item["stock"], "units_sold": 0.0, "daily_sales_velocity": 0.0})
                cited.extend(item["rows"].index.tolist())
            details.sort(key=lambda row: -row["stock"])
            title = "Stock with no sales in the selected period"
            intro = f"These products have on-hand stock but no matched sales in the selected window ({window_description}); sorted by stock quantity."
            headers = "| Medicine | Current stock | Units sold in window |\n|---|---:|---:|"
            lines = [f"| {row['product'].replace('|', '/')} | {row['stock']:g} | 0 |" for row in details[:20]]
        else:
            threshold_match = re.search(r"\b(?:within|in|under|less than|only)\s+(\d{1,3})\s+days?\b|\b(\d{1,3})[- ]day\s+(?:supply|cover)\b", q)
            threshold_days = int(next(value for value in threshold_match.groups() if value)) if threshold_match else (7 if re.search(r"\b(few|couple|week)\b", q) else 30)
            days_cover_only = demand_intent == "runout_risk" and bool(re.search(
                r"\b(few|couple)\s+(?:more\s+)?days?\b|\b(?:only|less than|under)\s+(?:\d+|a few|a couple|a)\s+(?:days?|weeks?)\s+(?:of\s+)?(?:stock|supply|cover)\b|\b\d+[- ]day\s+(?:supply|cover)\b",
                q,
            ))
            closest_runout_candidates = []
            high_demand_only = bool(re.search(r"\b(best[- ]selling|top[- ]selling|highest[- ]selling|high[- ]demand|fast[- ]selling|fastest[- ]selling|high sales)\b", q))
            positive_velocities = [float(daily_units.get(item["key"], 0.0)) for item in inventory_rows if float(daily_units.get(item["key"], 0.0)) > 0]
            high_demand_cutoff = float(pd.Series(positive_velocities).quantile(0.75)) if high_demand_only and positive_velocities else None
            for item in inventory_rows:
                group = matched_sales.get_group(item["key"]) if item["key"] in matched_sales.groups else None
                units = float(group["_qty"].sum()) if group is not None else 0.0
                velocity = float(daily_units.get(item["key"], 0.0))
                days_supply = item["stock"] / velocity if velocity > 0 else None
                if demand_intent == "high_demand_out_of_stock":
                    include = item["stock"] <= 0 and velocity > 0
                elif demand_intent == "overstock":
                    # A 90-day cover is an explicit screening heuristic, not a
                    # universal policy. Positive unsold stock is also a review
                    # case because its cover cannot be estimated finitely.
                    include = item["stock"] > 0 and (units <= 0 or (days_supply is not None and days_supply >= 90))
                elif demand_intent == "runout_risk":
                    below_reorder = item["reorder"] is not None and item["stock"] <= item["reorder"]
                    if velocity > 0 and days_supply is not None:
                        closest_runout_candidates.append({
                            "product": item["name"], "stock": item["stock"], "reorder_level": item["reorder"],
                            "units_sold": units, "daily_sales_velocity": round(velocity, 4),
                            "days_of_supply": round(days_supply, 1), "inventory_rows": item["rows"].index.tolist(),
                            "sales_rows": group.index.tolist() if group is not None else [],
                        })
                    include = velocity > 0 and (
                        (days_supply is not None and days_supply <= threshold_days)
                        if days_cover_only else
                        (below_reorder or (days_supply is not None and days_supply <= threshold_days))
                    )
                else:  # demand-based replenishment target
                    target_days = 30
                    recommended = max(0.0, velocity * target_days - item["stock"])
                    include = recommended > 0 and (item["reorder"] is None or recommended > item["reorder"])
                if high_demand_cutoff is not None and velocity < high_demand_cutoff:
                    include = False
                if not include:
                    continue
                row = {"product": item["name"], "stock": item["stock"], "reorder_level": item["reorder"], "units_sold": units, "daily_sales_velocity": round(velocity, 4), "days_of_supply": round(days_supply, 1) if days_supply is not None else None}
                if demand_intent == "reorder_quantity":
                    row["suggested_replenishment_units"] = round(recommended, 1)
                    row["target_cover_days"] = target_days
                details.append(row)
                cited.extend(item["rows"].index.tolist())
                if group is not None:
                    cited.extend(group.index.tolist())
            if demand_intent == "high_demand_out_of_stock":
                details.sort(key=lambda row: -row["daily_sales_velocity"])
                title = "High-demand products currently out of stock"
                intro = f"Products have zero current stock and recorded sales during the selected window ({window_description}), ranked by units sold per day."
                headers = "| Rank | Medicine | Sold per day | Units sold in window |\n|---:|---|---:|---:|"
                lines = [f"| {i} | {row['product'].replace('|', '/')} | {row['daily_sales_velocity']:g} | {row['units_sold']:g} |" for i, row in enumerate(details[:20], 1)]
            elif demand_intent == "overstock":
                details.sort(key=lambda row: -(row["days_of_supply"] or 0))
                title = "Potential overstock based on sales"
                intro = f"Screening rule: no sales in the selected window or at least 90 days of stock cover at the observed rate in {window_description}. This is a review flag; confirm supplier lead times and your target cover before changing orders."
                headers = "| Medicine | Current stock | Sold per day | Stock cover |\n|---|---:|---:|---:|"
                lines = [f"| {row['product'].replace('|', '/')} | {row['stock']:g} | {row['daily_sales_velocity']:g} | {format_days(row['days_of_supply'])} |" for row in details[:20]]
            elif demand_intent == "runout_risk":
                details.sort(key=lambda row: (row["days_of_supply"] if row["days_of_supply"] is not None else float("inf"), -row["daily_sales_velocity"]))
                title = "Products at risk of running out"
                criterion = f"{threshold_days} days or less of stock cover" if days_cover_only else f"at/below reorder level or with {threshold_days} days or less of stock cover"
                headers = "| Rank | Medicine | Stock / reorder level | Sold per day | Stock cover |\n|---:|---|---:|---:|---:|"
                lines = [f"| {i} | {row['product'].replace('|', '/')} | {format_stock_reorder(row['stock'], row['reorder_level'])} | {row['daily_sales_velocity']:g} | {format_days(row['days_of_supply'])} |" for i, row in enumerate(details[:20], 1)]
                if details:
                    intro = f"Flagged by {criterion}, using sales window {window_description}."
                    if high_demand_cutoff is not None:
                        intro += f" The high-demand filter uses the top quartile of observed product sales velocity (at least {high_demand_cutoff:g} units per day)."
                else:
                    closest_runout_candidates.sort(key=lambda row: (row["days_of_supply"], -row["daily_sales_velocity"]))
                    closest = closest_runout_candidates[:5]
                    intro = f"No products met {criterion} in sales window {window_description}. Closest stock-cover estimates are shown for context."
                    lines = [f"| {i} | {row['product'].replace('|', '/')} | {format_stock_reorder(row['stock'], row['reorder_level'])} | {row['daily_sales_velocity']:g} | {format_days(row['days_of_supply'])} |" for i, row in enumerate(closest, 1)]
                    for row in closest:
                        cited.extend(row["inventory_rows"]); cited.extend(row["sales_rows"])
            else:
                details.sort(key=lambda row: -row["suggested_replenishment_units"])
                title = "Demand-based replenishment review"
                intro = f"Suggested quantity targets 30 days of cover at observed demand, less current stock, using sales window {window_description}. This is an estimate; lead time and safety stock are not included."
                headers = "| Medicine | Stock / reorder level | Sold per day | Suggested replenishment units |\n|---|---:|---:|---:|"
                lines = [f"| {row['product'].replace('|', '/')} | {format_stock_reorder(row['stock'], row['reorder_level'])} | {row['daily_sales_velocity']:g} | {row['suggested_replenishment_units']:g} |" for row in details[:20]]

        answer = f"**{title}**\n\n{intro}"
        if lines:
            answer += f"\n\n{headers}\n" + "\n".join(lines)
        elif demand_intent == "runout_risk":
            answer += " No product had both usable current-stock data and a calculable sales rate in this period, so I couldn't rank stockout risk."
        else:
            answer += " No products matched the stated criteria in the selected data."
        values = {
            "status": "ok", "intent": demand_intent, "sales_window_days": velocity_span,
            "sales_date_from": window_start, "sales_date_to": window_end,
            "threshold_days": threshold_days if demand_intent == "runout_risk" else None,
            "overstock_threshold_days": 90 if demand_intent == "overstock" else None,
            "replenishment_target_days": 30 if demand_intent == "reorder_quantity" else None,
            "join_key": f"{sale_key}={inventory_key}", "products": details,
        }
        if demand_intent == "runout_risk" and not details:
            values["closest_products"] = [
                {key: value for key, value in row.items() if key not in {"inventory_rows", "sales_rows"}}
                for row in closest_runout_candidates[:5]
            ]
        return {"answer": answer, "values": values, "source_rows": citations(cited)}

    if reorder_request:
        has_reorder_threshold = any(row["reorder"] is not None for row in inventory_rows)
        explicit_below_reorder = bool(re.search(r"\b(below|under|less than)\b.{0,20}\b(reorder|minimum stock|minimum level)\b", q))
        for item in inventory_rows:
            if item["key"] not in matched_sales.groups:
                continue
            group = matched_sales.get_group(item["key"])
            sold_units = float(group["_qty"].sum())
            velocity = float(daily_units.get(item["key"], 0.0))
            days_supply = item["stock"] / velocity if velocity > 0 else None
            if has_reorder_threshold:
                if item["reorder"] is None:
                    continue
                below_reorder = item["stock"] < item["reorder"]
                if not below_reorder and (explicit_below_reorder or days_supply is None or days_supply > 30):
                    continue
            else:
                # Without a mapped policy threshold, use a clearly identified
                # stock-cover screen instead of pretending the field exists.
                below_reorder = False
                if days_supply is None or days_supply > 30:
                    continue
            if sold_units <= 0:
                continue
            details.append({"product": item["name"], "stock": item["stock"], "reorder_level": item["reorder"], "units_sold": sold_units, "daily_sales_velocity": round(velocity, 4), "days_of_supply": round(days_supply, 1) if days_supply is not None else None})
            cited.extend(item["rows"].index.tolist()); cited.extend(group.index.tolist())
        details.sort(key=lambda x: (x["days_of_supply"] if x["days_of_supply"] is not None else float("inf"), -x["daily_sales_velocity"]))
        if not details:
            empty_answer = (
                "No matched products had 30 days or less of stock cover in the selected period. "
                "The source has no mapped reorder threshold, so this result uses stock cover as a screening proxy."
                if not has_reorder_threshold else
                "No exact-matched products with recorded sales are currently below their reorder level."
            )
            return {"answer": empty_answer, "values": {"status": "ok", "products": [], "reorder_threshold_available": has_reorder_threshold, "proxy_days": None if has_reorder_threshold else 30}, "source_rows": []}
        display = details[:12]
        table_rows = "\n".join(
            f"| {i} | {item['product'].replace('|', '/')} | {format_stock_reorder(item['stock'], item['reorder_level'])} | {item['daily_sales_velocity']:g} | {format_days(item['days_of_supply'])} |"
            for i, item in enumerate(display, 1)
        )
        answer = (
            "**Reorder priority**\n\n"
            f"{len(details)} products match the reorder criteria. "
            + ("No reorder threshold was mapped, so products with 30 days or less of stock cover are shown as a proxy. " if not has_reorder_threshold else "")
            + f"Ranked by lowest stock cover; sales window: {window_description}.\n\n"
            "| Rank | Medicine | Stock / reorder level | Sold per day | Stock cover |\n"
            "|---:|---|---:|---:|---:|\n"
            f"{table_rows}"
        )
        values = {"status": "ok", "sales_window_days": velocity_span, "sales_date_from": window_start, "sales_date_to": window_end, "reorder_threshold_available": has_reorder_threshold, "proxy_days": None if has_reorder_threshold else 30, "join_key": f"{sale_key}={inventory_key}", "priority_rule": "below reorder level, then fewest days of supply" if has_reorder_threshold else "30 days or less stock cover proxy, then fewest days of supply", "products": details}
    elif expiry_slow_request:
        expiry_col = next((c for c in ("expiry_date", "expiration_date", "expires_at") if c in inventory), None)
        if not expiry_col:
            return {"answer": "The selected inventory has no expiry-date field.", "values": {"status": "unsupported_field", "required_field": "expiry_date"}, "source_rows": []}
        horizon_match = re.search(r"\b(?:next|within|coming|agle)\s+(\d{1,3})\s*(?:days?|din)\b", q)
        horizon = int(horizon_match.group(1)) if horizon_match else 60
        today = as_of
        expiry = pd.to_datetime(inventory[expiry_col], errors="coerce").dt.normalize()
        selected = inventory.loc[expiry.between(today, today + pd.Timedelta(days=horizon), inclusive="both")].copy()
        selected["_expiry"] = expiry.loc[selected.index]
        for key, group in selected.groupby("_join_product", sort=False):
            if key not in matched_sales.groups:
                continue
            sg = matched_sales.get_group(key)
            velocity = float(daily_units.get(key, 0.0))
            for idx, batch in group.iterrows():
                qty = pd.to_numeric(pd.Series([batch.get("_stock")]), errors="coerce").iloc[0]
                if pd.isna(qty):
                    continue
                days_left = max(0, int((batch["_expiry"] - today).days))
                expected_sales = velocity * days_left
                if expected_sales >= float(qty):
                    continue
                name = str(batch.get(product_col) or key)
                details.append({"product": name, "expiry_date": batch["_expiry"].date().isoformat(), "days_to_expiry": days_left, "batch": batch.get("batch_no"), "stock": float(qty), "daily_sales_velocity": round(velocity, 4), "estimated_units_sold_before_expiry": round(expected_sales, 2), "estimated_units_at_expiry": round(max(0.0, float(qty) - expected_sales), 2)})
                cited.append(idx); cited.extend(sg.index.tolist())
        details.sort(key=lambda x: (x["days_to_expiry"], -x["estimated_units_at_expiry"]))
        if not details:
            answer = f"No exact-matched near-expiry batches are projected to remain unsold within {horizon} days, based on the available sales window ({window_description})."
        else:
            display = details[:10]
            table_rows = "\n".join(
                f"| {item['product'].replace('|', '/')} | {item.get('batch') or '—'} | {item['expiry_date']} | {item['stock']:g} | {item['daily_sales_velocity']:g} | {item['estimated_units_at_expiry']:g} |"
                for item in display
            )
            answer = (
                "**Near-expiry stock at risk of remaining unsold**\n\n"
                f"Estimate uses the observed daily sales rate over {window_description}. Showing the 10 earliest-expiring batches out of {len(details)} matches.\n\n"
                "| Medicine | Batch | Expires | Stock | Sold per day | Estimated left at expiry |\n"
                "|---|---|---:|---:|---:|---:|\n"
                f"{table_rows}"
            )
        values = {"status": "ok", "horizon_days": horizon, "sales_window_days": velocity_span, "sales_date_from": window_start, "sales_date_to": window_end, "join_key": f"{sale_key}={inventory_key}", "batches": details}
    else:
        cost_fields = ("cost", "unit_cost", "cost_price", "purchase_price", "trade_price", "line_cost", "purchase_sub_total", "cost_of_goods_sold", "gross_profit", "line_profit")
        if not any(c in sales and sales[c].notna().any() for c in cost_fields):
            return {"answer": "The selected sales data has no product cost field, so gross profit cannot be calculated reliably.", "values": {"status": "unsupported_field", "required_field": "unit cost or gross_profit"}, "source_rows": []}
        for item in inventory_rows:
            if item["key"] not in matched_sales.groups:
                continue
            sg = matched_sales.get_group(item["key"])
            velocity = float(daily_units.get(item["key"], 0.0))
            days_supply = item["stock"] / velocity if velocity > 0 else None
            risk = (item["reorder"] is not None and item["stock"] <= item["reorder"]) or (days_supply is not None and days_supply <= 30)
            if not risk:
                continue
            qty = pd.to_numeric(sg[qty_col], errors="coerce").fillna(0)
            gp_col = next((c for c in ("gross_profit", "line_profit") if c in sg and sg[c].notna().any()), None)
            cost_col = next((c for c in ("unit_cost", "cost_price", "purchase_price", "trade_price", "cost") if c in sg and sg[c].notna().any()), None)
            if gp_col:
                profit = float(pd.to_numeric(sg[gp_col], errors="coerce").fillna(0).sum())
            elif cost_col:
                costs = pd.to_numeric(sg[cost_col], errors="coerce")
                cost_of_sales_col = next((c for c in ("line_cost", "purchase_sub_total", "cost_of_goods_sold", "cogs") if c in sg and sg[c].notna().any()), None)
                if cost_of_sales_col:
                    cogs = pd.to_numeric(sg[cost_of_sales_col], errors="coerce").fillna(0)
                else:
                    cogs = costs.fillna(0) * qty
                revenue_col = next((c for c in ("amount", "line_total", "sub_total", "subtotal") if c in sg and sg[c].notna().any()), None)
                if revenue_col:
                    revenue = pd.to_numeric(sg[revenue_col], errors="coerce").fillna(0)
                else:
                    price_col = next((c for c in ("unit_price", "sale_price", "selling_price") if c in sg and sg[c].notna().any()), None)
                    if not price_col:
                        continue
                    revenue = pd.to_numeric(sg[price_col], errors="coerce").fillna(0) * qty
                profit = float((revenue - cogs).sum())
            else:
                continue
            details.append({"product": item["name"], "gross_profit": round(profit, 2), "stock": item["stock"], "reorder_level": item["reorder"], "daily_sales_velocity": round(velocity, 4), "days_of_supply": round(days_supply, 1) if days_supply is not None else None, "stockout_risk_basis": "below reorder level" if item["reorder"] is not None and item["stock"] <= item["reorder"] else "30 days or less of supply"})
            cited.extend(item["rows"].index.tolist()); cited.extend(sg.index.tolist())
        details.sort(key=lambda x: -x["gross_profit"])
        if details:
            display = details[:10]
            table_rows = "\n".join(
                f"| {item['product'].replace('|', '/')} | PKR {item['gross_profit']:,.2f} | {format_stock_reorder(item['stock'], item['reorder_level'])} | {item['daily_sales_velocity']:g} | {format_days(item['days_of_supply'])} |"
                for item in display
            )
            answer = (
                "**Profitable products with stockout risk**\n\n"
                "Risk means stock is at/below its reorder level or projected supply is 30 days or less. Ranked by gross profit.\n\n"
                "| Product | Gross profit | Stock / reorder level | Sold per day | Stock cover |\n"
                "|---|---:|---:|---:|---:|\n"
                f"{table_rows}"
            )
        else:
            answer = "No products had both calculable gross profit and the stated stockout-risk threshold in the selected data."
        values = {"status": "ok", "risk_rule": "at/below reorder level or <=30 days of supply", "sales_window_days": velocity_span, "sales_date_from": window_start, "sales_date_to": window_end, "join_key": f"{sale_key}={inventory_key}", "products": details}

    return {"answer": answer, "values": values, "source_rows": citations(cited)}


def _answer_sales_question(question: str, frame: pd.DataFrame, filters=None):
    """Answer sales-ledger questions from the selected complete table."""
    id_col = next((c for c in ("transaction_id", "invoice_id", "receipt_id", "order_id") if c in frame), None)
    if not id_col or not any(c in frame for c in ("unit_price", "customer_id", "amount", "quantity")):
        return None
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    sales_intent = bool(re.search(
        r"\b(sales?|sold|sell|sells|selling|revenue|turnover|transactions?|invoices?|bills?|receipts?|returns?|refunds?|discount|cash|credit|payment|top|best[- ]selling|fast[- ]moving|slow[- ]moving|declining|trend|growth|profit|margin|how much|how many|total units?)\b",
        q,
    ))
    if not sales_intent or re.search(r"\b(stock|inventory)\s+(?:sold|purchase|purchased|movement|turnover)\b", q):
        return None
    payment_count_request = bool(
        re.search(r"\b(how many|count|number of)\b", q)
        and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
        and re.search(r"\b(each|every|by)\b", q)
        and re.search(r"\b(payment methods?|payment types?|payment modes?)\b", q)
    )
    if payment_count_request:
        method_col = next((c for c in ("payment_method", "payment_type", "payment_mode") if c in frame and frame[c].notna().any()), None)
        if method_col:
            payment_rows = frame.loc[frame[method_col].notna() & frame[id_col].notna()].copy()
            if payment_rows.empty:
                return {"answer": "No payment-method records are available in the selected sales data.", "values": {"status": "no_matching_records"}, "source_rows": []}
            counts = payment_rows.groupby(method_col, dropna=True)[id_col].nunique().sort_values(ascending=False)
            details = [{"payment_method": str(method), "receipts": int(count)} for method, count in counts.items()]
            answer = "Receipts by payment method: " + "; ".join(f"{item['payment_method']}: {item['receipts']}" for item in details) + "."
            header_file_col = "_header_source_file" if "_header_source_file" in payment_rows and payment_rows._header_source_file.notna().any() else None
            header_row_col = "_header_source_row" if "_header_source_row" in payment_rows and payment_rows._header_source_row.notna().any() else None
            if header_file_col and header_row_col:
                header_rows = payment_rows.drop_duplicates(subset=[id_col])
                cited = [
                    (str(row[header_file_col]), row[header_row_col])
                    for _, row in header_rows.iterrows()
                    if pd.notna(row[header_file_col]) and pd.notna(row[header_row_col])
                ]
                source_field = "_header_table_name" if "_header_table_name" in header_rows else header_file_col
                source_frame = header_rows
            else:
                source_field = next((c for c in ("file_id", "table_name", "source_file") if c in payment_rows), None)
                source_frame = payment_rows
                if "source_file" in payment_rows:
                    cited = [
                        (str(payment_rows.loc[i].get("file_id") or payment_rows.loc[i].get("source_file") or ""), payment_rows.loc[i].get("source_row"))
                        for i in payment_rows.index
                    ]
                else:
                    cited = payment_rows.source_row.tolist() if "source_row" in payment_rows else payment_rows.index.tolist()
            source_counts = (
                {str(key): int(value) for key, value in source_frame[source_field].fillna("unknown").astype(str).value_counts().items()}
                if source_field else {}
            )
            return {"answer": answer, "values": {"status": "ok", "receipts_by_payment_method": details, "payment_source_record_counts": source_counts}, "source_rows": cited}
    df = frame.copy()
    # A consolidated POS frame can contain invoice lines, purchases, inventory
    # snapshots, and product master rows. Sales calculations must start at the
    # sales grain and only join other grains for an explicitly hybrid question.
    if "txn_type" in df:
        mask = df.txn_type.fillna("").astype(str).str.casefold().str.contains(r"sale|return|refund|invoice|bill|dispens", regex=True)
        if mask.any():
            df = df.loc[mask].copy()
    if "table_name" in df:
        mask = df.table_name.fillna("").astype(str).str.contains(r"sales?.*detail|invoice.*detail|bill.*detail|sale[_ ]?items?", case=False, regex=True)
        if mask.any():
            df = df.loc[mask].copy()
    payment_col = next((c for c in ("payment_method", "payment_type", "payment_mode") if c in df and df[c].notna().any()), None)
    compare_payment_types = bool(
        re.search(r"\b(compare|versus|vs|difference between)\b", q)
        and re.search(r"\b(cash|credit|insurance)\b", q)
    )
    if payment_col and not compare_payment_types:
        payment_values = [str(value) for value in df[payment_col].dropna().unique()]
        matched_payment = next(
            (value for value in sorted(payment_values, key=len, reverse=True)
             if re.search(rf"(?<!\w){re.escape(value.casefold())}(?!\w)", q)),
            None,
        )
        if matched_payment:
            df = df[df[payment_col].astype(str).str.casefold().eq(matched_payment.casefold())].copy()
    row_ids = df["source_row"] if "source_row" in df else pd.Series(df.index + 1, index=df.index)
    value_col = next((c for c in ("amount", "invoice_total", "net_payable", "sales_subtotal") if c in df), None)
    qty_col = next((c for c in ("quantity", "qty_sold", "units_sold") if c in df), None)
    price_col = next((c for c in ("unit_price", "mrp", "sale_price") if c in df), None)
    discount_col = next((c for c in ("discount", "discount_amount", "line_discount_amount") if c in df), None)
    amounts = pd.to_numeric(df[value_col], errors="coerce") if value_col else pd.Series(0.0, index=df.index)
    quantities = pd.to_numeric(df[qty_col], errors="coerce") if qty_col else pd.Series(0.0, index=df.index)
    count = int(df[id_col].nunique())

    # The chat layer resolves relative expressions (today/this week/month) to
    # bounds. Apply those same bounds here, while preserving the existing
    # natural-language ISO/month handling below for direct callers.
    if filters:
        df = _apply_explicit_date_filters(question, df, filters)
        row_ids = df["source_row"] if "source_row" in df else pd.Series(df.index + 1, index=df.index)
        amounts = pd.to_numeric(df[value_col], errors="coerce") if value_col else pd.Series(0.0, index=df.index)
        quantities = pd.to_numeric(df[qty_col], errors="coerce") if qty_col else pd.Series(0.0, index=df.index)
        count = int(df[id_col].nunique())
    if df.empty:
        return {"answer": "No matching sales records were found in the selected date range.", "values": {"status": "ok", "matched_records": 0, "transaction_count": 0}, "source_rows": []}

    invoice_tax_col = next((c for c in ("invoice_tax", "invoice_tax_amount", "tax_amount") if c in df and df[c].notna().any()), None)
    if (invoice_tax_col and re.search(r"\b(tax|gst|vat)\b", q)
            and re.search(r"\b(total|sum|combined|how much)\b", q)):
        invoice_key = next((c for c in ("transaction_id", "invoice_id", "invoice_no", "bill_no", "receipt_id") if c in df and df[c].notna().any()), id_col)
        tax_rows = df.loc[df[invoice_key].notna()].drop_duplicates(subset=[invoice_key]).copy()
        tax_values = pd.to_numeric(tax_rows[invoice_tax_col], errors="coerce")
        valid_tax = tax_values.notna()
        tax_total = float(tax_values.loc[valid_tax].sum())
        tax_rows = tax_rows.loc[valid_tax]
        if ("_header_source_file" in tax_rows and "_header_source_row" in tax_rows
                and tax_rows["_header_source_file"].notna().any() and tax_rows["_header_source_row"].notna().any()):
            cited = [
                (str(row["_header_source_file"]), row["_header_source_row"])
                for _, row in tax_rows.iterrows()
                if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
            ]
        elif "source_file" in tax_rows:
            cited = [(str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row")) for _, row in tax_rows.iterrows()]
        else:
            cited = row_ids.loc[tax_rows.index].tolist()
        return {
            "answer": f"Total recorded invoice tax: {tax_total:,.2f}.",
            "values": {"status": "ok", "total_invoice_tax": tax_total, "invoice_count": int(len(tax_rows)), "tax_field": invoice_tax_col},
            "source_rows": cited,
        }

    invoice_discount_col = next((c for c in ("invoice_discount", "invoice_discount_amount") if c in df and df[c].notna().any()), None)
    if (invoice_discount_col and re.search(r"\b(average|avg|mean)\b", q)
            and re.search(r"\bdiscount\b", q)
            and re.search(r"\b(invoice|receipt|bill)s?\b", q)
            and re.search(r"\b(positive|discounted|with\s+(?:a\s+)?(?:positive\s+)?discount)\b", q)):
        invoice_key = next((c for c in ("transaction_id", "invoice_id", "invoice_no", "bill_no", "receipt_id") if c in df and df[c].notna().any()), id_col)
        invoice_rows = df.loc[df[invoice_key].notna() & df[invoice_discount_col].notna()].drop_duplicates(subset=[invoice_key]).copy()
        discounts = pd.to_numeric(invoice_rows[invoice_discount_col], errors="coerce")
        positive_rows = invoice_rows.loc[discounts.gt(0)].copy()
        positive_discounts = pd.to_numeric(positive_rows[invoice_discount_col], errors="coerce")
        if positive_discounts.empty:
            return {"answer": "No positive invoice-level discounts are recorded in the selected sales headers.", "values": {"status": "ok", "invoice_count": 0, "average_invoice_discount": 0.0, "invoice_discount_field": invoice_discount_col}, "source_rows": []}
        average_discount = float(positive_discounts.mean())
        if ("_header_source_file" in positive_rows and "_header_source_row" in positive_rows
                and positive_rows["_header_source_file"].notna().any() and positive_rows["_header_source_row"].notna().any()):
            cited = [
                (str(row["_header_source_file"]), row["_header_source_row"])
                for _, row in positive_rows.iterrows()
                if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
            ]
        elif "source_file" in positive_rows:
            cited = [(str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row")) for _, row in positive_rows.iterrows()]
        else:
            cited = row_ids.loc[positive_rows.index].tolist()
        return {
            "answer": f"Average recorded invoice discount among {len(positive_discounts):,} invoices with a positive discount: {average_discount:,.2f}.",
            "values": {"status": "ok", "average_invoice_discount": average_discount, "invoice_count": int(len(positive_discounts)), "total_invoice_discount": float(positive_discounts.sum()), "invoice_discount_field": invoice_discount_col},
            "source_rows": cited,
        }

    # Generic grouped metrics: choose the requested dimension and measure
    # instead of letting words like "which" fall through to a list of IDs.
    product_col = next((c for c in ("product_id", "product_name", "medicine_name", "product_code") if c in df and df[c].notna().any()), None)
    category_col = next((c for c in ("category", "therapeutic_class", "drug_class") if c in df and df[c].notna().any()), None)
    payment_col = next((c for c in ("payment_method", "payment_type", "payment_mode") if c in df and df[c].notna().any()), None)
    date_col = next((c for c in ("date", "sale_date", "transaction_date", "invoice_date") if c in df and df[c].notna().any()), None)
    invoice_return_count = bool(
        re.search(r"\b(how many|count|number of)\b", q)
        and re.search(r"\b(returned|returns?|refunded|refunds?)\b", q)
        and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
    )
    if invoice_return_count:
        return_col = next((col for col in ("returned", "is_returned", "return_flag", "_extra.RETURNED") if col in df and df[col].notna().any()), None)
        if return_col:
            raw_flags = df[return_col]
            numeric_flags = pd.to_numeric(raw_flags, errors="coerce")
            text_flags = raw_flags.fillna("").astype(str).str.strip().str.casefold()
            returned_mask = numeric_flags.gt(0) | text_flags.isin({"true", "yes", "y", "returned", "return", "refunded", "refund"})
        elif "txn_type" in df and df.txn_type.notna().any():
            returned_mask = df.txn_type.fillna("").astype(str).str.casefold().str.contains(r"return|refund", regex=True)
            return_col = "txn_type"
        else:
            return {"answer": "The selected sales rows have no return flag or return transaction type, so I can't count returned invoices reliably.", "values": {"status": "unsupported_field", "required_field": "return flag or return transaction type"}, "source_rows": []}
        returned_rows = df.loc[returned_mask]
        returned_invoice_count = int(returned_rows[id_col].nunique()) if id_col else int(len(returned_rows))
        evidence_rows = returned_rows if not returned_rows.empty else df
        if "source_file" in evidence_rows and "source_row" in evidence_rows:
            cited_rows = [(str(row["source_file"]), row["source_row"]) for _, row in evidence_rows.iterrows() if pd.notna(row["source_row"])]
        else:
            cited_rows = row_ids.loc[evidence_rows.index].tolist()
        return {
            "answer": f"{returned_invoice_count:,} sales invoices include a recorded returned item.",
            "values": {"status": "ok", "returned_invoices": returned_invoice_count, "returned_lines": int(len(returned_rows)), "return_field": return_col},
            "source_rows": cited_rows,
        }
    def receipt_identifier_column():
        invoice_col = next((c for c in ("invoice_id", "receipt_id", "bill_no") if c in df and df[c].notna().any()), None)
        if invoice_col and "transaction_id" in df and df.transaction_id.notna().any():
            ids_per_label = df.loc[df[invoice_col].notna()].groupby(invoice_col).transaction_id.nunique()
            placeholders = {"today-rec", "today_rec", "today rec", "unknown", "n/a", "na", "none", "null", "placeholder", "dummy", "temporary", "temp", "0"}
            repeated_placeholder = any(int(count) > 1 and str(label).strip().casefold() in placeholders for label, count in ids_per_label.items())
            if repeated_placeholder:
                return "transaction_id"
        return invoice_col or id_col
    daily_receipt_count = bool(
        date_col and re.search(r"\b(day|daily|date)\b", q)
        and re.search(r"\b(highest|most|largest|top|which)\b", q)
        and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
        and re.search(r"\b(how many|count|number of)\b", q)
    )
    if daily_receipt_count:
        receipt_col = receipt_identifier_column()
        dates = pd.to_datetime(df[date_col], errors="coerce")
        work = df.loc[dates.notna() & df[receipt_col].notna()].copy()
        work["_day"] = pd.to_datetime(work[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
        receipt_counts = work.groupby("_day")[receipt_col].nunique().sort_values(ascending=False)
        if not receipt_counts.empty:
            day = str(receipt_counts.index[0])
            day_rows = work.loc[work["_day"].eq(day)].drop_duplicates(subset=[receipt_col])
            if "_header_source_file" in day_rows and "_header_source_row" in day_rows and day_rows._header_source_file.notna().any():
                citations = [
                    (str(row["_header_source_file"]), row["_header_source_row"])
                    for _, row in day_rows.iterrows()
                    if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
                ]
            else:
                citations = [(str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row")) for _, row in day_rows.iterrows()]
            count = int(receipt_counts.iloc[0])
            return {"answer": f"{day} had the most recorded sales invoices: {count:,}.", "values": {"status": "ok", "highest_receipt_count_day": day, "receipt_count": count}, "source_rows": citations}
    asks_product = bool(re.search(r"\b(product|products|medicine|medicines|item|items|drug|drugs)\b", q))
    if not asks_product and product_col:
        asks_product = any(
            len(str(name).strip()) >= 3
            and re.search(
                rf"(?<![\w]){re.escape(str(name).strip().casefold())}(?![\w])",
                q,
            )
            for name in df[product_col].dropna().unique()
            if str(name).strip()
        )
    asks_category = bool(re.search(r"\b(category|categories|class|classes)\b", q))
    top_match = re.search(r"\btop\s+(?P<top>\d+)\b|\b(?:list|show|give|name)\s+(?:me\s+)?(?:the\s+)?(?P<listed>\d+)\b", q)
    requested_top_n = int(top_match.group("top") or top_match.group("listed")) if top_match else None
    limit = min(requested_top_n, 100) if requested_top_n else 10
    metric_col = qty_col if (re.search(r"\b(units?|quantity|volume|most units|best[- ]selling|worst[- ]selling|fast[- ]selling|slow[- ]selling|sell(?:s|ing)? the most)\b", q) and not re.search(r"\b(revenue|sales value|amount)\b", q)) else value_col
    descending = not bool(re.search(r"\b(worst|lowest|least|smallest|lowest[- ]selling)\b", q))

    asks_profit = bool(re.search(r"\b(profit|profitable|profitability|margin|loss|sold at a loss|selling below|below (?:the )?purchase price)\b", q))
    if asks_profit and value_col:
        if re.search(r"\b(discount|promotion|markdown)\b", q) and not re.search(r"\bmargin\b", q):
            return {"answer": "Profit lost specifically to discounts cannot be isolated from the available fields without a recorded pre-discount selling price and a cost basis.", "values": {"status": "unsupported_field", "required_field": "pre-discount price and cost"}, "source_rows": []}
        line_cost_col = next((c for c in ("line_cost", "cost_of_goods_sold", "cogs", "gross_profit", "line_profit") if c in df), None)
        if line_cost_col and df[line_cost_col].notna().all():
            line_cost = pd.to_numeric(df[line_cost_col], errors="coerce")
            gross_profit = amounts - line_cost if line_cost_col not in ("gross_profit", "line_profit") else line_cost
        else:
            unit_cost_col = next((c for c in ("unit_cost", "cost_price", "purchase_price", "cost") if c in df), None)
            if unit_cost_col and qty_col and df[unit_cost_col].notna().all():
                line_cost = pd.to_numeric(df[unit_cost_col], errors="coerce") * quantities
                gross_profit = amounts - line_cost
            else:
                gross_profit = None
        if gross_profit is None or gross_profit.isna().any():
            return {"answer": "Gross profit is unavailable because the selected sales rows do not have complete recorded cost data.", "values": {"status": "unsupported_field", "required_field": "complete line cost or unit cost"}, "source_rows": []}
        if re.search(r"\b(highest|most profitable|top|lowest|loss|below (?:the )?purchase price|selling below)\b", q) and (asks_product or asks_category):
            group_col = category_col if re.search(r"\b(category|categories|class|classes)\b", q) else product_col
            if group_col:
                work = df.assign(_gross_profit=gross_profit, _sales_amount=amounts).dropna(subset=[group_col, "_gross_profit"])
                if re.search(r"\b(loss|below (?:the )?purchase price|selling below)\b", q):
                    work = work.loc[work._gross_profit.lt(0)]
                grouped = work.groupby(group_col, dropna=True).agg(gross_profit=("_gross_profit", "sum"), sales=("_sales_amount", "sum"))
                if re.search(r"\bmargin", q):
                    grouped["metric"] = grouped.gross_profit.div(grouped.sales.where(grouped.sales.ne(0))).mul(100)
                    group_metric = "gross_margin_pct"
                else:
                    grouped["metric"] = grouped.gross_profit
                    group_metric = "gross_profit"
                group_sums = grouped.metric.dropna().sort_values(ascending=not descending)
                if re.search(r"\b(lowest|least|loss|below (?:the )?purchase price|selling below)\b", q):
                    group_sums = group_sums.sort_values()
                chosen = group_sums.head(limit)
                results = [{"product" if group_col == product_col else "category": str(k), group_metric: float(v)} for k, v in chosen.items()]
                return {"answer": "Gross profit ranking: " + "; ".join(f"{r.get('product', r.get('category'))} ({list(r.values())[1]:,.2f})" for r in results) + ("." if results else " No matching products."), "values": {"status": "ok", "metric": group_metric, "results": results}, "source_rows": row_ids.loc[work.index].tolist()}
        total_profit = float(gross_profit.sum())
        revenue_total = float(amounts.sum())
        if re.search(r"\b(average|avg)\b.{0,35}\b(transaction|sale|receipt|invoice)\b", q):
            transaction_profit = gross_profit.groupby(df[id_col]).sum(min_count=1).dropna()
            average_profit = float(transaction_profit.mean()) if len(transaction_profit) else 0.0
            return {"answer": f"Average gross profit per transaction: {average_profit:,.2f} across {len(transaction_profit):,} transactions.", "values": {"status": "ok", "average_gross_profit_per_transaction": average_profit, "transaction_count": int(len(transaction_profit))}, "source_rows": row_ids.loc[df.index].tolist()}
        if re.search(r"\b(margin|percentage)\b", q):
            margin = 100 * total_profit / revenue_total if revenue_total else 0.0
            return {"answer": f"Gross profit margin: {margin:.2f}% (gross profit {total_profit:,.2f} on sales {revenue_total:,.2f}).", "values": {"status": "ok", "gross_profit": total_profit, "sales": revenue_total, "gross_margin_pct": margin}, "source_rows": row_ids.loc[df.index].tolist()}
        return {"answer": f"Estimated gross profit from recorded sales and cost: {total_profit:,.2f}.", "values": {"status": "ok", "gross_profit": total_profit, "sales": revenue_total, "cost_of_goods_sold": float(line_cost.sum())}, "source_rows": row_ids.loc[df.index].tolist()}

    def grouped_result(column, label, metric, metric_label):
        if not column or not metric:
            return None
        values = pd.to_numeric(df[metric], errors="coerce")
        work = df.assign(_metric=values).dropna(subset=[column, "_metric"])
        if work.empty:
            return None
        totals = work.groupby(column, dropna=True)._metric.sum().sort_values(ascending=not descending)
        runner_up = bool(re.search(r"\b(runner[- ]?up|second(?:[- ](?:highest|place|one))?|number two)\b", q))
        if re.search(r"\b(top\s+\d+|best[- ]selling|worst[- ]selling|most|highest|lowest|largest|least|which|each|per|by|runner[- ]?up|second(?:[- ](?:highest|place|one))?|number two)\b", q):
            chosen = totals.iloc[1:2] if runner_up else totals.head(limit)
            items = [{label: str(k), metric_label: float(v)} for k, v in chosen.items()]
            noun = "; ".join(f"{item[label]} ({item[metric_label]:,.2f})" for item in items)
            title = f"Second-ranked {label.lower()} by {metric_label.replace('_', ' ')}" if runner_up else (f"Top {len(items)} {label.lower()} by {metric_label.replace('_', ' ')}" if top_match else f"{label}s ranked by {metric_label.replace('_', ' ')}")
            return {"answer": title + ": " + noun + ".", "values": {"status": "ok", "group_by": label, "metric": metric_label, "results": items}, "source_rows": row_ids.loc[work.index].tolist()}
        return None

    asks_day = bool(re.search(r"\b(day|daily|per day)\b", q))
    if (discount_col and product_col and asks_product
            and re.search(r"\b(any|ever|were|was|has|have|did)\b", q)
            and re.search(r"\b(discount(?:ed)?|saving|price cut)\b", q)):
        product_names = sorted(
            (str(value) for value in df[product_col].dropna().unique()),
            key=len, reverse=True,
        )
        matched_name = next((name for name in product_names if name.casefold() in q), None)
        if not matched_name:
            return {"answer": "Which medicine do you mean? Please provide its product name.", "values": {"status": "needs_product_name"}, "source_rows": []}
        product_rows = df.loc[df[product_col].astype(str).str.casefold().eq(matched_name.casefold())]
        product_discounts = pd.to_numeric(product_rows[discount_col], errors="coerce")
        recorded_discounts = product_discounts.dropna()
        if recorded_discounts.empty:
            return {"answer": f"A discount is not recorded for {matched_name} in the selected sales rows.", "values": {"status": "unsupported_field", "product": matched_name, "required_field": discount_col}, "source_rows": row_ids.loc[product_rows.index].tolist()}
        total = float(recorded_discounts.sum())
        answer = (f"Yes. A total discount of {total:,.2f} is recorded for {matched_name} across {len(recorded_discounts)} sales lines."
                  if total > 0 else f"No discount was recorded for {matched_name} across {len(recorded_discounts)} selected sales lines.")
        return {"answer": answer, "values": {"status": "ok", "product": matched_name, "total_discount": total, "matched_records": int(len(recorded_discounts))}, "source_rows": row_ids.loc[product_rows.index].tolist()}
    if discount_col and re.search(r"\b(total|how much|sum|combined)\b", q) and re.search(r"\bdiscount", q) and not (asks_product and re.search(r"\b(largest|highest|most|top|which|products?)\b", q)):
        total_discount = float(pd.to_numeric(df[discount_col], errors="coerce").fillna(0).sum())
        return {"answer": f"Total recorded discounts: {total_discount:,.2f}.", "values": {"status": "ok", "total_discount": total_discount}, "source_rows": row_ids.loc[df.index].tolist()}
    if asks_day and date_col and re.search(r"\b(highest|most|lowest|least|trend|daily)\b", q):
        dates = pd.to_datetime(df[date_col], errors="coerce").dt.date
        grouped = pd.DataFrame({"_day": dates, "_amount": amounts}).dropna().groupby("_day")._amount.sum().sort_index()
        if len(grouped):
            if re.search(r"\b(highest|most)\b", q):
                grouped = grouped[grouped.eq(grouped.max())]
            elif re.search(r"\b(lowest|least)\b", q):
                grouped = grouped[grouped.eq(grouped.min())]
            elif len(grouped) > 31:
                grouped = grouped.tail(31)
            result = [{"date": str(day), "sales": float(value)} for day, value in grouped.items()]
            return {"answer": "Daily sales: " + "; ".join(f"{x['date']}: {x['sales']:,.2f}" for x in result) + ".", "values": {"status": "ok", "daily_sales": result}, "source_rows": row_ids.loc[df.index].tolist()}
    sales_group_metric = bool(re.search(r"\b(sales?|sold|sell|sells|selling|revenue|units?|quantity|volume|best[- ]selling|worst[- ]selling|fast[- ]selling|slow[- ]selling|frequently)\b", q))
    average_units_request = bool(
        re.search(r"\b(average|avg|mean)\b", q)
        and re.search(r"\b(units?|quantity|volume)\b", q)
        and re.search(r"\b(per|each|every)\b.{0,20}\b(transactions?|invoices?|bills?|receipts?)\b", q)
    )
    if average_units_request and qty_col:
        requested_document = re.search(r"\b(invoices?|bills?|receipts?)\b", q)
        average_id_col = (
            next((c for c in ("invoice_id", "invoice_no", "bill_no", "bill_number") if c in df and df[c].notna().any()), id_col)
            if requested_document else id_col
        )
        unit_rows = df.loc[df[average_id_col].notna()].copy()
        unit_rows["_quantity"] = pd.to_numeric(unit_rows[qty_col], errors="coerce")
        per_document = unit_rows.groupby(average_id_col, dropna=True)["_quantity"].sum(min_count=1).dropna()
        if not per_document.empty:
            total_units = float(per_document.sum())
            average_units = float(per_document.mean())
            document_label = "invoice" if requested_document else "transaction"
            source_rows = row_ids.loc[unit_rows.index].tolist()
            return {
                "answer": f"Average quantity sold per {document_label}: {average_units:,.2f} units across {len(per_document):,} {document_label}s ({total_units:,.0f} units total).",
                "values": {"status": "ok", "average_units_per_document": average_units, "document_count": int(len(per_document)), "total_units": total_units, "document_field": average_id_col, "quantity_field": qty_col},
                "source_rows": source_rows,
            }
    asks_payment_method = bool(re.search(r"\b(payment methods?|payment types?|payment modes?)\b", q))
    asks_receipt_frequency = bool(
        re.search(r"\b(receipt|transaction|invoice|bill)s?\b", q)
        and re.search(r"\b(unique|distinct|frequency|frequent|most often|most|highest|number of|count)\b", q)
    )
    if (asks_category and asks_receipt_frequency and category_col and id_col
            and not re.search(r"\b(revenue|amount|sales value|price|profit|margin|units?)\b", q)):
        receipt_counts = df.groupby(category_col, dropna=True)[id_col].nunique().sort_values(ascending=False)
        if not receipt_counts.empty:
            results = [{"category": str(name), "receipt_count": int(value)} for name, value in receipt_counts.head(limit).items()]
            winner = results[0]
            winning_receipts = df.loc[df[category_col].astype(str).eq(winner["category"])].drop_duplicates(subset=[id_col])
            if "source_file" in winning_receipts:
                cited = [(str(winning_receipts.loc[index].get("file_id") or winning_receipts.loc[index].get("source_file") or ""), row_ids.loc[index]) for index in winning_receipts.index]
            else:
                cited = row_ids.loc[winning_receipts.index].tolist()
            answer = f"{winner['category']} appears on the most unique sales receipts: {winner['receipt_count']:,}."
            return {"answer": answer, "values": {"status": "ok", "highest_receipt_category": winner["category"], "receipt_count": winner["receipt_count"], "results": results}, "source_rows": cited}
    line_count_request = bool(
        asks_category and category_col
        and re.search(r"\b(lines?|line items?|detail rows?|sales rows?)\b", q)
        and re.search(r"\b(most|highest|count|number of|how many)\b", q)
        and not re.search(r"\b(revenue|amount|sales value|price|profit|margin|units?|quantity)\b", q)
    )
    if line_count_request:
        counts = df.groupby(category_col, dropna=True).size().sort_values(ascending=False, kind="stable")
        if not counts.empty:
            highest = int(counts.iloc[0])
            leaders = [str(category) for category, count in counts.items() if int(count) == highest]
            winning_rows = df.loc[df[category_col].astype(str).isin(leaders)]
            details = [{"category": str(category), "sales_detail_lines": int(count)} for category, count in counts.items()]
            if "source_file" in winning_rows:
                cited = [(str(winning_rows.loc[index].get("file_id") or winning_rows.loc[index].get("source_file") or ""), row_ids.loc[index]) for index in winning_rows.index]
            else:
                cited = row_ids.loc[winning_rows.index].tolist()
            tied_text = " and ".join(leaders)
            answer = f"{tied_text} tie for the most sales detail lines, with {highest:,} each." if len(leaders) > 1 else f"{leaders[0]} has the most sales detail lines: {highest:,}."
            return {"answer": answer, "values": {"status": "ok", "top_categories_by_line_count": [{"category": category, "sales_detail_lines": highest} for category in leaders], "category_line_counts": details}, "source_rows": cited}
    if asks_payment_method and sales_group_metric and not re.search(r"\b(profit|margin|price|discount|return|refund)\b", q):
        result = grouped_result(payment_col, "Payment method", metric_col, "units_sold" if metric_col == qty_col else "sales")
        if result:
            return result
    if asks_category and sales_group_metric and not re.search(r"\b(profit|margin|price|discount|return|refund)\b", q):
        result = grouped_result(category_col, "Category", metric_col, "units_sold" if metric_col == qty_col else "sales")
        if result: return result
    if asks_product and sales_group_metric and not re.search(r"\b(profit|margin|price|discount|return|refund)\b", q):
        if re.search(r"\bfrequently|most often|most frequently\b", q):
            frequencies = df.groupby(product_col, dropna=True)[id_col].nunique().sort_values(ascending=False) if product_col else None
            if frequencies is not None and len(frequencies):
                chosen = frequencies.head(limit)
                results = [{"product": str(k), "transaction_count": int(v)} for k, v in chosen.items()]
                return {"answer": "Products ranked by number of transactions: " + "; ".join(f"{r['product']} ({r['transaction_count']})" for r in results) + ".", "values": {"status": "ok", "results": results}, "source_rows": row_ids.loc[df.index].tolist()}
        result = grouped_result(product_col, "Product", metric_col, "units_sold" if metric_col == qty_col else "sales")
        if result: return result

    if asks_product and discount_col and re.search(r"\b(discount|discounts)\b", q) and re.search(r"\b(largest|highest|most|top|which|products?|runner[- ]?up|second(?:[- ](?:highest|place|one))?|number two)\b", q):
        result = grouped_result(product_col, "Product", discount_col, "discount")
        if result: return result
    if (asks_product and product_col and price_col
            and re.search(r"\b(most often|most common|recorded most|mode|frequency|frequently)\b", q)
            and re.search(r"\b(price|priced|selling)\b", q)):
        product_names = [str(value).strip() for value in df[product_col].dropna().unique() if str(value).strip()]
        matched_product = next((name for name in sorted(product_names, key=len, reverse=True) if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", q)), None)
        if matched_product:
            product_rows = df.loc[df[product_col].astype(str).str.casefold().eq(matched_product.casefold())].copy()
            product_rows["_price_value"] = pd.to_numeric(product_rows[price_col], errors="coerce")
            product_rows = product_rows.dropna(subset=["_price_value"])
            frequencies = product_rows["_price_value"].value_counts().sort_index()
            if not frequencies.empty:
                highest_frequency = int(frequencies.max())
                common_prices = [float(price) for price, frequency in frequencies.items() if int(frequency) == highest_frequency]
                common_rows = product_rows.loc[product_rows["_price_value"].isin(common_prices)]
                price_text = ", ".join(f"{price:,.2f}" for price in common_prices)
                if "source_file" in common_rows:
                    cited_rows = [
                        (str(common_rows.loc[index].get("file_id") or common_rows.loc[index].get("source_file") or ""), row_ids.loc[index])
                        for index in common_rows.index
                    ]
                else:
                    cited_rows = row_ids.loc[common_rows.index].tolist()
                return {
                    "answer": f"Most frequently recorded selling price for {matched_product}: {price_text} ({highest_frequency} sales lines).",
                    "values": {"status": "ok", "product": matched_product, "most_common_selling_price": common_prices, "frequency": highest_frequency, "price_counts": {str(float(price)): int(frequency) for price, frequency in frequencies.items()}},
                    "source_rows": cited_rows,
                }
    if asks_product and price_col and re.search(r"\b(highest|lowest|most|least)\b.{0,35}\b(selling )?price\b|\b(selling price)\b.{0,30}\b(highest|lowest|most|least)\b", q):
        prices = pd.to_numeric(df[price_col], errors="coerce")
        groups = df.assign(_price=prices).dropna(subset=[product_col, "_price"]).groupby(product_col)._price
        extreme = groups.max() if re.search(r"\b(highest|most)\b", q) else groups.min()
        target = extreme.max() if re.search(r"\b(highest|most)\b", q) else extreme.min()
        products = extreme[extreme.eq(target)]
        direction = "highest" if re.search(r"\b(highest|most)\b", q) else "lowest"
        return {"answer": f"Product(s) with the {direction} recorded selling price ({target:,.2f}): " + ", ".join(str(x) for x in products.index) + ".", "values": {"status": "ok", "selling_price": float(target), "price_direction": direction, "products": [str(x) for x in products.index]}, "source_rows": row_ids.loc[df.index[df[product_col].astype(str).isin([str(x) for x in products.index])]].tolist()}

    receipt_product_threshold = re.search(
        r"\b(?:more than|over|at least)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:distinct\s+|unique\s+|different\s+)?(?:products?|medicines?|items?)\b",
        q,
    )
    asks_receipt_count = bool(re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q))
    if (asks_receipt_count and receipt_product_threshold and product_col and id_col
            and re.search(r"\b(how many|count|number of)\b", q)):
        threshold_text = receipt_product_threshold.group(1)
        threshold = int(threshold_text) if threshold_text.isdigit() else {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }[threshold_text]
        products_per_receipt = df.groupby(id_col, dropna=True)[product_col].nunique()
        qualifying_ids = products_per_receipt.index[products_per_receipt.gt(threshold)]
        qualifying_rows = df[id_col].isin(qualifying_ids)
        count = int(len(qualifying_ids))
        indexes = df.index[qualifying_rows]
        if "source_file" in df:
            cited = [(str(df.loc[idx].get("source_file") or ""), row_ids.loc[idx]) for idx in indexes]
        else:
            cited = row_ids.loc[indexes].tolist()
        entity = "receipt" if re.search(r"\breceipts?\b", q) else "transaction" if re.search(r"\btransactions?\b", q) else "invoice"
        return {
            "answer": f"{count:,} {entity}{'' if count == 1 else 's'} included more than {threshold} different products.",
            "values": {"status": "ok", "qualifying_receipts": count, "distinct_product_threshold": threshold},
            "source_rows": cited,
        }

    if asks_product and product_col and re.search(r"\b(how many|count|number of)\b", q) and re.search(r"\b(unique|distinct|different)\b", q):
        distinct_products = int(df[product_col].dropna().astype(str).nunique())
        return {
            "answer": f"{distinct_products:,} distinct products in the selected sales records.",
            "values": {"status": "ok", "distinct_products": distinct_products},
            "source_rows": row_ids.loc[df.index[df[product_col].notna()]].tolist(),
        }
    if re.search(r"\b(how many|count|number of)\b", q) and re.search(r"\b(returned|returns?|refunded|refunds?)\b", q):
        return_col = next((col for col in ("returned", "is_returned", "return_flag", "_extra.RETURNED") if col in df and df[col].notna().any()), None)
        if return_col:
            raw_flags = df[return_col]
            numeric_flags = pd.to_numeric(raw_flags, errors="coerce")
            text_flags = raw_flags.fillna("").astype(str).str.strip().str.casefold()
            returned_mask = numeric_flags.gt(0) | text_flags.isin({"true", "yes", "y", "returned", "return", "refunded", "refund"})
        elif "txn_type" in df and df.txn_type.notna().any():
            returned_mask = df.txn_type.fillna("").astype(str).str.casefold().str.contains(r"return|refund", regex=True)
            return_col = "txn_type"
        else:
            return {"answer": "The selected sales rows have no return flag or return transaction type, so I can't count returned lines reliably.", "values": {"status": "unsupported_field", "required_field": "return flag or return transaction type"}, "source_rows": []}
        return_count = int(returned_mask.sum())
        cited_indexes = df.index[returned_mask] if return_count else df.index
        if "source_file" in df:
            cited_rows = [(str(df.loc[idx].get("source_file") or ""), row_ids.loc[idx]) for idx in cited_indexes]
        else:
            cited_rows = row_ids.loc[cited_indexes].tolist()
        noun = "line" if return_count == 1 else "lines"
        return {
            "answer": f"{return_count:,} sales {noun} are marked as returned.",
            "values": {"status": "ok", "returned_lines": return_count, "return_field": return_col},
            "source_rows": cited_rows,
        }
    if (discount_col and not asks_product
            and re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(lines?|sales detail|invoice detail|bill detail)\b", q)
            and not re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
            and re.search(r"\b(discount(?:ed)?|discounts?)\b", q)):
        discounts = pd.to_numeric(df[discount_col], errors="coerce").fillna(0)
        discounted_lines = df.loc[discounts.gt(0)]
        if "source_file" in discounted_lines:
            cited = [
                (str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row"))
                for _, row in discounted_lines.iterrows()
            ]
        else:
            cited = row_ids.loc[discounted_lines.index].tolist()
        line_count = int(len(discounted_lines))
        return {
            "answer": f"{line_count:,} sales lines have a recorded discount.",
            "values": {"status": "ok", "discounted_lines": line_count, "discount_field": discount_col},
            "source_rows": cited,
        }
    if (discount_col and not asks_product
            and re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
            and re.search(r"\b(discount(?:ed)?|discounts?)\b", q)):
        discounts = pd.to_numeric(df[discount_col], errors="coerce").fillna(0)
        discounted_rows = df.loc[discounts.gt(0)].drop_duplicates(subset=[id_col])
        receipt_count = int(discounted_rows[id_col].nunique())
        if "source_file" in discounted_rows:
            cited = [(str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row")) for _, row in discounted_rows.iterrows()]
        else:
            cited = row_ids.loc[discounted_rows.index].tolist()
        return {
            "answer": f"{receipt_count:,} receipts included at least one sales line with a recorded discount.",
            "values": {"status": "ok", "receipts_with_discount": receipt_count, "discounted_lines": int(discounts.gt(0).sum())},
            "source_rows": cited,
        }
    if (not asks_product
            and re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
            and re.search(r"\bstatus\b", q)):
        status_col = next((c for c in ("status", "status_code", "invoice_status") if c in df and df[c].notna().any()), None)
        requested_status = re.search(r"\bstatus(?:\s+field)?\s+(?:of\s+|is\s+)?([\w-]+)", q)
        if status_col and requested_status:
            status_value = requested_status.group(1).casefold()
            matched = df.loc[
                df[status_col].notna()
                & df[status_col].astype(str).str.strip().str.casefold().eq(status_value)
                & df[id_col].notna()
            ].drop_duplicates(subset=[id_col])
            receipt_count = int(matched[id_col].nunique())
            if "_header_source_file" in matched and "_header_source_row" in matched:
                cited = [
                    (str(row["_header_source_file"]), row["_header_source_row"])
                    for _, row in matched.iterrows()
                    if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
                ]
            elif "source_file" in matched:
                cited = [
                    (str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row"))
                    for _, row in matched.iterrows()
                ]
            else:
                cited = row_ids.loc[matched.index].tolist()
            return {
                "answer": f"{receipt_count:,} receipts have recorded status {requested_status.group(1)}.",
                "values": {"status": "ok", "receipt_count": receipt_count, "receipt_status": requested_status.group(1), "status_field": status_col},
                "source_rows": cited,
            }
    asks_positive_balance_count = bool(
        re.search(r"\b(how many|count|number of)\b", q)
        and re.search(r"\b(balance|amount due|receivable|owed|owing)\b", q)
        and re.search(r"\b(positive|above zero|greater than zero|nonzero|outstanding|due)\b", q)
        and re.search(r"\b(receipts?|transactions?|invoices?|bills?|headers?)\b", q)
    )
    if asks_positive_balance_count:
        balance_col = next((col for col in ("outstanding_balance", "balance_due", "amount_due", "customer_balance", "receivable_balance") if col in df and df[col].notna().any()), None)
        if balance_col:
            grain_cols = [col for col in ("_header_source_file", "_header_source_row") if col in df]
            if len(grain_cols) != 2:
                grain_cols = [col for col in ("source_file", "source_row") if col in df]
            work = df.loc[df[balance_col].notna() & df[id_col].notna()].copy()
            if len(grain_cols) == 2:
                work = work.drop_duplicates(subset=grain_cols)
            work["_balance_value"] = pd.to_numeric(work[balance_col], errors="coerce")
            positive = work.loc[work["_balance_value"].gt(0)]
            if len(grain_cols) == 2:
                citations = list(dict.fromkeys((str(row[grain_cols[0]]), row[grain_cols[1]]) for _, row in positive.iterrows()))
            else:
                citations = row_ids.loc[positive.index].tolist()
            return {"answer": f"{len(positive):,} of {len(work):,} receipt headers have a positive recorded balance due.", "values": {"status": "ok", "receipts_with_positive_balance": int(len(positive)), "receipt_headers_checked": int(len(work)), "balance_field": balance_col}, "source_rows": citations}
    count_request = re.search(r"\b(how many|count|number of)\b.{0,40}\b(transactions?|sales|invoices?|bills?|receipts?)\b", q)
    if not asks_product and count_request:
        requested_grain = count_request.group(2)
        count_col = (
            next((c for c in ("invoice_id", "invoice_no", "bill_no", "bill_number") if c in df and df[c].notna().any()), id_col)
            if re.fullmatch(r"invoices?|bills?", requested_grain)
            else next((c for c in ("receipt_id", "receipt_no", "receipt_number", "invoice_id") if c in df and df[c].notna().any()), id_col)
            if re.fullmatch(r"receipts?", requested_grain)
            else next((c for c in ("transaction_id", "order_id") if c in df and df[c].notna().any()), id_col)
        )
        counted_rows = df.loc[df[count_col].notna()].drop_duplicates(subset=[count_col])
        grain_count = int(counted_rows[count_col].nunique())
        label = "invoice" if re.fullmatch(r"invoices?|bills?", requested_grain) else "receipt" if re.fullmatch(r"receipts?", requested_grain) else "transaction"
        if "_header_source_file" in counted_rows and "_header_source_row" in counted_rows:
            cited = [
                (str(row["_header_source_file"]), row["_header_source_row"])
                for _, row in counted_rows.iterrows()
                if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
            ]
        elif "source_file" in counted_rows:
            cited = [(str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row")) for _, row in counted_rows.iterrows()]
        else:
            cited = row_ids.loc[counted_rows.index].tolist()
        return {"answer": f"{grain_count:,} distinct {label}{'' if grain_count == 1 else 's'}.", "values": {"status": "ok", "transaction_count": grain_count, "count_field": count_col}, "source_rows": cited}
    if re.search(r"\b(average|avg)\b.{0,35}\b(transaction|sale|receipt|invoice)\b", q) and value_col:
        totals = pd.to_numeric(df[value_col], errors="coerce").groupby(df[id_col]).sum(min_count=1).dropna()
        avg = float(totals.mean()) if len(totals) else 0.0
        return {"answer": f"Average transaction value: {_format_money(avg)} across {len(totals):,} transactions.", "values": {"status": "ok", "average_transaction_value": avg, "transaction_count": int(len(totals))}, "source_rows": row_ids.loc[df.index].tolist()}

    entity_specs = (
        ("customer", ("customer_id", "customer_name", "customer_alias")),
        ("product", ("product_id", "product_name", "medicine_name", "product_code")),
        ("branch", ("branch", "branch_name", "warehouse")),
        ("category", ("category", "therapeutic_class", "drug_class")),
        ("supplier", ("supplier_name", "supplier_id")),
    )
    hits_by_entity = {}
    for label, columns in entity_specs:
        col = next((c for c in columns if c in df), None)
        if not col: continue
        values = sorted((str(v) for v in df[col].dropna().unique() if str(v).strip()), key=len, reverse=True)
        hits = [v for v in values if re.search(rf"(?<!\w){re.escape(v.casefold())}(?!\w)", q)]
        if hits: hits_by_entity[label] = (col, hits)

    # A named customer that is absent must not silently turn into a whole-store total.
    if "customer" in q and any(c in df for c in ("customer_id", "customer_name", "customer_alias")) and "customer" not in hits_by_entity:
        match = re.search(r"\bcustomer\s+[\"'“‘]?([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+)+)", q)
        if match:
            candidate = re.sub(r"[\"'”’.,?]+$", "", match.group(1)).strip()
            first_word = candidate.split()[0].casefold()
            if first_word not in {"ko", "ne", "se", "ki", "ke", "par", "ka", "the", "a", "an", "discount", "diya", "given", "received"}:
                return {"answer": f"No records were found for customer {candidate.title()} in the selected data.", "values": {"matched_records": 0}, "source_rows": []}

    compare = bool(re.search(r"\b(compare|versus|\bvs\b|difference between|more than)\b", q))
    if not compare:
        for label, (col, values) in hits_by_entity.items():
            if len(values) == 1:
                df = df[df[col].astype(str).str.casefold().eq(values[0].casefold())]

    # Scope comparisons are computed on the complete table, with one shared
    # period predicate applied to all entities.
    if "date" in df:
        dates = pd.to_datetime(df.date, errors="coerce")
        date_tokens = re.findall(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", q)
        if len(date_tokens) >= 2:
            start, end = (pd.Timestamp(*map(int, v)) for v in date_tokens[:2])
            df = df[(dates.dt.normalize() >= start) & (dates.dt.normalize() <= end)]
        elif len(date_tokens) == 1 and re.search(r"\b(before|prior to|earlier than)\b", q):
            target = pd.Timestamp(*map(int, date_tokens[0])); df = df[dates.dt.normalize() < target]
        elif len(date_tokens) == 1 and re.search(r"\b(after|later than)\b", q):
            target = pd.Timestamp(*map(int, date_tokens[0])); df = df[dates.dt.normalize() > target]
        elif len(date_tokens) == 1 and re.search(r"\b(on|made|recorded|sold|sales|sale)\b", q):
            target = pd.Timestamp(*map(int, date_tokens[0])); df = df[dates.dt.date == target.date()]
        else:
            months = {"january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,"may":5,"june":6,"jun":6,"july":7,"jul":7,"august":8,"aug":8,"september":9,"sep":9,"october":10,"oct":10,"november":11,"nov":11,"december":12,"dec":12}
            month = next((m for name,m in months.items() if re.search(rf"\b{name}\b",q)),None)
            year = re.search(r"\b(20\d{2})\b", q)
            if month and year: df = df[(dates.dt.month == month) & (dates.dt.year == int(year.group(1)))]
            elif year and re.search(r"\b(in|during|year)\b", q): df = df[dates.dt.year == int(year.group(1))]

    month_receipt_count = bool(
        date_col and re.search(r"\b(month|monthly|calendar month)\b", q)
        and re.search(r"\b(highest|most|largest|top|which)\b", q)
        and re.search(r"\b(receipts?|transactions?|invoices?|bills?)\b", q)
        and re.search(r"\b(how many|count|number of|how many were)\b", q)
    )
    if month_receipt_count:
        receipt_col = receipt_identifier_column()
        dates = pd.to_datetime(df[date_col], errors="coerce")
        work = df.loc[dates.notna() & df[receipt_col].notna()].copy()
        work["_month"] = pd.to_datetime(work[date_col], errors="coerce").dt.to_period("M").astype(str)
        receipt_counts = work.groupby("_month")[receipt_col].nunique().sort_values(ascending=False)
        if not receipt_counts.empty:
            month = str(receipt_counts.index[0])
            period_rows = work.loc[work["_month"].eq(month)].drop_duplicates(subset=[receipt_col])
            if "_header_source_file" in period_rows and "_header_source_row" in period_rows and period_rows._header_source_file.notna().any():
                citations = [
                    (str(row["_header_source_file"]), row["_header_source_row"])
                    for _, row in period_rows.iterrows()
                    if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
                ]
            else:
                citations = [
                    (str(row.get("file_id") or row.get("source_file") or ""), row.get("source_row"))
                    for _, row in period_rows.iterrows()
                ]
            month_label = pd.Period(month, freq="M").strftime("%B %Y")
            count = int(receipt_counts.iloc[0])
            return {
                "answer": f"{month_label} had the most recorded sales receipts: {count:,}.",
                "values": {"status": "ok", "highest_receipt_count_month": month, "receipt_count": count},
                "source_rows": citations,
            }

    if (date_col and value_col
            and re.search(r"\b(month|monthly)\b", q)
            and re.search(r"\b(highest|most|largest|top|which)\b", q)
            and re.search(r"\b(revenue|sales|amount|value)\b", q)):
        dates = pd.to_datetime(df[date_col], errors="coerce")
        valid = dates.notna() & pd.to_numeric(df[value_col], errors="coerce").notna()
        work = df.loc[valid].copy()
        if not work.empty:
            work["_month"] = pd.to_datetime(work[date_col], errors="coerce").dt.to_period("M").astype(str)
            totals = pd.to_numeric(work[value_col], errors="coerce").groupby(work["_month"]).sum().sort_values(ascending=False)
            month = str(totals.index[0])
            period_rows = work.loc[work["_month"].eq(month)]
            month_label = pd.Period(month, freq="M").strftime("%B %Y")
            total = float(totals.iloc[0])
            citations = [(str(period_rows.loc[index].get("source_file") or ""), row_ids.loc[index]) for index in period_rows.index]
            return {
                "answer": f"{month_label} had the highest recorded sales revenue: {total:,.2f}.",
                "values": {"status": "ok", "highest_revenue_month": month, "sales_revenue": total},
                "source_rows": citations,
            }

    # Strict and inclusive numeric boundaries remain explicit.
    for col, label_patterns in ((qty_col, r"quantity|units?"), (discount_col, r"discount"), (value_col, r"total[_ ]amount|invoice[_ ]total|amount|sales value"), (price_col, r"unit[_ ]price|price")):
        if not col: continue
        m = re.search(rf"\b(?:{label_patterns})\b.{{0,35}}?\b(exactly|equal to|greater than|more than|over|above|at least|less than|below|under|at most)\s*(?:pkr\s*)?([\d,]+(?:\.\d+)?)", q)
        if not m: continue
        val = float(m.group(2).replace(",", "")); series = pd.to_numeric(df[col], errors="coerce"); op=m.group(1)
        if op in ("exactly", "equal to"): df=df[series.eq(val)]
        elif op in ("greater than", "more than", "over", "above"): df=df[series.gt(val)]
        elif op == "at least": df=df[series.ge(val)]
        elif op in ("less than", "below", "under"): df=df[series.lt(val)]
        elif op == "at most": df=df[series.le(val)]

    if df.empty:
        return {"answer": "No matching sales records were found in the selected data.", "values": {"matched_records": 0}, "source_rows": []}
    amounts = pd.to_numeric(df[value_col], errors="coerce") if value_col else pd.Series(0.0, index=df.index)
    quantities = pd.to_numeric(df[qty_col], errors="coerce") if qty_col else pd.Series(0.0, index=df.index)
    count = int(df[id_col].nunique())

    asks_outstanding_balance = bool(
        re.search(r"\b(outstanding|unpaid|amount due|balance due|receivable|owed|still owing)\b", q)
        and re.search(r"\b(balance|amount|receivable|due|owe|owing|unpaid)\b", q)
    )
    if asks_outstanding_balance:
        balance_col = next((c for c in ("outstanding_balance", "balance_due", "amount_due", "customer_balance", "receivable_balance") if c in df and df[c].notna().any()), None)
        if balance_col:
            work = df.loc[df[id_col].notna(), [id_col, balance_col, *[c for c in ("source_file", "source_row", "file_id", "_header_source_file", "_header_source_row") if c in df]]].copy()
            work["_balance"] = pd.to_numeric(work[balance_col], errors="coerce")
            balances = work.groupby(id_col, dropna=True)["_balance"].agg(lambda values: values.dropna().iloc[0] if values.notna().any() else float("nan")).dropna()
            source_rows = []
            for _, row in work.drop_duplicates(subset=[id_col]).iterrows():
                if "_header_source_file" in row and pd.notna(row.get("_header_source_file")) and pd.notna(row.get("_header_source_row")):
                    source_rows.append((str(row["_header_source_file"]), row["_header_source_row"]))
                elif pd.notna(row.get("source_row")):
                    source_rows.append((str(row.get("file_id") or row.get("source_file") or ""), row["source_row"]))
            if balances.empty:
                return {"answer": "The selected sales records have no usable outstanding-balance values, so I can't total receivables reliably.", "values": {"status": "unsupported_field", "required_field": balance_col}, "source_rows": []}
            total = float(balances.sum())
            positive = int(balances.gt(0).sum())
            return {
                "answer": f"Total recorded outstanding balance: {total:,.2f} across {positive:,} receipts with a positive balance.",
                "values": {"status": "ok", "total_outstanding_balance": total, "receipts_with_balance": positive, "receipts_checked": int(len(balances)), "balance_field": balance_col},
                "source_rows": source_rows,
            }
        invoice_total_col = "invoice_total" if "invoice_total" in df and df.invoice_total.notna().any() else None
        paid_col = "paid_amount" if "paid_amount" in df and df.paid_amount.notna().any() else None
        if invoice_total_col and paid_col:
            work = df.loc[df[id_col].notna(), [id_col, invoice_total_col, paid_col, *[c for c in ("source_file", "source_row", "file_id", "_header_source_file", "_header_source_row") if c in df]]].copy()
            for col in (invoice_total_col, paid_col):
                work[col] = pd.to_numeric(work[col], errors="coerce")
            unique = work.drop_duplicates(subset=[id_col]).dropna(subset=[invoice_total_col, paid_col])
            balances = (unique[invoice_total_col] - unique[paid_col]).clip(lower=0)
            total = float(balances.sum())
            source_rows = [(str(row.get("_header_source_file") or row.get("file_id") or row.get("source_file") or ""), row.get("_header_source_row") if pd.notna(row.get("_header_source_row")) else row.get("source_row")) for _, row in unique.iterrows()]
            return {"answer": f"Total recorded outstanding balance: {total:,.2f} across {int(balances.gt(0).sum()):,} receipts with a positive balance.", "values": {"status": "ok", "total_outstanding_balance": total, "receipts_with_balance": int(balances.gt(0).sum()), "receipts_checked": int(len(balances)), "balance_field": "invoice_total minus paid_amount"}, "source_rows": source_rows}
        return {"answer": "The selected sales data has no outstanding-balance field or complete paid and invoice totals, so I can't calculate receivables reliably.", "values": {"status": "unsupported_field", "required_field": "outstanding balance or paid/invoice totals"}, "source_rows": []}

    if qty_col and value_col and re.search(r"\b(total|combined|sum)\b", q) and re.search(r"\b(quantity|units?)\b", q) and re.search(r"\b(amount|revenue|sales value|sales amount)\b", q):
        total_qty, total_amount = float(quantities.sum()), float(amounts.sum())
        return {"answer": f"Total quantity: {total_qty:,.0f}; total sales amount: {total_amount:,.2f}.", "values": {"total_quantity": total_qty, "total_amount": total_amount, "total_sales": total_amount}, "source_rows": row_ids.loc[df.index].tolist()}

    # Keep a request for all units as a scalar total. A generic "total" phrase
    # must not accidentally select revenue merely because both fields exist.
    if qty_col and re.search(r"\b(total|how many|number of|combined)\b", q) and re.search(r"\b(units?|quantity|qty)\b.{0,50}\b(sold|sell|sells|selling|across|all|total)\b|\b(?:sold|sell|sells|selling)\b.{0,35}\b(units?|quantity|qty)\b", q) and not re.search(r"\b(by|per|each|top|which|most)\s+(?:product|medicine|month|day|branch|category)\b", q) and not re.search(r"\b(revenue|sales amount|sales value|amount)\b", q):
        total_qty = float(quantities.sum())
        return {"answer": f"Total units sold: {total_qty:,.0f}.", "values": {"total_quantity_sold": total_qty}, "source_rows": row_ids.loc[df.index].tolist()}

    if discount_col and re.search(r"\b(discount|discounted)\b", q) and re.search(r"\b(which|what|list|show|kin|kon si|konsi)\b", q):
        discounts = pd.to_numeric(df[discount_col], errors="coerce").fillna(0)
        discounted = df.loc[discounts > 0]
        product_col = next((c for c in ("product_id", "product_name", "medicine_name", "product_code") if c in df), None)
        if product_col:
            products = discounted[product_col].dropna().astype(str).drop_duplicates().tolist()
            return {"answer": "Products with a recorded discount: " + (", ".join(products[:50]) if products else "none") + (" (list capped at 50)." if len(products) > 50 else "."), "values": {"discounted_products": products, "count": len(products)}, "source_rows": row_ids.loc[discounted.index].tolist()}

    if compare:
        compare_entity = next(((label,col,vals) for label,(col,vals) in hits_by_entity.items() if len(vals)>=2),None)
        if compare_entity:
            label,col,entities=compare_entity; parts=[]; results={}; cited=[]
            for entity in entities[:2]:
                part=frame[frame[col].astype(str).str.casefold().eq(entity.casefold())]
                vals=pd.to_numeric(part[value_col],errors="coerce") if value_col else pd.Series(0,index=part.index)
                n=int(part[id_col].nunique()); total=float(vals.sum()); avg=total/n if n else 0.0
                info=[f"total {total:,.2f}",f"{n} transactions",f"average {avg:,.2f}"]
                results[entity]={"total":total,"transactions":n,"average":avg}
                parts.append(f"{entity}: " + ", ".join(info)); cited.extend(row_ids.loc[part.index].tolist())
            difference=abs(results[entities[0]]["total"]-results[entities[1]]["total"])
            return {"answer": "; ".join(parts)+f". Difference: {difference:,.2f}.","values":{"comparison":results,"difference":difference},"source_rows":cited}

    date_only_latest_request = bool(
        re.search(r"\b(what|which|date|when)\b", q)
        and re.search(r"\b(date|when)\b", q)
        and re.search(r"\b(latest|most recent)\b", q)
        and re.search(r"\b(sales?|receipts?|transactions?)\b", q)
        and not re.search(r"\b(id|number|amount|total|customer|product|how many|count)\b", q)
    )
    if date_only_latest_request and "date" in df:
        dates = pd.to_datetime(df.date, errors="coerce")
        if dates.notna().any():
            latest_date = dates.max()
            latest_rows = df.loc[dates.eq(latest_date)]
            if "_header_source_file" in latest_rows and "_header_source_row" in latest_rows:
                citations = list(dict.fromkeys(
                    (str(row["_header_source_file"]), row["_header_source_row"])
                    for _, row in latest_rows.iterrows()
                    if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
                ))
            else:
                citations = row_ids.loc[latest_rows.index].tolist()
            day = latest_date.date().isoformat()
            return {
                "answer": f"The most recent recorded sales receipt date is {day}.",
                "values": {"status": "ok", "latest_sales_date": day},
                "source_rows": citations,
            }

    if re.search(r"\b(earliest|latest|first|most recent)\b", q) and "date" in df:
        ds=pd.to_datetime(df.date,errors="coerce"); choices=[]
        if re.search(r"\b(earliest|first)\b",q): choices.append(("Earliest",ds.idxmin()))
        if re.search(r"\b(latest|most recent)\b",q): choices.append(("Latest",ds.idxmax()))
        parts=[]; vals={}; cites=[]
        for label,idx in choices:
            row=df.loc[idx]; day=str(ds.loc[idx].date()); sale=row[id_col]
            same=df.loc[ds.dt.date==ds.loc[idx].date()]; ids=same[id_col].astype(str).tolist()
            details=[str(row[c]) for c in ("customer_id","product_id","branch") if c in row and pd.notna(row[c])]
            parts.append(f"{label}: {sale} on {day}"+(f" ({'; '.join(details)})" if details else "")+f"; {len(ids)} sale(s) on that date")
            vals[label.casefold()]={"transaction_id":sale,"date":day,"transaction_ids_on_date":ids}; cites.extend(row_ids.loc[same.index].tolist())
        if parts: return {"answer":"; ".join(parts)+".","values":vals,"source_rows":cites}

    # Distinct field extrema (e.g., unit price vs transaction amount) are not
    # interchangeable. Return every tied row for the requested measure.
    if value_col and discount_col and re.search(r"\b(highest|largest|maximum|max)\b.{0,45}\b(total[_ ]amount|amount)\b",q) and re.search(r"\b(highest|largest|maximum|max)\b.{0,45}\bdiscount\b",q):
        amount_values=pd.to_numeric(df[value_col],errors="coerce"); discount_values=pd.to_numeric(df[discount_col],errors="coerce")
        top_amount=df.loc[amount_values.eq(amount_values.max())]; top_discount=df.loc[discount_values.eq(discount_values.max())]
        amount_ids=top_amount[id_col].astype(str).tolist(); discount_ids=top_discount[id_col].astype(str).tolist()
        cited_index=top_amount.index.union(top_discount.index)
        return {"answer":f"Highest total amount: {amount_values.max():,.2f} ({', '.join(amount_ids)}). Highest discount: {discount_values.max():,.2f} ({', '.join(discount_ids)}).","values":{"highest_total_amount":float(amount_values.max()),"highest_amount_sale_ids":amount_ids,"highest_discount":float(discount_values.max()),"highest_discount_sale_ids":discount_ids},"source_rows":row_ids.loc[cited_index].tolist()}

    if price_col and re.search(r"\b(highest|maximum|max)\b.{0,35}\b(unit price|price)\b",q):
        vals=pd.to_numeric(df[price_col],errors="coerce"); chosen=df[vals.eq(vals.max())]
        products=chosen.product_id.dropna().astype(str).unique().tolist() if "product_id" in chosen else []
        return {"answer":f"Highest unit price: {vals.max():,.2f} per unit. Product(s): "+"; ".join(products)+f". {len(chosen)} sale records use this price.","values":{"highest_unit_price":float(vals.max()),"products":products},"source_rows":row_ids.loc[chosen.index].tolist()}
    if re.search(r"\b(highest|largest|maximum|max|most)\b", q) and re.search(r"\b(invoice|receipt|bill)\s+total\b", q):
        header_total_col = next((col for col in ("invoice_total", "net_payable") if col in df and df[col].notna().any()), None)
        if header_total_col:
            header_frame = df.loc[df[header_total_col].notna()].copy()
            header_file_col = "_header_source_file" if "_header_source_file" in header_frame else "source_file" if "source_file" in header_frame else None
            header_row_col = "_header_source_row" if "_header_source_row" in header_frame else "source_row" if "source_row" in header_frame else None
            if header_file_col and header_row_col:
                header_frame = header_frame.drop_duplicates([header_file_col, header_row_col])
            elif "table_name" in header_frame:
                header_mask = header_frame.table_name.fillna("").astype(str).str.contains(r"header|receipt|invoice", case=False, regex=True)
                header_frame = header_frame.loc[header_mask] if header_mask.any() else header_frame
            header_values = pd.to_numeric(header_frame[header_total_col], errors="coerce")
            if header_values.notna().any():
                maximum = float(header_values.max())
                chosen = header_frame.loc[header_values.eq(maximum)]
                sale_ids = chosen[id_col].dropna().astype(str).unique().tolist()
                if header_file_col and header_row_col:
                    citations = list(dict.fromkeys((str(row[header_file_col]), row[header_row_col]) for _, row in chosen.iterrows()))
                else:
                    citations = row_ids.loc[chosen.index].tolist()
                return {"answer": f"Largest recorded invoice total: {maximum:,.2f}. Receipt(s): {', '.join(sale_ids)}.", "values": {"highest_invoice_total": maximum, "receipt_ids": sale_ids, "invoice_total_field": header_total_col}, "source_rows": citations}
    if re.search(r"\b(highest|largest|maximum|max)\b",q) and re.search(r"\b(total[_ ]amount|amount|sale value)\b",q) and not re.search(r"\baverage transaction value\b", q):
        vals=pd.to_numeric(df[value_col],errors="coerce"); chosen=df[vals.eq(vals.max())]
        return {"answer":f"Highest total amount: {vals.max():,.2f}. Sale(s): "+", ".join(chosen[id_col].astype(str).tolist())+".","values":{"highest_total_amount":float(vals.max()),"sale_ids":chosen[id_col].astype(str).tolist()},"source_rows":row_ids.loc[chosen.index].tolist()}
    if re.search(r"\b(highest|largest|maximum|max)\b",q) and re.search(r"\bdiscount\b",q):
        vals=pd.to_numeric(df[discount_col],errors="coerce"); chosen=df[vals.eq(vals.max())]
        return {"answer":f"Highest discount: {vals.max():,.2f}. Sale(s): "+", ".join(chosen[id_col].astype(str).tolist())+".","values":{"highest_discount":float(vals.max()),"sale_ids":chosen[id_col].astype(str).tolist()},"source_rows":row_ids.loc[chosen.index].tolist()}

    if re.search(r"\b(total quantity|quantity)\b",q) and re.search(r"\b(sales amount|total amount|total sales|sales value)\b",q) and re.search(r"\b(average|avg)\b.{0,40}\b(transaction|sale|purchase)\b",q):
        total=amounts.sum(); qty=quantities.sum(); avg=total/count if count else 0
        return {"answer":f"Quantity: {qty:,.0f}; total sales amount: {total:,.2f}; transactions: {count}; average transaction value: {avg:,.2f}.","values":{"quantity":float(qty),"total_sales":float(total),"transaction_count":count,"average_transaction_value":float(avg)},"source_rows":row_ids.loc[df.index].tolist()}

    if re.search(r"\bhow many sales\b", q) and re.search(r"\btotal quantity\b",q) and re.search(r"\b(average|avg)\b",q):
        total=amounts.sum(); qty=quantities.sum(); avg=total/count if count else 0
        return {"answer":f"Quantity: {qty:,.0f}; total sales amount: {total:,.2f}; transactions: {count}; average transaction value: {avg:,.2f}.","values":{"quantity":float(qty),"total_sales":float(total),"transaction_count":count,"average_transaction_value":float(avg)},"source_rows":row_ids.loc[df.index].tolist()}

    if re.search(r"\b(average|avg|mean)\b",q) and re.search(r"\b(spend|transaction value|per purchase|per sale|per transaction)\b",q) and not (re.search(r"\b(highest|most|greatest)\b", q) and "branch" in frame):
        total=amounts.sum(); avg=total/count if count else 0
        return {"answer":f"Total spend: {total:,.2f}; purchases: {count}; average spend per purchase: {avg:,.2f}.","values":{"total_spend":float(total),"purchase_count":count,"average_spend":float(avg)},"source_rows":row_ids.loc[df.index].tolist()}

    if "customer_id" in df and "product_id" in df and re.search(r"\bwhich products?\b", q) and re.search(r"\bhow many transactions?\b", q):
        products = df.product_id.dropna().astype(str).drop_duplicates().tolist()
        detail=[]
        for product in products[:30]:
            rows=df[df.product_id.astype(str).eq(product)]
            ids=rows[id_col].dropna().astype(str).tolist()
            detail.append(f"{product} ({len(ids)} transaction(s): {', '.join(ids)})")
        return {"answer": f"{count} transactions across {len(products)} distinct products: " + "; ".join(detail) + (" (product list capped at 30)." if len(products) > 30 else "."), "values": {"transaction_count": count, "products": products}, "source_rows": row_ids.loc[df.index].tolist()}

    if "customer_id" in df and "product_id" in df and re.search(r"\b(most frequently|most often|bought most)\b",q):
        counts=df.groupby("product_id",dropna=True)[id_col].nunique().sort_values(ascending=False)
        if len(counts):
            top=int(counts.iloc[0]); names=counts[counts.eq(top)].index.astype(str).tolist(); chosen=df[df.product_id.astype(str).isin(names)]
            next_count=int(counts[counts.lt(top)].iloc[0]) if (counts < top).any() else None
            next_names=counts[counts.eq(next_count)].index.astype(str).tolist() if next_count is not None else []
            runner_up=(f" Next highest: "+"; ".join(f"{name} ({next_count} transactions)" for name in next_names)+".") if next_names else ""
            return {"answer":"Most frequently purchased product(s): "+"; ".join(f"{name} ({top} transactions)" for name in names)+"."+runner_up,"values":{"products":names,"transactions":top,"next_highest_products":next_names,"next_highest_transactions":next_count},"source_rows":row_ids.loc[chosen.index].tolist()}

    if re.search(r"\b(percent(?:age)?|share|proportion)\b",q) and re.search(r"\b(total sales|all branches|all sales)\b",q) and "branch" in frame:
        branch=next((v for v in frame.branch.dropna().astype(str).unique() if v.casefold() in q),None)
        if branch:
            b=frame[frame.branch.astype(str).str.casefold().eq(branch.casefold())]
            part=pd.to_numeric(b[value_col],errors="coerce").sum(); total=pd.to_numeric(frame[value_col],errors="coerce").sum(); pct=100*part/total if total else 0
            return {"answer":f"{branch} generated {pct:.2f}% of total sales ({part:,.2f} of {total:,.2f}).","values":{"branch_sales":float(part),"total_sales":float(total),"share_pct":float(pct)},"source_rows":row_ids.loc[b.index].tolist()+row_ids.loc[frame.index].tolist()}

    if re.search(r"\b(highest|most|greatest)\b",q) and re.search(r"\baverage transaction value\b",q) and "branch" in frame:
        work=frame.copy(); work["_sale_value"]=pd.to_numeric(work[value_col],errors="coerce")
        grp=work.groupby("branch")._sale_value.agg(["sum","count"]); grp["avg"]=grp["sum"].div(grp["count"])
        winner=grp.avg.idxmax(); part=frame[frame.branch.astype(str).eq(str(winner))]
        return {"answer":f"{winner} has the highest average transaction value: {grp.loc[winner,'avg']:,.2f} per sale.","values":{"branch":winner,"average_transaction_value":float(grp.loc[winner,'avg'])},"source_rows":row_ids.loc[part.index].tolist()}

    if re.search(r"\bhow many|\bcount\b",q) and re.search(r"\bhow much|combined|total",q) and re.search(r"\b(sales?|transactions?)\b",q):
        total=amounts.sum();
        if "quantity" in q and qty_col: return {"answer":f"{count} sales; total quantity {quantities.sum():,.0f}; total amount {total:,.2f}.","values":{"sales_count":count,"total_quantity":float(quantities.sum()),"total_amount":float(total)},"source_rows":row_ids.loc[df.index].tolist()}
        return {"answer":f"{count} sales; combined total amount: {total:,.2f}.","values":{"sales_count":count,"total_amount":float(total)},"source_rows":row_ids.loc[df.index].tolist()}

    list_request=bool(re.search(r"\b(which|list|show|all sales|what sales)\b",q))
    if list_request:
        ids=df[id_col].dropna().astype(str).tolist()
        answer=f"{len(ids):,} matching sales: "+", ".join(ids[:30])+(" (list capped at 30)." if len(ids)>30 else ".")
        if "date" in df and len(re.findall(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", q)) >= 2:
            daily=pd.to_datetime(df.date,errors="coerce").dt.date.value_counts().sort_index()
            answer += " Daily counts: " + "; ".join(f"{day}: {count}" for day,count in daily.items()) + "."
            values={"matching_records":len(ids),"daily_counts":{str(day):int(count) for day,count in daily.items()}}
        else:
            values={"matching_records":len(ids)}
        return {"answer":answer,"values":values,"source_rows":row_ids.loc[df.index].tolist()}

    # Transaction totals and simple single-measure aggregates.
    invoice_total_col = next((c for c in ("invoice_total", "net_payable") if c in df and df[c].notna().any()), None)
    if (invoice_total_col and re.search(r"\b(invoices?|receipts?|bills?)\b", q)
            and re.search(r"\b(total|combined|sum|revenue|sales amount|amount)\b", q)):
        header_key = next((c for c in ("transaction_id", "invoice_id", "invoice_no", "receipt_id", "bill_no") if c in df and df[c].notna().any()), id_col)
        headers = df.loc[df[header_key].notna()].drop_duplicates(subset=[header_key]).copy()
        header_amounts = pd.to_numeric(headers[invoice_total_col], errors="coerce")
        valid_headers = headers.loc[header_amounts.notna()]
        if not valid_headers.empty:
            total = float(pd.to_numeric(valid_headers[invoice_total_col], errors="coerce").sum())
            if ("_header_source_file" in valid_headers and "_header_source_row" in valid_headers
                    and valid_headers["_header_source_file"].notna().any() and valid_headers["_header_source_row"].notna().any()):
                cited = list(dict.fromkeys(
                    (str(row["_header_source_file"]), row["_header_source_row"])
                    for _, row in valid_headers.iterrows()
                    if pd.notna(row["_header_source_file"]) and pd.notna(row["_header_source_row"])
                ))
            elif "source_file" in valid_headers and "source_row" in valid_headers:
                cited = [(str(row["source_file"]), row["source_row"]) for _, row in valid_headers.iterrows() if pd.notna(row["source_row"])]
            else:
                cited = row_ids.loc[valid_headers.index].tolist()
            return {"answer": f"Total sales amount: {total:,.2f}.", "values": {"total_sales": total, "invoice_count": int(len(valid_headers)), "sales_total_field": invoice_total_col}, "source_rows": cited}
    if re.search(r"\b(total|combined|sum|revenue|sales amount|spend)\b",q) and value_col:
        value=float(amounts.sum())
        return {"answer":f"Total sales amount: {value:,.2f}.","values":{"total_sales":value},"source_rows":row_ids.loc[df.index].tolist()}
    if qty_col and re.search(r"\b(total quantity|total units|units sold|quantity sold)\b",q):
        value=float(quantities.sum())
        return {"answer":f"Total quantity sold: {value:,.0f} units.","values":{"total_quantity_sold":value},"source_rows":row_ids.loc[df.index].tolist()}
    return None


def _answer_customer_question(question: str, frame: pd.DataFrame):
    """Answer a named customer's purchase-history request from matched rows only."""
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    if not re.search(r"\b(last time|last purchase|latest purchase|history|purchased?|bought|orders?|dispensed)\b", q):
        return None
    if not re.search(r"\b(customer|client|patient)\b", q):
        return None
    identity_cols = [c for c in ("customer_name", "customer_alias", "customer_id", "client_name", "client_id") if c in frame]
    if not identity_cols:
        return {"answer": "The selected records have no customer identity field, so I can't verify purchase history for a named customer.", "values": {"status": "unsupported_field", "required_field": "customer_identity"}, "source_rows": []}
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()
    normalized_query = normalize(q)
    candidates = sorted({str(value).strip() for col in identity_cols for value in frame[col].dropna().unique() if str(value).strip()}, key=len, reverse=True)
    hits = [value for value in candidates if len(normalize(value)) >= 3 and re.search(rf"(?<!\w){re.escape(normalize(value))}(?!\w)", normalized_query)]
    if not hits:
        named = re.search(r"\b(?:customer|client|patient)\s+([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+)+)", q)
        readable_labels = any(c in frame for c in ("customer_name", "customer_alias", "client_name")) or any(
            re.search(r"[a-z]{2,}\s+[a-z]{2,}", str(value), re.I)
            for col in ("customer_id", "client_id") if col in frame for value in frame[col].dropna().unique()
        )
        if not readable_labels and ("customer_id" in frame or "client_id" in frame):
            return {"answer": "This source stores customer IDs but no customer-name mapping. Provide the exact customer ID to look up purchase history.", "values": {"status": "missing_identity_mapping"}, "source_rows": []}
        if named:
            name_tokens = []
            for token in named.group(1).split():
                if token in {"purchase", "purchases", "purchased", "buy", "bought", "spend", "spent", "order", "orders", "history", "last", "time", "medicine", "medicines", "product", "products", "and", "what", "which"}:
                    break
                name_tokens.append(token)
            name = " ".join(name_tokens).title()
            return {"answer": f"No records were found for customer {name} in the selected data.", "values": {"status": "missing_record", "matched_records": 0}, "source_rows": []}
        return {"answer": "Which customer do you mean? Please provide the customer's name or ID.", "values": {"status": "needs_customer"}, "source_rows": []}
    if len(hits) > 1:
        return {"answer": "More than one customer matches that reference. Please provide the exact customer ID.", "values": {"status": "ambiguous_customer", "matches": hits[:10]}, "source_rows": []}
    matched = pd.Series(False, index=frame.index)
    for col in identity_cols:
        matched |= frame[col].fillna("").astype(str).str.casefold().eq(hits[0].casefold())
    rows = frame.loc[matched].copy()
    if "table_name" in rows and rows.table_name.astype(str).str.contains(r"sales|invoice|bill|dispens", case=False, regex=True).any():
        rows = rows.loc[rows.table_name.astype(str).str.contains(r"sales|invoice|bill|dispens", case=False, regex=True)].copy()
    if rows.empty:
        return {"answer": f"No sales or dispensing records were found for {hits[0]} in the selected data.", "values": {"status": "missing_record", "matched_records": 0}, "source_rows": []}
    row_ids = rows["source_row"] if "source_row" in rows else pd.Series(rows.index + 1, index=rows.index)
    date_col = next((c for c in ("date", "sale_date", "transaction_date") if c in rows), None)
    if date_col:
        dates = pd.to_datetime(rows[date_col], errors="coerce")
        if dates.notna().any():
            latest_date = dates.max()
            latest = rows.loc[dates.eq(latest_date)]
        else:
            latest = rows
            latest_date = None
    else:
        latest = rows
        latest_date = None
    if re.search(r"\b(last time|last purchase|latest purchase)\b", q):
        target = latest
    else:
        target = rows
    product_col = next((c for c in ("product_name", "medicine_name", "product_id", "product_code", "description") if c in target), None)
    products = target[product_col].dropna().astype(str).drop_duplicates().tolist() if product_col else []
    dates_text = f" on {latest_date.date()}" if latest_date is not None else " (no transaction date is recorded)"
    detail = ", ".join(products[:20]) if products else "the product names are not recorded"
    if len(products) > 20:
        detail += f" (showing 20 of {len(products)})"
    return {"answer": f"{hits[0]}'s most recent recorded purchase/dispensing{dates_text}: {detail}.", "values": {"status": "ok", "customer": hits[0], "products": products, "matched_records": len(target)}, "source_rows": row_ids.loc[target.index].tolist()}


def _answer_inventory_question(question: str, frame: pd.DataFrame):
    """Execute inventory questions against stock, expiry, and valuation fields.

    Inventory snapshots are not sales ledgers: quantity means stock on hand,
    and stock value is stock multiplied by unit cost. Use this planner only
    when the selected schema has inventory-specific fields.
    """
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    purchase_supplier_grouping = (
        re.search(r"\b(purchase|purchasing|purchased|buy|bought)\b", q)
        and re.search(r"\bsuppliers?\b", q)
        and re.search(r"\b(by|per|each|from each|for each)\b", q)
    )
    # A purchase summary mentioning suppliers is a purchase-ledger request,
    # not an inventory lookup for one supplier name.
    if purchase_supplier_grouping:
        return None
    inventory_scope = re.search(r"\b(stock|inventory|on[- ]hand|batch|batches|lot|lots|expir\w*|reorder|restock|rack|shelf|shelves|warehouse|location|stored|kept|located|therapeutic class|supplied by)\b", q)
    supplier_listing = re.search(r"\b(supplier|vendor|manufacturer)\b", q) and re.search(r"\b(which|list|show|find|items?|products?|medicines?)\b", q)
    if not inventory_scope and not supplier_listing:
        return None
    if re.search(r"\[(?:product|medicine|item) name\]", q):
        return {"answer": "Please provide the product or medicine name to look up its stock or batch details.", "values": {"status": "needs_product_name"}, "source_rows": []}
    df = frame.copy()
    product_id_aliases = {}
    master_id_col = next((c for c in ("_extra.ID", "product_master_id", "catalog_id") if c in frame), None)
    if master_id_col and "product_id" in frame:
        master = frame[[master_id_col, "product_id"]].dropna().copy()
        def normalize_product_identifier(value):
            normalized = str(value).strip()
            return re.sub(r"^(\d+)\.0+$", r"\1", normalized)
        master[master_id_col] = master[master_id_col].map(normalize_product_identifier)
        master["product_id"] = master["product_id"].astype(str).str.strip()
        for identifier, group in master.groupby(master_id_col, sort=False):
            names = {name.casefold() for name in group.product_id if name and not name.isdigit()}
            if len(names) == 1 and identifier:
                name = next(iter(names))
                product_id_aliases[identifier] = identifier
                product_id_aliases[name] = identifier
    scoped_to_inventory_database = False
    if "database_name" in df:
        inventory_database_rows = df.database_name.fillna("").astype(str).str.casefold().str.contains("inventory", regex=False)
        if inventory_database_rows.any():
            df = df.loc[inventory_database_rows].copy()
            scoped_to_inventory_database = True
    # Combined POS snapshots carry invoice and purchase columns for the whole
    # frame. Select inventory/batch rows first so sales-line quantities are
    # never mistaken for on-hand stock.
    if not scoped_to_inventory_database and "txn_type" in df and df["txn_type"].notna().any():
        txn_kind = df["txn_type"].fillna("").astype(str).str.casefold()
        recognized = txn_kind.str.contains(r"sale|expense|purchase|inventor|stock|batch", regex=True)
        if recognized.any():
            inventory_rows = txn_kind.str.contains(r"inventor|stock|batch", regex=True)
            df = df.loc[inventory_rows].copy()
    if not scoped_to_inventory_database and "table_name" in df:
        table = df["table_name"].fillna("").astype(str)
        inventory_mask = table.str.contains(r"inventory|stock|batch|product[_ ]?master|tbl[_ ]products", case=False, regex=True)
        if inventory_mask.any():
            df = df.loc[inventory_mask].copy()
            df = df.loc[~df["table_name"].astype(str).str.contains(r"archive|histor(?:y|ical)|old", case=False, regex=True)].copy()
    if (re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q)
            and re.search(r"\b(expir\w*|batch(?:es)?|lots?)\b", q)
            and "expiry_date" in df and "purchase_order_no" in df):
        purchase_ids = df.purchase_order_no.fillna("").astype(str).str.strip()
        df = df.loc[purchase_ids.eq("")].copy()
    stock_col = next((c for c in ("stock_qty", "closing_stock_qty", "available_qty", "quantity") if c in df and df[c].notna().any()), None)
    if not stock_col or not any(c in df and df[c].notna().any() for c in ("reorder_level", "expiry_date", "warehouse", "rack_location", "batch_no")):
        return None
    if "table_name" not in frame and any(c in frame for c in ("transaction_id", "invoice_id", "purchase_order_no")) and stock_col == "quantity" and not any(c in frame for c in ("stock_qty", "closing_stock_qty", "reorder_level", "expiry_date", "rack_location")):
        return None
    row_ids = df["source_row"] if "source_row" in df else pd.Series(df.index + 1, index=df.index)
    def source_keys(indexes):
        keys = []
        for index in indexes:
            source_file = df.loc[index].get("source_file")
            keys.append((str(source_file), row_ids.loc[index]) if source_file is not None and str(source_file).strip() else row_ids.loc[index])
        return keys
    def comparable(value: str) -> str:
        value = value.casefold().replace("’", "'").replace("‘", "'")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    inventory_base = df.copy()
    entity_masks = []
    product_entity_matched = False
    explicit_product_id = re.search(
        r"\b(?:product|medicine|item)\s+(?:id|code|number)\s*(\d+(?:\.0+)?)\b", q
    )
    explicit_product_id_trace = {}
    explicit_product_id_purchase_only_rows = pd.Index([])
    if explicit_product_id:
        requested_identifier = re.sub(r"\.0+$", "", explicit_product_id.group(1))
        id_mask = pd.Series(False, index=inventory_base.index)
        for id_col in ("product_id", "product_code", "product_master_id", "catalog_id"):
            if id_col in inventory_base:
                values = inventory_base[id_col].fillna("").astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
                field_match = values.eq(requested_identifier)
                explicit_product_id_trace[id_col] = {
                    "matching_rows": int(field_match.sum()),
                    "sample_values": values.drop_duplicates().head(8).tolist(),
                    "matching_examples": (
                        inventory_base.loc[field_match, [
                            col for col in ("product_id", "product_code", "table_name", "file_id", "source_file", "source_row", "quantity", "purchase_order_no")
                            if col in inventory_base
                        ]].head(5).to_dict("records")
                    ),
                }
                id_mask |= field_match
        mapped_product_names = {
            name.casefold() for name, identifier in product_id_aliases.items()
            if str(identifier).strip() == requested_identifier and not str(name).strip().isdigit()
        }
        if mapped_product_names and "product_id" in inventory_base:
            names = inventory_base.product_id.fillna("").astype(str).str.strip().str.casefold()
            mapped_name_match = names.isin(mapped_product_names)
            explicit_product_id_trace["mapped_product_names"] = sorted(mapped_product_names)
            explicit_product_id_trace["mapped_name_rows"] = int(mapped_name_match.sum())
            id_mask |= mapped_name_match
        all_identifier_matches = id_mask.copy()
        if stock_col in inventory_base:
            id_mask &= pd.to_numeric(inventory_base[stock_col], errors="coerce").notna()
        if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in inventory_base:
            purchase_ids = inventory_base.purchase_order_no.fillna("").astype(str).str.strip()
            explicit_product_id_purchase_only_rows = inventory_base.index[all_identifier_matches & purchase_ids.ne("")]
            id_mask &= purchase_ids.eq("")
        entity_masks.append(id_mask)
        product_entity_matched = bool(id_mask.any())
    for source_col, alternatives in (
        ("warehouse", ("warehouse", "branch", "location")),
        ("supplier", ("supplier_name", "supplier_id", "manufacturer")),
        ("category", ("category", "therapeutic_class", "drug_class")),
        ("product", ("product_name", "medicine_name", "product_id")),
    ):
        if source_col == "product" and explicit_product_id:
            continue
        if source_col == "product" and re.search(r"\b(?:product|medicine|item)\s+(?:id|code|number)\b", q):
            column = next((c for c in ("product_id", "product_code") if c in inventory_base), None)
        else:
            column = next((c for c in alternatives if c in inventory_base), None)
        if not column:
            continue
        # Resolve names against the full selected dataset. Resolving the next
        # field only inside an already narrowed subset silently drops a valid
        # second predicate when the combination has no matching row.
        vals = sorted((str(v) for v in inventory_base[column].dropna().unique() if str(v).strip()), key=len, reverse=True)
        normalized_q = comparable(q)
        explicit_numeric_product_id = (
            re.search(r"\b(?:product|medicine|item)\s+(?:id|code|number)\s*(\d+(?:\.0+)?)\b", q)
            if source_col == "product" and column in ("product_id", "product_code") else None
        )
        if explicit_numeric_product_id:
            requested_identifier = re.sub(r"\.0+$", "", explicit_numeric_product_id.group(1))
            hits = [
                value for value in vals
                if re.sub(r"\.0+$", "", str(value).strip()) == requested_identifier
            ]
        else:
            hits = [
                v for v in vals
                if re.search(rf"(?<!\w){re.escape(comparable(v))}(?!\w)", normalized_q)
                and not (
                    source_col == "product" and str(v).strip().isdigit()
                    and not re.search(rf"\b(?:product|medicine|item)\s+(?:id|code|number)?\s*{re.escape(str(v).strip())}\b", q)
                )
            ]
        if source_col == "product" and not hits:
            # Support a distinctive leading product phrase (brand + strength)
            # when the stored label includes extra formulation/generic text.
            for value in vals:
                tokens = comparable(value).split()
                if len(tokens) < 2:
                    continue
                prefix = " ".join(tokens[:2])
                if re.search(rf"(?<!\w){re.escape(prefix)}(?!\w)", normalized_q):
                    hits.append(value)
        if source_col == "product" and not hits and "generic_name" in inventory_base and column in inventory_base:
            generic_values = sorted((str(v) for v in inventory_base["generic_name"].dropna().unique() if str(v).strip()), key=len, reverse=True)
            generic_hits = [v for v in generic_values if re.search(rf"(?<!\w){re.escape(comparable(v))}(?!\w)", normalized_q)]
            if generic_hits:
                generic = generic_hits[0]
                candidates = inventory_base.loc[inventory_base["generic_name"].astype(str).str.casefold().eq(generic.casefold())]
                strengths = {re.sub(r"\s+", "", value) for value in re.findall(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", q)}
                if strengths:
                    candidates = candidates[candidates[column].astype(str).str.casefold().map(
                        lambda value: any(strength in re.sub(r"\s+", "", value) for strength in strengths)
                    )]
                product_values = candidates[column].dropna().astype(str).unique().tolist()
                if len(product_values) == 1:
                    hits = product_values
        if source_col == "product" and len(hits) == 1:
            product_entity_matched = True
        if len(hits) == 1 and not re.search(r"\b(compare|versus|\bvs\b|between .* and )\b", q):
            entity_masks.append(inventory_base[column].astype(str).str.casefold().eq(hits[0].casefold()))
    named_product_with_strength = re.search(r"\b([a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*){0,3})\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", q)
    if named_product_with_strength and not product_entity_matched:
        named_product = named_product_with_strength.group(1).strip()
        return {
            "answer": f"The selected inventory stores product IDs but has no verified name mapping for {named_product.title()}. Please provide the product ID or an exact batch number.",
            "values": {"status": "missing_product_mapping", "product_name": named_product, "required_field": "product_name-to-product_id mapping"},
            "source_rows": [],
        }
    if entity_masks:
        combined_mask = entity_masks[0]
        for entity_mask in entity_masks[1:]:
            combined_mask &= entity_mask
        df = inventory_base.loc[combined_mask].copy()

    if "expiry_date" in df and re.search(r"\b(expir\w*|before|after|between|from|through|inclusive|earliest|latest)\b", q):
        dates = pd.to_datetime(df.expiry_date, errors="coerce")
        iso_dates = [pd.Timestamp(*map(int, parts)) for parts in re.findall(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", q)]
        if len(iso_dates) >= 2:
            df = df[(dates.dt.normalize() >= min(iso_dates)) & (dates.dt.normalize() <= max(iso_dates))]
        elif iso_dates and re.search(r"\b(before|earlier than|prior to)\b", q):
            df = df[dates.dt.normalize() < iso_dates[0]]
        elif iso_dates and re.search(r"\b(after|later than)\b", q):
            df = df[dates.dt.normalize() > iso_dates[0]]
        elif iso_dates and re.search(r"\bas of\b", q):
            # The expired-stock predicate below applies this expiry cutoff.
            pass
        elif iso_dates and re.search(r"\b(by|on or before)\b", q):
            df = df[dates.dt.normalize() <= iso_dates[0]]
        elif iso_dates:
            df = df[dates.dt.date == iso_dates[0].date()]
        elif re.search(r"\b(earliest|latest|first|most recent)\b", q):
            pass
        elif re.search(r"\b(before|prior to|earlier than)\b", q):
            natural = re.search(r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b", q)
            if natural:
                months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
                target = pd.Timestamp(int(natural.group(3)), months[natural.group(2)], int(natural.group(1)))
                df = df[dates.dt.normalize() < target]
            else:
                year_only = re.search(r"\b(20\d{2})\b", q)
                if year_only:
                    target = pd.Timestamp(int(year_only.group(1)), 1, 1)
                    df = df[dates.dt.normalize() < target]
        elif re.search(r"\b20\d{2}\b", q):
            year = int(re.search(r"\b(20\d{2})\b", q).group(1))
            df = df[dates.dt.year == year]
        if re.search(r"\b(current|currently stocked|positive stock|on[- ]hand|in stock)\b", q):
            df = df.loc[pd.to_numeric(df[stock_col], errors="coerce").gt(0)]

    if any(c in frame for c in ("supplier_name", "supplier_id")) and re.search(r"\b(supplier|supplied by)\b", q):
        supplier_cols = [c for c in ("supplier_name", "supplier_id") if c in frame]
        normalized_q = comparable(q)
        has_supplier_hit = any(
            comparable(value) and re.search(rf"(?<!\w){re.escape(comparable(value))}(?!\w)", normalized_q)
            for col in supplier_cols for value in frame[col].dropna().astype(str).unique()
        )
        if not has_supplier_hit:
            return {"answer": "No matching inventory records were found for the named supplier in the selected data.", "values": {"matched_records": 0}, "source_rows": []}

    stock = pd.to_numeric(df[stock_col], errors="coerce")
    reorder = pd.to_numeric(df["reorder_level"], errors="coerce") if "reorder_level" in df else None
    cost_col = next((c for c in ("cost", "unit_cost", "unit_cost_price", "purchase_price") if c in df), None)
    cost = pd.to_numeric(df[cost_col], errors="coerce") if cost_col else None
    if re.search(r"\b(out of stock|zero stock|stock\s*=\s*0)\b", q):
        df = df.loc[stock.eq(0)]
    elif "expiry_date" in df and re.search(r"\b(already expired|have expired|has expired|expired but|past expiry)\b", q):
        as_of = re.search(r"\bas of\s+(20\d{2}-\d{1,2}-\d{1,2})\b", q)
        cutoff = pd.Timestamp(as_of.group(1)).normalize() if as_of else pd.Timestamp.now().normalize()
        expired = pd.to_datetime(df["expiry_date"], errors="coerce").dt.normalize() < cutoff
        positive = stock.gt(0) if re.search(r"\b(positive|still have|still has|remaining stock|stock left)\b", q) else pd.Series(True, index=df.index)
        df = df.loc[expired & positive]
    elif reorder is not None and (re.search(r"\b(below|under|less than)\b.{0,25}\breorder\b|\bbelow reorder\b|\bat\s+or\s+below\b.{0,25}\breorder\b|\bat/below\b.{0,25}\breorder\b", q) or re.search(r"\b(low[- ]stock|running low|need(?:s|ed)? to be reordered|reorder now|reordered now|understock(?:ed)?)\b", q)) and not re.search(r"\b(percent(?:age)?|share|proportion)\b", q) and not ("warehouse" in q and re.search(r"\b(every|each|per|by|which)\b", q)):
        at_or_below_reorder = bool(re.search(r"\b(at\s+or\s+below|at/below|equal\s+to\s+or\s+below)\b", q))
        df = df.loc[stock.le(reorder) if at_or_below_reorder else stock.lt(reorder)]
    elif reorder is not None and re.search(r"\b(?:exactly\s+)?equal\s+to\b.{0,30}\breorder|\bexactly\s+equal\s+to\s+reorder\b|\bexactly\s+at\s+(?:(?:the|their)\s+)?reorder(?:\s+(?:level|point))?\b|\bat\s+(?:the|their)\s+reorder\s+(?:level|point)\b|\bstock\s*=\s*reorder\b", q):
        df = df.loc[stock.eq(reorder)]
    elif re.search(r"\b(stock|quantity)\b.{0,24}\b(greater than|more than|over|above|>)\s*[\d,]+", q):
        m = re.search(r"\b(?:greater than|more than|over|above|>)\s*([\d,]+)", q)
        if m:
            df = df.loc[stock > float(m.group(1).replace(",", ""))]

    if (re.search(r"\b(expir\w*|batch(?:es)?|lots?)\b", q)
            and re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(units?|quantity|total)\b", q)
            and re.search(r"\b(already expired|have expired|has expired|expired but|past expiry)\b", q)):
        batch_col = next((col for col in ("batch_no", "batch_number", "lot_no", "lot_number") if col in df), None)
        unique_batches = int(df[batch_col].dropna().astype(str).nunique()) if batch_col else int(len(df))
        total_units = float(pd.to_numeric(df[stock_col], errors="coerce").dropna().sum())
        return {
            "answer": f"{unique_batches:,} current batches are past expiry with positive stock, totaling {total_units:,.0f} units.",
            "values": {"status": "ok", "expired_batches": unique_batches, "expired_stock_units": total_units, "as_of": cutoff.date().isoformat()},
            "source_rows": source_keys(df.index),
        }

    if df.empty:
        if explicit_product_id:
            if len(explicit_product_id_purchase_only_rows):
                purchase_source_rows = []
                for idx in explicit_product_id_purchase_only_rows:
                    row = inventory_base.loc[idx]
                    source_file = row.get("source_file") or row.get("file_id")
                    source_row = row.get("source_row")
                    if source_file is not None and source_row is not None:
                        purchase_source_rows.append((str(source_file), source_row))
                return {
                    "answer": f"Product ID {requested_identifier} matches purchase-linked rows, but I can't map those rows to current on-hand stock in the selected data. Please provide the product name or a batch number.",
                    "values": {"status": "missing_product_id_mapping", "requested_product_id": requested_identifier, "purchase_linked_matches": int(len(explicit_product_id_purchase_only_rows)), "identifier_match_trace": explicit_product_id_trace},
                    "source_rows": purchase_source_rows,
                }
            return {
                "answer": f"No current inventory rows could be matched to product ID {requested_identifier} in the selected scope.",
                "values": {"status": "no_matching_record", "requested_product_id": requested_identifier, "identifier_match_trace": explicit_product_id_trace},
                "source_rows": [],
            }
        return {"answer": "No matching inventory records were found in the selected data.", "values": {"matched_records": 0}, "source_rows": []}

    stock = pd.to_numeric(df[stock_col], errors="coerce")
    reorder = pd.to_numeric(df["reorder_level"], errors="coerce") if "reorder_level" in df else None
    cost = pd.to_numeric(df[cost_col], errors="coerce") if cost_col else None
    item_col = next((c for c in ("product_id", "product_name", "medicine_name", "product_code") if c in df), None)
    key_col = next((c for c in ("product_code", "product_id", "product_name") if c in df and df[c].notna().any()), None)
    location_col = next((col for col in ("rack_location", "rack", "warehouse", "location", "shelf") if col in df and df[col].notna().any()), None)
    if location_col and re.search(r"\b(racks?|warehouses?|locations?|shelves?)\b", q):
        # 1. Distinct location count query
        if (re.search(r"\b(?:how many|count|number of)\b", q)
                and re.search(r"\b(distinct|unique|different)\b", q)
                and not re.search(r"\b(products?|medicines?|items?)\b", q)
                and re.search(r"\b(positive stock|stock|inventory|in stock|on[- ]hand)\b", q)):
            work = df.copy()
            if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in work:
                purchase_ids = work.purchase_order_no.fillna("").astype(str).str.strip()
                work = work.loc[purchase_ids.eq("")].copy()
            work = work.loc[pd.to_numeric(work[stock_col], errors="coerce").gt(0) & work[location_col].notna()]
            locations = work.drop_duplicates(subset=[location_col])
            names = locations[location_col].astype(str).tolist()
            return {"answer": f"{len(names):,} distinct rack/location names have positive current stock.", "values": {"status": "ok", "positive_stock_locations": int(len(names)), "locations": names}, "source_rows": source_keys(locations.index)}

        # 2. Show/list distinct rack / location names or inventory by location
        is_location_list_request = bool(
            (
                re.search(r"\b(show|list|what are|which|give|tell me|display|names?)\b", q)
                and (
                    re.search(r"\bshow\s+(?:me\s+)?(?:the\s+)?names\b", q)
                    or re.search(r"\b(?:list|show|give|all)\s+(?:the\s+)?(?:distinct\s+|unique\s+)?(?:names\s+of\s+)?(racks?|warehouses?|locations?|shelves?)\b", q)
                    or re.search(r"\b(racks?|warehouses?|locations?|shelves?)\s+names?\b", q)
                    or re.search(r"\bwhich\s+(?:racks?|warehouses?|locations?|shelves?)\b", q)
                    or re.search(r"\bnames?\b", q)
                )
            )
            and not re.search(r"\b(?:how many|count of|number of)\b", q)
            and not re.search(r"\b(below|under|less than)\b.{0,25}\breorder\b", q)
            and not (
                re.search(r"\b(distinct|unique|different)\b", q)
                and re.search(r"\b(products?|medicines?|items?)\b", q)
                and re.search(r"\b(most|greatest|highest|largest|max(?:imum)?)\b", q)
            )
        )
        if is_location_list_request:
            work = df.copy()
            if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in work:
                purchase_ids = work.purchase_order_no.fillna("").astype(str).str.strip()
                work = work.loc[purchase_ids.eq("")].copy()
            if re.search(r"\b(positive|in stock|on[- ]hand|current stock)\b", q):
                work = work.loc[pd.to_numeric(work[stock_col], errors="coerce").gt(0)]
            work = work.loc[work[location_col].notna() & work[location_col].astype(str).str.strip().ne("")]

            loc_data = []
            for loc_name, grp in work.groupby(location_col, sort=True):
                loc_clean = str(loc_name).strip()
                if not loc_clean:
                    continue
                tot_stock = float(pd.to_numeric(grp[stock_col], errors="coerce").fillna(0).sum())
                dist_items = int(grp[item_col].nunique()) if item_col else int(len(grp))
                sample_items = [str(x) for x in grp[item_col].dropna().unique()[:2]] if item_col else []
                sample_text = ", ".join(sample_items) if sample_items else "—"
                loc_data.append({
                    "location": loc_clean,
                    "stock": tot_stock,
                    "distinct_items": dist_items,
                    "samples": sample_text
                })

            if loc_data:
                loc_data.sort(key=lambda x: x["location"])
                table_rows = []
                for idx, entry in enumerate(loc_data, 1):
                    table_rows.append(
                        f"| {idx} | {entry['location'].replace('|', '/')} | {entry['stock']:,.0f} | {entry['distinct_items']} | {entry['samples'].replace('|', '/')} |"
                    )

                table_md = "\n".join(table_rows)
                answer = (
                    "**Rack / Location Inventory**\n\n"
                    f"Found {len(loc_data):,} distinct rack/location names with positive current stock:\n\n"
                    "| # | Rack / Location | Current Stock Units | Distinct Products | Sample Medicines |\n"
                    "|---:|---|---:|---:|---|\n"
                    f"{table_md}"
                )
                return {
                    "answer": answer,
                    "values": {
                        "status": "ok",
                        "total_locations": len(loc_data),
                        "locations": [x["location"] for x in loc_data]
                    },
                    "source_rows": source_keys(work.index)
                }
    count_request = bool(re.search(r"\b(how many|count|number of)\b", q))
    aggregate_stock_request = bool(
        re.search(r"\b(total|combined|sum|average|avg|mean)\b", q)
        and re.search(r"\b(stock|units?|quantity)\b", q)
    )
    requested_rows = bool(re.search(
        r"\b(which|list|show|give|details?|records?|items?|products?)\b|\bwhat\s+(?:items?|products?|records?)\b",
        q,
    )) and not count_request and not aggregate_stock_request
    at_or_below_reorder = bool(re.search(r"\b(at\s+or\s+below|at/below|equal\s+to\s+or\s+below)\b.{0,25}\breorder\b", q))
    below = reorder is not None and bool(re.search(r"\b(below|under|less than)\b.{0,25}\breorder\b|\bbelow reorder\b|\bat\s+or\s+below\b.{0,25}\breorder\b|\bat/below\b.{0,25}\breorder\b|\b(low[- ]stock|running low|need(?:s|ed)? to be reordered|reorder now|reordered now|understock(?:ed)?)\b", q))

    location_stock_rank = bool(
        re.search(r"\b(highest|most|largest|maximum|max)\b", q)
        and re.search(r"\b(rack|warehouse|location|shelf|shelves|storage)\b", q)
        and re.search(r"\b(stock|units?|quantity|on[- ]hand)\b", q)
    )
    if (below and re.search(r"\b(rack|warehouse|location|shelf|shelves)\b", q)
            and re.search(r"\b(count|counts|number of|how many|most count|highest count|records?)\b", q)):
        location_col = next((col for col in ("rack_location", "rack", "warehouse", "location", "shelf") if col in df and df[col].notna().any()), None)
        if location_col:
            work = df.copy()
            if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in work:
                purchase_ids = work.purchase_order_no.fillna("").astype(str).str.strip()
                work = work.loc[purchase_ids.eq("")].copy()
            work["_stock_value"] = pd.to_numeric(work[stock_col], errors="coerce")
            work["_reorder_value"] = pd.to_numeric(work["reorder_level"], errors="coerce")
            threshold_match = work["_stock_value"].le(work["_reorder_value"]) if at_or_below_reorder else work["_stock_value"].lt(work["_reorder_value"])
            qualifying = work.loc[work["_stock_value"].notna() & work["_reorder_value"].notna() & threshold_match]
            counts = qualifying.groupby(location_col, dropna=True).size().sort_values(ascending=False, kind="stable")
            if not counts.empty:
                ranking_followup = bool(re.search(r"\b(next|second|runner[- ]?up|number two)\b", q))
                distinct_counts = sorted({int(value) for value in counts.tolist()}, reverse=True)
                if ranking_followup and len(distinct_counts) < 2:
                    return {"answer": "No lower distinct rack count is available in the selected below-reorder records.", "values": {"status": "no_second_rank", "counts_by_location": {str(name): int(count) for name, count in counts.items()}}, "source_rows": []}
                selected_count = distinct_counts[1] if ranking_followup else int(counts.iloc[0])
                leaders = [str(name) for name, count in counts.items() if int(count) == selected_count]
                selected_rows = qualifying.loc[qualifying[location_col].astype(str).isin(leaders)]
                parts = [f"{name}: {int(count)}" for name, count in counts.items()]
                source_rows = source_keys(selected_rows.index if ranking_followup else qualifying.index)
                answer_head = "Next distinct below-reorder count" if ranking_followup else "Current below-reorder record counts by rack/location"
                return {
                    "answer": answer_head + ": " + "; ".join(parts) + f". Selected count: {', '.join(leaders)} ({selected_count}).",
                    "values": {"status": "ok", "counts_by_location": {str(name): int(count) for name, count in counts.items()}, "selected_count": selected_count, "selected_locations": leaders, "rank": 2 if ranking_followup else 1},
                    "source_rows": source_rows,
                }
    if location_stock_rank:
        location_col = next((c for c in ("rack_location", "rack", "warehouse", "location", "shelf") if c in df and df[c].notna().any()), None)
        if location_col:
            work = df.assign(_stock=pd.to_numeric(df[stock_col], errors="coerce")).dropna(subset=[location_col, "_stock"])
            if re.search(r"\b(rack|shelf|shelves)\b", q):
                rack_rows = work[location_col].astype(str).str.contains(r"rack|shelf", case=False, regex=True)
                if rack_rows.any():
                    work = work.loc[rack_rows]
            grouped = work.groupby(location_col)._stock.sum().sort_values(ascending=False)
            if len(grouped):
                location = str(grouped.index[0]); quantity = float(grouped.iloc[0])
                contributing = work.loc[work[location_col].astype(str).eq(location)]
                return {
                    "answer": f"{location} holds the most recorded stock: {quantity:,.0f} units.",
                    "values": {"status": "ok", "location": location, "stock_units": quantity, "matching_records": int(len(contributing))},
                    "source_rows": source_keys(contributing.index),
                }
    if (re.search(r"\b(lowest|least|smallest)\b", q)
            and re.search(r"\b(stock|quantity|units?|on[- ]hand)\b", q)
            and re.search(r"\b(batch|lot)\b", q)
            and not re.search(r"\b(value|worth|cost|price|expiry|reorder)\b", q)):
        work = df.copy()
        if re.search(r"\b(positive|currently stocked|in stock|on[- ]hand)\b", q):
            work = work.loc[pd.to_numeric(work[stock_col], errors="coerce").gt(0)]
        if "purchase_order_no" in work and re.search(r"\b(current|currently|in stock|on[- ]hand)\b", q):
            purchase_ids = work.purchase_order_no.fillna("").astype(str).str.strip()
            work = work.loc[purchase_ids.eq("")]
        quantities = pd.to_numeric(work[stock_col], errors="coerce")
        work = work.loc[quantities.notna()].copy()
        if not work.empty:
            minimum = float(pd.to_numeric(work[stock_col], errors="coerce").min())
            chosen = work.loc[pd.to_numeric(work[stock_col], errors="coerce").eq(minimum)]
            batches = chosen["batch_no"].dropna().astype(str).unique().tolist() if "batch_no" in chosen else []
            products = chosen[item_col].dropna().astype(str).unique().tolist() if item_col else []
            labels = batches or products
            records_text = ", ".join(labels) if labels else "the matching inventory record"
            return {
                "answer": f"Lowest recorded positive stock: {minimum:g} units for batch {records_text}.",
                "values": {"status": "ok", "lowest_stock": minimum, "batches": batches, "products": products, "matching_records": int(len(chosen))},
                "source_rows": source_keys(chosen.index),
            }
    if (re.search(r"\b(highest|most|largest|maximum|max)\b", q)
            and re.search(r"\b(stock|quantity|units?|on[- ]hand)\b", q)
            and not re.search(r"\b(value|worth|cost|price)\b", q)):
        stock_values = pd.to_numeric(df[stock_col], errors="coerce")
        if stock_values.notna().any():
            maximum = float(stock_values.max())
            chosen = df.loc[stock_values.eq(maximum)]
            batch_names = chosen["batch_no"].dropna().astype(str).unique().tolist() if "batch_no" in chosen else []
            products = chosen[item_col].dropna().astype(str).unique().tolist() if item_col else []
            record_labels = batch_names if re.search(r"\b(batch|lot)\b", q) and batch_names else products or batch_names
            record_kind = "batch" if record_labels == batch_names and batch_names else "product"
            records_text = ", ".join(record_labels) if record_labels else "the matching inventory record"
            return {
                "answer": f"Highest recorded stock on hand: {maximum:g} units for {record_kind} {records_text}.",
                "values": {"status": "ok", "highest_stock": maximum, "batches": batch_names, "products": products},
                "source_rows": source_keys(chosen.index),
            }

    # Two named locations/suppliers with comparison language: compute every
    # requested measure over each side of the same filtered inventory table.
    if re.search(r"\bsupplier", q):
        compare_candidates = ("supplier_name", "supplier_id", "warehouse", "branch")
    elif re.search(r"\bwarehouse|location\b", q):
        compare_candidates = ("warehouse", "branch", "supplier_name", "supplier_id")
    elif "branch" in q:
        compare_candidates = ("branch", "warehouse", "supplier_name", "supplier_id")
    else:
        compare_candidates = ("warehouse", "supplier_name", "supplier_id", "branch")
    compare_field = None
    if re.search(r"\b(compare|versus|\bvs\b|between)\b", q):
        compare_field = next((
            c for c in compare_candidates if c in frame and
            sum(1 for value in frame[c].dropna().astype(str).unique() if value.casefold() in q) >= 2
        ), None)
    if compare_field and re.search(r"\b(compare|versus|\bvs\b|between)\b", q):
        entities = [str(v) for v in frame[compare_field].dropna().unique() if str(v).casefold() in q]
        if len(entities) >= 2:
            pieces, values, cited = [], {}, []
            for entity in entities[:2]:
                part = frame[frame[compare_field].astype(str).str.casefold().eq(entity.casefold())]
                qty = pd.to_numeric(part[stock_col], errors="coerce").sum()
                amount = None
                if cost_col:
                    amount = (pd.to_numeric(part[stock_col], errors="coerce") * pd.to_numeric(part[cost_col], errors="coerce")).sum()
                count = len(part)
                info = [f"{qty:,.0f} stock units"]
                if amount is not None and re.search(r"\b(value|worth|cost|inventory value)\b", q): info.append(f"inventory value {amount:,.2f}")
                if re.search(r"\b(records?|items?|number of)\b", q): info.append(f"{count:,} records")
                values[entity] = {"stock_units": float(qty), "inventory_value": float(amount) if amount is not None else None, "records": count}
                pieces.append(f"{entity}: " + ", ".join(info))
                cited.extend(source_keys(part.index))
            answer = "; ".join(pieces) + "."
            if len(values) == 2:
                first, second = list(values.values())
                if "stock_units" in first and re.search(r"\b(stock|units?)\b", q):
                    answer += f" Stock difference: {abs(first['stock_units'] - second['stock_units']):,.0f} units."
                if first.get("inventory_value") is not None and re.search(r"\b(value|worth|cost|inventory value)\b", q):
                    answer += f" Inventory value difference: {abs(first['inventory_value'] - second['inventory_value']):,.2f}."
            return {"answer": answer, "values": {"comparison": values}, "source_rows": cited}

    # Below-reorder breakdowns answer both count and the within-warehouse share.
    if below and "warehouse" in q and "warehouse" in df and re.search(r"\b(every|each|per|by|which)\b", q):
        work = df.copy()
        work["_below"] = pd.to_numeric(work[stock_col], errors="coerce") < pd.to_numeric(work["reorder_level"], errors="coerce")
        grouped = work.groupby("warehouse", dropna=True).agg(total=(stock_col, "size"), below=("_below", "sum"))
        grouped["share_pct"] = grouped.below.div(grouped.total.where(grouped.total.ne(0))).mul(100)
        parts = [f"{name}: {int(row.below)} below reorder of {int(row.total)} ({row.share_pct:.2f}%)" for name, row in grouped.iterrows()]
        highest_count = str(grouped.below.idxmax())
        highest_share = str(grouped.share_pct.idxmax())
        cited = source_keys(work.index[work._below])
        return {"answer": "; ".join(parts) + f". Most below reorder by count: {highest_count}; highest share: {highest_share}.", "values": {"by_warehouse": grouped.reset_index().to_dict("records"), "highest_count": highest_count, "highest_share": highest_share}, "source_rows": cited}

    if re.search(r"\b(earliest|latest|first|most recent)\b", q) and "expiry_date" in df:
        ranked = df
        if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in ranked:
            purchase_ids = ranked.purchase_order_no.fillna("").astype(str).str.strip()
            ranked = ranked.loc[purchase_ids.eq("")]
        if ranked.empty:
            return {"answer": "No current inventory batches with expiry dates were found in the selected data.", "values": {"status": "no_matching_record"}, "source_rows": []}
        dates = pd.to_datetime(ranked.expiry_date, errors="coerce")
        if not dates.notna().any():
            return {"answer":"Expiry dates are missing or invalid for the matching inventory records, so I can't rank their batches.","values":{"status":"unsupported_field","required_field":"expiry_date"},"source_rows":[]}
        which = []
        if re.search(r"\b(earliest|first)\b", q): which.append(("Earliest", dates.idxmin()))
        if re.search(r"\b(latest|most recent)\b", q): which.append(("Latest", dates.idxmax()))
        parts, cited, values = [], [], {}
        for label, idx in which:
            row = ranked.loc[idx]
            asks_batch_id = bool(re.search(r"\b(batch|lot)\b", q)) and "batch_no" in row and pd.notna(row.get("batch_no"))
            record = row.get("batch_no") if asks_batch_id else next((row.get(col) for col in ("product_code", "product_id", "product_name") if col in row and pd.notna(row.get(col)) and str(row.get(col)).strip()), row_ids.loc[idx])
            prod = row.get("product_id", row.get("product_name", ""))
            warehouse = row.get("warehouse", "")
            when = str(dates.loc[idx].date())
            prefix = "batch " if asks_batch_id else ""
            quantity = row.get(stock_col) if re.search(r"\b(quantity|units?)\b", q) else None
            quantity_text = f"; quantity {float(quantity):g}" if quantity is not None and pd.notna(quantity) else ""
            parts.append(f"{label}: {prefix}{record} ({prod}, {warehouse}) on {when}{quantity_text}")
            source_file = row.get("source_file")
            cited.append((str(source_file), row_ids.loc[idx]) if source_file is not None and str(source_file).strip() else row_ids.loc[idx])
            values[label.casefold()] = {"batch_no" if asks_batch_id else "record_id": str(record), "expiry_date": when}
            if quantity_text:
                values[label.casefold()]["quantity"] = float(quantity)
        if parts:
            return {"answer": "; ".join(parts) + ".", "values": values, "source_rows": cited}

    # Highest unit cost / highest single inventory value are record maxima.
    if cost_col and re.search(r"\b(highest|largest|maximum|most)\b.{0,30}\b(inventory value|stock value|value)\b", q):
        values = stock * cost
        chosen = df.loc[values.eq(values.max())]
        name = chosen[key_col].astype(str).tolist() if key_col else chosen.index.astype(str).tolist()
        return {"answer": f"Highest single inventory value: {values.max():,.2f}. Record(s): " + ", ".join(name[:30]) + ".", "values": {"highest_inventory_value": float(values.max()), "records": name}, "source_rows": source_keys(chosen.index)}

    if cost_col and re.search(r"\b(highest|maximum|max)\b.{0,30}\b(unit[_ ]cost|cost)\b", q):
        vals = pd.to_numeric(df[cost_col], errors="coerce")
        chosen = df.loc[vals.eq(vals.max())]
        asks_batch_id = bool(re.search(r"\b(batch|batches|lot|lots)\b", q) and "batch_no" in chosen and chosen.batch_no.notna().any())
        name = (chosen["batch_no"].dropna().astype(str).unique().tolist() if asks_batch_id
                else chosen[key_col].astype(str).tolist() if key_col else chosen.index.astype(str).tolist())
        products = chosen[item_col].dropna().astype(str).unique().tolist() if item_col else []
        detail = f" Products: {', '.join(products[:10])}." if products else ""
        record_label = "Batches" if asks_batch_id else "Records"
        return {"answer": f"Highest unit cost: {vals.max():,.2f}. {record_label}: " + ", ".join(name[:30]) + "." + detail, "values": {"highest_unit_cost": float(vals.max()), "records": name, "products": products}, "source_rows": source_keys(chosen.index)}

    if below and re.search(r"\b(percent(?:age)?|share|proportion)\b", q):
        below_mask = stock < reorder
        count = int(below_mask.sum()); pct = 100 * count / len(df)
        return {"answer": f"{count:,} of {len(df):,} inventory records are below reorder level ({pct:.2f}%).", "values": {"below_reorder": count, "total_records": len(df), "percentage": pct}, "source_rows": source_keys(df.index[below_mask])}

    retail_col = next((c for c in ("unit_price", "sale_price", "retail_price", "mrp") if c in df and df[c].notna().any()), None)
    if re.search(r"\b(retail value|retail inventory|retail worth)\b", q) and retail_col:
        retail_prices = pd.to_numeric(df[retail_col], errors="coerce")
        covered = stock.notna() & retail_prices.notna()
        missing = int(stock.notna().sum() - covered.sum())
        value = (stock[covered] * retail_prices[covered]).sum()
        if missing:
            answer = f"Recorded retail-value subtotal: {value:,.2f} across {int(covered.sum())} inventory records; {missing} records lack a usable stock quantity or retail price, so the full-scope total is unavailable."
            values = {"status": "partial_data", "retail_inventory_value_subtotal": float(value), "priced_records": int(covered.sum()), "missing_value_records": missing}
        else:
            answer = f"Total current inventory retail value at recorded prices: {value:,.2f}."
            values = {"status": "ok", "retail_inventory_value": float(value)}
        return {"answer": answer, "values": values, "source_rows": source_keys(df.index[covered])}
    if cost_col and re.search(r"\b(category|categories)\b", q) and re.search(r"\b(capital|money|inventory value|stock value|value|worth|investment)\b", q) and "category" in df:
        covered = stock.notna() & cost.notna() & df["category"].notna()
        missing = int((stock.notna() & df["category"].notna()).sum() - covered.sum())
        if missing:
            subtotal = float((stock[covered] * cost[covered]).sum())
            return {"answer": f"Recorded unit-cost subtotal: {subtotal:,.2f} across {int(covered.sum())} inventory records; {missing} category records lack unit cost, so a full-category valuation is unavailable.", "values": {"status": "partial_data", "inventory_value_subtotal": subtotal, "priced_records": int(covered.sum()), "missing_value_records": missing}, "source_rows": source_keys(df.index[covered])}
        work = df.assign(_inventory_value=stock * cost).dropna(subset=["category", "_inventory_value"])
        grouped = work.groupby("category")._inventory_value.sum().sort_values(ascending=False).head(10)
        details = [{"category": str(k), "inventory_value": float(v)} for k, v in grouped.items()]
        return {"answer": "Inventory value by category: " + "; ".join(f"{x['category']} ({x['inventory_value']:,.2f})" for x in details) + ".", "values": {"status": "ok", "categories": details}, "source_rows": source_keys(work.index)}
    if re.search(r"\b(inventory value|stock value|purchase value|purchase cost value|worth|stock\s*[×x*]\s*(?:unit )?cost|stock multiplied by)\b", q) and cost_col:
        covered = stock.notna() & cost.notna()
        missing = int(stock.notna().sum() - covered.sum())
        value = float((stock[covered] * cost[covered]).sum())
        if missing:
            answer = f"Recorded unit-cost subtotal: {value:,.2f} across {int(covered.sum())} inventory records; {missing} records lack unit cost, so the full-scope inventory value is unavailable."
            values = {"status": "partial_data", "inventory_value_subtotal": value, "priced_records": int(covered.sum()), "missing_value_records": missing}
        else:
            answer = f"Total current inventory value at recorded unit cost: {value:,.2f}."
            values = {"status": "ok", "inventory_value": value, "records": len(df)}
        return {"answer": answer, "values": values, "source_rows": source_keys(df.index[covered])}

    if (re.search(r"\b(distinct|unique|different)\b", q)
            and item_col
            and re.search(r"\b(products?|medicines?|items?)\b", q)
            and re.search(r"\b(by|per|each|which|what)\b.{0,35}\b(rack|warehouse|location|shelf|shelves)\b", q)
            and re.search(r"\b(most|greatest|highest|largest|max(?:imum)?)\b", q)):
        location_col = next((col for col in ("rack_location", "rack", "warehouse", "location", "shelf") if col in df and df[col].notna().any()), None)
        if location_col:
            grouped_frame = df.loc[stock.notna()].copy()
            if re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q) and "purchase_order_no" in grouped_frame:
                purchase_ids = grouped_frame.purchase_order_no.fillna("").astype(str).str.strip()
                grouped_frame = grouped_frame.loc[purchase_ids.eq("")].copy()
            grouped_frame["_distinct_item"] = grouped_frame[item_col].astype(str).str.strip()
            grouped_frame = grouped_frame.loc[grouped_frame["_distinct_item"].ne("")]
            counts = grouped_frame.groupby(location_col, dropna=True)["_distinct_item"].nunique().sort_values(ascending=False, kind="stable")
            if not counts.empty:
                highest = int(counts.iloc[0])
                leaders = [str(location) for location, count in counts.items() if int(count) == highest]
                winner_rows = grouped_frame.loc[grouped_frame[location_col].astype(str).isin(leaders)].drop_duplicates([location_col, "_distinct_item"])
                details = "; ".join(f"{location}: {int(count)}" for location, count in counts.items())
                return {
                    "answer": f"Most distinct products by rack/location: {details}. Highest: {', '.join(leaders)} ({highest}).",
                    "values": {"status": "ok", "distinct_products_by_location": {str(location): int(count) for location, count in counts.items()}, "highest_count": highest, "highest_locations": leaders, "count_field": item_col},
                    "source_rows": source_keys(winner_rows.index),
                }

    if (re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(distinct|different|unique)\b", q)
            and re.search(r"\b(product ids?|products?|medicines?|items?)\b", q)
            and re.search(r"\b(below|under|less than)\b.{0,35}\b(reorder|restock)\b|\b(reorder|restock)\b.{0,35}\b(below|under|less than)\b", q)):
        product_col = next((col for col in ("product_id", "product_code", "product_name") if col in df), None)
        reorder_col = next((col for col in ("reorder_level", "reorder_point", "reorder_qty", "minimum_stock") if col in df), None)
        if product_col and reorder_col:
            count_frame = df.loc[stock.notna()].copy()
            if "purchase_order_no" in count_frame:
                purchase_ids = count_frame.purchase_order_no.fillna("").astype(str).str.strip()
                count_frame = count_frame.loc[purchase_ids.eq("")].copy()
            stock_values = pd.to_numeric(count_frame[stock_col], errors="coerce")
            reorder_values = pd.to_numeric(count_frame[reorder_col], errors="coerce")
            below_rows = count_frame.loc[
                stock_values.notna() & reorder_values.notna() & stock_values.lt(reorder_values)
            ]
            products = below_rows[product_col].dropna().astype(str).str.strip()
            products = products[products.ne("")]
            representative_rows = below_rows.loc[products.index].groupby(product_col, sort=False).head(1)
            product_count = int(products.nunique())
            return {
                "answer": f"{product_count:,} distinct products have at least one current stock row below its recorded reorder level.",
                "values": {"status": "ok", "distinct_products_below_reorder": product_count, "qualifying_stock_rows": int(len(below_rows)), "reorder_field": reorder_col},
                "source_rows": source_keys(representative_rows.index),
            }

    if (re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(rows?|records?)\b", q)
            and re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q)
            and re.search(r"\b(stock|inventory|product)\b", q)):
        count_frame = df.loc[stock.notna()].copy()
        if "purchase_order_no" in count_frame:
            purchase_ids = count_frame.purchase_order_no.fillna("").astype(str).str.strip()
            count_frame = count_frame.loc[purchase_ids.eq("")].copy()
        product_col = next((col for col in ("product_id", "product_code", "product_name") if col in count_frame), None)
        if product_col:
            count_frame = count_frame.loc[count_frame[product_col].notna() & count_frame[product_col].astype(str).str.strip().ne("")]
        below_reorder = bool(re.search(r"\b(below|under|less than)\b.{0,35}\b(reorder|restock)\b|\b(reorder|restock)\b.{0,35}\b(below|under|less than)\b", q))
        reorder_col = next((col for col in ("reorder_level", "reorder_point", "reorder_qty", "minimum_stock") if col in count_frame), None)
        if below_reorder and reorder_col:
            stock_values = pd.to_numeric(count_frame[stock_col], errors="coerce")
            reorder_values = pd.to_numeric(count_frame[reorder_col], errors="coerce")
            count_frame = count_frame.loc[stock_values.notna() & reorder_values.notna() & stock_values.lt(reorder_values)]
        elif below_reorder:
            return {"answer": "The selected current stock rows have no recorded reorder threshold, so I can't count rows below it.", "values": {"status": "unsupported_field", "required_field": "reorder level"}, "source_rows": []}
        if below_reorder:
            answer = f"{len(count_frame):,} current stock rows are below their recorded reorder level."
        else:
            answer = f"{len(count_frame):,} current stock product rows are recorded."
        return {
            "answer": answer,
            "values": {"status": "ok", "current_stock_rows": int(len(count_frame)), "stock_field": stock_col, **({"below_reorder_level": int(len(count_frame)), "reorder_field": reorder_col} if below_reorder else {})},
            "source_rows": source_keys(count_frame.index),
        }

    if (re.search(r"\b(total|combined|sum(?:med)?|aggregate|average|avg|mean)\b", q)
            or (re.search(r"\bhow many\b", q) and re.search(r"\b(units?|quantity|stock)\b", q))) \
            and re.search(r"\b(stock|units?|quantity)\b", q):
        aggregate_df = df.loc[stock.notna()].copy()
        if (re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q)
                and "purchase_order_no" in aggregate_df):
            purchase_ids = aggregate_df.purchase_order_no.fillna("").astype(str).str.strip()
            aggregate_df = aggregate_df.loc[purchase_ids.eq("")].copy()
        aggregate_stock = pd.to_numeric(aggregate_df[stock_col], errors="coerce").dropna()
        if re.search(r"\b(average|avg|mean)\b", q):
            value = aggregate_stock.mean(); metric = "average_stock"
            answer = f"Average stock: {value:,.2f} units. Across {len(aggregate_stock):,} records."
        else:
            value = aggregate_stock.sum(); metric = "total_stock"
            answer = f"Total stock: {value:,.0f} units. Across {len(aggregate_stock):,} records."
        source = source_keys(aggregate_df.index)
        if requested_rows:
            labels = [str(df.loc[idx, key_col]) if key_col else str(row_ids.loc[idx]) for idx in aggregate_df.index]
            answer += " Records: " + ", ".join(labels[:30]) + (" (list capped at 30)." if len(labels) > 30 else ".")
        result = {metric: float(value), "matching_records": len(aggregate_stock)}
        if metric == "total_stock":
            result["total_quantity"] = float(value)  # compatible with existing inventory clients
        if metric == "average_stock" and re.search(r"\b(lowest|least|smallest)\b", q):
            idx = aggregate_stock.idxmin()
            record = next((df.loc[idx].get(col) for col in ("product_code", "product_id", "product_name") if col in df and pd.notna(df.loc[idx].get(col)) and str(df.loc[idx].get(col)).strip()), row_ids.loc[idx])
            lowest = float(aggregate_stock.loc[idx])
            answer += f" Lowest-stock record: {record} ({lowest:g} units)."
            result["lowest_stock_record"] = {"record_id": record, "stock": lowest}
        if cost_col and re.search(r"\b(and|plus|also)\b.{0,40}\b(value|worth|cost)\b", q):
            inventory_value = (aggregate_stock * pd.to_numeric(aggregate_df.loc[aggregate_stock.index, cost_col], errors="coerce")).sum()
            answer += f" Inventory value: {inventory_value:,.2f}."
            result["inventory_value"] = float(inventory_value)
        return {"answer": answer, "values": result, "source_rows": source}

    if (re.search(r"\b(how many|count|number of)\b", q)
            and re.search(r"\b(distinct|unique|different)\b", q)
            and item_col
            and re.search(r"\b(products?|medicines?|items?)\b", q)
            and not requested_rows):
        count_col = "product_id" if re.search(r"\bproduct ids?\b", q) and "product_id" in df else item_col
        count_frame = df.loc[stock.notna()].copy()
        if (re.search(r"\b(current|currently|on[- ]hand|in stock)\b", q)
                and "purchase_order_no" in count_frame):
            purchase_ids = count_frame.purchase_order_no.fillna("").astype(str).str.strip()
            count_frame = count_frame.loc[purchase_ids.eq("")].copy()
        if count_frame.empty:
            count_frame = df.copy()
        distinct = count_frame[count_col].dropna().astype(str).str.strip()
        distinct = distinct[distinct.ne("")]
        if count_col == "product_id" and product_id_aliases:
            canonical = distinct.map(lambda value: product_id_aliases.get(normalize_product_identifier(value), product_id_aliases.get(value.casefold(), f"value:{value.casefold()}")))
            count = int(canonical.nunique())
            representative_indices = count_frame.loc[distinct.index].assign(_canonical_product=canonical).groupby("_canonical_product", sort=False).head(1).index
        else:
            count = int(distinct.nunique())
            representative_indices = count_frame.loc[distinct.index].groupby(count_col, sort=False).head(1).index
        table_counts = {}
        distinct_counts_by_table = {}
        id_samples_by_table = {}
        table_col = next((col for col in ("table_name", "source_file", "file_id") if col in df), None)
        if table_col:
            table_counts = {str(key): int(value) for key, value in count_frame[table_col].fillna("unknown").astype(str).value_counts().items()}
            for key, group in count_frame.groupby(table_col, dropna=False, sort=False):
                values = group[count_col].dropna().astype(str).str.strip()
                values = values[values.ne("")]
                label = str(key) if pd.notna(key) else "unknown"
                distinct_counts_by_table[label] = int(values.nunique())
                id_samples_by_table[label] = values.drop_duplicates().head(12).tolist()
        return {
            "answer": f"{count:,} distinct products appear in the matching inventory records.",
            "values": {
                "status": "ok", "distinct_products": count, "count_field": count_col,
                "input_records": int(len(count_frame)), "source_table_record_counts": table_counts,
                "distinct_products_by_source_table": distinct_counts_by_table,
                "product_id_samples_by_source_table": id_samples_by_table,
            },
            "source_rows": source_keys(representative_indices),
        }

    if re.search(r"\b(how many|count|number of|records?)\b", q) and not requested_rows:
        if item_col and re.search(r"\b(products?|medicines?|items?)\b", q) and not re.search(r"\b(batch|record|row)s?\b", q):
            count = int(df.loc[stock.gt(0), item_col].dropna().astype(str).nunique()) if re.search(r"\bcurrently in stock|in stock\b", q) else int(df[item_col].dropna().astype(str).nunique())
            return {"answer": f"{count:,} distinct products match the inventory criteria.", "values": {"status": "ok", "distinct_products": count}, "source_rows": source_keys(df.index)}
        return {"answer": f"{len(df):,} matching inventory records.", "values": {"status": "ok", "matching_records": len(df)}, "source_rows": source_keys(df.index)}

    if requested_rows:
        if reorder is not None and re.search(r"\b(how much|quantity|units?|amount)\b.{0,35}\b(reorder|restock|buy|order)\b|\b(reorder|restock)\b.{0,35}\b(quantity|units?|amount)\b", q):
            deficit = (pd.to_numeric(df["reorder_level"], errors="coerce") - stock).clip(lower=0)
            items = [{"product": str(df.loc[idx, item_col]) if item_col else str(row_ids.loc[idx]), "stock": float(stock.loc[idx]), "reorder_level": float(df.loc[idx, "reorder_level"]), "suggested_order_qty": float(deficit.loc[idx])} for idx in df.index if pd.notna(deficit.loc[idx]) and deficit.loc[idx] > 0]
            if items:
                table_lines = [
                    "| # | Medicine / Product | Current Stock | Reorder Level | Suggested Order Qty |",
                    "|---:|---|---:|---:|---:|"
                ]
                for i, x in enumerate(items[:30], 1):
                    p_name = x["product"].replace("|", "/")
                    table_lines.append(f"| {i} | {p_name} | {x['stock']:g} | {x['reorder_level']:g} | {x['suggested_order_qty']:g} |")
                answer = (
                    "**Suggested Reorders**\n\n"
                    f"Showing {min(len(items), 30):,} of {len(items):,} medicines requiring reorders to reach target levels:\n\n"
                    + "\n".join(table_lines)
                    + ("\n\n*(List capped at 30 items)*" if len(items) > 30 else "")
                )
            else:
                answer = "No deficit against recorded reorder levels."
            return {"answer": answer, "values": {"status": "ok", "suggested_reorders": items}, "source_rows": source_keys(df.index)}

        # Build clean tabular inventory records
        has_rack = any(col in df and df[col].notna().any() for col in ("rack_location", "rack", "warehouse"))
        has_batch = "batch_no" in df and df["batch_no"].notna().any() and bool(re.search(r"\b(batch|lot)\b", q))
        has_supplier = "supplier_name" in df and df["supplier_name"].notna().any()
        has_expiry = "expiry_date" in df and df["expiry_date"].notna().any()

        cols = ["#", "Medicine / Product"]
        aligns = ["---:", "---"]
        if has_rack:
            cols.append("Rack / Location")
            aligns.append("---")
        if has_batch:
            cols.append("Batch")
            aligns.append("---")
        if has_supplier:
            cols.append("Supplier")
            aligns.append("---")
        if has_expiry:
            cols.append("Expiry Date")
            aligns.append("---")
        cols.append("Current Stock")
        aligns.append("---:")

        table_lines = [
            "| " + " | ".join(cols) + " |",
            "|" + "|".join(aligns) + "|"
        ]

        display_df = df.head(30)
        for idx, (_, row) in enumerate(display_df.iterrows(), 1):
            name_val = row.get("product_name")
            id_val = row.get("product_id")
            code_val = row.get("product_code")

            if pd.notna(name_val) and str(name_val).strip():
                item_label = str(name_val).strip()
                if pd.notna(code_val) and str(code_val).strip() and str(code_val).strip() != item_label:
                    item_label = f"{item_label} ({str(code_val).strip()})"
                elif pd.notna(id_val) and str(id_val).strip() and str(id_val).strip() != item_label:
                    item_label = f"{item_label} ({str(id_val).strip()})"
            elif pd.notna(id_val) and str(id_val).strip():
                item_label = str(id_val).strip()
            elif pd.notna(code_val) and str(code_val).strip():
                item_label = str(code_val).strip()
            else:
                item_label = "Item"
            item_label = item_label.replace("|", "/")

            row_cells = [str(idx), item_label]

            if has_rack:
                rack = str(row.get("rack_location") or row.get("rack") or row.get("warehouse") or "—").strip().replace("|", "/")
                row_cells.append(rack)
            if has_batch:
                batch = str(row.get("batch_no") or "—").strip().replace("|", "/")
                row_cells.append(batch)
            if has_supplier:
                supp = str(row.get("supplier_name") or "—").strip().replace("|", "/")
                row_cells.append(supp)
            if has_expiry:
                exp = pd.to_datetime(row.get("expiry_date"), errors="coerce")
                row_cells.append(str(exp.date()) if pd.notna(exp) else "—")

            stock_val = f"{float(row[stock_col]):g}" if stock_col in row and pd.notna(row.get(stock_col)) else "—"
            row_cells.append(stock_val)

            table_lines.append("| " + " | ".join(row_cells) + " |")

        count_header = f"Found {len(df):,} matching inventory records (showing {min(len(df), 30)}):"
        table_md = "\n".join(table_lines)
        capped_note = "\n\n*(List capped at 30 records)*" if len(df) > 30 else ""
        answer = f"**Matching Inventory Records**\n\n{count_header}\n\n{table_md}{capped_note}"
        return {"answer": answer, "values": {"matching_records": len(df)}, "source_rows": source_keys(df.index)}

    return None


def _answer_purchase_question(question: str, frame: pd.DataFrame):
    """Plan deterministic summaries over purchase ledgers without sales labels."""
    q = re.sub(r"\s+", " ", str(question).casefold().replace("_", " ")).strip()
    supplier_group_request = bool(
        re.search(r"\bpurchase|purchasing|purchased|buy|bought\b", q)
        and re.search(r"\bsuppliers?\b", q)
        and re.search(r"\b(by|per|each|from each|for each)\b", q)
    )
    if "purchase_order_no" not in frame.columns and not (supplier_group_request and "supplier_id" in frame and "invoice_total" in frame):
        return None
    purchase_intent = re.search(r"\b(purchase|purchasing|purchased|buy|bought|vendor payable|supplier payable|owe|owed|payable)\b", q)
    unit_cost_intent = re.search(r"\b(average|avg|mean)?\s*(unit )?cost\b", q) and re.search(r"\b(compare|versus|\bvs\b|between|supplier|vendor|paid|purchase order)\b", q)
    if not purchase_intent and not unit_cost_intent:
        return None
    if re.search(r"\b(batch|batches|lot|lots|expir\w*|rack|shelf|shelves|on[- ]hand|stock level)\b", q):
        return None
    df = frame.copy()
    if "txn_type" in df and df["txn_type"].notna().any():
        purchase_rows = df["txn_type"].fillna("").astype(str).str.casefold().str.contains(r"purchase|expense", regex=True)
        if purchase_rows.any():
            df = df.loc[purchase_rows].copy()
    ids = df["purchase_order_no"].fillna("").astype(str) if "purchase_order_no" in df else pd.Series("", index=df.index, dtype=str)
    row_ids = df["source_row"] if "source_row" in df else pd.Series(df.index + 1, index=df.index)
    quantity_col = next((c for c in ("quantity", "qty_ordered", "received_qty", "total_qty") if c in df), None)
    amount_col = next((c for c in ("invoice_total", "net_payable", "amount", "sales_subtotal") if c in df), None)
    cost_col = next((c for c in ("cost", "unit_cost", "unit_cost_price") if c in df), None)
    if not amount_col:
        return {"answer": "The selected purchase data is missing an invoice value field, so I can't calculate this reliably.", "values": {"status": "missing_required_field"}, "source_rows": []}

    # Supplier totals belong to purchase-header grain. In joined snapshots an
    # invoice_total can repeat once per purchase line, so collapse duplicate
    # header provenance before summing it. Raw purchase headers may not carry
    # line quantities; that does not prevent an invoice-value breakdown.
    if (re.search(r"\bsuppliers?\b", q)
            and re.search(r"\b(by|per|each|from each|for each)\b", q)
            and re.search(r"\b(total|value|amount|spend|purchase)\b", q)):
        supplier_col = next((c for c in ("supplier_name", "supplier_id") if c in df and df[c].notna().any()), None)
        if supplier_col:
            grouped_rows = df.loc[df[supplier_col].notna() & pd.to_numeric(df[amount_col], errors="coerce").notna()].copy()
            header_file = "_header_source_file" if "_header_source_file" in grouped_rows else None
            header_row = "_header_source_row" if "_header_source_row" in grouped_rows else None
            if header_file and header_row and grouped_rows[header_row].notna().any():
                grouped_rows = grouped_rows.drop_duplicates([header_file, header_row], keep="first")
            elif "source_file" in grouped_rows and "source_row" in grouped_rows:
                grouped_rows = grouped_rows.drop_duplicates(["source_file", "source_row"], keep="first")
            elif "_extra.ID" in grouped_rows:
                grouped_rows = grouped_rows.drop_duplicates([supplier_col, "_extra.ID"], keep="first")
            grouped_rows["_purchase_header_amount"] = pd.to_numeric(grouped_rows[amount_col], errors="coerce")
            grouped = grouped_rows.groupby(supplier_col, dropna=True)["_purchase_header_amount"].agg(["count", "sum"]).sort_values("sum", ascending=False)
            if not grouped.empty:
                parts = [f"{supplier}: {int(row['count']):,} purchase headers, {row['sum']:,.2f}" for supplier, row in grouped.iterrows()]
                source_rows = []
                for idx, row in grouped_rows.iterrows():
                    if header_file and header_row and pd.notna(row.get(header_file)) and pd.notna(row.get(header_row)):
                        source_rows.append((str(row[header_file]), row[header_row]))
                    elif "source_file" in grouped_rows and pd.notna(row.get("source_file")) and "source_row" in grouped_rows and pd.notna(row.get("source_row")):
                        source_rows.append((str(row["source_file"]), row["source_row"]))
                    else:
                        source_rows.append(row_ids.loc[idx])
                values = {str(supplier): {"purchase_headers": int(row["count"]), "purchase_value": float(row["sum"])} for supplier, row in grouped.iterrows()}
                return {"answer": "Purchase totals by supplier ID: " + "; ".join(parts) + f". Overall: {grouped['sum'].sum():,.2f}.", "values": {"by_supplier": values, "total_purchase_value": float(grouped["sum"].sum()), "purchase_header_count": int(grouped["count"].sum())}, "source_rows": source_rows}

    if not quantity_col:
        return {"answer": "The selected purchase data is missing a quantity field, so I can't calculate this reliably.", "values": {"status": "missing_required_field"}, "source_rows": []}

    # Select a single named entity, leaving two explicit comparison entities
    # intact for the comparison branch below.
    compare = bool(re.search(r"\b(compare|versus|\bvs\b|between)\b", q))
    comparison_label = "supplier" if re.search(r"\bsuppliers?\b", q) else "branch" if re.search(r"\bbranches\b", q) else None
    entity_specs = (
        ("supplier", ("supplier_name", "supplier_id")),
        ("product", ("product_id", "product_name", "medicine_name")),
        ("branch", ("branch", "warehouse")),
        ("category", ("category", "therapeutic_class")),
    )
    named_hits = {}
    for label, candidates in entity_specs:
        col = next((c for c in candidates if c in df), None)
        if not col:
            continue
        vals = sorted((str(v) for v in frame[col].dropna().unique() if str(v).strip()), key=len, reverse=True)
        hits = [v for v in vals if re.search(rf"(?<!\w){re.escape(v.casefold())}(?!\w)", q)]
        if hits:
            named_hits[label] = (col, hits)
            if len(hits) == 1 and (not compare or label != comparison_label):
                df = df[df[col].astype(str).str.casefold().eq(hits[0].casefold())]

    known_suppliers = [str(v).casefold() for v in frame.supplier_name.dropna().unique()] if "supplier_name" in frame else []
    supplier_lookup = re.search(r"\bfrom\s+([a-z][a-z0-9&' -]+?)(?:[?.]|$)", q)
    if supplier_lookup:
        requested_supplier = supplier_lookup.group(1).strip()
        if requested_supplier not in known_suppliers and not any(value == requested_supplier for value in known_suppliers):
            # A supplier that does not exist in this selected ledger is an
            # empty result, never an unfiltered whole-ledger total.
            if not any(requested_supplier in value or value in requested_supplier for value in known_suppliers):
                return {"answer": f"No matching purchase records were found for supplier {requested_supplier.title()} in the selected data.", "values": {"matched_records": 0}, "source_rows": []}

    grouped_status_request = "status" in df and re.search(r"\b(each|per|by|for each)\b", q) and re.search(r"\bstatus\b", q)
    explicit_status_request = bool(re.search(r"\b(?:payment\s+status|status\s+(?:is|of)|(?:paid|pending|partially\s+paid)\s+purchases?)\b", q))
    explicit_status_request = explicit_status_request or bool(re.search(r"\bpartially\s+paid\b", q))
    if "status" in df and explicit_status_request and not grouped_status_request:
        statuses = sorted((str(v) for v in df.status.dropna().unique()), key=len, reverse=True)
        status_hit = next((v for v in statuses if v.casefold() in q), None)
        if status_hit:
            df = df[df.status.astype(str).str.casefold().eq(status_hit.casefold())]

    # Purchase date periods use the source's transaction date, not today's date.
    if "date" in df and re.search(r"\b(on|during|in|from|between|through|inclusive|earliest|latest|first|last)\b", q):
        dates = pd.to_datetime(df.date, errors="coerce")
        iso = re.findall(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", q)
        if len(iso) >= 2:
            start, end = (pd.Timestamp(*map(int, item)) for item in iso[:2])
            df = df[(dates.dt.normalize() >= start) & (dates.dt.normalize() <= end)]
        elif len(iso) == 1 and re.search(r"\b(before|prior to|earlier than)\b", q):
            target = pd.Timestamp(*map(int, iso[0])); df = df[dates.dt.normalize() < target]
        elif len(iso) == 1 and re.search(r"\b(after|later than)\b", q):
            target = pd.Timestamp(*map(int, iso[0])); df = df[dates.dt.normalize() > target]
        elif len(iso) == 1:
            target = pd.Timestamp(*map(int, iso[0])); df = df[dates.dt.date == target.date()]
        else:
            natural = re.findall(r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b", q)
            if len(natural) >= 2 and re.search(r"\bfrom\b", q):
                months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
                parsed = [pd.Timestamp(int(year), months[month], int(day)) for day, month, year in natural[:2]]
                df = df[(dates.dt.normalize() >= min(parsed)) & (dates.dt.normalize() <= max(parsed))]
            month_names = {"january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,"may":5,"june":6,"jun":6,"july":7,"jul":7,"august":8,"aug":8,"september":9,"sep":9,"october":10,"oct":10,"november":11,"nov":11,"december":12,"dec":12}
            month = next((m for name, m in month_names.items() if re.search(rf"\b{name}\b", q)), None)
            year = re.search(r"\b(20\d{2})\b", q)
            if month and year and len(natural) < 2:
                df = df[(dates.dt.month == month) & (dates.dt.year == int(year.group(1)))]

    # Relational value filters (strict/inclusive semantics from the wording).
    if "status" in df and explicit_status_request and not grouped_status_request and re.search(r"\b(pending|paid|partially paid)\b", q):
        status_values = sorted((str(v) for v in df.status.dropna().unique()), key=len, reverse=True)
        hit = next((v for v in status_values if v.casefold() in q), None)
        if hit: df = df[df.status.astype(str).str.casefold().eq(hit.casefold())]
    amount_pred = re.search(r"\b(invoice[_ ]total|invoice total|total value|value)\b.{0,40}\b(greater than|more than|over|above|at least|exactly|equal to|less than|below|under)\s*(?:pkr\s*)?([\d,]+(?:\.\d+)?)", q)
    quantity_pred = re.search(r"\b(quantity|units?)\b.{0,40}\b(greater than|more than|over|above|at least|exactly|equal to|less than|below|under)\s*([\d,]+(?:\.\d+)?)", q)
    for pred, col in ((amount_pred, amount_col), (quantity_pred, quantity_col)):
        if pred:
            op = pred.group(2); val = float(pred.group(3).replace(",", "")); nums = pd.to_numeric(df[col], errors="coerce")
            if op in ("greater than", "more than", "over", "above"): df = df[nums > val]
            elif op == "at least": df = df[nums >= val]
            elif op == "less than" or op in ("below", "under"): df = df[nums < val]
            elif op in ("exactly", "equal to"): df = df[nums == val]

    if df.empty:
        return {"answer": "No matching purchase records were found in the selected data.", "values": {"matched_records": 0}, "source_rows": []}
    amounts = pd.to_numeric(df[amount_col], errors="coerce")
    quantities = pd.to_numeric(df[quantity_col], errors="coerce")
    record_count = int(ids.loc[df.index].replace("", pd.NA).nunique())

    # Compare two named suppliers or branches using requested measures.
    if compare:
        compare_info = next(((label, col, hits) for label, (col, hits) in named_hits.items() if len(hits) >= 2), None)
        if compare_info:
            label, col, hits = compare_info
            pieces, values, cited = [], {}, []
            for entity in hits[:2]:
                part = df[df[col].astype(str).str.casefold().eq(entity.casefold())]
                amount = pd.to_numeric(part[amount_col], errors="coerce").sum()
                qty = pd.to_numeric(part[quantity_col], errors="coerce").sum()
                count = int(part["purchase_order_no"].nunique())
                info = [f"purchase value {amount:,.2f}"]
                if re.search(r"\b(quantity|units?)\b", q): info.append(f"quantity {qty:,.0f}")
                if re.search(r"\b(number of|count|purchases?)\b", q): info.append(f"{count:,} purchases")
                average_cost = None
                if re.search(r"\baverage|avg|mean\b", q) and cost_col:
                    avg = pd.to_numeric(part[cost_col], errors="coerce").mean(); average_cost = float(avg); info.append(f"average unit cost {avg:,.2f}")
                pieces.append(f"{entity}: " + ", ".join(info))
                values[entity] = {"purchase_value": float(amount), "quantity": float(qty), "purchases": count, "average_unit_cost": average_cost}
                cited.extend(row_ids.loc[part.index].tolist())
            if re.search(r"\bshare\b", q):
                overall = pd.to_numeric(frame[amount_col], errors="coerce").sum()
                for entity in hits[:2]:
                    values[entity]["share_pct"] = 100 * values[entity]["purchase_value"] / overall if overall else None
            answer = "; ".join(pieces) + "."
            if re.search(r"\b(cheaper|lower cost|lowest cost)\b", q) and all(values[e]["average_unit_cost"] is not None for e in hits[:2]):
                cheaper = min(hits[:2], key=lambda e: values[e]["average_unit_cost"])
                cost_difference = abs(values[hits[0]]["average_unit_cost"] - values[hits[1]]["average_unit_cost"])
                answer += f" {cheaper} had the lower average unit cost by {cost_difference:,.2f}."
            elif len(hits) >= 2 and "purchase_value" in values[hits[0]]:
                difference = abs(values[hits[0]]["purchase_value"] - values[hits[1]]["purchase_value"])
                answer += f" Purchase value difference: {difference:,.2f}."
            return {"answer": answer, "values": {"comparison": values}, "source_rows": cited}

    # Grouped status totals and supplier-paid percentage.
    if "status" in q and "status" in df and re.search(r"\b(each|per|by|for each)\b", q):
        group = df.groupby("status", dropna=True).agg(purchases=("purchase_order_no", "nunique"), value=(amount_col, "sum"))
        total_value = float(group.value.sum())
        parts = [f"{name}: {int(r.purchases)} purchases, {r.value:,.2f}" for name, r in group.iterrows()]
        paid = float(group.loc[[v for v in group.index if str(v).casefold() == "paid"], "value"].sum()) if any(str(v).casefold() == "paid" for v in group.index) else 0.0
        pct = 100 * paid / total_value if total_value else 0.0
        return {"answer": "; ".join(parts) + f". Fully paid share of value: {pct:.2f}%.", "values": {"by_status": group.reset_index().to_dict("records"), "paid_value_share_pct": pct}, "source_rows": row_ids.loc[df.index].tolist()}

    if "branch" in df and re.search(r"\bbranch(?:es)?\b", q) and re.search(r"\b(highest|most|largest)\b", q) and re.search(r"\b(value|amount|total)\b", q):
        grouped = frame.assign(_purchase_value=pd.to_numeric(frame[amount_col], errors="coerce")).groupby("branch", dropna=True)._purchase_value.sum().sort_values(ascending=False)
        if len(grouped):
            branch = str(grouped.index[0]); value = float(grouped.iloc[0]); overall = float(pd.to_numeric(frame[amount_col], errors="coerce").sum()); share = 100 * value / overall if overall else 0.0
            part = frame[frame.branch.astype(str).eq(branch)]
            return {"answer": f"{branch} has the highest total purchase value: {value:,.2f}, which is {share:.2f}% of all purchase value ({overall:,.2f}).", "values": {"branch": branch, "purchase_value": value, "all_purchase_value": overall, "share_pct": share}, "source_rows": row_ids.loc[part.index].tolist() + row_ids.loc[frame.index].tolist()}

    # Explicit high-value purchase ranking.
    if re.search(r"\b(top|highest|largest)\b", q) and re.search(r"\b(purchases?|invoice|value|total)\b", q):
        n_match = re.search(r"\btop\s+(\d+)\b", q); n = int(n_match.group(1)) if n_match else 3
        chosen = df.assign(_amount=amounts).sort_values("_amount", ascending=False).head(n)
        records = []
        for _, row in chosen.iterrows():
            records.append(f"{row['purchase_order_no']}: {row.get('product_id', '')}, {row.get('supplier_name', row.get('supplier_id', ''))}, {float(row[amount_col]):,.2f}")
        return {"answer": "Top purchases by invoice value: " + "; ".join(records) + ".", "values": {"top_purchases": chosen[[c for c in ("purchase_order_no", "product_id", amount_col) if c in chosen]].to_dict("records")}, "source_rows": row_ids.loc[chosen.index].tolist()}

    # Extremes over the selected purchase dates.
    if re.search(r"\b(earliest|latest|first|most recent)\b", q) and "date" in df:
        dates = pd.to_datetime(df.date, errors="coerce")
        which = []
        if re.search(r"\b(earliest|first)\b", q): which.append(("Earliest", dates.idxmin()))
        if re.search(r"\b(latest|most recent)\b", q): which.append(("Latest", dates.idxmax()))
        parts, cited, values = [], [], {}
        for label, idx in which:
            row = df.loc[idx]; when = str(dates.loc[idx].date()); po = row["purchase_order_no"]
            products = [str(row[c]) for c in ("product_id", "supplier_name", "branch") if c in row and pd.notna(row[c])]
            same_date = df.loc[dates.dt.date == dates.loc[idx].date()]
            ids_on_date = same_date.purchase_order_no.dropna().astype(str).tolist()
            parts.append(f"{label}: {when}, purchases {', '.join(ids_on_date)}" + (f" ({'; '.join(products)})" if products else "") + f"; {len(ids_on_date)} purchases on that date")
            values[label.casefold()] = {"date": when, "purchase_order_no": po, "ids_on_date": ids_on_date}; cited.extend(row_ids.loc[same_date.index].tolist())
        if parts: return {"answer": "; ".join(parts) + ".", "values": values, "source_rows": cited}

    if cost_col and re.search(r"\b(average|avg|mean)\b.{0,40}\b(unit cost|cost)\b", q):
        costs = pd.to_numeric(df[cost_col], errors="coerce").dropna()
        if len(costs):
            avg = float(costs.mean()); qty = float(quantities.sum())
            answer = f"Average unit cost: {avg:,.2f}. Total quantity purchased: {qty:,.0f} across {record_count:,} purchases."
            return {"answer": answer, "values": {"average_unit_cost": avg, "total_quantity": qty, "purchase_count": record_count}, "source_rows": row_ids.loc[df.index].tolist()}

    if re.search(r"\b(percent(?:age)?|share|proportion)\b", q) and "status" in frame and re.search(r"\bpending\b", q):
        all_count = int(frame.purchase_order_no.nunique()); pending = frame[frame.status.astype(str).str.casefold().eq("pending")]
        count = int(pending.purchase_order_no.nunique()); value = pd.to_numeric(pending[amount_col], errors="coerce").sum(); pct = 100 * count / all_count if all_count else 0.0
        return {"answer": f"Pending purchases: {count:,} of {all_count:,} ({pct:.2f}%), total value {value:,.2f}.", "values": {"pending_count": count, "total_purchases": all_count, "pending_percentage": pct, "pending_value": float(value)}, "source_rows": row_ids.loc[pending.index].tolist()}

    # Generic sum/count/average over the already selected entity/date/status set.
    wants_total_value = bool(re.search(r"\b(total|combined|sum|value|amount|spend|purchase value|invoice)\b", q))
    wants_total_qty = bool(re.search(r"\b(total quantity|total qty|quantity|units?)\b", q))
    wants_count = bool(re.search(r"\b(how many|count|number of|purchases?)\b", q))
    wants_average_cost = bool(re.search(r"\b(average|avg|mean)\b.{0,40}\b(cost)\b", q))
    if wants_total_value or wants_total_qty or wants_count or wants_average_cost:
        parts, values = [], {}
        if wants_total_qty:
            qty = float(quantities.sum()); values["total_quantity"] = qty; parts.append(f"Total quantity: {qty:,.0f}")
        if wants_total_value:
            total = float(amounts.sum()); values["total_purchase_value"] = total; parts.append(f"Total purchase value: {total:,.2f}")
        if wants_count:
            values["purchase_count"] = record_count; parts.append(f"Purchases: {record_count:,}")
        if wants_average_cost and cost_col:
            avg = float(pd.to_numeric(df[cost_col], errors="coerce").mean()); values["average_unit_cost"] = avg; parts.append(f"Average unit cost: {avg:,.2f}")
        # List rows when the request explicitly asks to show/list purchases.
        if re.search(r"\b(show|list|which purchases|all purchases)\b", q):
            parts.append("Records: " + "; ".join(str(v) for v in ids.loc[df.index].tolist()[:30]))
        if amount_col == "invoice_total" and "_header_source_file" in df and "_header_source_row" in df:
            header_rows = df.loc[df[amount_col].notna() & df["_header_source_file"].notna() & df["_header_source_row"].notna()]
            source_rows = list(dict.fromkeys(
                (str(row["_header_source_file"]), row["_header_source_row"])
                for _, row in header_rows.iterrows()
            ))
        elif "source_file" in df and "source_row" in df:
            source_rows = [(str(row["source_file"]), row["source_row"]) for _, row in df.loc[df["source_row"].notna()].iterrows() if pd.notna(row["source_file"])]
        else:
            source_rows = row_ids.loc[df.index].tolist()
        return {"answer": "; ".join(parts) + ".", "values": values, "source_rows": source_rows}

    # Exact equality/range list query or an entity request without an aggregate.
    if re.search(r"\b(which|list|show|all|every|what)\b", q):
        table_lines = [
            "| # | PO Number | Date | Supplier | Product | Qty | Cost | Invoice Total |",
            "|---:|---|---|---|---|---:|---:|---:|"
        ]
        for idx, (_, row) in enumerate(df.head(30).iterrows(), 1):
            po = str(row.get("purchase_order_no") or "—").replace("|", "/")
            dt = str(row.get("date") or "—").replace("|", "/")
            supp = str(row.get("supplier_name") or row.get("supplier_id") or "—").replace("|", "/")
            prod = str(row.get("product_name") or row.get("product_id") or "—").replace("|", "/")
            qty = f"{float(row['quantity']):g}" if "quantity" in row and pd.notna(row.get("quantity")) else "—"
            cost_val = f"{float(row['cost']):,.2f}" if "cost" in row and pd.notna(row.get("cost")) else "—"
            inv_val = f"{float(row['invoice_total']):,.2f}" if "invoice_total" in row and pd.notna(row.get("invoice_total")) else "—"
            table_lines.append(f"| {idx} | {po} | {dt} | {supp} | {prod} | {qty} | {cost_val} | {inv_val} |")

        table_md = "\n".join(table_lines)
        capped_note = "\n\n*(List capped at 30 purchases)*" if len(df) > 30 else ""
        answer = f"**Matching Purchases**\n\nFound {len(df):,} matching purchases (showing {min(len(df), 30)}):\n\n{table_md}{capped_note}"
        return {"answer": answer, "values": {"matched_records": len(df)}, "source_rows": row_ids.loc[df.index].tolist()}

    return None


def answer_cross_dataset_question(question: str, frame: pd.DataFrame):
    """Answer conservative set/date comparisons across selected source files.

    This intentionally limits cross-source calculations to explicit common
    keys. It never joins products, customers, or suppliers by fuzzy similarity.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty or "source_file" not in frame:
        return None
    q = re.sub(r"\s+", " ", str(question).casefold()).strip()
    files = frame.source_file.fillna("").astype(str)
    distinct_files = [v for v in files.unique().tolist() if v]
    if len(distinct_files) < 2:
        return None

    # A direct table count must stay on that table's row grain. The general
    # cross-dataset inventory below answers role-level counts and would
    # otherwise return mixed sales/purchase totals with unrelated leading
    # citations for a question naming one concrete table.
    table_match = re.search(r"\b(tbl[_ ]\d+(?:[_ ]\d+)*)\b", q)
    record_count_request = bool(re.search(r"\b(how many|number of|count)\b", q) and re.search(r"\b(records?|rows?|entries|lines?)\b", q))
    if table_match and record_count_request and "table_name" in frame:
        requested_table = re.sub(r"[ ]+", "_", table_match.group(1))
        table_names = frame["table_name"].fillna("").astype(str).str.casefold()
        selected = frame.loc[table_names.eq(requested_table)]
        if not selected.empty:
            row_ids = selected["source_row"] if "source_row" in selected else pd.Series(selected.index + 1, index=selected.index)
            source_rows = []
            for idx in selected.index:
                row = selected.loc[idx]
                file_name = row.get("source_file")
                source_row = row_ids.loc[idx]
                if file_name is not None and str(file_name).strip():
                    source_rows.append((str(file_name), source_row))
                else:
                    source_rows.append(source_row)
            count = int(row_ids.nunique())
            entity = "purchase-detail" if re.search(r"\b(purchase|purchas\w*|bought|buy)\b", q) else ""
            description = f" {entity}" if entity else ""
            return {"answer": f"{requested_table} contains {count:,}{description} records in the selected scope.", "values": {"table": requested_table, "record_count": count}, "source_rows": source_rows}

    def subset(file_name):
        return frame.loc[files.eq(file_name)]

    def role_mask(kind):
        masks = {
            "sales": ("transaction_id", "customer_id", "unit_price"),
            "inventory": ("stock_qty", "reorder_level", "expiry_date"),
            "purchases": ("purchase_order_no", "supplier_name", "cost"),
        }
        cols = masks[kind]
        active = [c for c in cols if c in frame]
        if not active: return pd.Series(False, index=frame.index)
        # A role must have its signature on a row, not merely in the combined schema.
        required = {
            "sales": ("transaction_id",),
            "inventory": ("stock_qty",),
            "purchases": ("purchase_order_no",),
        }[kind]
        required = [c for c in required if c in frame]
        if not required: return pd.Series(False, index=frame.index)
        result = pd.Series(True, index=frame.index)
        for col in required: result &= frame[col].notna()
        return result

    role_frames = {role: frame.loc[role_mask(role)] for role in ("sales", "inventory", "purchases")}
    role_frames = {role: data for role, data in role_frames.items() if not data.empty}
    if len(role_frames) < 2:
        return None
    row_ids = frame["source_row"] if "source_row" in frame else pd.Series(frame.index + 1, index=frame.index)

    def citations(data):
        return [(str(frame.loc[idx, "source_file"]), row_ids.loc[idx]) for idx in data.index]

    explicit_metric_request = bool(re.search(
        r"\b(units?|quantity|qty|amount|revenue|sales value|profit|discount|tax|median|average|avg)\b", q
    ))
    if (not explicit_metric_request and re.search(r"\b(how many|number of|count).{0,40}\brecords?\b|\bwhich (?:dataset|file) has (?:the )?most\b", q)):
        counts = {role: len(data) for role, data in role_frames.items()}
        names = {role: sorted(set(files.loc[data.index].tolist())) for role, data in role_frames.items()}
        largest = max(counts, key=counts.get)
        return {"answer": "; ".join(f"{role.title()}: {count:,} rows" for role, count in counts.items()) + f". {largest.title()} has the most records.", "values": {"record_counts": counts, "largest": largest}, "source_rows": citations(frame)}

    if re.search(r"\b(shared|common|appear in all|all three|intersection)\b", q) and re.search(r"\bcategor(?:y|ies)\b", q):
        roles = [r for r in ("sales", "inventory", "purchases") if r in role_frames]
        sets = {role: set(role_frames[role].get("category", pd.Series(dtype=str)).dropna().astype(str)) for role in roles}
        pairs = []
        for i, a in enumerate(roles):
            for b in roles[i+1:]: pairs.append(f"{a.title()} ∩ {b.title()}: " + (", ".join(sorted(sets[a] & sets[b])) or "none"))
        return {"answer": "; ".join(pairs) + ".", "values": {"shared_categories": {f"{a}:{b}": sorted(sets[a] & sets[b]) for i,a in enumerate(roles) for b in roles[i+1:]}}, "source_rows": citations(frame)}

    if re.search(r"\b(appear|also|shared|same|any|does|do)\b", q):
        field = None
        if re.search(r"\b(product|medicine|item)\b", q): field = "product_id"
        elif re.search(r"\bsupplier\b", q): field = "supplier_name" if "supplier_name" in frame else "supplier_id"
        elif re.search(r"\bcustomer\b", q): field = "customer_id"
        elif re.search(r"\b(branch(?:es)?|pharmacy)\b", q): field = "branch"
        if field:
            present = {role: set(data[field].dropna().astype(str)) for role, data in role_frames.items() if field in data}
            if len(present) >= 2:
                source_role = next((r for r in present if re.search(rf"\b{r}\b", q)), next(iter(present)))
                other_roles = [r for r in present if r != source_role]
                named = next((v for v in present[source_role] if len(v) > 2 and v.casefold() in q), None)
                if named:
                    hits = [r for r in other_roles if named.casefold() in {x.casefold() for x in present[r]}]
                    answer = f"{named} appears in {source_role} data" + (" and " + ", ".join(f"{r} data" for r in hits) if hits else " only; no exact match was found in " + ", ".join(f"{r} data" for r in other_roles)) + "."
                    citing = frame.loc[role_mask(source_role)]
                    return {"answer": answer, "values": {"entity": named, "source_role": source_role, "matching_roles": hits}, "source_rows": citations(citing)}
                if re.search(r"\b(any|shared|common)\b", q):
                    common = set.intersection(*(set(x.casefold() for x in s) for s in present.values()))
                    return {"answer": "Exact shared values: " + (", ".join(sorted(common)) if common else "none") + ".", "values": {"intersection": sorted(common)}, "source_rows": citations(frame)}

    if re.search(r"\b(sales?|purchases?)\b", q) and re.search(r"\b(on|date|recorded)\b", q) and "date" in frame:
        date_match = re.search(r"\b(20\d{4}-\d{2}-\d{2})\b", q)
        # Correct four-digit-year form, kept separate from flexible relative dates.
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", q)
        if date_match:
            target = pd.to_datetime(date_match.group(1), errors="coerce").date()
            parts, counts = [], {}
            cited_indexes = []
            for role, data in role_frames.items():
                ds = pd.to_datetime(data.date, errors="coerce")
                selected = data.loc[ds.dt.date == target]
                ids_col = "transaction_id" if role == "sales" else "purchase_order_no" if role == "purchases" else None
                record_ids = selected[ids_col].dropna().astype(str).tolist() if ids_col else []
                counts[role] = len(selected); parts.append(f"{role.title()}: {len(selected)}" + (" (" + ", ".join(record_ids[:30]) + ")" if record_ids else ""))
                cited_indexes.extend(selected.index.tolist())
            cited_data = frame.loc[list(dict.fromkeys(cited_indexes))]
            return {"answer": "; ".join(parts) + f" on {target.isoformat()}.", "values": {"date": target.isoformat(), "counts": counts}, "source_rows": citations(cited_data)}

    # This branch is explicitly a sales-versus-purchases comparison. Merely
    # asking for total sales (or total purchases) must not silently add the
    # other ledger and report its value as part of the answer.
    mentions_both_ledgers = bool(re.search(
        r"\b(?:sales?\b.{0,60}\bpurchases?\b|purchases?\b.{0,60}\bsales?\b)", q
    ))
    if mentions_both_ledgers and "sales" in role_frames and "purchases" in role_frames and re.search(r"\b(total|compare|difference|profit margin|per[- ]product)\b", q):
        sales, purchases = role_frames["sales"], role_frames["purchases"]
        sales_amount = next((c for c in ("amount", "invoice_total", "net_payable") if c in sales), None)
        purchase_amount = next((c for c in ("invoice_total", "net_payable", "amount") if c in purchases), None)
        if sales_amount and purchase_amount:
            total_sales = float(pd.to_numeric(sales[sales_amount], errors="coerce").sum())
            total_purchase = float(pd.to_numeric(purchases[purchase_amount], errors="coerce").sum())
            sales_products = set(sales.get("product_id", pd.Series(dtype=str)).dropna().astype(str).str.casefold())
            purchase_products = set(purchases.get("product_id", pd.Series(dtype=str)).dropna().astype(str).str.casefold())
            shared = sorted(sales_products & purchase_products)
            difference = total_purchase - total_sales
            answer = f"Total sales: {total_sales:,.2f}; total purchase value: {total_purchase:,.2f}; difference (purchases minus sales): {difference:,.2f}. "
            answer += "A per-product profit margin cannot be computed because there are no exact product matches across these selected sources." if not shared else "Exact product matches exist, but a per-product margin still requires matched cost-of-goods and sales-price records at compatible units."
            source_indexes = sales.index.union(purchases.index)
            return {"answer": answer, "values": {"total_sales": total_sales, "total_purchase_value": total_purchase, "difference": difference, "shared_products": shared}, "source_rows": citations(frame.loc[source_indexes])}

    return None
