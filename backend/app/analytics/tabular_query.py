"""Conservative, schema-driven execution for questions over connected tabular data.

This is intentionally deterministic: it operates on the complete selected frame,
returns contributing source rows, and declines unsupported operations.
"""
from __future__ import annotations
import re
import difflib
import pandas as pd


def _catalog_available_mask(series):
    """Recognize explicit availability labels in pharmacy catalog exports."""
    values = series.fillna("").astype(str).str.strip().str.casefold()
    return values.isin({"available", "in stock", "low stock", "add to cart"})


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


def answer_tabular_question(question: str, frame):
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    catalog_result = answer_product_catalog_question(question, frame)
    if catalog_result is not None:
        return catalog_result
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
            idx=exp.idxmin(); row=df.loc[idx]
            record_id=row.get("product_code",row.get("product_id","inventory record"))
            return {"answer":f"No batch number is recorded. The earliest-expiring matching inventory record is {record_id}, expiring {exp.loc[idx].date()}.","values":{"expiry_date":str(exp.loc[idx].date()),"record_id":record_id,"status":"missing_batch_field"},"source_rows":[row_ids.loc[idx]]}
        return {"answer":"The selected records do not contain batch or lot identifiers, so I can't identify which batch expires first.","values":{"status":"missing_batch_field"},"source_rows":[]}

    if (pred or named_pred) and field and re.search(r"\b(which|list|show|how many|count)\b",q):
        id_col=next((c for c in ("invoice_id","transaction_id","product_code","product_id") if c in df),None)
        ids=df[id_col].dropna().astype(str).tolist() if id_col else []
        return {"answer":f"{len(df)} matching records"+(": "+", ".join(ids[:30]) if ids else "")+".","values":{"matched_records":len(df)},"source_rows":row_ids.loc[df.index].tolist()}

    # Multiple explicit inventory IDs indicate a bounded value calculation.
    identifiers=re.findall(r"\b(?:sale|sales|pur|purchase|inv|inventory|sku)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",q)
    if len(set(identifiers))>1:
        id_col=next((c for c in ("product_code","invoice_id","transaction_id","product_id") if c in frame),None)
        if id_col and re.search(r"\b(value|worth|cost|combined|total)\b",q) and "quantity" in frame and "cost" in frame:
            wanted={x.replace("_","-").casefold() for x in identifiers}
            chosen=frame[frame[id_col].astype(str).str.casefold().isin(wanted)]
            if len(chosen):
                value=(pd.to_numeric(chosen.quantity,errors="coerce")*pd.to_numeric(chosen.cost,errors="coerce")).sum()
                return {"answer":f"Combined inventory value: {value:,.0f}.","values":{"inventory_value":value,"matched_records":len(chosen)},"source_rows":row_ids.loc[chosen.index].tolist()}

    # Exact record identifier selection (never infer from a partial identifier).
    id_match = re.search(r"\b(?:sale|sales|pur|purchase|inv|inventory|sku)[-_/]?[a-z0-9-]*\d[a-z0-9-]*\b",q)
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
        dcol="expiry_date" if "expir" in q and "expiry_date" in frame else "date"
        ds=pd.to_datetime(frame[dcol],errors="coerce")
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
