"""Schema grounded query planning and deterministic execution for tabular data.

The language model only maps a request to a small validated query plan. All
filtering, grouping and arithmetic run over the selected DataFrame locally.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd


PLAN_FORMAT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ready", "clarification", "unsupported"]},
        "reason": {"type": "string"},
        "filters": {"type": "array", "items": {"type": "object", "properties": {
            "field": {"type": "string"}, "op": {"type": "string", "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "contains"]}, "value": {}
        }, "required": ["field", "op", "value"]}},
        "group_by": {"type": "array", "items": {"type": "string"}},
        "having": {"type": "object", "properties": {
            "aggregate": {"type": "string", "enum": ["count_rows", "count_non_null", "count_distinct"]},
            "field": {"type": "string"},
            "op": {"type": "string", "enum": ["eq", "ne", "gt", "gte", "lt", "lte"]},
            "value": {"type": "number"}
        }, "required": ["aggregate", "op", "value"]},
        "measure": {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["count_rows", "count_distinct", "sum", "mean", "median", "min", "max"]},
            "field": {"type": "string"}
        }, "required": ["op", "field"]},
        "limit": {"type": "integer"}
    },
    "required": ["status", "reason", "filters", "group_by", "having", "measure", "limit"]
}


def needs_compositional_plan(question: str) -> bool:
    """Route compositional aggregation requests to a schema grounded planner."""
    q = re.sub(r"\s+", " ", str(question).casefold())
    asks_group_size_condition = bool(re.search(r"\b(exactly|at least|at most|more than|fewer than|less than|greater than)\b", q))
    asks_count = bool(re.search(r"\b(how many|count|number of)\b", q))
    asks_group_entity = bool(re.search(r"\b(receipts?|invoices?|transactions?|bills?|orders?|customers?|products?|patients?|suppliers?|vendors?|accounts?)\b", q))
    asks_child_rows = bool(re.search(r"\b(rows?|records?|lines?|items?|entries|details?)\b", q))
    # "At least one returned/discounted item" is an existence predicate and
    # already has dedicated handlers; do not reinterpret it as a line-count
    # comparison. Explicit detail-line wording is a genuine group-size query.
    existence_phrase = bool(re.search(r"\bat least one\b", q))
    explicit_detail_count = bool(re.search(r"\bdetail\s+(?:rows?|records?|lines?|items?)\b", q))
    return asks_group_size_condition and asks_count and asks_group_entity and asks_child_rows and (not existence_phrase or explicit_detail_count)


def _schema(frame: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for column in frame.columns:
        s = frame[column]
        nonnull = s.dropna()
        # Give categorical labels only when they are genuinely low-cardinality.
        # Identifier examples encourage small models to hallucinate a specific
        # record filter; names and IDs are described by schema, never sampled.
        is_identifier = bool(re.search(r"(?:^|[_ ])(?:id|no|number|ref|key|code)(?:$|[_ ])", str(column).casefold().replace("-", " ")))
        unique = nonnull.nunique(dropna=True)
        samples = [str(v)[:80] for v in nonnull.drop_duplicates().head(12).tolist()] if unique <= 12 and not is_identifier else []
        normalized = re.sub(r"[^a-z0-9]", "", str(column).casefold())
        role = "entity_key" if re.search(r"(?:invoice|receipt|transaction|bill|order|customer|product|patient|supplier|vendor).*(?:id|key|ref|no)$", normalized) else (
            "detail_value" if re.search(r"(?:product|item|line|detail|sku|medicine)", normalized) else "field"
        )
        columns.append({"name": str(column), "dtype": str(s.dtype), "populated": int(nonnull.size), "role_hint": role, "samples": samples})
    provenance = {}
    for column in ("table_name", "source_table", "source_file", "file_name", "database_name"):
        if column in frame:
            provenance[column] = [str(v)[:100] for v in frame[column].dropna().astype(str).drop_duplicates().head(30)]
    return {"rows": int(len(frame)), "columns": columns, "source_values": provenance}


def _decode_plan(question: str, frame: pd.DataFrame) -> dict[str, Any]:
    from app.core.llm import llm

    schema = _schema(frame)
    messages = [
        {"role": "system", "content": (
            "Translate the user's request into a query plan over the supplied selected-data schema. "
            "Return clarification if the requested entity, source, or calculation is ambiguous or absent. "
            "Use exact column names from schema. Do not calculate results. Use filters only for explicit conditions; "
            "use group_by and having for per-entity row-count conditions. For a request asking how many groups "
            "satisfy a condition, measure count_rows with an empty field. Never invent a field or infer missing facts. "
            "Example: for 'How many invoices have exactly 4 detail rows?', if the schema contains invoice_id, "
            "return group_by=['invoice_id'], having={aggregate:'count_rows',op:'eq',value:4}, and measure count_rows. "
            "Choose the identifier for the entity named by the user, and count rows at the selected detail grain."
            "For a detail-line count, prefer count_non_null over a populated line item/product field when blank rows are possible."
        )},
        {"role": "user", "content": json.dumps({"question": question, "schema": schema}, ensure_ascii=False)},
    ]
    raw = llm.chat(messages=messages, options={"temperature": 0}, format=PLAN_FORMAT)
    return json.loads(raw)


def _decline(status: str, reason: str, diagnostic: dict[str, Any]) -> dict[str, Any]:
    message = reason or ("I need clarification to identify the requested data." if status == "clarification" else "I can't answer this from the selected data.")
    return {"answer": message, "values": {"status": status, "query_diagnostic": diagnostic}, "source_rows": []}


def answer_schema_query(question: str, frame: pd.DataFrame) -> dict[str, Any] | None:
    """Plan and execute supported compositional queries, declining unsafe plans."""
    if not needs_compositional_plan(question) or frame is None or frame.empty:
        return None
    schema = _schema(frame)
    fields = {str(c): c for c in frame.columns}
    diag: dict[str, Any] = {"planner": "schema_query_v1", "input_rows": len(frame), "column_count": len(fields)}
    try:
        plan = _decode_plan(question, frame)
    except Exception as exc:
        diag.update({"validation": "planner_error", "error_type": type(exc).__name__})
        return _decline("unavailable", "I couldn't safely interpret this structured query. Please rephrase it with the field or grouping you mean.", diag)
    status = plan.get("status")
    diag["plan"] = plan
    if status != "ready":
        return _decline("clarification" if status == "clarification" else "unsupported", str(plan.get("reason", "")), diag)
    groups = plan.get("group_by", [])
    measure = plan.get("measure") or {}
    filters = plan.get("filters", [])
    having = plan.get("having")
    explicit_filters = [condition for condition in filters if _filter_is_grounded(condition, question)]
    if len(explicit_filters) != len(filters):
        discarded_count = len(filters) - len(explicit_filters)
        plan["filters"] = explicit_filters
        filters = explicit_filters
        diag["discarded_ungrounded_filters"] = discarded_count
        diag["plan"] = plan
    inferred_group = _infer_group_key(question, fields)
    groups_are_known = all(name in fields for name in groups)
    entity_name = _requested_entity(question)
    if inferred_group and groups_are_known and (not groups or any(_entity_key_score(entity_name, fields[name]) == 0 for name in groups)):
        groups = [inferred_group]
        plan["group_by"] = groups
        diag["plan"] = plan
        diag["schema_repair"] = "aligned_grouping_to_entity_key"
    if having and not groups:
        diag["validation"] = "having_without_group"
        return _decline("unsupported", "The per-entity condition cannot be evaluated because the selected schema has no unambiguous key for that entity.", diag)
    group_size_condition = _parse_group_size_condition(question)
    if group_size_condition and groups:
        having = dict(having or {})
        having.update(group_size_condition)
        plan["having"] = having
        diag["plan"] = plan
        diag["schema_repair"] = "parsed_explicit_group_size_condition"
    if having and re.search(r"\b(detail|sales detail)\s+(?:rows?|records?|lines?|items?)\b", question, re.I):
        detail_field = _infer_detail_field(fields)
        if detail_field:
            having.update({"aggregate": "count_non_null", "field": detail_field})
            diag["plan"] = plan
            diag["schema_repair"] = "detail_rows_counted_by_populated_field"
    if having and re.search(r"\b(how many|count|number of)\b", question, re.I):
        measure = {"op": "count_rows", "field": ""}
        plan["measure"] = measure
        diag["plan"] = plan
    diag["selected_fields"] = list(dict.fromkeys(field for field in [
        *groups, *(f.get("field") for f in filters), having.get("field") if having else None, measure.get("field")
    ] if field))
    referenced = list(groups) + [f.get("field") for f in filters] + [measure.get("field")]
    if having:
        referenced += []
    if any(field not in fields for field in referenced if field):
        diag["validation"] = "unknown_field"
        return _decline("unsupported", "The interpreted request refers to a field that is not in the selected data, so I can't calculate it safely.", diag)
    if having and having.get("aggregate") in {"count_non_null", "count_distinct"} and having.get("field") not in fields:
        diag["validation"] = "unknown_having_field"
        return _decline("unsupported", "The selected data doesn't contain the detail field needed for this group condition.", diag)
    if measure.get("op") not in {"count_rows", "count_distinct", "sum", "mean", "median", "min", "max"} or (measure.get("op") != "count_rows" and not measure.get("field")):
        diag["validation"] = "invalid_measure"
        return _decline("unsupported", "That calculation is not supported by the selected data query engine.", diag)
    work = frame.copy()
    scope_filter = _infer_source_scope(question, work)
    if scope_filter is not None:
        scope_column, scope_mask = scope_filter
        work = work.loc[scope_mask]
        diag["source_scope"] = {"field": scope_column, "rows_after_scope": len(work)}
    for condition in filters:
        col = fields.get(condition.get("field"))
        op, value = condition.get("op"), condition.get("value")
        if col is None or op not in {"eq", "ne", "gt", "gte", "lt", "lte", "contains"}:
            diag["validation"] = "invalid_filter"
            return _decline("unsupported", "I couldn't validate a requested filter against the selected schema.", diag)
        series = work[col]
        try:
            if op == "contains": mask = series.fillna("").astype(str).str.contains(re.escape(str(value)), case=False, regex=True)
            elif op in {"eq", "ne"}:
                mask = series.astype(str).str.casefold().eq(str(value).casefold())
                if op == "ne": mask = ~mask
            else:
                numeric = pd.to_numeric(series, errors="coerce")
                target = float(value)
                mask = {"gt": numeric.gt, "gte": numeric.ge, "lt": numeric.lt, "lte": numeric.le}[op](target)
            work = work.loc[mask]
        except (TypeError, ValueError):
            diag["validation"] = "filter_type_mismatch"
            return _decline("unsupported", "A requested filter doesn't match the field's data type.", diag)
    if groups:
        group_cols = [fields[g] for g in groups]
        grouped = work.dropna(subset=group_cols).groupby(group_cols, dropna=True, sort=False)
        counts = grouped.size()
        if having:
            if having.get("aggregate") not in {"count_rows", "count_non_null", "count_distinct"} or having.get("op") not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                diag["validation"] = "invalid_having"
                return _decline("unsupported", "That group condition isn't supported.", diag)
            op, value = having["op"], float(having["value"])
            group_counts = counts
            if having.get("aggregate") == "count_non_null":
                group_counts = work.groupby(group_cols, dropna=True, sort=False)[fields[having["field"]]].count()
            elif having.get("aggregate") == "count_distinct":
                group_counts = work.groupby(group_cols, dropna=True, sort=False)[fields[having["field"]]].nunique(dropna=True)
            matched = {"eq": group_counts.eq, "ne": group_counts.ne, "gt": group_counts.gt, "gte": group_counts.ge, "lt": group_counts.lt, "lte": group_counts.le}[op](value)
            selected = group_counts.loc[matched]
            op_symbol = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}[op]
            # The outer question determines whether the requested scalar is
            # the number of groups. Small local models sometimes encode that
            # as count_distinct(field); the explicit natural-language request
            # for "how many receipts/invoices/etc" is the authoritative cue.
            asks_group_count = bool(re.search(r"\b(how many|count|number of)\b", question, re.I))
            result = int(len(selected)) if asks_group_count else None
            if result is None:
                diag["validation"] = "ambiguous_group_measure"
                return _decline("clarification", "Please clarify whether you want the number of matching groups or a field aggregate within those groups.", diag)
            rows = work.loc[work[group_cols].apply(tuple, axis=1).isin(selected.index if isinstance(selected.index, pd.MultiIndex) else [(x,) for x in selected.index])]
            if having.get("aggregate") == "count_non_null":
                rows = rows.loc[rows[fields[having["field"]]].notna()]
            entity = _requested_entity(question)
            labels = {"receipt": "receipts", "invoice": "invoices", "transaction": "transactions", "bill": "bills", "order": "orders", "customer": "customers", "product": "products", "patient": "patients", "supplier": "suppliers", "vendor": "vendors"}
            result_text = f"{result:,} {labels.get(entity, 'groups')}"
            explanation = f"where the number of rows per group is {op_symbol} {value:g}"
            citation_rows = _citations(rows)
            values = {"status": "ok", "result": result, "group_by": groups, "having": having, "query_diagnostic": {**diag, "validation": "ok", "output_groups": result, "matched_source_rows": len(rows)}}
            return {"answer": f"{result_text} {explanation}.", "values": values, "source_rows": citation_rows}
        agg = _aggregate(grouped, measure, fields)
        limit = max(1, min(int(plan.get("limit", 20) or 20), 100))
        agg = agg.head(limit)
        rows = work.loc[work[group_cols].apply(tuple, axis=1).isin(agg.index if isinstance(agg.index, pd.MultiIndex) else [(x,) for x in agg.index])]
        result = [{**dict(zip(groups, key if isinstance(key, tuple) else (key,))), "value": _scalar(val)} for key, val in agg.items()]
        values = {"status": "ok", "groups": result, "measure": measure, "query_diagnostic": {**diag, "validation": "ok", "output_groups": len(result), "matched_source_rows": len(rows)}}
        answer = "; ".join(f"{', '.join(str(x) for x in (key if isinstance(key, tuple) else (key,)))}: {_scalar(value):,}" if isinstance(_scalar(value), (int, float)) else f"{key}: {value}" for key, value in agg.items())
        return {"answer": answer + ".", "values": values, "source_rows": _citations(rows)}
    if measure["op"] == "count_rows": result = int(len(work))
    else: result = _aggregate(work, measure, fields)
    return {"answer": f"Result: {_scalar(result):,}." if isinstance(_scalar(result), (int, float)) else f"Result: {_scalar(result)}.", "values": {"status": "ok", "result": _scalar(result), "measure": measure, "query_diagnostic": {**diag, "validation": "ok", "matched_rows": len(work)}}, "source_rows": _citations(work)}


def _aggregate(data, measure, fields):
    op = measure["op"]
    col = fields.get(measure.get("field"))
    if op == "count_rows": return data.size() if hasattr(data, "size") and not isinstance(data, pd.DataFrame) else len(data)
    series = data[col]
    if op == "count_distinct": return series.nunique(dropna=True)
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if op == "sum": return numeric.sum()
    if op == "mean": return numeric.mean()
    if op == "median": return numeric.median()
    if op == "min": return numeric.min()
    if op == "max": return numeric.max()
    raise ValueError("unsupported aggregate")


def _infer_group_key(question: str, fields: dict[str, Any]) -> str | None:
    """Choose a unique entity key using semantic agreement with the schema."""
    q = str(question).casefold()
    aliases = {
        "receipt": ("receipt", "transaction", "invoice", "bill", "order"),
        "invoice": ("invoice", "receipt", "transaction", "bill"),
        "transaction": ("transaction", "invoice", "receipt", "bill"),
        "bill": ("bill", "invoice", "receipt", "transaction"),
        "order": ("order", "purchase", "transaction"),
        "customer": ("customer", "client", "patient"),
        "product": ("product", "item", "medicine", "drug", "sku"),
        "patient": ("patient", "customer", "client"),
        "supplier": ("supplier", "vendor", "distributor"),
        "vendor": ("vendor", "supplier", "distributor"),
    }
    requested = _requested_entity(question)
    if requested is None:
        return None
    options = []
    for field in fields:
        normalized = re.sub(r"[^a-z0-9]", "", field.casefold())
        if not re.search(r"(?:id|key|ref|no)$", normalized):
            continue
        score = _entity_key_score(requested, field)
        if score:
            options.append((score, field))
    if not options:
        return None
    best = max(score for score, _ in options)
    winners = [field for score, field in options if score == best]
    return winners[0] if len(winners) == 1 else None


def _requested_entity(question: str) -> str | None:
    q = str(question).casefold()
    ordered = ("receipt", "invoice", "transaction", "bill", "order", "customer", "product", "patient", "supplier", "vendor")
    return next((term for term in ordered if re.search(rf"\b{term}s?\b", q)), None)


def _entity_key_score(requested: str, field: Any) -> int:
    aliases = {
        "receipt": ("receipt", "transaction", "invoice", "bill", "order"),
        "invoice": ("invoice", "receipt", "transaction", "bill"),
        "transaction": ("transaction", "invoice", "receipt", "bill"),
        "bill": ("bill", "invoice", "receipt", "transaction"),
        "order": ("order", "purchase", "transaction"),
        "customer": ("customer", "client", "patient"),
        "product": ("product", "item", "medicine", "drug", "sku"),
        "patient": ("patient", "customer", "client"),
        "supplier": ("supplier", "vendor", "distributor"),
        "vendor": ("vendor", "supplier", "distributor"),
    }
    normalized = re.sub(r"[^a-z0-9]", "", str(field).casefold())
    core = normalized.removeprefix("extra")
    if not re.search(r"(?:id|key|ref|no)$", core):
        return 0
    choices = aliases.get(requested, ())
    match = next((len(choices) - i for i, alias in enumerate(choices) if alias in core), 0)
    if match and normalized.startswith("extra"):
        match -= 2
    return max(0, match)


def _filter_is_grounded(condition: dict[str, Any], question: str) -> bool:
    """Reject model-added row selections that the user never requested."""
    value = condition.get("value")
    if isinstance(value, (dict, list)) or value is None:
        return False
    candidate = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    request = re.sub(r"[^a-z0-9]", "", str(question).casefold())
    if not candidate:
        return False
    if candidate in request:
        return True
    # Permit singular/plural spelling normalization (e.g. sale/sales).
    return len(candidate) > 3 and candidate.endswith("s") and candidate[:-1] in request


def _infer_source_scope(question: str, frame: pd.DataFrame):
    """Apply an explicit source-role noun to low-cardinality provenance fields."""
    q = str(question).casefold()
    scopes = (
        ("sales", r"\b(sales?|sold|selling)\b", r"sales?|sale|sold|selling|invoice|receipt"),
        ("purchase", r"\b(purchases?|purchased|buying|bought)\b", r"purchases?|purchase|supplier|vendor"),
        ("inventory", r"\b(inventory|stock|on[- ]hand)\b", r"inventory|stock|warehouse|batch"),
    )
    selected = next(((name, pattern) for name, intent, pattern in scopes if re.search(intent, q)), None)
    if selected is None:
        return None
    scope_name, value_pattern = selected
    candidates = []
    for column in frame.columns:
        name = re.sub(r"[^a-z0-9]", "", str(column).casefold())
        if not re.search(r"group|database|scope|(?:txn|transaction|record|source|table|file).*(?:type|name|file|group)?", name):
            continue
        series = frame[column].fillna("").astype(str)
        unique = series.nunique(dropna=True)
        if unique < 2 or unique > 100:
            continue
        matching = series.str.contains(value_pattern, case=False, regex=True)
        if matching.any() and (~matching).any():
            priority = 5 if "group" in name or "database" in name else 4 if "type" in name else 3
            candidates.append((priority, column, matching))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    tied = [item for item in candidates if item[0] == candidates[0][0]]
    if len(tied) > 1 and any(not tied[0][2].equals(item[2]) for item in tied[1:]):
        # Multiple equally strong provenance dimensions may encode different
        # grains. Leave the frame intact rather than choose one silently.
        return None
    _, column, mask = candidates[0]
    return column, mask


def _infer_detail_field(fields: dict[str, Any]) -> str | None:
    candidates = []
    for field in fields:
        normalized = re.sub(r"[^a-z0-9]", "", field.casefold())
        core = normalized.removeprefix("extra")
        match = re.search(r"(?:product|item|medicine|drug|sku)(id|name|code)?$", core)
        if match:
            quality = {"id": 5, "code": 4, "name": 3, None: 2}[match.group(1)]
            if normalized.startswith("extra"):
                quality -= 2
            candidates.append((quality, field))
    if not candidates:
        return None
    best = max(score for score, _ in candidates)
    winners = [field for score, field in candidates if score == best]
    return winners[0] if len(winners) == 1 else None


def _parse_group_size_condition(question: str) -> dict[str, Any] | None:
    """Parse an explicit threshold and number from a natural language group condition."""
    q = re.sub(r"[-_]", " ", str(question).casefold())
    comparisons = (
        (r"\b(?:exactly|equal to|equals?)\s+", "eq"),
        (r"\b(?:at least|no fewer than|not less than)\s+", "gte"),
        (r"\b(?:at most|no more than|not greater than)\s+", "lte"),
        (r"\b(?:more than|greater than|over|above)\s+", "gt"),
        (r"\b(?:fewer than|less than|under|below)\s+", "lt"),
    )
    op, tail = None, None
    for pattern, candidate_op in comparisons:
        match = re.search(pattern, q)
        if match:
            op, tail = candidate_op, q[match.end():]
            break
    if op is None or tail is None:
        return None
    number_words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }
    match = re.match(r"\s*(\d+(?:\.\d+)?|[a-z]+(?:\s+[a-z]+)?)\b", tail)
    if not match:
        return None
    token = match.group(1).strip()
    try:
        value = float(token)
    except ValueError:
        pieces = token.split()
        if pieces[0] not in number_words:
            return None
        value = number_words[pieces[0]]
        if len(pieces) > 1 and pieces[1] in number_words and value >= 20:
            value += number_words[pieces[1]]
    return {"aggregate": "count_rows", "op": op, "value": value}


def _scalar(value):
    if hasattr(value, "item"):
        try: return value.item()
        except (ValueError, AttributeError): pass
    return value


def _citations(rows: pd.DataFrame) -> list[Any]:
    file_col = next((c for c in ("source_file", "file_id", "_header_source_file") if c in rows), None)
    row_col = next((c for c in ("source_row", "_header_source_row") if c in rows), None)
    if file_col and row_col:
        return list(dict.fromkeys((str(r[file_col]), r[row_col]) for _, r in rows.iterrows() if pd.notna(r[row_col])))
    if row_col:
        return rows[row_col].dropna().tolist()
    return rows.index.tolist()
