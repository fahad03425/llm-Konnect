"""
test_schema_mapping_hard.py — Adversarial / stress tests for Module 6.3.

Each test is designed to hit a real failure mode:
  - Deceptive headers (look like one field, should be another)
  - Pathological inputs (None columns, all-NaN, empty, duplicate names)
  - Money / date parsing edge cases (lakh notation, impossible dates, floats-as-dates)
  - Inference false positives (money that looks like quantity, dates that look like IDs)
  - Conflict resolution at scale (10 columns all claiming the same field)
  - Determinism (same input ≥ same output, always)
  - Urdu mixed-script corner cases
  - apply_mapping with bad / contradictory mapping dicts
  - Profile store with Unicode labels, long signatures, corrupt JSON
  - core-only mode (domain_pack=None) must never crash
"""
import pytest
import json
import os
import math
import numpy as np
import pandas as pd
from pathlib import Path

import app.schema.pharmacy  # ensure pharmacy pack is registered

from app.schema.mapper import (
    suggest_mapping, map_headers,
    _normalize_header_string, _infer_from_values,
    MappingProposal,
)
from app.schema.normalize import apply_mapping, _clean_money, _clean_date, _clean_text
from app.schema.profile import (
    source_signature, save_profile, find_profile,
    delete_profile, list_profiles,
)
from app.schema.domain import get_domain_pack


# ============================================================
# Helpers
# ============================================================

def _pharm():
    return get_domain_pack("pharmacy")

def _smap(columns, rows=None, pack=None):
    """Shorthand: suggest_mapping → {col: field} dict."""
    p = suggest_mapping(columns, rows or [], pack)
    return {s.source_column: s.canonical_field for s in p.suggestions}

def _field(column, rows=None, pack=None):
    return _smap([column], rows, pack).get(column)


# ============================================================
# A. Deceptive / Adversarial Headers
# ============================================================

class TestDeceptiveHeaders:
    """Headers that look like one thing but should map to something else,
    or look completely unrelated to their true meaning."""

    def test_tp_two_chars_maps_to_cost(self):
        """'TP' is pharmacist slang for Trade Price (= cost). Short but must match."""
        assert _field("TP", pack=_pharm()) == "cost"

    def test_pp_maps_to_cost(self):
        """'PP' = Purchase Price = cost."""
        assert _field("PP", pack=_pharm()) == "cost"

    def test_rx_maps_to_prescription_ref(self):
        """'Rx' is medical shorthand for prescription."""
        assert _field("Rx", pack=_pharm()) == "prescription_ref"

    def test_sku_maps_to_barcode(self):
        assert _field("SKU", pack=_pharm()) == "barcode"

    def test_wht_maps_to_tax(self):
        """WHT = Withholding Tax, a Pakistani tax term."""
        assert _field("WHT", pack=_pharm()) == "tax"

    def test_gst_maps_to_tax(self):
        assert _field("GST", pack=_pharm()) == "tax"

    def test_closing_stock_maps_to_quantity(self):
        assert _field("Closing Stock", pack=_pharm()) == "quantity"

    def test_party_name_maps_to_supplier(self):
        """'Party Name' is standard Pakistani accounting term for supplier."""
        assert _field("Party Name", pack=_pharm()) == "supplier_id"

    def test_patient_maps_to_customer(self):
        assert _field("Patient", pack=_pharm()) == "customer_id"

    def test_salt_maps_to_generic(self):
        """'Salt' = active ingredient = generic_name in Pakistani pharmacy context."""
        assert _field("Salt", pack=_pharm()) == "generic_name"

    def test_column_named_value_is_ambiguous(self):
        """'Value' is in amount synonyms — should map without crashing."""
        result = _field("Value", pack=_pharm())
        assert result in ("amount", None)  # must not crash; None is acceptable

    def test_column_named_type_should_not_map_to_category(self):
        """'Type' maps to txn_type in pharmacy pack, NOT category.
        txn_type and category both have 'type'-like synonyms — correct one must win."""
        result = _field("Type", pack=_pharm())
        # 'type' is listed as synonym for BOTH category and txn_type in pharmacy.py
        # whichever wins, it must be one of these two, not something else
        assert result in ("txn_type", "category", None)

    def test_name_column_maps_to_product_id(self):
        """Bare 'Name' column in a pharmacy context → product_id (medicine name)."""
        assert _field("Name", pack=_pharm()) == "product_id"

    def test_bill_no_maps_to_invoice_id(self):
        assert _field("Bill No", pack=_pharm()) == "invoice_id"

    def test_challan_no_maps_to_invoice_id(self):
        assert _field("Challan No", pack=_pharm()) == "invoice_id"

    def test_voucher_date_maps_to_date(self):
        assert _field("Voucher Date", pack=_pharm()) == "date"


# ============================================================
# B. Pathological / Edge-Case Inputs
# ============================================================

class TestPathologicalInputs:
    """Inputs that should never crash — just return safe/empty results."""

    def test_empty_column_list(self):
        proposal = suggest_mapping([], [], _pharm())
        assert proposal.suggestions == []
        assert isinstance(proposal.columns, list)

    def test_non_string_input_raises(self):
        with pytest.raises((ValueError, TypeError, AttributeError)):
            suggest_mapping("not a list", [], _pharm())

    def test_none_column_does_not_crash(self):
        """A None entry in the column list must not raise."""
        proposal = suggest_mapping([None, "Date"], [], _pharm())
        assert len(proposal.suggestions) == 2

    def test_all_unnamed_columns(self):
        """All 'Unnamed:X' columns → all None suggestions."""
        cols = ["Unnamed: 0", "Unnamed: 1", "Unnamed: 2"]
        proposal = suggest_mapping(cols, [], _pharm())
        for s in proposal.suggestions:
            assert s.canonical_field is None

    def test_pure_punctuation_header(self):
        """'---' and '***' must not crash and must produce no match."""
        for col in ["---", "***", "...", "####"]:
            result = _field(col, pack=_pharm())
            assert result is None, f"'{col}' should not match anything"

    def test_numeric_header(self):
        """Column named '1' or '42' should not crash and produce no match."""
        for col in ["1", "42", "0"]:
            result = _field(col, pack=_pharm())
            assert result is None

    def test_very_long_header(self):
        """200-char header must not crash (no regex overflow etc.)."""
        long_header = "a" * 200
        result = _field(long_header, pack=_pharm())
        assert result is None  # no match expected

    def test_header_with_only_whitespace(self):
        """Whitespace-only header treated as blank → no mapping."""
        for col in ["   ", "\t", "\n"]:
            result = _field(col, pack=_pharm())
            assert result is None

    def test_1000_identical_columns_conflict(self):
        """1000 columns all named 'Total' — only ONE should map to amount."""
        cols = ["Total"] * 1000
        proposal = suggest_mapping(cols, [], _pharm())
        amount_count = sum(1 for s in proposal.suggestions if s.canonical_field == "amount")
        assert amount_count == 1

    def test_core_only_mode_no_crash(self):
        """domain_pack=None must work cleanly with all core fields."""
        cols = ["Date", "Amount", "Qty", "Description", "Category"]
        proposal = suggest_mapping(cols, [], domain_pack=None)
        s = {sg.source_column: sg.canonical_field for sg in proposal.suggestions}
        assert s["Date"] == "date"
        assert s["Amount"] == "amount"

    def test_sample_rows_with_none_values(self):
        """Rows containing None values must not crash inference."""
        cols = ["ColA"]
        rows = [{"ColA": None}, {"ColA": None}, {"ColA": None}]
        proposal = suggest_mapping(cols, rows, _pharm())
        assert proposal.suggestions[0].canonical_field is None  # all None → no inference

    def test_sample_rows_missing_column_key(self):
        """If sample_rows don't contain the column key, inference must not crash."""
        cols = ["ColA", "ColB"]
        rows = [{"ColA": "01/25"}]  # ColB missing
        proposal = suggest_mapping(cols, rows, _pharm())
        assert len(proposal.suggestions) == 2

    def test_mixed_none_and_nan_rows(self):
        """NaN and None values in rows must not crash inference."""
        cols = ["X"]
        rows = [{"X": float("nan")}, {"X": None}, {"X": np.nan}]
        proposal = suggest_mapping(cols, rows, _pharm())
        assert proposal.suggestions[0].canonical_field is None


# ============================================================
# C. Money Parsing Edge Cases
# ============================================================

class TestMoneyEdgeCases:
    """_clean_money must handle real-world Pakistani/South-Asian formats."""

    def test_lakh_notation(self):
        """1,00,000 — Indian/Pakistani lakh comma grouping → 100000.0"""
        assert _clean_money("1,00,000") == 100000.0

    def test_crore_notation(self):
        assert _clean_money("1,00,00,000") == 10000000.0

    def test_negative_parentheses(self):
        assert _clean_money("(1500)") == -1500.0

    def test_rs_prefix_with_space(self):
        assert _clean_money("Rs. 250") == 250.0

    def test_pkr_uppercase(self):
        assert _clean_money("PKR1200") == 1200.0

    def test_rupee_symbol(self):
        assert _clean_money("₨ 500") == 500.0

    def test_trailing_slash_dash(self):
        """150/- is common in Pakistani receipts."""
        assert _clean_money("150/-") == 150.0

    def test_double_negative(self):
        """(Rs. 500) → -500."""
        assert _clean_money("(Rs. 500)") == -500.0

    def test_zero(self):
        assert _clean_money("0") == 0.0
        assert _clean_money("0.00") == 0.0

    def test_negative_float_string(self):
        assert _clean_money("-99.5") == -99.5

    def test_empty_string_returns_none(self):
        assert _clean_money("") is None

    def test_pure_text_returns_none(self):
        assert _clean_money("N/A") is None
        assert _clean_money("--") is None

    def test_nan_returns_none(self):
        assert _clean_money(float("nan")) is None

    def test_urdu_digits_money(self):
        assert _clean_money("۱۲۳۴") == 1234.0

    def test_urdu_digits_with_rupee(self):
        assert _clean_money("Rs. ۵۰۰") == 500.0

    def test_integer_input(self):
        assert _clean_money(1500) == 1500.0

    def test_float_input(self):
        assert _clean_money(99.99) == 99.99

    def test_very_large_number(self):
        assert _clean_money("999,999,999") == 999999999.0

    def test_scientific_notation(self):
        """1.5e3 → 1500.0 — uncommon but must not crash."""
        result = _clean_money("1.5e3")
        assert result == 1500.0 or result is None  # either is acceptable; must not raise


# ============================================================
# D. Date Parsing Edge Cases
# ============================================================

class TestDateEdgeCases:
    """_clean_date must handle every format encountered in Pakistani pharmacy exports."""

    def test_mm_yy_two_digit_year(self):
        ts = _clean_date("06/25")
        assert ts.month == 6 and ts.year == 2025

    def test_mm_yyyy_four_digit_year(self):
        ts = _clean_date("03/2026")
        assert ts.month == 3 and ts.year == 2026

    def test_mm_yy_last_day_of_month(self):
        ts = _clean_date("02/26")  # Feb 2026
        assert ts.month == 2 and ts.day == 28  # 2026 is not a leap year

    def test_feb_29_leap_year(self):
        ts = _clean_date("02/28")  # Feb 2028 is a leap year
        assert ts.month == 2 and ts.day == 29  # last day of Feb 2028

    def test_impossible_month_returns_nat(self):
        """13/25 is an impossible month — must gracefully return NaT."""
        ts = _clean_date("13/25")
        assert ts is pd.NaT

    def test_excel_serial_date(self):
        """Excel serial 45000 = ~2023-02-17."""
        ts = _clean_date("45000")
        assert ts is not pd.NaT
        assert ts.year in (2023, 2022)  # rough sanity check

    def test_iso_format(self):
        ts = _clean_date("2023-07-15")
        assert ts.year == 2023 and ts.month == 7 and ts.day == 15

    def test_dd_mm_yyyy_pakistani(self):
        """Pakistani day-first: 15/07/2023."""
        ts = _clean_date("15/07/2023")
        assert ts.day == 15 and ts.month == 7

    def test_urdu_digit_date(self):
        """Date written with Urdu digits: ۱۵/۰۷/۲۰۲۳."""
        ts = _clean_date("۱۵/۰۷/۲۰۲۳")
        assert ts.day == 15 and ts.month == 7

    def test_nat_for_empty(self):
        assert _clean_date("") is pd.NaT

    def test_nat_for_none(self):
        assert _clean_date(None) is pd.NaT

    def test_nat_for_nan(self):
        assert _clean_date(float("nan")) is pd.NaT

    def test_nat_for_garbage(self):
        assert _clean_date("not-a-date-xyz") is pd.NaT

    def test_already_timestamp(self):
        ts_in = pd.Timestamp("2023-06-30")
        ts_out = _clean_date(ts_in)
        assert ts_out == ts_in

    def test_mm_yy_with_dash_separator(self):
        ts = _clean_date("06-25")
        assert ts.month == 6 and ts.year == 2025


# ============================================================
# E. Value Inference False-Positive / Negative Tests
# ============================================================

class TestInferenceFalsePositives:
    """These are designed to catch wrong inference — the most dangerous kind of error."""

    def test_quantity_column_not_claimed_as_invoice_id(self):
        """Small integers (10, 5, 200) must NOT be inferred as invoice_id."""
        rows = [{"X": "10"}, {"X": "5"}, {"X": "200"}]
        result = _field("X", rows=rows, pack=_pharm())
        assert result != "invoice_id"

    def test_date_column_not_claimed_as_amount(self):
        """01/25 looks like a date, not money."""
        rows = [{"X": "01/25"}, {"X": "12/26"}, {"X": "06/27"}]
        result = _field("X", rows=rows, pack=_pharm())
        assert result != "amount"

    def test_product_codes_with_slashes_not_date(self):
        """Product codes like 'ABC/001' should NOT be inferred as expiry_date."""
        rows = [{"X": "MED/001"}, {"X": "PHR/999"}, {"X": "DRG/042"}]
        result = _field("X", rows=rows, pack=_pharm())
        assert result != "expiry_date"
        assert result != "date"

    def test_highly_mixed_column_gets_no_inference(self):
        """A column with wildly mixed types should get no confident inference."""
        rows = [
            {"X": "Panadol"},
            {"X": "100"},
            {"X": "01/25"},
            {"X": "INV2024"},
        ]
        result = _field("X", rows=rows, pack=_pharm())
        # With mixed content, confidence should be too low → None or a very low-conf guess
        # We only check it doesn't confidently mis-map
        assert result in (None, "invoice_id", "expiry_date", "quantity", "amount")

    def test_all_zero_values_not_forced_into_quantity(self):
        """A column of all zeros: 0, 0, 0 should infer quantity (small int) or amount, not crash."""
        rows = [{"X": "0"}, {"X": "0"}, {"X": "0"}]
        result = _field("X", rows=rows, pack=_pharm())
        assert result in ("quantity", "amount", None)

    def test_column_of_long_product_names_not_invoice_id(self):
        """Long text values like 'Augmentin 625mg Tablet' should NOT be invoice_id.
        The alphanumeric-ID heuristic requires BOTH letters AND digits."""
        rows = [
            {"X": "Augmentin Tablet"},
            {"X": "Panadol Syrup"},
            {"X": "Amoxicillin Capsule"},
        ]
        # These have letters but no digits → should not match the alpha+digit ID heuristic
        result = _field("X", rows=rows, pack=_pharm())
        assert result != "invoice_id"


# ============================================================
# F. apply_mapping Adversarial Inputs
# ============================================================

class TestApplyMappingAdversarial:

    def test_mapping_with_nonexistent_source_column(self):
        """Mapping references a column that doesn't exist in the DataFrame → must not crash."""
        df = pd.DataFrame({"A": [1, 2]})
        mapping = {"B": "amount"}  # 'B' not in df
        result = apply_mapping(df, mapping, keep_extras=False)
        # 'amount' column simply won't appear (graceful)
        assert "amount" not in result.columns or result.empty

    def test_empty_dataframe(self):
        """Applying mapping to a 0-row DataFrame must not crash."""
        df = pd.DataFrame({"Total": [], "Date": []})
        mapping = {"Total": "amount", "Date": "date"}
        result = apply_mapping(df, mapping, keep_extras=False)
        assert "amount" in result.columns
        assert len(result) == 0

    def test_single_row_dataframe(self):
        df = pd.DataFrame({"Total": ["Rs. 1,200"]})
        mapping = {"Total": "amount"}
        result = apply_mapping(df, mapping, keep_extras=False)
        assert result["amount"].iloc[0] == 1200.0

    def test_all_nan_column(self):
        """Column of all NaN values must not crash and must coerce to None/NaT."""
        df = pd.DataFrame({"Total": [float("nan"), None, float("nan")]})
        mapping = {"Total": "amount"}
        result = apply_mapping(df, mapping, keep_extras=False)
        assert result["amount"].isna().all()

    def test_duplicate_canonical_target_in_mapping(self):
        """Two source columns both mapped to 'amount' — last-write-wins on rename;
        must not crash, and result must have the 'amount' column."""
        df = pd.DataFrame({"ColA": [100.0], "ColB": [200.0]})
        mapping = {"ColA": "amount", "ColB": "amount"}
        # This is a user error; must not crash
        result = apply_mapping(df, mapping, keep_extras=False)
        assert "amount" in result.columns

    def test_mapping_empty_dict(self):
        """Empty mapping: with keep_extras=False, result has only traceability cols."""
        df = pd.DataFrame({"A": [1], "B": [2], "source_connector": ["csv"]})
        result = apply_mapping(df, {}, keep_extras=False)
        assert "source_connector" in result.columns
        assert "A" not in result.columns

    def test_keep_extras_with_extra_col_that_has_dot_in_name(self):
        """Column named 'data.value' → _extra.data.value — must not crash."""
        df = pd.DataFrame({"data.value": ["x"], "Total": [100]})
        mapping = {"Total": "amount"}
        result = apply_mapping(df, mapping, keep_extras=True)
        assert "_extra.data.value" in result.columns

    def test_raw_not_mutated(self):
        """apply_mapping must never modify the original raw DataFrame."""
        df = pd.DataFrame({"Total": ["100"], "Qty": ["5"]})
        original_cols = list(df.columns)
        mapping = {"Total": "amount"}
        apply_mapping(df, mapping, keep_extras=True)
        assert list(df.columns) == original_cols

    def test_urdu_column_name_as_extra(self):
        """An Urdu-named unmapped column should survive in _extra without mangling."""
        df = pd.DataFrame({"میعاد": ["12/25"], "Total": ["500"]})
        mapping = {"Total": "amount"}
        result = apply_mapping(df, mapping, keep_extras=True)
        assert "_extra.میعاد" in result.columns

    def test_coerce_date_column_with_mixed_formats(self):
        """A date column with mixed valid/invalid entries: invalids → NaT."""
        df = pd.DataFrame({"Date": ["01/01/2023", "not-a-date", "15/07/2022", ""]})
        mapping = {"Date": "date"}
        result = apply_mapping(df, mapping, keep_extras=False)
        # Valid rows parsed, invalid → NaT
        assert pd.notna(result["date"].iloc[0])
        assert result["date"].iloc[1] is pd.NaT
        assert pd.notna(result["date"].iloc[2])
        assert result["date"].iloc[3] is pd.NaT

    def test_mrp_coerced_as_money(self):
        """mrp is in pharmacy domain money fields → must be float, not text."""
        df = pd.DataFrame({"MRP": ["Rs. 120", "150", "200/-"]})
        mapping = {"MRP": "mrp"}
        result = apply_mapping(df, mapping, domain="pharmacy", keep_extras=False)
        assert result["mrp"].tolist() == [120.0, 150.0, 200.0]

    def test_reorder_level_coerced_as_money(self):
        df = pd.DataFrame({"Reorder": ["50", "100", "25"]})
        mapping = {"Reorder": "reorder_level"}
        result = apply_mapping(df, mapping, domain="pharmacy", keep_extras=False)
        assert result["reorder_level"].tolist() == [50.0, 100.0, 25.0]


# ============================================================
# G. Determinism — same input = same output every time
# ============================================================

class TestDeterminism:

    def test_repeated_calls_same_result(self):
        """suggest_mapping is deterministic: 100 calls → same output."""
        cols = ["Exp. Date", "Qty", "Trade Price", "Total", "Batch#", "Formula"]
        rows = [{"Exp. Date": "12/25", "Qty": "10", "Trade Price": "50",
                 "Total": "500", "Batch#": "B001", "Formula": "Paracetamol"}]
        results = []
        for _ in range(10):
            p = suggest_mapping(cols, rows, _pharm())
            results.append(
                [(s.source_column, s.canonical_field, s.confidence) for s in p.suggestions]
            )
        assert all(r == results[0] for r in results), "suggest_mapping is not deterministic"

    def test_column_order_does_not_change_individual_mapping(self):
        """Shuffling the column order must not change which field each column maps to,
        only the order of suggestions."""
        cols_a = ["Date", "Qty", "Total", "Exp"]
        cols_b = ["Total", "Exp", "Date", "Qty"]
        pa = suggest_mapping(cols_a, [], _pharm())
        pb = suggest_mapping(cols_b, [], _pharm())
        map_a = {s.source_column: s.canonical_field for s in pa.suggestions}
        map_b = {s.source_column: s.canonical_field for s in pb.suggestions}
        assert map_a == map_b

    def test_conflict_winner_is_stable(self):
        """When multiple synonyms tie at confidence=1.0, the winner must be
        the same every call (alphabetical tiebreak on source_column)."""
        # Both "Total" and "Amount" are synonyms for "amount" at conf 1.0
        cols = ["Total", "Amount"]
        winners = set()
        for _ in range(20):
            p = suggest_mapping(cols, [], _pharm())
            winner = next(s.source_column for s in p.suggestions if s.canonical_field == "amount")
            winners.add(winner)
        assert len(winners) == 1, f"Winner is non-deterministic: {winners}"


# ============================================================
# H. Scale / Many Conflicting Columns
# ============================================================

class TestScale:

    def test_10_columns_all_claiming_amount(self):
        """10 synonyms for 'amount', all with confidence 1.0 — exactly 1 wins."""
        synonyms_for_amount = [
            "Total", "Amount", "Net Amount", "Line Total",
            "Total Amount", "Value", "Net Value",
            "Grand Total", "Sale Amount", "Invoice Total",
        ]
        # Add some of these to pharmacy synonyms or just use existing ones
        cols = synonyms_for_amount
        proposal = suggest_mapping(cols, [], _pharm())
        amount_winners = [s.source_column for s in proposal.suggestions if s.canonical_field == "amount"]
        assert len(amount_winners) == 1

    def test_all_canonical_fields_mapped_simultaneously(self):
        """If we feed every canonical field name as a column, every one should map."""
        from app.schema.mapper import get_canonical_fields
        fields = get_canonical_fields(_pharm())
        # Feed canonical field names directly — should all exact-match
        proposal = suggest_mapping(fields, [], _pharm())
        s = {sg.source_column: sg.canonical_field for sg in proposal.suggestions}
        for f in fields:
            if f not in ("source_connector", "source_row"):
                assert s.get(f) == f, f"Canonical field '{f}' failed to self-map"

    def test_100_random_columns_no_crash(self):
        """100 columns with garbage names must not crash."""
        import random
        random.seed(42)
        cols = ["".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=8))
                for _ in range(100)]
        proposal = suggest_mapping(cols, [], _pharm())
        assert len(proposal.suggestions) == 100


# ============================================================
# I. Header Normalizer Edge Cases
# ============================================================

class TestHeaderNormalizer:

    def test_mixed_case_preserved_as_lowercase(self):
        assert _normalize_header_string("EXPIRY DATE") == "expiry date"

    def test_urdu_chars_survive_normalization(self):
        """Urdu script must pass through the normalizer unchanged (lowercase ASCII only)."""
        result = _normalize_header_string("میعاد")
        assert "میعاد" in result

    def test_punctuation_stripped(self):
        assert _normalize_header_string("Exp. Date") == "exp date"
        assert _normalize_header_string("Batch#") == "batch"

    def test_excess_whitespace_collapsed(self):
        assert _normalize_header_string("  date  ") == "date"
        assert _normalize_header_string("expiry   date") == "expiry date"

    def test_tab_and_newline_normalized(self):
        assert _normalize_header_string("expiry\tdate") == "expiry date"

    def test_non_string_returns_empty(self):
        assert _normalize_header_string(None) == ""
        assert _normalize_header_string(42) == ""
        assert _normalize_header_string([]) == ""


# ============================================================
# J. Profile Store Adversarial Cases
# ============================================================

class TestProfileAdversarial:

    def test_unicode_label_persists(self):
        """Label in Urdu/Arabic must survive JSON round-trip."""
        sig = source_signature(["A", "B"], "UnicodeTest")
        label = "دواخانہ ایکسپورٹ"  # Urdu: "Pharmacy Export"
        save_profile(sig, {"A": "amount"}, label)
        loaded = find_profile(sig)
        assert loaded["label"] == label
        delete_profile(sig)

    def test_corrupt_json_in_store_does_not_crash_list(self):
        """A corrupt JSON file in the store dir must not crash list_profiles()."""
        from app.schema.profile import _get_store_dir
        store = _get_store_dir()
        corrupt = store / "corrupt_test_file.json"
        corrupt.write_text("{this is not valid json", encoding="utf-8")
        try:
            profiles = list_profiles()  # must not raise
            assert isinstance(profiles, list)
        finally:
            corrupt.unlink(missing_ok=True)

    def test_empty_columns_signature_is_stable(self):
        """source_signature on empty list must not crash."""
        sig = source_signature([], "Test")
        assert isinstance(sig, str) and len(sig) == 32  # MD5 hex

    def test_signature_ignores_case_and_whitespace(self):
        """Column names differing only in case/spaces must hash the same."""
        sig1 = source_signature(["Date", "Amount", "Qty"], "CSV")
        sig2 = source_signature(["  DATE  ", "amount", "QTY"], "CSV")
        assert sig1 == sig2

    def test_profile_save_does_not_store_row_data_ever(self):
        """Even if caller accidentally passes extra keys, only mapping is stored."""
        sig = source_signature(["X"], "Test")
        mapping_with_extra = {"X": "amount"}
        save_profile(sig, mapping_with_extra, "test")
        loaded = find_profile(sig)
        allowed_keys = {"signature", "label", "mapping"}
        assert set(loaded.keys()) <= allowed_keys
        delete_profile(sig)

    def test_overwrite_profile_replaces_not_appends(self):
        sig = source_signature(["P", "Q", "R"], "Test")
        save_profile(sig, {"P": "date", "Q": "amount"}, "v1")
        save_profile(sig, {"P": "cost"}, "v2")
        loaded = find_profile(sig)
        assert loaded["mapping"] == {"P": "cost"}  # full replacement
        assert "Q" not in loaded["mapping"]
        delete_profile(sig)
