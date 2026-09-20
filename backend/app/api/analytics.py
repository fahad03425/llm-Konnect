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
from app.core.config import settings
from app.schema.domain import get_domain_pack
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping
from app.schema.validate import validate
from app.ingestion.store import KnowledgeBase

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
    domain: str = "pharmacy"
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


_df_cache: Dict[str, Any] = {}

def clear_analytics_cache(db_name: Optional[str] = None):
    """Purge in-memory DataFrame cache for all or specific database sources."""
    global _df_cache
    if db_name:
        db_low = db_name.strip().lower()
        keys_to_remove = [k for k in _df_cache if db_low in k.lower()]
        for k in keys_to_remove:
            _df_cache.pop(k, None)
    else:
        _df_cache.clear()

def _build_canonical_database(df: pd.DataFrame) -> pd.DataFrame:
    """
    Intelligently structure multi-table relational database records into a canonical DataFrame.

    Prevents naively dumping sales, purchases, batches, and dimensions together:
    1. Distinguishes Sales vs. Expenses/Purchases by setting `txn_type = 'sale'` and `txn_type = 'expense'`.
    2. Merges sales details with sales headers (enriching line items with date, invoice_id, customer) to prevent double counting.
    3. Dynamically links product costs from product master or purchase detail tables so Gross Profit and Gross Margin compute accurately.
    4. Handles inventory/stock records for expiry analytics with `txn_type = 'inventory'`.
    """
    import re

    if "table_name" not in df.columns or df["table_name"].nunique() <= 1:
        if "table_name" in df.columns and "txn_type" not in df.columns:
            tbl_name = str(df["table_name"].iloc[0]).lower()
            if re.search(r"purchase|expense", tbl_name):
                df = df.copy()
                df["txn_type"] = "expense"
        return df

def _build_canonical_database(df: pd.DataFrame) -> pd.DataFrame:
    """
    Decompose multi-table whole-database records into a clean canonical DataFrame.
    Dynamically identifies sales headers vs details, supplier purchases/expenses,
    inventory/stock, and cross-references product catalogs for accurate cost linking.
    Works dynamically across arbitrary database schemas.
    """
    if "table_name" not in df.columns:
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

    # Identify transaction and inventory tables using broad schema patterns
    sales_detail, sd_name = find_table(r"sales.*detail|order.*detail|order.*item|invoice.*detail|line.*item|bill.*detail|pos.*detail|sales_item")
    sales_header, sh_name = find_table(r"sales.*header|order.*header|invoice.*header|bill.*header|pos.*header|sale_header|order_header|order_main|sales_main")
    sales_gen, sg_name = find_table(r"^sales?$|^orders?$|^transactions?$|^bills?$|^invoices?$|^pos$")

    purchase_header, ph_name = find_table(r"purchase.*header|expense.*header|vendor_bill|supplier_invoice")
    purchase_detail, pd_name = find_table(r"purchase.*detail|purchase.*item|expense.*detail|vendor.*detail")
    purchase_gen, pg_name = find_table(r"^purchases?$|^expenses?$|^supplier_bills?$|^bills_payable$")

    stock_tbl, st_name = find_table(r"batch|inventory|stock|warehouse")
    prod_tbl, pr_name = find_table(r"product|item|article|sku|catalog")

    parts = []

    # 1. Assemble Sales Transactions
    sales = None
    if sales_detail is not None and sales_header is not None:
        join_keys = [
            c for c in ["invoice_id", "order_id", "sale_id", "bill_id", "bill_no", "transaction_id", "header_id", "receipt_id"]
            if _has_valid_col(sales_detail, c) and _has_valid_col(sales_header, c)
        ]
        if join_keys:
            join_key = join_keys[0]
            header_cols = [
                c for c in ["date", "month", "year", "customer_id", "doctor_name", "discount", "client_id", "user_id", "buyer_id"]
                if _has_valid_col(sales_header, c)
            ]
            clean_sd = sales_detail.drop(
                columns=[c for c in header_cols if c in sales_detail.columns and not sales_detail[c].notna().any()]
            )
            sales = clean_sd.merge(sales_header[[join_key] + header_cols], on=join_key, how="left")
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

    # Dynamic Product Cost Linking (if sales does not have cost or cost is all-null)
    if sales is not None:
        has_valid_cost = _has_valid_col(sales, "cost") and (pd.to_numeric(sales["cost"], errors="coerce") > 0).any()
        if not has_valid_cost:
            cost_ratio_map: Dict[str, float] = {}
            direct_cost_map: Dict[str, float] = {}
            observed_ratios: List[float] = []

            cost_cols = [
                "cost", "cost_price", "purchase_price", "buying_price",
                "unit_cost", "standard_cost", "buy_rate"
            ]
            id_cols = [
                "product_id", "product_sku", "product_code", "generic_name",
                "item_id", "item_code", "sku", "barcode"
            ]
            mrp_cols = [
                "mrp", "retail_price", "selling_price", "unit_price",
                "sale_price", "list_price"
            ]

            # Discover any table in the DB that has both an ID column and a cost column
            for t_name, c_tbl in tables.items():
                if t_name in (sd_name, sh_name, sg_name):
                    continue
                found_id = next((c for c in id_cols if _has_valid_col(c_tbl, c)), None)
                found_cost = next((c for c in cost_cols if _has_valid_col(c_tbl, c)), None)
                found_mrp = next((c for c in mrp_cols if _has_valid_col(c_tbl, c)), None)

                if found_id and found_cost:
                    for key_val, group in c_tbl.groupby(found_id):
                        c_series = pd.to_numeric(group[found_cost], errors="coerce").dropna()
                        if c_series.empty:
                            continue
                        cost_val = float(c_series.iloc[0])
                        key_str = str(key_val).strip().lower()

                        if found_mrp:
                            m_series = pd.to_numeric(group[found_mrp], errors="coerce").dropna()
                            if not m_series.empty and float(m_series.iloc[0]) > 0:
                                ratio = cost_val / float(m_series.iloc[0])
                                cost_ratio_map[key_str] = ratio
                                observed_ratios.append(ratio)
                        else:
                            direct_cost_map[key_str] = cost_val

            avg_ratio = (sum(observed_ratios) / len(observed_ratios)) if observed_ratios else None

            sales_id_cols = [
                c for c in id_cols
                if c in sales.columns
            ]

            def _resolve_line_cost(row):
                # Try ratio-based lookup first (handles pack cost vs loose selling units)
                for sc in sales_id_cols:
                    val = str(row.get(sc, "")).strip().lower()
                    if val and val in cost_ratio_map:
                        ratio = cost_ratio_map[val]
                        unit_p = pd.to_numeric(row.get("unit_price", 0), errors="coerce")
                        if pd.notna(unit_p) and unit_p > 0:
                            return round(float(unit_p) * ratio, 2)

                if avg_ratio is not None:
                    unit_p = pd.to_numeric(row.get("unit_price", 0), errors="coerce")
                    if pd.notna(unit_p) and unit_p > 0:
                        return round(float(unit_p) * avg_ratio, 2)

                # Try direct cost lookup
                for sc in sales_id_cols:
                    val = str(row.get(sc, "")).strip().lower()
                    if val and val in direct_cost_map:
                        return direct_cost_map[val]

                return None

            sales["cost"] = sales.apply(_resolve_line_cost, axis=1)

        parts.append(sales)

    # 2. Assemble Purchases / Expenses (money paid out to suppliers)
    used_sales_names = [n for n in [sd_name, sh_name, sg_name] if n]
    purchase_header, ph_name = find_table(r"purchase.*header|expense.*header|vendor_bill|supplier_invoice", exclude=used_sales_names)
    purchase_gen, pg_name = find_table(r"^(tbl_)?(purchases?|expenses?|supplier_bills?|bills_payable)$", exclude=used_sales_names)
    purchase_detail, pd_name = find_table(r"purchase.*detail|purchase.*item|expense.*detail|vendor.*detail", exclude=used_sales_names)

    if purchase_header is not None:
        purchases = purchase_header.copy()
        purchases["txn_type"] = "expense"
        parts.append(purchases)
    elif purchase_gen is not None:
        purchases = purchase_gen.copy()
        purchases["txn_type"] = "expense"
        parts.append(purchases)
    elif purchase_detail is not None:
        purchases = purchase_detail.copy()
        purchases["txn_type"] = "expense"
        parts.append(purchases)

    # 3. Assemble Inventory / Stock (for stock holding and expiry analysis)
    candidate_stock = [t for t in [stock_tbl, purchase_detail] if t is not None]
    stock = None
    for c_tbl in candidate_stock:
        if _has_valid_col(c_tbl, "quantity") and _has_valid_col(c_tbl, "expiry_date"):
            stock = c_tbl.copy()
            stock["txn_type"] = "inventory"
            break

    if stock is not None:
        parts.append(stock)
    elif stock_tbl is not None:
        stock = stock_tbl.copy()
        stock["txn_type"] = "inventory"
        parts.append(stock)

    if parts:
        combined = pd.concat(parts, ignore_index=True)
        return combined

    return df


def _load_canonical(req: KPIRequest):
    """Connector -> mapping -> canonical DataFrame with in-memory caching."""
    # Handle Whole Database Selection (e.g. db://PharmacyPOS)
    if req.file_path.startswith("db://"):
        db_name = req.file_path.replace("db://", "").strip()
        cache_key = f"db:{db_name}:{req.domain}"
        if cache_key in _df_cache:
            return _df_cache[cache_key]["canonical"].copy(), _df_cache[cache_key]["mapping"]
        try:
            import pandas as pd
            kb_inst = KnowledgeBase()
            col = kb_inst._get_chroma()
            res = col.get(where={"group_name": db_name}, include=["metadatas"])
            if not res or not res.get("metadatas") or len(res["metadatas"]) == 0:
                res = col.get(where={"database_name": db_name}, include=["metadatas"])
            if not res or not res.get("metadatas") or len(res["metadatas"]) == 0:
                raise HTTPException(status_code=404, detail=f"Database '{db_name}' records not found in KnowledgeBase")
            
            raw_canonical = pd.DataFrame(res["metadatas"])
            canonical = _build_canonical_database(raw_canonical)
            mapping = {col_name: col_name for col_name in canonical.columns}
            _df_cache[cache_key] = {"canonical": canonical, "mapping": mapping}
            return canonical.copy(), mapping
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error loading whole database records: {e}")

    # Handle Individual Database Table Selection (e.g. sql://PharmacyPOS/tbl_SalesHeader)
    if req.file_path.startswith("sql://"):
        cache_key = f"{req.file_path}:{req.domain}"
        if cache_key in _df_cache:
            return _df_cache[cache_key]["canonical"].copy(), _df_cache[cache_key]["mapping"]
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
            mapping = {col_name: col_name for col_name in canonical.columns}
            _df_cache[cache_key] = {"canonical": canonical, "mapping": mapping}
            return canonical.copy(), mapping
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error loading database records: {e}")

    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        mtime = os.path.getmtime(req.file_path)
        mapping_str = str(sorted(req.mapping.items())) if req.mapping else "auto"
        cache_key = f"{req.file_path}:{mtime}:{req.domain}:{req.sheet_name}:{req.table_or_query}:{mapping_str}"
        
        if cache_key in _df_cache:
            return _df_cache[cache_key]["canonical"].copy(), _df_cache[cache_key]["mapping"]
    except Exception:
        cache_key = None

    connector = detect_connector(req.file_path)
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

    mapping = req.mapping or map_headers(list(raw.columns), domain_pack)
    canonical = apply_mapping(raw, mapping, domain=req.domain, keep_extras=True)
    
    if cache_key:
        if len(_df_cache) > 10:
            _df_cache.pop(next(iter(_df_cache)))
        _df_cache[cache_key] = {"canonical": canonical, "mapping": mapping}

    return canonical.copy(), mapping


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


def _envelope(req: KPIRequest, canonical, mapping: Dict[str, str]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "file_path": req.file_path,
        "domain": req.domain,
        "mapping_used": mapping,
        "total_rows": int(len(canonical)),
    }
    if req.include_validation:
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
def list_available_kpis(domain: str = "pharmacy") -> Dict[str, List[Dict[str, Any]]]:
    """List registered KPIs (core + those registered for `domain`) and their definitions."""
    return {"kpis": [spec.to_dict() for spec in engine.list_kpis(domain)]}


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
        series = trend_dict.get("series", [])
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
