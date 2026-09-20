"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          LLM-Konnect — RAG Chatbot Stress Test Suite                        ║
║          Scope: Sales Header & Purchase Header (Pharma DB)                  ║
║          Run:   python scripts/test_rag_chatbot.py                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

Prerequisites:
  1. Backend running:  uvicorn app.main:app --host 127.0.0.1 --port 8760
  2. Both "Sales Header" and "Purchase Header" tables ingested and active
  3. (Optional) Set env var SALES_FILE_ID and PURCHASE_FILE_ID to scope to
     specific file IDs; otherwise the chatbot uses its global active scope.

Usage:
  # Basic run (prints results to terminal)
  python scripts/test_rag_chatbot.py

  # Save an HTML report
  python scripts/test_rag_chatbot.py --report

  # Target a different backend host
  python scripts/test_rag_chatbot.py --host http://127.0.0.1:8760

  # Run a single category
  python scripts/test_rag_chatbot.py --category analytics

  # Scope to specific ingested table IDs
  python scripts/test_rag_chatbot.py --sales-id <id> --purchase-id <id>
"""

import argparse
import datetime
import io
import json
import os
import sys

# Force UTF-8 stdout on Windows so unicode chars don't hit cp1252 limits
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Live file logger: mirrors all print() output to a log file in real-time ──
class _TeeLogger:
    """Writes to both the original stdout AND a log file simultaneously."""
    def __init__(self, original, log_path: str):
        self._orig = original
        self._log  = open(log_path, "w", encoding="utf-8", buffering=1)  # line-buffered

    def write(self, msg):
        self._orig.write(msg)
        self._orig.flush()
        self._log.write(msg)
        self._log.flush()

    def flush(self):
        self._orig.flush()
        self._log.flush()

    def close(self):
        self._log.close()

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_run_live.log")
sys.stdout = _TeeLogger(sys.stdout, _LOG_PATH)

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' library not found. Install it: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_HOST = "http://127.0.0.1:8760"
CHAT_ENDPOINT = "/api/chat"
HEALTH_ENDPOINT = "/api/health"

# Optional: set these to restrict tests to specific ingested table IDs.
# Leave None to let the chatbot use its global active scope.
SALES_FILE_ID: Optional[str] = os.environ.get("SALES_FILE_ID")
PURCHASE_FILE_ID: Optional[str] = os.environ.get("PURCHASE_FILE_ID")

DOMAIN = "pharmacy"
REQUEST_TIMEOUT = 120  # seconds — LLM can be slow on first call

# ANSI colour codes (disabled on plain Windows unless Windows Terminal is detected)
_COLOUR = sys.platform != "win32" or os.environ.get("WT_SESSION")
GREEN  = "\033[92m" if _COLOUR else ""
YELLOW = "\033[93m" if _COLOUR else ""
RED    = "\033[91m" if _COLOUR else ""
CYAN   = "\033[96m" if _COLOUR else ""
BOLD   = "\033[1m"  if _COLOUR else ""
RESET  = "\033[0m"  if _COLOUR else ""

# ASCII-safe status symbols (avoids cp1252 issues on Windows)
PASS_SYM = "[OK]  "
FAIL_SYM = "[FAIL]"


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    id: str                              # e.g. "C1-Q1"
    category: str                        # e.g. "basic_lookup"
    question: str
    expected_route: str                  # "rag" | "analytics" | "chit-chat"
    check_fn: Optional[Any] = None       # fn(response_json) -> (bool, str)
    notes: str = ""                      # human-readable expectation
    is_multipart: bool = False           # True = part of a multi-turn test
    session_id: Optional[str] = None    # shared across a multi-turn group


@dataclass
class TestResult:
    test_id: str
    category: str
    question: str
    expected_route: str
    actual_route: str
    answer: str
    passed: bool
    failure_reason: str
    timing_s: float
    http_status: int
    sources_count: int
    has_computed_values: bool
    notes: str


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build file_ids list from global vars
# ─────────────────────────────────────────────────────────────────────────────

def _file_ids() -> Optional[List[str]]:
    ids = [fid for fid in [SALES_FILE_ID, PURCHASE_FILE_ID] if fid]
    return ids if ids else None


# ─────────────────────────────────────────────────────────────────────────────
# Check Functions  (assertions applied to the ChatResponse JSON)
# ─────────────────────────────────────────────────────────────────────────────

def _check_not_empty(r: dict):
    """Answer must be non-empty and not a raw error string."""
    a = r.get("answer", "").strip()
    if not a:
        return False, "Answer is empty"
    if "error" in a.lower() and len(a) < 80:
        return False, f"Answer looks like an error: {a[:100]}"
    return True, ""


def _check_has_number(r: dict):
    """Analytics responses must contain at least one digit in the answer."""
    import re
    a = r.get("answer", "")
    if re.search(r"\d", a):
        return True, ""
    if r.get("computed_values"):
        return True, ""   # computed_values present is an acceptable substitute
    return False, "Expected a numeric answer but found none"


def _check_no_hallucinate_scope(r: dict):
    """
    Trap questions: the chatbot must NOT claim inventory/stock/expiry/margin
    data exists when it is not in Sales or Purchase Header.
    """
    a = r.get("answer", "").lower()
    bad_phrases = [
        "stock on hand is", "current stock is", "inventory shows",
        "expiry date is", "expires on", "batch expires",
        "profit margin is", "margin is",
    ]
    for phrase in bad_phrases:
        if phrase in a:
            return False, f"Potential hallucination: '{phrase}' found in answer"
    return True, ""


def _check_sources_present(r: dict):
    """RAG responses should cite at least one source record."""
    sources = r.get("sources", [])
    if sources:
        return True, ""
    a = r.get("answer", "").lower()
    if any(p in a for p in ["no records", "not found", "couldn't find", "cannot find", "no data"]):
        return True, ""   # graceful no-data reply is acceptable
    return False, "Expected source citations but none were returned"


def _check_refusal(r: dict):
    """Out-of-scope traps: the chatbot MUST admit it cannot answer."""
    a = r.get("answer", "").lower()
    refusal_phrases = [
        "no records", "not found", "couldn't find", "cannot find",
        "not available", "not in the", "not present", "unavailable",
        "no data", "cannot determine", "not part of", "not included",
        "outside the scope", "not in scope", "not connected",
        "no information", "no such", "doesn't exist", "does not exist",
    ]
    for phrase in refusal_phrases:
        if phrase in a:
            return True, ""
    return False, f"Expected a refusal/no-data response but got: {a[:150]}"


def _check_both_tables_mentioned(r: dict):
    """Cross-table questions should reference both sales and purchase."""
    a = r.get("answer", "").lower()
    has_sales    = any(k in a for k in ["sale", "sales"])
    has_purchase = any(k in a for k in ["purchase", "purchases"])
    if has_sales and has_purchase:
        return True, ""
    missing = (["sales"] if not has_sales else []) + (["purchase"] if not has_purchase else [])
    return False, f"Answer is missing reference to: {', '.join(missing)}"


def _check_lists_sources(r: dict):
    """Data-source inquiry must name at least one known table."""
    a = r.get("answer", "").lower()
    keywords = [
        "sales header", "purchase header", "sales", "purchase",
        "table", "dataset", "source",
    ]
    if any(k in a for k in keywords):
        return True, ""
    return False, "Expected data source/table names in the answer"


def _combine(*checks):
    """Run multiple check functions; fail fast on first failure."""
    def _fn(r: dict):
        for c in checks:
            ok, reason = c(r)
            if not ok:
                return False, reason
        return True, ""
    return _fn


# ─────────────────────────────────────────────────────────────────────────────
# Multi-turn session IDs  (shared within each conversation group)
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_MT1 = str(uuid.uuid4())
_SESSION_MT2 = str(uuid.uuid4())
_SESSION_MT3 = str(uuid.uuid4())
_SESSION_MT4 = str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Test Case Definitions  — 39 questions across 8 categories
# ─────────────────────────────────────────────────────────────────────────────

TEST_CASES: List[TestCase] = [

    # ── Category 1: Basic Record Lookup ──────────────────────────────────────
    TestCase(
        id="C1-Q1", category="basic_lookup",
        question="Show me the latest 5 sales transactions.",
        expected_route="rag",
        check_fn=_combine(_check_not_empty, _check_sources_present),
        notes="Must return recent Sales Header records with source citations.",
    ),
    TestCase(
        id="C1-Q2", category="basic_lookup",
        question="Who is the most recent customer in the sales header?",
        expected_route="rag",
        check_fn=_combine(_check_not_empty, _check_sources_present),
        notes="Should return the latest customer name from Sales Header.",
    ),
    TestCase(
        id="C1-Q3", category="basic_lookup",
        question="What is the invoice number of the first purchase ever recorded?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Should return the oldest entry or admit uncertainty about ordering.",
    ),
    TestCase(
        id="C1-Q4", category="basic_lookup",
        question="Find me all records where the supplier name contains Medical.",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Supplier substring filter from Purchase Header.",
    ),
    TestCase(
        id="C1-Q5", category="basic_lookup",
        question="Show me any sale that has a discount applied.",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Tests discount field retrieval from Sales Header.",
    ),

    # ── Category 2: Aggregate Analytics ──────────────────────────────────────
    TestCase(
        id="C2-Q6", category="analytics",
        question="What is the total sales amount across all records?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Must route to ANALYTICS and return a computed sum with currency.",
    ),
    TestCase(
        id="C2-Q7", category="analytics",
        question="What is the total purchase amount?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Purchase Header total only — must NOT mix with Sales data.",
    ),
    TestCase(
        id="C2-Q8", category="analytics",
        question="What is the average invoice value in the sales header?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Must return average, not total.",
    ),
    TestCase(
        id="C2-Q9", category="analytics",
        question="How many purchase records are there?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Row count from Purchase Header specifically.",
    ),
    TestCase(
        id="C2-Q10", category="analytics",
        question="What is the highest single sale amount ever recorded?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Max value from Sales Header. Should cite the invoice.",
    ),
    TestCase(
        id="C2-Q11", category="analytics",
        question="What is the lowest purchase amount?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Min from Purchase Header — tests min vs max distinction.",
    ),
    TestCase(
        id="C2-Q12", category="analytics",
        question="What is the total number of rows across both sales and purchase headers?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number, _check_both_tables_mentioned),
        notes="Should report counts for each table separately before combining.",
    ),

    # ── Category 3: Date-Filtered Analytics ──────────────────────────────────
    TestCase(
        id="C3-Q13", category="date_filtered",
        question="What was the total sales revenue in January?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Must extract month=1 filter and compute Sales Header sum for January.",
    ),
    TestCase(
        id="C3-Q14", category="date_filtered",
        question="What were total purchases from 1st March to 31st March?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Date range filter on Purchase Header for full March.",
    ),
    TestCase(
        id="C3-Q15", category="date_filtered",
        question="How many sales happened between January 1st and February 28th 2025?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Multi-month range count scoped to Sales Header.",
    ),
    TestCase(
        id="C3-Q16", category="date_filtered",
        question="Compare total sales and total purchases for the month of March.",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number, _check_both_tables_mentioned),
        notes="Cross-table comparison within a single month — both values must be labeled.",
    ),
    TestCase(
        id="C3-Q17", category="date_filtered",
        question="What was the average purchase value in the last quarter?",
        expected_route="analytics",
        check_fn=_check_not_empty,
        notes="Relative date — chatbot should compute or ask for clarification.",
    ),

    # ── Category 4: Cross-Table Reasoning ────────────────────────────────────
    TestCase(
        id="C4-Q18", category="cross_table",
        question="What is the difference between total purchases and total sales?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number, _check_both_tables_mentioned),
        notes="Must compute both independently then subtract.",
    ),
    TestCase(
        id="C4-Q19", category="cross_table",
        question="Which is higher — our total purchases or our total sales?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_both_tables_mentioned),
        notes="Must name the winner and give both values.",
    ),
    TestCase(
        id="C4-Q20", category="cross_table",
        question="Are there any purchase records from the same date as our highest-value sale?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Advanced cross-table date correlation — hardest reasoning test.",
    ),
    TestCase(
        id="C4-Q21", category="cross_table",
        question="What percentage of total revenue do sales represent compared to purchases?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Ratio calculation across two tables.",
    ),

    # ── Category 5: Trap / Edge Cases ────────────────────────────────────────
    TestCase(
        id="C5-Q22", category="trap",
        question="What is the total sales in the inventory table?",
        expected_route="rag",
        check_fn=_check_refusal,
        notes="TRAP: 'inventory table' is not in scope — must refuse gracefully.",
    ),
    TestCase(
        id="C5-Q23", category="trap",
        question="What is the profit margin on each product?",
        expected_route="rag",
        check_fn=_combine(_check_not_empty, _check_no_hallucinate_scope),
        notes="TRAP: Must NOT hallucinate margin data if cost+price columns are absent.",
    ),
    TestCase(
        id="C5-Q24", category="trap",
        question="Tell me about expiring medicines.",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="If expiry data not in Sales/Purchase Header, must clearly say so.",
    ),
    TestCase(
        id="C5-Q25", category="trap",
        question="What is the total sales for XYZ Pharma Ltd?",
        expected_route="analytics",
        check_fn=_check_not_empty,
        notes="TRAP: If supplier doesn't exist, must say 'no records' not invent a number.",
    ),
    TestCase(
        id="C5-Q26", category="trap",
        question="What is the stock on hand for Panadol?",
        expected_route="rag",
        check_fn=_combine(_check_not_empty, _check_no_hallucinate_scope),
        notes="TRAP: Stock/inventory is not a Sales/Purchase Header concept.",
    ),
    TestCase(
        id="C5-Q27", category="trap",
        question="What was the revenue in 1999?",
        expected_route="analytics",
        check_fn=_check_refusal,
        notes="Year out of data range — must report 'no records for 1999' gracefully.",
    ),

    # ── Category 6: Multi-Turn Conversation ──────────────────────────────────
    TestCase(
        id="C6-Q28a", category="multi_turn",
        question="What is total sales?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Turn 1/2: seed session with a known analytics answer.",
        is_multipart=True, session_id=_SESSION_MT1,
    ),
    TestCase(
        id="C6-Q28b", category="multi_turn",
        question="Are you sure?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Turn 2/2: must re-confirm the SAME value. No new numbers invented.",
        is_multipart=True, session_id=_SESSION_MT1,
    ),
    TestCase(
        id="C6-Q29a", category="multi_turn",
        question="Show me the highest sales record.",
        expected_route="rag",
        check_fn=_combine(_check_not_empty, _check_sources_present),
        notes="Turn 1/2: retrieve top sale record.",
        is_multipart=True, session_id=_SESSION_MT2,
    ),
    TestCase(
        id="C6-Q29b", category="multi_turn",
        question="Who was the customer for that sale?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Turn 2/2: must use context from previous answer.",
        is_multipart=True, session_id=_SESSION_MT2,
    ),
    TestCase(
        id="C6-Q30a", category="multi_turn",
        question="What is total purchases?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Turn 1/2: global purchase total.",
        is_multipart=True, session_id=_SESSION_MT3,
    ),
    TestCase(
        id="C6-Q30b", category="multi_turn",
        question="What about for just January?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Turn 2/2: must apply January filter to the prior purchase query.",
        is_multipart=True, session_id=_SESSION_MT3,
    ),
    TestCase(
        id="C6-Q31a", category="multi_turn",
        question="Show me recent purchase records.",
        expected_route="rag",
        check_fn=_combine(_check_not_empty, _check_sources_present),
        notes="Turn 1/2: retrieve purchase records.",
        is_multipart=True, session_id=_SESSION_MT4,
    ),
    TestCase(
        id="C6-Q31b", category="multi_turn",
        question="Which table did that come from?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Turn 2/2: must correctly name 'Purchase Header' as the source.",
        is_multipart=True, session_id=_SESSION_MT4,
    ),

    # ── Category 7: Metadata & Scope Awareness ────────────────────────────────
    TestCase(
        id="C7-Q32", category="metadata",
        question="What data sources are you currently using?",
        expected_route="rag",
        check_fn=_check_lists_sources,
        notes="Must list Sales Header and Purchase Header as active sources.",
    ),
    TestCase(
        id="C7-Q33", category="metadata",
        question="What tables are connected?",
        expected_route="rag",
        check_fn=_check_lists_sources,
        notes="Alias for above — must list both table names.",
    ),
    TestCase(
        id="C7-Q34", category="metadata",
        question="What columns are available in the sales header?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Should enumerate field names from Sales Header records.",
    ),
    TestCase(
        id="C7-Q35", category="metadata",
        question="What columns are available in the purchase header?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Same for Purchase Header.",
    ),

    # ── Category 8: Urdu / Roman-Urdu ────────────────────────────────────────
    TestCase(
        id="C8-Q36", category="urdu",
        question="Total sales kitni hai?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Urdu keyword 'kitni' must trigger ANALYTICS route.",
    ),
    TestCase(
        id="C8-Q37", category="urdu",
        question="Kitne purchase records hain?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="'Kitne' → ANALYTICS. Returns row count from Purchase Header.",
    ),
    TestCase(
        id="C8-Q38", category="urdu",
        question="Sab se zyada sale kab hui?",
        expected_route="rag",
        check_fn=_check_not_empty,
        notes="Should return the date of the maximum sale.",
    ),
    TestCase(
        id="C8-Q39", category="urdu",
        question="Purchase mein sab se kam amount kya hai?",
        expected_route="analytics",
        check_fn=_combine(_check_not_empty, _check_has_number),
        notes="Urdu 'sab se kam' → minimum purchase amount.",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────────────────────────────────

class ChatbotTestRunner:
    def __init__(self, host: str, category_filter: Optional[str] = None):
        self.host = host.rstrip("/")
        self.category_filter = category_filter
        self.results: List[TestResult] = []

    # ── backend health check ──────────────────────────────────────────────────
    def _health_check(self) -> bool:
        try:
            r = requests.get(f"{self.host}{HEALTH_ENDPOINT}", timeout=10)
            if r.status_code == 200:
                print(f"{GREEN}[OK] Backend reachable at {self.host}{RESET}")
                return True
            print(f"{RED}[ERR] Backend returned HTTP {r.status_code}{RESET}")
            return False
        except Exception as e:
            print(f"{RED}[ERR] Cannot reach backend: {e}{RESET}")
            return False

    # ── single chat API call ──────────────────────────────────────────────────
    def _ask(self, question: str, session_id: str):
        """Returns (response_json | None, elapsed_seconds, http_status)."""
        payload: Dict[str, Any] = {
            "question": question,
            "session_id": session_id,
            "domain": DOMAIN,
        }
        file_ids = _file_ids()
        if file_ids:
            payload["file_ids"] = file_ids

        t0 = time.time()
        try:
            resp = requests.post(
                f"{self.host}{CHAT_ENDPOINT}",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            elapsed = round(time.time() - t0, 2)
            if resp.status_code == 200:
                return resp.json(), elapsed, 200
            return None, elapsed, resp.status_code
        except requests.exceptions.Timeout:
            return None, REQUEST_TIMEOUT, 408
        except Exception:
            return None, round(time.time() - t0, 2), -1

    # ── evaluate a single test case ───────────────────────────────────────────
    def _run_one(self, tc: TestCase, session_id: str) -> TestResult:
        resp, elapsed, status = self._ask(tc.question, session_id)

        if resp is None:
            return TestResult(
                test_id=tc.id, category=tc.category, question=tc.question,
                expected_route=tc.expected_route, actual_route="—",
                answer=f"[HTTP {status} — no response]",
                passed=False, failure_reason=f"HTTP error: status {status}",
                timing_s=elapsed, http_status=status,
                sources_count=0, has_computed_values=False, notes=tc.notes,
            )

        actual_route      = resp.get("route", "unknown")
        answer            = resp.get("answer", "")
        sources_count     = len(resp.get("sources", []))
        has_cv            = bool(resp.get("computed_values"))

        passed, failure_reason = True, ""
        if tc.check_fn:
            passed, failure_reason = tc.check_fn(resp)

        return TestResult(
            test_id=tc.id, category=tc.category, question=tc.question,
            expected_route=tc.expected_route, actual_route=actual_route,
            answer=answer, passed=passed, failure_reason=failure_reason,
            timing_s=elapsed, http_status=status,
            sources_count=sources_count, has_computed_values=has_cv,
            notes=tc.notes,
        )

    # ── run all (or filtered) test cases ─────────────────────────────────────
    def run(self) -> List[TestResult]:
        if not self._health_check():
            print(f"\n{RED}Aborting: backend not reachable.{RESET}")
            return []

        cases = TEST_CASES
        if self.category_filter:
            cases = [tc for tc in TEST_CASES if tc.category == self.category_filter]
            if not cases:
                print(f"{YELLOW}No tests found for category '{self.category_filter}'{RESET}")
                return []

        print(f"\n{BOLD}Running {len(cases)} test(s)…{RESET}")
        if _file_ids():
            print(f"{CYAN}Scoped to file IDs: {_file_ids()}{RESET}")
        else:
            print(f"{CYAN}No file_ids set — using global active scope{RESET}")

        for tc in cases:
            # Multi-turn tests share a session; single tests get a fresh one
            session_id = tc.session_id if tc.is_multipart else str(uuid.uuid4())
            result = self._run_one(tc, session_id)
            self.results.append(result)
            self._print_result(result)

        self._print_summary()
        return self.results

    # ── terminal output helpers ───────────────────────────────────────────────
    def _print_result(self, r: TestResult):
        icon      = f"{GREEN}{PASS_SYM}{RESET}" if r.passed else f"{RED}{FAIL_SYM}{RESET}"
        route_ok  = r.actual_route == r.expected_route
        route_col = GREEN if route_ok else YELLOW
        print(
            f"\n[{r.test_id}] {icon}  {BOLD}{r.category.upper()}{RESET}  "
            f"({r.timing_s}s | HTTP {r.http_status})"
        )
        print(f"  Q: {r.question}")
        print(
            f"  Route: {route_col}{r.actual_route}{RESET} "
            f"(expected {r.expected_route})  "
            f"| Sources: {r.sources_count}  | CV: {r.has_computed_values}"
        )
        short_ans = r.answer[:200].replace("\n", " ")
        if len(r.answer) > 200:
            short_ans += "..."
        print(f"  A: {short_ans}")
        if not r.passed:
            print(f"  {RED}Reason: {r.failure_reason}{RESET}")

    def _print_summary(self):
        total  = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        avg_t  = round(sum(r.timing_s for r in self.results) / total, 2) if total else 0

        print("\n" + "=" * 70)
        print(f"{BOLD}TEST SUMMARY{RESET}")
        print("=" * 70)
        print(f"  Total : {total}")
        print(f"  {GREEN}Passed: {passed}{RESET}")
        print(f"  {RED}Failed: {failed}{RESET}")
        print(f"  Avg response time: {avg_t}s")
        print("-" * 70)

        categories: Dict[str, Dict] = {}
        for r in self.results:
            cat = categories.setdefault(r.category, {"pass": 0, "fail": 0})
            if r.passed:
                cat["pass"] += 1
            else:
                cat["fail"] += 1

        print(f"  {'Category':<20} {'Pass':>6} {'Fail':>6}")
        print("  " + "-" * 34)
        for cat, counts in sorted(categories.items()):
            col = GREEN if counts["fail"] == 0 else (RED if counts["pass"] == 0 else YELLOW)
            print(f"  {col}{cat:<20}{RESET} {counts['pass']:>6} {counts['fail']:>6}")
        print("=" * 70)

        if failed > 0:
            print(f"\n{BOLD}{RED}Failed Tests:{RESET}")
            for r in self.results:
                if not r.passed:
                    print(f"  [{r.test_id}] {r.question[:65]}...")
                    print(f"    >> {r.failure_reason}")


# ─────────────────────────────────────────────────────────────────────────────
# HTML Report Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_html_report(results: List[TestResult], output_path: str):
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    ts     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    avg_t  = round(sum(r.timing_s for r in results) / len(results), 2) if results else 0

    rows = ""
    for r in results:
        status_cls = "pass" if r.passed else "fail"
        route_cls  = "match" if r.actual_route == r.expected_route else "mismatch"
        reason_html = (
            f'<div class="reason">&#9888; {r.failure_reason}</div>' if not r.passed else ""
        )
        short_ans = r.answer[:300].replace("<", "&lt;").replace(">", "&gt;")
        if len(r.answer) > 300:
            short_ans += "…"
        rows += f"""
        <tr class="{status_cls}">
          <td><code>{r.test_id}</code></td>
          <td>{r.category}</td>
          <td>{r.question}</td>
          <td class="route {route_cls}">{r.actual_route}<br><small>exp: {r.expected_route}</small></td>
          <td class="answer">{short_ans}{reason_html}</td>
          <td>{r.sources_count}</td>
          <td>{"&#10004;" if r.has_computed_values else "&#8212;"}</td>
          <td>{r.timing_s}s</td>
          <td class="verdict">{"&#10004; PASS" if r.passed else "&#10008; FAIL"}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>RAG Chatbot Test Report — LLM-Konnect</title>
<style>
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#0f1117;color:#e2e8f0;margin:0;padding:20px}}
  h1{{color:#7dd3fc;margin-bottom:4px}}
  .meta{{color:#94a3b8;font-size:13px;margin-bottom:24px}}
  .summary{{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}}
  .card{{background:#1e2532;border-radius:10px;padding:16px 24px;min-width:140px}}
  .card .val{{font-size:36px;font-weight:700}}
  .card .lbl{{font-size:12px;color:#94a3b8;margin-top:4px}}
  .pass-card .val{{color:#4ade80}}.fail-card .val{{color:#f87171}}
  .total-card .val{{color:#7dd3fc}}.time-card .val{{color:#fbbf24;font-size:28px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{background:#1e2532;color:#7dd3fc;text-align:left;padding:10px 8px;position:sticky;top:0}}
  td{{padding:8px;border-bottom:1px solid #1e2532;vertical-align:top}}
  tr.pass{{background:#0f1e14}}tr.fail{{background:#1e100f}}
  tr:hover td{{background:#1a2236}}
  .verdict{{font-weight:700}}
  tr.pass .verdict{{color:#4ade80}}tr.fail .verdict{{color:#f87171}}
  .route{{font-family:monospace;font-size:12px}}
  .route.match{{color:#4ade80}}.route.mismatch{{color:#fbbf24}}
  .answer{{max-width:360px;font-size:12px;color:#cbd5e1}}
  .reason{{color:#f87171;font-style:italic;margin-top:6px}}
  code{{background:#1e2532;padding:1px 5px;border-radius:4px}}
</style>
</head>
<body>
<h1>&#129514; RAG Chatbot Test Report</h1>
<div class="meta">LLM-Konnect &middot; Scope: Sales Header &amp; Purchase Header &middot; {ts}</div>
<div class="summary">
  <div class="card total-card"><div class="val">{len(results)}</div><div class="lbl">Total Tests</div></div>
  <div class="card pass-card"><div class="val">{passed}</div><div class="lbl">Passed</div></div>
  <div class="card fail-card"><div class="val">{failed}</div><div class="lbl">Failed</div></div>
  <div class="card time-card"><div class="val">{avg_t}s</div><div class="lbl">Avg Response Time</div></div>
</div>
<table>
  <thead>
    <tr><th>ID</th><th>Category</th><th>Question</th><th>Route</th>
    <th>Answer</th><th>Srcs</th><th>CV</th><th>Time</th><th>Result</th></tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n{GREEN}HTML report saved to: {output_path}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="LLM-Konnect RAG Chatbot Stress Test Suite"
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"Backend base URL (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Generate a dated HTML report in the reports/ directory"
    )
    parser.add_argument(
        "--category",
        choices=[
            "basic_lookup", "analytics", "date_filtered",
            "cross_table", "trap", "multi_turn", "metadata", "urdu",
        ],
        default=None,
        help="Run only a specific category of tests"
    )
    parser.add_argument(
        "--sales-id", default=None,
        help="File ID of the Sales Header dataset (overrides SALES_FILE_ID env var)"
    )
    parser.add_argument(
        "--purchase-id", default=None,
        help="File ID of the Purchase Header dataset (overrides PURCHASE_FILE_ID env var)"
    )
    args = parser.parse_args()

    # Allow CLI overrides for file IDs
    global SALES_FILE_ID, PURCHASE_FILE_ID
    if args.sales_id:
        SALES_FILE_ID = args.sales_id
    if args.purchase_id:
        PURCHASE_FILE_ID = args.purchase_id

    runner = ChatbotTestRunner(host=args.host, category_filter=args.category)
    results = runner.run()

    if args.report and results:
        report_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports",
        )
        os.makedirs(report_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = os.path.join(report_dir, f"rag_test_report_{ts}.html")
        generate_html_report(results, report_path)

    # Non-zero exit for CI pipelines
    sys.exit(1 if any(not r.passed for r in results) else 0)


if __name__ == "__main__":
    main()
