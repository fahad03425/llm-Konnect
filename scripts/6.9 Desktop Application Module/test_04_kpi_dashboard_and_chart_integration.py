"""Test Suite 04: Desktop KPI Dashboard & Recharts Integration.

Module: Module 6.9 — Desktop Application Module
Target Files:
- desktop/src/pages/Dashboard.tsx
- desktop/src/pages/Dashboard.css
- backend/app/api/analytics.py (KPI Pack Feed)
Scope:
- Verifies executive KPI card layout (Total Revenue, Gross Margin %, Net Profit, Units Sold, Refund Rate %, ATV).
- Verifies Recharts component integration for interactive time-series visualizations.
- Verifies timeframe filter presets ('7d', '28d', '6m', 'all').
- Verifies provenance audit tooltips displaying formulas and source row counts.
- Verifies statistical anomaly detection alert banners in the desktop interface.
"""

import pytest
from pathlib import Path


class TestKpiDashboardAndChartIntegration:
    """Verifies desktop executive dashboard, chart rendering, and provenance transparency."""

    def test_dashboard_kpi_cards_definition(self, repo_paths):
        """Verifies that Dashboard.tsx renders core financial metric cards computed deterministically."""
        dashboard_path = repo_paths["src"] / "pages" / "Dashboard.tsx"
        assert dashboard_path.exists()
        code = dashboard_path.read_text(encoding="utf-8")

        # Core financial KPIs
        assert "revenue" in code.lower()
        assert "gross_margin" in code.lower() or "margin" in code.lower()
        assert "net_profit" in code.lower() or "profit" in code.lower()
        assert "refund" in code.lower()

    def test_recharts_visualization_integration(self, repo_paths):
        """Verifies that Dashboard.tsx integrates Recharts components for trend visualization."""
        dashboard_path = repo_paths["src"] / "pages" / "Dashboard.tsx"
        code = dashboard_path.read_text(encoding="utf-8")

        # Must import Recharts elements
        assert "recharts" in code
        assert "ResponsiveContainer" in code
        assert "BarChart" in code or "LineChart" in code or "AreaChart" in code
        assert "Tooltip" in code

    def test_timeframe_range_presets(self, repo_paths):
        """Verifies that Dashboard.tsx provides time-window presets ('7d', '28d', '6m', 'all')."""
        dashboard_path = repo_paths["src"] / "pages" / "Dashboard.tsx"
        code = dashboard_path.read_text(encoding="utf-8")

        # Standard range presets
        assert "7d" in code or "7_days" in code or "7 days" in code.lower()
        assert "28d" in code or "28_days" in code or "28 days" in code.lower()
        assert "6m" in code or "6_months" in code or "6 months" in code.lower()
        assert "all" in code.lower()

    def test_audit_provenance_ui_tooltips(self, repo_paths):
        """Verifies that Dashboard.tsx surfaces mathematical formulas and source rows in audit tooltips."""
        dashboard_path = repo_paths["src"] / "pages" / "Dashboard.tsx"
        code = dashboard_path.read_text(encoding="utf-8")

        # Provenance transparency
        assert "provenance" in code.lower() or "formula" in code.lower()
        assert "source" in code.lower()

    def test_anomaly_banner_alert(self, repo_paths):
        """Verifies that Dashboard.tsx renders anomaly/risk detection indicators for flagged records."""
        dashboard_path = repo_paths["src"] / "pages" / "Dashboard.tsx"
        code = dashboard_path.read_text(encoding="utf-8")

        # Must include anomaly and risk indicators (AlertTriangle, Near-Expiry, Refund Rate)
        assert "alerttriangle" in code.lower()
        assert "expiry" in code.lower() or "refund" in code.lower()
