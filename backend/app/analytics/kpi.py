"""
Module 6.6 (KPI Engine) — kpi.py

The CORE, domain-agnostic financial KPIs. Every figure here is computed
deterministically in pandas and carries provenance back to the contributing
canonical rows (`source_row`).

Rules this file obeys without exception:
  * The LLM is NEVER involved: this package does not import the LLM client module.
  * Deterministic: same input -> identical output. Breakdowns are sorted explicitly.
  * Offline, CPU-only, pure pandas.
  * Domain-agnostic: NO domain-specific vocabulary. Domain KPIs register additively
    via `KPIEngine.register` (see engine.py) and never require editing this file.
  * A KPI that cannot be computed returns an `unavailable` result WITH A REASON.
    It never returns a silently-wrong 0 and it never raises.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd

from app.core.config import settings
from app.analytics.filters import KPIFilters
from app.analytics.models import (
    UNIT_COUNT,
    UNIT_CURRENCY,
    UNIT_PERCENT,
    KPIResult,
    Period,
    Provenance,
    unavailable,
)

# ---------------------------------------------------------------------------
# Transaction-kind vocabulary (generic business terms, not domain nouns)
# ---------------------------------------------------------------------------

_SALE_TOKENS = frozenset({"sale", "sales", "sold", "sell", "invoice", "pos", "revenue", "income"})
_EXPENSE_TOKENS = frozenset(
    {"expense", "expenses", "purchase", "purchases", "buy", "bought", "bill", "payable", "cogs", "overhead"}
)
_REFUND_TOKENS = frozenset({"refund", "refunds", "return", "returns", "returned", "creditnote", "reversal"})

_MONEY_ROUND = 2
_PCT_ROUND = 2


@dataclass(frozen=True)
class TxnClassification:
    """Boolean masks splitting rows into sales / expenses / refunds."""

    has_column: bool
    sale: pd.Series
    expense: pd.Series
    refund: pd.Series
    notes: List[str]


def classify_transactions(df: pd.DataFrame) -> TxnClassification:
    """
    Split rows by `txn_type`.

    When the canonical `txn_type` column is absent (common for a plain sales/POS
    export), every row is treated as a sale and that assumption is recorded. In
    that case expense and refund masks are all-False but `has_column` is False, so
    callers can return "unavailable + reason" rather than a misleading zero.
    """
    if "txn_type" not in df.columns:
        return TxnClassification(
            has_column=False,
            sale=pd.Series(True, index=df.index),
            expense=pd.Series(False, index=df.index),
            refund=pd.Series(False, index=df.index),
            notes=[
                "canonical 'txn_type' column is absent; every row was treated as a SALE "
                "for revenue purposes, and expense/refund figures are reported as unavailable"
            ],
        )

    tokens = (
        df["txn_type"]
        .astype(str)
        .str.casefold()
        .str.replace(r"[^a-z]+", " ", regex=True)
        .str.strip()
    )

    def _match(vocab: frozenset) -> pd.Series:
        return tokens.apply(lambda t: bool(vocab & set(t.split())) if t else False)

    sale = _match(_SALE_TOKENS)
    expense = _match(_EXPENSE_TOKENS)
    refund = _match(_REFUND_TOKENS)

    # A refund is a refund even when the label also says "sale return".
    sale = sale & ~refund
    expense = expense & ~refund

    notes: List[str] = []
    unclassified = ~(sale | expense | refund)
    if unclassified.any():
        labels = sorted(set(df.loc[unclassified, "txn_type"].astype(str)))[:5]
        notes.append(
            f"{int(unclassified.sum())} row(s) have a txn_type that matched no known "
            f"sale/expense/refund term and were excluded (e.g. {labels})"
        )

    return TxnClassification(has_column=True, sale=sale, expense=expense, refund=refund, notes=notes)


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def _row_refs(df: pd.DataFrame, mask: pd.Series) -> List[int]:
    """`source_row` values of the masked rows, sorted. Falls back to 1-based position."""
    if df.empty:
        return []
    positions = {label: i for i, label in enumerate(df.index)}
    if "source_row" in df.columns:
        refs: List[int] = []
        for label, value in zip(df.index[mask], df.loc[mask, "source_row"]):
            try:
                refs.append(int(value) if pd.notna(value) else positions[label] + 1)
            except (TypeError, ValueError):
                refs.append(positions[label] + 1)
        return sorted(refs)
    return sorted(positions[label] + 1 for label in df.index[mask])


def _source_labels(df: pd.DataFrame, mask: pd.Series) -> List[str]:
    """Distinct origin labels for the contributing rows, sorted for determinism."""
    for col in ("source_file", "source_connector"):
        if col in df.columns:
            return sorted(set(df.loc[mask, col].dropna().astype(str).tolist()))
    return []


def _period(df: pd.DataFrame, mask: pd.Series) -> Optional[Period]:
    """Date span actually covered by the contributing rows."""
    if "date" not in df.columns:
        return None
    dates = pd.to_datetime(df.loc[mask, "date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return Period(start=dates.min().strftime("%Y-%m-%d"), end=dates.max().strftime("%Y-%m-%d"))


def build_provenance(
    df: pd.DataFrame,
    mask: pd.Series,
    filters: KPIFilters,
    columns_used: List[str],
    assumptions: List[str],
) -> Provenance:
    """Assemble the audit trail for one KPI from the rows that actually contributed."""
    refs = _row_refs(df, mask)
    cap = int(settings.analytics_max_provenance_rows)
    return Provenance(
        filters=filters.as_dict(),
        filter_description=filters.describe(),
        row_count=len(refs),
        source_rows=refs[:cap],
        source_rows_truncated=len(refs) > cap,
        sources=_source_labels(df, mask),
        columns_used=list(dict.fromkeys(columns_used)),
        assumptions=list(dict.fromkeys(assumptions)),
    )


def _merge_provenance(
    filters: KPIFilters, components: List[KPIResult], row_source: Optional[KPIResult] = None
) -> Provenance:
    """
    Combine the provenance of several component KPIs into one audit trail.

    `row_source`, when given, supplies the contributing rows instead of the union
    (used by average_transaction_value, whose rows are the revenue rows).
    """
    cap = int(settings.analytics_max_provenance_rows)
    if row_source is not None:
        rows = list(row_source.provenance.source_rows)
        row_count = row_source.provenance.row_count
        truncated = row_source.provenance.source_rows_truncated
    else:
        merged = set()
        for c in components:
            merged |= set(c.provenance.source_rows)
        rows = sorted(merged)
        row_count = len(rows)
        truncated = len(rows) > cap

    assumptions: List[str] = []
    for c in components:
        for note in c.provenance.assumptions:
            if note not in assumptions:
                assumptions.append(note)

    sources, columns = set(), set()
    for c in components:
        sources |= set(c.provenance.sources)
        columns |= set(c.provenance.columns_used)

    return Provenance(
        filters=filters.as_dict(),
        filter_description=filters.describe(),
        row_count=row_count,
        source_rows=rows[:cap],
        source_rows_truncated=truncated,
        sources=sorted(sources),
        columns_used=sorted(columns),
        assumptions=assumptions,
    )


# ---------------------------------------------------------------------------
# Column access helpers
# ---------------------------------------------------------------------------


def _amount_series(df: pd.DataFrame) -> Tuple[Optional[pd.Series], List[str], List[str]]:
    """
    The monetary value of each row.

    Prefers the canonical `amount` (line total). If absent, derives
    `unit_price x quantity` and records that derivation as an assumption.

    Returns (series_or_None, columns_used, notes).
    """
    if "amount" in df.columns:
        return pd.to_numeric(df["amount"], errors="coerce"), ["amount"], []
    if "unit_price" in df.columns and "quantity" in df.columns:
        series = pd.to_numeric(df["unit_price"], errors="coerce") * pd.to_numeric(
            df["quantity"], errors="coerce"
        )
        return (
            series,
            ["unit_price", "quantity"],
            ["canonical 'amount' column is absent; amount derived as unit_price x quantity"],
        )
    return None, [], []


def _cogs_series(df: pd.DataFrame) -> Tuple[Optional[pd.Series], List[str], List[str]]:
    """
    Cost of goods sold per row.

    `cost` is a UNIT cost in the canonical schema, so COGS is `cost x quantity`
    when a quantity is available; otherwise `cost` is taken as the line cost.
    Whichever rule applied is recorded in the result's formula and assumptions.
    """
    if "cost" not in df.columns:
        return None, [], []
    cost = pd.to_numeric(df["cost"], errors="coerce")
    if "quantity" in df.columns:
        qty = pd.to_numeric(df["quantity"], errors="coerce")
        return cost * qty, ["cost", "quantity"], []
    return (
        cost,
        ["cost"],
        ["canonical 'quantity' column is absent; 'cost' treated as a per-row line cost, not a unit cost"],
    )


def _round_money(value) -> float:
    return round(float(value), _MONEY_ROUND)


def _round_pct(value) -> float:
    return round(float(value), _PCT_ROUND)


def _no_rows(df: pd.DataFrame) -> pd.Series:
    return pd.Series(False, index=df.index, dtype=bool)


# ---------------------------------------------------------------------------
# Generic building blocks
# ---------------------------------------------------------------------------


def _sum_over(
    df: pd.DataFrame,
    filters: KPIFilters,
    kind_mask: pd.Series,
    key: str,
    name: str,
    formula: str,
    extra_notes: List[str],
) -> KPIResult:
    """Sum row amounts over `kind_mask`, with full provenance. Shared by the money KPIs."""
    amounts, cols, notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], extra_notes),
        )

    contributing = kind_mask & amounts.notna()
    provenance = build_provenance(df, contributing, filters, cols, extra_notes + notes)

    if not contributing.any():
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "no rows with a usable amount matched this KPI's criteria",
            provenance,
        )

    return KPIResult(
        key=key,
        name=name,
        value=_round_money(amounts[contributing].sum()),
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        period=_period(df, contributing),
    )


def _ratio(
    key: str,
    name: str,
    formula: str,
    numerator: KPIResult,
    denominator: KPIResult,
    filters: KPIFilters,
) -> KPIResult:
    """
    Percentage of two KPIResults, with zero-division safety.

    NaN and inf never escape: a zero denominator yields unavailable + reason.
    """
    provenance = _merge_provenance(filters, [numerator, denominator])

    for component, role in ((numerator, "numerator"), (denominator, "denominator")):
        if not component.is_available:
            return unavailable(
                key, name, UNIT_PERCENT, formula,
                f"{role} '{component.key}' is unavailable: {component.reason}",
                provenance,
            )

    if float(denominator.value) == 0.0:
        return unavailable(
            key, name, UNIT_PERCENT, formula,
            f"cannot compute a percentage: {denominator.key} is zero",
            provenance,
        )

    return KPIResult(
        key=key,
        name=name,
        value=_round_pct(100.0 * float(numerator.value) / float(denominator.value)),
        unit=UNIT_PERCENT,
        formula=formula,
        provenance=provenance,
        period=numerator.period or denominator.period,
    )


def _breakdown(
    df: pd.DataFrame,
    filters: KPIFilters,
    kind_mask: pd.Series,
    group_column: str,
    key: str,
    name: str,
    formula: str,
    extra_notes: List[str],
    top_n: Optional[int] = None,
) -> KPIResult:
    """
    Grouped sum of amounts, returned as a small breakdown table.

    Sorting is explicit (amount desc, then group label asc) so output is stable
    across runs and pandas versions. `value` is the total across ALL groups, even
    when the breakdown itself is truncated to top-N.
    """
    if group_column not in df.columns:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            f"canonical '{group_column}' column is not present in this data",
            build_provenance(df, _no_rows(df), filters, [], extra_notes),
        )

    amounts, cols, notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], extra_notes),
        )

    contributing = kind_mask & amounts.notna() & df[group_column].notna()
    provenance = build_provenance(df, contributing, filters, cols + [group_column], extra_notes + notes)

    if not contributing.any():
        return unavailable(
            key, name, UNIT_CURRENCY, formula,
            f"no rows with both a usable amount and a '{group_column}' value matched this KPI's criteria",
            provenance,
        )

    grouped = (
        pd.DataFrame(
            {
                group_column: df.loc[contributing, group_column].astype(str),
                "amount": amounts[contributing],
            }
        )
        .groupby(group_column, as_index=False, sort=False)
        .agg(amount=("amount", "sum"), row_count=("amount", "size"))
        .sort_values(["amount", group_column], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )

    total = _round_money(grouped["amount"].sum())
    truncated_note = []
    if top_n is not None and len(grouped) > top_n:
        truncated_note = [
            f"breakdown shows the top {top_n} of {len(grouped)} groups; "
            f"'value' is the total across all groups"
        ]
        grouped = grouped.head(top_n)

    if truncated_note:
        provenance = build_provenance(
            df, contributing, filters, cols + [group_column], extra_notes + notes + truncated_note
        )

    return KPIResult(
        key=key,
        name=name,
        value=total,
        unit=UNIT_CURRENCY,
        formula=formula,
        provenance=provenance,
        period=_period(df, contributing),
        breakdown=[
            {
                group_column: row[group_column],
                "amount": _round_money(row["amount"]),
                "row_count": int(row["row_count"]),
            }
            for _, row in grouped.iterrows()
        ],
        breakdown_columns=[group_column, "amount", "row_count"],
    )


# ---------------------------------------------------------------------------
# Core KPI implementations
# Each takes (df, filters, domain) and returns a KPIResult. `domain` is accepted
# for signature uniformity with domain-registered KPIs; core KPIs ignore it.
# ---------------------------------------------------------------------------


def total_revenue(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Sum of sale amounts."""
    txn = classify_transactions(df)
    return _sum_over(
        df, filters, txn.sale,
        "total_revenue", "Total Revenue",
        "sum of amount where txn_type is a sale",
        txn.notes,
    )


def total_expenses(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Sum of expense/purchase amounts."""
    txn = classify_transactions(df)
    formula = "sum of amount where txn_type is an expense or purchase"
    if not txn.has_column:
        return unavailable(
            "total_expenses", "Total Expenses", UNIT_CURRENCY, formula,
            "canonical 'txn_type' column is absent, so expense rows cannot be distinguished "
            "from sales; reporting unavailable rather than a misleading zero",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
    return _sum_over(df, filters, txn.expense, "total_expenses", "Total Expenses", formula, txn.notes)


def total_refunds(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Sum of refund amounts, reported as a positive magnitude.

    Sources model refunds either as positive rows tagged 'refund' or as negative
    amounts; the absolute value is taken so the figure is direction-independent.
    """
    txn = classify_transactions(df)
    formula = "sum of |amount| where txn_type is a refund or return"
    if not txn.has_column:
        return unavailable(
            "total_refunds", "Total Refunds", UNIT_CURRENCY, formula,
            "canonical 'txn_type' column is absent, so refund rows cannot be identified; "
            "reporting unavailable rather than a misleading zero",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    amounts, cols, notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            "total_refunds", "Total Refunds", UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    contributing = txn.refund & amounts.notna()
    provenance = build_provenance(df, contributing, filters, cols, txn.notes + notes)

    # txn_type exists and no row is a refund: a genuine, auditable zero.
    value = 0.0 if not contributing.any() else _round_money(amounts[contributing].abs().sum())

    return KPIResult(
        key="total_refunds", name="Total Refunds", value=value, unit=UNIT_CURRENCY,
        formula=formula, provenance=provenance, period=_period(df, contributing),
    )


def net_profit(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Net profit = total revenue - total expenses - total refunds.

    Refunds are subtracted separately from expenses; a source that models refunds
    as negative sale rows would otherwise double-count them, which is why refunds
    are classified out of the sale mask in `classify_transactions`.
    """
    formula = "total_revenue - total_expenses - total_refunds"
    revenue = total_revenue(df, filters)
    expenses = total_expenses(df, filters)
    refunds = total_refunds(df, filters)
    provenance = _merge_provenance(filters, [revenue, expenses, refunds])

    for component in (revenue, expenses, refunds):
        if not component.is_available:
            return unavailable(
                "net_profit", "Net Profit", UNIT_CURRENCY, formula,
                f"component '{component.key}' is unavailable: {component.reason}",
                provenance,
            )

    return KPIResult(
        key="net_profit", name="Net Profit",
        value=_round_money(float(revenue.value) - float(expenses.value) - float(refunds.value)),
        unit=UNIT_CURRENCY, formula=formula, provenance=provenance, period=revenue.period,
    )


def gross_profit(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Gross profit = sale revenue - COGS, over sale rows that have both figures."""
    formula = "sum(amount) - sum(cost x quantity) over sale rows where cost is known"
    txn = classify_transactions(df)

    amounts, amount_cols, amount_notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            "gross_profit", "Gross Profit", UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    cogs, cost_cols, cost_notes = _cogs_series(df)
    if cogs is None:
        return unavailable(
            "gross_profit", "Gross Profit", UNIT_CURRENCY, formula,
            "canonical 'cost' column is not present, so cost of goods sold is unknown",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    contributing = txn.sale & amounts.notna() & cogs.notna()
    provenance = build_provenance(
        df, contributing, filters, amount_cols + cost_cols, txn.notes + amount_notes + cost_notes
    )
    if not contributing.any():
        return unavailable(
            "gross_profit", "Gross Profit", UNIT_CURRENCY, formula,
            "no sale rows have both an amount and a cost, so gross profit cannot be computed",
            provenance,
        )

    return KPIResult(
        key="gross_profit", name="Gross Profit",
        value=_round_money(amounts[contributing].sum() - cogs[contributing].sum()),
        unit=UNIT_CURRENCY, formula=formula, provenance=provenance,
        period=_period(df, contributing),
    )


def _revenue_matched_to_cost(df: pd.DataFrame, filters: KPIFilters) -> KPIResult:
    """
    Revenue restricted to exactly the rows gross_profit used.

    Gross margin must divide by the revenue of the costed rows only; dividing by
    total revenue would understate margin whenever some rows lack a cost.
    """
    formula = "sum of amount over sale rows that also have a known cost"
    txn = classify_transactions(df)
    amounts, amount_cols, amount_notes = _amount_series(df)
    cogs, cost_cols, cost_notes = _cogs_series(df)

    if amounts is None or cogs is None:
        return unavailable(
            "costed_revenue", "Revenue (costed rows)", UNIT_CURRENCY, formula,
            "a required column ('amount'/'unit_price'+'quantity', or 'cost') is not present",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    contributing = txn.sale & amounts.notna() & cogs.notna()
    provenance = build_provenance(
        df, contributing, filters, amount_cols + cost_cols, txn.notes + amount_notes + cost_notes
    )
    if not contributing.any():
        return unavailable(
            "costed_revenue", "Revenue (costed rows)", UNIT_CURRENCY, formula,
            "no sale rows have both an amount and a cost", provenance,
        )

    return KPIResult(
        key="costed_revenue", name="Revenue (costed rows)",
        value=_round_money(amounts[contributing].sum()),
        unit=UNIT_CURRENCY, formula=formula, provenance=provenance,
        period=_period(df, contributing),
    )


def gross_margin_pct(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Gross margin % = gross profit / revenue of costed rows x 100."""
    return _ratio(
        "gross_margin_pct", "Gross Margin %",
        "gross_profit / revenue of costed sale rows x 100",
        gross_profit(df, filters), _revenue_matched_to_cost(df, filters), filters,
    )


def net_margin_pct(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Net margin % = net profit / total revenue x 100."""
    return _ratio(
        "net_margin_pct", "Net Margin %",
        "net_profit / total_revenue x 100",
        net_profit(df, filters), total_revenue(df, filters), filters,
    )


def refund_rate_pct(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Refund rate % = total refunds / total revenue x 100."""
    return _ratio(
        "refund_rate_pct", "Refund Rate %",
        "total_refunds / total_revenue x 100",
        total_refunds(df, filters), total_revenue(df, filters), filters,
    )


def transaction_count(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Number of sale transactions.

    Counts distinct `invoice_id` when that column exists (a multi-line invoice is
    one transaction); otherwise counts sale rows. The rule used is recorded.
    """
    formula = "count of distinct invoice_id over sale rows"
    txn = classify_transactions(df)
    notes = list(txn.notes)

    if df.empty:
        return unavailable(
            "transaction_count", "Transaction Count", UNIT_COUNT, formula,
            "no rows are available to count",
            build_provenance(df, _no_rows(df), filters, [], notes),
        )

    if "invoice_id" in df.columns:
        contributing = txn.sale & df["invoice_id"].notna()
        columns = ["invoice_id"]
        value = int(df.loc[contributing, "invoice_id"].astype(str).nunique())
    else:
        contributing = txn.sale
        formula = "count of sale rows"
        columns = []
        notes.append("canonical 'invoice_id' column is absent; each row counted as one transaction")
        value = int(contributing.sum())

    provenance = build_provenance(df, contributing, filters, columns, notes)
    if not contributing.any():
        return unavailable(
            "transaction_count", "Transaction Count", UNIT_COUNT, formula,
            "no sale rows matched the requested filter", provenance,
        )

    return KPIResult(
        key="transaction_count", name="Transaction Count", value=float(value),
        unit=UNIT_COUNT, formula=formula, provenance=provenance, period=_period(df, contributing),
    )


def average_transaction_value(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Average bill value = total revenue / transaction count."""
    formula = "total_revenue / transaction_count"
    revenue = total_revenue(df, filters)
    count = transaction_count(df, filters)
    provenance = _merge_provenance(filters, [revenue, count], row_source=revenue)

    for component in (revenue, count):
        if not component.is_available:
            return unavailable(
                "average_transaction_value", "Average Transaction Value", UNIT_CURRENCY, formula,
                f"component '{component.key}' is unavailable: {component.reason}", provenance,
            )

    if float(count.value) == 0.0:
        return unavailable(
            "average_transaction_value", "Average Transaction Value", UNIT_CURRENCY, formula,
            "transaction_count is zero", provenance,
        )

    return KPIResult(
        key="average_transaction_value", name="Average Transaction Value",
        value=_round_money(float(revenue.value) / float(count.value)),
        unit=UNIT_CURRENCY, formula=formula, provenance=provenance, period=revenue.period,
    )


def expense_breakdown_by_category(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Expenses grouped by canonical `category`."""
    formula = "sum of amount grouped by category, over expense rows"
    txn = classify_transactions(df)
    if not txn.has_column:
        return unavailable(
            "expense_breakdown_by_category", "Expense Breakdown by Category", UNIT_CURRENCY, formula,
            "canonical 'txn_type' column is absent, so expense rows cannot be identified",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
    return _breakdown(
        df, filters, txn.expense, "category",
        "expense_breakdown_by_category", "Expense Breakdown by Category", formula, txn.notes,
    )


def expense_breakdown_by_supplier(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Expenses grouped by canonical `supplier_id`."""
    formula = "sum of amount grouped by supplier_id, over expense rows"
    txn = classify_transactions(df)
    if not txn.has_column:
        return unavailable(
            "expense_breakdown_by_supplier", "Expense Breakdown by Supplier", UNIT_CURRENCY, formula,
            "canonical 'txn_type' column is absent, so expense rows cannot be identified",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )
    return _breakdown(
        df, filters, txn.expense, "supplier_id",
        "expense_breakdown_by_supplier", "Expense Breakdown by Supplier", formula, txn.notes,
    )


def revenue_breakdown_by_category(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Revenue grouped by canonical `category`."""
    txn = classify_transactions(df)
    return _breakdown(
        df, filters, txn.sale, "category",
        "revenue_breakdown_by_category", "Revenue Breakdown by Category",
        "sum of amount grouped by category, over sale rows", txn.notes,
    )


def revenue_breakdown_by_product(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """Revenue grouped by canonical `product_id`, top-N by amount."""
    txn = classify_transactions(df)
    top_n = int(settings.analytics_top_n)
    return _breakdown(
        df, filters, txn.sale, "product_id",
        "revenue_breakdown_by_product", f"Revenue Breakdown by Product (top {top_n})",
        f"sum of amount grouped by product_id, over sale rows, top {top_n} by amount",
        txn.notes, top_n=top_n,
    )


def revenue_by_month(df: pd.DataFrame, filters: KPIFilters, domain: str = "") -> KPIResult:
    """
    Revenue aggregated per calendar month, ascending.

    This is a plain historical aggregation. It is NOT a forecast and performs no
    trend fitting or prediction of any kind.
    """
    formula = "sum of amount grouped by calendar month (YYYY-MM), over sale rows"
    txn = classify_transactions(df)

    if "date" not in df.columns:
        return unavailable(
            "revenue_by_month", "Revenue by Month", UNIT_CURRENCY, formula,
            "canonical 'date' column is not present, so rows cannot be grouped by month",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    amounts, cols, notes = _amount_series(df)
    if amounts is None:
        return unavailable(
            "revenue_by_month", "Revenue by Month", UNIT_CURRENCY, formula,
            "no monetary column available: need 'amount', or both 'unit_price' and 'quantity'",
            build_provenance(df, _no_rows(df), filters, [], txn.notes),
        )

    dates = pd.to_datetime(df["date"], errors="coerce")
    contributing = txn.sale & amounts.notna() & dates.notna()
    provenance = build_provenance(df, contributing, filters, cols + ["date"], txn.notes + notes)

    if not contributing.any():
        return unavailable(
            "revenue_by_month", "Revenue by Month", UNIT_CURRENCY, formula,
            "no sale rows have both a usable amount and a parseable date", provenance,
        )

    grouped = (
        pd.DataFrame({"month": dates[contributing].dt.strftime("%Y-%m"), "amount": amounts[contributing]})
        .groupby("month", as_index=False, sort=False)
        .agg(amount=("amount", "sum"), row_count=("amount", "size"))
        .sort_values("month", ascending=True, kind="mergesort")
        .reset_index(drop=True)
    )

    return KPIResult(
        key="revenue_by_month", name="Revenue by Month",
        value=_round_money(grouped["amount"].sum()),
        unit=UNIT_CURRENCY, formula=formula, provenance=provenance,
        period=_period(df, contributing),
        breakdown=[
            {"month": row["month"], "amount": _round_money(row["amount"]), "row_count": int(row["row_count"])}
            for _, row in grouped.iterrows()
        ],
        breakdown_columns=["month", "amount", "row_count"],
    )
