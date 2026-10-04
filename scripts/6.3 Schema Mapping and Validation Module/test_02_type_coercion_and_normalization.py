"""Test Suite 02: Type Coercion, Currency Normalization & Date Cleaning.

Module: Module 6.3 — Schema Mapping and Validation Module
Target File: backend/app/schema/normalize.py (functions: apply_mapping, normalize, _clean_money, _clean_date, _convert_urdu_digits)
Scope:
- Verifies Urdu / Eastern Arabic numeral transliteration to ASCII digits.
- Verifies financial string cleaning (accounting parentheses negation, currency prefixes, trailing /-).
- Verifies robust date parsing (Pakistani day-first DD/MM/YYYY, Excel serial numbers, ISO strings, Urdu digits).
- Verifies full mapping application and schema normalization over raw DataFrames.
- Verifies retention of unmapped extra columns under _extra. namespace when keep_extras=True.
"""

import pandas as pd
import numpy as np
import pytest
from app.schema.normalize import apply_mapping, _clean_money, _clean_date, _convert_urdu_digits


class TestTypeCoercionAndNormalization:
    """Verifies parsing of localized currencies, dates, and numerals into canonical types."""

    def test_convert_urdu_numerals(self):
        """Transliterates Eastern Arabic/Urdu numerals to ASCII standard digits."""
        assert _convert_urdu_digits("۰۱۲۳۴۵۶۷۸۹") == "0123456789"
        assert _convert_urdu_digits("Rs. ۱۲۵۰/-") == "Rs. 1250/-"
        assert _convert_urdu_digits("۱۵/۰۱/۲۰۲۶") == "15/01/2026"
        assert _convert_urdu_digits(12345) == 12345  # Pass-through non-strings

    def test_clean_money_accounting_conventions(self):
        """Parses currencies, accounting parentheses negation, and Pakistani pricing formats."""
        # Standard clean float
        assert _clean_money(150.50) == 150.50
        assert _clean_money("150.50") == 150.50

        # Parentheses accounting notation indicates negative amount
        assert _clean_money("(500.00)") == -500.00
        assert _clean_money("(1,250)") == -1250.00

        # Currency prefixes and suffixes
        assert _clean_money("Rs. 1,500/-") == 1500.00
        assert _clean_money("Rs 450/=") == 450.00
        assert _clean_money("PKR 2,400.75") == 2400.75
        assert _clean_money("₨ 999.00") == 999.00

        # Urdu digits in money
        assert _clean_money("Rs. ۱۲۵۰/-") == 1250.00

        # Null / invalid handling
        assert _clean_money(None) is None
        assert _clean_money(np.nan) is None
        assert _clean_money("") is None
        assert _clean_money("N/A") is None

    def test_clean_date_variations(self):
        """Parses ISO, day-first DD/MM/YYYY, Excel serials, and Urdu numeral dates."""
        # ISO format
        dt_iso = _clean_date("2026-01-15")
        assert dt_iso == pd.Timestamp("2026-01-15")

        # Pakistani / UK Day-First format DD/MM/YYYY
        dt_dayfirst = _clean_date("15/01/2026")
        assert dt_dayfirst == pd.Timestamp("2026-01-15")

        dt_hyphen = _clean_date("28-02-2026")
        assert dt_hyphen == pd.Timestamp("2026-02-28")

        # Excel serial date number (e.g. 45678 represents 2025-01-21)
        dt_excel = _clean_date(45678)
        assert isinstance(dt_excel, pd.Timestamp)
        assert dt_excel.year == 2025

        # Urdu digits in dates
        dt_urdu = _clean_date("۱۵/۰۱/۲۰۲۶")
        assert dt_urdu == pd.Timestamp("2026-01-15")

        # Invalid / NaT
        assert pd.isna(_clean_date("not-a-date"))
        assert pd.isna(_clean_date(""))
        assert pd.isna(_clean_date(None))

    def test_apply_mapping_full_normalization(self):
        """Transforms raw messy DataFrame into a canonical DataFrame with typed columns."""
        raw_df = pd.DataFrame({
            "Bill_Date": ["15/01/2026", "16/01/2026"],
            "Medicine_Name": ["Panadol 500mg", "Brufen 400mg"],
            "Sold_Qty": ["10", "5"],
            "Unit_Price": ["Rs. 25.00/-", "Rs. 40.00/-"],
            "Net_Total": ["250.00", "200.00"],
            "Invoice_Num": ["INV-001", "INV-002"],
            "Internal_Store_Code": ["STR-9", "STR-9"],
            "source_connector": ["csv", "csv"],
            "source_row": [2, 3]
        })

        mapping = {
            "Bill_Date": "date",
            "Medicine_Name": "product_id",
            "Sold_Qty": "quantity",
            "Unit_Price": "unit_price",
            "Net_Total": "amount",
            "Invoice_Num": "invoice_id"
        }

        canonical_df = apply_mapping(raw_df, mapping, domain="pharmacy", keep_extras=True)

        assert "date" in canonical_df.columns
        assert "product_id" in canonical_df.columns
        assert "quantity" in canonical_df.columns
        assert "unit_price" in canonical_df.columns
        assert "amount" in canonical_df.columns
        assert "invoice_id" in canonical_df.columns

        # Coerced types
        assert isinstance(canonical_df.loc[0, "date"], pd.Timestamp)
        assert canonical_df.loc[0, "date"] == pd.Timestamp("2026-01-15")
        assert canonical_df.loc[0, "quantity"] == 10.0
        assert canonical_df.loc[0, "unit_price"] == 25.00
        assert canonical_df.loc[0, "amount"] == 250.00

        # Extra column retained with _extra. prefix
        assert "_extra.Internal_Store_Code" in canonical_df.columns
        assert canonical_df.loc[0, "_extra.Internal_Store_Code"] == "STR-9"

        # Traceability retained
        assert canonical_df.loc[0, "source_connector"] == "csv"
        assert canonical_df.loc[0, "source_row"] == 2

    def test_apply_mapping_omit_extras_when_flag_false(self):
        """Unmapped columns are dropped cleanly when keep_extras=False."""
        raw_df = pd.DataFrame({
            "Txn_Date": ["2026-01-01"],
            "Amount": ["100.00"],
            "Internal_Flag": ["IGNORE"]
        })
        mapping = {"Txn_Date": "date", "Amount": "amount"}

        canonical_df = apply_mapping(raw_df, mapping, keep_extras=False)
        assert "_extra.Internal_Flag" not in canonical_df.columns
        assert "Internal_Flag" not in canonical_df.columns
        assert set(canonical_df.columns) == {"date", "amount"}
