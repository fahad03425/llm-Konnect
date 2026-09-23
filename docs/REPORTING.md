# Module 6.8 — Verified Report Generator

## Overview
Module 6.8 is the **culmination module** and the **core trust mechanism** of LLM-Konnect. It brings together the KPI Engine (Module 6.6), Expiry Analytics, Trend & Forecasting, and Anomaly Detection (Module 6.7) into a publication-ready, downloadable report in both **HTML** and **PDF** formats.

### The Core Philosophy
> **"Code computes. The LLM narrates. A verifier checks."**

1. **Code Computes**: Every number, chart data point, and table row is calculated deterministically via pure Python/Pandas in Module 6.6 / 6.7.
2. **The LLM Narrates**: The local LLM receives only a compact, grounded summary of computed values and writes natural-language executive prose.
3. **A Verifier Checks**: A deterministic regex and tolerance-based verification engine scans the generated prose, extracts every numeric claim, and cross-checks it against the computed ground truth.

---

## The Verification Contract

| Verification Status | Meaning | Action Taken |
| :--- | :--- | :--- |
| `verified` | Claim matches computed ground-truth within tolerance | Report receives prominent green **VERIFIED** badge |
| `mismatch` | Claim was found in ground-truth but value differs beyond tolerance | Triggers 1 automatic corrective retry with explicit feedback |
| `unmatched` | Claim refers to a figure not present in computed analytics | Flagged as hallucination / ungrounded claim |

### Failure Handling & Zero Silent Failures
- If claims are still mismatched after retries, the report is **never discarded or silently approved**.
- The system generates the complete report with all charts and tables, but stamps it with an unmistakable **UNVERIFIED WARNING** banner listing the exact failed claims.
- **Small Integer Handling**: Integers $\le 9$ (e.g. "top 3 products", "section 2") that are neither currency nor percentages are excluded from financial verification to prevent false alarms on ordinals or list markers.

### Tolerances
- **Currency (`PKR`)**: $\pm 1\%$ relative tolerance to allow for natural prose rounding.
- **Percentages (`percent`)**: $\pm 0.5$ percentage points absolute tolerance.
- **Counts (`count`)**: $\pm 1$ unit absolute tolerance or $\pm 1\%$ relative tolerance.

---

## Pipeline Architecture

```
[Raw Canonical DataFrame]
           │
           ▼
1. gather_report_data() ──────────► Assembles ReportData (Single Source of Truth)
           │
     ┌─────┴──────────────────────┬──────────────────────┐
     ▼                            ▼                      ▼
2. render_charts()     3. generate_narrative()    4. Tables & Breakdowns
(Matplotlib PNGs)       (Local LLM Prompt)         (Auto-formatted)
     │                            │                      │
     │                            ▼                      │
     │                 verifier.verify()                 │
     │                  (Check & Retry)                  │
     │                            │                      │
     └───────────────────┬────────┘                      │
                         ▼                               ▼
                 5. build_report() (HTML + ReportLab PDF)
                         │
                         ▼
        settings.reports_dir / report_*.html & .pdf
```

---

## Domain Pack Extensibility

The reporting core (`report.py`, `verifier.py`, `charts.py`, `pdf_report.py`) contains **zero domain-specific vocabulary**.

- Domain packs extend `DomainPack.report_sections`.
- For example, `PharmacyDomainPack` declares:
  ```python
  @property
  def report_sections(self) -> List[str]:
      return ["expiry_risk"]
  ```
- When `expiry_risk` is present in `report_data.sections`, the report automatically generates the Expiry Risk bucket chart and the Batch Detail Table.
- A future Grocery or Retail domain pack can declare its own sections (e.g. `shrinkage_risk`, `spoilage_rate`) without modifying any report engine core code.

---

## API Endpoints

### 1. Generate Report
`POST /api/report/generate`

**Payload:**
```json
{
  "source_file": "pharmacy_sales.xlsx",
  "domain": "pharmacy",
  "business_name": "Al-Shifa Pharmacy",
  "max_regeneration_attempts": 1,
  "formats": ["html", "pdf"]
}
```

**Response:**
```json
{
  "report": {
    "verification_passed": true,
    "regenerated": false,
    "html_path": ".../reports/report_pharmacy_20260913_150000.html",
    "pdf_path": ".../reports/report_pharmacy_20260913_150000.pdf",
    "verification": {
      "all_verified": true,
      "verified_count": 8,
      "mismatch_count": 0,
      "unmatched_count": 0
    }
  },
  "download_url_html": "/api/report/download/report_pharmacy_20260913_150000.html",
  "download_url_pdf": "/api/report/download/report_pharmacy_20260913_150000.pdf"
}
```

### 2. Download Report
`GET /api/report/download/{filename}`
Serves either `.html` or `.pdf` with path traversal protections.
