"""
Domain-specific KPI packs for the Module 6.6 KPI engine.

Everything here is registered onto `KPIEngine` through a `DomainPack.register_kpis`
hook and is invisible to other domains. The engine core (`models`, `filters`,
`engine`, `kpi`, `seam`) stays free of domain vocabulary — a grep over those files
must find no domain nouns, and a test enforces it.

Modules in this package are NOT imported eagerly: the engine loads a domain the
first time it is asked to do work for that domain.
"""


def analyze_specialized_question(question, frame, filters, domain):
    """Dispatch complete-dataset analyzers owned by a domain pack.

    This hook handles sources whose row grain needs a domain-specific strategy
    before the generic KPI selector runs (for example, normalized multi-table
    snapshots and ledgers with repeated invoice totals).
    """
    from app.language.roman_urdu import normalize_roman_urdu_intent
    question = normalize_roman_urdu_intent(question)
    if str(domain or "").casefold() != "pharmacy":
        return None

    from dataclasses import replace
    import re
    import pandas as pd
    from app.analytics.domains.pharmacy_pos import analyze_pos_question, is_pos_snapshot
    from app.analytics.domains.pharmacy_purchases import pharmacy_purchase_analysis
    from app.analytics.kpi import build_provenance
    from app.analytics.models import KPIResult, UNIT_COUNT, unavailable

    if is_pos_snapshot(frame):
        options = dict(filters.options or {})
        options["question"] = question
        return analyze_pos_question(frame, question, replace(filters, options=options))

    purchase_ledger = {"transaction_id", "invoice_id", "net_payable", "supplier_name"}.issubset(frame.columns)
    purchase_question = bool(re.search(
        r"\b(unique|distinct|invoices?|transactions?|supplier|purchases?|purchased|product groups?|"
        r"margins?|net payable|gst|tax amount|discount amount|bonus quantity|expired by|rows?|records?|"
        r"earliest-expiring|transaction types|cash|credit|total amount|total quantity|product called|product named|"
        r"quantit|units?|price|location|percentage|percent|frequent|variety|different suppliers?|multiple suppliers?|"
        r"expenditure|purchasing|expiry|expired|shelf life|payment|invoice|deliver(?:s|y|ed)?|reliable|stock|"
        r"fastest|demand|profit|reorder|fake|counterfeit|stop purchasing|decrease|increase|month|discount)",
        (question or "").casefold(),
    ))
    if purchase_ledger and purchase_question:
        options = dict(filters.options or {})
        options["question"] = question
        return pharmacy_purchase_analysis(frame, replace(filters, options=options), domain=domain)

    q = str(question or "").casefold()
    invoice_match = re.search(
        r"\b(?:invoice|bill|receipt)\s*(?:no\.?|number|#)?\s*[:#-]?\s*((?=[a-z0-9/-]*\d)[a-z0-9][a-z0-9/-]{2,})\b",
        q,
    )
    if invoice_match and "invoice_id" in frame:
        requested = invoice_match.group(1).casefold()
        mask = frame["invoice_id"].astype(str).str.casefold() == requested
        rows = frame.loc[mask]
        if rows.empty:
            return unavailable("pharmacy_invoice_lookup", "Invoice details", UNIT_COUNT,
                "exact invoice ID match in selected sale records",
                f"invoice {invoice_match.group(1)} was not found in the selected records",
                build_provenance(frame, mask, filters, ["invoice_id"], []))
        product_col = next((col for col in ("product_id", "product_code", "description") if col in frame), None)
        columns = [col for col in (product_col, "quantity", "unit_price", "amount", "discount_pct", "discount_amount", "tax_amount") if col]
        breakdown = []
        for _, row in rows.iterrows():
            item = {col: row[col] for col in columns if pd.notna(row.get(col))}
            breakdown.append(item)
        requested_value = next((
            (phrase, column, title)
            for phrase, column, title in (
                ("net payable", "net_payable", "Net payable"),
                ("payable", "net_payable", "Net payable"),
                ("paid", "paid_amount", "Paid amount"),
                ("balance", "customer_balance", "Customer balance"),
                ("tax", "tax_amount", "Tax amount"),
                ("gst", "tax_amount", "Tax amount"),
                ("discount", "discount_amount", "Discount amount"),
                ("quantity", "quantity", "Quantity"),
                ("qty", "quantity", "Quantity"),
            ) if phrase in q and column in rows
            and pd.to_numeric(rows[column], errors="coerce").notna().any()
        ), None)
        if requested_value:
            _, value_column, title = requested_value
            values = pd.to_numeric(rows[value_column], errors="coerce").dropna()
            # Header amounts are repeated on each detail row in some exports;
            # use the single recorded header value instead of summing it.
            value = float(values.iloc[0]) if value_column != "quantity" else float(values.sum())
            formula = f"recorded {value_column} for the exact invoice ID"
            if value_column == "quantity":
                formula = "sum of line quantities for the exact invoice ID"
            # Don't show unrelated or misleading columns (for example a POS
            # header's zero-valued gross amount beside its actual net payable).
            breakdown = [{title: round(value, 2)}]
            columns = [value_column]
        elif "invoice_total" in rows and pd.to_numeric(rows["invoice_total"], errors="coerce").notna().any():
            title = "Invoice total"
            value = float(pd.to_numeric(rows["invoice_total"], errors="coerce").dropna().iloc[0])
            formula = "first recorded invoice_total for the exact invoice (header amount is not summed once per line)"
        elif "amount" in rows and pd.to_numeric(rows["amount"], errors="coerce").notna().any():
            title = "Invoice details"
            value = float(pd.to_numeric(rows["amount"], errors="coerce").sum())
            formula = "sum of line amounts for the exact invoice ID"
        else:
            title = "Invoice details"
            value = len(rows)
            formula = "number of matching invoice detail rows; no monetary total field is available"
        has_money = value_column != "quantity" if requested_value else (
            ("invoice_total" in rows and pd.to_numeric(rows["invoice_total"], errors="coerce").notna().any()) or
            ("amount" in rows and pd.to_numeric(rows["amount"], errors="coerce").notna().any())
        )
        unit = UNIT_COUNT if requested_value and value_column == "quantity" else "PKR" if has_money else UNIT_COUNT
        return KPIResult("pharmacy_invoice_lookup", title, round(value, 2), unit,
            formula, build_provenance(frame, mask, filters, ["invoice_id"] + columns,
                [f"Matched invoice ID: {invoice_match.group(1)}."]),
            breakdown=breakdown, breakdown_columns=[title] if breakdown else None)

    product_col = next((col for col in ("product_id", "product_code", "description") if col in frame), None)
    if re.search(r"\b(bonus|free quantity|free units?)\b", q):
        col = next((name for name in ("bonus_quantity", "total_bonus") if name in frame), None)
        if col is None:
            empty = pd.Series(False, index=frame.index)
            return unavailable("pharmacy_line_item_filter", "Bonus quantity", UNIT_COUNT,
                "sum of recorded bonus units by product", "the selected data has no mapped bonus-quantity field",
                build_provenance(frame, empty, filters, [], []))
        values = pd.to_numeric(frame[col], errors="coerce").fillna(0)
        mask = values > 0
        if product_col:
            query_tokens = set(re.sub(r"[^a-z0-9]+", " ", q).split())
            ignored = {"tablet", "tablets", "tab", "tabs", "cream", "capsule", "capsules", "syrup", "drops", "mg", "ml"}
            named = []
            for value in frame.loc[mask, product_col].dropna().unique():
                tokens = re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split()
                identity = [token for token in tokens if len(token) >= 4 and not token.isdigit() and token not in ignored]
                if identity and any(token in query_tokens for token in identity):
                    named.append(str(value))
            if named:
                mask &= frame[product_col].astype(str).isin(named)
        if product_col:
            grouped = frame.loc[mask].assign(_bonus=values[mask]).groupby(product_col, dropna=True)["_bonus"].sum()
            breakdown = [{"product": str(name), "bonus_units": float(qty)} for name, qty in grouped.sort_values(ascending=False).items()]
        else:
            breakdown = [{"source_row": int(row.get("source_row")), "bonus_units": float(values.loc[idx])}
                         for idx, row in frame.loc[mask].iterrows() if pd.notna(row.get("source_row"))]
        return KPIResult("pharmacy_line_item_filter", "Recorded bonus quantity", float(values[mask].sum()),
            UNIT_COUNT, f"sum of positive {col} values from matching rows",
            build_provenance(frame, mask, filters, [col] + ([product_col] if product_col else []),
                             ["Only bonus values explicitly present in the selected rows are counted."]),
            breakdown=breakdown, breakdown_columns=list(breakdown[0]) if breakdown else None)

    discount_request = bool(re.search(r"\b(discount|discounted|markdown)\b", q) and re.search(r"\b(which|what|list|show|find)\b", q))
    if discount_request:
        col = "discount_pct" if "discount_pct" in frame else None
        percent_match = re.search(r"\b(\d+(?:\.\d+)?)\s*%", q)
        if col is None:
            empty = pd.Series(False, index=frame.index)
            return unavailable("pharmacy_line_item_filter", "Discounted products", UNIT_COUNT,
                "filter sale rows by recorded discount percentage", "the selected data has no mapped discount-percentage field",
                build_provenance(frame, empty, filters, [], []))
        values = pd.to_numeric(frame[col], errors="coerce")
        mask = values.notna() & (values > 0)
        if percent_match:
            mask &= values.round(4) == float(percent_match.group(1))
        if product_col:
            grouped = frame.loc[mask].groupby(product_col, dropna=True)[col].agg(["max", "count"]).sort_values(["max", "count"], ascending=False)
            breakdown = [{"product": str(name), "discount_pct": float(row["max"]), "matching_rows": int(row["count"])}
                         for name, row in grouped.iterrows()]
            value = len(breakdown)
        else:
            breakdown = []
            value = int(mask.sum())
        qualifier = f"exactly {percent_match.group(1)} percent" if percent_match else "a positive percentage"
        return KPIResult("pharmacy_line_item_filter", "Products with recorded discounts", value, UNIT_COUNT,
            f"distinct products among rows with {qualifier} in {col}",
            build_provenance(frame, mask, filters, [col] + ([product_col] if product_col else []),
                             ["Discount percentages are filtered as stored; monetary discount amounts are not treated as percentages."]),
            breakdown=breakdown, breakdown_columns=list(breakdown[0]) if breakdown else None)
    return None
