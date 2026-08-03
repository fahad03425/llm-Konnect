import json
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
import pandas as pd

class Problem:
    """
    Structured problem record for validation.
    Supports both aggregate (vectorized) problems across multiple rows and single-row problems.
    """
    def __init__(
        self, 
        severity: str, 
        message: str, 
        row_index: Optional[int] = None, 
        column: Optional[str] = None,
        code: str = "GENERIC_PROBLEM",
        field: Optional[str] = None,
        row_refs: Optional[List[int]] = None,
        count: Optional[int] = None,
        sample: Optional[List[Any]] = None
    ):
        self.severity = severity.lower()  # "error", "warning", or "info"
        self.message = message
        self.code = code
        self.field = field or column
        self.column = self.field
        
        if row_refs is not None:
            self.row_refs = row_refs
        elif row_index is not None:
            self.row_refs = [row_index]
        else:
            self.row_refs = []

        self.row_index = self.row_refs[0] if self.row_refs else None
        self.count = count if count is not None else len(self.row_refs)
        self.sample = sample if sample is not None else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "column": self.column,
            "row_refs": self.row_refs,
            "row_index": self.row_index,
            "count": self.count,
            "sample": self.sample
        }

    def __repr__(self) -> str:
        ref_str = f"Rows: {self.row_refs[:3]}..." if len(self.row_refs) > 3 else f"Rows: {self.row_refs}"
        return f"[{self.severity.upper()}][{self.code}] {self.message} ({ref_str}, Field: {self.field})"


class ValidationReport:
    """
    Structured, serializable report for a dataset validation run.
    Contains table-level summary statistics, an overall verdict, and detailed problems.
    """
    def __init__(
        self,
        total_rows: int,
        error_rows: int,
        warning_rows: int,
        null_counts: Dict[str, int],
        verdict: str,
        problems: List[Problem]
    ):
        self.total_rows = total_rows
        self.error_rows = error_rows
        self.warning_rows = warning_rows
        self.null_counts = null_counts
        self.verdict = verdict  # "usable", "usable_with_warnings", "not_usable"
        self.problems = problems

    @property
    def errors(self) -> List[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def warnings(self) -> List[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    @property
    def info(self) -> List[Problem]:
        return [p for p in self.problems if p.severity == "info"]

    @property
    def is_usable(self) -> bool:
        return self.verdict in ("usable", "usable_with_warnings")

    def __iter__(self):
        return iter(self.problems)

    def __len__(self):
        return len(self.problems)

    def __getitem__(self, item):
        return self.problems[item]

    def to_dict(self) -> Dict[str, Any]:

        return {
            "total_rows": self.total_rows,
            "error_rows": self.error_rows,
            "warning_rows": self.warning_rows,
            "null_counts": self.null_counts,
            "verdict": self.verdict,
            "is_usable": self.is_usable,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "problems": [p.to_dict() for p in self.problems]
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class CleaningSummary:
    """
    Auditable summary of an opt-in dataset cleaning pass.
    """
    def __init__(
        self,
        original_rows: int,
        cleaned_rows: int,
        rows_dropped: int,
        empty_rows_dropped: int,
        duplicate_rows_dropped: int,
        whitespace_trimmed_cells: int,
        details: List[str]
    ):
        self.original_rows = original_rows
        self.cleaned_rows = cleaned_rows
        self.rows_dropped = rows_dropped
        self.empty_rows_dropped = empty_rows_dropped
        self.duplicate_rows_dropped = duplicate_rows_dropped
        self.whitespace_trimmed_cells = whitespace_trimmed_cells
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_rows": self.original_rows,
            "cleaned_rows": self.cleaned_rows,
            "rows_dropped": self.rows_dropped,
            "empty_rows_dropped": self.empty_rows_dropped,
            "duplicate_rows_dropped": self.duplicate_rows_dropped,
            "whitespace_trimmed_cells": self.whitespace_trimmed_cells,
            "details": self.details
        }


class DomainPack(ABC):
    """
    Abstract base class for a Domain Pack.
    A Domain Pack defines extra fields, synonyms, and specific validation rules
    required for a particular business domain (e.g., pharmacy, grocery).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the domain pack (e.g., 'pharmacy')."""
        pass

    @property
    @abstractmethod
    def extra_fields(self) -> List[str]:
        """List of canonical field names introduced by this domain pack."""
        pass

    @property
    @abstractmethod
    def header_synonyms(self) -> Dict[str, List[str]]:
        """
        Dictionary mapping canonical field names (core + extra) 
        to a list of common raw header names found in real-world exports.
        """
        pass

    def validate_row(self, row: dict, index: int) -> List[Problem]:
        """
        Legacy row-by-row validation rule runner.
        Subclasses should implement validate_dataframe for vectorized checks.
        """
        return []

    def validate_dataframe(self, df: pd.DataFrame) -> List[Problem]:
        """
        Vectorized validation rule runner over a canonical DataFrame.
        Defaults to executing validate_row over records for backward compatibility.
        """
        problems = []
        records = df.replace({pd.NA: None, pd.NaT: None}).to_dict(orient="records")
        for i, row in enumerate(records):
            row_idx = row.get("source_row", i + 1)
            problems.extend(self.validate_row(row, row_idx))
        return problems


class DomainRegistry:
    def __init__(self):
        self._packs: Dict[str, DomainPack] = {}

    def register(self, pack: DomainPack):
        self._packs[pack.name] = pack

    def get(self, name: str) -> DomainPack:
        if name not in self._packs:
            raise ValueError(f"Domain pack '{name}' not found.")
        return self._packs[name]

    def available_domains(self) -> List[str]:
        return list(self._packs.keys())


registry = DomainRegistry()

def get_domain_pack(name: str) -> DomainPack:
    return registry.get(name)

