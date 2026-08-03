from typing import Dict, List, Any
import datetime
import pandas as pd

from app.schema.domain import DomainPack, Problem, registry

class PharmacyDomainPack(DomainPack):
    @property
    def name(self) -> str:
        return "pharmacy"

    @property
    def extra_fields(self) -> List[str]:
        return [
            "generic_name", "manufacturer", "batch_no", "expiry_date", 
            "mfg_date", "pack_size", "barcode", "mrp", "drap_reg_no", 
            "schedule_flag", "scheme", "rack_location", "reorder_level", 
            "prescription_ref"
        ]

    @property
    def header_synonyms(self) -> Dict[str, List[str]]:
        return {
            # --- Pharmacy-specific fields ---
            "expiry_date": [
                "exp", "exp date", "exp.date", "expiry", "expiry date",
                "e.date", "exp dt", "expdt", "میعاد", "expiry_date",
            ],
            "mfg_date": [
                "mfg date", "mfg dt", "mfgdate", "manufacture date",
                "manufacturing date", "prod date", "production date",
            ],
            "batch_no": [
                "batch", "batch no", "batch#", "batch number", "batchno",
                "lot", "lot no", "lot number", "lot#",
            ],
            "generic_name": [
                "generic", "formula", "salt", "molecule", "composition",
                "generic name", "active ingredient", "ingredient",
            ],
            "mrp": [
                "mrp", "retail price", "sale price", "max retail",
                "maximum retail price", "retail", "selling price",
            ],
            "barcode": [
                "barcode", "bar code", "ean", "ean13", "upc", "sku",
                "item code", "product code",
            ],
            "pack_size": [
                "pack size", "packsize", "pack", "packing", "pack qty",
                "strip size", "tabs per strip",
            ],
            "drap_reg_no": [
                "drap", "drap no", "drap reg", "registration no",
                "reg no", "reg number", "drug reg", "drug reg no",
            ],
            "schedule_flag": [
                "schedule", "drug schedule", "schedule flag", "controlled",
            ],
            "rack_location": [
                "rack", "location", "rack location", "rack no",
                "shelf", "bin", "store location",
            ],
            "reorder_level": [
                "reorder", "reorder level", "min stock", "minimum stock",
                "reorder qty", "reorder quantity",
            ],
            "prescription_ref": [
                "prescription", "rx", "prescription no", "prescription ref",
                "prescription number", "script",
            ],
            "scheme": ["scheme", "bonus", "deal", "offer", "discount scheme"],

            # --- Core fields: pharmacy-specific aliases ---
            "manufacturer": [
                "company", "mfg", "manufacturer", "made by", "brand",
                "pharma company", "lab", "laboratory",
            ],
            "product_id": [
                "product name", "item name", "brand name", "medicine",
                "product", "drug name", "medicine name", "drug",
                "item", "name", "product desc",
            ],
            "supplier_id": [
                "supplier", "vendor", "distributor", "supplier name",
                "vendor name", "distributor name", "party", "party name",
            ],
            "customer_id": [
                "customer", "patient", "client", "customer name",
                "patient name", "buyer",
            ],
            "description": [
                "desc", "description", "details", "particulars",
                "item desc", "product desc", "narration", "remarks",
            ],
            "category": [
                "category", "cat", "type", "drug type", "product type",
                "therapeutic class", "class", "group",
            ],
            "quantity": [
                "qty", "quantity", "stock", "on hand", "units",
                "available qty", "closing stock",
            ],
            "unit_price": [
                "price", "rate", "unit price", "rate pkr",
                "selling rate", "per unit",
            ],
            "amount": [
                "total", "amount", "net amount", "line total",
                "total amount", "value", "net value",
            ],
            "cost": [
                "trade price", "tp", "purchase price", "cost price",
                "pp", "cost", "landed cost",
            ],
            "date": [
                "date", "txn date", "invoice date", "transaction date",
                "posting date", "voucher date",
            ],
            "invoice_id": [
                "invoice", "bill no", "receipt no", "invoice no",
                "invoice number", "bill number", "voucher no",
                "challan no", "order no",
            ],
            "discount": [
                "discount", "disc", "disc%", "discount%",
                "trade discount", "special discount",
            ],
            "tax": [
                "tax", "gst", "vat", "sales tax", "st",
                "withholding tax", "wht",
            ],
            "payment_method": [
                "payment", "payment method", "mode of payment",
                "pay mode", "payment mode",
            ],
            "txn_type": [
                "type", "txn type", "transaction type", "voucher type",
                "entry type",
            ],
        }

    def validate_dataframe(self, df: pd.DataFrame) -> List[Problem]:
        """
        Vectorized validation rules for the Pharmacy domain pack.
        """
        problems: List[Problem] = []

        if df.empty:
            return problems

        def _get_refs(mask: pd.Series) -> List[int]:
            if "source_row" in df.columns:
                refs = df.loc[mask, "source_row"].tolist()
                cleaned = []
                for idx, r in zip(df.index[mask], refs):
                    if pd.notna(r):
                        try:
                            cleaned.append(int(r))
                        except (ValueError, TypeError):
                            cleaned.append(int(idx) + 1)
                    else:
                        cleaned.append(int(idx) + 1)
                return cleaned
            return (df.index[mask] + 1).tolist()

        # 1. BELOW_COST (warning: MRP or unit_price < cost)
        if "cost" in df.columns:
            cost_num = pd.to_numeric(df["cost"], errors="coerce")
            
            if "mrp" in df.columns:
                mrp_num = pd.to_numeric(df["mrp"], errors="coerce")
                below_cost_mrp = mrp_num.notna() & cost_num.notna() & (mrp_num < cost_num)
                if below_cost_mrp.any():
                    row_refs = _get_refs(below_cost_mrp)
                    samples = [
                        f"mrp={m}, cost={c}" 
                        for m, c in zip(mrp_num[below_cost_mrp][:5], cost_num[below_cost_mrp][:5])
                    ]
                    problems.append(
                        Problem(
                            severity="warning",
                            code="BELOW_COST",
                            message="MRP is less than cost (Trade Price)",

                            field="mrp",
                            row_refs=row_refs,
                            sample=samples
                        )
                    )

            if "unit_price" in df.columns:
                price_num = pd.to_numeric(df["unit_price"], errors="coerce")
                below_cost_price = price_num.notna() & cost_num.notna() & (price_num < cost_num)
                if below_cost_price.any():
                    row_refs = _get_refs(below_cost_price)
                    samples = [
                        f"unit_price={p}, cost={c}" 
                        for p, c in zip(price_num[below_cost_price][:5], cost_num[below_cost_price][:5])
                    ]
                    problems.append(
                        Problem(
                            severity="warning",
                            code="BELOW_COST",
                            message="Sale price is below cost (Trade Price)",
                            field="unit_price",
                            row_refs=row_refs,
                            sample=samples
                        )
                    )

        # 2. MRP_OVERCHARGE (warning: unit_price > MRP)
        if "unit_price" in df.columns and "mrp" in df.columns:
            price_num = pd.to_numeric(df["unit_price"], errors="coerce")
            mrp_num = pd.to_numeric(df["mrp"], errors="coerce")
            overcharge_mask = price_num.notna() & mrp_num.notna() & (price_num > mrp_num)
            if overcharge_mask.any():
                row_refs = _get_refs(overcharge_mask)
                samples = [
                    f"unit_price={p}, mrp={m}" 
                    for p, m in zip(price_num[overcharge_mask][:5], mrp_num[overcharge_mask][:5])
                ]
                problems.append(
                    Problem(
                        severity="warning",
                        code="MRP_OVERCHARGE",
                        message="Sale price exceeds MRP (Regulatory compliance risk)",

                        field="unit_price",
                        row_refs=row_refs,
                        sample=samples
                    )
                )

        # 3. EXPIRED_STOCK (warning: expiry_date in the past)
        if "expiry_date" in df.columns:
            exp_dates = pd.to_datetime(df["expiry_date"], errors="coerce")
            today = pd.Timestamp(datetime.date.today())
            expired_mask = exp_dates.notna() & (exp_dates < today)
            if expired_mask.any():
                row_refs = _get_refs(expired_mask)
                samples = exp_dates[expired_mask].dt.strftime("%Y-%m-%d").tolist()[:5]
                problems.append(
                    Problem(
                        severity="warning",
                        code="EXPIRED_STOCK",
                        message="Stock is expired (Financial and patient safety risk)",

                        field="expiry_date",
                        row_refs=row_refs,
                        sample=samples
                    )
                )

        # 4a. MISSING_EXPIRY — batch number present but expiry_date blank
        if "expiry_date" in df.columns:
            exp_val = df["expiry_date"]
            # Treat NaT, None, NaN, and blank strings as missing
            exp_missing = (
                exp_val.isna()
                | (exp_val.astype(str).str.strip() == "")
                | (exp_val.astype(str).str.strip() == "NaT")
                | (exp_val.astype(str).str.strip() == "None")
                | (exp_val.astype(str).str.strip() == "nan")
            )

            # Sub-case A: has a batch number but no expiry
            batch_no_exp = pd.Series(False, index=df.index)
            if "batch_no" in df.columns:
                batch_val = df["batch_no"]
                has_batch = batch_val.notna() & (batch_val.astype(str).str.strip() != "")
                batch_no_exp = has_batch & exp_missing
                if batch_no_exp.any():
                    row_refs = _get_refs(batch_no_exp)
                    problems.append(
                        Problem(
                            severity="warning",
                            code="MISSING_EXPIRY",
                            message="Batch number provided but expiry date is missing",
                            field="expiry_date",
                            row_refs=row_refs,
                            sample=row_refs[:5],
                        )
                    )

            # Sub-case B: inventory rows that lack expiry AND aren't already flagged
            # by the batch-no path (avoid double-reporting the same rows).
            is_inventory = (
                "quantity" in df.columns
                and not ("amount" in df.columns and df["amount"].notna().any())
            )
            if is_inventory:
                inv_missing_exp = exp_missing & (~batch_no_exp)
                if inv_missing_exp.any():
                    row_refs = _get_refs(inv_missing_exp)
                    problems.append(
                        Problem(
                            severity="warning",
                            code="MISSING_EXPIRY",
                            message="Missing expiry date on pharmacy stock item",
                            field="expiry_date",
                            row_refs=row_refs,
                            sample=row_refs[:5],
                        )
                    )

            # 4b. INVALID_EXPIRY — non-blank value that failed date parsing.
            # Use pd.to_datetime coercion to actually attempt parsing, rather than
            # col.isna() which returns False for raw strings like "not-a-date".
            raw_exp_str = exp_val.astype(str).str.strip()
            raw_exp_present = (
                exp_val.notna()
                & raw_exp_str.ne("")
                & raw_exp_str.ne("nan")
                & raw_exp_str.ne("None")
                & raw_exp_str.ne("NaT")
            )
            coerced_exp_date = pd.to_datetime(exp_val, errors="coerce")
            invalid_exp_mask = raw_exp_present & coerced_exp_date.isna()
            if invalid_exp_mask.any():
                row_refs = _get_refs(invalid_exp_mask)
                samples = df.loc[invalid_exp_mask, "expiry_date"].astype(str).tolist()[:5]
                problems.append(
                    Problem(
                        severity="warning",
                        code="INVALID_EXPIRY",
                        message="Expiry date could not be parsed (expected formats: MM/YY, MM-YYYY, YYYY-MM-DD)",
                        field="expiry_date",
                        row_refs=row_refs,
                        sample=samples,
                    )
                )

        # 5. MISSING_MRP (warning/info)
        if "mrp" not in df.columns or df["mrp"].isna().all():
            problems.append(
                Problem(
                    severity="warning",
                    code="MISSING_MRP",
                    message="Pharmacy data is missing Maximum Retail Price (MRP) column/values",
                    field="mrp",
                    row_refs=_get_refs(pd.Series(True, index=df.index)),
                    sample=[]
                )
            )
        else:
            mrp_missing = df["mrp"].isna() | (df["mrp"].astype(str).str.strip() == "")
            if mrp_missing.any():
                row_refs = _get_refs(mrp_missing)
                problems.append(
                    Problem(
                        severity="warning",
                        code="MISSING_MRP",
                        message="Missing Maximum Retail Price (MRP) for item",
                        field="mrp",
                        row_refs=row_refs,
                        sample=row_refs[:5]
                    )
                )

        # 6. UNREGISTERED_HINT (info: missing DRAP registration number)
        if "drap_reg_no" in df.columns:
            drap_missing = df["drap_reg_no"].isna() | (df["drap_reg_no"].astype(str).str.strip() == "")
            if drap_missing.any():
                row_refs = _get_refs(drap_missing)
                problems.append(
                    Problem(
                        severity="info",
                        code="UNREGISTERED_HINT",
                        message="DRAP registration number is absent for item",
                        field="drap_reg_no",
                        row_refs=row_refs,
                        sample=row_refs[:5]
                    )
                )
        else:
            problems.append(
                Problem(
                    severity="info",
                    code="UNREGISTERED_HINT",
                    message="DRAP registration number field is absent",
                    field="drap_reg_no",
                    row_refs=[],
                    sample=[]
                )
            )

        return problems

    def validate_row(self, row: dict, index: int) -> List[Problem]:
        single_df = pd.DataFrame([row])
        return self.validate_dataframe(single_df)


# Register the pharmacy pack
registry.register(PharmacyDomainPack())

