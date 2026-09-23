from typing import Dict, List, Any
import pandas as pd
from app.schema.domain import DomainPack, Problem, registry

class HomeFinanceDomainPack(DomainPack):
    @property
    def name(self) -> str:
        return "home_finance"

    @property
    def extra_fields(self) -> List[str]:
        return [
            "transaction_id", "date", "description", "category", 
            "account", "income_or_expense", "amount", "budget_limit", 
            "payment_mode", "notes"
        ]

    @property
    def searchable_fields(self) -> List[str]:
        return [
            "transaction_id", "description", "category", "account", "notes"
        ]

    @property
    def filter_metadata_fields(self) -> List[str]:
        return [
            "date", "amount", "income_or_expense", "category", "account"
        ]

    @property
    def header_synonyms(self) -> Dict[str, List[str]]:
        return {
            "transaction_id": ["transaction_id", "id", "ref"],
            "date": ["date", "txn_date", "post_date", "day"],
            "description": ["description", "payee", "title", "details", "particulars", "merchant"],
            "category": ["category", "expense_category", "tag", "budget_category"],
            "account": ["account", "bank", "card", "wallet", "source"],
            "income_or_expense": ["income_or_expense", "type", "flow", "in_out"],
            "amount": ["amount", "value", "spent", "received", "sum"],
            "budget_limit": ["budget_limit", "budget", "monthly_limit"],
            "payment_mode": ["payment_mode", "method", "card_cash", "mode"],
            "notes": ["notes", "memo", "remarks", "comment"]
        }

    def row_to_text(self, row: dict) -> str:
        def safe_str(val):
            return str(val).strip() if pd.notna(val) and str(val).strip() != "" else None

        fields = [
            ("date", "Date", ""),
            ("description", "Description", ""),
            ("category", "Category", ""),
            ("income_or_expense", "Type", ""),
            ("amount", "Amount", "$"),
            ("account", "Account", ""),
            ("notes", "Notes", "")
        ]

        parts = []
        for key, label, prefix in fields:
            val = safe_str(row.get(key))
            if val:
                parts.append(f"{label}: {prefix}{val}")

        sentence = ". ".join(parts)
        if not sentence.endswith("."):
            sentence += "."
        return sentence

# Automatically register
registry.register(HomeFinanceDomainPack())
