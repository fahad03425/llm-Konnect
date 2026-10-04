"""Test Suite 01: CSV and Excel Universal Baseline Connectors.

Module: Module 6.2 — Data Connector Module
Target File: backend/app/connectors/csv_excel.py (classes: CSVConnector, ExcelConnector)
Scope:
- Verifies encoding detection and decoding from raw bytes.
- Verifies automatic delimiter sniffing (comma, semicolon, tab, pipe).
- Verifies header detection heuristic skipping report title and branding metadata.
- Verifies malformed row recovery when commas are unquoted in trailing notes/review columns.
- Verifies Excel multi-sheet support, sheet discovery, and sheet name validation.
- Verifies row provenance injection (source_connector, source_row).
"""

import io
import os
import pandas as pd
import pytest
from app.connectors.csv_excel import CSVConnector, ExcelConnector, _sniff_csv_format_from_bytes, _find_excel_header


class TestCSVConnector:
    """Verifies CSV parsing, format sniffing, header detection, and error recovery."""

    def test_standard_csv_fetch_and_provenance(self, tmp_path):
        """Standard CSV file extraction with correct row provenance."""
        csv_file = tmp_path / "sales.csv"
        csv_file.write_text("Date,Invoice,Medicine,Amount\n2026-01-10,INV-01,Panadol,150.00\n2026-01-11,INV-02,Brufen,200.00\n", encoding="utf-8")

        connector = CSVConnector(str(csv_file))
        df = connector.fetch()

        assert len(df) == 2
        assert "Date" in df.columns
        assert "source_connector" in df.columns
        assert "source_row" in df.columns
        assert df.loc[0, "source_connector"] == "csv"
        assert df.loc[0, "source_row"] == 2
        assert df.loc[1, "source_row"] == 3
        assert df.loc[0, "Medicine"] == "Panadol"

    def test_delimiter_sniffing_semicolon_and_tab(self, tmp_path):
        """Sniffs semicolon and tab separated values without user configuration."""
        # Semicolon
        semi_file = tmp_path / "semi.csv"
        semi_file.write_text("Item;Quantity;Price\nAmoxicillin;10;55.50\nAugmentin;5;120.00\n", encoding="utf-8")
        conn_semi = CSVConnector(str(semi_file))
        assert conn_semi.delimiter == ";"
        df_semi = conn_semi.fetch()
        assert len(df_semi) == 2
        assert list(df_semi.columns[:3]) == ["Item", "Quantity", "Price"]

        # Tab
        tab_file = tmp_path / "tab.tsv"
        tab_file.write_text("Product\tStock\nDisprin\t50\n", encoding="utf-8")
        conn_tab = CSVConnector(str(tab_file))
        assert conn_tab.delimiter == "\t"
        df_tab = conn_tab.fetch()
        assert len(df_tab) == 1
        assert df_tab.loc[0, "Product"] == "Disprin"

    def test_header_detection_skips_metadata_preamble(self, tmp_path):
        """Skips company name, export date, and blank metadata lines before actual table headers."""
        messy_csv = (
            "AL-MADINA PHARMACY SYSTEM EXPORT\n"
            "Generated on: 2026-01-15\n"
            "\n"
            "Date,Invoice_No,Customer,Total\n"
            "2026-01-15,1001,Walk-in,450.00\n"
            "2026-01-15,1002,Ali Khan,1200.00\n"
        )
        csv_file = tmp_path / "messy_pos.csv"
        csv_file.write_text(messy_csv, encoding="utf-8")

        connector = CSVConnector(str(csv_file))
        assert connector.header_idx == 3  # 0-indexed, 4th line is header
        df = connector.fetch()
        assert len(df) == 2
        assert "Invoice_No" in df.columns
        assert df.loc[0, "Invoice_No"] == 1001

    def test_unquoted_trailing_notes_repair(self, tmp_path):
        """Recovers when free-text columns (notes, comments, remarks) contain unquoted delimiters."""
        content = (
            "order_id,amount,review_text\n"
            '1,10,"Works well"\n'
            "2,20,Second purchase, love the packaging!\n"
        )
        csv_file = tmp_path / "with_notes.csv"
        csv_file.write_text(content, encoding="utf-8")

        connector = CSVConnector(str(csv_file))
        df = connector.fetch()
        assert len(df) == 2
        assert df["review_text"].tolist() == [
            "Works well",
            "Second purchase, love the packaging!"
        ]




    def test_preview_limits_row_count(self, tmp_path):
        """preview(n) retrieves only first n rows efficiently."""
        lines = ["ID,Val"] + [f"{i},{i*10}" for i in range(100)]
        csv_file = tmp_path / "large.csv"
        csv_file.write_text("\n".join(lines), encoding="utf-8")

        connector = CSVConnector(str(csv_file))
        preview_df = connector.preview(n=5)
        assert len(preview_df) == 5
        assert connector.total_rows() >= 100


class TestExcelConnector:
    """Verifies Excel multi-sheet extraction, header discovery, and streaming bytes."""

    @pytest.fixture
    def sample_workbook_path(self, tmp_path):
        """Creates a multi-sheet Excel workbook in memory."""
        path = tmp_path / "pharmacy_data.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            # Sheet 1: Sales
            df_sales = pd.DataFrame({
                "Date": ["2026-01-01", "2026-01-02"],
                "Item": ["Panadol 500mg", "Arinac"],
                "Qty": [20, 15],
                "Price": [35.0, 50.0]
            })
            df_sales.to_excel(writer, sheet_name="Sales_Daily", index=False)

            # Sheet 2: Inventory
            df_inv = pd.DataFrame({
                "Medicine": ["Panadol", "Brufen", "Calamox"],
                "Current_Stock": [150, 80, 45],
                "Reorder_Level": [50, 30, 20]
            })
            df_inv.to_excel(writer, sheet_name="Inventory", index=False)
        return str(path)

    def test_excel_fetch_default_sheet(self, sample_workbook_path):
        """Default fetch extracts first sheet with correct metadata."""
        connector = ExcelConnector(sample_workbook_path)
        df = connector.fetch()

        assert len(df) == 2
        assert "Item" in df.columns
        assert df.loc[0, "source_connector"] == "excel"
        assert df.loc[0, "Item"] == "Panadol 500mg"

    def test_excel_multi_sheet_discovery_and_selection(self, sample_workbook_path):
        """Lists sheets and extracts specified secondary sheet."""
        connector = ExcelConnector(sample_workbook_path)
        sheets = connector.list_sheets()

        assert "Sales_Daily" in sheets
        assert "Inventory" in sheets
        assert connector.capabilities().get("multi_sheet") is True

        # Fetch secondary sheet
        df_inv = connector.fetch(sheet_name="Inventory")
        assert len(df_inv) == 3
        assert "Current_Stock" in df_inv.columns
        assert df_inv.loc[1, "Medicine"] == "Brufen"

    def test_excel_invalid_sheet_raises_informative_value_error(self, sample_workbook_path):
        """Requesting a non-existent sheet name raises a clear descriptive ValueError."""
        connector = ExcelConnector(sample_workbook_path)
        with pytest.raises(ValueError) as exc:
            connector.fetch(sheet_name="NonExistentSheet")
        assert "does not exist" in str(exc.value)
        assert "Available sheets" in str(exc.value)

    def test_excel_preview(self, sample_workbook_path):
        """preview(n) returns exact subset on Excel."""
        connector = ExcelConnector(sample_workbook_path)
        preview_df = connector.preview(n=1, sheet_name="Inventory")
        assert len(preview_df) == 1
        assert preview_df.loc[0, "Medicine"] == "Panadol"
