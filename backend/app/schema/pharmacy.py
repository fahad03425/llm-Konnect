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
            "prescription_ref", "doctor_name", "mobile_number", "bonus_quantity",
            "branch", "cashier_name", "client_type", "customer_alias",
            "reorder_quantity", "safety_stock_qty",
            "supplier_payable_amount", "supplier_payment_due_date", "last_sold_date",
            "opening_stock_qty", "closing_stock_qty", "status", "transaction_id",
            "time_of_day", "is_cancelled", "stock_qty",
            "supplier_name", "product_code", "net_payable", "tax_amount",
            "warehouse",
            "discount_amount", "tax_pct", "margin_pct", "discount_pct",
            "total_qty", "total_bonus", "total_items", "total_pack",
            "invoice_total", "paid_amount", "customer_balance", "previous_balance",
            "invoice_tax_pct", "invoice_discount_pct", "sales_subtotal",
            "line_discount_amount", "line_tax_amount", "invoice_discount",
            "invoice_tax", "carriage_charges", "other_charges"
            , "original_price", "discounted_price", "availability"
            , "pharmacist_name", "prescription_required", "purchase_order_no", "line_cost", "sub_items", "unit_name", "customer_due_date"
        ]

    @property
    def searchable_fields(self) -> List[str]:
        return [
            "product_id", "generic_name", "manufacturer", "batch_no", 
            "description", "category", "supplier_id", "customer_id", "invoice_id",
            "doctor_name", "mobile_number",
            "supplier_payable_amount", "supplier_payment_due_date", "last_sold_date",
            "opening_stock_qty", "closing_stock_qty", "category", "reorder_level",
            "time_of_day", "status", "is_cancelled", "tax", "tax_amount",
            "branch", "cashier_name", "client_type", "customer_alias",
            "warehouse",
            "discount_amount", "discount_pct", "margin_pct", "line_discount_amount",
            "line_tax_amount", "invoice_discount", "invoice_tax", "total_qty",
            "invoice_total", "paid_amount", "customer_balance", "previous_balance",
            "invoice_tax_pct", "invoice_discount_pct", "sales_subtotal",
            "bonus_quantity", "pack_size", "rack_location", "stock_qty",
            "original_price", "discounted_price", "discount_pct", "availability",
            "pharmacist_name", "prescription_required"
        ]

    @property
    def filter_metadata_fields(self) -> List[str]:
        return [
            "line_cost", "sub_items", "unit_name", "customer_due_date",
            "date", "txn_type", "payment_method", "transaction_id", "amount", "net_payable", "unit_price", "quantity",
            "product_id", "product_code", "supplier_id", "supplier_name", "customer_id", "invoice_id",
            "purchase_order_no",
            "generic_name", "manufacturer", "batch_no", "expiry_date",
            "mrp", "drap_reg_no", "schedule_flag", "doctor_name", "discount", "cost",
            "category", "reorder_level", "time_of_day", "status", "is_cancelled",
            "tax", "tax_amount", "discount_amount", "discount_pct", "margin_pct",
            "line_discount_amount", "line_tax_amount", "invoice_discount", "invoice_tax",
            "invoice_total", "paid_amount", "customer_balance", "previous_balance",
            "invoice_tax_pct", "invoice_discount_pct", "sales_subtotal",
            "total_qty", "total_bonus", "pack_size", "rack_location", "stock_qty",
            "branch", "cashier_name", "client_type",
            "supplier_payable_amount", "supplier_payment_due_date", "last_sold_date",
            "opening_stock_qty", "closing_stock_qty"
            , "warehouse", "original_price", "discounted_price", "availability",
            "pharmacist_name", "prescription_required"
            , "purchase_order_no"
        ]

    @property
    def kpi_question_rules(self) -> List[tuple]:
        """
        Expiry question vocabulary (English + Roman-Urdu) for the chatbot's numeric
        route. Kept on the pack so no pharmacy words reach the chatbot or KPI core.
        """
        from app.analytics.domains.pharmacy import PHARMACY_QUESTION_RULES

        return list(PHARMACY_QUESTION_RULES)

    @property
    def report_sections(self) -> List[str]:
        return ["expiry_risk"]

    def register_kpis(self, engine) -> None:
        """
        Attach the pharmacy expiry KPIs to the Module 6.6 KPI engine.

        Imported lazily: `app.schema` must not depend on `app.analytics` at import
        time, and the engine only calls this when it first does pharmacy work.
        """
        from app.analytics.domains.pharmacy import register as register_expiry_kpis

        register_expiry_kpis(engine, domain=self.name)

    def row_to_text(self, row: dict) -> str:
        # Build an explicit, labeled string to ensure reliable field extraction by the LLM.
        # Required format: "Label: value. Label: value." 
        # Missing fields are omitted entirely.

        def safe_str(val):
            return str(val).strip() if pd.notna(val) and str(val).strip() != "" else None

        # Fixed canonical order, prioritizing identity/relationships before truncation
        # Format: (key_in_row, Display_Label, optional_prefix)
        fields = [
            ("txn_type", "Transaction type", ""),
            ("date", "Date", ""),
            ("transaction_id", "Transaction", ""),
            ("invoice_id", "Invoice", ""),
            ("purchase_order_no", "Purchase Order", ""),
            ("customer_id", "Customer", ""),
            ("doctor_name", "Doctor", ""),
            ("pharmacist_name", "Pharmacist", ""),
            ("product_id", "Product", ""),
            ("medicine_name", "Product", ""),
            ("generic_name", "Generic", ""),
            ("category", "Category", ""),
            ("batch_no", "Batch", ""),
            ("expiry_date", "Expiry", ""),
            ("manufacturer", "Manufacturer", ""),
            ("supplier_id", "Supplier", ""),
            ("supplier_name", "Supplier", ""),
            ("product_code", "Product code", ""),
            ("quantity", "Quantity", ""),
            ("qty_sold", "Quantity sold", ""),
            ("qty_ordered", "Quantity ordered", ""),
            ("received_qty", "Received quantity", ""),
            ("stock_qty", "Stock quantity", ""),
            ("reorder_level", "Reorder level", ""),
            ("pack_size", "Pack size", ""),
            ("unit_price", "Unit price", "Rs "),
            ("original_price", "Original price", "Rs "),
            ("discounted_price", "Discounted price", "Rs "),
            ("sale_price", "Sale price", "Rs "),
            ("amount", "Total amount", "Rs "),
            ("net_payable", "Net payable", "Rs "),
            ("tax_amount", "GST amount", "Rs "),
            ("tax_pct", "GST", "%"),
            ("margin_pct", "Margin", "%"),
            ("discount_amount", "Discount amount", "Rs "),
            ("discount", "Discount", "Rs "),
            ("discount_pct", "Discount", ""),
            ("availability", "Availability", ""),
            ("cost", "Cost price", "Rs "),
            ("cost_price", "Cost price", "Rs "),
            ("mrp", "MRP", "Rs "),
            ("payment_method", "Payment", ""),
            ("outstanding_payable", "Outstanding payable", "Rs "),
            ("contact", "Contact", ""),
            ("mobile_number", "Contact", ""),
            ("last_order_date", "Last order date", ""),
            ("rack_location", "Rack", ""),
            ("bonus_quantity", "Bonus", ""),
            ("opening_stock_qty", "Opening stock", ""),
            ("closing_stock_qty", "Closing stock", ""),
            ("supplier_payable_amount", "Supplier payable", "Rs "),
            ("supplier_payment_due_date", "Supplier due date", ""),
            ("last_sold_date", "Last sold date", ""),
        ]

        parts = []
        handled_keys = set()
        for key, label, prefix in fields:
            val = safe_str(row.get(key))
            if val and key not in handled_keys:
                handled_keys.add(key)
                if key == "txn_type":
                    val = val.capitalize()
                rendered = f"{val}%" if key.endswith("_pct") else f"{prefix}{val}"
                parts.append(f"{label}: {rendered}")

        # Also capture any remaining unhandled extra attributes
        ignored_keys = {"source_connector", "source_row", "id"}
        for k, v in row.items():
            clean_k = k.replace("_extra.", "")
            if k not in handled_keys and clean_k not in handled_keys and clean_k not in ignored_keys:
                v_str = safe_str(v)
                if v_str:
                    label = clean_k.replace("_", " ").title()
                    parts.append(f"{label}: {v_str}")

        sentence = ". ".join(parts)
        if not sentence.endswith("."):
            sentence += "."
        
        return sentence

    @property
    def header_synonyms(self) -> Dict[str, List[str]]:
        return {
            # --- Supplier credit / Accounts payable to distributors ---
            "supplier_payable_amount": [
                "payable", "outstanding to supplier",
                "udhaar", "balance payable", "supplier payable", "payable to supplier",
                "vendor payable", "distributor payable", "supplier payable amount",
                "supplier credit", "credit balance", "supplier balance", "udhar",
                "balance payable to supplier", "distributor balance",
            ],
            "supplier_payment_due_date": [
                "supplier due date",
                "supplier payment due date",
                "distributor due date",
            ],

            # --- Stock movement & velocity ---
            "last_sold_date": [
                "last sale date", "last sold", "last transaction date",
                "last sold date", "last sale", "last sale dt", "last_sold",
                "last transaction", "last sold on", "last transaction dt",
            ],
            "opening_stock_qty": [
                "opening stock", "opening qty", "opening quantity", "opening stock qty",
                "opening balance", "op stock", "op qty", "op. stock", "op. qty",
                "opening units", "opening count", "op stock qty", "op qty count",
            ],
            "closing_stock_qty": [
                "closing qty", "closing quantity", "closing stock qty",
                "closing balance", "cl stock", "cl qty", "cl. stock", "cl. qty",
                "closing units", "closing count", "ending stock", "ending qty",
            ] + ["closing stock"],
            "stock_qty": [
                "stock qty", "stock quantity",
                "current stock", "current quantity", "current inventory",
                "on hand", "on hand qty", "on hand quantity", "quantity on hand", "qty on hand",
                "onhand qty", "onhand quantity", "available stock units", "available stock",
                "available quantity", "available qty", "stock", "stock level", "stock on hand",
                "inventory stock", "inventory quantity", "stock balance", "balance quantity",
                "physical stock", "total stock",
            ],
            "reorder_level": [
                "reorder", "reorder level", "min stock", "minimum stock",
                "reorder point", "rop", "min level", "minimum level", "min stock level",
                "minimum stock quantity", "minimum inventory", "restock level", "restock point",
                "reorder threshold", "reorder limit", "low stock threshold", "restock threshold",
                "minimum inventory level", "stock minimum", "minimum on hand", "minimum on hand qty", "min qty on hand",
                "minimum quantity on hand", "reorder at",
                "minstocklevel", "minimumstocklevel",
            ],
            "reorder_quantity": [
                "reorder qty", "reorder quantity", "order qty", "order quantity",
                "suggested order quantity", "replenishment quantity", "restock quantity",
            ],
            "safety_stock_qty": ["safety stock", "safety stock qty", "safety stock quantity"],
            "time_of_day": [
                "time", "time of sale", "time of transaction", "transaction time",
                "timeofsale", "transactiontime", "bill time", "billtime",
                "invoice time", "invoicetime", "sale time", "timestamp time",
            ],
            "is_cancelled": ["is cancelled", "is canceled", "iscancelled", "iscanceled", "voided"],
            "branch": ["branch", "branch name", "store branch", "store name", "pharmacy branch"],
            "cashier_name": ["cashier", "cashier name", "cashier_name", "operator name"],
            "client_type": ["client type", "client_type", "customer type", "account type"],
            "customer_alias": ["customer alias", "customer alias name", "alias name"],

            # --- Pharmacy-specific fields ---
            "doctor_name": [
                "doctor", "doctor name", "doctor_name", "doctorname",
                "dr name", "dr", "prescriber", "physician", "consultant",
                "doctor id", "doctor_id",
                "prescribing doctor", "prescribing physician", "prescribing doctor name",
            ],
            "pharmacist_name": [
                "pharmacist", "pharmacist name", "attending pharmacist",
                "attending pharmacist name", "dispensing pharmacist",
            ],
            "prescription_required": [
                "prescription required", "prescription flag", "rx required",
                "requires prescription",
            ],
            "mobile_number": [
                "mobile", "mobile number", "mobile no", "mobile_number",
                "mobilenumber", "mobileno", "phone", "phone number",
                "contact", "cell", "customer mobile", "customer mobile number", "customer phone",
            ],
            "bonus_quantity": ["bonus units",
                "bonus", "bon", "bon.", "bonus count", "bonus qty", "bonus quantity",
            ],
            "expiry_date": [
                "exp", "exp date", "exp.date", "expiry", "expiry date",
                "e.date", "exp dt", "expdt", "میعاد", "expiry_date", "inventory exp",
            ],
            "mfg_date": [
                "mfg date", "mfg dt", "mfgdate", "manufacture date",
                "manufacturing date", "prod date", "production date",
            ],
            "batch_no": [
                "batch", "batch no", "batch#", "batch number", "batchno",
                "lot", "lot no", "lot number", "lot#", "inventory batch",
            ],
            "generic_name": [
                "generic", "formula", "salt", "molecule", "composition",
                "generic name", "generic salt", "generic slash salt",
                "active ingredient", "ingredient", "product generic",
            ],
            "mrp": [
                "mrp", "retail price", "sale price", "max retail",
                "maximum retail price", "retail", "selling price",
                "s price", "s. price", "s price 1", "s. price 1", "sprice", "pricing retail price",
                "max retail price usd", "maximum retail price usd", "mrp usd", "retail price usd",
            ],
            "barcode": ["barcode", "bar code", "ean", "ean13", "upc"],
            "product_code": ["product code", "product_code", "code", "item code", "medicine code", "sku", "stock keeping unit"],
            "transaction_id": ["transaction id", "transaction no", "transaction number", "transaction #", "txn no", "txn number", "sale id", "sales id", "sale number", "sales number", "sale no", "sales no", "sale #", "sales #"],
            "purchase_order_no": ["porder id", "purchase order id", "purchase order no", "purchase id", "purchase number", "purchase no", "purchase #", "po number", "po no"],
            "supplier_name": ["supplier", "supplier name", "vendor name", "distributor name"],
            "pack_size": [
                "pack size", "packsize", "pack", "packing", "pack qty", "items per unit",
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
                "rack", "location", "rack location", "rack no", "rack number", "rack_number", "rack #", "rack num",
                "shelf", "shelf no", "shelf number", "shelf_number", "shelf #",
                "bin", "bin no", "bin number", "bin_number", "bin #",
                "store location", "s.l.", "sl", "loc.", "loc",
                "shelf location", "location code", "stock location rack",
            ],
            "reorder_level": [
                "reorder", "reorder level", "min stock", "minimum stock",
                "reorder point", "rop", "min level", "minimum level", "min stock level",
                "minimum stock quantity", "minimum inventory", "restock level", "restock point",
                "reorder threshold", "reorder limit", "low stock threshold", "restock threshold",
                "minimum inventory level", "stock minimum", "minimum on hand", "minimum on hand qty", "min qty on hand",
                "minimum quantity on hand", "reorder at",
                "minstocklevel", "minimumstocklevel",
            ],
            "reorder_quantity": [
                "reorder qty", "reorder quantity", "order qty", "order quantity",
                "suggested order quantity", "replenishment quantity", "restock quantity",
            ],
            "safety_stock_qty": ["safety stock", "safety stock qty", "safety stock quantity"],
            "prescription_ref": [
                "prescription", "rx", "prescription no", "prescription ref",
                "prescription number", "script",
            ],
            "scheme": ["scheme", "deal", "offer", "discount scheme"],
            "net_payable": ["net payable", "net payable amount", "nettotalamount"],
            "tax_amount": ["gst amount", "vat amount", "tax amount"],
            "invoice_total": ["invoice total", "bill total", "total amount", "grand total", "value including tax", "total amount including tax"],
            "paid_amount": ["paid amount", "amount paid", "payment amount", "paid"],
            "line_cost": ["purchase sub total", "purchase subtotal", "line cost", "line cogs", "total cogs"],
            "sub_items": ["sub items"],
            "unit_name": ["unit name", "unit"],
            "customer_due_date": ["customer due date", "customer payment due date"],
            "customer_balance": ["customer balance", "balance due"],
            "previous_balance": ["previous balance", "prior balance"],
            "invoice_tax_pct": ["invoice gst percentage", "invoice gst percent", "invoice tax percentage"],
            "invoice_discount_pct": ["discount by percentage", "invoice discount percentage"],
            "sales_subtotal": ["sales line subtotal"],
            "discount_amount": ["discount amount", "discount value"],
            "tax_pct": ["gst percent", "gst percentage", "tax percent", "vat percent"],
            "margin_pct": ["margin percent", "margin percentage", "profit margin percent"],
            "discount_pct": ["discount percent", "discount percentage", "disc percent"],
            "total_qty": ["total qty", "total quantity"],
            "total_bonus": ["total bonus", "total bonus qty", "total bonus quantity"],
            "total_items": ["total items", "item count"],
            "total_pack": ["total pack", "total packs"],
            "line_discount_amount": ["item discount", "item discount amount"],
            "line_tax_amount": ["item gst", "item tax", "item gst amount"],
            "invoice_discount": ["flat discount", "invoice discount"],
            "invoice_tax": ["flat gst", "invoice gst", "invoice tax"],
            "carriage_charges": ["carriage charges", "carriage", "carriage adda charges"],
            "other_charges": ["other charges", "additional charges", "additional amount", "misc amount", "miscellaneous amount"],

            # --- Core fields: pharmacy-specific aliases ---
            "manufacturer": [
                "company", "mfg", "manufacturer", "made by", "brand",
                "pharma company", "lab", "laboratory",
            ],
            "product_id": [
                "name", "product name", "item name", "brand name", "medicine",
                "product", "drug name", "medicine name", "drug",
                "item", "product desc", "product_name", "item_name", "drug_name", "product title",
            ],
            "supplier_id": [
                "supplier id", "vendor", "vendor id", "distributor id", "party", "party id", "party name",
            ],
            "customer_id": [
                "patient name", "patient", "patientname", "customer",
                "client", "customer name", "patient name", "buyer",
                "client name", "customer_name",
            ],
            "description": [
                "desc", "description", "details", "particulars",
                "item desc", "product desc", "narration", "remarks",
            ],
            "category": [
                "category", "cat", "type", "drug type", "product type",
                "therapeutic class", "class", "group", "drug class",
                "therapeutic category", "specialty", "disease",
            ],
            "quantity": [
                "qty", "quantity", "units", "stock units", "stockunits", "current stock units",
                "currentstockunits", "on hand units", "onhandunits",
                "quantity sold", "qty sold", "units sold", "sold quantity", "sales quantity",
            ],
            "unit_price": [
                "price", "rate", "unit price", "rate pkr",
                "selling rate", "per unit",
            ],
            "original_price": [
                "original price", "price before", "price_before", "list price", "regular price",
                "price before discount", "mrp before discount",
            ],
            "discounted_price": [
                "discounted price", "price after", "price_after", "price after discount",
                "current discounted price", "offer price", "sale price after discount",
            ],
            "availability": ["availability", "available status", "stock availability", "product availability"],
            "amount": [
                "amount", "line amount", "product amount", "line total", "sales subtotal", "sub total",
                "total", "net amount", "value", "net value", "sale amount", "sales amount",
                "total transaction value", "total transaction value usd", "transaction value", "transaction value usd", "sales value", "sales value usd", "total sales value",
                "gross amount", "subtotal",
            ],
            "cost": [
                "trade price", "tp", "purchase price", "cost price", "cost_price", "costprice",
                "pp", "cost", "landed cost", "p price", "p. price", "pprice", "pricing cost price",
                "unit cost", "unit_cost", "unitcost", "purchase unit cost", "buying unit cost",
                "unit cost price", "unit cost usd", "unit cost price usd", "cost price usd",
                "cogs", "purchase_cost", "purchase cost", "purchase_rate", "purchase rate",
                "buying price", "buying_price", "buying rate", "buying_rate",
                "purchase_rate_pkr", "item cost", "item_cost", "product cost", "product_cost",
                "unit purchase price", "unit_purchase_price", "pur price", "pur_price", "pur rate", "pur_rate",
                "pur cost", "pur_cost", "cost rate", "cost_rate", "cost val", "cost_val", "cost value", "cost_value",
            ],
            "date": [
                "date", "txn date", "invoice date", "transaction date",
                "posting date", "voucher date", "invoice date time",
                "invoicedatetime", "invoice_date_time", "inv date", "inv. date",
                "date & time", "bill date", "bill_date", "billdate",
                "bill datetime", "bill_datetime",
                "sale date", "sales date", "sales_date", "order date", "order_date",
                "purchase date", "purchase_date", "purchased date",
                "created at", "created_at", "timestamp", "time stamp", "entry time date", "dated", "receipt date", "receipt_date",
            ],
            "invoice_id": [
                "invoice", "reference number", "bill no", "receipt no", "invoice no",
                "invoice number", "bill number", "voucher no",
                "challan no", "order no", "billno", "bill_no", "inv no", "inv_no",
                "invoice_no", "invoice_id", "invoiceid", "bill_id", "billid",
                "transaction_id", "transaction id", "transaction_no", "transaction no",
                "trans_id", "trans id", "trans_no", "trans no",
                "txn_id", "txn id", "txn_no", "txn no",
                "sale_id", "sale id", "sale_no", "sale no", "saleno", "sales_id",
                "order_id", "order id", "order_no", "orderno",
                "receipt_id", "receipt id", "receipt_no", "receiptno",
                "doc_no", "doc no", "document_no", "counter_sale_no", "billnum", "invoicenum",
            ],
            "discount": [
                "discount", "disc", "disc%", "discount%",
                "trade discount", "special discount", "item discount",
                "item discount total", "itemdiscounttotal", "item disc",
                "flat disc", "flat discount", "disc by %",
            ],
            "tax": [
                "tax", "gst", "vat", "sales tax", "st",
                "withholding tax", "wht", "gst %", "flat gst", "item gst",
            ],
            "payment_method": [
                "payment", "payment method", "mode of payment",
                "pay mode", "payment mode", "client type", "clienttype",
                "payment type",
            ],
            "txn_type": [
                "type", "txn type", "transaction type", "voucher type",
                "entry type",
            ],
            "status": ["status", "transaction status", "order status", "sale status", "payment status", "payment_status"],
            "warehouse": ["warehouse", "warehouse name", "stock location", "storage location", "depot"],
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
                        message="No DRAP registration number is recorded for this item in the source data; this does not establish registration status",
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
                    message="The source has no DRAP registration-number field; registration status cannot be determined from this data",
                    field="drap_reg_no",
                    row_refs=[],
                    sample=[]
                )
            )

        return problems

    def validate_row(self, row: dict, index: int) -> List[Problem]:
        single_df = pd.DataFrame([row])
        return self.validate_dataframe(single_df)

    def derive_last_sold_date(self, df: pd.DataFrame) -> pd.DataFrame:
        """Derives 'last_sold_date' per product_id as max(date) over sale transactions."""
        return derive_last_sold_date(df)


def derive_last_sold_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derives 'last_sold_date' per product_id as max(date) where txn is a sale (or all dates
    if txn_type is absent), if 'last_sold_date' is not already present with values.
    Returns a copy of df with 'last_sold_date' populated if 'product_id' and 'date' exist.
    """
    if df is None or df.empty:
        return df
    if "last_sold_date" in df.columns and df["last_sold_date"].notna().any():
        return df

    prod_col = "product_id" if "product_id" in df.columns else ("product_name" if "product_name" in df.columns else None)
    date_col = "date" if "date" in df.columns else None

    if not prod_col or not date_col:
        return df

    out_df = df.copy()
    dates = pd.to_datetime(out_df[date_col], errors="coerce")
    valid_mask = dates.notna() & out_df[prod_col].notna()

    if "txn_type" in out_df.columns:
        from app.analytics.kpi import classify_transactions
        txn = classify_transactions(out_df)
        if txn.sale.any():
            valid_mask = valid_mask & txn.sale

    if not valid_mask.any():
        return out_df

    temp = pd.DataFrame({
        "prod": out_df.loc[valid_mask, prod_col].astype(str),
        "dt": dates[valid_mask]
    })
    max_dates = temp.groupby("prod")["dt"].max().to_dict()

    out_df["last_sold_date"] = out_df[prod_col].astype(str).map(max_dates)
    return out_df


# Register the pharmacy pack
registry.register(PharmacyDomainPack())


