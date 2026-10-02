"""
Module 6.6 (KPI Engine) — analytics API routes.

Thin transport layer only: connect a source through the existing
connectors -> schema mapping -> validation pipeline, then hand the canonical
DataFrame to `KPIEngine`. All arithmetic lives in `app.analytics`.

No LLM is involved on this path.
"""

import os
import re
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

    # Single-table case: tag purchase/expense/sale tables appropriately
    if df["table_name"].nunique() <= 1:
        if "txn_type" not in df.columns:
            tbl_name = str(df["table_name"].iloc[0]).lower()
            if re.search(r"purchase|expense", tbl_name):
                df = df.copy()
                df["txn_type"] = "expense"
            elif re.search(r"sale|order|invoice", tbl_name):
                df = df.copy()
                df["txn_type"] = "sale"
        return df

    tables = {t: df[df["table_name"] == t].copy() for t in df["table_name"].unique()}

    def find_table(pattern: str, exclude: Optional[List[str]] = None):
        exclude = exclude or []
        for name, t_df in tables.items():
            if name in exclude:
                continue
            if re.search(pattern, name, re.IGNORECASE):
                return t_df, name
        return None, None

    def _has_valid_col(t_df: Optional[pd.DataFrame], col_name: str) -> bool:
        return t_df is not None and col_name in t_df.columns and t_df[col_name].notna().any()

    def _safe_detail_header_join(detail: pd.DataFrame, header: pd.DataFrame, candidates: List[str], header_fields: List[str]):
        join_key = next((
            key for key in candidates
            if _has_valid_col(detail, key)
            and _has_valid_col(header, key)
            and not header.loc[header[key].notna(), key].duplicated().any()
        ), None)
        if not join_key:
            preserved = detail.copy()
            preserved["_join_warning"] = "Header join skipped: no unique shared transaction key"
            return preserved, None
        selected_fields = [
            field for field in header_fields
            if field == join_key or field not in detail.columns
        ]
        selected_fields = list(dict.fromkeys([join_key, *selected_fields]))
        joined = detail.merge(header[selected_fields], on=join_key, how="left", validate="many_to_one")
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
    for name, t_df in tables.items():
        if _has_valid_col(t_df, "product_id") and _has_valid_col(t_df, "product_code"):
            for _, r in t_df.iterrows():
                p_name = str(r.get("product_id", "")).strip()
                p_code = str(r.get("product_code", "")).strip()
                r_id = str(r.get("row_id", "")).strip()
                if p_name and p_name.lower() not in ("nan", "none", ""):
                    if r_id: prod_map[r_id] = p_name
                    if p_code: prod_map[p_code.lower()] = p_name

    vendor_map: Dict[str, str] = {}
    for name, t_df in tables.items():
        if _has_valid_col(t_df, "supplier_id") and _has_valid_col(t_df, "product_id") and not _has_valid_col(t_df, "batch_no") and not _has_valid_col(t_df, "amount"):
            for _, r in t_df.iterrows():
                v_name = str(r.get("product_id", "")).strip()
                s_id = str(r.get("supplier_id", "")).strip()
                r_id = str(r.get("row_id", "")).strip()
                if v_name and v_name.lower() not in ("nan", "none", ""):
                    if s_id: vendor_map[s_id] = v_name
                    if r_id: vendor_map[r_id] = v_name

    cat_map: Dict[str, str] = {}
    for name, t_df in tables.items():
        if _has_valid_col(t_df, "category") and _has_valid_col(t_df, "product_id"):
            for _, r in t_df.iterrows():
                p_name = str(r.get("product_id", "")).strip()
                c_name = str(r.get("category", "")).strip()
                if p_name and c_name and c_name.lower() not in ("nan", "none", ""):
                    cat_map[p_name.lower()] = c_name

    # Identify transaction and inventory tables using broad schema patterns
    sales_detail, sd_name = find_table(r"sales.*detail|order.*detail|order.*item|invoice.*detail|line.*item|bill.*detail|pos.*detail|sales_item")
    sales_header, sh_name = find_table(r"sales.*header|order.*header|invoice.*header|bill.*header|pos.*header|sale_header|order_header|order_main|sales_main")
    sales_gen, sg_name = find_table(r"^sales?$|^orders?$|^transactions?$|^bills?$|^invoices?$|^pos$")

    stock_tbl, st_name = find_table(r"batch|inventory|stock|warehouse")

    # Structural fallback for opaque/generic table names (e.g. tbl_1..25)
    if sales_detail is None and sales_gen is None:
        for name, t_df in tables.items():
            if (_has_valid_col(t_df, "sales_subtotal") or (_has_valid_col(t_df, "quantity") and (_has_valid_col(t_df, "unit_price") or _has_valid_col(t_df, "mrp") or _has_valid_col(t_df, "cost")))) and (_has_valid_col(t_df, "transaction_id") or _has_valid_col(t_df, "invoice_id") or _has_valid_col(t_df, "order_id")):
                sales_detail, sd_name = t_df.copy(), name
                break

    if sales_header is None and sales_gen is None:
        best_sh = None
        best_overlap = -1
        for name, t_df in tables.items():
            if name == sd_name:
                continue
            if (_has_valid_col(t_df, "date") or _has_valid_col(t_df, "created_at")) and (
                _has_valid_col(t_df, "invoice_total") or _has_valid_col(t_df, "amount") or _has_valid_col(t_df, "customer_id") or _has_valid_col(t_df, "payment_method")
            ):
                overlap = 0
                if sales_detail is not None:
                    for s_col in ["transaction_id", "invoice_id", "order_id"]:
                        if _has_valid_col(sales_detail, s_col):
                            s_vals = set(sales_detail[s_col].astype(str).dropna())
                            for h_col in [s_col, "row_id", "id"]:
                                if _has_valid_col(t_df, h_col):
                                    h_vals = set(t_df[h_col].astype(str).dropna())
                                    overlap = max(overlap, len(s_vals & h_vals))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_sh = (t_df.copy(), name)
        if best_sh is not None:
            sales_header, sh_name = best_sh

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
            c for c in ["invoice_id", "order_id", "sale_id", "bill_id", "bill_no", "transaction_id", "header_id", "receipt_id"]
            if _has_valid_col(sales_detail, c) and _has_valid_col(sales_header, c)
        ]
        if not join_keys:
            if _has_valid_col(sales_detail, "transaction_id") and _has_valid_col(sales_header, "row_id"):
                sales_header["transaction_id"] = sales_header["row_id"].astype(str)
                sales_detail["transaction_id"] = sales_detail["transaction_id"].astype(str)
                join_keys = ["transaction_id"]
            elif _has_valid_col(sales_detail, "invoice_id") and _has_valid_col(sales_header, "row_id"):
                sales_header["invoice_id"] = sales_header["row_id"].astype(str)
                sales_detail["invoice_id"] = sales_detail["invoice_id"].astype(str)
                join_keys = ["invoice_id"]

        if join_keys:
            header_cols = [
                c for c in ["date", "month", "year", "customer_id", "doctor_name", "discount", "client_id", "user_id", "buyer_id", "payment_method", "payment_type", "sales_type", "user_name", "status"]
                if _has_valid_col(sales_header, c)
            ]
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

        if "category" not in sales.columns or not sales["category"].notna().any():
            if "product_id" in sales.columns:
                sales["category"] = sales["product_id"].map(lambda x: cat_map.get(str(x).lower(), "General"))

        parts.append(sales)

    # 2. Assemble Purchases / Expenses (money paid out to suppliers)
    used_sales_names = [n for n in [sd_name, sh_name, sg_name] if n]
    purchase_header, ph_name = find_table(r"purchase.*header|expense.*header|vendor_bill|supplier_invoice", exclude=used_sales_names)
    purchase_gen, pg_name = find_table(r"^(tbl_)?(purchases?|expenses?|supplier_bills?|bills_payable)$", exclude=used_sales_names)
    purch_detail, pdet_name = find_table(r"purchase.*detail|purchase.*item|expense.*detail|vendor.*detail", exclude=used_sales_names)

    # Generic purchase header fallback: table with supplier_id and amount/paid_amount/sales_subtotal (e.g. tbl_14)
    if purchase_header is None and purchase_gen is None:
        for name, t_df in tables.items():
            if name in used_sales_names or name == st_name:
                continue
            if _has_valid_col(t_df, "supplier_id") and (_has_valid_col(t_df, "paid_amount") or (_has_valid_col(t_df, "amount") and not _has_valid_col(t_df, "customer_id"))):
                purchase_header, ph_name = t_df.copy(), name
                break

    # If purchase header exists, try joining date table (e.g. tbl_17) if date missing
    if purchase_header is not None and not _has_valid_col(purchase_header, "date"):
        for name, t_df in tables.items():
            if name in used_sales_names or name in (ph_name, st_name):
                continue
            if _has_valid_col(t_df, "date") and _has_valid_col(t_df, "row_id") and len(t_df) == len(purchase_header):
                purchase_header = purchase_header.merge(t_df[["row_id", "date"]], on="row_id", how="left")
                break

    if purch_detail is not None and purchase_header is not None:
        join_keys = [
            c for c in ["transaction_id", "purchase_id", "bill_id", "invoice_id", "header_id", "order_id"]
            if _has_valid_col(purch_detail, c) and _has_valid_col(purchase_header, c)
        ]
        if join_keys:
            header_cols = [
                c for c in ["date", "month", "year", "supplier_name", "supplier_id", "vendor_name", "invoice_id", "bill_no", "payment_method", "net_payable", "paid_amount"]
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
        purchases["txn_type"] = "expense"
        parts.append(purchases)
    elif purchase_gen is not None:
        purchases = purchase_gen.copy()
        purchases["txn_type"] = "expense"
        parts.append(purchases)

    # 3. Assemble Inventory / Stock (for stock holding and expiry analysis)
    candidate_stock = [t for t in [stock_tbl, purch_detail] if t is not None]
    stock = None
    for c_tbl in candidate_stock:
        if _has_valid_col(c_tbl, "quantity") and _has_valid_col(c_tbl, "expiry_date"):
            stock = c_tbl.copy()
            stock["txn_type"] = "inventory"
            break

    if stock is not None:
        if "product_id" in stock.columns:
            stock["product_id"] = stock["product_id"].astype(str).map(lambda x: prod_map.get(x, x))
        if "category" not in stock.columns or not stock["category"].notna().any():
            if "product_id" in stock.columns:
                stock["category"] = stock["product_id"].map(lambda x: cat_map.get(str(x).lower(), "General"))
        if "vendor_name" not in stock.columns and "supplier_id" in stock.columns:
            stock["vendor_name"] = stock["supplier_id"].astype(str).map(lambda x: vendor_map.get(x, "Unknown"))
            stock["supplier_name"] = stock["vendor_name"]
        parts.append(stock)
    elif stock_tbl is not None:
        stock = stock_tbl.copy()
        if "product_id" in stock.columns:
            stock["product_id"] = stock["product_id"].astype(str).map(lambda x: prod_map.get(x, x))
        if "category" not in stock.columns or not stock["category"].notna().any():
            if "product_id" in stock.columns:
                stock["category"] = stock["product_id"].map(lambda x: cat_map.get(str(x).lower(), "General"))
        if "vendor_name" not in stock.columns and "supplier_id" in stock.columns:
            stock["vendor_name"] = stock["supplier_id"].astype(str).map(lambda x: vendor_map.get(x, "Unknown"))
            stock["supplier_name"] = stock["vendor_name"]
        stock["txn_type"] = "inventory"
        parts.append(stock)

    if parts:
        combined = pd.concat(parts, ignore_index=True)
        return combined

    return df


def _load_canonical(req: KPIRequest, include_raw: bool = False):
    """Connector -> mapping -> canonical DataFrame with in-memory caching."""

    def _loaded(canonical, mapping, raw=None):
        if include_raw:
            # The report generator treats source frames as read-only. Use a
            # shallow copy to avoid another full-sized allocation for large XLSX files.
            return canonical.copy(deep=False), mapping, (raw if raw is not None else canonical).copy(deep=False)
        return canonical.copy(), mapping

    # Handle Whole Database Selection (e.g. db://PharmacyPOS)
    if req.file_path.startswith("db://"):
        raw_target = req.file_path.replace("db://", "").strip()
        db_name = raw_target
        cache_key = f"db:{db_name}:{req.domain}"
        if cache_key in _df_cache:
            return _loaded(_df_cache[cache_key]["canonical"], _df_cache[cache_key]["mapping"])
        try:
            import pandas as pd
            kb_inst = KnowledgeBase()
            col = kb_inst._get_chroma()
            if raw_target.lower() in ("all", "all_databases", "all_pos_databases", "*"):
                res = col.get(where={"source_type": "database"}, include=["metadatas"])
                if not res or not res.get("metadatas") or len(res["metadatas"]) == 0:
                    res = col.get(include=["metadatas"])
            elif "," in raw_target:
                db_names = [d.strip() for d in raw_target.split(",") if d.strip()]
                all_metas = []
                for db_n in db_names:
                    sub_res = col.get(where={"group_name": db_n}, include=["metadatas"])
                    if not sub_res or not sub_res.get("metadatas") or len(sub_res["metadatas"]) == 0:
                        sub_res = col.get(where={"database_name": db_n}, include=["metadatas"])
                    if sub_res and sub_res.get("metadatas"):
                        all_metas.extend(sub_res["metadatas"])
                res = {"metadatas": all_metas}
            else:
                res = col.get(where={"group_name": db_name}, include=["metadatas"])
            if not res or not res.get("metadatas") or len(res["metadatas"]) == 0:
                res = col.get(where={"database_name": db_name}, include=["metadatas"])
            if not res or not res.get("metadatas") or len(res["metadatas"]) == 0:
                raise HTTPException(status_code=404, detail=f"Database '{raw_target}' records not found in KnowledgeBase")
            
            # Check for registry index gaps
            active_tables = [
                r for r in file_registry.list_files()
                if getattr(r, "status", "") == "active" and (
                    getattr(r, "group_name", "") == db_name or getattr(r, "database_name", "") == db_name
                )
            ]
            if active_tables:
                indexed_file_ids = {m.get("file_id") for m in (res.get("metadatas") or []) if m.get("file_id")}
                indexed_table_names = {m.get("table_name") for m in (res.get("metadatas") or []) if m.get("table_name")}
                missing = [
                    r for r in active_tables
                    if r.file_id not in indexed_file_ids and getattr(r, "table_name", None) not in indexed_table_names
                ]
                if missing:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{len(missing)} active source table(s) have no indexed rows in KnowledgeBase"
                    )

            raw_canonical = pd.DataFrame(res["metadatas"])
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
            kb_inst = KnowledgeBase()
            col = kb_inst._get_chroma()
            res = col.get(where={"source_file": req.file_path}, include=["metadatas"])
            if not res or not res.get("metadatas"):
                table_name = req.file_path.split("/")[-1]
                res = col.get(where={"table_name": table_name}, include=["metadatas"])
            if not res or not res.get("metadatas") or len(res["metadatas"]) == 0:
                raise HTTPException(status_code=404, detail="Database table records not found in KnowledgeBase")
            
            canonical = pd.DataFrame(res["metadatas"])
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

    if not req.mapping:
        from app.schema.mapper import suggest_mapping
        sample_records = raw.head(5).to_dict(orient="records") if hasattr(raw, "head") else []
        proposal = suggest_mapping(list(raw.columns), sample_records, domain_pack, resolve_conflicts=True)
        mapping = {s.source_column: s.canonical_field for s in proposal.suggestions if s.canonical_field is not None}
    else:
        mapping = req.mapping
    canonical = apply_mapping(raw, mapping, domain=req.domain, keep_extras=True)

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
        if "date" in canonical.columns and not current_dict.get("date_from"):
            valid_dates = pd.to_datetime(canonical["date"], errors="coerce").dropna()
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
