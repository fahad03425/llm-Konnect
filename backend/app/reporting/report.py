"""
Module 4.3 — Report orchestrator (report.py). Month 5.

Role in the pipeline
--------------------
"Code computes. The LLM narrates. A verifier checks."

This module is the top-level orchestrator that wires Steps 1 and 2 together:

  1. Calls `narrative.generate_narrative()` to get the LLM-written prose.
  2. Calls `verifier.verify()` to check every numeric claim in that prose
     against the KPI engine's ground truth.
  3. If verification fails, optionally retries narrative generation once.
  4. Assembles a clean HTML report with a KPI summary table, the narrative,
     and a prominent verification-status banner.
  5. Writes the HTML to `settings.reports_dir` with a timestamped filename.
  6. Returns a `ReportResult` dataclass summarising the whole run.

Design constraints
------------------
* This file is the ONLY place that imports from both narrative.py and
  verifier.py — they must never import each other.
* HTML is assembled with plain Python f-strings (no Jinja2 dependency needed).
* All file I/O uses Python stdlib only (os, pathlib, datetime).
* A RuntimeError from the LLM layer propagates upward unchanged — the caller
  (the API endpoint in Step 4) is responsible for converting it to an HTTP 500.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.reporting.narrative import generate_narrative
from app.reporting.verifier import verify, VerificationReport, VerifiedClaim

# ──────────────────────────────────────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ReportResult:
    """
    Full output of one `generate_report()` call.

    Attributes
    ----------
    kpi_snapshot:
        Plain-dict form of every KPIResult passed in (via .to_dict()),
        suitable for JSON serialisation.  Named *snapshot* to make clear
        it is a copy of what was used, not a live view.
    narrative:
        The final narrative text embedded in the report.
    verification:
        The VerificationReport produced by verifier.verify().
    verification_passed:
        True iff verification_report.all_verified is True at the end of all
        allowed attempts.
    regenerated:
        True if a second narrative generation attempt was made.
    html_path:
        Absolute path to the written HTML file, or None if writing failed
        (failure reason will be in `warnings`).
    warnings:
        Human-readable advisory messages — e.g. verification failure or
        write errors.  Non-empty does NOT mean the report is unusable, but
        a human reviewer should read them.
    """

    kpi_snapshot: Dict[str, Any]
    narrative: str
    verification: VerificationReport
    verification_passed: bool
    regenerated: bool = False
    html_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kpi_snapshot": self.kpi_snapshot,
            "narrative": self.narrative,
            "verification": self.verification.to_dict(),
            "verification_passed": self.verification_passed,
            "regenerated": self.regenerated,
            "html_path": self.html_path,
            "warnings": list(self.warnings),
        }


# ──────────────────────────────────────────────────────────────────────────────
# HTML assembly helpers
# ──────────────────────────────────────────────────────────────────────────────

_CSS = """
    :root {
        --ok:   #1a7f37;
        --warn: #b45309;
        --fail: #b91c1c;
        --bg:   #f9fafb;
        --card: #ffffff;
        --border: #e5e7eb;
        --text: #111827;
        --muted: #6b7280;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        background: var(--bg);
        color: var(--text);
        padding: 2rem 1.5rem;
        max-width: 960px;
        margin: 0 auto;
        line-height: 1.6;
    }
    h1 { font-size: 1.75rem; font-weight: 700; margin-bottom: .25rem; }
    h2 { font-size: 1.1rem; font-weight: 600; margin: 2rem 0 .75rem; color: #374151; }
    .subtitle { color: var(--muted); font-size: .9rem; margin-bottom: 2rem; }
    .card {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
    }
    /* KPI table */
    table { width: 100%; border-collapse: collapse; font-size: .9rem; }
    th {
        text-align: left; padding: .5rem .75rem;
        background: #f3f4f6; border-bottom: 2px solid var(--border);
        font-weight: 600; color: #374151;
    }
    td { padding: .5rem .75rem; border-bottom: 1px solid var(--border); }
    tr:last-child td { border-bottom: none; }
    .status-ok   { color: var(--ok);   font-weight: 600; }
    .status-na   { color: var(--muted); font-style: italic; }
    /* Narrative */
    .narrative { white-space: pre-wrap; font-size: .95rem; }
    /* Verification banner */
    .banner {
        border-radius: 8px; padding: 1rem 1.25rem;
        margin-bottom: 1.5rem; font-size: .95rem;
    }
    .banner-ok   { background: #f0fdf4; border: 1px solid #bbf7d0; color: var(--ok); }
    .banner-warn { background: #fffbeb; border: 1px solid #fde68a; color: var(--warn); }
    .banner h3   { font-size: 1rem; margin-bottom: .4rem; }
    .claim-list  { margin-top: .75rem; padding-left: 1.25rem; font-size: .88rem; }
    .claim-list li { margin-bottom: .3rem; }
    .footer { font-size: .8rem; color: var(--muted); text-align: center; margin-top: 2rem; }
"""


def _escape(text: str) -> str:
    """Minimal HTML escaping for user-supplied strings."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _kpi_table_rows(kpi_snapshot: Dict[str, Any]) -> str:
    """Build <tr> rows for the KPI summary table."""
    rows: List[str] = []
    for key, kpi in kpi_snapshot.items():
        name   = _escape(str(kpi.get("name", key)))
        unit   = _escape(str(kpi.get("unit", "")))
        status = kpi.get("status", "ok")

        if status == "ok":
            raw_val = kpi.get("value")
            if raw_val is None:
                val_cell  = '<span class="status-na">N/A</span>'
                stat_cell = '<span class="status-na">unavailable</span>'
            else:
                # Format nicely: currency with commas, percent with symbol
                if unit == "PKR":
                    val_cell = f"PKR {raw_val:,.2f}"
                elif unit == "percent":
                    val_cell = f"{raw_val:,.4g}%"
                else:
                    val_cell = f"{raw_val:,.4g}"
                stat_cell = '<span class="status-ok">✓ ok</span>'
        else:
            reason = _escape(str(kpi.get("reason") or "could not be determined"))
            val_cell  = f'<span class="status-na">N/A — {reason}</span>'
            stat_cell = '<span class="status-na">unavailable</span>'

        rows.append(
            f"<tr>"
            f"<td>{_escape(key)}</td>"
            f"<td>{name}</td>"
            f"<td>{val_cell}</td>"
            f"<td>{unit}</td>"
            f"<td>{stat_cell}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _verification_banner(vr: VerificationReport) -> str:
    """Return the HTML verification status banner."""
    if vr.all_verified:
        return (
            '<div class="banner banner-ok">'
            "<h3>✅ Verification Passed</h3>"
            "<p>All numeric claims in the narrative were matched to KPI ground-truth "
            "values within tolerance. No manual review required.</p>"
            f"<p><small>Verified: {vr.verified_count} claim(s).</small></p>"
            "</div>"
        )

    # Build a list of problem claims
    problem_items: List[str] = []
    for claim in vr.claims:
        if claim.status == "mismatch":
            expected = (
                f"{claim.expected_value:,.4g}" if claim.expected_value is not None else "—"
            )
            problem_items.append(
                f'<li><b>Mismatch</b> — '
                f'"{_escape(claim.matched_text)}" '
                f'(extracted {claim.extracted_value:,.4g}, '
                f'expected {expected} for KPI <code>{_escape(claim.matched_kpi_key or "?")}</code>)'
                f"</li>"
            )
        elif claim.status == "unmatched":
            problem_items.append(
                f'<li><b>Unmatched</b> — '
                f'"{_escape(claim.matched_text)}" '
                f'({_escape(claim.reason or "no matching KPI found")})'
                f"</li>"
            )

    claim_list_html = (
        f'<ul class="claim-list">{"".join(problem_items)}</ul>'
        if problem_items
        else ""
    )

    return (
        '<div class="banner banner-warn">'
        "<h3>⚠️ Verification Warning — Manual Review Required</h3>"
        "<p>One or more numeric claims in the narrative could not be fully "
        "verified against the KPI engine ground truth. This section has been "
        "flagged for manual review. Do not rely on the figures below without "
        "cross-checking against the KPI table above.</p>"
        f"<p><small>"
        f"Verified: {vr.verified_count} | "
        f"Mismatched: {vr.mismatch_count} | "
        f"Unmatched: {vr.unmatched_count}"
        f"</small></p>"
        f"{claim_list_html}"
        "</div>"
    )


def _assemble_html(
    business_name: str,
    domain: str,
    kpi_snapshot: Dict[str, Any],
    narrative: str,
    vr: VerificationReport,
    generated_at: datetime,
) -> str:
    """Build the complete HTML report string."""
    table_rows    = _kpi_table_rows(kpi_snapshot)
    banner_html   = _verification_banner(vr)
    narrative_esc = _escape(narrative)
    title         = _escape(f"{business_name} — Analytics Report")
    domain_label  = _escape(domain.title())
    ts_display    = generated_at.strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>{_CSS}</style>
</head>
<body>
  <h1>{title}</h1>
  <p class="subtitle">Domain: {domain_label} &nbsp;|&nbsp; Generated: {ts_display}</p>

  {banner_html}

  <h2>KPI Summary</h2>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Key</th><th>Metric</th><th>Value</th><th>Unit</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>

  <h2>Narrative</h2>
  <div class="card">
    <p class="narrative">{narrative_esc}</p>
  </div>

  <p class="footer">
    Generated by LLM-Konnect Report Generator &mdash;
    "Code computes. The LLM narrates. A verifier checks."
  </p>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def generate_report(
    kpi_results: Dict[str, Any],
    domain: str = "pharmacy",
    business_name: Optional[str] = None,
    max_regeneration_attempts: int = 1,
) -> ReportResult:
    """
    Top-level report pipeline: narrate → verify → (retry once) → HTML → save.

    Parameters
    ----------
    kpi_results:
        Dict[str, KPIResult] from ``engine.compute_all(...)``.
    domain:
        Business domain label passed through to the narrative generator.
    business_name:
        Display name embedded in the report header and system prompt.
    max_regeneration_attempts:
        How many extra narrative-generation passes are allowed if verification
        fails on the first attempt.  Set to 0 to skip the retry entirely.
        Currently capped at 1 (a simple retry, not an iterative repair loop).

    Returns
    -------
    ReportResult
        Populated with the narrative, verification result, html_path, and
        any advisory warnings.

    Raises
    ------
    RuntimeError
        Propagated from ``narrative.generate_narrative()`` if Ollama is
        unreachable or the model fails to load.  The caller is responsible
        for converting this to an appropriate HTTP response.
    """
    effective_name = business_name or f"the {domain.title()} business"
    warnings: List[str] = []
    regenerated = False

    # ── Snapshot KPI results into plain dicts once (immutable ground truth) ──
    kpi_snapshot: Dict[str, Any] = {}
    for key, result in kpi_results.items():
        if hasattr(result, "to_dict"):
            kpi_snapshot[key] = result.to_dict()
        elif isinstance(result, dict):
            kpi_snapshot[key] = result
        else:
            kpi_snapshot[key] = {"key": key, "value": str(result)}

    # ── Step 1: generate narrative ────────────────────────────────────────────
    # RuntimeError from the LLM layer propagates upward to the caller.
    narrative_text = generate_narrative(kpi_results, domain=domain, business_name=business_name)

    # ── Step 2: verify ────────────────────────────────────────────────────────
    vr = verify(narrative_text, kpi_results)

    # ── Step 3: optional retry ────────────────────────────────────────────────
    if not vr.all_verified and max_regeneration_attempts > 0:
        regenerated = True
        warnings.append(
            "Verification failed on first attempt — retrying narrative generation."
        )
        narrative_text = generate_narrative(
            kpi_results, domain=domain, business_name=business_name
        )
        vr = verify(narrative_text, kpi_results)

    # ── Step 4: record final verification outcome ─────────────────────────────
    verification_passed = vr.all_verified
    if not verification_passed:
        problem_summary = (
            f"mismatches={vr.mismatch_count}, unmatched={vr.unmatched_count}"
        )
        warnings.append(
            f"Verification failed after {'retry' if regenerated else 'first attempt'} "
            f"({problem_summary}). Report flagged for manual review."
        )

    # ── Step 5: assemble HTML ─────────────────────────────────────────────────
    generated_at = datetime.now()
    html_content = _assemble_html(
        business_name=effective_name,
        domain=domain,
        kpi_snapshot=kpi_snapshot,
        narrative=narrative_text,
        vr=vr,
        generated_at=generated_at,
    )

    # ── Step 6: write to disk ─────────────────────────────────────────────────
    timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
    safe_domain = "".join(c if c.isalnum() or c == "_" else "_" for c in domain)
    filename = f"report_{safe_domain}_{timestamp}.html"

    reports_path = Path(settings.reports_dir)
    html_path: Optional[str] = None
    try:
        reports_path.mkdir(parents=True, exist_ok=True)
        output_file = reports_path / filename
        output_file.write_text(html_content, encoding="utf-8")
        html_path = str(output_file.resolve())
    except OSError as exc:
        warnings.append(f"HTML write failed: {exc}. Report not saved to disk.")

    return ReportResult(
        kpi_snapshot=kpi_snapshot,
        narrative=narrative_text,
        verification=vr,
        verification_passed=verification_passed,
        regenerated=regenerated,
        html_path=html_path,
        warnings=warnings,
    )


# ──────────────────────────────────────────────────────────────────────────────
# __main__ demonstration
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dataclasses import dataclass as _dc, field as _field
    from typing import List as _List

    # ── Minimal stand-ins (same style as verifier.py and narrative.py) ────────

    @_dc
    class _FakeProvenance:
        filter_description: str = "no filter (all rows)"
        row_count: int = 0
        source_rows: _List[int] = _field(default_factory=list)
        source_rows_truncated: bool = False
        sources: _List[str] = _field(default_factory=list)
        columns_used: _List[str] = _field(default_factory=list)
        assumptions: _List[str] = _field(default_factory=list)
        filters: dict = _field(default_factory=dict)

        def to_dict(self):
            return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @_dc
    class _FakePeriod:
        start: str = "2026-01-01"
        end: str = "2026-06-30"

        def to_dict(self):
            return {"start": self.start, "end": self.end}

    @_dc
    class _FakeKPI:
        key: str
        name: str
        value: Optional[float]
        unit: str
        formula: str = ""
        status: str = "ok"
        reason: Optional[str] = None
        period: Optional[_FakePeriod] = None
        breakdown: None = None
        breakdown_columns: None = None
        method: Optional[str] = None
        series: None = None
        forecast: None = None
        provenance: _FakeProvenance = _field(default_factory=_FakeProvenance)

        def to_dict(self):
            return {
                "key": self.key,
                "name": self.name,
                "value": self.value,
                "unit": self.unit,
                "formula": self.formula,
                "status": self.status,
                "reason": self.reason,
                "period": self.period.to_dict() if self.period else None,
                "breakdown": self.breakdown,
                "breakdown_columns": self.breakdown_columns,
                "method": self.method,
                "series": self.series,
                "forecast": self.forecast,
                "provenance": self.provenance.to_dict(),
            }

    # ── Demo KPI set ──────────────────────────────────────────────────────────
    period = _FakePeriod()
    demo_kpis = {
        "total_revenue": _FakeKPI(
            key="total_revenue", name="Total Revenue",
            value=2_450_000.0, unit="PKR",
            formula="sum(net_amount)", period=period,
        ),
        "profit_margin": _FakeKPI(
            key="profit_margin", name="Profit Margin",
            value=21.4, unit="percent",
            formula="(revenue - cogs) / revenue * 100", period=period,
        ),
        "transaction_count": _FakeKPI(
            key="transaction_count", name="Transaction Count",
            value=842.0, unit="count",
            formula="count(invoice_id)", period=period,
        ),
        "expired_stock_value": _FakeKPI(
            key="expired_stock_value", name="Expired Stock Value",
            value=None, unit="PKR",
            formula="sum(net_amount) where expiry < today",
            status="unavailable",
            reason="Column 'expiry_date' was not present in the uploaded dataset.",
        ),
        "revenue_forecast_next_month": _FakeKPI(
            key="revenue_forecast_next_month",
            name="Revenue Forecast — Next Month",
            value=2_600_000.0, unit="PKR",
            formula="linear_regression(monthly_revenue)", method="linear_regression",
            forecast=[
                {"month": "2026-07", "value": 2_600_000.0,
                 "lower": 2_300_000.0, "upper": 2_900_000.0}
            ],
        ),
    }

    print("=" * 64)
    print("Report Generator — Demo")
    print("(Requires Ollama running with the configured model)")
    print("=" * 64)

    try:
        result = generate_report(
            kpi_results=demo_kpis,
            domain="pharmacy",
            business_name="Al-Shifa Pharmacy",
            max_regeneration_attempts=1,
        )
        # Summary
        print(f"\nVerification passed : {result.verification_passed}")
        print(f"Regenerated          : {result.regenerated}")
        print(f"Verified claims      : {result.verification.verified_count}")
        print(f"Unmatched claims     : {result.verification.unmatched_count}")
        print(f"Mismatched claims    : {result.verification.mismatch_count}")
        if result.html_path:
            print(f"HTML report written  : {result.html_path}")
        if result.warnings:
            print("\nWarnings:")
            for w in result.warnings:
                print(f"  • {w}")
        print("\n--- Narrative (first 500 chars) ---")
        print(result.narrative[:500])

    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}")
        print("Make sure Ollama is running and the model is loaded.")
