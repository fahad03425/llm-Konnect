"""
Module 6.3 (Part B) — Data Validation: Challenging Test Suite
==============================================================
Covers every core rule (§3) and every pharmacy-pack rule (§4) with at least
one *passing* case (rule should NOT fire) and one *failing* case (rule MUST fire),
plus a bank of adversarial edge cases.

Run with:
    cd backend
    python -m pytest tests/test_validation_hard.py -v
"""

import datetime
import json
import os

import pandas as pd
import pytest

from app.schema.canonical import validate_core_dataframe, _NUMERIC_CORE_FIELDS
from app.schema.domain import (
    CleaningSummary,
    DomainPack,
    Problem,
    ValidationReport,
    registry,
)
from app.schema.pharmacy import PharmacyDomainPack
from app.schema.validate import clean, validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "samples"
)
_MESSY_CSV = os.path.join(_SAMPLES_DIR, "messy_pharmacy.csv")
_SAMPLE_CSV = os.path.join(_SAMPLES_DIR, "sample_pharmacy.csv")


def _codes(problems):
    return [p.code for p in problems]


def _by_code(problems, code):
    return [p for p in problems if p.code == code]


def _make_txn(**kwargs):
    """Return a single-row transaction DataFrame with sensible defaults."""
    base = {"date": pd.Timestamp("2026-01-15"), "amount": 100.0, "source_row": 1}
    base.update(kwargs)
    return pd.DataFrame([base])


def _make_inv(**kwargs):
    """Return a single-row inventory DataFrame with sensible defaults."""
    base = {"product_id": "Panadol 500mg", "quantity": 10.0, "source_row": 1}
    base.update(kwargs)
    return pd.DataFrame([base])


# ===========================================================================
# §3 CORE RULES — each with a PASSING (no-fire) and FAILING (must-fire) case
# ===========================================================================


class TestEmptyRow:
    """EMPTY_ROW: fully-blank rows → error."""

    def test_clean_row_does_not_fire(self):
        df = _make_txn()
        problems = validate_core_dataframe(df)
        assert "EMPTY_ROW" not in _codes(problems)

    def test_completely_empty_row_fires(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": 50.0, "source_row": 1},
            {"date": None, "amount": None, "source_row": 99},
        ])
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "EMPTY_ROW")
        assert p, "EMPTY_ROW should fire"
        assert 99 in p[0].row_refs

    def test_whitespace_only_cells_count_as_empty(self):
        df = pd.DataFrame([
            {"date": "   ", "amount": "  ", "description": "", "source_row": 7}
        ])
        problems = validate_core_dataframe(df)
        assert "EMPTY_ROW" in _codes(problems)

    def test_row_with_one_non_empty_field_not_flagged(self):
        df = pd.DataFrame([
            {"date": None, "amount": None, "description": "Something", "source_row": 2}
        ])
        problems = validate_core_dataframe(df)
        assert "EMPTY_ROW" not in _codes(problems)

    def test_multiple_empty_rows_all_captured(self):
        rows = [
            {"date": None, "amount": None, "source_row": i}
            for i in range(1, 6)
        ]
        df = pd.DataFrame(rows)
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "EMPTY_ROW")
        assert p and len(p[0].row_refs) == 5


class TestDuplicateRow:
    """DUPLICATE_ROW: exact duplicates → warning."""

    def test_unique_rows_do_not_fire(self):
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 100.0, "source_row": 1},
            {"date": "2026-01-02", "amount": 200.0, "source_row": 2},
        ])
        report = validate(df, domain="pharmacy")
        assert "DUPLICATE_ROW" not in _codes(report.problems)

    def test_exact_duplicates_fire(self):
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 100.0, "description": "Drug A", "source_row": 1},
            {"date": "2026-01-01", "amount": 100.0, "description": "Drug A", "source_row": 2},
        ])
        report = validate(df, domain="pharmacy")
        assert "DUPLICATE_ROW" in _codes(report.problems)

    def test_only_second_occurrence_is_flagged_not_first(self):
        """The 'keep=first' policy means the original row is NOT in row_refs."""
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 100.0, "source_row": 10},
            {"date": "2026-01-01", "amount": 100.0, "source_row": 20},
        ])
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "DUPLICATE_ROW")
        assert p
        assert 10 not in p[0].row_refs, "First occurrence must not be flagged"
        assert 20 in p[0].row_refs

    def test_triplicate_two_flagged(self):
        df = pd.DataFrame([
            {"amount": 5.0, "source_row": 1},
            {"amount": 5.0, "source_row": 2},
            {"amount": 5.0, "source_row": 3},
        ])
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "DUPLICATE_ROW")
        assert p and p[0].count == 2


class TestMissingRequiredField:
    """MISSING_REQUIRED_FIELD: transactions need date+amount; inventory needs product."""

    # --- Transactions ---
    def test_transaction_with_both_fields_passes(self):
        df = _make_txn()
        report = validate(df, domain="pharmacy", table_kind="transactions")
        assert "MISSING_REQUIRED_FIELD" not in _codes(report.problems)

    def test_transaction_missing_amount_fires(self):
        df = pd.DataFrame([{"date": pd.Timestamp("2026-01-01"), "amount": None, "source_row": 5}])
        report = validate(df, domain="pharmacy", table_kind="transactions")
        assert "MISSING_REQUIRED_FIELD" in _codes(report.problems)

    def test_transaction_missing_date_fires(self):
        df = pd.DataFrame([{"date": None, "amount": 100.0, "source_row": 3}])
        report = validate(df, domain="pharmacy", table_kind="transactions")
        assert "MISSING_REQUIRED_FIELD" in _codes(report.problems)

    def test_transaction_missing_both_fields_fires_once(self):
        df = pd.DataFrame([{"date": None, "amount": None, "description": "X", "source_row": 1}])
        report = validate(df, domain="pharmacy", table_kind="transactions")
        codes = _codes(report.problems)
        # Must fire but must not duplicate
        assert codes.count("MISSING_REQUIRED_FIELD") == 1

    # --- Inventory ---
    def test_inventory_with_product_id_passes(self):
        df = _make_inv()
        report = validate(df, domain="pharmacy", table_kind="inventory")
        assert "MISSING_REQUIRED_FIELD" not in _codes(report.problems)

    def test_inventory_with_description_passes(self):
        df = pd.DataFrame([{"description": "Panadol", "quantity": 5.0, "source_row": 1}])
        report = validate(df, domain="pharmacy", table_kind="inventory")
        assert "MISSING_REQUIRED_FIELD" not in _codes(report.problems)

    def test_inventory_missing_product_fires(self):
        df = pd.DataFrame([{"quantity": 5.0, "cost": 10.0, "source_row": 2}])
        report = validate(df, domain="pharmacy", table_kind="inventory")
        assert "MISSING_REQUIRED_FIELD" in _codes(report.problems)

    def test_row_ref_traces_back_via_source_row(self):
        # Use a row that has a non-null description (so it's NOT caught as EMPTY_ROW)
        # but is missing both date and amount (so MISSING_REQUIRED_FIELD must fire).
        df = pd.DataFrame([{"date": None, "amount": None, "description": "Drug X", "source_row": 42}])
        report = validate(df, domain="pharmacy", table_kind="transactions")
        p = _by_code(report.problems, "MISSING_REQUIRED_FIELD")
        assert p and 42 in p[0].row_refs


class TestUnparseableNumber:
    """UNPARSEABLE_NUMBER: numeric field present but non-parseable."""

    def test_clean_numeric_field_does_not_fire(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": 100.0, "source_row": 1}
        ])
        problems = validate_core_dataframe(df)
        assert "UNPARSEABLE_NUMBER" not in _codes(problems)

    def test_none_in_numeric_field_does_not_fire(self):
        """Null/None amount must NOT trigger UNPARSEABLE_NUMBER.
        With col.notna() guard, None cells are excluded (pandas treats None as NaN,
        so notna()=False), and only truly non-null, non-numeric strings fire the rule.
        """
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": None, "source_row": 1}
        ])
        problems = validate_core_dataframe(df)
        # None is NA — col.notna()=False for this cell — so UNPARSEABLE_NUMBER must not fire.
        assert "UNPARSEABLE_NUMBER" not in _codes(problems)

    def test_garbage_amount_string_fires(self):
        """After normalize, a field like 'abc' in amount col → NaN; detect it."""
        # Simulate post-mapping state: column is NaN but "raw" intent was text
        # We inject a column whose dtype is object (string) and value is "abc"
        # to mimic a pre-coercion snapshot reaching the validator without normalize.
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": "abc", "source_row": 3}
        ])
        problems = validate_core_dataframe(df)
        # amount "abc" is not parseable — UNPARSEABLE_NUMBER should fire
        assert "UNPARSEABLE_NUMBER" in _codes(problems)

    def test_quantity_garbage_fires(self):
        df = pd.DataFrame([
            {"product_id": "DrugA", "quantity": "N/A", "source_row": 5}
        ])
        problems = validate_core_dataframe(df, table_kind="inventory")
        assert "UNPARSEABLE_NUMBER" in _codes(problems)

    def test_field_name_is_correct(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "unit_price": "???", "source_row": 7}
        ])
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "UNPARSEABLE_NUMBER")
        assert p and p[0].field == "unit_price"

    def test_count_and_sample_populated(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": "bad", "source_row": 10},
            {"date": pd.Timestamp("2026-01-02"), "amount": "worse", "source_row": 11},
        ])
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "UNPARSEABLE_NUMBER")
        assert p
        assert p[0].count == 2
        assert len(p[0].sample) == 2


class TestUnparseableDate:
    """UNPARSEABLE_DATE: non-empty date value that could not be parsed."""

    def test_valid_timestamp_does_not_fire(self):
        df = _make_txn(date=pd.Timestamp("2026-06-01"))
        problems = validate_core_dataframe(df, table_kind="transactions")
        assert "UNPARSEABLE_DATE" not in _codes(problems)

    def test_null_date_does_not_fire_unparseable_date(self):
        """Null dates must NOT trigger UNPARSEABLE_DATE.
        With date_col.notna() guard, None cells are excluded (pandas treats None/NaN
        as NA, so notna()=False), and UNPARSEABLE_DATE only fires for non-null strings
        that fail pd.to_datetime coercion.
        """
        df = pd.DataFrame([{"date": None, "amount": 50.0, "source_row": 1}])
        problems = validate_core_dataframe(df, table_kind="transactions")
        # None date — date_col.notna()=False — must not fire UNPARSEABLE_DATE.
        assert "UNPARSEABLE_DATE" not in _codes(problems)

    def test_garbage_date_string_fires(self):
        """A non-empty string that normalizer couldn't parse → NaT → UNPARSEABLE_DATE."""
        df = pd.DataFrame([
            {"date": "NOT_A_DATE", "amount": 50.0, "source_row": 4}
        ])
        problems = validate_core_dataframe(df, table_kind="transactions")
        assert "UNPARSEABLE_DATE" in _codes(problems)

    def test_inventory_table_does_not_fire_for_missing_date(self):
        """Inventory tables don't require a date, so UNPARSEABLE_DATE shouldn't fire."""
        df = pd.DataFrame([{"product_id": "DrugX", "quantity": 5.0, "source_row": 1}])
        problems = validate_core_dataframe(df, table_kind="inventory")
        assert "UNPARSEABLE_DATE" not in _codes(problems)

    def test_row_ref_via_source_row(self):
        df = pd.DataFrame([
            {"date": "NOTADATE", "amount": 10.0, "source_row": 77}
        ])
        problems = validate_core_dataframe(df, table_kind="transactions")
        p = _by_code(problems, "UNPARSEABLE_DATE")
        assert p and 77 in p[0].row_refs


class TestInvalidDateRange:
    """INVALID_DATE_RANGE: future or pre-1990 dates → warning."""

    def test_normal_date_does_not_fire(self):
        df = _make_txn(date=pd.Timestamp("2025-03-15"))
        problems = validate_core_dataframe(df)
        assert "INVALID_DATE_RANGE" not in _codes(problems)

    def test_far_future_date_fires(self):
        df = _make_txn(date=pd.Timestamp("2099-12-31"))
        problems = validate_core_dataframe(df)
        assert "INVALID_DATE_RANGE" in _codes(problems)

    def test_pre_1990_date_fires(self):
        df = _make_txn(date=pd.Timestamp("1985-01-01"))
        problems = validate_core_dataframe(df)
        assert "INVALID_DATE_RANGE" in _codes(problems)

    def test_exactly_1990_01_01_is_safe(self):
        df = _make_txn(date=pd.Timestamp("1990-01-01"))
        problems = validate_core_dataframe(df)
        assert "INVALID_DATE_RANGE" not in _codes(problems)

    def test_sample_contains_date_strings(self):
        df = _make_txn(date=pd.Timestamp("2099-01-01"))
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "INVALID_DATE_RANGE")
        assert p and len(p[0].sample) >= 1
        assert "2099" in p[0].sample[0]


class TestNegativeQuantity:
    """NEGATIVE_QUANTITY: negative quantity → warning."""

    def test_positive_quantity_passes(self):
        df = _make_txn(quantity=10.0)
        problems = validate_core_dataframe(df)
        assert "NEGATIVE_QUANTITY" not in _codes(problems)

    def test_zero_quantity_passes(self):
        df = _make_txn(quantity=0.0)
        problems = validate_core_dataframe(df)
        assert "NEGATIVE_QUANTITY" not in _codes(problems)

    def test_negative_quantity_fires(self):
        df = _make_txn(quantity=-5.0)
        problems = validate_core_dataframe(df)
        assert "NEGATIVE_QUANTITY" in _codes(problems)

    def test_negative_quantity_sample_contains_value(self):
        df = _make_txn(quantity=-7.5)
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "NEGATIVE_QUANTITY")
        assert p and -7.5 in p[0].sample

    def test_null_quantity_does_not_fire(self):
        df = _make_txn(quantity=None)
        problems = validate_core_dataframe(df)
        assert "NEGATIVE_QUANTITY" not in _codes(problems)


class TestLineTotalMismatch:
    """LINE_TOTAL_MISMATCH: unit_price * quantity ≠ amount → warning."""

    def test_correct_total_passes(self):
        df = _make_txn(unit_price=10.0, quantity=5.0, amount=50.0)
        problems = validate_core_dataframe(df)
        assert "LINE_TOTAL_MISMATCH" not in _codes(problems)

    def test_within_tolerance_passes(self):
        """50 ± 0.49 is inside the 0.5 tolerance."""
        df = _make_txn(unit_price=10.0, quantity=5.0, amount=50.49)
        problems = validate_core_dataframe(df)
        assert "LINE_TOTAL_MISMATCH" not in _codes(problems)

    def test_mismatch_fires(self):
        df = _make_txn(unit_price=10.0, quantity=5.0, amount=100.0)
        problems = validate_core_dataframe(df)
        assert "LINE_TOTAL_MISMATCH" in _codes(problems)

    def test_missing_unit_price_suppresses_rule(self):
        """Rule only fires when all three fields are present."""
        df = _make_txn(quantity=5.0, amount=50.0)
        # No unit_price column at all
        if "unit_price" in df.columns:
            df = df.drop(columns=["unit_price"])
        problems = validate_core_dataframe(df)
        assert "LINE_TOTAL_MISMATCH" not in _codes(problems)

    def test_sample_message_shows_values(self):
        df = _make_txn(unit_price=10.0, quantity=5.0, amount=999.0)
        problems = validate_core_dataframe(df)
        p = _by_code(problems, "LINE_TOTAL_MISMATCH")
        assert p
        msg = p[0].sample[0]
        assert "10" in msg and "5" in msg and "999" in msg


# ===========================================================================
# §4 PHARMACY PACK RULES — pass and fail for each
# ===========================================================================


class TestBelowCost:
    """BELOW_COST: mrp < cost or unit_price < cost → warning."""

    def test_mrp_above_cost_passes(self):
        df = pd.DataFrame([{"mrp": 100.0, "cost": 80.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "BELOW_COST" not in _codes(pack.validate_dataframe(df))

    def test_mrp_equals_cost_passes(self):
        df = pd.DataFrame([{"mrp": 80.0, "cost": 80.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "BELOW_COST" not in _codes(pack.validate_dataframe(df))

    def test_mrp_below_cost_fires(self):
        df = pd.DataFrame([{"mrp": 70.0, "cost": 80.0, "source_row": 2}])
        pack = PharmacyDomainPack()
        assert "BELOW_COST" in _codes(pack.validate_dataframe(df))

    def test_unit_price_below_cost_fires(self):
        df = pd.DataFrame([{"unit_price": 60.0, "cost": 80.0, "source_row": 3}])
        pack = PharmacyDomainPack()
        assert "BELOW_COST" in _codes(pack.validate_dataframe(df))

    def test_no_cost_column_does_not_fire(self):
        df = pd.DataFrame([{"mrp": 50.0, "unit_price": 40.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "BELOW_COST" not in _codes(pack.validate_dataframe(df))

    def test_row_ref_traces_source_row(self):
        df = pd.DataFrame([{"mrp": 10.0, "cost": 50.0, "source_row": 99}])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "BELOW_COST")
        assert p and 99 in p[0].row_refs


class TestMrpOvercharge:
    """MRP_OVERCHARGE: unit_price > mrp → warning."""

    def test_price_at_mrp_passes(self):
        df = pd.DataFrame([{"unit_price": 100.0, "mrp": 100.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "MRP_OVERCHARGE" not in _codes(pack.validate_dataframe(df))

    def test_price_below_mrp_passes(self):
        df = pd.DataFrame([{"unit_price": 90.0, "mrp": 100.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "MRP_OVERCHARGE" not in _codes(pack.validate_dataframe(df))

    def test_price_above_mrp_fires(self):
        df = pd.DataFrame([{"unit_price": 110.0, "mrp": 100.0, "source_row": 5}])
        pack = PharmacyDomainPack()
        assert "MRP_OVERCHARGE" in _codes(pack.validate_dataframe(df))

    def test_sample_shows_both_values(self):
        df = pd.DataFrame([{"unit_price": 150.0, "mrp": 100.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "MRP_OVERCHARGE")
        assert p
        assert "150" in p[0].sample[0] and "100" in p[0].sample[0]

    def test_null_mrp_suppresses_rule(self):
        df = pd.DataFrame([{"unit_price": 200.0, "mrp": None, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "MRP_OVERCHARGE" not in _codes(pack.validate_dataframe(df))


class TestExpiredStock:
    """EXPIRED_STOCK: expiry_date in the past → warning."""

    def test_future_expiry_passes(self):
        future = pd.Timestamp(datetime.date.today() + datetime.timedelta(days=180))
        df = _make_inv(expiry_date=future)
        pack = PharmacyDomainPack()
        assert "EXPIRED_STOCK" not in _codes(pack.validate_dataframe(df))

    def test_past_expiry_fires(self):
        past = pd.Timestamp("2019-06-01")
        df = _make_inv(expiry_date=past)
        pack = PharmacyDomainPack()
        assert "EXPIRED_STOCK" in _codes(pack.validate_dataframe(df))

    def test_null_expiry_does_not_fire_expired(self):
        df = _make_inv(expiry_date=None)
        pack = PharmacyDomainPack()
        assert "EXPIRED_STOCK" not in _codes(pack.validate_dataframe(df))

    def test_sample_contains_date_string(self):
        past = pd.Timestamp("2020-03-15")
        df = _make_inv(expiry_date=past)
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "EXPIRED_STOCK")
        assert p and "2020" in p[0].sample[0]

    def test_row_ref_source_row(self):
        past = pd.Timestamp("2021-01-01")
        df = pd.DataFrame([{"product_id": "DrugX", "quantity": 5.0, "expiry_date": past, "source_row": 88}])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "EXPIRED_STOCK")
        assert p and 88 in p[0].row_refs


class TestMissingExpiry:
    """MISSING_EXPIRY: batch present but no expiry → warning."""

    def test_batch_with_expiry_passes(self):
        future = pd.Timestamp(datetime.date.today() + datetime.timedelta(days=90))
        df = _make_inv(batch_no="B001", expiry_date=future)
        pack = PharmacyDomainPack()
        assert "MISSING_EXPIRY" not in _codes(pack.validate_dataframe(df))

    def test_batch_without_expiry_fires(self):
        df = pd.DataFrame([{
            "product_id": "Brufen", "quantity": 20.0,
            "batch_no": "B123", "expiry_date": None, "source_row": 2
        }])
        pack = PharmacyDomainPack()
        assert "MISSING_EXPIRY" in _codes(pack.validate_dataframe(df))

    def test_no_batch_no_expiry_still_fires_for_inventory(self):
        """Inventory without batch AND without expiry should still flag MISSING_EXPIRY."""
        df = pd.DataFrame([{
            "product_id": "Disprin", "quantity": 10.0,
            "expiry_date": None, "source_row": 3
        }])
        pack = PharmacyDomainPack()
        assert "MISSING_EXPIRY" in _codes(pack.validate_dataframe(df))

    def test_no_duplicate_emission_for_same_row(self):
        """A row with batch_no AND missing expiry must appear in at most one MISSING_EXPIRY."""
        df = pd.DataFrame([{
            "product_id": "X", "quantity": 5.0,
            "batch_no": "BAT1", "expiry_date": None, "source_row": 10
        }])
        pack = PharmacyDomainPack()
        all_missing_expiry = _by_code(pack.validate_dataframe(df), "MISSING_EXPIRY")
        # Flatten all row_refs across all MISSING_EXPIRY problems
        all_refs = [r for p in all_missing_expiry for r in p.row_refs]
        # Row 10 must not appear twice
        assert all_refs.count(10) <= 1, (
            f"Row 10 appears {all_refs.count(10)} times across MISSING_EXPIRY problems"
        )


class TestInvalidExpiry:
    """INVALID_EXPIRY: non-blank expiry value that could not be parsed."""

    def test_valid_expiry_passes(self):
        future = pd.Timestamp(datetime.date.today() + datetime.timedelta(days=60))
        df = _make_inv(expiry_date=future)
        pack = PharmacyDomainPack()
        assert "INVALID_EXPIRY" not in _codes(pack.validate_dataframe(df))

    def test_null_expiry_does_not_fire_invalid(self):
        """Null expiry must NOT trigger INVALID_EXPIRY.
        With exp_val.notna() guard, None cells are excluded (pandas treats None as NaN,
        so notna()=False). These are handled by MISSING_EXPIRY, not INVALID_EXPIRY.
        """
        df = _make_inv(expiry_date=None)
        pack = PharmacyDomainPack()
        # None expiry — exp_val.notna()=False — must not fire INVALID_EXPIRY.
        assert "INVALID_EXPIRY" not in _codes(pack.validate_dataframe(df))

    def test_garbage_string_fires(self):
        """A non-empty, non-parseable expiry string → INVALID_EXPIRY."""
        df = pd.DataFrame([{
            "product_id": "DrugZ", "quantity": 5.0,
            "expiry_date": "not-a-date-at-all", "source_row": 4
        }])
        pack = PharmacyDomainPack()
        assert "INVALID_EXPIRY" in _codes(pack.validate_dataframe(df))

    def test_sample_shows_offending_value(self):
        df = pd.DataFrame([{
            "product_id": "Y", "quantity": 2.0,
            "expiry_date": "GARBAGE/99", "source_row": 6
        }])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "INVALID_EXPIRY")
        assert p and len(p[0].sample) >= 1


class TestMissingMrp:
    """MISSING_MRP: mrp column absent or has null values."""

    def test_mrp_present_passes(self):
        df = pd.DataFrame([{"product_id": "X", "mrp": 50.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "MISSING_MRP" not in _codes(pack.validate_dataframe(df))

    def test_no_mrp_column_fires(self):
        df = pd.DataFrame([{"product_id": "X", "quantity": 5.0, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "MISSING_MRP" in _codes(pack.validate_dataframe(df))

    def test_mrp_all_null_fires(self):
        df = pd.DataFrame([{"product_id": "X", "mrp": None, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "MISSING_MRP" in _codes(pack.validate_dataframe(df))

    def test_partial_mrp_missing_fires(self):
        df = pd.DataFrame([
            {"product_id": "A", "mrp": 100.0, "source_row": 1},
            {"product_id": "B", "mrp": None, "source_row": 2},
        ])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "MISSING_MRP")
        assert p and 2 in p[0].row_refs and 1 not in p[0].row_refs


class TestUnregisteredHint:
    """UNREGISTERED_HINT: drap_reg_no absent or empty → info."""

    def test_drap_present_passes(self):
        df = pd.DataFrame([{"product_id": "X", "drap_reg_no": "D-12345", "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "UNREGISTERED_HINT" not in _codes(pack.validate_dataframe(df))

    def test_drap_column_absent_fires(self):
        df = pd.DataFrame([{"product_id": "X", "source_row": 1}])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "UNREGISTERED_HINT")
        assert p and p[0].severity == "info"

    def test_drap_null_fires(self):
        df = pd.DataFrame([{"product_id": "X", "drap_reg_no": None, "source_row": 1}])
        pack = PharmacyDomainPack()
        assert "UNREGISTERED_HINT" in _codes(pack.validate_dataframe(df))

    def test_severity_is_info_not_warning(self):
        df = pd.DataFrame([{"product_id": "X", "source_row": 1}])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "UNREGISTERED_HINT")
        assert p and p[0].severity == "info"


# ===========================================================================
# ValidationReport contract
# ===========================================================================


class TestValidationReport:
    """Verify ValidationReport structure, properties, and serialisation."""

    def test_json_serializable_clean_data(self):
        df = _make_txn()
        report = validate(df, domain="pharmacy")
        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert "verdict" in parsed
        assert "total_rows" in parsed
        assert "null_counts" in parsed
        assert "problems" in parsed

    def test_json_serializable_with_problems(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2099-01-01"), "amount": 50.0, "unit_price": 15.0,
             "quantity": 2.0, "mrp": 10.0, "cost": 20.0, "source_row": 1}
        ])
        report = validate(df, domain="pharmacy")
        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert isinstance(parsed["problems"], list)

    def test_errors_property_filters_correctly(self):
        df = pd.DataFrame([{"date": None, "amount": None, "source_row": 1}])
        report = validate(df, domain="pharmacy", table_kind="transactions")
        for p in report.errors:
            assert p.severity == "error"

    def test_warnings_property_filters_correctly(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": 50.0,
             "unit_price": 10.0, "quantity": 5.0, "source_row": 1}  # LINE_TOTAL_MISMATCH
        ])
        report = validate(df, domain="pharmacy")
        for p in report.warnings:
            assert p.severity == "warning"

    def test_info_property_filters_correctly(self):
        df = _make_inv()  # no drap_reg_no → UNREGISTERED_HINT (info)
        report = validate(df, domain="pharmacy", table_kind="inventory")
        for p in report.info:
            assert p.severity == "info"

    def test_is_usable_true_for_clean_data(self):
        df = pd.DataFrame([{
            "date": pd.Timestamp("2026-01-01"), "amount": 50.0,
            "mrp": 60.0, "drap_reg_no": "D-001", "source_row": 1
        }])
        report = validate(df, domain="pharmacy")
        assert report.is_usable

    def test_is_usable_false_for_empty_df(self):
        df = pd.DataFrame()
        report = validate(df, domain="pharmacy")
        assert not report.is_usable
        assert report.verdict == "not_usable"

    def test_verdict_usable_with_warnings_for_minor_issues(self):
        """A dataset with one duplicate row should be usable_with_warnings, not not_usable."""
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 100.0, "source_row": 1},
            {"date": "2026-01-01", "amount": 100.0, "source_row": 2},
        ])
        report = validate(df, domain="pharmacy")
        assert report.verdict == "usable_with_warnings"
        assert report.is_usable

    def test_verdict_not_usable_when_majority_rows_have_errors(self):
        """>20% error rows → not_usable."""
        rows = [{"date": None, "amount": None, "source_row": i} for i in range(1, 4)]
        rows += [{"date": pd.Timestamp("2026-01-01"), "amount": 10.0, "source_row": i}
                 for i in range(4, 6)]
        df = pd.DataFrame(rows)
        report = validate(df, domain="pharmacy", table_kind="transactions")
        # 3 out of 5 rows have errors (60%) → not_usable
        assert report.verdict == "not_usable"

    def test_null_counts_correct(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": None, "source_row": 1},
            {"date": pd.Timestamp("2026-01-02"), "amount": 50.0, "source_row": 2},
        ])
        report = validate(df, domain="pharmacy")
        assert report.null_counts.get("amount", 0) == 1

    def test_total_rows_correct(self):
        df = pd.DataFrame([{"date": pd.Timestamp("2026-01-01"), "amount": 10.0}] * 7)
        report = validate(df, domain="pharmacy")
        assert report.total_rows == 7

    def test_problem_has_count_field(self):
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 100.0, "source_row": 1},
            {"date": "2026-01-01", "amount": 100.0, "source_row": 2},
        ])
        report = validate(df, domain="pharmacy")
        for p in report.problems:
            assert isinstance(p.count, int)

    def test_problem_has_sample_field(self):
        df = _make_txn(date=pd.Timestamp("2099-01-01"))
        report = validate(df, domain="pharmacy")
        for p in report.problems:
            assert isinstance(p.sample, list)

    def test_iteration_and_len(self):
        df = _make_txn()
        report = validate(df, domain="pharmacy")
        count = sum(1 for _ in report)
        assert count == len(report)

    def test_to_dict_contains_all_required_keys(self):
        df = _make_txn()
        report = validate(df, domain="pharmacy")
        d = report.to_dict()
        for key in ("total_rows", "error_rows", "warning_rows", "null_counts",
                    "verdict", "is_usable", "error_count", "warning_count", "problems"):
            assert key in d, f"Missing key: {key}"


# ===========================================================================
# source_row traceability
# ===========================================================================


class TestSourceRowTraceability:
    """Row refs must trace back to source_row, not pandas index."""

    def test_source_row_overrides_index(self):
        # Pandas index is 0-based but source_row is 100
        df = pd.DataFrame(
            [{"date": None, "amount": None, "source_row": 100}],
            index=[0]
        )
        problems = validate_core_dataframe(df, table_kind="transactions")
        for p in problems:
            if p.row_refs:
                assert 100 in p.row_refs, f"{p.code}: expected 100, got {p.row_refs}"

    def test_index_fallback_when_no_source_row(self):
        """When source_row column is absent, use 1-based index."""
        df = pd.DataFrame([{"date": None, "amount": None}])
        problems = validate_core_dataframe(df, table_kind="transactions")
        # Index 0 → 1-based ref = 1
        for p in problems:
            if p.row_refs:
                assert 1 in p.row_refs

    def test_mixed_source_rows_correct(self):
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 50.0, "source_row": 2},
            {"date": None, "amount": None, "source_row": 7},
            {"date": "2026-01-03", "amount": 75.0, "source_row": 9},
        ])
        problems = validate_core_dataframe(df, table_kind="transactions")
        empty = _by_code(problems, "EMPTY_ROW")
        assert empty and 7 in empty[0].row_refs
        assert 2 not in empty[0].row_refs
        assert 9 not in empty[0].row_refs

    def test_pharmacy_rules_also_trace_source_row(self):
        df = pd.DataFrame([
            {"unit_price": 200.0, "mrp": 100.0, "source_row": 55}
        ])
        pack = PharmacyDomainPack()
        p = _by_code(pack.validate_dataframe(df), "MRP_OVERCHARGE")
        assert p and 55 in p[0].row_refs


# ===========================================================================
# opt-in clean()
# ===========================================================================


class TestOptInClean:
    """Verify clean() behaviour and guardrails."""

    def test_clean_not_called_by_validate(self):
        """validate() must never mutate the input DataFrame."""
        df = pd.DataFrame([
            {"product_id": "  Panadol  ", "quantity": 10.0, "source_row": 1}
        ])
        original_val = df.iloc[0]["product_id"]
        validate(df, domain="pharmacy")
        assert df.iloc[0]["product_id"] == original_val, "validate() must not mutate the DataFrame"

    def test_whitespace_trimmed_correctly(self):
        df = pd.DataFrame([
            {"product_id": "  DrugA  ", "quantity": 5.0, "source_row": 1}
        ])
        cleaned, summary = clean(df, options={"trim_whitespace": True, "drop_empty": False})
        assert cleaned.iloc[0]["product_id"] == "DrugA"
        assert summary.whitespace_trimmed_cells >= 1

    def test_empty_rows_dropped(self):
        df = pd.DataFrame([
            {"product_id": "X", "quantity": 5.0, "source_row": 1},
            {"product_id": None, "quantity": None, "source_row": 2},
        ])
        cleaned, summary = clean(df, options={"drop_empty": True, "trim_whitespace": False})
        assert summary.empty_rows_dropped == 1
        assert len(cleaned) == 1

    def test_duplicates_dropped_when_requested(self):
        df = pd.DataFrame([
            {"product_id": "A", "quantity": 5.0, "source_row": 1},
            {"product_id": "A", "quantity": 5.0, "source_row": 2},
        ])
        cleaned, summary = clean(df, options={"drop_empty": False, "drop_duplicates": True})
        assert summary.duplicate_rows_dropped == 1
        assert len(cleaned) == 1

    def test_duplicates_not_dropped_by_default(self):
        df = pd.DataFrame([
            {"product_id": "A", "quantity": 5.0, "source_row": 1},
            {"product_id": "A", "quantity": 5.0, "source_row": 2},
        ])
        cleaned, summary = clean(df)  # default: drop_duplicates=False
        assert summary.duplicate_rows_dropped == 0
        assert len(cleaned) == 2

    def test_money_values_not_altered(self):
        df = pd.DataFrame([
            {"product_id": "  X  ", "amount": 1234.56, "cost": 999.99, "source_row": 1}
        ])
        cleaned, _ = clean(df, options={"trim_whitespace": True})
        assert cleaned.iloc[0]["amount"] == 1234.56
        assert cleaned.iloc[0]["cost"] == 999.99

    def test_quantity_not_altered(self):
        df = pd.DataFrame([
            {"product_id": "  Y  ", "quantity": 42.0, "source_row": 1}
        ])
        cleaned, _ = clean(df, options={"trim_whitespace": True})
        assert cleaned.iloc[0]["quantity"] == 42.0

    def test_summary_counts_are_consistent(self):
        df = pd.DataFrame([
            {"product_id": " A ", "quantity": 1.0, "source_row": 1},
            {"product_id": None, "quantity": None, "source_row": 2},
            {"product_id": " A ", "quantity": 1.0, "source_row": 3},
        ])
        cleaned, summary = clean(df, options={
            "trim_whitespace": True, "drop_empty": True, "drop_duplicates": True
        })
        assert summary.original_rows == 3
        assert summary.cleaned_rows == len(cleaned)
        assert summary.rows_dropped == summary.empty_rows_dropped + summary.duplicate_rows_dropped

    def test_summary_details_list_populated(self):
        df = pd.DataFrame([
            {"product_id": " X ", "source_row": 1},
            {"product_id": None, "source_row": 2},
        ])
        _, summary = clean(df, options={"trim_whitespace": True, "drop_empty": True})
        assert len(summary.details) >= 1

    def test_cleaning_summary_serializable(self):
        df = pd.DataFrame([{"product_id": " Z ", "source_row": 1}])
        _, summary = clean(df)
        d = summary.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)

    def test_null_only_column_does_not_crash_whitespace_trim(self):
        """A column of all-None values must not throw during whitespace trimming."""
        df = pd.DataFrame([
            {"product_id": "A", "expiry_date": None, "source_row": 1}
        ])
        cleaned, summary = clean(df, options={"trim_whitespace": True})
        assert len(cleaned) == 1  # no crash


# ===========================================================================
# Domain-agnostic extensibility — grocery pack registers without core edits
# ===========================================================================


class TestGroceryPackExtensibility:
    """Prove that a new domain pack can register and run with zero core edits."""

    @pytest.fixture(autouse=True)
    def _register_grocery(self):
        """Register a minimal GroceryPack and remove it after the test."""
        class GroceryPack(DomainPack):
            @property
            def name(self):
                return "grocery_test"

            @property
            def extra_fields(self):
                return ["shelf_life_days", "barcode"]

            @property
            def header_synonyms(self):
                return {"barcode": ["ean", "upc"]}

            def validate_dataframe(self, df):
                problems = []
                if "shelf_life_days" in df.columns:
                    sl = pd.to_numeric(df["shelf_life_days"], errors="coerce")
                    bad = sl.notna() & (sl < 0)
                    if bad.any():
                        problems.append(Problem(
                            severity="warning",
                            code="NEGATIVE_SHELF_LIFE",
                            message="Shelf life cannot be negative",
                            field="shelf_life_days",
                            row_refs=(df.index[bad] + 1).tolist(),
                        ))
                return problems

        registry.register(GroceryPack())
        yield
        # cleanup
        if "grocery_test" in registry._packs:
            del registry._packs["grocery_test"]

    def test_grocery_domain_registered(self):
        assert "grocery_test" in registry.available_domains()

    def test_grocery_rule_fires(self):
        df = pd.DataFrame([
            {"description": "Apple", "shelf_life_days": -3, "source_row": 1}
        ])
        report = validate(df, domain="grocery_test")
        assert "NEGATIVE_SHELF_LIFE" in _codes(report.problems)

    def test_grocery_rule_does_not_fire_for_positive(self):
        df = pd.DataFrame([
            {"description": "Banana", "shelf_life_days": 7, "source_row": 1}
        ])
        report = validate(df, domain="grocery_test")
        assert "NEGATIVE_SHELF_LIFE" not in _codes(report.problems)

    def test_core_rules_still_apply_under_grocery_domain(self):
        """Core rules must run regardless of domain."""
        df = pd.DataFrame([
            {"date": "2026-01-01", "amount": 100.0, "source_row": 1},
            {"date": "2026-01-01", "amount": 100.0, "source_row": 2},  # duplicate
        ])
        report = validate(df, domain="grocery_test")
        assert "DUPLICATE_ROW" in _codes(report.problems)

    def test_unknown_domain_still_runs_core_rules(self):
        """If a domain pack isn't registered, validate() must still run core rules."""
        df = pd.DataFrame([{"date": None, "amount": None, "source_row": 9}])
        report = validate(df, domain="nonexistent_domain_xyz", table_kind="transactions")
        assert "EMPTY_ROW" in _codes(report.problems) or "MISSING_REQUIRED_FIELD" in _codes(report.problems)

    def test_core_code_contains_no_pharmacy_vocabulary(self):
        """Grep canonical.py for pharmacy-specific words."""
        canonical_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "schema", "canonical.py"
        )
        with open(canonical_path, encoding="utf-8") as f:
            src = f.read().lower()

        pharmacy_words = ["expiry", "mrp", "drap", "batch_no", "mfg_date", "schedule_flag"]
        for word in pharmacy_words:
            assert word not in src, (
                f"canonical.py contains pharmacy vocabulary '{word}' — violates domain separation"
            )


# ===========================================================================
# Adversarial / edge-case corpus
# ===========================================================================


class TestAdversarialEdgeCases:
    """Unusual inputs that must not crash or produce wrong results."""

    def test_empty_dataframe_returns_report(self):
        report = validate(pd.DataFrame(), domain="pharmacy")
        assert isinstance(report, ValidationReport)
        assert report.total_rows == 0
        assert report.verdict == "not_usable"

    def test_single_row_all_fields_clean(self):
        df = pd.DataFrame([{
            "date": pd.Timestamp("2026-06-01"), "amount": 250.0,
            "unit_price": 50.0, "quantity": 5.0, "mrp": 60.0,
            "drap_reg_no": "D-9999", "source_row": 1
        }])
        report = validate(df, domain="pharmacy")
        assert not report.errors

    def test_all_rows_empty_entire_table(self):
        rows = [{"date": None, "amount": None, "source_row": i} for i in range(1, 11)]
        df = pd.DataFrame(rows)
        report = validate(df, domain="pharmacy", table_kind="transactions")
        assert report.verdict == "not_usable"

    def test_urdu_product_name_does_not_crash(self):
        df = pd.DataFrame([{
            "product_id": "پیناڈول ۵۰۰", "quantity": 5.0, "source_row": 1
        }])
        report = validate(df, domain="pharmacy", table_kind="inventory")
        assert isinstance(report, ValidationReport)

    def test_very_large_dataset_performance(self):
        """10 000 rows should validate in under 5 seconds (vectorized check)."""
        import time
        rows = [
            {"date": pd.Timestamp("2026-01-01"), "amount": float(i),
             "unit_price": 10.0, "quantity": float(i // 10 or 1), "source_row": i}
            for i in range(1, 10_001)
        ]
        df = pd.DataFrame(rows)
        t0 = time.time()
        report = validate(df, domain="pharmacy")
        elapsed = time.time() - t0
        assert elapsed < 5.0, f"Validation of 10k rows took {elapsed:.2f}s — too slow"
        assert report.total_rows == 10_000

    def test_mixed_types_in_amount_column(self):
        """Amount column with a mix of floats and junk strings."""
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": 100.0, "source_row": 1},
            {"date": pd.Timestamp("2026-01-02"), "amount": "bad_value", "source_row": 2},
        ])
        report = validate(df, domain="pharmacy")
        assert isinstance(report, ValidationReport)
        # Bad value should produce UNPARSEABLE_NUMBER
        assert "UNPARSEABLE_NUMBER" in _codes(report.problems)

    def test_line_total_mismatch_tolerance_boundary(self):
        """Exactly 0.5 unit gap — on the boundary, must NOT fire (> 0.5 required)."""
        df = _make_txn(unit_price=10.0, quantity=5.0, amount=49.5)  # diff = 0.5 exactly
        problems = validate_core_dataframe(df)
        assert "LINE_TOTAL_MISMATCH" not in _codes(problems)

    def test_line_total_mismatch_just_above_tolerance(self):
        df = _make_txn(unit_price=10.0, quantity=5.0, amount=49.49)  # diff = 0.51
        problems = validate_core_dataframe(df)
        assert "LINE_TOTAL_MISMATCH" in _codes(problems)

    def test_validate_does_not_mutate_input(self):
        df = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01"), "amount": 50.0, "source_row": 1}
        ])
        df_copy = df.copy()
        validate(df, domain="pharmacy")
        pd.testing.assert_frame_equal(df, df_copy)

    def test_problem_to_dict_json_serializable(self):
        p = Problem(
            severity="error",
            code="TEST_CODE",
            message="Test message",
            field="amount",
            row_refs=[1, 2, 3],
            count=3,
            sample=["val1", "val2"],
        )
        d = p.to_dict()
        json.dumps(d)  # must not raise

    def test_problem_repr_does_not_crash(self):
        p = Problem(severity="warning", code="X", message="Y", row_refs=[1, 2, 3, 4, 5])
        r = repr(p)
        assert "WARNING" in r and "X" in r

    def test_auto_table_kind_detects_transactions(self):
        """Date + amount present → should auto-detect as transactions."""
        df = pd.DataFrame([{
            "date": pd.Timestamp("2026-01-01"), "amount": 50.0, "source_row": 1
        }])
        # If auto-detected as inventory, MISSING_REQUIRED_FIELD would not fire for
        # the missing product — but it should not fire either way since it's a txn.
        problems = validate_core_dataframe(df, table_kind="auto")
        assert "MISSING_REQUIRED_FIELD" not in _codes(problems)

    def test_auto_table_kind_detects_inventory(self):
        """product_id + no date → auto-detect as inventory."""
        df = pd.DataFrame([{"product_id": "X", "quantity": 5.0, "source_row": 1}])
        problems = validate_core_dataframe(df, table_kind="auto")
        # Inventory requires product_id which is present — no MISSING_REQUIRED_FIELD
        assert "MISSING_REQUIRED_FIELD" not in _codes(problems)


# ===========================================================================
# End-to-end tests with real sample files
# ===========================================================================


class TestEndToEnd:
    """Integration tests using the sample CSV fixtures."""

    def test_messy_pharmacy_validates_without_crash(self):
        if not os.path.exists(_MESSY_CSV):
            pytest.skip("messy_pharmacy.csv not found")
        from app.connectors.csv_excel import CSVConnector
        from app.schema.mapper import map_headers
        from app.schema.normalize import apply_mapping

        connector = CSVConnector(_MESSY_CSV)
        raw_df = connector.fetch()
        pack = PharmacyDomainPack()
        mapping = map_headers(list(raw_df.columns), pack)
        canonical_df = apply_mapping(raw_df, mapping, domain="pharmacy")
        report = validate(canonical_df, domain="pharmacy")
        assert isinstance(report, ValidationReport)
        assert report.total_rows > 0
        json.loads(report.to_json())  # must be serialisable

    def test_sample_pharmacy_csv_detects_expired_stock(self):
        """sample_pharmacy.csv has an item with expiry 12-2024 (past)."""
        if not os.path.exists(_SAMPLE_CSV):
            pytest.skip("sample_pharmacy.csv not found")
        from app.connectors.csv_excel import CSVConnector
        from app.schema.mapper import map_headers
        from app.schema.normalize import apply_mapping

        connector = CSVConnector(_SAMPLE_CSV)
        raw_df = connector.fetch()
        pack = PharmacyDomainPack()
        mapping = map_headers(list(raw_df.columns), pack)
        canonical_df = apply_mapping(raw_df, mapping, domain="pharmacy")
        report = validate(canonical_df, domain="pharmacy", table_kind="inventory")
        codes = _codes(report.problems)
        assert "EXPIRED_STOCK" in codes, (
            "sample_pharmacy.csv contains an expired item (12-2024); EXPIRED_STOCK should fire"
        )

    def test_clean_on_messy_csv_removes_empty_rows(self):
        if not os.path.exists(_MESSY_CSV):
            pytest.skip("messy_pharmacy.csv not found")
        from app.connectors.csv_excel import CSVConnector
        from app.schema.mapper import map_headers
        from app.schema.normalize import apply_mapping

        connector = CSVConnector(_MESSY_CSV)
        raw_df = connector.fetch()
        pack = PharmacyDomainPack()
        mapping = map_headers(list(raw_df.columns), pack)
        canonical_df = apply_mapping(raw_df, mapping, domain="pharmacy")
        cleaned_df, summary = clean(canonical_df, options={
            "drop_empty": True, "trim_whitespace": True
        })
        # messy_pharmacy.csv has at least one fully-empty row
        assert summary.empty_rows_dropped >= 1
        assert len(cleaned_df) < len(canonical_df)
