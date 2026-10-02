"""Deterministic analysis for a normalized, multi-table pharmacy POS snapshot.

The POS collection contains separate invoice headers, item lines, batches, and
product masters. This module intentionally chooses the right row grain for each
question so invoice totals are not multiplied by their line count and purchase
flows are never mistaken for stock on hand.
"""
from __future__ import annotations

import itertools
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.filters import KPIFilters
from app.analytics.kpi import build_provenance
from app.analytics.models import KPIResult, UNIT_COUNT, UNIT_CURRENCY, UNIT_PERCENT, unavailable


def is_pos_snapshot(df: pd.DataFrame) -> bool:
    if "table_name" not in df.columns:
        return False
    names = set(df["table_name"].dropna().astype(str).str.casefold())
    return any("salesdetail" in name or "sales detail" in name for name in names) and any(
        "batch" in name or "inventory" in name or "stock" in name for name in names
    )


def _metric(df, key, name, value, unit, formula, mask, filters, breakdown=None,
            breakdown_columns=None, reason=None, assumptions=None):
    if reason:
        reason_mask = pd.Series(mask, index=df.index, dtype=bool).reindex(df.index, fill_value=False)
        return unavailable(key, name, unit, formula, reason,
                           build_provenance(df, reason_mask, filters,
                                            [c for c in df.columns if c in {"date", "invoice_id", "product_id", "batch_no", "amount", "quantity", "cost", "status"}],
                                            assumptions or []))
    mask = pd.Series(mask, index=df.index, dtype=bool).reindex(df.index, fill_value=False)
    provenance = build_provenance(df, mask, filters,
                                  [c for c in df.columns if c in {
                                      "date", "amount", "quantity", "unit_price", "cost", "invoice_id",
                                      "product_id", "product_code", "supplier_name", "expiry_date",
                                      "batch_no", "discount_amount", "tax_amount", "time_of_day",
                                      "reorder_level", "is_cancelled", "status"
                                  }], assumptions or [])
    return KPIResult(key=key, name=name, value=value, unit=unit, formula=formula,
                     provenance=provenance, breakdown=breakdown or [],
                     breakdown_columns=breakdown_columns)


def _subset(df: pd.DataFrame, pattern: str) -> pd.DataFrame:
    if "table_name" not in df:
        return df.iloc[0:0]
    return df[df["table_name"].astype(str).str.contains(pattern, case=False, regex=True, na=False)].copy()


def _dates(df: pd.DataFrame) -> pd.Series:
    if "date" not in df:
        return pd.Series(pd.NaT, index=df.index)
    return pd.to_datetime(df["date"], errors="coerce")


def _period(df: pd.DataFrame, filters: KPIFilters) -> pd.DataFrame:
    dates = _dates(df)
    mask = dates.notna()
    if filters.date_from:
        start = pd.to_datetime(filters.date_from, errors="coerce")
        if pd.notna(start):
            mask &= dates >= start
    if filters.date_to:
        end = pd.to_datetime(filters.date_to, errors="coerce")
        if pd.notna(end):
            mask &= dates < end.normalize() + pd.Timedelta(days=1)
    if filters.month is not None:
        mask &= dates.dt.month == int(filters.month)
    if filters.year is not None:
        mask &= dates.dt.year == int(filters.year)
    return df.loc[mask]


def _products(df: pd.DataFrame) -> List[str]:
    names = []
    for col in ("product_id", "product_code", "medicine_name", "description"):
        if col in df:
            names.extend(df[col].dropna().astype(str).tolist())
    return sorted(set(n.strip() for n in names if n.strip()))


def _find_product(question: str, df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    q = re.sub(r"[^a-z0-9]+", " ", question.casefold()).strip()
    query_tokens = set(q.split())
    candidates = _products(df)
    matches = []
    for name in candidates:
        norm = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
        if norm and norm in q:
            matches.append(name)
            continue
        tokens = norm.split()
        identity_tokens = [t for t in tokens if t.isalpha() and len(t) >= 4
                           and t not in {"tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "syrup", "drop", "drops", "injection", "inj"}]
        dose_tokens = [t for t in tokens if any(ch.isdigit() for ch in t)]
        if identity_tokens and all(t in query_tokens for t in identity_tokens):
            asked_doses = {t for t in query_tokens if any(ch.isdigit() for ch in t)}
            if asked_doses and dose_tokens and not set(dose_tokens).intersection(asked_doses):
                continue
            matches.append(name)
    if matches:
        exact_match = next((m for m in matches if m.casefold() in q), None)
        if exact_match:
            return exact_match, None
        return matches[0], None

    if re.search(r"\b(particular|specific)\s+(?:medicine|medicines|product|products|drug|item)\b", q):
        return None, "Please include the medicine or product name so I can filter its records."
    return None, None


def _find_supplier(question: str, df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    q = re.sub(r"[^a-z0-9]+", " ", question.casefold()).strip()
    candidates = []
    for column in ("supplier_name", "supplier_id"):
        if column in df:
            candidates.extend(df[column].dropna().astype(str).tolist())
    candidates = sorted(set(value.strip() for value in candidates if value.strip()), key=len, reverse=True)
    matches = []
    for name in candidates:
        norm = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
        if len(norm) >= 3 and re.search(rf"\b{re.escape(norm)}\b", q):
            matches.append(name)
    if matches:
        return matches[0], None
    return None, None


def analyze_pos_question(df: pd.DataFrame, question: str, filters: KPIFilters) -> KPIResult:
    """Answer a pharmacy POS question from full normalized table records."""
    q = (question or "").casefold()
    sales = _subset(df, r"sales.*detail|sale.*detail|invoice.*detail|bill.*detail")
    if sales.empty:
        return _metric(df, "pharmacy_pos_analysis", "POS analysis", None, "count", "POS line-level data",
                       pd.Series(False, index=df.index), filters, reason="the selected database has no sales detail rows")
    sales_dates = _dates(sales)
    sales = sales.copy()
    sales["_date"] = sales_dates
    sales["_amount"] = pd.to_numeric(sales.get("amount"), errors="coerce")
    sales["_qty"] = pd.to_numeric(sales.get("quantity"), errors="coerce")
    sales["_cost"] = pd.to_numeric(sales.get("cost"), errors="coerce")
    sales["_product"] = sales.get("product_id", sales.get("product_code", pd.Series("(unknown)", index=sales.index))).fillna("(unknown)").astype(str)
    if "is_cancelled" in sales.columns:
        cancelled = sales["is_cancelled"].astype(str).str.casefold().str.strip().isin({"1", "true", "yes", "y"})
        sales = sales.loc[~cancelled]
        sales_dates = sales["_date"]
    sales = sales[sales["_date"].notna()]
    if not any((filters.date_from, filters.date_to, filters.month, filters.year)) and not sales.empty:
        # An unqualified "sales"/"sales report" question means the most recent
        # complete calendar month represented by this source, not every year of
        # accumulated POS history.
        latest = sales["_date"].max()
        date_filtered = sales.loc[(sales["_date"].dt.year == latest.year) & (sales["_date"].dt.month == latest.month)]
    else:
        date_filtered = _period(sales, filters)

    purchases = _subset(df, r"purchase.*detail|purchase.*item")
    purchases = purchases.copy()
    purchases["_date"] = _dates(purchases)
    purchases["_amount"] = pd.to_numeric(purchases.get("amount"), errors="coerce")
    purchases["_qty"] = pd.to_numeric(purchases.get("quantity"), errors="coerce")
    purchases["_cost"] = pd.to_numeric(purchases.get("cost"), errors="coerce")
    purchases["_product"] = purchases.get("product_id", purchases.get("product_code", pd.Series("(unknown)", index=purchases.index))).fillna("(unknown)").astype(str)

    purchase_headers = _subset(df, r"purchase.*header|purchase_header")
    purchase_headers = purchase_headers.copy()
    purchase_headers["_date"] = _dates(purchase_headers)
    purchase_headers["_amount"] = pd.to_numeric(purchase_headers.get("net_payable", purchase_headers.get("amount")), errors="coerce")

    batch_stock = _subset(df, r"batch")
    batch_stock = batch_stock.copy()
    if "table_name" in batch_stock:
        archived = batch_stock["table_name"].astype(str).str.contains(r"archive|histor(?:y|ical)|old", case=False, regex=True)
        batch_stock = batch_stock.loc[~archived].copy()
    batch_stock["_qty"] = pd.to_numeric(batch_stock.get("quantity"), errors="coerce")
    batch_stock["_cost"] = pd.to_numeric(batch_stock.get("cost"), errors="coerce")
    batch_stock["_mrp"] = pd.to_numeric(batch_stock.get("mrp"), errors="coerce")
    batch_stock["_expiry"] = pd.to_datetime(batch_stock.get("expiry_date"), errors="coerce")
    batch_stock["_product"] = batch_stock.get("product_id", batch_stock.get("product_code", pd.Series("(unknown)", index=batch_stock.index))).fillna("(unknown)").astype(str)

    product_stock = _subset(df, r"product_master|tbl_products|product master")
    if product_stock.empty:
        product_stock = _subset(df, r"inventory|stock")
    stock = product_stock.copy() if not product_stock.empty else batch_stock.copy()
    stock["_qty"] = pd.to_numeric(stock.get("quantity"), errors="coerce")
    stock["_cost"] = pd.to_numeric(stock.get("cost"), errors="coerce")
    stock["_mrp"] = pd.to_numeric(stock.get("mrp"), errors="coerce")
    stock["_expiry"] = pd.to_datetime(stock.get("expiry_date"), errors="coerce")
    stock["_product"] = stock.get("product_id", stock.get("product_code", pd.Series("(unknown)", index=stock.index))).fillna("(unknown)").astype(str)
    if "reorder_level" not in stock:
        stock["reorder_level"] = pd.NA

    product, absent_product = _find_product(question, pd.concat([sales, purchases, stock, batch_stock], ignore_index=True, sort=False))
    supplier, absent_supplier = _find_supplier(question, pd.concat([purchases, purchase_headers], ignore_index=True, sort=False))

    if absent_product:
        return _metric(df, "pharmacy_pos_analysis", "POS analysis", None, "count", "exact product match in selected records",
                       pd.Series(False, index=df.index), filters, reason=absent_product)

    stock_mask = pd.Series(df.index.isin(stock.index), index=df.index)
    all_sales_mask = df.index.isin(sales.index)
    filtered_sales = date_filtered
    mask = df.index.isin(filtered_sales.index)
    amount = filtered_sales["_amount"].fillna(0)
    qty = filtered_sales["_qty"].fillna(0)
    invoice_col = filtered_sales.get("invoice_id", pd.Series(index=filtered_sales.index, dtype=object))
    label = product or "all products"

    period_purchases = _period(purchases, filters)
    period_headers = _period(purchase_headers, filters)

    # Reference today date for expiry and velocity calculations
    today_dt = sales["_date"].max().normalize() if sales["_date"].notna().any() else pd.Timestamp.today().normalize()

    # Single product handlers
    if product:
        prod_sales = sales[sales["_product"].str.casefold() == product.casefold()]
        prod_filtered_sales = date_filtered[date_filtered.get("product_id", pd.Series(index=date_filtered.index, dtype=str)).astype(str).str.casefold() == product.casefold()]
        prod_purchases = purchases[purchases["_product"].str.casefold() == product.casefold()]
        prod_stock = stock[stock["_product"].str.casefold() == product.casefold()]
        prod_batch = batch_stock[batch_stock["_product"].str.casefold() == product.casefold()]

        if re.search(r"\b(batch|batches|expire|expiry|miyad|meyad)\b", q):
            b_rows = prod_batch.sort_values("_expiry") if not prod_batch.empty else prod_stock.sort_values("_expiry")
            if not b_rows.empty:
                rows = [{"product": product, "batch_no": str(r.get("batch_no")), "expiry_date": r["_expiry"].strftime("%Y-%m-%d") if pd.notna(r["_expiry"]) else "N/A", "quantity": float(r["_qty"])} for _, r in b_rows.iterrows()]
                val = rows[0]["expiry_date"] if "expire" in q or "expiry" in q else len(rows)
                unit = "date" if "expire" in q or "expiry" in q else UNIT_COUNT
                return _metric(df, "pharmacy_pos_analysis", f"Batches for {product}", val, unit, "batches for the matched product", stock_mask, filters, rows, ["product", "batch_no", "expiry_date", "quantity"])

        if re.search(r"\b(supplier|suppliers|vendor|vendors)\b", q):
            p_sup = prod_purchases.loc[prod_purchases.get("supplier_name").notna()] if not prod_purchases.empty and "supplier_name" in prod_purchases else pd.DataFrame()
            if not p_sup.empty:
                latest_p = p_sup.sort_values("_date", ascending=False).head(1)
                row = latest_p.iloc[0]
                rows = [{"product": product, "supplier_name": str(row.get("supplier_name")), "last_purchase_date": row["_date"].strftime("%Y-%m-%d") if pd.notna(row["_date"]) else "N/A", "amount": float(row["_amount"]) if pd.notna(row["_amount"]) else 0}]
                return _metric(df, "pharmacy_pos_analysis", f"Supplier for {product}", str(row.get("supplier_name")), "supplier", "latest purchase supplier for the product", pd.Series(df.index.isin(latest_p.index), index=df.index), filters, rows, ["product", "supplier_name", "last_purchase_date", "amount"])

        if re.search(r"\b(last time|last purchase|last buy|rate|price|cost|qeemat|khareedi|khareeda|mangwai)\b", q) and not re.search(r"\b(sale|sales|sold|revenue|bikri|bika|biki|bikin)\b", q):
            if not prod_purchases.empty:
                latest_p = prod_purchases.sort_values("_date", ascending=False).head(1)
                row = latest_p.iloc[0]
                unit_p = float(row["_amount"]) / float(row["_qty"]) if float(row.get("_qty", 0)) > 0 else float(row.get("_cost", 0))
                rows = [{"product": product, "date": row["_date"].strftime("%Y-%m-%d") if pd.notna(row["_date"]) else "N/A", "quantity": float(row.get("_qty", 0)), "unit_price": round(unit_p, 2), "amount": float(row.get("_amount", 0)), "supplier": str(row.get("supplier_name", ""))}]
                val = round(unit_p, 2) if "price" in q or "rate" in q or "cost" in q or "qeemat" in q or "how much" in q else rows[0]["date"]
                unit = UNIT_CURRENCY if "price" in q or "rate" in q or "cost" in q or "qeemat" in q or "how much" in q else "date"
                return _metric(df, "pharmacy_pos_analysis", f"Latest purchase for {product}", val, unit, "latest purchase line for the product", pd.Series(df.index.isin(latest_p.index), index=df.index), filters, rows, ["product", "date", "quantity", "unit_price", "amount", "supplier"])

        if re.search(r"\b(stock|quantity|units|kitni|kitna|kitne|pari|parri|bachi|available|remaining|chalega|chalay ga)\b", q) and not re.search(r"\b(sale|sales|sold|revenue|bikri|farokht|kamai|bika|biki|bikin)\b", q):
            stock_qty = float(prod_stock["_qty"].sum()) if not prod_stock.empty else 0.0
            if "chalega" in q or "days" in q or "din" in q:
                recent_sales_qty = float(prod_sales.loc[prod_sales["_date"] >= (sales["_date"].max() - pd.Timedelta(days=29)), "_qty"].sum()) if not prod_sales.empty else 0.0
                daily_vel = recent_sales_qty / 30.0 if recent_sales_qty > 0 else 1.0
                days_left = round(stock_qty / daily_vel, 1)
                rows = [{"product": product, "stock_units": stock_qty, "daily_sales": round(daily_vel, 2), "days_cover": days_left}]
                return _metric(df, "pharmacy_pos_analysis", f"Stock cover for {product}", days_left, "days", "on-hand stock divided by 30-day average daily sales", stock_mask, filters, rows, ["product", "stock_units", "daily_sales", "days_cover"])
            rows = [{"product": product, "stock_units": stock_qty}]
            return _metric(df, "pharmacy_pos_analysis", f"Current stock of {product}", stock_qty, UNIT_COUNT, "sum of on-hand units for the matched product", stock_mask, filters, rows, ["product", "stock_units"])

        if re.search(r"\b(sales?|sold|revenue|kamai|aamdani|farokht|bikri|bika|biki|bikin|sell)\b", q):
            target_sales = prod_filtered_sales if not prod_filtered_sales.empty else prod_sales
            sales_qty = float(target_sales["_qty"].sum()) if not target_sales.empty else 0.0
            sales_rev = float(target_sales["_amount"].sum()) if not target_sales.empty else 0.0
            is_rev = bool(re.search(r"\b(revenue|kamai|aamdani|paisa|value|amount)\b", q))
            val = round(sales_rev, 2) if is_rev else sales_qty
            unit = UNIT_CURRENCY if is_rev else UNIT_COUNT
            rows = [{"product": product, "units_sold": sales_qty, "revenue": round(sales_rev, 2)}]
            return _metric(df, "pharmacy_pos_analysis", f"Sales for {product}", val, unit, "sum of sales for the matched product", mask, filters, rows, ["product", "units_sold", "revenue"])

    if supplier:
        purchases = purchases[purchases.get("supplier_name", pd.Series(index=purchases.index, dtype=str)).fillna("").astype(str).str.casefold() == supplier.casefold()]
        purchase_headers = purchase_headers[purchase_headers.get("supplier_name", pd.Series(index=purchase_headers.index, dtype=str)).fillna("").astype(str).str.casefold() == supplier.casefold()]

    if re.search(r"\b(lead[- ]time|delivery time|how long .*deliver|days? .*deliver|usually take to deliver)\b", q):
        receipt_col = next((name for name in ("received_date", "date_received", "delivery_date", "date_delivered") if name in purchases.columns), None)
        supplier_rows = purchases if supplier else purchases.iloc[0:0]
        if receipt_col is None or supplier_rows.empty:
            return _metric(
                df, "pharmacy_pos_analysis", "Supplier delivery lead time", None, "days",
                "mean calendar days from purchase order to receipt", df.index.isin(supplier_rows.index), filters,
                reason="Supplier lead time cannot be calculated because matched order and receipt dates are not recorded.",
            )
        order_dates = pd.to_datetime(supplier_rows.get("order_date", supplier_rows.get("date")), errors="coerce")
        receipt_dates = pd.to_datetime(supplier_rows[receipt_col], errors="coerce")
        days = (receipt_dates - order_dates).dt.days.dropna()
        if days.empty:
            return _metric(
                df, "pharmacy_pos_analysis", "Supplier delivery lead time", None, "days",
                "mean calendar days from purchase order to receipt", df.index.isin(supplier_rows.index), filters,
                reason="Supplier lead time cannot be calculated because matched order and receipt dates are missing or invalid.",
            )
        rows = supplier_rows.assign(_lead_days=days).dropna(subset=["_lead_days"])
        value = float(rows["_lead_days"].mean())
        return _metric(
            df, "pharmacy_pos_analysis", "Supplier delivery lead time", round(value, 1), "days",
            "mean calendar days from purchase order to receipt", df.index.isin(rows.index), filters,
            [{"supplier": supplier or "all suppliers", "mean_days": round(value, 1), "orders": len(rows)}],
            ["supplier", "mean_days", "orders"],
        )

    # Owner advice and decision support
    advice_request = bool(re.search(
        r"\b(how can|what should|what changes|which .* should|recommend|promot|restock|stop purchasing|"
        r"slow[- ]moving|overstock|not keeping enough|high[- ]demand|biggest problems|what do about|"
        r"barha|barhana|tareeqa|tawajjo|masla|masail|faisla|iqdamat|dastiyabi|behtar|performance|haal|surat-e-haal|loss|nuksan|khatam|phansa|surat|halat|karobar|profitability|order karna|mangwaon|mangwani|foran restock|kami ki wajah)\b", q
    ))
    if advice_request and not re.search(r"\b(total stock|kul stock|fewer than|stock mein kitne pais|stock ki qeemat|stock value|konsi medicines stock mein|highest|lowest)\b", q):
        reference = sales["_date"].max().normalize() if sales["_date"].notna().any() else pd.Timestamp.today().normalize()
        recent_start = reference - pd.Timedelta(days=29)
        prior_start = reference - pd.Timedelta(days=59)
        recent = sales.loc[sales["_date"].between(recent_start, reference, inclusive="both")].copy()
        prior = sales.loc[sales["_date"].between(prior_start, recent_start, inclusive="left")].copy()
        recent_by = recent.groupby("_product").agg(units=("_qty", "sum"), revenue=("_amount", "sum")) if not recent.empty else pd.DataFrame(columns=["units", "revenue"])
        prior_by = prior.groupby("_product").agg(units=("_qty", "sum")) if not prior.empty else pd.DataFrame(columns=["units"])
        demand = recent_by.join(prior_by, how="outer", lsuffix="_recent", rsuffix="_prior").fillna(0)
        if "units_recent" not in demand: demand["units_recent"] = demand.get("units", 0)
        if "units_prior" not in demand: demand["units_prior"] = 0
        if "revenue" not in demand: demand["revenue"] = 0
        stock_by = stock.groupby("_product", dropna=True).agg(stock_units=("_qty", "sum")) if not stock.empty else pd.DataFrame(columns=["stock_units"])
        demand = demand.join(stock_by, how="outer").fillna({"units_recent": 0, "units_prior": 0, "revenue": 0, "stock_units": 0})
        demand["days_cover"] = demand["stock_units"] / (demand["units_recent"] / 30).replace(0, pd.NA)

        rows: List[Dict[str, Any]] = []
        ranked = demand.sort_values(["revenue", "units_recent"], ascending=False).head(10)
        for name, row in ranked.iterrows():
            rows.append({
                "action": "Maintain availability and review stock cover",
                "product": str(name),
                "revenue_30d": round(float(row["revenue"]), 2),
                "units_sold_30d": float(row["units_recent"]),
                "stock_units": float(row["stock_units"]),
            })
        return _metric(df, "pharmacy_pos_analysis", "Owner recommendations & priority actions", len(rows), UNIT_COUNT,
                       "actionable recommendations grounded in sales velocity and current stock", all_sales_mask, filters, rows,
                       ["action", "product", "revenue_30d", "units_sold_30d", "stock_units"])

    # Average Daily Sales
    if re.search(r"\b(average daily sale|average daily sales|ausatan|daily average|rozana ausatan|average sale)\b", q):
        last = sales["_date"].max().normalize()
        first = last.replace(day=1)
        days = max(1, (last - first).days + 1)
        daily_total = float(filtered_sales["_amount"].fillna(0).sum()) if not filtered_sales.empty else float(sales["_amount"].fillna(0).sum())
        return _metric(df, "pharmacy_pos_analysis", "Average daily sales", round(daily_total / days, 2), UNIT_CURRENCY,
                       "sales revenue divided by calendar days", mask, filters)

    # Sales Comparison & Growth
    if "compare" in q or "farq" in q or "percentage" in q or "growth" in q or "barhi" in q or "kam hui" in q or "izafa" in q:
        today = sales["_date"].max().normalize() if sales["_date"].notna().any() else pd.Timestamp.today().normalize()
        this_start = today.replace(day=1)
        last_end = this_start - pd.Timedelta(days=1)
        last_start = last_end.replace(day=1)
        current = float(sales.loc[sales["_date"] >= this_start, "_amount"].sum())
        previous = float(sales.loc[(sales["_date"] >= last_start) & (sales["_date"] <= last_end), "_amount"].sum())
        change = current - previous
        pct = round((change / previous * 100), 2) if previous > 0 else 0.0
        rows = [
            {"period": last_start.strftime("%B %Y"), "revenue": round(previous, 2)},
            {"period": this_start.strftime("%B %Y"), "revenue": round(current, 2)},
            {"period": "Change", "revenue": round(change, 2), "change_pct": pct}
        ]
        val = pct if "percent" in q or "feesad" in q else round(change, 2)
        unit = UNIT_PERCENT if "percent" in q or "feesad" in q else UNIT_CURRENCY
        return _metric(df, "pharmacy_pos_analysis", "Sales comparison and growth", val, unit, "current period sales compared with previous period", mask, filters, rows, ["period", "revenue", "change_pct"])

    # Sales Trend
    if "trend" in q or "recent sales" in q or "haal" in q:
        daily = filtered_sales.groupby(filtered_sales["_date"].dt.strftime("%Y-%m-%d"))["_amount"].sum().sort_index()
        rows = [{"date": d, "revenue": round(float(v), 2)} for d, v in daily.items()]
        return _metric(df, "pharmacy_pos_analysis", "Daily sales trend", round(float(daily.sum()), 2), UNIT_CURRENCY,
                       "daily sales revenue", mask, filters, rows, ["date", "revenue"])

    # Invoices and Bills
    if re.search(r"\b(bill|bills|invoice|invoices|parchi)\b", q):
        if re.search(r"\b(how many|count|kitne|bane|banay)\b", q):
            count = int(invoice_col.dropna().astype(str).nunique())
            return _metric(df, "pharmacy_pos_analysis", "Sales invoices count", count, UNIT_COUNT, "distinct invoice count", mask, filters, [{"invoice_count": count}], ["invoice_count"])
        if re.search(r"\b(highest|largest|bara|sab se bara|max)\b", q):
            grouped = filtered_sales.groupby("invoice_id", as_index=False)["_amount"].sum().sort_values("_amount", ascending=False)
            val = round(float(grouped.iloc[0]["_amount"]), 2) if not grouped.empty else 0.0
            rows = [{"invoice_id": str(grouped.iloc[0]["invoice_id"]), "amount": val}] if not grouped.empty else []
            return _metric(df, "pharmacy_pos_analysis", "Highest-value sales invoice", val, UNIT_CURRENCY, "largest invoice amount", mask, filters, rows, ["invoice_id", "amount"])
        if re.search(r"\b(lowest|smallest|choti|sab se choti|min)\b", q):
            grouped = filtered_sales.groupby("invoice_id", as_index=False)["_amount"].sum().sort_values("_amount", ascending=True)
            val = round(float(grouped.iloc[0]["_amount"]), 2) if not grouped.empty else 0.0
            rows = [{"invoice_id": str(grouped.iloc[0]["invoice_id"]), "amount": val}] if not grouped.empty else []
            return _metric(df, "pharmacy_pos_analysis", "Lowest-value sales invoice", val, UNIT_CURRENCY, "smallest invoice amount", mask, filters, rows, ["invoice_id", "amount"])

    # Highest / Lowest Sales Day & Hour
    if re.search(r"\b(highest.*day|lowest.*day|sab se zyada.*din|sab se kam.*din|achi sale.*din|kis din)\b", q):
        daily = filtered_sales.groupby(filtered_sales["_date"].dt.strftime("%Y-%m-%d"))["_amount"].sum().sort_values(ascending="kam" not in q and "lowest" not in q)
        top = daily.head(1)
        rows = [{"date": d, "amount": round(float(v), 2)} for d, v in top.items()]
        val = rows[0]["amount"] if rows else 0
        return _metric(df, "pharmacy_pos_analysis", "Daily sales peak/trough", val, UNIT_CURRENCY, "daily sales grouped by day", mask, filters, rows, ["date", "amount"])

    if re.search(r"\b(hour|time|kab|kis waqt)\b", q):
        times = filtered_sales["time_of_day"] if "time_of_day" in filtered_sales else filtered_sales["date"]
        parsed = pd.to_datetime(times.astype(str), errors="coerce")
        grouped = pd.DataFrame({"hour": parsed.dt.hour, "amount": filtered_sales["_amount"]}).dropna().groupby("hour")["amount"].sum().sort_values(ascending=False).head(1)
        rows = [{"hour": f"{int(h):02d}:00", "amount": round(float(v), 2)} for h, v in grouped.items()]
        val = rows[0]["amount"] if rows else 0.0
        return _metric(df, "pharmacy_pos_analysis", "Highest-sales hour", val, UNIT_CURRENCY, "sales amount grouped by hour", mask, filters, rows, ["hour", "amount"])

    # Expiry Handlers (expired, near expiry, 30/60/90 days, multiple batches, FEFO)
    if re.search(r"\b(expire|expiry|expired|expiring|qareeb-ul-expiry|near expiry|khatre|meyad|miyad)\b", q):
        exp_source = batch_stock if not batch_stock.empty else stock
        if re.search(r"\b(already expired|ho chuki|ho gai|expired stock|expired medicine|expired medicines)\b", q):
            exp_items = exp_source.loc[exp_source["_expiry"] < today_dt].copy()
            rows = [{"product": str(r["_product"]), "batch_no": str(r.get("batch_no", "N/A")), "expiry_date": r["_expiry"].strftime("%Y-%m-%d") if pd.notna(r["_expiry"]) else "N/A", "quantity": float(r["_qty"])} for _, r in exp_items.head(20).iterrows()]
            val = len(rows)
            return _metric(df, "pharmacy_pos_analysis", "Expired medicines in stock", val, UNIT_COUNT, "batches with expiry date prior to current date", stock_mask, filters, rows, ["product", "batch_no", "expiry_date", "quantity"])

        # Specific horizon (30, 60, 90 days)
        h_days = 30
        if "60" in q: h_days = 60
        elif "90" in q: h_days = 90
        elif "30" in q: h_days = 30

        near_exp = exp_source.loc[(exp_source["_expiry"] >= today_dt) & (exp_source["_expiry"] <= today_dt + pd.Timedelta(days=h_days))].sort_values("_expiry")
        rows = [{"product": str(r["_product"]), "batch_no": str(r.get("batch_no", "N/A")), "expiry_date": r["_expiry"].strftime("%Y-%m-%d") if pd.notna(r["_expiry"]) else "N/A", "quantity": float(r["_qty"])} for _, r in near_exp.head(20).iterrows()]
        val = len(rows)
        return _metric(df, "pharmacy_pos_analysis", f"Medicines expiring in {h_days} days", val, UNIT_COUNT, f"batches expiring within {h_days} days", stock_mask, filters, rows, ["product", "batch_no", "expiry_date", "quantity"])

    # Batch specific questions
    if re.search(r"\b(multiple batches|pehle expire|earliest expire|sab se pehle|pehle sell|kis batch)\b", q):
        exp_source = batch_stock if not batch_stock.empty else stock
        if "multiple" in q:
            grouped = exp_source.groupby("_product")["batch_no"].nunique()
            multi = grouped[grouped > 1].reset_index()
            rows = [{"product": str(r["_product"]), "batch_count": int(r["batch_no"])} for _, r in multi.iterrows()]
            return _metric(df, "pharmacy_pos_analysis", "Medicines with multiple batches", len(rows), UNIT_COUNT, "products with more than one active batch", stock_mask, filters, rows, ["product", "batch_count"])
        earliest = exp_source.loc[exp_source["_expiry"].notna()].sort_values("_expiry").head(1)
        if not earliest.empty:
            r = earliest.iloc[0]
            rows = [{"product": str(r["_product"]), "batch_no": str(r.get("batch_no", "")), "expiry_date": r["_expiry"].strftime("%Y-%m-%d"), "quantity": float(r["_qty"])}]
            return _metric(df, "pharmacy_pos_analysis", "Earliest expiring batch (FEFO)", str(r.get("batch_no", "")), "batch", "earliest expiring batch to sell first", stock_mask, filters, rows, ["product", "batch_no", "expiry_date", "quantity"])

    # Top / Fast / Slow / Demand Products
    if re.search(r"\b(top|best-selling|fast|slow|chal rahi|demand|paisa aya|revenue|bikne wali|bik rahi|konsi dawa|bilkul nahi bik)\b", q) and not re.search(r"\b(profit|margin|stock|purchases)\b", q):
        grouped = filtered_sales.groupby("_product", dropna=True)["_qty"].sum().sort_values(ascending="slow" not in q and "kam" not in q and "nahi" not in q)
        rev_grouped = filtered_sales.groupby("_product", dropna=True)["_amount"].sum().sort_values(ascending=False)
        limit = 10 if "10" in q else 5
        rows = [{"product": str(p), "units_sold": float(v), "revenue": round(float(rev_grouped.get(p, 0)), 2)} for p, v in grouped.head(limit).items()]
        val = rows[0]["units_sold"] if rows else 0.0
        return _metric(df, "pharmacy_pos_analysis", "Top / fast / slow moving medicines", val, UNIT_COUNT, "product sales ranked by quantity and revenue", mask, filters, rows, ["product", "units_sold", "revenue"])

    # Stock & Inventory Handlers
    if re.search(r"\b(total stock|kul stock|total kitna stock|stock kitna|total medicine units|total stock quantity)\b", q):
        total_units = float(stock["_qty"].sum())
        return _metric(df, "pharmacy_pos_analysis", "Total current stock", total_units, UNIT_COUNT, "sum of on-hand units in stock", stock_mask, filters, [{"total_stock_units": total_units}], ["total_stock_units"])

    if re.search(r"\b(stock value|stock ki qeemat|stock price|stock mein kitne pais|paisa phansa|dead stock|capital tied up|inventory value|selling value|stock.*price|price.*stock)\b", q):
        cost_val = float((stock["_qty"] * stock["_cost"]).sum())
        grouped = stock.groupby("_product").agg(quantity=("_qty", "sum"), cost=("_cost", "mean")).dropna()
        grouped["stock_value"] = grouped["quantity"] * grouped["cost"]
        rows = [{"product": p, "stock_units": float(r["quantity"]), "estimated_value": round(float(r["stock_value"]), 2)} for p, r in grouped.sort_values("stock_value", ascending=False).head(10).iterrows()]
        return _metric(df, "pharmacy_pos_analysis", "Stock value / Capital tied up", round(cost_val, 2), UNIT_CURRENCY, "sum of on-hand quantity multiplied by unit cost", stock_mask, filters, rows, ["product", "stock_units", "estimated_value"])

    if re.search(r"\b(fewer than 10|fewer than 5|10 se kam|5 se kam|low stock|stock kam|out of stock|khatam ho gai)\b", q):
        threshold = 5 if "5" in q else 10
        low_s = stock.groupby("_product").agg(quantity=("_qty", "sum")).reset_index()
        low_s = low_s[low_s["quantity"] < threshold].sort_values("quantity")
        rows = [{"product": str(r["_product"]), "stock_units": float(r["quantity"])} for _, r in low_s.head(20).iterrows()]
        return _metric(df, "pharmacy_pos_analysis", f"Medicines with fewer than {threshold} units in stock", len(rows), UNIT_COUNT, f"products with on-hand quantity below {threshold}", stock_mask, filters, rows, ["product", "stock_units"])

    if re.search(r"\b(overstock|overstocked|zaroorat se zyada|zyada quantity|zyada stock|highest stock|most most units|sab se zyada quantity|sab se zyada stock)\b", q):
        top_s = stock.groupby("_product").agg(quantity=("_qty", "sum")).reset_index().sort_values("quantity", ascending=False)
        rows = [{"product": str(r["_product"]), "stock_units": float(r["quantity"])} for _, r in top_s.head(10).iterrows()]
        val = rows[0]["stock_units"] if rows else 0.0
        return _metric(df, "pharmacy_pos_analysis", "Medicines with highest stock quantity", val, UNIT_COUNT, "products ranked by on-hand quantity", stock_mask, filters, rows, ["product", "stock_units"])

    if re.search(r"\b(how many medicines|total kitni medicines|total medicines|kitni dawaiyan|different products|currently available|stock mein hain|stock in hain)\b", q) and not re.search(r"\b(sold|sale|sales)\b", q):
        distinct_meds = int(stock.loc[stock["_qty"] > 0, "_product"].nunique())
        rows = [{"product": str(p), "stock_units": float(q_val)} for p, q_val in stock.groupby("_product")["_qty"].sum().head(20).items()]
        return _metric(df, "pharmacy_pos_analysis", "Products currently in stock", distinct_meds, UNIT_COUNT, "distinct products with positive on-hand quantity", stock_mask, filters, rows, ["product", "stock_units"])

    if re.search(r"\b(chalega|chalay ga|kitne din ke liye kafi|days of cover)\b", q):
        total_units = float(stock["_qty"].sum())
        total_recent_sales = float(sales.loc[sales["_date"] >= (today_dt - pd.Timedelta(days=29)), "_qty"].sum())
        daily_vel = total_recent_sales / 30.0 if total_recent_sales > 0 else 1.0
        days_left = round(total_units / daily_vel, 1)
        return _metric(df, "pharmacy_pos_analysis", "Overall stock cover (days)", days_left, "days", "total on-hand stock divided by daily sales velocity", stock_mask, filters, [{"total_stock_units": total_units, "days_cover": days_left}], ["total_stock_units", "days_cover"])

    # Purchasing & Supplier Handlers
    if re.search(r"\b(purchase|purchases|purchasing|purchased|khareed|khareeda|khareedi|supplier|suppliers|vendor|vendors|maal)\b", q) and not re.search(r"\b(sale|sales|sold|revenue|bikri)\b", q):
        target_p = period_purchases if not period_purchases.empty else purchases
        target_h = period_headers if not period_headers.empty else purchase_headers

        if re.search(r"\b(stock|inventory|units|quantity|qty|maal)\b", q):
            purchased_units = float(target_p["_qty"].sum()) if not target_p.empty else 0.0
            rows = [{"product": str(r.get("_product", "")), "units_purchased": float(r.get("_qty", 0))}
                    for _, r in target_p.head(20).iterrows()]
            return _metric(df, "pharmacy_pos_analysis", "Units purchased", purchased_units, UNIT_COUNT,
                           "sum of recorded purchase line quantities in period", pd.Series(df.index.isin(target_p.index), index=df.index),
                           filters, rows, ["product", "units_purchased"])

        if re.search(r"\b(highest|top|most|sab se zyada|bara)\b", q) and re.search(r"\b(supplier|suppliers|vendor)\b", q):
            sup_g = target_p.groupby("supplier_name", dropna=True)["_amount"].sum().sort_values(ascending=False)
            rows = [{"supplier_name": str(s), "purchase_amount": round(float(v), 2)} for s, v in sup_g.head(5).items()]
            val = rows[0]["purchase_amount"] if rows else 0.0
            return _metric(df, "pharmacy_pos_analysis", "Top supplier by purchase amount", val, UNIT_CURRENCY, "purchases grouped by supplier", pd.Series(df.index.isin(target_p.index), index=df.index), filters, rows, ["supplier_name", "purchase_amount"])

        if re.search(r"\b(supplier wise|har supplier|kis supplier se)\b", q):
            sup_g = target_p.groupby("supplier_name", dropna=True)["_amount"].sum().sort_values(ascending=False)
            rows = [{"supplier_name": str(s), "purchase_amount": round(float(v), 2)} for s, v in sup_g.items()]
            return _metric(df, "pharmacy_pos_analysis", "Purchases by supplier", round(float(sup_g.sum()), 2), UNIT_CURRENCY, "supplier purchases breakdown", pd.Series(df.index.isin(target_p.index), index=df.index), filters, rows, ["supplier_name", "purchase_amount"])

        if re.search(r"\b(sab se zyada konsi medicine|top medicine khareedi|most purchased)\b", q):
            p_med = target_p.groupby("_product", dropna=True)["_qty"].sum().sort_values(ascending=False)
            rows = [{"product": str(p), "units_purchased": float(v)} for p, v in p_med.head(5).items()]
            val = rows[0]["units_purchased"] if rows else 0.0
            return _metric(df, "pharmacy_pos_analysis", "Most purchased medicine", val, UNIT_COUNT, "purchased units grouped by product", pd.Series(df.index.isin(target_p.index), index=df.index), filters, rows, ["product", "units_purchased"])

        if re.search(r"\b(rate barh|price.*increase|mehngi|cost.*increase|cost.*kam|rate.*izafa)\b", q):
            p_cost = purchases.sort_values("_date").groupby("_product").agg(first_cost=("_cost", "first"), last_cost=("_cost", "last")).reset_index()
            p_cost["diff"] = p_cost["last_cost"] - p_cost["first_cost"]
            inc = p_cost.sort_values("diff", ascending=False).head(5)
            rows = [{"product": str(r["_product"]), "old_price": round(float(r["first_cost"]), 2), "new_price": round(float(r["last_cost"]), 2), "change": round(float(r["diff"]), 2)} for _, r in inc.iterrows()]
            return _metric(df, "pharmacy_pos_analysis", "Purchase price changes and rate increases", len(rows), UNIT_COUNT, "comparison of initial and latest purchase cost", pd.Series(df.index.isin(purchases.index), index=df.index), filters, rows, ["product", "old_price", "new_price", "change"])

        total_p_amt = float(target_h["_amount"].sum()) if not target_h.empty else float(target_p["_amount"].sum())
        return _metric(df, "pharmacy_pos_analysis", "Total purchases", round(total_p_amt, 2), UNIT_CURRENCY, "sum of purchase transactions in period", pd.Series(df.index.isin(target_p.index), index=df.index), filters, [{"total_purchases": round(total_p_amt, 2)}], ["total_purchases"])

    # Profit & Margins
    if re.search(r"\b(profit|munafa|margin|faida)\b", q):
        if not filtered_sales.empty and ("_cost" not in filtered_sales or filtered_sales["_cost"].isna().any()):
            return _metric(
                df, "pharmacy_pos_analysis", "Gross profit and product margins", None, UNIT_CURRENCY,
                "sales revenue minus recorded cost of goods sold", mask, filters,
                reason="Profit cannot be calculated reliably because purchase cost is missing for one or more matching sales rows.",
            )
        total_rev = float(filtered_sales["_amount"].sum())
        total_c = float((filtered_sales["_qty"] * filtered_sales["_cost"]).sum())
        profit = total_rev - total_c
        margin_pct = round((profit / total_rev * 100), 2) if total_rev > 0 else 0.0
        grouped_p = filtered_sales.groupby("_product").agg(rev=("_amount", "sum"), cost=("_cost", "mean"), qty=("_qty", "sum")).reset_index()
        grouped_p["profit"] = grouped_p["rev"] - (grouped_p["qty"] * grouped_p["cost"])
        grouped_p["margin"] = (grouped_p["profit"] / grouped_p["rev"] * 100).fillna(0)
        ranked = grouped_p.sort_values("profit", ascending="kam" not in q and "low" not in q).head(5)
        rows = [{"product": str(r["_product"]), "profit": round(float(r["profit"]), 2), "margin_pct": round(float(r["margin"]), 2)} for _, r in ranked.iterrows()]
        val = round(profit, 2)
        return _metric(df, "pharmacy_pos_analysis", "Gross profit and product margins", val, UNIT_CURRENCY, "sales revenue minus cost of goods sold", mask, filters, rows, ["product", "profit", "margin_pct"])

    # General Sales / Revenue fallback
    total_rev = float(amount.sum())
    total_q = float(qty.sum())
    rows = [{"revenue": round(total_rev, 2), "units_sold": total_q}]
    return _metric(df, "pharmacy_pos_analysis", "Total sales revenue", round(total_rev, 2), UNIT_CURRENCY,
                   "sum of line amount in filtered sales detail records", mask, filters, rows, ["revenue", "units_sold"])
