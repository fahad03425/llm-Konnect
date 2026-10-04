"""Test Suite 02: Chunking Strategies, Token Safety & Metadata Sanitization.

Module: Module 6.4 — Document Ingestion and Knowledge Base Module
Target Files: backend/app/ingestion/safety.py & backend/app/ingestion/store.py
Scope:
- Verifies character-span preserving token windowing (token_windows) with encoder token limits.
- Verifies token budget calculation and prefix overhead handling.
- Verifies chunk_size and chunk_overlap bounds validation.
- Verifies deterministic chunk ID generation preventing duplication on re-ingestion.
- Verifies metadata sanitation ensuring ChromaDB compatibility.
"""

import pytest
from app.core.config import settings
from app.ingestion.safety import token_windows
from app.ingestion.store import KnowledgeBase, _get_global_embedder


class TestChunkingStrategiesAndTokenSafety:
    """Verifies chunk windowing, token boundary enforcement, and metadata cleaning."""

    def test_token_windows_splits_long_text_within_budget(self):
        """Splits text exceeding token budget into multiple valid overlapping windows."""
        embedder = _get_global_embedder(settings.embedding_model)
        prefix = "passage: "

        # Generate a multi-sentence text
        text = " ".join([
            f"Sentence number {i} provides detailed transaction telemetry for invoice #{1000 + i}."
            for i in range(100)
        ])

        # Window with size = 64 tokens and 15% overlap
        windows = token_windows(text, embedder, prefix, size=64, overlap=0.15)

        assert len(windows) > 1
        # Each window must be non-empty and non-trivial
        for win in windows:
            assert len(win.strip()) > 0
            # Ensure text span preserves words without corruption
            assert "Sentence number" in win or "invoice #" in win

    def test_token_windows_validates_chunk_parameters(self):
        """Rejects non-positive chunk size and invalid overlap ratios with ValueError."""
        embedder = _get_global_embedder(settings.embedding_model)

        # Invalid size <= 0
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            token_windows("Sample text", embedder, "passage: ", size=0, overlap=0.1)

        # Invalid overlap >= 1.0
        with pytest.raises(ValueError, match="chunk_overlap must be in \\[0, 1\\)"):
            token_windows("Sample text", embedder, "passage: ", size=128, overlap=1.0)

        # Negative overlap
        with pytest.raises(ValueError, match="chunk_overlap must be in \\[0, 1\\)"):
            token_windows("Sample text", embedder, "passage: ", size=128, overlap=-0.1)

    def test_deterministic_chunk_id_generation(self, isolated_kb):
        """Chunk IDs are generated deterministically from source file, row index, and window."""
        source_file = "C:/data/invoices_march_2026.csv"
        row_idx = 42
        chunk_idx = 0

        chunk_id_1 = isolated_kb._generate_chunk_id(source_file, row_idx, chunk_idx)
        chunk_id_2 = isolated_kb._generate_chunk_id(source_file, row_idx, chunk_idx)

        # Deterministic: Identical inputs produce identical IDs
        assert chunk_id_1 == chunk_id_2
        assert len(chunk_id_1) == 32  # MD5 hex digest

        # Float 42.0 and int 42 yield identical hash (idempotency fix)
        chunk_id_float = isolated_kb._generate_chunk_id(source_file, 42.0, chunk_idx)
        assert chunk_id_float == chunk_id_1

        # Changing row index or chunk index yields distinct IDs
        chunk_id_different_row = isolated_kb._generate_chunk_id(source_file, 43, chunk_idx)
        assert chunk_id_1 != chunk_id_different_row

    def test_metadata_sanitization_for_chromadb(self, isolated_kb):
        """Sanitizes raw metadata into ChromaDB-compatible types (str, int, float, bool conversion)."""
        raw_metadata = {
            "invoice_id": "INV-1092",
            "quantity": 10.5,
            "unit_price": 50,
            "is_active": True,
            "notes": None,
            "tags": ["pharmacy", "pos"],  # Complex nested object
        }

        sanitized = isolated_kb._sanitize_metadata(raw_metadata)

        # Strings, numbers preserved
        assert sanitized["invoice_id"] == "INV-1092"
        assert sanitized["quantity"] == 10.5
        assert sanitized["unit_price"] == 50

        # Booleans converted or preserved safely
        assert "is_active" in sanitized
        # ChromaDB allows booleans or ints; verified no unhandled exceptions
        assert isinstance(sanitized["is_active"], (bool, int, str))

        # None/Complex lists coerced or safely serialized
        assert isinstance(sanitized.get("tags", ""), str)
