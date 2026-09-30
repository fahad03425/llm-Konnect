"""Deterministic analytics for pharmacy purchase ledgers."""

import re
from numbers import Real
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.filters import KPIFilters
from app.analytics.kpi import build_provenance
from app.analytics.models import KPIResult, UNIT_COUNT, UNIT_CURRENCY, UNIT_PERCENT, unavailable


def pharmacy_purchase_analysis(
    df: pd.DataFrame, filters: KPIFilters, domain: str = "pharmacy"
) -> KPIResult:
    """Answer deterministic profile and purchase-ledger questions from every row."""
    question = str(filters.option("question", "")).casefold()
    all_rows = pd.Series(True, index=df.index)
    money = lambda col: pd.to_numeric(df[col], errors="coerce") if col in df else None
    tx_col = "transaction_id" if "transaction_id" in df else "invoice_id"
    product_col = "product_id" if "product_id" in df else "product_code"
    supplier_col = "supplier_name" if "supplier_name" in df else "supplier_id"
    amount = money("amount")

    def result(name: str, value: float, unit: str = UNIT_COUNT, formula: str = "", breakdown=None, mask=None):
        used = mask if mask is not None else all_rows
        prov = build_provenance(df, used, filters, [], [])
        numeric_value = float(value) if isinstance(value, Real) else value
        return KPIResult(
            key="pharmacy_purchase_analysis", name=name, value=numeric_value, unit=unit,
            formula=formula or name, provenance=prov, breakdown=breakdown,
            breakdown_columns=list(breakdown[0]) if breakdown else None,
        )

    def insufficient(name: str, reason: str, unit: str = UNIT_COUNT):
        return unavailable("pharmacy_purchase_analysis", name, unit, "required source data availability", reason,
            build_provenance(df, df.index.to_series().isin([]), filters, [], []))

    def table_records(frame: pd.DataFrame, columns: List[str]) -> List[Dict[str, Any]]:
        rows = []
        for _, row in frame.iterrows():
            item = {}
            for col in columns:
                value = row.get(col)
                if pd.isna(value):
                    item[col] = None
                elif isinstance(value, Real):
                    item[col] = round(float(value), 2)
                elif hasattr(value, "strftime"):
                    item[col] = value.strftime("%Y-%m-%d")
                else:
                    item[col] = str(value)
            rows.append(item)
        return rows

    def top_n(default: int = 5) -> int:
        found = re.search(r"\btop\s+(\d+)\b", question)
        return max(1, min(20, int(found.group(1)))) if found else default

    def ranking_limit() -> int:
        plural_rank = bool(re.search(r"\b(products|items|medicines)\b", question)) and not re.search(r"\bwhich product\b", question)
        return top_n(5 if plural_rank else 1)

    def transaction_rows():
        match = re.search(r"\b(?:transaction|txn)\s*(?:no\.?|number|#)?\s*(\d{3,})\b", question)
        if not match:
            return None
        if tx_col not in df:
            return df.iloc[0:0]
        return df[df[tx_col].astype(str).str.strip() == match.group(1)]

    def supplier_rows():
        if supplier_col not in df:
            return df.iloc[0:0]
        candidates = sorted(
            (str(v) for v in df[supplier_col].dropna().unique() if len(str(v)) >= 5),
            key=len, reverse=True,
        )
        found = next((name for name in candidates if name.casefold() in question), None)
        return df[df[supplier_col].astype(str).str.casefold() == found.casefold()] if found else None

    supplier_intent = bool(re.search(r"\b(suppliers?|vendors?|distributors?)\b", question))

    # Do not turn purchase rows into unsupported claims about sales, stock,
    # delivery performance, product authenticity, or business causes.
    unsupported_data_questions = [
        (r"selling fastest|sales? velocity|fastest[- ]selling", "Customer sales history is not present in this purchase dataset, so sales velocity cannot be calculated."),
        (r"\breorder\b|should i order|should we order", "A reliable reorder decision needs current stock and customer consumption or sales velocity; this file contains historical purchases only."),
        (r"\breliable\b|reliability|delivers? (?:medicines? )?fastest|delivery (?:time|performance|speed)", "Supplier reliability or delivery speed cannot be determined because delivery dates, service levels, and quality outcomes are not recorded."),
        (r"currently in stock|current stock|stock on hand|how many .* in stock", "Historical purchased quantities are not current inventory. Stock sales, adjustments, and an as-of inventory balance are required."),
        (r"\bprofit\b|\bnet earnings\b", "Actual profit cannot be calculated from this purchase ledger alone; it lacks completed customer sales and operating expenses."),
        (r"forecast|predict|next month.*demand|demand.*next month", "This file records purchases, not customer demand or sales. A demand forecast from it would be misleading."),
        (r"fake|counterfeit|authentic|genuine medicine", "The dataset contains no medicine-authenticity or regulatory verification evidence."),
        (r"stop purchasing|stop buying|which supplier should .* stop", "The dataset alone cannot justify stopping purchases; it lacks supplier quality, delivery, and comparable pricing evidence."),
        (r"shortest shelf life|shelf life", "Manufacturing dates or a validated shelf-life field are not available, so shelf life cannot be calculated from purchase and expiry dates."),
        (r"why did purchases? (?:decrease|increase)|cause of (?:the )?purchase", "The ledger can show when purchase totals changed, but it does not record why the change occurred."),
    ]
    for pattern, reason in unsupported_data_questions:
        if re.search(pattern, question):
            return insufficient("Insufficient data", reason)

    qty = money("quantity")
    if supplier_intent and "bonus" not in question and "expired" not in question and re.search(r"\b(quantit\w*|qty|units?)\b", question) and re.search(r"highest|largest|greatest|most|second", question):
        grouped = df.assign(_qty=qty).groupby(supplier_col, dropna=True)['_qty'].sum().rename("quantity").reset_index()
        grouped = grouped.sort_values(["quantity", supplier_col], ascending=[False, True]).reset_index(drop=True)
        rank = 2 if "second" in question else 1
        selected = grouped.iloc[rank - 1:rank] if len(grouped) >= rank else grouped.iloc[0:0]
        if selected.empty:
            return insufficient("Supplier quantity ranking", "There are not enough supplier records to determine that rank.")
        selected = grouped.head(top_n(5 if "suppliers" in question else 1)) if rank == 1 else selected
        return result(f"Supplier at rank {rank} by purchased quantity", selected.iloc[0]["quantity"], "units",
            "sum line quantities by supplier name", table_records(selected, [supplier_col, "quantity"]))
    if (supplier_intent or re.search(r"\bsuppl(?:y|ied|ies)\b", question)) and "expired" not in question and re.search(r"\b(quantit\w*|qty|units?)\b", question) and "how many" in question:
        supplier = supplier_rows()
        if supplier is None:
            return insufficient("Supplier purchased quantity", "No exact supplier name in the question matched the selected dataset.")
        return result("Total units supplied", pd.to_numeric(supplier["quantity"], errors="coerce").sum(), "units",
            "sum line quantities for the matched supplier name", mask=df.index.isin(supplier.index))

    # Rank records (not grouped products) and unit prices independently.
    if re.search(r"individual record|single record|record has the highest quantity", question) and ("quantity" in question or "qty" in question):
        maximum = qty.max()
        tied_count = int(qty.eq(maximum).sum())
        matches = df.loc[qty.eq(maximum)].sort_values([product_col, tx_col]).head(10)
        cols = [c for c in [product_col, "quantity", tx_col, "invoice_id", supplier_col] if c in matches]
        return result(f"Highest quantity per product record ({tied_count} tied; showing up to 10)", maximum, "units", "maximum row-level quantity; identify ties instead of presenting an arbitrary record as uniquely highest", table_records(matches, cols), mask=df.index.isin(matches.index))
    if "purchase price" in question and re.search(r"highest|largest|most expensive|maximum", question):
        price_col = "cost" if "cost" in df else "unit_price"
        if price_col not in df:
            return insufficient("Purchase price", "No purchase-price field is present in this dataset", UNIT_CURRENCY)
        idx = pd.to_numeric(df[price_col], errors="coerce").idxmax()
        row = df.loc[[idx]]
        return result("Highest purchase price in one record", row.iloc[0][price_col], UNIT_CURRENCY,
            f"maximum row-level {price_col}", table_records(row, [product_col, price_col, tx_col, supplier_col]), mask=df.index == idx)
    if "sale price" in question and re.search(r"highest|largest|most expensive|maximum", question):
        price_col = "mrp" if "mrp" in df else "sale_price"
        if price_col not in df:
            return insufficient("Sale price", "No sale-price field is present in this dataset", UNIT_CURRENCY)
        idx = pd.to_numeric(df[price_col], errors="coerce").idxmax()
        row = df.loc[[idx]]
        return result("Highest listed sale price", row.iloc[0][price_col], UNIT_CURRENCY,
            f"maximum row-level {price_col}", table_records(row, [product_col, price_col, tx_col, supplier_col]), mask=df.index == idx)

    # Bonus, discount and tax incidence are record counts, distinct from their
    # corresponding monetary/quantity sums.
    if re.search(r"how many records?.*(bonus|discount|gst|tax)|records?.*(contain|have|include).*(bonus|discount|gst|tax)", question) and not re.search(r"percentage|percent", question):
        if "bonus" in question:
            col, label = "bonus_quantity", "records with a positive bonus quantity"
        elif "discount" in question:
            col, label = "discount_pct", "records with a positive discount percentage"
        else:
            col, label = "tax_pct", "records with a positive GST percentage"
        vals = money(col)
        mask = vals.gt(0)
        return result(label.title(), int(mask.sum()), UNIT_COUNT, f"count rows where {col} > 0", mask=mask)

    if re.search(r"location code|location occurs|location .*frequent", question):
        if "rack_location" not in df:
            return insufficient("Location frequency", "No location-code field is present in this dataset")
        counts = df["rack_location"].dropna().value_counts()
        top = counts[counts.eq(counts.max())].rename_axis("location_code").reset_index(name="records")
        return result("Most frequent location code", top.iloc[0]["records"], UNIT_COUNT,
            "count records per location; include all tied top locations", table_records(top, ["location_code", "records"]))

    # Quantity totals and expenditure over the same supplier/category/time axes.
    category_match = next((str(v) for v in df.get("category", pd.Series(dtype=object)).dropna().unique() if str(v).casefold() in question), None)
    category_question = "group" in question or "category" in question or category_match is not None
    if category_question and ("quantity" in question or "qty" in question or "units" in question):
        grouped = df.assign(_qty=qty).groupby("category", dropna=True)["_qty"].sum().rename("quantity").reset_index()
        if "compare" in question and category_match:
            mentioned = [str(v) for v in df["category"].dropna().unique() if str(v).casefold() in question]
            if len(mentioned) >= 2:
                selected = grouped[grouped["category"].isin(mentioned)].copy()
                values = dict(zip(selected["category"], selected["quantity"]))
                difference = abs(float(values[mentioned[0]]) - float(values[mentioned[1]]))
                return result("Quantity difference between product groups", difference, "units",
                    "absolute difference between group-level sums", table_records(selected, ["category", "quantity"]))
        if re.search(r"highest|largest|greatest|most|lowest|least", question):
            ascending = bool(re.search(r"lowest|least", question))
            selected = grouped.sort_values(["quantity", "category"], ascending=[ascending, True]).head(top_n(5 if "which product groups" in question else 1))
            return result("Product group with highest/lowest purchased quantity", selected.iloc[0]["quantity"], "units",
                "sum line quantities by group", table_records(selected, ["category", "quantity"]))
        if category_match:
            row = grouped[grouped["category"].str.casefold() == category_match.casefold()].head(1)
            return result(f"Purchased quantity for {category_match}", row.iloc[0]["quantity"], "units",
                "sum line quantities for product group", table_records(row, ["category", "quantity"]))
        return result("Purchased quantity by product group", grouped["quantity"].sum(), "units",
            "sum line quantities grouped by product group", table_records(grouped.sort_values("quantity", ascending=False), ["category", "quantity"]))

    if "quantity" in question and ("compare" in question or "how much higher" in question) and re.search(r"supplier|distributors|pharma", question):
        candidates = [str(v) for v in df[supplier_col].dropna().unique() if str(v).casefold() in question]
        if len(candidates) >= 2:
            selected_df = df[df[supplier_col].isin(candidates)].assign(_qty=qty)
            grouped = selected_df.groupby(supplier_col)['_qty'].sum().rename("quantity").reset_index()
            diff = float(grouped["quantity"].max() - grouped["quantity"].min())
            return result("Supplier quantity difference", diff, "units", "difference between named supplier totals", table_records(grouped, [supplier_col, "quantity"]))

    if "recorded" in question and "month" in question and re.search(r"highest|largest|most", question):
        dated = df.assign(_month=pd.to_datetime(df["date"], errors="coerce").dt.to_period("M").astype(str), _amount=money("amount"))
        grouped = dated.groupby("_month", dropna=True)['_amount'].sum().rename("amount").reset_index().rename(columns={"_month": "month"})
        selected = grouped.sort_values(["amount", "month"], ascending=[False, True]).head(top_n(1))
        return result("Month with highest product amount", selected.iloc[0]["amount"], UNIT_CURRENCY,
            "sum line amounts per invoice month", table_records(selected, ["month", "amount"]))

    if "quantity" in question and "margin" in question and re.search(r"products?|medicines?", question):
        grouped = df.groupby(product_col, dropna=True).agg(
            quantity=("quantity", "sum"), average_margin_pct=("margin_pct", "mean")
        ).reset_index()
        grouped = grouped[grouped["average_margin_pct"] > 40].sort_values(["quantity", "average_margin_pct", product_col], ascending=[False, False, True])
        grouped = grouped.head(top_n(10))
        return result("High-quantity products with margins above 40%", len(grouped), UNIT_COUNT,
            "product quantity sum and mean margin; show products with mean margin above 40 percent", table_records(grouped, [product_col, "quantity", "average_margin_pct"]))

    if re.search(r"percentage|percent", question) and re.search(r"gst|tax|discount|bonus", question) and not ("supplier" in question and "average discount" in question):
        if "gst" in question or "tax" in question:
            field, label = "tax_pct", "GST-positive records"
        elif "discount" in question:
            field, label = "discount_pct", "discounted records"
        else:
            field, label = "bonus_quantity", "records with a bonus"
        positive = money(field).gt(0)
        count = int(positive.sum())
        percent = count * 100.0 / len(df) if len(df) else 0.0
        return result(f"Percentage of {label}", percent, UNIT_PERCENT,
            f"{count} of {len(df)} product records have {field} > 0")

    product_name = next((str(v) for v in sorted(df[product_col].dropna().unique(), key=lambda v: len(str(v)), reverse=True) if str(v).casefold() in question), None) if product_col in df else None
    if "different suppliers" in question and product_name:
        rows = df[df[product_col].astype(str).str.casefold() == product_name.casefold()]
        supplier_count = rows[supplier_col].nunique() if supplier_col in rows else 0
        return result(f"Suppliers for {product_name}", supplier_count, UNIT_COUNT,
            "distinct supplier names for exact product name", [{"product_id": product_name, "distinct_suppliers": supplier_count}], mask=df.index.isin(rows.index))
    if "product" in question and "supplier" in question and re.search(r"greatest number|most|highest|maximum", question) and re.search(r"different suppliers?|supplier names", question):
        grouped = df.groupby(product_col, dropna=True)[supplier_col].nunique().rename("distinct_suppliers").reset_index()
        grouped = grouped.sort_values(["distinct_suppliers", product_col], ascending=[False, True]).head(top_n(1))
        return result("Product with the greatest supplier variety", grouped.iloc[0]["distinct_suppliers"], UNIT_COUNT,
            "count distinct supplier names per product", table_records(grouped, [product_col, "distinct_suppliers"]))
    if supplier_intent and re.search(r"greatest number|most|highest|maximum|variety", question) and "product" in question and "quantity" not in question:
        grouped = df.groupby(supplier_col, dropna=True)[product_col].nunique().rename("distinct_products").reset_index()
        grouped = grouped.sort_values(["distinct_products", supplier_col], ascending=[False, True]).head(top_n(5 if "suppliers" in question else 1))
        return result("Suppliers by product variety", grouped.iloc[0]["distinct_products"], UNIT_COUNT,
            "count distinct products per supplier name", table_records(grouped, [supplier_col, "distinct_products"]))
    if "product" in question and "compare" not in question and ("different suppliers" in question or "multiple suppliers" in question or "more than one supplier" in question):
        grouped = df.groupby(product_col, dropna=True)[supplier_col].nunique().rename("distinct_suppliers").reset_index()
        grouped = grouped[grouped["distinct_suppliers"] > 1].sort_values(["distinct_suppliers", product_col], ascending=[False, True]).head(top_n(10))
        product_count = int((df.groupby(product_col)[supplier_col].nunique() > 1).sum())
        return result(f"Products supplied by multiple suppliers ({product_count} total; top 10 shown)", product_count, UNIT_COUNT,
            "distinct supplier names per product; list highest supplier variety", table_records(grouped, [product_col, "distinct_suppliers"]))
    if "compare" in question and "purchase price" in question and ("multiple suppliers" in question or "more than one supplier" in question or "available from" in question):
        grouped = df.groupby([product_col, supplier_col], dropna=True).agg(
            average_purchase_price=("cost", "mean"), supplier_count=(supplier_col, "nunique")
        ).reset_index()
        variety = grouped.groupby(product_col)[supplier_col].nunique().sort_values(ascending=False)
        selected_products = set(variety.head(top_n(5)).index)
        rows = grouped[grouped[product_col].isin(selected_products)].sort_values([product_col, "average_purchase_price", supplier_col])
        return result("Purchase-price comparison across suppliers", len(selected_products), UNIT_COUNT,
            "mean recorded unit purchase price by product and supplier for products with multiple suppliers", table_records(rows, [product_col, supplier_col, "average_purchase_price"]))

    if re.search(r"product groups?|categor(?:y|ies)", question) and re.search(r"expenditure|purchasing|purchase amount|spending", question):
        grouped = df.groupby("category", dropna=True).agg(amount=("amount", "sum")).reset_index().sort_values(["amount", "category"], ascending=[False, True]).head(top_n(5))
        return result("Highest purchase expenditure by product group", grouped.iloc[0]["amount"], UNIT_CURRENCY,
            "sum line amounts by product group", table_records(grouped, ["category", "amount"]))
    if supplier_intent and "average discount" in question:
        grouped = df.groupby(supplier_col, dropna=True)["discount_pct"].mean().rename("average_discount_pct").reset_index().sort_values(["average_discount_pct", supplier_col], ascending=[False, True]).head(top_n(5))
        return result("Suppliers by average discount percentage", grouped.iloc[0]["average_discount_pct"], UNIT_PERCENT,
            "mean row discount percentage grouped by supplier name", table_records(grouped, [supplier_col, "average_discount_pct"]))
    if "discount" in question and "supplier" not in question and ("products" in question or "largest" in question or "highest" in question) and not re.search(r"records?.*(?:have|with).*(?:discount)", question):
        grouped = df.groupby(product_col, dropna=True)["discount_amount"].sum().rename("discount_amount").reset_index().sort_values(["discount_amount", product_col], ascending=[False, True]).head(top_n(5))
        return result("Products with largest total discounts", grouped.iloc[0]["discount_amount"], UNIT_CURRENCY,
            "sum line discount amounts by product", table_records(grouped, [product_col, "discount_amount"]))
    if "bonus" in question and ("frequently" in question or "most often" in question):
        if supplier_intent:
            grouped = df.assign(_bonus=money("bonus_quantity").gt(0)).groupby(supplier_col, dropna=True).agg(
                bonus_records=("_bonus", "sum"), bonus_units=("bonus_quantity", "sum")
            ).reset_index().sort_values(["bonus_records", supplier_col], ascending=[False, True]).head(top_n(5))
            return result("Suppliers by bonus frequency", grouped.iloc[0]["bonus_records"], UNIT_COUNT,
                "count product records with positive bonus; also sum bonus units", table_records(grouped, [supplier_col, "bonus_records", "bonus_units"]))
        grouped = df.assign(_bonus=money("bonus_quantity").gt(0)).groupby(product_col, dropna=True).agg(
            bonus_records=("_bonus", "sum"), bonus_units=("bonus_quantity", "sum")
        ).reset_index().sort_values(["bonus_records", product_col], ascending=[False, True]).head(top_n(5))
        return result("Products by bonus frequency", grouped.iloc[0]["bonus_records"], UNIT_COUNT,
            "count product records with positive bonus; also sum bonus units", table_records(grouped, [product_col, "bonus_records", "bonus_units"]))
    if "month" in question and re.search(r"expenditure|purchasing|spending|purchase amount", question):
        dated = df.assign(_month=pd.to_datetime(df["date"], errors="coerce").dt.to_period("M").astype(str), _amount=money("amount"))
        grouped = dated.groupby("_month", dropna=True)["_amount"].sum().rename("amount").reset_index().rename(columns={"_month": "month"})
        grouped = grouped.sort_values(["amount", "month"], ascending=[False, True]).head(top_n(5))
        return result("Highest monthly purchase expenditure", grouped.iloc[0]["amount"], UNIT_CURRENCY,
            "sum line amounts by invoice month", table_records(grouped, ["month", "amount"]))
    if "2024" in question and "2025" in question and re.search(r"expenditure|purchasing|purchase amount|spending", question):
        dated = df.assign(_year=pd.to_datetime(df["date"], errors="coerce").dt.year)
        grouped = dated[dated["_year"].isin([2024, 2025])].groupby("_year")["amount"].sum().rename("amount").reset_index().rename(columns={"_year": "year"})
        return result("Purchase expenditure comparison by year", grouped["amount"].sum(), UNIT_CURRENCY,
            "sum line amounts by invoice year", table_records(grouped, ["year", "amount"]))

    if re.search(r"approaching.*expir|expiring soon|near(?:ing)? expiry", question):
        as_of = pd.Timestamp.today().normalize()
        expiry = pd.to_datetime(df["expiry_date"], errors="coerce")
        horizon = int(filters.option("expiry_days", 90)) if hasattr(filters, "option") else 90
        mask = expiry.gt(as_of) & expiry.le(as_of + pd.Timedelta(days=horizon))
        frame = df.loc[mask].assign(_expiry=expiry[mask]).sort_values(["_expiry", product_col]).head(top_n(10))
        cols = [c for c in [product_col, "batch_no", "category", "_expiry", supplier_col] if c in frame]
        return result(f"Products expiring within {horizon} days", int(mask.sum()), UNIT_COUNT,
            f"expiry dates after {as_of:%Y-%m-%d} and within {horizon} days", table_records(frame, cols), mask=mask)
    if supplier_intent and "expired" in question and re.search(r"quantit|qty|units?", question):
        cutoff = pd.Timestamp.today().normalize()
        expiry = pd.to_datetime(df["expiry_date"], errors="coerce")
        expired = df[expiry.le(cutoff)].copy()
        grouped = expired.assign(_qty=pd.to_numeric(expired["quantity"], errors="coerce")).groupby(supplier_col, dropna=True)["_qty"].sum().rename("expired_quantity").reset_index().sort_values(["expired_quantity", supplier_col], ascending=[False, True]).head(top_n(5))
        return result("Supplier with highest expired-record quantity", grouped.iloc[0]["expired_quantity"], "units",
            f"sum purchased quantity where expiry_date is on or before {cutoff:%Y-%m-%d}", table_records(grouped, [supplier_col, "expired_quantity"]))
    if "batch" in question and ("same medicine" in question or "different expiry" in question):
        frame = df.assign(_expiry=pd.to_datetime(df["expiry_date"], errors="coerce"))
        multiple = frame.groupby(product_col)["_expiry"].nunique()
        selected = set(multiple[multiple > 1].index)
        rows = frame[frame[product_col].isin(selected)].drop_duplicates([product_col, "batch_no", "_expiry"]).sort_values([product_col, "_expiry", "batch_no"]).head(top_n(10))
        return result("Batches with differing expiry dates", len(rows), UNIT_COUNT,
            "list batch and expiry date for medicines with multiple expiry dates", table_records(rows, [product_col, "batch_no", "_expiry"]))
    if "product group" in question and "expired" in question:
        cutoff = pd.Timestamp.today().normalize()
        expiry = pd.to_datetime(df["expiry_date"], errors="coerce")
        expired = df.loc[expiry.le(cutoff)].copy()
        grouped = expired.groupby("category", dropna=True)["batch_no"].nunique().rename("expired_batches").reset_index().sort_values(["expired_batches", "category"], ascending=[False, True])
        return result("Expired batches by product group", grouped["expired_batches"].sum(), UNIT_COUNT,
            f"count distinct batches by group expired on or before {cutoff:%Y-%m-%d}", table_records(grouped, ["category", "expired_batches"]))

    if supplier_intent and "highest total payment" in question:
        invoices = df.drop_duplicates(tx_col) if tx_col in df else df
        grouped = invoices.groupby(supplier_col, dropna=True)["net_payable"].sum().rename("net_payable").reset_index().sort_values(["net_payable", supplier_col], ascending=[False, True]).head(1)
        return result("Highest supplier net payable (not payment history)", grouped.iloc[0]["net_payable"], UNIT_CURRENCY,
            "sum invoice net payable once per transaction; actual payments are not recorded", table_records(grouped, [supplier_col, "net_payable"]))
    if "invoice" in question and ("top 10" in question or "top ten" in question):
        invoices = df.drop_duplicates(tx_col) if tx_col in df else df
        grouped = invoices.sort_values(["net_payable", tx_col], ascending=[False, True]).head(10)
        return result("Top invoices by net payable (10 shown)", pd.to_numeric(grouped["net_payable"], errors="coerce").iloc[0], UNIT_CURRENCY,
            "invoice totals deduplicated by transaction; ordered by net payable", table_records(grouped, [tx_col, "invoice_id", "supplier_name", "net_payable"]))

    if "why" in question and "purchase" in question:
        return insufficient("Purchase change cause", "Monthly expenditure can be compared, but this dataset contains no recorded reasons for increases or decreases.")

    requested_tx = transaction_rows()
    if requested_tx is not None and requested_tx.empty:
        tx_match = re.search(r"\b(?:transaction|txn)\s*(?:no\.?|number|#)?\s*(\d{3,})\b", question)
        return unavailable("pharmacy_purchase_analysis", "Transaction lookup", UNIT_COUNT,
            "exact transaction-number match", f"transaction {tx_match.group(1)} was not found in the selected dataset" if tx_match else "no matching transaction was found",
            build_provenance(df, df.index.to_series().isin([]), filters, [], []))

    # Exact entity lookups fail closed when the requested identifier is absent.
    if "product code" in question:
        code = re.search(r"\bproduct code\s+([a-z0-9_-]+)\b", question)
        if code:
            codes = df["product_code"] if "product_code" in df else pd.Series(index=df.index, dtype=object)
            rows = df[codes.astype(str).str.casefold() == code.group(1).casefold()]
            if rows.empty:
                return unavailable("pharmacy_purchase_analysis", "Purchase quantity", UNIT_COUNT,
                    "sum quantity for exact product code", f"product code {code.group(1)} was not found in the selected dataset", build_provenance(df, df.index.to_series().isin([]), filters, [], []))
            return result("Total purchased quantity for product code", pd.to_numeric(rows["quantity"], errors="coerce").sum(), UNIT_COUNT,
                "sum quantity for exact product code", table_records(rows.head(1), ["product_code", product_col]), mask=df.index.isin(rows.index))
    if re.search(r"\b(do we have|is there|does the dataset have)\b", question) and "product" in question:
        product = re.search(r"\bproduct\s+(?:called|named)\s+(.+?)[?.!]*$", question)
        if product and product_col in df:
            asked = product.group(1).strip().strip("\"'")
            rows = df[df[product_col].astype(str).str.casefold() == asked.casefold()]
            if rows.empty:
                return unavailable("pharmacy_purchase_analysis", "Product existence", UNIT_COUNT,
                    "exact product-name match", f"no exact product named {asked} exists in the selected dataset", build_provenance(df, df.index.to_series().isin([]), filters, [], []))
            return result("Exact product match", 1, UNIT_COUNT, "exact product-name match", table_records(rows.head(1), [product_col, "product_code"]), mask=df.index.isin(rows.index))
    invoice_match = re.search(r"\binvoice\s+([a-z0-9_-]*\d[a-z0-9_-]*)\b", question)
    if invoice_match and re.search(r"net payable|payable|amount|value", question):
        rows = df[df["invoice_id"].astype(str).str.casefold() == invoice_match.group(1).casefold()] if "invoice_id" in df else df.iloc[0:0]
        if rows.empty:
            return unavailable("pharmacy_purchase_analysis", "Invoice net payable", UNIT_CURRENCY,
                "exact invoice-number match", f"invoice {invoice_match.group(1)} was not found in the selected dataset", build_provenance(df, df.index.to_series().isin([]), filters, [], []))
        record = rows.drop_duplicates(tx_col).head(1)
        return result("Invoice net payable", pd.to_numeric(record["net_payable"], errors="coerce").iloc[0], UNIT_CURRENCY,
            "exact invoice-number lookup", table_records(record, [tx_col, "invoice_id", "supplier_name", "net_payable"]), mask=df.index.isin(rows.index))

    # Simple dataset questions and row-level payment type counts.
    if re.search(r"expired (?:by|as of)", question):
        expiry = pd.to_datetime(df["expiry_date"], errors="coerce")
        ref = re.search(r"(\d{4}-\d{2}-\d{2})", question)
        if not ref:
            ref = re.search(r"\b([a-z]+\s+\d{1,2},?\s+\d{4})\b", question)
        cutoff = pd.to_datetime(ref.group(1).replace(",", ""), errors="coerce") if ref else pd.Timestamp("2025-12-31")
        if pd.isna(cutoff):
            cutoff = pd.Timestamp("2025-12-31")
        count = int((expiry <= cutoff).sum())
        return result("Product records expired by reference date", count, UNIT_COUNT, f"expiry_date on or before {cutoff:%Y-%m-%d}", mask=expiry.le(cutoff))
    if "earliest-expiring" in question or "earliest expiring" in question:
        earliest = df.assign(_expiry=pd.to_datetime(df["expiry_date"], errors="coerce")).dropna(subset=["_expiry"]).sort_values(["_expiry", product_col]).head(top_n(5))
        cols = [c for c in [product_col, "batch_no", "_expiry"] if c in earliest]
        return result("Earliest-expiring products", len(earliest), breakdown=table_records(earliest, cols))
    if re.search(r"\b(rows?|records?)\b", question) and re.search(r"\b(cash|credit)\b", question):
        label = "credit" if "credit" in question else "cash"
        col = "payment_method" if "payment_method" in df else "txn_type"
        matching = df[col].astype(str).str.casefold().eq(label) if col in df else all_rows
        return result("Matching transaction rows", int(matching.sum()), UNIT_COUNT, f"count rows whose {col} is {label.title()}", mask=matching)
    if re.search(r"how many rows?|count of rows?|row count|rows? in (?:the )?dataset|records? in (?:the )?dataset|dataset size", question):
        return result("Dataset rows", len(df), UNIT_COUNT, "count of selected data rows")
    if re.search(r"unique\s+(transactions?|invoices?)|distinct\s+(transactions?|invoices?)", question):
        col = tx_col if "transaction" in question and tx_col in df else "invoice_id"
        return result("Unique transactions / invoices", df[col].nunique() if col in df else 0, formula=f"distinct {col}")
    if re.search(r"unique\s+products?|distinct\s+products?", question):
        col = "product_code" if "product_code" in df else product_col
        return result("Unique products", df[col].nunique() if col in df else 0, formula=f"distinct {col}")
    if re.search(r"unique\s+supplier|distinct\s+supplier", question):
        return result("Unique supplier IDs", df["supplier_id"].nunique() if "supplier_id" in df else 0, formula="distinct supplier_id")
    if re.search(r"invoices? (?:were )?recorded", question) and len(set(re.findall(r"\b202\d\b", question))) > 1:
        yearly = df.assign(_year=pd.to_datetime(df["date"], errors="coerce").dt.year)
        grouped = yearly.groupby("_year").agg(invoices=(tx_col, "nunique")).reset_index().rename(columns={"_year": "year"})
        return result("Invoices by year", grouped["invoices"].sum(), breakdown=table_records(grouped, ["year", "invoices"]))
    if re.search(r"how many invoices|number of invoices", question) and "supplier" in question:
        supp = supplier_rows()
        if supp is not None:
            return result("Invoices for supplier", supp[tx_col].nunique() if tx_col in supp else len(supp), formula="distinct transactions grouped by supplier name", mask=df.index.isin(supp.index))
    if "earliest" in question and "date" in question or "latest" in question and "date" in question:
        dates = pd.to_datetime(df["date"], errors="coerce") if "date" in df else pd.Series(dtype="datetime64[ns]")
        if dates.dropna().empty:
            return unavailable("pharmacy_purchase_analysis", "Invoice date", "date", "minimum/maximum invoice date", "no valid invoice dates", build_provenance(df, df.index.to_series().isin([]), filters, [], []))
        date_value = dates.min() if "earliest" in question else dates.max()
        return result("Earliest invoice date" if "earliest" in question else "Latest invoice date", date_value.strftime("%Y-%m-%d"), "date", "minimum/maximum invoice date")
    if "transaction types" in question:
        col = "payment_method" if "payment_method" in df else "txn_type"
        vals = sorted(df[col].dropna().astype(str).unique()) if col in df else []
        return result("Transaction types", len(vals), UNIT_COUNT, "distinct transaction type labels", [{"type": v} for v in vals])
    if re.search(r"product groups?\b", question):
        vals = sorted(df["category"].dropna().astype(str).unique()) if "category" in df else []
        if re.search(r"\b(what are|what is|list|show|name)\b", question) and not re.search(r"\b(amount|margin|records|how many|highest|top)\b", question):
            return result("Product groups", len(vals), UNIT_COUNT, "distinct product groups", [{"group": v} for v in vals])
        if "how many" in question or "records belong" in question:
            grouped = df.groupby("category", dropna=True).size().rename("records").reset_index().sort_values("records", ascending=False)
            return result("Records by product group", len(df), UNIT_COUNT, "count product records by group", table_records(grouped, ["category", "records"]))

    # Invoice-level summaries must deduplicate repeated totals by transaction.
    if "net payable" in question and re.search(r"highest|lowest|largest|smallest", question) and "transaction" in question:
        invoices = df.drop_duplicates(tx_col) if tx_col in df else df
        ascending = not bool(re.search(r"highest|largest", question))
        best = invoices.sort_values("net_payable", ascending=ascending).head(1)
        cols = [c for c in [tx_col, "invoice_id", "supplier_name", "net_payable"] if c in best]
        return result("Transaction with highest/lowest net payable", best.iloc[0]["net_payable"], UNIT_CURRENCY, "rank invoice totals once per transaction", table_records(best, cols))

    tx_rows = transaction_rows()
    if tx_rows is not None and not tx_rows.empty and re.search(
        r"\b(products?|supplier|net payable|invoice|calculate|list|amount|quantity|qty)\b", question
    ):
        tx_total = tx_rows.drop_duplicates(tx_col).iloc[0]
        cols = [c for c in ["product_id", "quantity", "amount", "tax_pct", "margin_pct", "bonus_quantity"] if c in tx_rows]
        lines = table_records(tx_rows, cols)
        summary = {
            "transaction_id": tx_total.get(tx_col),
            "invoice_id": tx_total.get("invoice_id"),
            "supplier_name": tx_total.get("supplier_name"),
            "total_quantity": pd.to_numeric(tx_rows["quantity"], errors="coerce").sum(),
            "total_product_amount": pd.to_numeric(tx_rows["amount"], errors="coerce").sum(),
            "total_bonus": pd.to_numeric(tx_rows["bonus_quantity"], errors="coerce").sum(),
            "net_payable": tx_total.get("net_payable"),
        }
        lines.append(summary)
        return result("Transaction products and totals", summary["net_payable"], UNIT_CURRENCY, "sum line values and read invoice total once", lines, mask=df.index.isin(tx_rows.index))

    if "individual product record" in question or "largest discount amount" in question or ("highest amount" in question and "record" in question):
        column = "discount_amount" if "discount" in question else "amount"
        row = df.loc[pd.to_numeric(df[column], errors="coerce").idxmax()]
        fields = [c for c in ["product_id", tx_col, "invoice_id", "supplier_name", "quantity", "amount", "discount_pct", "discount_amount"] if c in df]
        return result("Largest product record", row[column], UNIT_CURRENCY, f"maximum row-level {column}", table_records(df.loc[[row.name]], fields), mask=df.index == row.name)

    if re.search(r"net payable|payable amount|invoice value|total purchase amount across invoices", question):
        if "net_payable" not in df:
            return unavailable("pharmacy_purchase_analysis", "Net payable", UNIT_CURRENCY, "distinct invoice net payable", "net payable is not present in this dataset", build_provenance(df, all_rows, filters, [], []))
        invoice_frame = df.drop_duplicates(tx_col) if tx_col in df else df
        net = pd.to_numeric(invoice_frame["net_payable"], errors="coerce")
        supp = supplier_rows()
        if supp is not None:
            invoice_frame = supp.drop_duplicates(tx_col) if tx_col in supp else supp
            net = pd.to_numeric(invoice_frame["net_payable"], errors="coerce")
            if re.search(r"how many invoices|number of invoices", question):
                return result("Invoices for supplier", invoice_frame[tx_col].nunique() if tx_col in invoice_frame else len(invoice_frame), formula="distinct transactions for supplier")
            return result("Total net payable", net.sum(), UNIT_CURRENCY, "sum net_payable once per transaction", mask=df.index.isin(supp.index))
        if "highest" in question or "largest" in question or "which supplier" in question:
            grouped = invoice_frame.groupby(supplier_col, dropna=True).agg(
                net_payable=("net_payable", "sum"), invoices=(tx_col, "nunique")
            ).reset_index().sort_values(["net_payable", supplier_col], ascending=[False, True]).head(top_n(1)) if supplier_col in invoice_frame and tx_col in invoice_frame else pd.DataFrame()
            if "number of invoices" in question or "how many invoices" in question or "and how many" in question:
                return result("Supplier with highest net payable", grouped.iloc[0]["net_payable"], UNIT_CURRENCY, "invoice-level net payable grouped by supplier name", table_records(grouped, [supplier_col, "invoices", "net_payable"]))
            return result("Supplier net payable", grouped.iloc[0]["net_payable"], UNIT_CURRENCY, "invoice-level net payable grouped by supplier name", table_records(grouped, [supplier_col, "net_payable"]))

    # Exact category and supplier breakdowns.
    if "highest number of invoices" in question or "most invoices" in question:
        invoice_frame = df.drop_duplicates(tx_col) if tx_col in df else df
        grouped = invoice_frame.groupby(supplier_col, dropna=True)[tx_col].nunique().rename("invoices").reset_index().sort_values(["invoices", supplier_col], ascending=[False, True]).head(top_n(1))
        return result("Supplier invoice counts", grouped.iloc[0]["invoices"], breakdown=table_records(grouped, [supplier_col, "invoices"]))
    if "supplier" in question and ("amount" in question or "expenditure" in question or "purchasing" in question) and ("total" in question or "highest" in question or "investigate" in question):
        invoice_frame = df.drop_duplicates(tx_col) if tx_col in df else df
        grouped = invoice_frame.groupby(supplier_col, dropna=True).agg(net_payable=("net_payable", "sum"), invoices=(tx_col, "nunique")).reset_index().sort_values(["net_payable", supplier_col], ascending=[False, True]).head(top_n(1))
        return result("Supplier net payable", grouped.iloc[0]["net_payable"], UNIT_CURRENCY, "invoice net payable grouped by supplier", table_records(grouped, [supplier_col, "invoices", "net_payable"]))
    if re.search(r"\b(by|per)\s+(product group|group|category)\b|each product group|which product group|amount for (?:the )?.*group", question):
        if "average margin" in question or "highest average margin" in question:
            grouped = df.groupby("category", dropna=True)["margin_pct"].mean().rename("average_margin_pct").reset_index().sort_values(["average_margin_pct", "category"], ascending=[False, True])
            grouped["average_margin_pct"] = grouped["average_margin_pct"].round(2)
            return result("Average margin by product group", grouped.iloc[0]["average_margin_pct"], UNIT_PERCENT, "mean row margin percentage grouped by product group", table_records(grouped, ["category", "average_margin_pct"]))
        grouped = df.groupby("category", dropna=True).agg(records=("category", "size"), amount=("amount", "sum")).reset_index()
        if "how many" in question or "records" in question:
            return result("Product records by group", grouped["records"].sum(), breakdown=table_records(grouped.sort_values(["records", "category"], ascending=[False, True]), ["category", "records"]))
        name = next((str(v) for v in grouped["category"] if str(v).casefold() in question), None)
        if name:
            row = grouped[grouped["category"] == name].iloc[0]
            return result(f"Purchase amount for {name}", row["amount"], UNIT_CURRENCY, "sum product amount in product group")

    if "compare" in question and "cash" in question and "credit" in question:
        payment = "payment_method" if "payment_method" in df else "txn_type"
        grouped = df.groupby(payment, dropna=True).agg(rows=(payment, "size"), amount=("amount", "sum")).reset_index()
        return result("Cash and Credit purchase comparison", grouped["amount"].sum(), UNIT_CURRENCY, "line amount grouped by payment type", table_records(grouped, [payment, "rows", "amount"]))
    if "bonus" in question:
        return result("Total bonus quantity", money("bonus_quantity").sum(), "units", "sum line bonus quantities")
    if "average margin" in question and not re.search(r"which product|top\s+\d+|products?\b|product group", question):
        return result("Average margin percentage", money("margin_pct").mean(), UNIT_PERCENT, "arithmetic mean of row margin percentages")
    if "total purchase amount" in question and re.search(r"\b202\d\b", question):
        year = int(re.search(r"\b(202\d)\b", question).group(1))
        year_mask = pd.to_datetime(df["date"], errors="coerce").dt.year.eq(year)
        return result(f"Purchase amount for {year}", money("amount")[year_mask].sum(), UNIT_CURRENCY, "sum line amounts for calendar year", mask=year_mask)
    if "total amount" in question and ("across all" in question or "all product records" in question):
        return result("Total product amount", money("amount").sum(), UNIT_CURRENCY, "sum line product amounts")
    if ("total quantity purchased" in question or ("quantity" in question and "across all product records" in question)) and not re.search(r"\b(top|highest|most|which product)\b", question):
        return result("Total quantity purchased", money("quantity").sum(), "units", "sum line quantities")

    # Product quantity, purchase value and margin rankings.
    if product_col in df and ("quantit" in question or "qty" in question or "units" in question or "margin" in question or "purchase amount" in question):
        if "margin" in question:
            margins = money("margin_pct")
            work = df.assign(_margin=margins)
            grouped = work.groupby(product_col, dropna=True).agg(
                average_margin_pct=("_margin", "mean"), quantity=("quantity", "sum"), amount=("amount", "sum")
            ).reset_index()
            threshold_query = bool(re.search(r"above\s+40\s*%|over\s+40\s*%|above\s+40 percent", question))
            if threshold_query:
                grouped = grouped[grouped["average_margin_pct"] > 40]
            qualifying_products = len(grouped)
            grouped = grouped.sort_values(["average_margin_pct", product_col], ascending=[False, True])
            if not threshold_query:
                grouped = grouped.head(ranking_limit())
            grouped["average_margin_pct"] = grouped["average_margin_pct"].round(2)
            if "above 40" in question or "over 40" in question:
                return result("Products with margins above 40%", qualifying_products, UNIT_COUNT, "count all products whose mean row margin exceeds 40 percent; list the highest-margin matches", table_records(grouped, [product_col, "average_margin_pct"]))
            return result("Products by average margin", grouped.iloc[0]["average_margin_pct"] if not grouped.empty else 0, UNIT_PERCENT, "mean row margin percentage by product", table_records(grouped, [product_col, "average_margin_pct"]))
        if "purchase amount" in question or "amount" in question:
            grouped = df.groupby(product_col, dropna=True)["amount"].sum().rename("amount").reset_index().sort_values(["amount", product_col], ascending=[False, True]).head(ranking_limit())
            return result("Products by total purchase amount", grouped.iloc[0]["amount"], UNIT_CURRENCY, "sum line Amount by product", table_records(grouped, [product_col, "amount"]))
        wanted = next((str(v) for v in df[product_col].dropna().unique() if str(v).casefold() in question), None)
        if wanted and "total" in question:
            rows = df[df[product_col].astype(str).str.casefold() == wanted.casefold()]
            qty = pd.to_numeric(rows["quantity"], errors="coerce").sum()
            return result(f"Total purchased quantity for {wanted}", qty, "units", "sum line quantities for product", mask=df.index.isin(rows.index))
        grouped = df.groupby(product_col, dropna=True).agg(quantity=("quantity", "sum"), amount=("amount", "sum")).reset_index().sort_values(["quantity", product_col], ascending=[False, True]).head(ranking_limit())
        return result("Products by purchased quantity", grouped.iloc[0]["quantity"], "units", "sum line quantities by product", table_records(grouped, [product_col, "quantity", "amount"]))

    # Invoice-specific combined detail asks.
    tx_rows = transaction_rows()
    if tx_rows is not None and not tx_rows.empty and ("calculate" in question or "list each product" in question or "net payable" in question):
        tx_total = tx_rows.drop_duplicates(tx_col).iloc[0]
        cols = [c for c in ["product_id", "quantity", "amount", "tax_pct", "margin_pct", "bonus_quantity"] if c in tx_rows]
        lines = table_records(tx_rows, cols)
        summary = [{
            "transaction_id": tx_total.get(tx_col),
            "total_quantity": pd.to_numeric(tx_rows.get("quantity"), errors="coerce").sum(),
            "total_product_amount": pd.to_numeric(tx_rows.get("amount"), errors="coerce").sum(),
            "total_bonus": pd.to_numeric(tx_rows.get("bonus_quantity"), errors="coerce").sum(),
            "net_payable": tx_total.get("net_payable"),
        }]
        return result("Transaction line items and totals", summary[0]["net_payable"], UNIT_CURRENCY, "line sums plus invoice-level net payable", lines + summary, mask=df.index.isin(tx_rows.index))

    # Dataset-wide scalar sums and means.
    if "gst" in question or "tax amount" in question:
        col = "tax_amount" if "tax_amount" in df else "tax"
        return result("Total GST amount", money(col).sum(), UNIT_CURRENCY, f"sum of {col}")
    if "discount" in question and "amount" in question:
        col = "discount_amount" if "discount_amount" in df else "discount"
        return result("Total discount amount", money(col).sum(), UNIT_CURRENCY, f"sum of {col}")
    if "bonus" in question:
        return result("Total bonus quantity", money("bonus_quantity").sum(), "units", "sum line bonus quantities")
    if "average margin" in question or "margin percentage across" in question:
        return result("Average margin percentage", money("margin_pct").mean(), UNIT_PERCENT, "arithmetic mean of row margin percentages")
    if "quantity" in question and ("purchased" in question or "across all" in question or "total" in question) and not re.search(r"\b(top|highest|most|which product)\b", question):
        return result("Total quantity purchased", money("quantity").sum(), "units", "sum line quantities")
    if "amount" in question and ("total" in question or "across all" in question):
        col = "amount"
        return result("Total product amount", money(col).sum(), UNIT_CURRENCY, "sum of line product amounts")

    return unavailable(
        "pharmacy_purchase_analysis", "Purchase analysis", UNIT_COUNT,
        "data-dependent pharmacy purchase analysis", "this question does not match a supported purchase analysis", build_provenance(df, all_rows, filters, [], []),
    )
