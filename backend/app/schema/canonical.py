from typing import List
from app.schema.domain import Problem

# Core canonical fields (domain-agnostic)
CORE_FIELDS = [
    "date", "description", "category", "quantity", "unit_price", 
    "amount", "cost", "tax", "discount", "invoice_id", "product_id", 
    "customer_id", "supplier_id", "txn_type", "payment_method",
    "source_connector", "source_row" # Traceability columns
]

def validate_core_rules(row: dict, index: int) -> List[Problem]:
    """
    Domain-agnostic core validation rules.
    """
    problems = []
    
    # 1. No fully-empty rows
    # Ignore traceability columns for this check
    data_cols = [k for k in row.keys() if k not in ("source_connector", "source_row") and not k.startswith("Unnamed:")]
    if all(row.get(k) is None or str(row.get(k)).strip() == "" or row.get(k) != row.get(k) for k in data_cols):
        problems.append(Problem("error", "Row is completely empty", index))
        return problems # Stop further checks if empty

    # 2. Numeric fields must be numeric
    numeric_fields = ["quantity", "unit_price", "amount", "cost", "tax", "discount"]
    for field in numeric_fields:
        val = row.get(field)
        # Using string representation to check for valid numbers if it's not already a float/int
        if val is not None and str(val).strip() != "" and val == val: # check for nan
            try:
                float(val)
            except (ValueError, TypeError):
                problems.append(Problem("error", f"Field '{field}' must be a number, got '{val}'", index, field))
                
    # 3. Required fields present (at minimum `date` and `amount` for transactions, or `product_id` for inventory)
    has_txn = (row.get("date") is not None and str(row.get("date")).strip() != "") and \
              (row.get("amount") is not None and str(row.get("amount")).strip() != "")
    has_inv = (row.get("product_id") is not None and str(row.get("product_id")).strip() != "")
    
    if not has_txn and not has_inv:
        problems.append(Problem("error", "Row must have either (date and amount) for transactions, or (product name/id) for inventory", index))

    return problems
