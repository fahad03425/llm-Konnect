from typing import Dict, List, Any
import pandas as pd
from app.schema.domain import DomainPack, Problem, registry

class EcommerceDomainPack(DomainPack):
    @property
    def name(self) -> str:
        return "ecommerce"

    @property
    def extra_fields(self) -> List[str]:
        return [
            "order_id", "product_sku", "product_name", "category", 
            "sale_amount", "quantity", "customer_id", "customer_email",
            "shipping_city", "payment_status", "fulfillment_status", 
            "discount_amount", "order_date"
        ]

    @property
    def searchable_fields(self) -> List[str]:
        return [
            "order_id", "product_sku", "product_name", "category",
            "customer_id", "customer_email", "shipping_city"
        ]

    @property
    def filter_metadata_fields(self) -> List[str]:
        return [
            "order_date", "sale_amount", "quantity", "payment_status",
            "fulfillment_status", "category", "shipping_city"
        ]

    @property
    def header_synonyms(self) -> Dict[str, List[str]]:
        return {
            "order_id": ["order_id", "order_no", "order_number", "transaction_id", "receipt_id"],
            "product_sku": ["product_sku", "sku", "item_code", "product_code"],
            "product_name": ["product_name", "title", "item_name", "product_title", "description"],
            "category": ["category", "product_type", "department", "type"],
            "sale_amount": ["sale_amount", "total", "subtotal", "total_price", "amount", "revenue"],
            "quantity": ["quantity", "qty", "items_count", "units"],
            "customer_id": ["customer_id", "user_id", "buyer_id", "client_id"],
            "customer_email": ["customer_email", "email", "buyer_email", "contact_email"],
            "shipping_city": ["shipping_city", "city", "delivery_city", "destination_city"],
            "payment_status": ["payment_status", "paid", "financial_status", "payment_state"],
            "fulfillment_status": ["fulfillment_status", "shipping_status", "status", "delivery_status"],
            "discount_amount": ["discount_amount", "discount", "voucher_discount"],
            "order_date": ["order_date", "date", "created_at", "order_timestamp"]
        }

    def row_to_text(self, row: dict) -> str:
        def safe_str(val):
            return str(val).strip() if pd.notna(val) and str(val).strip() != "" else None

        fields = [
            ("order_id", "Order ID", "#"),
            ("order_date", "Date", ""),
            ("product_name", "Product", ""),
            ("product_sku", "SKU", ""),
            ("category", "Category", ""),
            ("quantity", "Quantity", ""),
            ("sale_amount", "Amount", "$"),
            ("shipping_city", "City", ""),
            ("payment_status", "Payment Status", ""),
            ("fulfillment_status", "Fulfillment", "")
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
registry.register(EcommerceDomainPack())
