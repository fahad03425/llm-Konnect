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
            "expiry_date": ["exp", "exp date", "exp.date", "expiry", "expiry date", "e.date", "میعاد"],
            "batch_no": ["batch", "batch no", "batch#", "lot", "lot no"],
            "generic_name": ["generic", "formula", "salt", "molecule", "composition"],
            "mrp": ["mrp", "retail price", "sale price", "max retail"],
            "cost": ["trade price", "tp", "purchase price", "cost price", "pp"],
            "manufacturer": ["company", "mfg", "manufacturer", "made by"],
            "scheme": ["scheme", "bonus", "deal"],
            "product_id": ["product name", "item name", "brand name", "medicine"],
            # Common aliases for core fields
            "quantity": ["qty", "quantity", "stock", "on hand"],
            "unit_price": ["price", "rate", "unit price", "rate pkr"],
            "amount": ["total", "amount", "net amount", "line total", "total amount"],
            "date": ["date", "txn date", "invoice date"],
            "invoice_id": ["invoice", "bill no", "receipt no", "invoice no"],
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
