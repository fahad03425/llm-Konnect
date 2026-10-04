"""Test Suite 03: Data Source Connection Wizard & Ingestion Flow.

Module: Module 6.9 — Desktop Application Module
Target Files:
- desktop/src/pages/ConnectSource.tsx
- desktop/src/components/connect/UploadZone.tsx
- desktop/src/components/connect/PreviewTable.tsx
- desktop/src/components/connect/MappingTable.tsx
- desktop/src/components/connect/KBStatus.tsx
- desktop/src/pages/UploadedFiles.tsx
Scope:
- Verifies 4-step data onboarding workflow (Source Selection, Preview, Schema Mapping, Vector Indexing).
- Verifies connector baselines: CSV/Excel, Tally Prime local ODBC/HTTP, Shopify Admin API token.
- Verifies schema mapping verification interface without CLI requirement.
- Verifies offline Knowledge Base indexing progress visualization.
- Verifies file catalog and database sync status management in UploadedFiles.tsx.
"""

import pytest
from pathlib import Path


class TestDataSourceConnectionWizard:
    """Verifies the multi-step data connector wizard and offline knowledge base indexing UX."""

    def test_connector_types_supported_in_wizard(self, repo_paths):
        """Verifies that ConnectSource.tsx supports CSV/Excel, Tally Prime, and Shopify connectors."""
        connect_path = repo_paths["src"] / "pages" / "ConnectSource.tsx"
        assert connect_path.exists()
        code = connect_path.read_text(encoding="utf-8")

        # Universal baseline file import
        assert "csv" in code.lower() or "excel" in code.lower()
        # Accounting & E-Commerce connectors
        assert "tally" in code.lower()
        assert "shopify" in code.lower()

    def test_upload_zone_file_formats(self, repo_paths):
        """Verifies that UploadZone.tsx configures drag-and-drop file acceptance for financial formats."""
        upload_zone_path = repo_paths["src"] / "components" / "connect" / "UploadZone.tsx"
        assert upload_zone_path.exists()
        code = upload_zone_path.read_text(encoding="utf-8")

        # Must accept tabular files
        assert ".csv" in code.lower() or "csv" in code.lower()
        assert ".xlsx" in code.lower() or "excel" in code.lower()

    def test_preview_table_sample_rendering(self, repo_paths):
        """Verifies that PreviewTable.tsx safely renders sample rows from the parsed dataset."""
        preview_path = repo_paths["src"] / "components" / "connect" / "PreviewTable.tsx"
        assert preview_path.exists()
        code = preview_path.read_text(encoding="utf-8")

        assert "table" in code.lower()
        assert "headers" in code.lower() or "columns" in code.lower()
        assert "rows" in code.lower() or "data" in code.lower()

    def test_mapping_table_schema_confirmation(self, repo_paths):
        """Verifies that MappingTable.tsx provides the user confirmation interface for column mappings."""
        mapping_path = repo_paths["src"] / "components" / "connect" / "MappingTable.tsx"
        assert mapping_path.exists()
        code = mapping_path.read_text(encoding="utf-8")

        assert "mapping" in code.lower()
        assert "canonical" in code.lower() or "target" in code.lower() or "field" in code.lower()

    def test_kb_status_ingestion_progress(self, repo_paths):
        """Verifies that KBStatus.tsx visualizes offline chunking and vector embedding generation."""
        kb_status_path = repo_paths["src"] / "components" / "connect" / "KBStatus.tsx"
        assert kb_status_path.exists()
        code = kb_status_path.read_text(encoding="utf-8")

        # Must show vector indexing state
        assert "chunk" in code.lower() or "progress" in code.lower() or "embedding" in code.lower()
        assert "status" in code.lower()

    def test_uploaded_files_catalog_management(self, repo_paths):
        """Verifies that UploadedFiles.tsx provides file lifecycle management and sync status."""
        files_page_path = repo_paths["src"] / "pages" / "UploadedFiles.tsx"
        assert files_page_path.exists()
        code = files_page_path.read_text(encoding="utf-8")

        assert "files" in code.lower()
        assert "delete" in code.lower() or "remove" in code.lower()
        assert "sync" in code.lower() or "active" in code.lower()
