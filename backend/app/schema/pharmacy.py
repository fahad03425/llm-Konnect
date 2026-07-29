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

    def validate_row(self, row: dict, index: int) -> List[Problem]:
        problems = []
        
        # Pharmacy warning: MRP < cost (Selling below cost)
        mrp = row.get("mrp")
        cost = row.get("cost")
        if pd.notna(mrp) and pd.notna(cost):
            try:
                if float(mrp) < float(cost):
                    problems.append(Problem("warning", "MRP is less than cost (Trade Price)", index, "mrp"))
            except (ValueError, TypeError):
                pass
                
        # Pharmacy warning: Sale unit_price > MRP (MRP overcharge)
        unit_price = row.get("unit_price")
        if pd.notna(unit_price) and pd.notna(mrp):
            try:
                if float(unit_price) > float(mrp):
                    problems.append(Problem("warning", "Sale price exceeds MRP (Regulatory compliance risk)", index, "unit_price"))
            except (ValueError, TypeError):
                pass

        # Pharmacy warning: Expiry date in the past
        expiry = row.get("expiry_date")
        if pd.notna(expiry):
            if isinstance(expiry, (datetime.date, datetime.datetime, pd.Timestamp)):
                if expiry.date() < datetime.date.today():
                    problems.append(Problem("warning", "Stock is expired", index, "expiry_date"))

        # Pharmacy warning: Batch present but expiry missing
        batch = row.get("batch_no")
        if pd.notna(batch) and str(batch).strip() and pd.isna(expiry):
            problems.append(Problem("warning", "Batch number provided but expiry date is missing", index, "expiry_date"))
            
        # Pharmacy warning: Missing expiry date on inventory (we consider it inventory if it has qty but no invoice/sale)
        if pd.isna(expiry) and pd.notna(row.get("quantity")) and pd.isna(row.get("invoice_id")):
             problems.append(Problem("warning", "Missing expiry date on stock item", index, "expiry_date"))

        return problems


# Register the pharmacy pack
registry.register(PharmacyDomainPack())
