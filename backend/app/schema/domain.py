from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional

class Problem:
    def __init__(self, severity: str, message: str, row_index: Optional[int] = None, column: Optional[str] = None):
        self.severity = severity  # "error" or "warning"
        self.message = message
        self.row_index = row_index
        self.column = column

    def to_dict(self):
        return {
            "severity": self.severity,
            "message": self.message,
            "row_index": self.row_index,
            "column": self.column
        }
    
    def __repr__(self):
        return f"[{self.severity.upper()}] {self.message} (Row: {self.row_index}, Col: {self.column})"


class DomainPack(ABC):
    """
    Abstract base class for a Domain Pack.
    A Domain Pack defines the extra fields, synonyms, and specific validation rules
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

    @abstractmethod
    def validate_row(self, row: dict, index: int) -> List[Problem]:
        """
        Run domain-specific validation rules on a normalized row.
        Returns a list of Problem objects (warnings or errors).
        """
        pass


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
