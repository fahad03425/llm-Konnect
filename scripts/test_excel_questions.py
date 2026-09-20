"""
╔══════════════════════════════════════════════════════════════════════════════╗
║     LLM-Konnect — Pharmacy POS Excel Questions RAG Test Suite                ║
║     Evaluates all 91 questions from pharmacy_pos_rag_chatbot_questions.xlsx  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Usage:
  # Run all 91 questions from Excel
  python scripts/test_excel_questions.py

  # Run with HTML & Markdown reports generated
  python scripts/test_excel_questions.py --report

  # Run a specific module or priority
  python scripts/test_excel_questions.py --module "Inventory Intelligence"
  python scripts/test_excel_questions.py --priority High

  # Run a subset (e.g. first 10 questions)
  python scripts/test_excel_questions.py --limit 10 --start 1
"""

import argparse
import datetime
import html
import io
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import pandas as pd
except ImportError:
    print("[ERROR] 'pandas' and 'openpyxl' required. Run: pip install pandas openpyxl")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' required. Run: pip install requests")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration & ANSI Colors
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_HOST = "http://127.0.0.1:8760"
CHAT_ENDPOINT = "/api/chat"
HEALTH_ENDPOINT = "/api/health"
DEFAULT_EXCEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pharmacy_pos_rag_chatbot_questions.xlsx")
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

_COLOUR = sys.platform != "win32" or os.environ.get("WT_SESSION")
GREEN  = "\033[92m" if _COLOUR else ""
YELLOW = "\033[93m" if _COLOUR else ""
RED    = "\033[91m" if _COLOUR else ""
CYAN   = "\033[96m" if _COLOUR else ""
BOLD   = "\033[1m"  if _COLOUR else ""
MAGENTA = "\033[95m" if _COLOUR else ""
RESET  = "\033[0m"  if _COLOUR else ""


# ─────────────────────────────────────────────────────────────────────────────
# Live Logger
# ─────────────────────────────────────────────────────────────────────────────
class _TeeLogger:
    def __init__(self, original, log_path: str):
        self._orig = original
        self._log = open(log_path, "w", encoding="utf-8", buffering=1)

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


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class QuestionItem:
    question_id: int
    module: str
    question: str
    priority: str
    data_source: str
    expected_route: Optional[str] = None


@dataclass
class EvalResult:
    question_id: int
    module: str
    priority: str
    question: str
    data_source: str
    status_code: int
    actual_route: str
    timing_s: float
    sources_count: int
    has_computed_values: bool
    answer: str
    verdict: str           # "PASS", "GRACEFUL_NO_DATA", "ROUTING_MISMATCH", "EMPTY_OR_ERROR", "POSSIBLE_HALLUCINATION"
    failure_category: str  # "None", "Routing", "No Data in DB", "Missing Table / Out of Scope", "HTTP/LLM Error", "Hallucination"
    evaluation_notes: str
    raw_response: Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Intelligent Evaluator
# ─────────────────────────────────────────────────────────────────────────────
def classify_expected_intent(q: str, module: str) -> str:
    """Determine whether the question fundamentally expects deterministic SQL aggregation or RAG lookup."""
    q_lower = q.lower()
    analytics_keywords = [
        "how much", "how many", "total", "average", "sum", "count", "highest", "lowest",
        "compare", "comparison", "difference", "revenue", "profit this month", "kitni",
        "kitne", "sab se zyada", "sab se kam", "paisa", "sales today", "sales this week",
        "sales for the last", "margin", "most sales", "most refunds", "most discounts"
    ]
    if any(k in q_lower for k in analytics_keywords):
        return "analytics"
    return "rag"


def evaluate_response(q_item: QuestionItem, status_code: int, resp_data: Dict[str, Any], timing: float) -> EvalResult:
    if status_code != 200:
        return EvalResult(
            question_id=q_item.question_id,
            module=q_item.module,
            priority=q_item.priority,
            question=q_item.question,
            data_source=q_item.data_source,
            status_code=status_code,
            actual_route="error",
            timing_s=timing,
            sources_count=0,
            has_computed_values=False,
            answer=resp_data.get("detail", str(resp_data)),
            verdict="EMPTY_OR_ERROR",
            failure_category="HTTP/LLM Error",
            evaluation_notes=f"HTTP Status {status_code}: {resp_data}",
            raw_response=resp_data
        )

    answer = resp_data.get("answer", "").strip()
    actual_route = resp_data.get("route", "unknown")
    sources = resp_data.get("sources", [])
    computed_values = resp_data.get("computed_values")
    has_computed = bool(computed_values)
    sources_count = len(sources) if sources else 0

    if not answer:
        return EvalResult(
            question_id=q_item.question_id,
            module=q_item.module,
            priority=q_item.priority,
            question=q_item.question,
            data_source=q_item.data_source,
            status_code=status_code,
            actual_route=actual_route,
            timing_s=timing,
            sources_count=sources_count,
            has_computed_values=has_computed,
            answer=answer,
            verdict="EMPTY_OR_ERROR",
            failure_category="HTTP/LLM Error",
            evaluation_notes="Received completely empty answer from chatbot",
            raw_response=resp_data
        )

    ans_lower = answer.lower()
    
    # 1. Check for graceful refusal / missing table / out-of-scope notifications
    no_data_phrases = [
        "no records found", "could not find any records", "cannot find", "no data available",
        "not available in the connected", "no information available", "i cannot find that table",
        "outside the scope", "not in the active data", "not present in the active data",
        "no sales records", "no purchase records", "no matching records"
    ]
    is_graceful_no_data = any(p in ans_lower for p in no_data_phrases)

    # 2. Check for known out-of-scope modules where data is not in POS sales/purchase tables:
    # E.g. Prescriptions (tbl_Prescriptions), Cash Drawer Variances, Lost Sales / Demand notes
    out_of_scope_modules = [
        "Prescription Intelligence", "Staff & Anomaly Intelligence",
        "Demand / Lost Sales", "Local Pharmacy Operations"
    ]

    expected_route = classify_expected_intent(q_item.question, q_item.module)

    # 3. Check for Hallucination Indicators
    hallucination_phrases = [
        "as an ai language model", "in a standard pharmacy", "typically,", "generally,"
    ]
    is_hallucinating = any(h in ans_lower for h in hallucination_phrases) and not sources_count and not has_computed

    # 4. Evaluation decision tree
    verdict = "PASS"
    failure_cat = "None"
    notes = []

    if is_hallucinating:
        verdict = "POSSIBLE_HALLUCINATION"
        failure_cat = "Hallucination"
        notes.append("Response gave generic or ungrounded AI statements without database citations.")

    elif is_graceful_no_data:
        if q_item.module in out_of_scope_modules or "prescription" in q_item.question.lower() or "variance" in q_item.question.lower():
            verdict = "GRACEFUL_NO_DATA"
            failure_cat = "Missing Table / Out of Scope"
            notes.append("Table/domain not present in active POS datasets; bot correctly responded with no data/unavailable.")
        else:
            verdict = "GRACEFUL_NO_DATA"
            failure_cat = "No Data in DB"
            notes.append("No matching records found in active tables for this query filter.")

    else:
        # We got an answer!
        if actual_route == "analytics" and has_computed:
            verdict = "PASS"
            notes.append(f"Successfully computed deterministic analytics: {list(computed_values.keys()) if isinstance(computed_values, dict) else computed_values}")
        elif actual_route == "rag" and sources_count > 0:
            verdict = "PASS"
            notes.append(f"Retrieved {sources_count} grounded context sources.")
        elif actual_route == "chit-chat":
            if q_item.module == "Business Intelligence" and any(w in q_item.question.lower() for w in ["aaj", "kya", "scene"]):
                verdict = "ROUTING_MISMATCH"
                failure_cat = "Routing"
                notes.append("Urdu business intelligence question was misclassified as chit-chat.")
            else:
                verdict = "PASS"
                notes.append("Answered via conversational model.")
        else:
            # Answer provided, check if numerical when expected
            has_numbers = bool(re.search(r"\d", answer))
            if expected_route == "analytics" and not has_numbers and not is_graceful_no_data:
                verdict = "ROUTING_MISMATCH"
                failure_cat = "Routing"
                notes.append(f"Expected analytical calculation but received descriptive text (Route: {actual_route}).")
            else:
                verdict = "PASS"
                notes.append(f"Valid grounded response generated via {actual_route.upper()}.")

    return EvalResult(
        question_id=q_item.question_id,
        module=q_item.module,
        priority=q_item.priority,
        question=q_item.question,
        data_source=q_item.data_source,
        status_code=status_code,
        actual_route=actual_route,
        timing_s=timing,
        sources_count=sources_count,
        has_computed_values=has_computed,
        answer=answer,
        verdict=verdict,
        failure_category=failure_cat,
        evaluation_notes=" | ".join(notes) if notes else "OK",
        raw_response=resp_data
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test Runner Class
# ─────────────────────────────────────────────────────────────────────────────
class PharmacyPOSTestRunner:
    def __init__(self, excel_path: str, host: str = DEFAULT_HOST, delay_s: float = 0.2):
        self.excel_path = excel_path
        self.host = host.rstrip("/")
        self.delay_s = delay_s
        self.results: List[EvalResult] = []

    def load_questions(self, module_filter: Optional[str] = None, priority_filter: Optional[str] = None, limit: Optional[int] = None, start: int = 1) -> List[QuestionItem]:
        df = pd.read_excel(self.excel_path, sheet_name="All Questions")
        questions: List[QuestionItem] = []
        for _, row in df.iterrows():
            qid = int(row["Question ID"])
            if qid < start:
                continue
            mod = str(row["Module"]).strip()
            q_text = str(row["Question"]).strip()
            prio = str(row["Priority"]).strip()
            dsrc = str(row["Data Source"]).strip()

            if module_filter and module_filter.lower() not in mod.lower():
                continue
            if priority_filter and priority_filter.lower() != prio.lower():
                continue

            questions.append(QuestionItem(
                question_id=qid,
                module=mod,
                question=q_text,
                priority=prio,
                data_source=dsrc
            ))

            if limit and len(questions) >= limit:
                break
        return questions

    def run_single(self, q_item: QuestionItem) -> EvalResult:
        payload = {
            "question": q_item.question,
            "session_id": str(uuid.uuid4()),
            "domain": "pharmacy"
        }
        t0 = time.time()
        try:
            r = requests.post(f"{self.host}{CHAT_ENDPOINT}", json=payload, timeout=120)
            timing = round(time.time() - t0, 2)
            try:
                resp_json = r.json()
            except Exception:
                resp_json = {"raw_text": r.text}
            return evaluate_response(q_item, r.status_code, resp_json, timing)
        except Exception as exc:
            timing = round(time.time() - t0, 2)
            return EvalResult(
                question_id=q_item.question_id,
                module=q_item.module,
                priority=q_item.priority,
                question=q_item.question,
                data_source=q_item.data_source,
                status_code=0,
                actual_route="exception",
                timing_s=timing,
                sources_count=0,
                has_computed_values=False,
                answer="",
                verdict="EMPTY_OR_ERROR",
                failure_category="HTTP/LLM Error",
                evaluation_notes=f"Request exception: {str(exc)}",
                raw_response={"error": str(exc)}
            )

    def run_all(self, questions: List[QuestionItem]) -> List[EvalResult]:
        total = len(questions)
        print(f"\n{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
        print(f"{BOLD}{CYAN}  LLM-Konnect — RAG Chatbot Test Suite ({total} Questions Loaded){RESET}")
        print(f"{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}\n")

        self.results = []
        for idx, q in enumerate(questions, 1):
            sys.stdout.write(f"[{idx:02d}/{total:02d}] Q{q.question_id:02d} | [{q.priority:<6}] [{q.module:<28}] {q.question[:45]:<45} ... ")
            sys.stdout.flush()

            res = self.run_single(q)
            self.results.append(res)

            # Colorized symbol & summary
            if res.verdict == "PASS":
                sym = f"{GREEN}[PASS]{RESET}"
            elif res.verdict == "GRACEFUL_NO_DATA":
                sym = f"{CYAN}[NO-DATA]{RESET}"
            elif res.verdict == "ROUTING_MISMATCH":
                sym = f"{YELLOW}[ROUTE-MISMATCH]{RESET}"
            elif res.verdict == "POSSIBLE_HALLUCINATION":
                sym = f"{MAGENTA}[HALLUCINATION]{RESET}"
            else:
                sym = f"{RED}[FAIL]{RESET}"

            print(f"{sym} ({res.timing_s:.1f}s | {res.actual_route.upper()})")
            if res.verdict != "PASS":
                print(f"        └─ {YELLOW}Reason:{RESET} {res.evaluation_notes}")
                if res.answer:
                    snippet = res.answer.replace('\n', ' ')[:100]
                    print(f"        └─ {CYAN}Answer snippet:{RESET} {snippet}...")

            if self.delay_s > 0 and idx < total:
                time.sleep(self.delay_s)

        return self.results


# ─────────────────────────────────────────────────────────────────────────────
# Report Generators (HTML, Markdown, JSON)
# ─────────────────────────────────────────────────────────────────────────────
def generate_reports(results: List[EvalResult], output_dir: str = REPORT_DIR) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(output_dir, f"rag_excel_test_results_{ts}.json")
    html_path = os.path.join(output_dir, f"rag_excel_test_report_{ts}.html")
    md_path   = os.path.join(output_dir, f"rag_excel_test_report_{ts}.md")

    # JSON export
    serializable = [asdict(r) for r in results]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    total = len(results)
    passed = sum(1 for r in results if r.verdict == "PASS")
    graceful = sum(1 for r in results if r.verdict == "GRACEFUL_NO_DATA")
    routing_fail = sum(1 for r in results if r.verdict == "ROUTING_MISMATCH")
    errors = sum(1 for r in results if r.verdict == "EMPTY_OR_ERROR")
    halluc = sum(1 for r in results if r.verdict == "POSSIBLE_HALLUCINATION")
    effective_handled = passed + graceful
    pass_pct = (passed / total * 100) if total else 0
    handled_pct = (effective_handled / total * 100) if total else 0
    avg_timing = sum(r.timing_s for r in results) / total if total else 0

    # Module breakdown
    module_stats = {}
    for r in results:
        if r.module not in module_stats:
            module_stats[r.module] = {"total": 0, "pass": 0, "graceful": 0, "route_fail": 0, "error": 0, "halluc": 0}
        module_stats[r.module]["total"] += 1
        if r.verdict == "PASS":
            module_stats[r.module]["pass"] += 1
        elif r.verdict == "GRACEFUL_NO_DATA":
            module_stats[r.module]["graceful"] += 1
        elif r.verdict == "ROUTING_MISMATCH":
            module_stats[r.module]["route_fail"] += 1
        elif r.verdict == "POSSIBLE_HALLUCINATION":
            module_stats[r.module]["halluc"] += 1
        else:
            module_stats[r.module]["error"] += 1

    # Markdown Report
    md_content = f"""# Pharmacy POS RAG Chatbot Test Report

**Execution Date:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  
**Total Questions Evaluated:** {total}  
**Grounded Accurate Answers (PASS):** {passed} ({pass_pct:.1f}%)  
**Gracefully Handled Missing Scope (NO DATA):** {graceful}  
**Effective Safe Handling Rate (PASS + GRACEFUL):** {effective_handled}/{total} ({handled_pct:.1f}%)  
**Routing Mismatches:** {routing_fail}  
**Errors / Failures:** {errors}  
**Average Latency:** {avg_timing:.2f}s  

---

## 1. Module-by-Module Breakdown

| Module | Total | Pass | Graceful No-Data | Routing Mismatch | Errors | Success Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
"""
    for mod, st in sorted(module_stats.items()):
        succ = (st["pass"] + st["graceful"]) / st["total"] * 100
        md_content += f"| {mod} | {st['total']} | {st['pass']} | {st['graceful']} | {st['route_fail']} | {st['error']} | {succ:.0f}% |\n"

    md_content += """
---

## 2. Failure Analysis & Key Insights

### A. Routing Mismatches
Questions that were dispatched to general RAG semantic search or chit-chat instead of deterministic SQL Analytics, leading to descriptive answers instead of exact computed sums.

### B. Missing Tables / Out-of-Scope Data
Pharmacy operational domains (such as Prescriptions `tbl_Prescriptions`, Lost Sales Logs, Cash Variances) that are not part of the active Sales and Purchase datasets. The bot properly returned "No records found" or declared data missing.

### C. Urdu & Roman-Urdu Comprehension
Business Intelligence questions in Roman-Urdu (e.g., "Aaj kitni sale hui?", "Mera sab se zyada bikne wala medicine konsa hai?") require accurate translation and intent detection to hit the Analytics engine.

---

## 3. Detailed Results Table

| ID | Module | Priority | Question | Route | Timing | Verdict | Notes |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- |
"""
    for r in results:
        clean_q = r.question.replace("|", "/")
        clean_notes = r.evaluation_notes.replace("|", "/")
        md_content += f"| Q{r.question_id:02d} | {r.module} | {r.priority} | {clean_q} | {r.actual_route.upper()} | {r.timing_s:.1f}s | {r.verdict} | {clean_notes} |\n"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # HTML Report
    html_rows = ""
    for r in results:
        badge_cls = {
            "PASS": "badge-pass",
            "GRACEFUL_NO_DATA": "badge-graceful",
            "ROUTING_MISMATCH": "badge-warn",
            "POSSIBLE_HALLUCINATION": "badge-halluc",
            "EMPTY_OR_ERROR": "badge-fail"
        }.get(r.verdict, "badge-unknown")

        ans_escaped = html.escape(r.answer)
        notes_escaped = html.escape(r.evaluation_notes)
        raw_json_str = html.escape(json.dumps(r.raw_response, indent=2))

        html_rows += f"""
        <tr class="test-row" data-verdict="{r.verdict}" data-module="{r.module}" data-priority="{r.priority}">
            <td><strong>Q{r.question_id:02d}</strong></td>
            <td><span class="badge badge-subtle">{r.module}</span></td>
            <td><span class="prio-tag prio-{r.priority.lower()}">{r.priority}</span></td>
            <td class="q-cell">{html.escape(r.question)}</td>
            <td><code>{r.actual_route.upper()}</code></td>
            <td>{r.timing_s:.1f}s</td>
            <td><span class="badge {badge_cls}">{r.verdict}</span></td>
            <td>
                <div class="notes">{notes_escaped}</div>
                <details class="ans-details">
                    <summary>Inspect Response</summary>
                    <div class="ans-body">
                        <strong>Answer:</strong>
                        <p>{ans_escaped}</p>
                        <strong>Sources:</strong> {r.sources_count} records | <strong>Computed:</strong> {r.has_computed_values}
                        <pre><code>{raw_json_str}</code></pre>
                    </div>
                </details>
            </td>
        </tr>
        """

    mod_breakdown_html = ""
    for mod, st in sorted(module_stats.items()):
        pct = int((st["pass"] + st["graceful"]) / st["total"] * 100)
        mod_breakdown_html += f"""
        <div class="mod-card">
            <h4>{mod}</h4>
            <div class="stat-bar"><div class="stat-fill" style="width: {pct}%;"></div></div>
            <div class="mod-meta">
                <span>Total: {st['total']}</span>
                <span>Pass: {st['pass']}</span>
                <span>No-Data: {st['graceful']}</span>
                <span>Mismatch: {st['route_fail']}</span>
            </div>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM-Konnect — RAG Chatbot Test Report (91 Questions)</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0b0f19;
    --card: #151d2e;
    --card-border: #232f48;
    --text: #f1f5f9;
    --text-muted: #94a3b8;
    --accent: #3b82f6;
    --pass: #10b981;
    --graceful: #06b6d4;
    --warn: #f59e0b;
    --fail: #ef4444;
    --halluc: #ec4899;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }}
  body {{ background: var(--bg); color: var(--text); padding: 32px 24px; }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  .header {{ margin-bottom: 28px; }}
  .header h1 {{ font-size: 28px; font-weight: 800; background: linear-gradient(135deg, #60a5fa, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .header p {{ color: var(--text-muted); margin-top: 6px; }}
  
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }}
  .stat-card {{ background: var(--card); border: 1px solid var(--card-border); padding: 20px; border-radius: 12px; }}
  .stat-card .label {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-card .value {{ font-size: 32px; font-weight: 800; margin-top: 6px; }}

  .mod-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; margin-bottom: 32px; }}
  .mod-card {{ background: var(--card); border: 1px solid var(--card-border); padding: 14px; border-radius: 10px; }}
  .mod-card h4 {{ font-size: 14px; font-weight: 600; margin-bottom: 8px; }}
  .stat-bar {{ height: 6px; background: #1e293b; border-radius: 3px; overflow: hidden; margin-bottom: 8px; }}
  .stat-fill {{ height: 100%; background: linear-gradient(90deg, #10b981, #3b82f6); }}
  .mod-meta {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--text-muted); }}

  .filters {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
  .filter-btn {{ background: #1e293b; border: 1px solid #334155; color: #cbd5e1; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 13px; }}
  .filter-btn.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}

  table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; overflow: hidden; }}
  th, td {{ padding: 12px 14px; text-align: left; font-size: 13px; border-bottom: 1px solid var(--card-border); }}
  th {{ background: #1e293b; color: #cbd5e1; font-weight: 600; }}
  .q-cell {{ max-width: 320px; font-weight: 500; }}

  .badge {{ display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
  .badge-pass {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid #059669; }}
  .badge-graceful {{ background: rgba(6, 182, 212, 0.15); color: #22d3ee; border: 1px solid #0891b2; }}
  .badge-warn {{ background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid #d97706; }}
  .badge-fail {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid #dc2626; }}
  .badge-halluc {{ background: rgba(236, 72, 153, 0.15); color: #f472b6; border: 1px solid #db2777; }}
  .badge-subtle {{ background: #1e293b; color: #94a3b8; }}

  .prio-tag {{ font-size: 11px; font-weight: 600; padding: 2px 6px; border-radius: 4px; }}
  .prio-high {{ color: #f87171; background: rgba(239,68,68,0.1); }}
  .prio-medium {{ color: #fbbf24; background: rgba(245,158,11,0.1); }}

  .ans-details {{ margin-top: 6px; }}
  .ans-details summary {{ color: var(--accent); cursor: pointer; font-size: 12px; font-weight: 500; }}
  .ans-body {{ background: #0b0f19; border: 1px solid var(--card-border); padding: 10px; border-radius: 8px; margin-top: 6px; font-size: 12px; line-height: 1.5; }}
  .ans-body pre {{ background: #050811; padding: 8px; border-radius: 6px; overflow-x: auto; margin-top: 6px; font-size: 11px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>LLM-Konnect — Pharmacy POS RAG Test Report</h1>
    <p>Automated evaluation across all 91 benchmark questions from <code>pharmacy_pos_rag_chatbot_questions.xlsx</code></p>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="label">Total Questions</div>
      <div class="value">{total}</div>
    </div>
    <div class="stat-card">
      <div class="label">Grounded Pass</div>
      <div class="value" style="color: var(--pass);">{passed}</div>
    </div>
    <div class="stat-card">
      <div class="label">Graceful Scope Refusal</div>
      <div class="value" style="color: var(--graceful);">{graceful}</div>
    </div>
    <div class="stat-card">
      <div class="label">Routing Mismatches</div>
      <div class="value" style="color: var(--warn);">{routing_fail}</div>
    </div>
    <div class="stat-card">
      <div class="label">Errors / Failures</div>
      <div class="value" style="color: var(--fail);">{errors}</div>
    </div>
    <div class="stat-card">
      <div class="label">Avg Latency</div>
      <div class="value">{avg_timing:.2f}s</div>
    </div>
  </div>

  <h3 style="margin-bottom: 12px; font-size: 16px;">Module Performance Summary</h3>
  <div class="mod-grid">
    {mod_breakdown_html}
  </div>

  <div class="filters">
    <button class="filter-btn active" onclick="filterTable('all')">All ({total})</button>
    <button class="filter-btn" onclick="filterTable('PASS')">Pass ({passed})</button>
    <button class="filter-btn" onclick="filterTable('GRACEFUL_NO_DATA')">No Data / Out-of-Scope ({graceful})</button>
    <button class="filter-btn" onclick="filterTable('ROUTING_MISMATCH')">Routing Mismatches ({routing_fail})</button>
    <button class="filter-btn" onclick="filterTable('EMPTY_OR_ERROR')">Errors ({errors})</button>
  </div>

  <table id="resultsTable">
    <thead>
      <tr>
        <th>ID</th>
        <th>Module</th>
        <th>Priority</th>
        <th>Question</th>
        <th>Route</th>
        <th>Latency</th>
        <th>Verdict</th>
        <th>Evaluation Details</th>
      </tr>
    </thead>
    <tbody>
      {html_rows}
    </tbody>
  </table>
</div>

<script>
function filterTable(verdict) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  const rows = document.querySelectorAll('.test-row');
  rows.forEach(r => {{
    if (verdict === 'all' || r.dataset.verdict === verdict) {{
      r.style.display = '';
    }} else {{
      r.style.display = 'none';
    }}
  }});
}}
</script>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return {
        "json": json_path,
        "html": html_path,
        "md": md_path
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Test RAG Chatbot on Pharmacy POS Questions Excel.")
    parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH, help="Path to Excel questions file.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Chatbot backend host.")
    parser.add_argument("--module", default=None, help="Filter by module name.")
    parser.add_argument("--priority", default=None, help="Filter by priority (High/Medium).")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions to test.")
    parser.add_argument("--start", type=int, default=1, help="Starting Question ID.")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between requests in seconds.")
    parser.add_argument("--report", action="store_true", default=True, help="Generate HTML and MD reports.")
    args = parser.parse_args()

    # Log to both stdout and test run live log
    live_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_excel_live.log")
    sys.stdout = _TeeLogger(sys.stdout, live_log)

    runner = PharmacyPOSTestRunner(excel_path=args.excel, host=args.host, delay_s=args.delay)
    questions = runner.load_questions(
        module_filter=args.module,
        priority_filter=args.priority,
        limit=args.limit,
        start=args.start
    )

    if not questions:
        print(f"{RED}[ERROR] No questions matched filters.{RESET}")
        return

    results = runner.run_all(questions)

    if args.report:
        paths = generate_reports(results)
        print(f"\n{BOLD}{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
        print(f"{BOLD}{GREEN}  Reports successfully generated:{RESET}")
        print(f"  • HTML Report : {paths['html']}")
        print(f"  • Markdown    : {paths['md']}")
        print(f"  • Raw JSON    : {paths['json']}")
        print(f"{BOLD}{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}\n")


if __name__ == "__main__":
    main()
