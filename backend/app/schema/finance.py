from typing import Dict, List, Any
import pandas as pd
from app.schema.domain import DomainPack, Problem, registry

class FinanceDomainPack(DomainPack):
    @property
    def name(self) -> str:
        return "finance"

    @property
    def extra_fields(self) -> List[str]:
        return [
            "transaction_id", "account_name", "account_number", "category",
            "transaction_type", "amount", "currency", "vendor_customer",
            "invoice_number", "payment_method", "tax_amount", "status",
            "transaction_date"
        ]

    @property
    def searchable_fields(self) -> List[str]:
        return [
            "transaction_id", "account_name", "category", "transaction_type",
            "vendor_customer", "invoice_number", "status"
        ]

    @property
    def filter_metadata_fields(self) -> List[str]:
        return [
            "transaction_date", "amount", "transaction_type", "category",
            "account_name", "status"
        ]

    @property
    def header_synonyms(self) -> Dict[str, List[str]]:
        return {
            "transaction_id": ["transaction_id", "txn_id", "id", "reference_no", "voucher_no"],
            "account_name": ["account_name", "account", "ledger", "bank_account", "account_title"],
            "category": ["category", "expense_type", "income_type", "ledger_group", "classification"],
            "transaction_type": ["transaction_type", "type", "credit_debit", "direction", "dr_cr"],
            "amount": ["amount", "value", "net_amount", "total", "sum", "debit", "credit"],
            "currency": ["currency", "curr", "currency_code"],
            "vendor_customer": ["vendor_customer", "party", "payee", "payer", "vendor", "customer", "description"],
            "invoice_number": ["invoice_number", "bill_no", "ref_no", "invoice_no"],
            "payment_method": ["payment_method", "mode", "payment_mode", "cash_bank"],
            "tax_amount": ["tax_amount", "vat", "gst", "tax"],
            "status": ["status", "state", "cleared", "reconciled"],
            "transaction_date": ["transaction_date", "date", "txn_date", "entry_date", "posting_date"]
        }

    def row_to_text(self, row: dict) -> str:
        def safe_str(val):
            return str(val).strip() if pd.notna(val) and str(val).strip() != "" else None

        fields = [
            ("transaction_id", "Txn ID", "#"),
            ("transaction_date", "Date", ""),
            ("account_name", "Account", ""),
            ("category", "Category", ""),
            ("transaction_type", "Type", ""),
            ("amount", "Amount", "$"),
            ("vendor_customer", "Party", ""),
            ("invoice_number", "Invoice Ref", ""),
            ("status", "Status", "")
        ]

        parts = []
        for key, label, prefix in fields:
            val = safe_str(row.get(key))
            if val:
                if key == "transaction_type":
                    val = val.capitalize()
                parts.append(f"{label}: {prefix}{val}")

        sentence = ". ".join(parts)
        if not sentence.endswith("."):
            sentence += "."
        return sentence

# Automatically register
registry.register(FinanceDomainPack())
