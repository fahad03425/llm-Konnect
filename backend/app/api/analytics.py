"""
Module 6.6 (KPI Engine) — analytics API routes.

Thin transport layer only: connect a source through the existing
connectors -> schema mapping -> validation pipeline, then hand the canonical
DataFrame to `KPIEngine`. All arithmetic lives in `app.analytics`.

No LLM is involved on this path.
"""

import os
import re
from pathlib import Path
from dataclasses import replace
from datetime import date
from typing import Any, Dict, List, Optional
import pandas as pd

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analytics.engine import engine
from app.analytics.filters import KPIFilters
from app.connectors.base import detect_connector
from app.core.config import settings, get_default_domain
from app.schema.domain import get_domain_pack
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping
from app.schema.validate import validate
from app.ingestion.store import KnowledgeBase
from app.ingestion.registry import file_registry

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class AnalyticsFilters(BaseModel):
    """Filter slice applied before any KPI is computed."""

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    category: Optional[str] = None
    product_id: Optional[str] = None
    supplier_id: Optional[str] = None
    customer_id: Optional[str] = None
    txn_type: Optional[str] = None
    as_of: Optional[str] = Field(
        None,
        description="Reference date KPIs compute 'now' against (YYYY-MM-DD). Defaults to today.",
    )
    options: Optional[Dict[str, Any]] = Field(
        None,
        description="KPI-specific options, e.g. {\"value_basis\": \"mrp\"} for expiry valuation.",
    )


class KPIRequest(BaseModel):
    file_path: str
    domain: str = Field(default_factory=get_default_domain, description="Active business domain")
    mapping: Optional[Dict[str, str]] = None
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    filters: Optional[AnalyticsFilters] = None
    range_preset: Optional[str] = Field(
        None, description="Time window preset: '7d', '28d', '6m', or 'all'."
    )
    include_validation: bool = Field(
        True, description="Include the validation report so a figure can be judged against data quality."
    )


from app.analytics.cache import (
    get_cached_table,
    set_cached_table,
    clear_analytics_cache as _clear_cache,
)

_df_cache: Dict[str, Any] = {}

def clear_analytics_cache(db_name: Optional[str] = None):
    """Purge in-memory DataFrame cache for all or specific database sources."""
    global _df_cache
    _clear_cache(db_name)
    if db_name:
        db_low = db_name.strip().lower()
        keys_to_remove = [k for k in _df_cache if db_low in k.lower()]
        for k in keys_to_remove:
            _df_cache.pop(k, None)
    else:
        _df_cache.clear()

def _build_canonical_database(df: pd.DataFrame) -> pd.DataFrame:
    """
    Decompose multi-table whole-database records into a clean canonical DataFrame.
    Dynamically identifies sales headers vs details, supplier purchases/expenses,
    inventory/stock, and cross-references product catalogs for accurate cost linking.
    Works dynamically across arbitrary database schemas.
    """
    import re

    if "table_name" not in df.columns:
        return df

    # Identically named tables in unrelated databases must never be mixed.
    if "database_name" in df.columns:
        collisions = df.groupby("table_name")["database_name"].nunique()
        if (collisions > 1).any():
            raise ValueError("Tables from different databases share names; select a single database "
                             "or configure explicit relationships before combining them")

    # Database rows can have multiple embedding windows; count each source row
    # once. Conflicting payloads for the same primary key require reconciliation.
    identity = [field for field in ("database_name", "table_name", "row_id") if field in df]
    if "row_id" in identity:
        window_fields = {"chunk_id", "chunk_index", "chunk_count", "ingested_at", "record_json"}
        compare = [field for field in df if field not in window_fields]
        df = df.drop_duplicates(subset=compare)
        identified = df[df["row_id"].notna()]
        if identified.duplicated(subset=identity).any():
            raise ValueError("Conflicting source records share a primary key; re-sync the database")

    # Single-table case: tag purchase/expense/sale tables appropriately
    if df["table_name"].nunique() <= 1:
        if "txn_type" not in df.columns:
            tbl_name = str(df["table_name"].iloc[0]).lower()
            if re.search(r"purchase|expense", tbl_name):
                df = df.copy()
                df["txn_type"] = "expense"
            elif (re.search(r"stock|inventory|batch|product", tbl_name)
                  or ("expiry_date" in df and df["expiry_date"].notna().any())):
                df = df.copy()
                df["txn_type"] = "inventory"
            elif "purchase_order_no" in df and df["purchase_order_no"].notna().any():
                df = df.copy()
                df["txn_type"] = "expense"
            elif re.search(r"sale|order|invoice", tbl_name):
                df = df.copy()
                df["txn_type"] = "sale"
        return df

    tables = {t: df[df["table_name"] == t].copy() for t in df["table_name"].unique()}

    def find_table(pattern: str, exclude: Optional[List[str]] = None):
        matches = [(table, name) for name, table in tables.items()
                   if name not in (exclude or []) and re.search(pattern, name, re.IGNORECASE)]
        if len(matches) > 1:
            raise ValueError("Ambiguous table role: " + ", ".join(name for _, name in matches)
                             + "; configure explicit source relationships")
        return matches[0] if matches else (None, None)

    def _has_valid_col(t_df: Optional[pd.DataFrame], col_name: str) -> bool:
        return t_df is not None and col_name in t_df.columns and t_df[col_name].notna().any()

    def _safe_detail_header_join(detail: pd.DataFrame, header: pd.DataFrame, candidates: List[str], header_fields: List[str]):
        join_key = None
        for key in candidates:
            if _has_valid_col(detail, key) and _has_valid_col(header, key):
                d_vals = set(detail[key].astype(str).dropna())
                h_vals = set(header[key].astype(str).dropna())
                if len(d_vals & h_vals) > 0:
                    join_key = key
                    break

        if not join_key:
            preserved = detail.copy()
            preserved["_join_warning"] = "Header join skipped: no unique shared transaction key with overlap"
            return preserved, None

        # Duplicate payloads are harmless, conflicting transaction headers are
        # not. Never silently choose an arbitrary version of an invoice.
        comparable = list(dict.fromkeys([join_key, *header_fields]))
        comparable = [field for field in comparable if field in header]
        clean_header = header.drop_duplicates(subset=comparable)
        if clean_header[join_key].duplicated().any():
            raise ValueError(f"Conflicting transaction headers for '{join_key}'; reconcile source records")
        detail_keys = set(detail[join_key].dropna().astype(str))
        header_keys = set(clean_header[join_key].dropna().astype(str))
        if not detail_keys.issubset(header_keys):
            raise ValueError(f"Orphan detail records for '{join_key}'; transaction dates cannot be verified")
        selected_fields = [
            field for field in header_fields
            if field == join_key or field not in detail.columns
        ]
        selected_fields = list(dict.fromkeys([join_key, *selected_fields]))
        joined = detail.merge(clean_header[selected_fields], on=join_key, how="left", validate="many_to_one")
        provenance_fields = [field for field in ("source_file", "source_row", "file_id", "table_name") if field in clean_header]
        if provenance_fields:
            header_provenance = clean_header[[join_key, *provenance_fields]].drop_duplicates(subset=[join_key]).rename(
                columns={field: f"_header_{field}" for field in provenance_fields}
            )
            joined = joined.merge(header_provenance, on=join_key, how="left", validate="many_to_one")
        header_amount_fields = {
            "net_payable", "invoice_total", "invoice_tax", "invoice_discount",
            "invoice_tax_pct", "invoice_discount_pct", "discount", "paid_amount",
            "customer_balance", "previous_balance", "supplier_payable_amount",
            "total_qty", "total_items", "total_bonus",
        }
        for field in header_amount_fields.intersection(set(selected_fields) - {join_key}):
            joined.loc[joined[join_key].duplicated(), field] = pd.NA
        return joined, join_key

    # Build master reference lookups across all tables
    prod_map: Dict[str, str] = {}
    reorder_map: Dict[str, float] = {}
    cat_map: Dict[str, str] = {}
    mfg_map: Dict[str, str] = {}
    pack_size_map: Dict[str, float] = {}
    batch_cost_map: Dict[str, float] = {}
    prod_cost_map: Dict[str, float] = {}
    prod_price_map: Dict[str, float] = {}
    vendor_map: Dict[str, str] = {}
    prod_supplier_map: Dict[str, str] = {}

    for name, t_df in tables.items():
        if _has_valid_col(t_df, "product_id") and _has_valid_col(t_df, "product_code"):
            for _, r in t_df.iterrows():
                p_name = str(r.get("product_id", "")).strip()
                p_code = str(r.get("product_code", "")).strip()
                # A source primary key is often preserved as _extra.ID by the
                # schema normalizer when it is not a canonical business field.
                # Keep it as a product lookup key so opaque table layouts can
                # still resolve inventory foreign keys to the catalog name.
                r_id = next((str(r.get(key, "")).strip() for key in
                             ("row_id", "id", "_extra.ID", "_extra.id")
                             if pd.notna(r.get(key)) and str(r.get(key, "")).strip()), "")
                if p_name and p_name.lower() not in ("nan", "none", ""):
                    if r_id: prod_map[r_id] = p_name
                    if p_code: prod_map[p_code.lower()] = p_name
                    if p_code: prod_map[p_code] = p_name

        if _has_valid_col(t_df, "reorder_level"):
            for _, r in t_df.iterrows():
                lvl = pd.to_numeric(r.get("reorder_level"), errors="coerce")
                if pd.notna(lvl):
                    p_name = str(r.get("product_id", "")).strip()
                    p_code = str(r.get("product_code", "")).strip()
                    r_id = str(r.get("row_id", "")).strip()
                    if p_code: reorder_map[p_code.lower()] = float(lvl)
                    if p_name: reorder_map[p_name.lower()] = float(lvl)
                    if r_id: reorder_map[r_id] = float(lvl)

        if _has_valid_col(t_df, "supplier_id") and _has_valid_col(t_df, "product_id") and not _has_valid_col(t_df, "batch_no") and not _has_valid_col(t_df, "amount"):
            for _, r in t_df.iterrows():
                v_name = str(r.get("product_id", "")).strip()
                s_id = str(r.get("supplier_id", "")).strip()
                r_id = str(r.get("row_id", "")).strip()
                if v_name and v_name.lower() not in ("nan", "none", ""):
                    if s_id: vendor_map[s_id] = v_name
                    if r_id: vendor_map[r_id] = v_name

        if _has_valid_col(t_df, "supplier_name") and (_has_valid_col(t_df, "product_id") or _has_valid_col(t_df, "product_code")):
            for _, r in t_df.iterrows():
                s_name = str(r.get("supplier_name", "")).strip()
                if s_name and s_name.lower() not in ("nan", "none", ""):
                    p_code = str(r.get("product_code", "")).strip()
                    p_name = str(r.get("product_id", "")).strip()
                    if p_code: prod_supplier_map[p_code.lower()] = s_name
                    if p_name: prod_supplier_map[p_name.lower()] = s_name

        if _has_valid_col(t_df, "manufacturer"):
            for _, r in t_df.iterrows():
                m_name = str(r.get("manufacturer", "")).strip()
                if m_name and m_name.lower() not in ("nan", "none", ""):
                    p_code = str(r.get("product_code", "")).strip()
                    p_name = str(r.get("product_id", "")).strip()
                    if p_code: mfg_map[p_code.lower()] = m_name
                    if p_name: mfg_map[p_name.lower()] = m_name

        if _has_valid_col(t_df, "category"):
            for _, r in t_df.iterrows():
                c_name = str(r.get("category", "")).strip()
                if c_name and c_name.lower() not in ("nan", "none", ""):
                    p_name = str(r.get("product_id", "")).strip()
                    p_code = str(r.get("product_code", "")).strip()
                    if p_name: cat_map[p_name.lower()] = c_name
                    if p_code: cat_map[p_code.lower()] = c_name

        if _has_valid_col(t_df, "pack_size"):
            for _, r in t_df.iterrows():
                try:
                    psize = float(r.get("pack_size", 1.0))
                    if psize > 0:
                        pc = str(r.get("product_code", "")).strip()
                        pname = str(r.get("product_id", "")).strip()
                        if pc: pack_size_map[pc.lower()] = psize
                        if pname: pack_size_map[pname.lower()] = psize
                except Exception:
                    pass

        if _has_valid_col(t_df, "mrp") or _has_valid_col(t_df, "unit_price"):
            for _, r in t_df.iterrows():
                mrp_val = pd.to_numeric(r.get("mrp") if _has_valid_col(t_df, "mrp") else r.get("unit_price"), errors="coerce")
                if pd.notna(mrp_val) and mrp_val > 0:
                    pc = str(r.get("product_code", "")).strip()
                    pname = str(r.get("product_id", "")).strip()
                    if pc: prod_price_map[pc.lower()] = float(mrp_val)
                    if pname: prod_price_map[pname.lower()] = float(mrp_val)

        if _has_valid_col(t_df, "cost"):
            for _, r in t_df.iterrows():
                try:
                    c = float(r.get("cost", 0.0))
                    if c > 0:
                        b = str(r.get("batch_no", "")).strip()
                        pc = str(r.get("product_code", "")).strip()
                        pname = str(r.get("product_id", "")).strip()
                        psize = pack_size_map.get(pc.lower()) or pack_size_map.get(pname.lower()) or 1.0
                        unit_c = c / psize if psize > 1.0 else c

                        if b and b.lower() not in ("nan", "none", ""):
                            batch_cost_map[b.lower()] = unit_c
                        if pc and pc.lower() not in ("nan", "none", ""):
                            prod_cost_map[pc.lower()] = unit_c
                        if pname and pname.lower() not in ("nan", "none", ""):
                            prod_cost_map[pname.lower()] = unit_c
                except Exception:
                    pass

    # Identify transaction and inventory tables using broad schema patterns
    sales_detail, sd_name = find_table(r"sales.*detail|order.*detail|order.*item|invoice.*detail|line.*item|bill.*detail|pos.*detail|sales_item")
    sales_header, sh_name = find_table(r"sales.*header|order.*header|invoice.*header|bill.*header|pos.*header|sale_header|order_header|order_main|sales_main")
    sales_gen, sg_name = find_table(r"^sales?$|^orders?$|^transactions?$|^bills?$|^invoices?$|^pos$")

    stock_tbl, st_name = find_table(r"batch|inventory|stock|warehouse")

    # Structural fallback for opaque/generic table names (e.g. tbl_1..25)
    if sales_detail is None and sales_gen is None:
        def _score_sd(tname: str, t_df: pd.DataFrame) -> int:
            score = 0
            src = str(t_df["source_file"].iloc[0]).lower() if "source_file" in t_df.columns else ""
            tn = tname.lower()

            # Disqualify purchase/inventory/supplier tables from being customer sales
            if "inventory" in src or "purchase" in src or "stock" in src: return -10000
            if "purchase" in tn or "vendor" in tn or "supplier" in tn: score -= 100
            if _has_valid_col(t_df, "supplier_id") or _has_valid_col(t_df, "vendor_name") or _has_valid_col(t_df, "supplier_name"): score -= 100
            if "_extra.PurchaseDetailID" in t_df.columns or "_extra.PurchaseType" in t_df.columns: score -= 100

            if (_has_valid_col(t_df, "expiry_date")
                    and not any(_has_valid_col(t_df, key) for key in ("transaction_id", "invoice_id", "order_id"))):
                return -10000  # Stock without transaction identity is not a sale.
            if _has_valid_col(t_df, "purchase_order_no"):
                return -10000

            # Detail-specific line item signals
            if "detail" in tn or "item" in tn or "line" in tn: score += 100
            if _has_valid_col(t_df, "product_id") or _has_valid_col(t_df, "product_code"): score += 50
            if _has_valid_col(t_df, "cost"): score += 40
            if _has_valid_col(t_df, "sales_subtotal") or _has_valid_col(t_df, "unit_price") or _has_valid_col(t_df, "mrp"): score += 40
            if _has_valid_col(t_df, "quantity"): score += 30
            if _has_valid_col(t_df, "transaction_id"): score += 20
            
            # Header attributes penalize detail score
            if _has_valid_col(t_df, "customer_id"): score -= 40
            if _has_valid_col(t_df, "payment_method"): score -= 30
            if "header" in tn or "main" in tn: score -= 80

            if "sales" in src or "pos" in src: score += 50
            return score

        sd_candidates = [(_score_sd(n, t), n, t) for n, t in tables.items() if _score_sd(n, t) > 0]
        if sd_candidates:
            sd_candidates.sort(key=lambda x: x[0], reverse=True)
            sales_detail, sd_name = sd_candidates[0][2].copy(), sd_candidates[0][1]

    if sales_header is None and sales_gen is None:
        def _score_sh(tname: str, t_df: pd.DataFrame) -> int:
            score = 0
            src = str(t_df["source_file"].iloc[0]).lower() if "source_file" in t_df.columns else ""
            tn = tname.lower()

            # Disqualify purchase/inventory/supplier tables from being customer sales
            if "inventory" in src or "purchase" in src or "stock" in src: return -10000
            if "purchase" in tn or "vendor" in tn or "supplier" in tn: score -= 100
            if _has_valid_col(t_df, "supplier_id") or _has_valid_col(t_df, "vendor_name") or _has_valid_col(t_df, "supplier_name"): score -= 100
            if "_extra.PurchaseType" in t_df.columns or "_extra.PurchaseDetailID" in t_df.columns: score -= 100

            # Header-specific invoice signals
            if "header" in tn or "sales" in tn or "main" in tn or "order" in tn: score += 100
            if _has_valid_col(t_df, "date"): score += 60
            if _has_valid_col(t_df, "customer_id") or _has_valid_col(t_df, "client_id"): score += 50
            if _has_valid_col(t_df, "payment_method"): score += 40
            if _has_valid_col(t_df, "invoice_total") or _has_valid_col(t_df, "amount"): score += 30

            # Detail attributes penalize header score
            if "detail" in tn or "item" in tn: score -= 80
            if _has_valid_col(t_df, "sales_subtotal"): score -= 30

            if "sales" in src or "pos" in src: score += 50
            return score

        sh_candidates = [(_score_sh(n, t), n, t) for n, t in tables.items() if n != sd_name and _score_sh(n, t) > 0]
        if sh_candidates:
            sh_candidates.sort(key=lambda x: x[0], reverse=True)
            sales_header, sh_name = sh_candidates[0][2].copy(), sh_candidates[0][1]

    if stock_tbl is None:
        for name, t_df in tables.items():
            if name in (sd_name, sh_name):
                continue
            if _has_valid_col(t_df, "expiry_date") or _has_valid_col(t_df, "batch_no") or (_has_valid_col(t_df, "reorder_level") and _has_valid_col(t_df, "quantity")):
                stock_tbl, st_name = t_df.copy(), name
                break

    parts = []

    # 1. Assemble Sales Transactions
    sales = None
    if sales_detail is not None and sales_header is not None:
        join_keys = [
            c for c in ["transaction_id", "invoice_id", "order_id", "sale_id", "bill_id", "bill_no", "header_id", "receipt_id"]
            if _has_valid_col(sales_detail, c) and _has_valid_col(sales_header, c)
            and len(set(sales_detail[c].astype(str).dropna()) & set(sales_header[c].astype(str).dropna())) > 0
        ]
        if not join_keys:
            for d_k in ["transaction_id", "invoice_id", "order_id"]:
                for h_k in ["row_id", "id", "_extra.ID"]:
                    if _has_valid_col(sales_detail, d_k) and _has_valid_col(sales_header, h_k):
                        d_vals = set(sales_detail[d_k].astype(str).dropna())
                        h_vals = set(sales_header[h_k].astype(str).dropna())
                        if len(d_vals & h_vals) > 0:
                            sales_header[d_k] = sales_header[h_k].astype(str)
                            sales_detail[d_k] = sales_detail[d_k].astype(str)
                            join_keys = [d_k]
                            break
                if join_keys:
                    break

        if join_keys:
            if (_has_valid_col(sales_detail, "invoice_id") and _has_valid_col(sales_detail, "product_id")
                    and sales_detail["invoice_id"].fillna("").equals(sales_detail["product_id"].fillna(""))):
                sales_detail = sales_detail.drop(columns=["invoice_id"])
            header_cols = [
                c for c in ["invoice_total", "invoice_id", "invoice_tax", "invoice_discount", "date", "month", "year", "customer_id", "doctor_name", "discount", "client_id", "user_id", "buyer_id", "payment_method", "payment_type", "paid_amount", "customer_balance", "outstanding_balance", "balance_due", "amount_due", "previous_balance", "sales_type", "user_name", "status"]
                if _has_valid_col(sales_header, c)
            ]
            # Keep noncanonical business attributes from the selected header
            # table (for example POS shipping/channel fields) so exact receipt
            # questions can use them after the relational join. Exclude source
            # and embedding metadata; those have dedicated provenance fields.
            header_metadata = {
                "_extra.id", "_extra.source_row", "_extra.source_file", "_extra.file_id",
                "_extra.table_name", "_extra.database_name", "_extra.row_id",
            }
            header_cols.extend(
                column for column in sales_header.columns
                if str(column).casefold().startswith("_extra.")
                and str(column).casefold() not in header_metadata
                and _has_valid_col(sales_header, column)
            )
            clean_sd = sales_detail.drop(
                columns=[c for c in header_cols if c in sales_detail.columns and not sales_detail[c].notna().any()]
            )
            sales, join_key = _safe_detail_header_join(
                clean_sd, sales_header, join_keys, header_cols
            )
            sales["txn_type"] = "sale"
        else:
            sales = sales_detail.copy()
            sales["txn_type"] = "sale"
    elif sales_header is not None:
        sales = sales_header.copy()
        sales["txn_type"] = "sale"
    elif sales_detail is not None:
        sales = sales_detail.copy()
        sales["txn_type"] = "sale"
    elif sales_gen is not None:
        sales = sales_gen.copy()
        sales["txn_type"] = "sale"

    if sales is not None:
        if "amount" not in sales.columns or sales["amount"].isna().all() or (pd.to_numeric(sales["amount"], errors="coerce") == 0).all():
            if "sales_subtotal" in sales.columns and sales["sales_subtotal"].notna().any():
                sales["amount"] = pd.to_numeric(sales["sales_subtotal"], errors="coerce")
            elif "unit_price" in sales.columns and "quantity" in sales.columns:
                sales["amount"] = pd.to_numeric(sales["unit_price"], errors="coerce") * pd.to_numeric(sales["quantity"], errors="coerce")
            elif "mrp" in sales.columns and "quantity" in sales.columns:
                sales["amount"] = pd.to_numeric(sales["mrp"], errors="coerce") * pd.to_numeric(sales["quantity"], errors="coerce")

        # A trusted invoice total includes invoice-level adjustments absent
        # from detail subtotals. Allocate the difference before any KPI/product
        # filtering so category and product sums reconcile to net invoices.
        if (sales_header is not None and sales_detail is not None
                and _has_valid_col(sales, "invoice_total")):
            key = next((field for field in ("transaction_id", "invoice_id")
                        if _has_valid_col(sales, field)), None)
            if key:
                sales["_extra.original_line_amount"] = sales["amount"]
                for _, indexes in sales.groupby(key, dropna=False).groups.items():
                    lines = pd.to_numeric(sales.loc[indexes, "amount"], errors="coerce")
                    totals = pd.to_numeric(sales.loc[indexes, "invoice_total"], errors="coerce").dropna()
                    if totals.empty:
                        continue
                    if len(totals) != 1 or lines.isna().any():
                        raise ValueError("Invoice total cannot be reconciled with incomplete or conflicting sale lines")
                    difference = float(totals.iloc[0]) - float(lines.sum())
                    if abs(difference) < 0.000001:
                        continue
                    weights = lines.abs()
                    if weights.sum() == 0:
                        raise ValueError("Invoice adjustment cannot be allocated to zero-valued sale lines")
                    adjusted = (lines + difference * weights / weights.sum()).round(2)
                    adjusted.iloc[-1] += round(float(totals.iloc[0]) - float(adjusted.sum()), 2)
                    sales.loc[indexes, "amount"] = adjusted.to_numpy()
                sales["_invoice_adjustment_basis"] = "Net invoice totals; adjustments allocated by absolute line value"

        if "product_id" not in sales.columns or sales["product_id"].isna().all():
            if "product_code" in sales.columns:
                sales["product_id"] = sales["product_code"].astype(str).str.lower().map(prod_map)
        elif "product_code" in sales.columns:
            sales["product_id"] = sales["product_id"].combine_first(sales["product_code"].astype(str).str.lower().map(prod_map))

        if "category" not in sales.columns or not sales["category"].notna().any():
            if "product_code" in sales.columns:
                sales["category"] = sales["product_code"].astype(str).str.lower().map(cat_map)
            if "product_id" in sales.columns and ("category" not in sales.columns or not sales["category"].notna().any()):
                sales["category"] = sales["product_id"].astype(str).str.lower().map(cat_map)
            if "category" not in sales.columns or not sales["category"].notna().any():
                sales["category"] = "General"

        if "manufacturer" not in sales.columns or sales["manufacturer"].isna().all():
            if "product_code" in sales.columns:
                sales["manufacturer"] = sales["product_code"].astype(str).str.lower().map(mfg_map)
            if "product_id" in sales.columns and ("manufacturer" not in sales.columns or sales["manufacturer"].isna().all()):
                sales["manufacturer"] = sales["product_id"].astype(str).str.lower().map(mfg_map)

        if "supplier_name" not in sales.columns or sales["supplier_name"].isna().all():
            if "product_code" in sales.columns:
                sales["supplier_name"] = sales["product_code"].astype(str).str.lower().map(prod_supplier_map)
            if "product_id" in sales.columns and ("supplier_name" not in sales.columns or sales["supplier_name"].isna().all()):
                sales["supplier_name"] = sales["product_id"].astype(str).str.lower().map(prod_supplier_map)
            if "supplier_id" in sales.columns and ("supplier_name" not in sales.columns or sales["supplier_name"].isna().all()):
                sales["supplier_name"] = sales["supplier_id"].astype(str).map(vendor_map)

        if ("cost" not in sales.columns or sales["cost"].isna().all()) and (batch_cost_map or prod_cost_map):
            if "cost" not in sales.columns:
                sales["cost"] = pd.NA
            for idx in sales.index:
                row_val = sales.loc[idx]
                b = str(row_val.get("batch_no", "")).strip().lower() if "batch_no" in sales.columns else ""
                pc = str(row_val.get("product_code", "")).strip().lower() if "product_code" in sales.columns else ""
                pname = str(row_val.get("product_id", "")).strip().lower() if "product_id" in sales.columns else ""
                c_val = batch_cost_map.get(b) or prod_cost_map.get(pc) or prod_cost_map.get(pname)
                if c_val is not None:
                    sales.loc[idx, "cost"] = c_val

        parts.append(sales)

    # 2. Assemble Purchases / Expenses (money paid out to suppliers)
    purchases = None
    used_sales_names = [n for n in [sd_name, sh_name, sg_name] if n]
    purchase_header, ph_name = find_table(r"purchase.*header|expense.*header|vendor_bill|supplier_invoice", exclude=used_sales_names)
    purchase_gen, pg_name = find_table(r"^(tbl_)?(purchases?|expenses?|supplier_bills?|bills_payable)$", exclude=used_sales_names)
    purch_detail, pdet_name = find_table(r"purchase.*detail|purchase.*item|expense.*detail|vendor.*detail", exclude=used_sales_names)

    # Generic purchase header fallback: table with supplier_id and amount/paid_amount/sales_subtotal (e.g. tbl_14)
    if purchase_header is None and purchase_gen is None:
        for name, t_df in tables.items():
            if name in used_sales_names or name == st_name:
                continue
            if _has_valid_col(t_df, "supplier_id") and not _has_valid_col(t_df, "product_id") and (_has_valid_col(t_df, "paid_amount") or (_has_valid_col(t_df, "amount") and not _has_valid_col(t_df, "customer_id"))):
                purchase_header, ph_name = t_df.copy(), name
                break

    # Structural purchase-detail discovery uses the explicit order foreign key.
    # Equal row counts do not establish any relationship to an expense ledger.
    if purch_detail is None and purchase_header is not None:
        for name, candidate in tables.items():
            if name in used_sales_names or name in (ph_name, st_name):
                continue
            if _has_valid_col(candidate, "purchase_order_no") and _has_valid_col(candidate, "quantity"):
                purch_detail, pdet_name = candidate.copy(), name
                break
    if purch_detail is not None and purchase_header is not None:
        for key in ("purchase_order_no", "transaction_id"):
            if _has_valid_col(purch_detail, key) and not _has_valid_col(purchase_header, key):
                if _has_valid_col(purchase_header, "row_id"):
                    purchase_header[key] = purchase_header["row_id"].astype(str)
                    purch_detail[key] = purch_detail[key].astype(str)

    if purch_detail is not None and purchase_header is not None:
        join_keys = [
            c for c in ["purchase_order_no", "transaction_id", "purchase_id", "bill_id", "invoice_id", "header_id", "order_id"]
            if _has_valid_col(purch_detail, c) and _has_valid_col(purchase_header, c)
        ]
        if join_keys:
            header_cols = [
                c for c in ["invoice_total", "date", "month", "year", "supplier_name", "supplier_id", "vendor_name", "invoice_id", "bill_no", "payment_method", "net_payable", "paid_amount"]
                if _has_valid_col(purchase_header, c)
            ]
            clean_pd = purch_detail.drop(
                columns=[c for c in header_cols if c in purch_detail.columns and not purch_detail[c].notna().any()]
            )
            purchases, join_key = _safe_detail_header_join(
                clean_pd, purchase_header, join_keys, header_cols
            )
            purchases["txn_type"] = "expense"
        else:
            purchases = purch_detail.copy()
            purchases["txn_type"] = "expense"
        if "cost" in purchases.columns and "quantity" in purchases.columns:
            if "amount" not in purchases.columns or (pd.to_numeric(purchases["amount"], errors="coerce") == 0).all():
                purchases["amount"] = pd.to_numeric(purchases["cost"], errors="coerce") * pd.to_numeric(purchases["quantity"], errors="coerce")
        if "product_id" not in purchases.columns or purchases["product_id"].isna().all():
            if "product_code" in purchases.columns:
                purchases["product_id"] = purchases["product_code"].astype(str).str.lower().map(prod_map)
        parts.append(purchases)
    elif purchase_header is not None:
        purchases = purchase_header.copy()
        if "supplier_id" in purchases.columns and "vendor_name" not in purchases.columns:
            purchases["vendor_name"] = purchases["supplier_id"].astype(str).map(lambda x: vendor_map.get(x, "Unknown"))
            purchases["supplier_name"] = purchases["vendor_name"]
        purchases["txn_type"] = "expense"
        parts.append(purchases)
    elif purch_detail is not None:
        purchases = purch_detail.copy()
        if "cost" in purchases.columns and "quantity" in purchases.columns:
            if "amount" not in purchases.columns or (pd.to_numeric(purchases["amount"], errors="coerce") == 0).all():
                purchases["amount"] = pd.to_numeric(purchases["cost"], errors="coerce") * pd.to_numeric(purchases["quantity"], errors="coerce")
        if "product_id" not in purchases.columns or purchases["product_id"].isna().all():
            if "product_code" in purchases.columns:
                purchases["product_id"] = purchases["product_code"].astype(str).str.lower().map(prod_map)
        purchases["txn_type"] = "expense"
        parts.append(purchases)
    elif purchase_gen is not None:
        purchases = purchase_gen.copy()
        purchases["txn_type"] = "expense"
        parts.append(purchases)

    # Extract product-to-supplier mapping from purchases
    if purchases is not None and "supplier_name" in purchases.columns:
        for _, r in purchases.iterrows():
            s_name = str(r.get("supplier_name", "")).strip()
            if s_name and s_name.lower() not in ("nan", "none", "", "unknown"):
                pc = str(r.get("product_code", "")).strip().lower()
                pname = str(r.get("product_id", "")).strip().lower()
                if pc: prod_supplier_map[pc] = s_name
                if pname: prod_supplier_map[pname] = s_name

    # Backfill sales supplier_name from updated prod_supplier_map if sales was already assembled
    if sales is not None and ("supplier_name" not in sales.columns or sales["supplier_name"].isna().all()):
        if "product_code" in sales.columns:
            sales["supplier_name"] = sales["product_code"].astype(str).str.lower().map(prod_supplier_map)
        if "product_id" in sales.columns and ("supplier_name" not in sales.columns or sales["supplier_name"].isna().all()):
            sales["supplier_name"] = sales["product_id"].astype(str).str.lower().map(prod_supplier_map)

    # 3. Assemble Inventory / Stock (for stock holding and expiry analysis)
    candidate_stock = [t for t in [stock_tbl, purch_detail] if t is not None]
    stock = None
    for c_tbl in candidate_stock:
        if _has_valid_col(c_tbl, "quantity") and _has_valid_col(c_tbl, "expiry_date"):
            stock = c_tbl.copy()
            stock["txn_type"] = "inventory"
            break

    if stock is None and stock_tbl is not None:
        stock = stock_tbl.copy()
        stock["txn_type"] = "inventory"

    if stock is not None:
        if "product_code" in stock.columns:
            stock_code_pnames = stock["product_code"].astype(str).str.lower().map(prod_map)
            if "product_id" not in stock.columns:
                stock["product_id"] = stock_code_pnames
            else:
                stock["product_id"] = stock["product_id"].fillna(stock_code_pnames).map(lambda x: prod_map.get(str(x).lower(), x))
        elif "product_id" in stock.columns:
            stock["product_id"] = stock["product_id"].astype(str).map(lambda x: prod_map.get(x, x))

        if "reorder_level" not in stock.columns or stock["reorder_level"].isna().all():
            if "product_code" in stock.columns:
                stock["reorder_level"] = stock["product_code"].astype(str).str.lower().map(reorder_map)
            if "product_id" in stock.columns and ("reorder_level" not in stock.columns or stock["reorder_level"].isna().all()):
                stock["reorder_level"] = stock["product_id"].astype(str).str.lower().map(reorder_map)

        if "category" not in stock.columns or not stock["category"].notna().any():
            if "product_code" in stock.columns:
                stock["category"] = stock["product_code"].astype(str).str.lower().map(cat_map)
            if "product_id" in stock.columns and ("category" not in stock.columns or not stock["category"].notna().any()):
                stock["category"] = stock["product_id"].astype(str).str.lower().map(cat_map)
            if "category" not in stock.columns or not stock["category"].notna().any():
                stock["category"] = "General"

        if "manufacturer" not in stock.columns or stock["manufacturer"].isna().all():
            if "product_code" in stock.columns:
                stock["manufacturer"] = stock["product_code"].astype(str).str.lower().map(mfg_map)
            if "product_id" in stock.columns and ("manufacturer" not in stock.columns or stock["manufacturer"].isna().all()):
                stock["manufacturer"] = stock["product_id"].astype(str).str.lower().map(mfg_map)

        if "supplier_name" not in stock.columns or stock["supplier_name"].isna().all():
            if "supplier_id" in stock.columns:
                stock["supplier_name"] = stock["supplier_id"].astype(str).map(vendor_map)
            if "product_code" in stock.columns and ("supplier_name" not in stock.columns or stock["supplier_name"].isna().all()):
                stock["supplier_name"] = stock["product_code"].astype(str).str.lower().map(prod_supplier_map)
            if "product_id" in stock.columns and ("supplier_name" not in stock.columns or stock["supplier_name"].isna().all()):
                stock["supplier_name"] = stock["product_id"].astype(str).str.lower().map(prod_supplier_map)

        if "cost" not in stock.columns or stock["cost"].isna().all():
            if "batch_no" in stock.columns:
                stock["cost"] = stock["batch_no"].astype(str).str.lower().map(batch_cost_map)
            if "product_code" in stock.columns and ("cost" not in stock.columns or stock["cost"].isna().all()):
                stock["cost"] = stock["product_code"].astype(str).str.lower().map(prod_cost_map)
            if "product_id" in stock.columns and ("cost" not in stock.columns or stock["cost"].isna().all()):
                stock["cost"] = stock["product_id"].astype(str).str.lower().map(prod_cost_map)

        if "mrp" not in stock.columns or stock["mrp"].isna().all():
            if "product_code" in stock.columns:
                stock["mrp"] = stock["product_code"].astype(str).str.lower().map(prod_price_map)
            if "product_id" in stock.columns and ("mrp" not in stock.columns or stock["mrp"].isna().all()):
                stock["mrp"] = stock["product_id"].astype(str).str.lower().map(prod_price_map)

        parts.append(stock)

    if parts:
        combined = pd.concat(parts, ignore_index=True)
        return combined

    return df


def _fetch_metadatas_from_sqlite(chroma_dir: str, where_field: str, where_values: Optional[List[str]] = None, collection_name: Optional[str] = None) -> List[Dict[str, Any]]:
    import sqlite3
    import json
    from app.security.crypto import decrypt_string

    db_path = os.path.join(chroma_dir, "chroma.sqlite3")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not where_values:
            cursor = conn.execute("SELECT DISTINCT id FROM embedding_metadata WHERE key = ?", (where_field,))
            ids = [r[0] for r in cursor.fetchall()]
        elif where_field == "sql_prefix":
            placeholders = " OR ".join(["string_value LIKE ?" for _ in where_values])
            params = [f"sql://{v.strip()}/%" for v in where_values]
            cursor = conn.execute(f"SELECT DISTINCT id FROM embedding_metadata WHERE key = 'source_file' AND ({placeholders})", params)
            ids = [r[0] for r in cursor.fetchall()]
        else:
            placeholders = ",".join("?" * len(where_values))
            cursor = conn.execute(f"""
                SELECT DISTINCT id FROM embedding_metadata 
                WHERE key = ? AND string_value IN ({placeholders})
            """, [where_field] + list(where_values))
            ids = [r[0] for r in cursor.fetchall()]
            
            # If matching database_name or group_name, also check source_file prefix
            if not ids and where_field in ("database_name", "group_name"):
                sql_params = [f"sql://{v.strip()}/%" for v in where_values]
                sql_ph = " OR ".join(["string_value LIKE ?" for _ in where_values])
                cursor2 = conn.execute(f"SELECT DISTINCT id FROM embedding_metadata WHERE key = 'source_file' AND ({sql_ph})", sql_params)
                ids = [r[0] for r in cursor2.fetchall()]

        # Temporary staging and rollback collections are not business data.
        active_ids = {row[0] for row in conn.execute(
            "SELECT e.id FROM embeddings e JOIN segments s ON s.id=e.segment_id "
            "JOIN collections c ON c.id=s.collection WHERE c.name=?",
            (collection_name or settings.collection_name,))}
        ids = [identifier for identifier in ids if identifier in active_ids]
        if not ids:
            return []

        results = []
        batch_size = 500
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            b_ph = ",".join("?" * len(batch_ids))
            cur2 = conn.execute(f"""
                SELECT id, key, string_value, int_value, float_value, bool_value 
                FROM embedding_metadata 
                WHERE id IN ({b_ph})
            """, batch_ids)
            chunk_ids = dict(conn.execute(
                f"SELECT id, embedding_id FROM embeddings WHERE id IN ({b_ph})", batch_ids).fetchall())
            rows_by_id: Dict[str, Dict[str, Any]] = {}
            for r in cur2.fetchall():
                eid = r["id"]
                if eid not in rows_by_id:
                    rows_by_id[eid] = {}
                k = r["key"]
                val = r["string_value"] if r["string_value"] is not None else (
                    r["int_value"] if r["int_value"] is not None else (
                        r["float_value"] if r["float_value"] is not None else r["bool_value"]
                    )
                )
                if k == "record_json" and val:
                    rows_by_id[eid][k] = val
                    try:
                        decrypted = decrypt_string(str(val))
                        record_dict = json.loads(decrypted)
                        if isinstance(record_dict, dict):
                            rows_by_id[eid].update(record_dict)
                        elif isinstance(record_dict, list) and record_dict and isinstance(record_dict[0], dict):
                            rows_by_id[eid].update(record_dict[0])
                    except Exception:
                        rows_by_id[eid][k] = val
                else:
                    rows_by_id[eid][k] = val
            for eid, record in rows_by_id.items():
                record["chunk_id"] = chunk_ids[eid]
            results.extend(rows_by_id.values())
        return results
    finally:
        conn.close()


def _load_canonical(req: KPIRequest, include_raw: bool = False):
    """Connector -> mapping -> canonical DataFrame with in-memory caching."""

    def _loaded(canonical, mapping, raw=None):
        if include_raw:
            return canonical.copy(deep=False), mapping, (raw if raw is not None else canonical).copy(deep=False)
        return canonical.copy(), mapping

    # Handle Whole Database Selection (e.g. db://PharmacyPOS or db://inventory,sales)
    if req.file_path.startswith("db://"):
        raw_target = req.file_path.replace("db://", "").strip()
        db_name = raw_target
        cache_key = f"db:{db_name}:{req.domain}"
        if cache_key in _df_cache:
            return _loaded(_df_cache[cache_key]["canonical"], _df_cache[cache_key]["mapping"])
        try:
            import pandas as pd
            chroma_dir = settings.chroma_dir
            if raw_target.lower() in ("all", "all_databases", "all_pos_databases", "*"):
                all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "source_type", ["database"])
                if not all_metas:
                    all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "domain", [req.domain])
            elif "," in raw_target:
                db_names = [d.strip() for d in raw_target.split(",") if d.strip()]
                all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "group_name", db_names)
                if not all_metas:
                    all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "database_name", db_names)
                if not all_metas:
                    all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "sql_prefix", db_names)
            else:
                all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "group_name", [db_name])
                if not all_metas:
                    all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "database_name", [db_name])
                if not all_metas:
                    all_metas = _fetch_metadatas_from_sqlite(chroma_dir, "sql_prefix", [db_name])
            if not all_metas:
                raise HTTPException(status_code=404, detail=f"Database '{raw_target}' records not found in KnowledgeBase")

            raw_canonical = pd.DataFrame(all_metas)
            canonical = _build_canonical_database(raw_canonical)
            mapping = {col_name: col_name for col_name in canonical.columns}
            _df_cache[cache_key] = {"canonical": canonical, "mapping": mapping}
            return _loaded(canonical, mapping)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error loading whole database records: {e}")

    # Handle Individual Database Table Selection (e.g. sql://PharmacyPOS/tbl_SalesHeader)
    if req.file_path.startswith("sql://"):
        cache_key = f"{req.file_path}:{req.domain}"
        if cache_key in _df_cache:
            return _loaded(_df_cache[cache_key]["canonical"], _df_cache[cache_key]["mapping"])
        try:
            import pandas as pd
            chroma_dir = settings.chroma_dir
            metas = _fetch_metadatas_from_sqlite(chroma_dir, "source_file", [req.file_path])
            if not metas:
                table_name = req.file_path.split("/")[-1]
                metas = _fetch_metadatas_from_sqlite(chroma_dir, "table_name", [table_name])
            if not metas:
                raise HTTPException(status_code=404, detail="Database table records not found in KnowledgeBase")
            
            canonical = pd.DataFrame(metas)
            canonical = _build_canonical_database(canonical)
            mapping = {col_name: col_name for col_name in canonical.columns}
            _df_cache[cache_key] = {"canonical": canonical, "mapping": mapping}
            return _loaded(canonical, mapping)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error loading database records: {e}")

    actual_path = req.file_path
    if not os.path.exists(actual_path):
        fname = Path(req.file_path).name
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        up_cand = os.path.join(base_dir, "data", "uploads", fname)
        samp_cand = os.path.join(base_dir, "data", "samples", fname)
        if os.path.exists(up_cand):
            actual_path = up_cand
        elif os.path.exists(samp_cand):
            actual_path = samp_cand
        else:
            raise HTTPException(status_code=404, detail=f"File '{fname}' does not exist on disk.")

    try:
        mtime = os.path.getmtime(actual_path)
        mapping_str = str(sorted(req.mapping.items())) if req.mapping else "auto"
        cache_key = f"{actual_path}:{mtime}:{req.domain}:{req.sheet_name}:{req.table_or_query}:{mapping_str}"
        
        if cache_key in _df_cache:
            cached = _df_cache[cache_key]
            return _loaded(cached["canonical"], cached["mapping"])
    except Exception:
        cache_key = None

    connector = detect_connector(actual_path)
    kwargs: Dict[str, Any] = {}
    if req.sheet_name:
        kwargs["sheet_name"] = req.sheet_name
    if req.table_or_query:
        kwargs["table_or_query"] = req.table_or_query

    raw = connector.fetch(**kwargs)

    domain_pack = None
    try:
        domain_pack = get_domain_pack(req.domain)
    except ValueError:
        pass

    from app.schema.profile import source_signature, source_identity, confirmed_mapping
    saved = confirmed_mapping(source_signature(list(raw.columns), connector.__class__.__name__, req.domain,
                              source_identity(actual_path, req.sheet_name, req.table_or_query)), raw, req.domain)
    if not req.mapping and saved is not None:
        mapping = saved
    elif not req.mapping:
        from app.schema.mapper import suggest_mapping
        sample_records = raw.head(5).to_dict(orient="records") if hasattr(raw, "head") else []
        proposal = suggest_mapping(list(raw.columns), sample_records, domain_pack, resolve_conflicts=True)
        mapping = {s.source_column: s.canonical_field for s in proposal.suggestions if s.canonical_field is not None}
    else:
        mapping = req.mapping
    canonical = apply_mapping(raw, mapping, domain=req.domain, keep_extras=True)
    # Keep the source currency with the canonical frame so deterministic
    # tabular answers can name the unit without guessing or hardcoding one.
    currency_tokens = {"USD": r"(?:usd|\$|dollar)", "PKR": r"(?:pkr|rs\.?|₨|rupee)", "EUR": r"(?:eur|€|euro)", "GBP": r"(?:gbp|£|pound)"}
    detected_currencies = [code for code, pattern in currency_tokens.items()
                           if any(re.search(pattern, str(col), re.I) for col in raw.columns)]
    canonical.attrs["source_currency"] = detected_currencies[0] if len(detected_currencies) == 1 else ("MIXED" if detected_currencies else None)

    # Preserve common spreadsheet fields that the general pharmacy mapper
    # cannot represent as one-to-one canonical columns. These rules are based
    # on explicit headers and table shape, never on question wording.
    if req.domain == "pharmacy":
        extra_cols = {str(col).casefold(): col for col in canonical.columns if str(col).startswith("_extra.")}
        product_name_col = extra_cols.get("_extra.product_name")
        raw_names = {str(col).strip().casefold().replace(" ", "_") for col in raw.columns}
        if product_name_col and "product_id" in canonical.columns and "product_id" in raw_names:
            # Keep the stock keeping ID distinct from the readable medicine name.
            if "product_code" not in canonical.columns:
                canonical["product_code"] = canonical["product_id"]
            canonical["product_id"] = canonical[product_name_col]
        for extra_name, canonical_name in (
            ("_extra.unit_cost", "cost"),
            ("_extra.warehouse", "warehouse"),
            ("_extra.payment_status", "status"),
            ("_extra.purchase_date", "date"),
        ):
            source_col = extra_cols.get(extra_name)
            if source_col and (canonical_name not in canonical.columns or canonical[canonical_name].isna().all()):
                canonical[canonical_name] = canonical[source_col]
        if "warehouse" in canonical.columns and "branch" not in canonical.columns:
            canonical["branch"] = canonical["warehouse"]
        if "txn_type" not in canonical.columns:
            if any(name in raw_names for name in ("purchase_id", "purchase_date")):
                canonical["txn_type"] = "purchase"
            elif any(name in raw_names for name in ("sale_id", "sales_id")):
                canonical["txn_type"] = "sale"
            elif "stock" in raw_names and any(name in raw_names for name in ("reorder_level", "expiry_date")):
                canonical["txn_type"] = "inventory"
    
    if cache_key:
        if len(_df_cache) > 10:
            _df_cache.pop(next(iter(_df_cache)))
        _df_cache[cache_key] = {"canonical": canonical, "mapping": mapping}

    return _loaded(canonical, mapping, raw)


def _filters(req: KPIRequest, canonical: Optional[pd.DataFrame] = None) -> KPIFilters:
    current_dict = req.filters.dict() if req.filters else {}

    # If range_preset is provided and not 'all', dynamically calculate date_from and date_to
    if req.range_preset and req.range_preset.lower().strip() not in ("all", "all_time") and canonical is not None:
        sales = canonical
        if "txn_type" in sales.columns and sales["txn_type"].astype(str).str.casefold().eq("sale").any():
            sales = sales[sales["txn_type"].astype(str).str.casefold().eq("sale")]
        if not current_dict.get("date_from"):
            if "date" not in sales or pd.to_datetime(sales["date"], errors="coerce").isna().any():
                raise ValueError("Timeframe cannot be applied: sale dates are missing or invalid; review the schema mapping")
        if "date" in canonical.columns and not current_dict.get("date_from"):
            valid_dates = pd.to_datetime(sales["date"], errors="coerce").dropna()
            if not valid_dates.empty:
                max_date = valid_dates.max()
                preset = req.range_preset.lower().strip()
                date_from = None
                if preset in ("7d", "7_days", "last_7_days"):
                    date_from = (max_date - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
                elif preset in ("28d", "28_days", "last_28_days"):
                    date_from = (max_date - pd.Timedelta(days=27)).strftime("%Y-%m-%d")
                elif preset in ("6m", "6_months", "last_6_months"):
                    date_from = (max_date - pd.DateOffset(months=6)).strftime("%Y-%m-%d")

                if date_from:
                    current_dict["date_from"] = date_from
                    current_dict["date_to"] = max_date.strftime("%Y-%m-%d")

    return KPIFilters.from_dict(current_dict if current_dict else None)


class ExpiryReportRequest(KPIRequest):
    """An expiry report is a KPI request with a value basis and reference date."""

    value_basis: Optional[str] = Field(
        None, description="'cost' (money lost, default) or 'mrp' (revenue foregone)."
    )
    as_of: Optional[str] = Field(
        None, description="Reference date for expiry maths (YYYY-MM-DD). Defaults to today."
    )


def _envelope(req: KPIRequest, canonical, mapping: Dict[str, str], include_validation: Optional[bool] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "file_path": req.file_path,
        "domain": req.domain,
        "mapping_used": mapping,
        "total_rows": int(len(canonical)),
    }
    should_validate = req.include_validation if include_validation is None else include_validation
    if should_validate:
        body["validation_report"] = validate(canonical, domain=req.domain).to_dict()
    return body


def _sanitize_json(obj: Any) -> Any:
    """Recursively convert NaN and Infinity floats to None for valid JSON serialization."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj


@router.post("/kpis")
def compute_kpi_pack(req: KPIRequest):
    """Compute the full core KPI pack (plus any domain-registered KPIs) with provenance."""
    try:
        canonical, mapping = _load_canonical(req)
        filters = _filters(req, canonical)
        results = engine.compute_all(canonical, filters, domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["range_preset"] = req.range_preset or "all"
        body["date_from"] = filters.date_from
        body["date_to"] = filters.date_to
        body["kpis"] = {key: result.to_dict() for key, result in results.items()}
        return _sanitize_json(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/kpi/{key}")
def compute_single_kpi(key: str, req: KPIRequest):
    """Compute one KPI by key."""
    if not engine.has(key):
        raise HTTPException(
            status_code=404,
            detail=f"Unknown KPI '{key}'. Available: {[s.key for s in engine.list_kpis(req.domain)]}",
        )
    try:
        canonical, mapping = _load_canonical(req)
        filters = _filters(req, canonical)
        result = engine.compute(key, canonical, filters, domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["range_preset"] = req.range_preset or "all"
        body["date_from"] = filters.date_from
        body["date_to"] = filters.date_to
        body["kpi"] = result.to_dict()
        return _sanitize_json(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/kpis")
def list_available_kpis(domain: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """List registered KPIs (core + those registered for `domain`) and their definitions."""
    eff_domain = domain or get_default_domain()
    return {"kpis": [spec.to_dict() for spec in engine.list_kpis(eff_domain)]}


class TrendRequest(KPIRequest):
    """Trend/forecast request: metric, period size, horizon, optional single product."""

    metric: str = Field("revenue", description="'revenue' or 'units'.")
    granularity: Optional[str] = Field(None, description="daily | weekly | monthly.")
    range_preset: Optional[str] = Field(None, description="'7d' | '28d' | '6m' | 'all'")
    horizon: Optional[int] = Field(None, description="Periods to forecast ahead.")
    product_id: Optional[str] = Field(
        None, description="Restrict to one product (required for a per-product forecast)."
    )
    as_of: Optional[str] = Field(
        None, description="Reference date; history is truncated here. Defaults to all data."
    )


def _trend_filters(req: "TrendRequest") -> KPIFilters:
    """Fold the trend-specific fields into the shared filter object."""
    filters = _filters(req)
    options = dict(filters.options or {})
    if req.granularity:
        options["granularity"] = req.granularity
    if req.horizon is not None:
        options["horizon"] = req.horizon
    return replace(
        filters,
        options=options,
        as_of=req.as_of or filters.as_of,
        product_id=req.product_id or filters.product_id,
    )


@router.post("/trend")
def trend(req: TrendRequest):
    """Descriptive trend only: observed series, moving average, growth. No prediction."""
    try:
        canonical, mapping = _load_canonical(req)

        effective_req = req
        if req.range_preset and "date" in canonical.columns:
            import pandas as pd
            valid_dates = pd.to_datetime(canonical["date"], errors="coerce").dropna()
            if not valid_dates.empty:
                max_date = valid_dates.max()
                preset = req.range_preset.lower().strip()
                date_from = None
                auto_granularity = req.granularity

                if preset in ("7d", "7_days", "last_7_days"):
                    date_from = (max_date - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
                    auto_granularity = auto_granularity or "daily"
                elif preset in ("28d", "28_days", "last_28_days"):
                    date_from = (max_date - pd.Timedelta(days=27)).strftime("%Y-%m-%d")
                    auto_granularity = auto_granularity or "daily"
                elif preset in ("6m", "6_months", "last_6_months"):
                    date_from = (max_date - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
                    auto_granularity = auto_granularity or "monthly"

                if date_from:
                    date_to = max_date.strftime("%Y-%m-%d")
                    current_filters = req.filters.dict() if req.filters else {}
                    current_filters["date_from"] = date_from
                    current_filters["date_to"] = date_to
                    effective_filters = AnalyticsFilters(**current_filters)
                    effective_req = req.model_copy(update={
                        "filters": effective_filters,
                        "granularity": auto_granularity
                    })

        key = "units_trend" if effective_req.metric == "units" else "revenue_trend"
        result = engine.compute(key, canonical, _trend_filters(effective_req), domain=effective_req.domain)
        body = _envelope(effective_req, canonical, mapping)
        body["metric"] = effective_req.metric
        body["granularity"] = effective_req.granularity or settings.forecast_granularity
        body["range_preset"] = req.range_preset
        trend_dict = result.to_dict()
        series = trend_dict.get("series") or []
        body["trend"] = trend_dict
        body["monthly"] = series
        body["series"] = series
        body["total_revenue"] = round(sum(p.get("value", 0) for p in series), 2)
        body["point_count"] = len(series)
        return _sanitize_json(body)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))



@router.post("/forecast")
def forecast(req: TrendRequest):
    """
    Forecast with uncertainty bands.

    Returns the history, the forecast points with lower/upper bands, and the method
    and tier used. When history is too short or too gappy the result is
    `unavailable` with the reason — that refusal is deliberate, not an error.
    """
    try:
        canonical, mapping = _load_canonical(req)
        filters = _trend_filters(req)
        if not filters.as_of:
            filters = replace(filters, as_of=date.today().isoformat())

        if req.product_id:
            key = "product_demand_forecast"
        elif req.metric == "units":
            key = "demand_forecast"
        else:
            key = "revenue_forecast"

        result = engine.compute(key, canonical, filters, domain=req.domain)
        body = _envelope(req, canonical, mapping)
        body["metric"] = req.metric
        body["granularity"] = req.granularity or settings.forecast_granularity
        body["horizon"] = req.horizon or settings.forecast_horizon
        body["forecast"] = result.to_dict()
        return _sanitize_json(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/expiry-report")
def expiry_report(req: ExpiryReportRequest):
    """
    Convenience report: every expiry KPI registered for the domain, in one call.

    Returns the banded buckets, already-expired value, the near-expiry headline and
    counts, and the by-manufacturer breakdown — each with its own item-level
    breakdown and provenance. Thin: all maths happens in the domain KPI pack.
    """
    try:
        canonical, mapping = _load_canonical(req)

        filters = _filters(req)
        options = dict(filters.options or {})
        if req.value_basis:
            options["value_basis"] = req.value_basis
        filters = replace(
            filters,
            options=options,
            as_of=req.as_of or filters.as_of,
        )

        specs = [s for s in engine.list_kpis(req.domain) if "risk" in s.tags]
        if not specs:
            raise HTTPException(
                status_code=404,
                detail=f"Domain '{req.domain}' registers no expiry KPIs.",
            )

        results = {s.key: engine.compute(s.key, canonical, filters, domain=req.domain) for s in specs}

        body = _envelope(req, canonical, mapping)
        body["reference_date"] = filters.as_of or date.today().isoformat()
        body["value_basis"] = options.get("value_basis", settings.expiry_value_basis)
        body["buckets_days"] = list(settings.expiry_buckets_days)
        body["kpis"] = {key: result.to_dict() for key, result in results.items()}
        return _sanitize_json(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
