from typing import Dict, List, Any, Optional
import datetime
import pandas as pd

from app.schema.domain import DomainPack, Problem, registry


class EcommerceDomainPack(DomainPack):
    """
    E-commerce Store Domain Pack.
    Provides standard entity modeling, synonyms, validation rules, and
    text generation for online stores (Shopify, WooCommerce, Amazon, Stripe, POS).
    """

    @property
    def name(self) -> str:
        return "ecommerce"

    @property
    def extra_fields(self) -> List[str]:
        return [
            "order_id", "product_sku", "product_name", "category",
            "sale_amount", "gross_amount", "net_amount", "discount_amount",
            "refund_amount", "shipping_amount", "tax_amount",
            "cost_per_item", "unit_price", "quantity",
            "customer_id", "customer_name", "customer_email",
            "shipping_city", "shipping_country", "payment_gateway",
            "payment_status", "fulfillment_status", "rating", "review_text",
            "order_date"
        ]

    @property
    def searchable_fields(self) -> List[str]:
        return [
            "order_id", "product_sku", "product_name", "category",
            "customer_id", "customer_name", "customer_email",
            "shipping_city", "payment_gateway", "payment_status",
            "fulfillment_status", "review_text"
        ]

    @property
    def filter_metadata_fields(self) -> List[str]:
        return [
            "order_date", "order_id", "product_sku", "sale_amount",
            "net_amount", "refund_amount", "quantity", "cost_per_item",
            "payment_status", "fulfillment_status", "payment_gateway",
            "category", "shipping_city", "rating"
        ]

    @property
    def header_synonyms(self) -> Dict[str, List[str]]:
        return {
            "order_id": [
                "order_id", "order_no", "order_number", "order_name",
                "name", "transaction_id", "receipt_id", "invoice_no"
            ],
            "order_date": [
                "order_date", "date", "created_at", "order_timestamp",
                "order_time", "processed_at", "timestamp", "datetime"
            ],
            "product_sku": [
                "product_sku", "sku", "lineitem_sku", "item_code",
                "product_code", "variant_sku", "barcode"
            ],
            "product_name": [
                "product_name", "title", "product_title", "lineitem_name",
                "item_name", "item_description", "description", "variant_title"
            ],
            "category": [
                "category", "product_type", "type", "department",
                "collection", "tag", "product_category"
            ],
            "sale_amount": [
                "sale_amount", "total", "total_price", "amount",
                "gross_sales", "lineitem_price", "total_sales"
            ],
            "gross_amount": [
                "gross_amount", "subtotal", "subtotal_price", "sub_total",
                "base_amount", "undiscounted_subtotal"
            ],
            "net_amount": [
                "net_amount", "net_sales", "net_payment", "settlement_amount",
                "net_total"
            ],
            "discount_amount": [
                "discount_amount", "discount", "total_discounts", "coupon_discount",
                "voucher_discount", "promo_discount"
            ],
            "refund_amount": [
                "refund_amount", "refunds", "refunded_amount", "return_amount",
                "credit_note_amount"
            ],
            "shipping_amount": [
                "shipping_amount", "shipping_fee", "shipping_price",
                "shipping_charges", "freight"
            ],
            "tax_amount": [
                "tax_amount", "tax", "total_tax", "vat", "gst"
            ],
            "cost_per_item": [
                "cost_per_item", "cost", "cogs", "unit_cost", "cost_price",
                "purchase_price", "supplier_price"
            ],
            "unit_price": [
                "unit_price", "price", "variant_price", "rate", "selling_price"
            ],
            "quantity": [
                "quantity", "qty", "lineitem_quantity", "units", "items_count", "sold_qty"
            ],
            "customer_id": [
                "customer_id", "user_id", "buyer_id", "client_id", "account_id"
            ],
            "customer_name": [
                "customer_name", "billing_name", "shipping_name", "customer",
                "buyer_name", "client_name"
            ],
            "customer_email": [
                "customer_email", "email", "billing_email", "buyer_email",
                "contact_email"
            ],
            "shipping_city": [
                "shipping_city", "city", "delivery_city", "destination_city",
                "shipping_address_city"
            ],
            "shipping_country": [
                "shipping_country", "country", "shipping_address_country", "region"
            ],
            "payment_gateway": [
                "payment_gateway", "gateway", "payment_method", "source_name",
                "processor"
            ],
            "payment_status": [
                "payment_status", "financial_status", "payment_state", "paid"
            ],
            "fulfillment_status": [
                "fulfillment_status", "shipping_status", "status", "delivery_status",
                "order_status"
            ],
            "rating": [
                "rating", "score", "review_rating", "stars"
            ],
            "review_text": [
                "review_text", "review", "comment", "feedback", "body"
            ]
        }

    @property
    def kpi_question_rules(self) -> List[tuple]:
        """
        E-commerce question vocabulary for the chatbot's numeric route.
        Delegates lazily to the domain pack so no domain vocabulary leaks into KPI engine core.
        """
        try:
            from app.analytics.domains.ecommerce import ECOMMERCE_QUESTION_RULES
            return list(ECOMMERCE_QUESTION_RULES)
        except ImportError:
            return []

    @property
    def report_sections(self) -> List[str]:
        return [
            "ecommerce_financials",
            "order_velocity",
            "product_profitability",
            "refund_risk",
            "customer_retention"
        ]

    def register_kpis(self, engine) -> None:
        """
        Attach the E-Commerce domain KPIs to the KPI engine lazily.
        """
        try:
            from app.analytics.domains.ecommerce import register as register_ecommerce_kpis
            register_ecommerce_kpis(engine, domain=self.name)
        except ImportError:
            pass

    def row_to_text(self, row: dict) -> str:
        """
        Convert a canonical row into an explicit, labeled sentence for semantic search and RAG.
        """
        def safe_str(val):
            return str(val).strip() if pd.notna(val) and str(val).strip() != "" else None

        fields = [
            ("order_id", "Order", "#"),
            ("order_date", "Date", ""),
            ("product_name", "Product", ""),
            ("product_sku", "SKU", ""),
            ("category", "Category", ""),
            ("quantity", "Qty", ""),
            ("unit_price", "Unit Price", "$"),
            ("sale_amount", "Total", "$"),
            ("cost_per_item", "Cost", "$"),
            ("discount_amount", "Discount", "$"),
            ("refund_amount", "Refund", "$"),
            ("customer_name", "Customer", ""),
            ("customer_email", "Email", ""),
            ("shipping_city", "City", ""),
            ("payment_gateway", "Gateway", ""),
            ("payment_status", "Payment", ""),
            ("fulfillment_status", "Fulfillment", ""),
            ("rating", "Rating", "★"),
            ("review_text", "Review", "")
        ]

        parts = []
        for key, label, prefix in fields:
            val = safe_str(row.get(key))
            if val:
                parts.append(f"{label}: {prefix}{val}")

        sentence = ". ".join(parts)
        if sentence and not sentence.endswith("."):
            sentence += "."
        return sentence or "Empty e-commerce record."

    def validate_row(self, row: dict, row_idx: int) -> List[Problem]:
        """
        Domain-specific validation checks for e-commerce records.
        """
        problems = []

        # Validate sale amount / unit price
        sale_amt = row.get("sale_amount")
        if pd.notna(sale_amt):
            try:
                amt_val = float(sale_amt)
                if amt_val < 0:
                    problems.append(Problem(
                        field="sale_amount",
                        row_idx=row_idx,
                        message=f"Negative sale amount ({amt_val}) on non-refund record"
                    ))
            except (ValueError, TypeError):
                problems.append(Problem(
                    field="sale_amount",
                    row_idx=row_idx,
                    message="Invalid numeric format for sale_amount"
                ))

        # Validate rating if present
        rating = row.get("rating")
        if pd.notna(rating):
            try:
                r_val = float(rating)
                if r_val < 1 or r_val > 5:
                    problems.append(Problem(
                        field="rating",
                        row_idx=row_idx,
                        message=f"Review rating {r_val} outside expected 1-5 range"
                    ))
            except (ValueError, TypeError):
                pass

        return problems


# Automatically register the Ecommerce domain pack
registry.register(EcommerceDomainPack())
