"""Test Suite 03: Profile Signature Generation & One-Time Confirmation Pattern.

Module: Module 6.3 — Schema Mapping and Validation Module
Target File: backend/app/schema/profile.py (functions: source_signature, save_profile, find_profile, list_profiles, delete_profile, compatible_mapping)
Scope:
- Verifies deterministic signature generation across permutations of column order and casing.
- Verifies elimination of transient 'Unnamed:' artifact columns from signatures.
- Verifies atomic persistence of confirmed mapping profiles to disk.
- Verifies the One-Time Confirmation pattern: automatic profile reuse on recurring data files.
- Verifies profile deletion and compatibility checks against evolving schemas.
"""

from pathlib import Path
import pytest
from app.schema.profile import (
    source_signature,
    save_profile,
    find_profile,
    list_profiles,
    delete_profile,
    compatible_mapping
)


class TestProfileSignatureAndOneTimeConfirmation:
    """Verifies schema fingerprinting and persistence for recurring ingestions."""

    def test_signature_deterministic_and_order_insensitive(self):
        """Column signatures must be invariant to header casing, leading spaces, and column order."""
        cols_a = ["Date", "Item_Name", "Total_Amount", "Invoice_No"]
        cols_b = ["invoice_no", "date", "TOTAL_AMOUNT", "item_name"]

        sig_a = source_signature(cols_a, connector_type="CSVConnector", domain="pharmacy")
        sig_b = source_signature(cols_b, connector_type="CSVConnector", domain="pharmacy")

        assert sig_a == sig_b
        assert len(sig_a) == 32  # Standard MD5 hexadecimal string

    def test_signature_ignores_unnamed_artifact_columns(self):
        """Excel or CSV exports often create 'Unnamed: 4' blank trailing columns."""
        cols_clean = ["Date", "Medicine", "Qty", "Price"]
        cols_with_artifacts = ["Date", "Medicine", "Qty", "Price", "Unnamed: 4", "Unnamed: 5"]

        sig_clean = source_signature(cols_clean, connector_type="ExcelConnector")
        sig_dirty = source_signature(cols_with_artifacts, connector_type="ExcelConnector")

        assert sig_clean == sig_dirty

    def test_one_time_confirmation_save_and_lookup_lifecycle(self, temp_storage):
        """
        Validates the core one-time confirmation requirement:
        1. New source has no saved profile.
        2. User confirms mapping once -> profile saved.
        3. Subsequent loads find the confirmed profile immediately.
        """
        source_cols = ["Trans_Date", "Prod_Desc", "Units_Sold", "Net_Val"]
        sig = source_signature(source_cols, connector_type="CSVConnector", domain="pharmacy")

        # 1. First time: no profile exists
        assert find_profile(sig) is None

        # 2. User confirms mapping
        confirmed_mapping = {
            "Trans_Date": "date",
            "Prod_Desc": "product_id",
            "Units_Sold": "quantity",
            "Net_Val": "amount"
        }
        save_profile(sig, confirmed_mapping, label="Monthly Sales POS Export")

        # 3. Subsequent check: profile exists and matches confirmed mapping
        retrieved = find_profile(sig)
        assert retrieved is not None
        assert retrieved["signature"] == sig
        assert retrieved["label"] == "Monthly Sales POS Export"
        assert retrieved["mapping"] == confirmed_mapping

    def test_list_and_delete_profiles(self, temp_storage):
        """Lists active confirmed profiles and supports profile deletion."""
        sig_1 = source_signature(["ColA", "ColB"], connector_type="CSVConnector")
        sig_2 = source_signature(["ColX", "ColY"], connector_type="CSVConnector")

        save_profile(sig_1, {"ColA": "date", "ColB": "amount"}, label="Profile 1")
        save_profile(sig_2, {"ColX": "product_id", "ColY": "quantity"}, label="Profile 2")

        profiles = list_profiles()
        assert len(profiles) == 2
        labels = [p["label"] for p in profiles]
        assert "Profile 1" in labels
        assert "Profile 2" in labels

        # Delete Profile 1
        assert delete_profile(sig_1) is True
        assert find_profile(sig_1) is None
        assert find_profile(sig_2) is not None
        assert len(list_profiles()) == 1

    def test_compatible_mapping_subset_validation(self):
        """Rebinds saved mapping keys to current columns' casing and rejects missing keys."""
        saved_mapping = {
            "Date": "date",
            "Item": "product_id",
            "Qty": "quantity"
        }
        # Incoming dataset with varied casing
        current_cols = ["date", "ITEM", "qty"]

        active_mapping = compatible_mapping(saved_mapping, current_cols)
        assert active_mapping["date"] == "date"
        assert active_mapping["ITEM"] == "product_id"
        assert active_mapping["qty"] == "quantity"

        # Missing column should raise ValueError demanding review
        with pytest.raises(ValueError, match="is missing or ambiguous"):
            compatible_mapping(saved_mapping, ["date", "ITEM"])
