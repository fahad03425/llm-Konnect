"""
Module 6.6 / 6.5 — High-Performance In-Memory Analytics & DataFrame Cache.

Caches normalized canonical DataFrames and multi-table combined query frames
so repetitive analytical questions (and sub-queries) run in milliseconds instead of
re-reading disk/DB and re-running expensive schema normalization.
"""

import time
import threading
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

class AnalyticsCache:
    """Thread-safe LRU-like in-memory cache for canonical DataFrames and analytics records."""

    def __init__(self, max_entries: int = 256, ttl_seconds: float = 3600.0):
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        # Storage: key -> {"data": Any, "timestamp": float, "meta": Any}
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if (time.time() - entry["timestamp"]) > self._ttl_seconds:
                self._cache.pop(key, None)
                return None
            # Update access timestamp for LRU behavior
            entry["timestamp"] = time.time()
            return entry["data"]

    def set(self, key: str, data: Any, meta: Any = None) -> None:
        with self._lock:
            # LRU eviction if full
            if len(self._cache) >= self._max_entries and key not in self._cache:
                # Evict oldest entry
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k]["timestamp"])
                self._cache.pop(oldest_key, None)

            self._cache[key] = {
                "data": data,
                "timestamp": time.time(),
                "meta": meta
            }

    def clear(self, pattern: Optional[str] = None) -> int:
        """Clear all entries, or entries containing `pattern` in their key."""
        with self._lock:
            if not pattern:
                count = len(self._cache)
                self._cache.clear()
                return count
            
            pat_lower = pattern.strip().lower()
            keys_to_remove = [k for k in self._cache if pat_lower in k.lower()]
            for k in keys_to_remove:
                self._cache.pop(k, None)
            return len(keys_to_remove)

    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# Global singleton cache instances
_table_cache = AnalyticsCache(max_entries=256, ttl_seconds=3600.0)
_query_records_cache = AnalyticsCache(max_entries=128, ttl_seconds=1800.0)


def get_cached_table(cache_key: str) -> Optional[Tuple[pd.DataFrame, Dict[str, str]]]:
    """Retrieve cached canonical table DataFrame and mapping."""
    res = _table_cache.get(cache_key)
    if res is not None:
        # Return a shallow copy of DataFrame so manipulations don't mutate cache
        df, mapping = res
        return df.copy(deep=False), dict(mapping)
    return None


def set_cached_table(cache_key: str, df: pd.DataFrame, mapping: Dict[str, str]) -> None:
    """Store canonical table DataFrame and mapping in memory."""
    _table_cache.set(cache_key, (df, mapping))


def get_cached_analytics_records(cache_key: str) -> Optional[Tuple[Any, List[Any]]]:
    """Retrieve pre-aggregated DataFrame/records and citation chunks for analytics."""
    res = _query_records_cache.get(cache_key)
    if res is not None:
        records, chunks = res
        if isinstance(records, pd.DataFrame):
            return records.copy(deep=False), list(chunks)
        return records, list(chunks)
    return None


def set_cached_analytics_records(cache_key: str, records: Any, citation_chunks: List[Any]) -> None:
    """Store pre-aggregated DataFrame/records and citation chunks."""
    _query_records_cache.set(cache_key, (records, citation_chunks))


def clear_analytics_cache(pattern: Optional[str] = None) -> int:
    """Purge in-memory analytics caches."""
    c1 = _table_cache.clear(pattern)
    c2 = _query_records_cache.clear(pattern)
    return c1 + c2
