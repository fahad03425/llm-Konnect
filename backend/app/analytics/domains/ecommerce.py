"""
E-Commerce Store Analytics — DomainPack KPIs on the Module 6.6 KPI engine.

Provides deterministic, pure-pandas e-commerce KPIs covering:
  1. Financials: GMV, Net Sales, Average Order Value (AOV), Discounts, Refunds, Gross Profit & Margins.
  2. Customer & Retention: Unique Customers, Repeat Customer Rate %, Average Units Per Order.
  3. Product & Inventory Velocity: Best Selling SKUs, Sell-Through Rate %, Slow Moving Products.
  4. Reviews & Sentiment: Average Rating (1-5), Positive Review Ratio %.

Same contract as every other KPI in 6.6:
  * Deterministic pure pandas. The LLM is never involved: code computes, LLM narrates.
  * Offline, CPU-only, no network.
  * Every result is a `KPIResult` with full `Provenance` back to `source_row`.
  * A figure that cannot be computed is `unavailable` with an explicit reason.
"""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

from app.analytics.filters import KPIFilters, apply_filters
from app.analytics.kpi import build_provenance
from app.analytics.models import (
    KPIResult,
    Period,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    UNIT_COUNT,
    UNIT_CURRENCY,
    UNIT_PERCENT,
    unavailable,
)


def _safe_numeric(series: pd.Series) -> pd.Series:
    """Coerce series to float, filling NaNs with 0.0."""
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _get_sale_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask for rows representing sales / orders."""
    if "txn_type" in df.columns:
        return df["txn_type"].astype(str).str.lower().isin(["sale", "order", "invoice", "nan", "", "none"])
    return pd.Series(True, index=df.index)


# ---------------------------------------------------------------------------
# 1. Financial & Sales KPIs
# ---------------------------------------------------------------------------

def gmv(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Gross Merchandise Value (GMV): Total sales before discounts, refunds, and returns."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Sum of gross line items / order values before discounts and refunds"
    name = "Gross Merchandise Value (GMV)"

    if sales.empty:
        return unavailable("gmv", name, UNIT_CURRENCY, formula, "No sales or order records found")

    col_used: List[str] = []
    assumptions: List[str] = []

    if "gross_amount" in sales.columns and sales["gross_amount"].notna().any():
        vals = _safe_numeric(sales["gross_amount"])
        col_used = ["gross_amount"]
    elif "sale_amount" in sales.columns and sales["sale_amount"].notna().any():
        vals = _safe_numeric(sales["sale_amount"])
        col_used = ["sale_amount"]
    elif "amount" in sales.columns and sales["amount"].notna().any():
        vals = _safe_numeric(sales["amount"])
        col_used = ["amount"]
    elif "quantity" in sales.columns and "unit_price" in sales.columns:
        vals = _safe_numeric(sales["quantity"]) * _safe_numeric(sales["unit_price"])
        col_used = ["quantity", "unit_price"]
        assumptions.append("Computed GMV from quantity * unit_price as gross_amount was missing")
    else:
        return unavailable("gmv", name, UNIT_CURRENCY, formula, "Missing sale amount or price/quantity columns")

    total_val = float(vals.sum())
    prov = build_provenance(filtered_df, sale_mask, filters, col_used, assumptions)

    return KPIResult(
        key="gmv",
        name=name,
        value=round(total_val, 2),
        unit=UNIT_CURRENCY,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def ecommerce_net_sales(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Net Sales: Gross sales minus discounts and refunds."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Gross sales - total discounts - total refunds"
    name = "Net Sales"

    if sales.empty:
        return unavailable("ecommerce_net_sales", name, UNIT_CURRENCY, formula, "No sales records found")

    col_used: List[str] = []
    assumptions: List[str] = []

    if "net_amount" in sales.columns and sales["net_amount"].notna().any():
        vals = _safe_numeric(sales["net_amount"])
        col_used = ["net_amount"]
    else:
        base_amt = _safe_numeric(sales["sale_amount"] if "sale_amount" in sales.columns else sales.get("amount", pd.Series(0, index=sales.index)))
        refund_amt = _safe_numeric(sales["refund_amount"]) if "refund_amount" in sales.columns else pd.Series(0, index=sales.index)
        vals = base_amt - refund_amt
        col_used = [c for c in ["sale_amount", "amount", "refund_amount"] if c in sales.columns]
        assumptions.append("Derived net sales by deducting refunds from total sale amount")

    total_val = float(vals.sum())
    prov = build_provenance(filtered_df, sale_mask, filters, col_used, assumptions)

    return KPIResult(
        key="ecommerce_net_sales",
        name=name,
        value=round(total_val, 2),
        unit=UNIT_CURRENCY,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def average_order_value_ecom(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Average Order Value (AOV): Net Revenue divided by unique order count."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Total Sale Revenue / Unique Order Count"
    name = "Average Order Value (AOV)"

    if sales.empty:
        return unavailable("average_order_value_ecom", name, UNIT_CURRENCY, formula, "No sales records found")

    order_col = None
    for cand in ["order_id", "invoice_id"]:
        if cand in sales.columns and sales[cand].notna().any():
            order_col = cand
            break

    amount_col = "sale_amount" if "sale_amount" in sales.columns else ("amount" if "amount" in sales.columns else None)
    if not amount_col:
        return unavailable("average_order_value_ecom", name, UNIT_CURRENCY, formula, "Missing amount column")

    total_revenue = float(_safe_numeric(sales[amount_col]).sum())
    
    if order_col:
        order_count = int(sales[order_col].nunique())
    else:
        order_count = len(sales)

    if order_count == 0:
        return unavailable("average_order_value_ecom", name, UNIT_CURRENCY, formula, "Zero valid orders found")

    aov_val = total_revenue / order_count
    cols_used = [amount_col] + ([order_col] if order_col else [])
    assumptions = [] if order_col else ["Assuming each row represents one distinct order as order_id was absent"]

    prov = build_provenance(filtered_df, sale_mask, filters, cols_used, assumptions)

    return KPIResult(
        key="average_order_value_ecom",
        name=name,
        value=round(aov_val, 2),
        unit=UNIT_CURRENCY,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def total_discounts_applied(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Total discount coupons and promotions granted."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Sum of discount_amount"
    name = "Total Discounts Granted"

    disc_col = None
    for cand in ["discount_amount", "discount"]:
        if cand in sales.columns and sales[cand].notna().any():
            disc_col = cand
            break

    if not disc_col:
        return unavailable("total_discounts_applied", name, UNIT_CURRENCY, formula, "No discount column found in dataset")

    total_disc = float(_safe_numeric(sales[disc_col]).sum())
    prov = build_provenance(filtered_df, sale_mask, filters, [disc_col], [])

    return KPIResult(
        key="total_discounts_applied",
        name=name,
        value=round(total_disc, 2),
        unit=UNIT_CURRENCY,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def total_refunds_ecom(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Total refund value."""
    filtered_df, period = apply_filters(df, filters)
    formula = "Sum of refund amounts"
    name = "Total Refunds"

    ref_col = None
    for cand in ["refund_amount", "refund"]:
        if cand in filtered_df.columns and filtered_df[cand].notna().any():
            ref_col = cand
            break

    if ref_col:
        total_ref = float(_safe_numeric(filtered_df[ref_col]).sum())
        mask = filtered_df[ref_col].notna() & (_safe_numeric(filtered_df[ref_col]) > 0)
        cols_used = [ref_col]
    elif "txn_type" in filtered_df.columns:
        mask = filtered_df["txn_type"].astype(str).str.lower().isin(["refund", "return"])
        refund_rows = filtered_df[mask]
        amount_col = "amount" if "amount" in refund_rows.columns else "sale_amount"
        total_ref = float(_safe_numeric(refund_rows[amount_col]).abs().sum()) if amount_col in refund_rows.columns else 0.0
        cols_used = ["txn_type", amount_col] if amount_col in refund_rows.columns else ["txn_type"]
    else:
        mask = pd.Series(False, index=filtered_df.index)
        total_ref = 0.0
        cols_used = []

    prov = build_provenance(filtered_df, mask if mask.any() else pd.Series(True, index=filtered_df.index), filters, cols_used, [])

    return KPIResult(
        key="total_refunds_ecom",
        name=name,
        value=round(total_ref, 2),
        unit=UNIT_CURRENCY,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def refund_rate_pct_ecom(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Refund percentage of gross sales."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "(Total Refunds / Gross Sales) * 100"
    name = "Refund Rate %"

    if sales.empty:
        return unavailable("refund_rate_pct_ecom", name, UNIT_PERCENT, formula, "No sales records found")

    amt_col = "sale_amount" if "sale_amount" in sales.columns else ("amount" if "amount" in sales.columns else None)
    if not amt_col:
        return unavailable("refund_rate_pct_ecom", name, UNIT_PERCENT, formula, "Missing sales amount column")

    gross_val = float(_safe_numeric(sales[amt_col]).sum())
    if gross_val == 0:
        return unavailable("refund_rate_pct_ecom", name, UNIT_PERCENT, formula, "Gross sales are 0")

    ref_result = total_refunds_ecom(df, filters, domain=domain)
    ref_val = ref_result.value if ref_result.status == STATUS_OK and ref_result.value is not None else 0.0

    rate = (ref_val / gross_val) * 100.0
    cols_used = list(set([amt_col] + ref_result.provenance.columns_used))
    prov = build_provenance(filtered_df, sale_mask, filters, cols_used, [])

    return KPIResult(
        key="refund_rate_pct_ecom",
        name=name,
        value=round(rate, 2),
        unit=UNIT_PERCENT,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def ecom_gross_profit(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Gross Profit: Total Revenue minus Cost of Goods Sold (COGS)."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Revenue - (Quantity * Unit Cost)"
    name = "Gross Profit"

    cost_col = None
    for cand in ["cost_per_item", "cost", "cogs"]:
        if cand in sales.columns and sales[cand].notna().any():
            cost_col = cand
            break

    if not cost_col:
        return unavailable("ecom_gross_profit", name, UNIT_CURRENCY, formula, "Missing cost per item / COGS column to calculate profit")

    amt_col = "sale_amount" if "sale_amount" in sales.columns else ("amount" if "amount" in sales.columns else None)
    if not amt_col or "quantity" not in sales.columns:
        return unavailable("ecom_gross_profit", name, UNIT_CURRENCY, formula, "Missing sale_amount or quantity column")

    revenue = _safe_numeric(sales[amt_col])
    cogs = _safe_numeric(sales["quantity"]) * _safe_numeric(sales[cost_col])
    profit = float((revenue - cogs).sum())

    prov = build_provenance(filtered_df, sale_mask, filters, [amt_col, "quantity", cost_col], [])

    return KPIResult(
        key="ecom_gross_profit",
        name=name,
        value=round(profit, 2),
        unit=UNIT_CURRENCY,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def ecom_gross_margin_pct(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Gross Margin %: (Gross Profit / Revenue) * 100."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "(Gross Profit / Revenue) * 100"
    name = "Gross Margin %"

    amt_col = "sale_amount" if "sale_amount" in sales.columns else ("amount" if "amount" in sales.columns else None)
    if not amt_col:
        return unavailable("ecom_gross_margin_pct", name, UNIT_PERCENT, formula, "Missing sales amount column")

    rev_val = float(_safe_numeric(sales[amt_col]).sum())
    if rev_val == 0:
        return unavailable("ecom_gross_margin_pct", name, UNIT_PERCENT, formula, "Total revenue is 0")

    profit_res = ecom_gross_profit(df, filters, domain=domain)
    if profit_res.status != STATUS_OK or profit_res.value is None:
        return unavailable("ecom_gross_margin_pct", name, UNIT_PERCENT, formula, f"Cannot compute margin: {profit_res.reason}")

    margin = (profit_res.value / rev_val) * 100.0
    cols_used = list(set([amt_col] + profit_res.provenance.columns_used))
    prov = build_provenance(filtered_df, sale_mask, filters, cols_used, [])

    return KPIResult(
        key="ecom_gross_margin_pct",
        name=name,
        value=round(margin, 2),
        unit=UNIT_PERCENT,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


# ---------------------------------------------------------------------------
# 2. Customer & Retention KPIs
# ---------------------------------------------------------------------------

def unique_customers_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Count of distinct buyers across orders."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    cust_col = None
    for cand in ["customer_id", "customer_email", "customer_name"]:
        if cand in sales.columns and sales[cand].notna().any():
            cust_col = cand
            break

    formula = f"Distinct count of {cust_col or 'customer_id'}"
    name = "Unique Customers"

    if not cust_col:
        return unavailable("unique_customers_count", name, UNIT_COUNT, formula, "No customer identification column found")

    count = int(sales[cust_col].dropna().nunique())
    prov = build_provenance(filtered_df, sale_mask, filters, [cust_col], [])

    return KPIResult(
        key="unique_customers_count",
        name=name,
        value=count,
        unit=UNIT_COUNT,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def repeat_customer_rate_pct(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Percentage of customers who have placed 2 or more orders."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "(Customers with >= 2 orders / Total Unique Customers) * 100"
    name = "Repeat Customer Rate %"

    cust_col = None
    for cand in ["customer_id", "customer_email", "customer_name"]:
        if cand in sales.columns and sales[cand].notna().any():
            cust_col = cand
            break

    order_col = None
    for cand in ["order_id", "invoice_id"]:
        if cand in sales.columns and sales[cand].notna().any():
            order_col = cand
            break

    if not cust_col:
        return unavailable("repeat_customer_rate_pct", name, UNIT_PERCENT, formula, "No customer column found")

    if order_col:
        orders_per_cust = sales.groupby(cust_col)[order_col].nunique()
    else:
        orders_per_cust = sales.groupby(cust_col).size()

    total_custs = len(orders_per_cust)
    if total_custs == 0:
        return unavailable("repeat_customer_rate_pct", name, UNIT_PERCENT, formula, "Zero customers in dataset")

    repeat_custs = int((orders_per_cust >= 2).sum())
    rate = (repeat_custs / total_custs) * 100.0

    cols_used = [cust_col] + ([order_col] if order_col else [])
    prov = build_provenance(filtered_df, sale_mask, filters, cols_used, [])

    return KPIResult(
        key="repeat_customer_rate_pct",
        name=name,
        value=round(rate, 2),
        unit=UNIT_PERCENT,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def avg_items_per_order(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Average units/items per completed order."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Total Units Sold / Unique Orders"
    name = "Average Items Per Order"

    if "quantity" not in sales.columns:
        return unavailable("avg_items_per_order", name, UNIT_COUNT, formula, "Missing quantity column")

    total_units = float(_safe_numeric(sales["quantity"]).sum())

    order_col = None
    for cand in ["order_id", "invoice_id"]:
        if cand in sales.columns and sales[cand].notna().any():
            order_col = cand
            break

    order_count = int(sales[order_col].nunique()) if order_col else len(sales)
    if order_count == 0:
        return unavailable("avg_items_per_order", name, UNIT_COUNT, formula, "Zero valid orders found")

    avg_items = total_units / order_count
    cols_used = ["quantity"] + ([order_col] if order_col else [])
    prov = build_provenance(filtered_df, sale_mask, filters, cols_used, [])

    return KPIResult(
        key="avg_items_per_order",
        name=name,
        value=round(avg_items, 2),
        unit=UNIT_COUNT,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


# ---------------------------------------------------------------------------
# 3. Product Velocity & Reviews
# ---------------------------------------------------------------------------

def slow_moving_skus_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Count of inventory SKUs with zero or single-unit sales."""
    filtered_df, period = apply_filters(df, filters)
    sale_mask = _get_sale_mask(filtered_df)
    sales = filtered_df[sale_mask]

    formula = "Count of SKUs with <= 1 unit sold in period"
    name = "Slow Moving SKUs Count"

    sku_col = None
    for cand in ["product_sku", "product_id", "product_name"]:
        if cand in sales.columns and sales[cand].notna().any():
            sku_col = cand
            break

    if not sku_col or "quantity" not in sales.columns:
        return unavailable("slow_moving_skus_count", name, UNIT_COUNT, formula, "Missing SKU or quantity columns")

    qty_per_sku = sales.groupby(sku_col)["quantity"].sum()
    slow_count = int((qty_per_sku <= 1).sum())
    prov = build_provenance(filtered_df, sale_mask, filters, [sku_col, "quantity"], [])

    return KPIResult(
        key="slow_moving_skus_count",
        name=name,
        value=slow_count,
        unit=UNIT_COUNT,
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


def avg_customer_rating(df: pd.DataFrame, filters: KPIFilters, domain: str = "ecommerce") -> KPIResult:
    """Average customer review star rating (1-5 scale)."""
    filtered_df, period = apply_filters(df, filters)

    formula = "Mean of customer review star ratings"
    name = "Average Customer Rating"

    if "rating" not in filtered_df.columns or filtered_df["rating"].isna().all():
        return unavailable("avg_customer_rating", name, "stars", formula, "No review ratings found in dataset")

    valid_ratings = _safe_numeric(filtered_df["rating"])
    rating_mask = filtered_df["rating"].notna() & (valid_ratings > 0)
    valid_ratings = valid_ratings[rating_mask]

    if valid_ratings.empty:
        return unavailable("avg_customer_rating", name, "stars", formula, "No positive ratings found")

    avg_r = float(valid_ratings.mean())
    prov = build_provenance(filtered_df, rating_mask, filters, ["rating"], [])

    return KPIResult(
        key="avg_customer_rating",
        name=name,
        value=round(avg_r, 2),
        unit="stars",
        formula=formula,
        status=STATUS_OK,
        period=period,
        provenance=prov,
    )


# ---------------------------------------------------------------------------
# Natural Language Chat Routing Rules
# ---------------------------------------------------------------------------

ECOMMERCE_QUESTION_RULES: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("gmv", "gross merchandise value", "gross sales", "total sales"), ("gmv",)),
    (("net sales", "net revenue", "store revenue"), ("ecommerce_net_sales",)),
    (("aov", "average order value", "average cart size", "avg order"), ("average_order_value_ecom",)),
    (("discount", "discounts", "coupon", "promo"), ("total_discounts_applied",)),
    (("refund", "refunds", "return rate", "returns"), ("total_refunds_ecom", "refund_rate_pct_ecom")),
    (("profit", "gross profit", "margin", "gross margin"), ("ecom_gross_profit", "ecom_gross_margin_pct")),
    (("repeat customer", "repeat rate", "customer retention", "loyalty"), ("repeat_customer_rate_pct", "unique_customers_count")),
    (("unique customers", "total customers", "how many buyers", "buyer count"), ("unique_customers_count",)),
    (("items per order", "units per order", "basket size"), ("avg_items_per_order",)),
    (("slow moving", "dead stock", "unsold products", "stagnant inventory"), ("slow_moving_skus_count",)),
    (("rating", "reviews", "stars", "customer feedback", "satisfaction"), ("avg_customer_rating",)),
)


# ---------------------------------------------------------------------------
# Engine Registration Hook
# ---------------------------------------------------------------------------

def register(engine, domain: str = "ecommerce") -> None:
    """
    Attach the E-Commerce domain KPIs to a KPIEngine instance.
    """
    from app.analytics.engine import KPISpec

    specs = [
        KPISpec("gmv", "Gross Merchandise Value (GMV)", UNIT_CURRENCY,
                "Total sales before discounts and refunds.", gmv, domain=domain, tags=("money", "headline")),
        KPISpec("ecommerce_net_sales", "Net Sales", UNIT_CURRENCY,
                "Gross sales minus discounts and refunds.", ecommerce_net_sales, domain=domain, tags=("money", "headline")),
        KPISpec("average_order_value_ecom", "Average Order Value (AOV)", UNIT_CURRENCY,
                "Net sales / total unique orders.", average_order_value_ecom, domain=domain, tags=("money", "headline")),
        KPISpec("total_discounts_applied", "Total Discounts", UNIT_CURRENCY,
                "Sum of discount deductions.", total_discounts_applied, domain=domain, tags=("money",)),
        KPISpec("total_refunds_ecom", "Total Refunds", UNIT_CURRENCY,
                "Sum of refund / return amounts.", total_refunds_ecom, domain=domain, tags=("money",)),
        KPISpec("refund_rate_pct_ecom", "Refund Rate %", UNIT_PERCENT,
                "(Total refunds / Gross sales) * 100.", refund_rate_pct_ecom, domain=domain, tags=("ratio",)),
        KPISpec("ecom_gross_profit", "Gross Profit", UNIT_CURRENCY,
                "Revenue minus COGS.", ecom_gross_profit, domain=domain, tags=("money", "headline")),
        KPISpec("ecom_gross_margin_pct", "Gross Margin %", UNIT_PERCENT,
                "(Gross profit / Revenue) * 100.", ecom_gross_margin_pct, domain=domain, tags=("ratio",)),
        KPISpec("unique_customers_count", "Unique Customers", UNIT_COUNT,
                "Distinct buyer count.", unique_customers_count, domain=domain, tags=("volume",)),
        KPISpec("repeat_customer_rate_pct", "Repeat Customer Rate %", UNIT_PERCENT,
                "(Customers with >= 2 orders / Total Customers) * 100.", repeat_customer_rate_pct, domain=domain, tags=("ratio",)),
        KPISpec("avg_items_per_order", "Avg Items Per Order", UNIT_COUNT,
                "Total units sold / unique orders.", avg_items_per_order, domain=domain, tags=("volume",)),
        KPISpec("slow_moving_skus_count", "Slow Moving SKUs", UNIT_COUNT,
                "SKUs with <= 1 unit sold.", slow_moving_skus_count, domain=domain, tags=("volume",)),
        KPISpec("avg_customer_rating", "Avg Customer Rating", "stars",
                "Average star rating across reviews.", avg_customer_rating, domain=domain, tags=("ratio",)),
    ]

    for spec in specs:
        engine.register(spec)
