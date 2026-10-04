"""Test Suite 05: API Mapping Confirmation Workflow & End-to-End Integration.

Module: Module 6.3 — Schema Mapping and Validation Module
Target File: backend/app/api/routes.py (endpoints: POST /api/sources/preview, POST /api/sources/mapping/confirm)
Scope:
- Verifies Step 1: Initial preview request generates schema signature and mapping proposal.
- Verifies Step 2: One-time user confirmation step saves mapping profile to disk.
- Verifies Step 3: Subsequent preview request automatically discovers and reuses the saved profile.
- Verifies input validation and error handling for missing files or invalid schema mappings.
"""

from pathlib import Path
import pytest


class TestApiMappingConfirmationWorkflow:
    """Verifies the complete interactive schema confirmation and reuse workflow."""

    def test_end_to_end_confirmation_and_reuse_lifecycle(self, test_client, temp_storage, tmp_path):
        """
        Executes the three-phase user workflow:
        Phase 1: Preview new file -> Unconfirmed source, proposal generated.
        Phase 2: Confirm mapping -> Normalization executed, profile saved.
        Phase 3: Subsequent preview -> Profile detected, manual confirmation bypassed.
        """
        csv_file = tmp_path / "pos_daily_sales.csv"
        csv_content = (
            "Txn_Date,Medicine,Sold_Units,Price_Per_Unit,Total_Revenue\n"
            "2026-03-01,Panadol Extra,10,35.00,350.00\n"
            "2026-03-01,Disprin,5,20.00,100.00\n"
        )
        csv_file.write_text(csv_content, encoding="utf-8")

        # -------------------------------------------------------------
        # Phase 1: Initial Preview (Unconfirmed Source)
        # -------------------------------------------------------------
        res_preview_1 = test_client.post("/api/sources/preview", json={
            "file_path": str(csv_file),
            "n": 5,
            "domain": "pharmacy"
        })
        assert res_preview_1.status_code == 200
        preview_data_1 = res_preview_1.json()

        assert preview_data_1["saved_profile"] is None  # Never seen before
        assert "signature" in preview_data_1
        assert "mapping_proposal" in preview_data_1
        sig = preview_data_1["signature"]

        # -------------------------------------------------------------
        # Phase 2: One-Time User Confirmation Step
        # -------------------------------------------------------------
        user_confirmed_mapping = {
            "Txn_Date": "date",
            "Medicine": "product_id",
            "Sold_Units": "quantity",
            "Price_Per_Unit": "unit_price",
            "Total_Revenue": "amount"
        }

        res_confirm = test_client.post("/api/sources/mapping/confirm", json={
            "file_path": str(csv_file),
            "mapping": user_confirmed_mapping,
            "domain": "pharmacy",
            "save_profile": True,
            "keep_extras": True
        })
        assert res_confirm.status_code == 200
        confirm_data = res_confirm.json()

        assert "mapped_columns" in confirm_data
        assert "data_preview" in confirm_data
        assert "product_id" in confirm_data["mapped_columns"]
        assert len(confirm_data["data_preview"]) == 2

        # -------------------------------------------------------------
        # Phase 3: Subsequent Ingestion (Auto-detected Saved Profile)
        # -------------------------------------------------------------
        # When previewing the confirmed source, saved profile is immediately retrieved
        res_preview_2 = test_client.post("/api/sources/preview", json={
            "file_path": str(csv_file),
            "n": 5,
            "domain": "pharmacy"
        })
        assert res_preview_2.status_code == 200
        preview_data_2 = res_preview_2.json()

        # System immediately finds the confirmed profile!
        assert preview_data_2["saved_profile"] is not None
        assert preview_data_2["saved_profile"]["mapping"] == user_confirmed_mapping

    def test_confirm_mapping_missing_file_404(self, test_client):
        """Confirming mapping for a non-existent file returns 404 Not Found."""
        res = test_client.post("/api/sources/mapping/confirm", json={
            "file_path": "non_existent_path.csv",
            "mapping": {"A": "date"},
            "domain": "pharmacy"
        })
        assert res.status_code == 404

    def test_confirm_mapping_invalid_schema_400(self, test_client, tmp_path):
        """Submitting mapping with non-existent source columns raises 400 Bad Request."""
        csv_file = tmp_path / "valid.csv"
        csv_file.write_text("A,B\n1,2", encoding="utf-8")

        res = test_client.post("/api/sources/mapping/confirm", json={
            "file_path": str(csv_file),
            "mapping": {"NonExistentColumn": "date"},
            "domain": "pharmacy"
        })
        assert res.status_code == 400
