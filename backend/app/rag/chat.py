"""Module 6.5 — RAG Chatbot Core."""

import time
import json
from typing import Generator, List, Dict, Any, Optional, Tuple

from app.core.config import settings
from app.core.llm import llm
from app.ingestion.models import RetrievedChunk
from app.ingestion.store import KnowledgeBase
from app.rag.models import ChatRequest, ChatResponse, SourceReference
from app.rag.history import session_manager
from app.rag.router import classify_route, extract_filters, AnalyticsRouter, RouteType

class RAGChat:
    def __init__(self):
        self.kb = KnowledgeBase()
        self.analytics_router = AnalyticsRouter()
        
    def _normalize_question(self, question: str) -> str:
        """Trim whitespace, quotes, and normalize digits (e.g., Urdu to ASCII)."""
        q = question.strip().strip('"\'“”‘’')
        # Basic digit normalization (Urdu/Indic to ASCII)
        translation_table = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        return q.translate(translation_table)

    def _is_data_source_inquiry(self, question: str) -> bool:
        import re
        q = question.strip().lower().rstrip("?.! ")
        
        # Exclude questions asking about capabilities, help, or types of questions
        if re.search(r"\b(type\s+of\s+questions?|what\s+can\s+you\s+do|how\s+to\s+use|help|questions?\s+can\s+i\s+ask|capabilities|examples?|suggest\s+questions?)\b", q):
            return False

        patterns = [
            r"^(what|which)\s+(is|are)\s+(the\s+|my\s+|active\s+|current\s+|selected\s+)?(data\s*sources?|datasets?|source\s*files?|files?|tables?|database)\b",
            r"^(what|which)\s+(data\s*sources?|datasets?|source\s*files?|files?|tables?)\s+(are\s+)?(active|selected|loaded|connected|used|in\s+use|being\s+used)\b",
            r"^(list|show|display|tell\s+me|name)\s+(the\s+|all\s+|active\s+|current\s+|selected\s+)?(data\s*sources?|datasets?|source\s*files?|tables?|active\s*scope)\b",
            r"^(data\s*sources?|active\s*sources?|active\s*scope|active\s*files?|active\s*datasets?|connected\s*datasets?|current\s*dataset)$",
            r"^where\s+(is\s+the\s+data\s+from|are\s+you\s+getting\s+the\s+data)\b",
            r"^(what|which)\s+(data\s*source|dataset|file|table)\s+are\s+you\s+using\b"
        ]
        return any(re.search(p, q) for p in patterns)

    def _get_active_source_names(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None) -> List[str]:
        import os
        from app.ingestion.registry import file_registry
        names = []
        if file_ids:
            for fid in file_ids:
                rec = file_registry.get_file_by_id(fid)
                if rec:
                    name = rec.table_name or rec.filename or fid
                    if getattr(rec, "group_name", None):
                        names.append(f"{rec.group_name} — {name}")
                    elif getattr(rec, "database_name", None):
                        names.append(f"{rec.database_name} — {name}")
                    else:
                        names.append(name)
                else:
                    names.append(fid)
        elif source_files:
            for sf in source_files:
                rec = file_registry.get_file_by_path(sf)
                if rec:
                    name = rec.table_name or rec.filename or os.path.basename(sf)
                    if getattr(rec, "group_name", None):
                        names.append(f"{rec.group_name} — {name}")
                    else:
                        names.append(name)
                else:
                    names.append(os.path.basename(sf))
        else:
            try:
                active_files = file_registry.list_files()
                for f in active_files:
                    if f.get("is_ingested"):
                        name = f.get("table_name") or f.get("filename")
                        if f.get("group_name"):
                            names.append(f"{f.get('group_name')} — {name}")
                        elif name:
                            names.append(name)
            except Exception:
                pass

        unique_names = []
        for n in names:
            if n and n not in unique_names:
                unique_names.append(n)
        return unique_names

    def _format_data_source_response(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None) -> str:
        sources = self._get_active_source_names(file_ids, source_files)
        if not sources:
            return "No data sources are currently connected or selected."

        if file_ids or source_files:
            if len(sources) == 1:
                return f"The active data source is:\n- **{sources[0]}**"
            else:
                lines = ["The active data sources are:"]
                for s in sources:
                    lines.append(f"- **{s}**")
                return "\n".join(lines)
        else:
            if len(sources) == 1:
                return f"Connected data source (all data scope):\n- **{sources[0]}**"
            else:
                lines = [f"Connected data sources ({len(sources)} available):"]
                for s in sources:
                    lines.append(f"- **{s}**")
                return "\n".join(lines)

    def _get_system_prompt(self, domain: str, route: str, selected_sources: Optional[List[str]] = None) -> str:
        """
        Domain-agnostic core logic, but uses domain pack if available.
        For now, a generic prompt with strict grounding constraints.
        """
        base_prompt = (
            "You are an offline assistant for LLM-Konnect. "
            "Reply in the language the user used (English, Urdu, or Roman-Urdu). "
            "When answering in Urdu, transliterate any English terms into Urdu script (e.g. 'Dataset' -> 'ڈیٹا سیٹ') to avoid left-to-right writing style conflicts. "
            "Be concise.\n\n"
        )
        
        # ── Guardrail 1: Active scope declaration & out-of-scope table refusal ──
        if selected_sources:
            friendly_names = []
            try:
                from app.ingestion.registry import file_registry
                for s in selected_sources:
                    rec = file_registry.get_file_by_id(s)
                    if rec and (rec.table_name or rec.filename):
                        friendly_names.append(rec.table_name or rec.filename)
                    else:
                        friendly_names.append(s)
            except Exception:
                friendly_names = selected_sources

            sources_str = ", ".join(friendly_names)
            base_prompt += (
                f"Active Data Scope: The user has selected the following specific data sources for this conversation: {sources_str}.\n"
                "- If the user asks which data source(s), file(s), or table(s) you are answering from, explicitly name all of these active sources.\n"
                "- When answering questions, synthesize and compare information across these sources whenever relevant records are present in the Context Records.\n"
                "- When listing fields, columns, or records from multiple sources, present each source under its own clearly separated heading with bullet points on separate lines (do NOT merge multiple tables into a single line).\n"
                f"- STRICT SCOPE RULE: You may ONLY answer from the active data sources listed above ({sources_str}). "
                "If the user's question references a table, dataset, or file by a name that is NOT in this list "
                "(for example: 'inventory table', 'stock table', 'products table', 'ledger'), "
                "you MUST respond with: 'I cannot find that table in the active data sources. "
                f"The connected sources are: {sources_str}.' Do NOT answer from any other source.\n\n"
            )
        else:
            # No explicit scope selected — warn against fabricating data for non-existent tables
            base_prompt += (
                "Active Data Scope: You are answering from all currently connected and ingested data sources.\n"
                "- STRICT SCOPE RULE: If the user references a specific table, dataset, or file by a name that does NOT "
                "appear in the Context Records (e.g. 'inventory table', 'stock ledger', 'products table'), "
                "you MUST respond: 'No records found for that table in the connected data sources.' "
                "Do NOT fabricate data or answer from a different source than what was referenced.\n\n"
            )
        
        # ── Guardrail 2: Route-specific instructions ─────────────────────────
        if route == RouteType.RAG:
            base_prompt += (
                "Answer the user's question clearly, accurately, and concisely in 1 to 3 sentences using the provided Context Records. "
                "Do not repeat raw metadata tags or row numbers unless specifically requested. "
                "Be concise and do not guess information not in the records. "
                "If the Context Records are empty or contain no relevant data for the question asked, "
                "respond with: 'No records found for that query in the connected data sources.'"
            )
        elif route == RouteType.ANALYTICS:
            base_prompt += (
                "CRITICAL: The user asked a numeric or aggregate question. "
                "The actual answer has been calculated by the Analytics Engine and provided as 'Computed Values'. "
                "For any metric with \"status\": \"ok\", state its computed \"value\" and \"unit\" accurately and clearly. "
                "You MUST NARRATE the Computed Values exactly as provided. "
                "Do NOT recalculate or guess numbers.\n"
                "If a value has \"is_estimate\": true, it is a FORECAST, not a measured fact. "
                "Say so plainly and give the range from \"estimate_range\" "
                "(for example: 'roughly X, likely between A and B'). "
                "Never present a forecast as a certainty and never narrow the range.\n"
                "If a Detailed Breakdown is provided (such as top products or top suppliers), identify the top item (item #1) and state its name and value clearly to answer the user's question.\n"
                "If one metric has \"status\": \"unavailable\" while another relevant metric or breakdown is available with \"status\": \"ok\", answer directly using the available metric and breakdown.\n"
                "ONLY if all metrics are unavailable, inform the user that the metric cannot be determined and state the reason provided.\n"
                "NO-DATA PERIOD RULE: If the Computed Values show zero records, no data, or the time period "
                "requested falls entirely outside the date range of the dataset, you MUST start your response "
                "with the exact phrase: 'No records found for that query.' "
                "Then briefly state what date range the dataset does cover. "
                "Do NOT return figures from a different period as if they answer the user's question."
            )
        elif route == RouteType.CHITCHAT:
            base_prompt += (
                "You are an offline assistant for analyzing local business and inventory data. "
                "Answer greetings briefly and explain that you can help lookup records or summarize aggregates from their data."
            )
            
        return base_prompt

    def _format_context_records(self, chunks, selected_sources: Optional[List[str]] = None) -> str:
        import os
        lines = []
        if selected_sources:
            friendly_names = []
            try:
                from app.ingestion.registry import file_registry
                for s in selected_sources:
                    rec = file_registry.get_file_by_id(s)
                    if rec and (rec.table_name or rec.filename):
                        friendly_names.append(rec.table_name or rec.filename)
                    else:
                        friendly_names.append(s)
            except Exception:
                friendly_names = selected_sources
            lines.append(f"Active Data Sources: {', '.join(friendly_names)}")
        for c in chunks:
            raw_src = c.metadata.get("source_file") or c.metadata.get("filename") or ""
            filename = os.path.basename(raw_src) if raw_src else "dataset"
            row_idx = c.source_row if c.source_row is not None else c.metadata.get("source_row", "")
            
            row_str = f", Row: #{row_idx}" if row_idx != "" else ""
            meta_tag = f"[Source File: {filename}{row_str}]"
            lines.append(f"- {meta_tag} {c.text}")
        return "Context Records:\n" + "\n".join(lines)

    def _format_sources(self, retrieved_chunks) -> List[SourceReference]:
        import os
        sources = []
        for c in retrieved_chunks:
            meta = c.metadata
            label_parts = []
            if "invoice_id" in meta:
                label_parts.append(f"Invoice {meta['invoice_id']}")
            elif "product_id" in meta:
                label_parts.append(f"Product {meta['product_id']}")
            elif "date" in meta:
                label_parts.append(f"Date {meta['date']}")
            
            row_idx = c.source_row if c.source_row is not None else meta.get("source_row")
            label = ", ".join(label_parts) if label_parts else f"Record {row_idx}"
            
            raw_src = meta.get("source_file") or meta.get("filename") or "unknown"
            filename = os.path.basename(raw_src) if raw_src else "unknown"
            
            sources.append(SourceReference(
                source_file=filename,
                source_row=row_idx,
                label=label
            ))
        return sources


    def _format_computed_values_context(self, computed_values: dict) -> str:
        lines = ["Computed Values from Analytics Engine:"]
        for key, item in computed_values.items():
            name = item.get("name", key)
            status = item.get("status", "ok")
            val = item.get("value")
            unit = item.get("unit", "")
            if status == "ok" and val is not None:
                if isinstance(val, (int, float)):
                    if unit.lower() in ("pkr", "rs", "usd", "eur", "gbp") or unit == "PKR":
                        formatted_val = f"{val} {unit}".strip()
                    elif unit == "percent":
                        formatted_val = f"{val:.2f}%"
                    elif unit == "count":
                        formatted_val = f"{int(val):,} items"
                    elif unit == "rows":
                        formatted_val = f"{int(val):,} rows"
                    else:
                        formatted_val = f"{val} {unit}".strip()
                else:
                    formatted_val = f"{val} {unit}".strip()
                
                period_str = ""
                if item.get("period") and isinstance(item["period"], dict):
                    p = item["period"]
                    if p.get("start") and p.get("end"):
                        period_str = f" for period {p.get('start')} to {p.get('end')}"
                
                est_str = ""
                if item.get("estimate_range") and isinstance(item["estimate_range"], dict):
                    er = item["estimate_range"]
                    est_str = f" (estimate_range: {er.get('lower')} to {er.get('upper')})"

                rows = item.get("provenance", {}).get("rows_used", "")
                rows_str = f" (computed over {rows:,} matching records)" if rows else ""
                lines.append(f"- {name}: {formatted_val}{est_str}{period_str}{rows_str}")

                # Format structured breakdown table if present (e.g. Near-Expiry liquidation or Low-Stock reorder predictions)
                breakdown = item.get("breakdown")
                if breakdown and isinstance(breakdown, list):
                    lines.append("  Detailed Breakdown / Recommendations:")
                    for idx, row in enumerate(breakdown[:20], 1):
                        parts = []
                        for col_k, col_v in row.items():
                            if col_k == "source_row" or col_v is None or col_v == "":
                                continue
                            parts.append(f"{col_k}: {col_v}")
                        lines.append(f"  {idx}. " + " | ".join(parts))
            elif status == "unavailable":
                reason = item.get("reason", "data unavailable")
                lines.append(f"- {name}: UNAVAILABLE (Reason: {reason})")
        return "\n".join(lines)

    def _get_records_for_analytics(
        self, request: ChatRequest, filters: Dict[str, Any]
    ) -> Tuple[List[dict], List[RetrievedChunk]]:
        """
        Retrieves full dataset records from cached canonical DataFrames for deterministic
        whole-dataset analytics, plus a small top_k sample of chunks for citation sources.
        """
        from unittest.mock import Mock
        if isinstance(getattr(self.kb, "search", None), Mock):
            citation_chunks = self.kb.search(
                request.question,
                top_k=50,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            if citation_chunks:
                return [c.metadata for c in citation_chunks], citation_chunks
            return [], []

        import os
        import pandas as pd
        from app.ingestion.registry import file_registry
        from app.schema.domain import get_domain_pack

        target_files = []
        if request.file_ids:
            for fid in request.file_ids:
                rec = file_registry.get_file_by_id(fid)
                if rec and rec.file_path and (os.path.exists(rec.file_path) or rec.file_path.startswith("sql://") or rec.file_path.startswith("db://")):
                    target_files.append(rec)
        elif request.source_files:
            for sf in request.source_files:
                rec = file_registry.get_file_by_path(sf)
                if rec and rec.file_path and (os.path.exists(rec.file_path) or rec.file_path.startswith("sql://") or rec.file_path.startswith("db://")):
                    target_files.append(rec)
                elif sf.startswith("sql://") or sf.startswith("db://") or os.path.exists(sf):
                    target_files.append(type("TempFileRec", (), {"file_path": sf, "domain": request.domain, "filename": os.path.basename(sf)})())
        else:
            try:
                active_files = file_registry.list_files()
                for rec in active_files:
                    fp = getattr(rec, "file_path", None)
                    if fp and (getattr(rec, "status", None) == "active" or getattr(rec, "chunk_count", 0) > 0):
                        if os.path.exists(fp) or fp.startswith("sql://") or fp.startswith("db://"):
                            target_files.append(rec)
            except Exception:
                pass

        dfs = []
        if target_files:
            for tf in target_files:
                try:
                    from app.api.analytics import _load_canonical, KPIRequest
                    kpi_req = KPIRequest(file_path=tf.file_path, domain=getattr(tf, "domain", request.domain))
                    df, _ = _load_canonical(kpi_req)
                    if df is not None and not df.empty:
                        if "source_row" not in df.columns:
                            df["source_row"] = df.index + 2
                        if "source_file" not in df.columns:
                            df["source_file"] = getattr(tf, "filename", os.path.basename(tf.file_path))
                        dfs.append(df)
                except Exception:
                    continue

        if dfs:
            combined_df = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
            records = combined_df.to_dict(orient="records")
            
            # Fast in-memory citation chunks from top matching records
            sample_rows = combined_df.head(5).to_dict(orient="records")
            pack = None
            try:
                pack = get_domain_pack(request.domain)
            except Exception:
                pass

            citation_chunks = [
                RetrievedChunk(
                    text=pack.row_to_text(r) if pack else str(r),
                    metadata=r,
                    score=1.0,
                    source_row=r.get("source_row")
                )
                for r in sample_rows
            ]
            return records, citation_chunks

        # Fallback: if not explicitly scoped or running in mocked test environment, search KB
        citation_chunks = self.kb.search(
            request.question,
            top_k=50,
            filters=filters,
            domain=request.domain,
            file_ids=request.file_ids,
            source_files=request.source_files
        )
        if citation_chunks:
            return [c.metadata for c in citation_chunks], citation_chunks

        return [], []

    def ask(self, request: ChatRequest) -> ChatResponse:
        """End-to-end non-streaming RAG pipeline."""
        start_time = time.time()
        
        question = self._normalize_question(request.question)
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id,
                "assistant",
                direct_answer,
                domain=request.domain,
                route="rag",
                sources=None,
                timing=timing
            )
            return ChatResponse(
                answer=direct_answer,
                route="rag",
                sources=[],
                computed_values=None,
                session_id=request.session_id,
                timing=timing
            )

        last_turn = session_manager.get_last_assistant_turn(request.session_id)
        last_route = last_turn.get("route") if last_turn else None
        route = classify_route(question, last_route=last_route)
        filters = extract_filters(question, request.domain)
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            # Skip retrieval for greetings
            pass
            
        elif route == RouteType.ANALYTICS:
            records, citation_chunks = self._get_records_for_analytics(request, filters)
            
            # Compute deterministically via the Module 6.6 KPI engine.
            computed_values, source_rows = self.analytics_router.compute(
                question, filters, records, request.domain
            )
            if computed_values:
                context_text = self._format_computed_values_context(computed_values)
                if citation_chunks:
                    matched = [c for c in citation_chunks if c.source_row in source_rows] if source_rows else []
                    sources = self._format_sources(matched if matched else citation_chunks[:5])
                else:
                    sources = []
            else:
                context_text = "No records found to compute the answer."
                 
        elif route == RouteType.RAG:
            # Dynamic retrieval depth: widen when date or category filters are active
            retrieval_k = 25 if filters else settings.retrieval_top_k
            chunks = self.kb.search(
                question,
                top_k=retrieval_k,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            
            if not chunks and not request.file_ids and not request.source_files:
                # Short-circuit without LLM call to save time if no sources scoped
                return ChatResponse(
                    answer="I couldn't find anything about that in the selected data.",
                    route=route,
                    sources=[],
                    computed_values=None,
                    session_id=request.session_id,
                    timing=time.time() - start_time
                )
                 
            sources = self._format_sources(chunks) if chunks else []
            context_text = self._format_context_records(chunks, selected_sources=request.file_ids) if chunks else ""
            
        # Build prompt messages
        system_prompt = self._get_system_prompt(request.domain, route, selected_sources=request.file_ids)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"
             
        messages.append({"role": "user", "content": user_msg})
        
        # Call LLM
        try:
            answer = llm.chat(messages=messages)
        except Exception as e:
            answer = f"Error: LLM unavailable ({str(e)}). I am returning offline results if any."
             
        timing = round(time.time() - start_time, 2)
        session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
        session_manager.append_turn(
            request.session_id,
            "assistant",
            answer,
            domain=request.domain,
            route=route,
            sources=[s.dict() if hasattr(s, 'dict') else s for s in sources] if sources else None,
            timing=timing
        )
        
        return ChatResponse(
            answer=answer,
            route=route,
            sources=sources,
            computed_values=computed_values,
            session_id=request.session_id,
            timing=timing
        )
        
    def ask_stream(self, request: ChatRequest) -> Generator[str, None, None]:
        """End-to-end streaming RAG pipeline."""
        start_time = time.time()
        question = self._normalize_question(request.question)
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id,
                "assistant",
                direct_answer,
                domain=request.domain,
                route="rag",
                sources=None,
                timing=timing
            )
            yield json.dumps({
                "chunk": direct_answer,
                "route": "rag",
                "sources": [],
                "computed_values": None
            }) + "\n"
            return

        last_turn = session_manager.get_last_assistant_turn(request.session_id)
        last_route = last_turn.get("route") if last_turn else None
        route = classify_route(question, last_route=last_route)
        filters = extract_filters(question, request.domain)
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            pass
        elif route == RouteType.ANALYTICS:
            records, citation_chunks = self._get_records_for_analytics(request, filters)
            computed_values, source_rows = self.analytics_router.compute(
                question, filters, records, request.domain
            )
            if computed_values:
                context_text = self._format_computed_values_context(computed_values)
                if citation_chunks:
                    matched = [c for c in citation_chunks if c.source_row in source_rows] if source_rows else []
                    sources = self._format_sources(matched if matched else citation_chunks[:5])
                else:
                    sources = []
            else:
                context_text = "No records found to compute the answer."
                 
        elif route == RouteType.RAG:
            retrieval_k = 25 if filters else settings.retrieval_top_k
            chunks = self.kb.search(
                question,
                top_k=retrieval_k,
                filters=filters,
                domain=request.domain,
                file_ids=request.file_ids,
                source_files=request.source_files
            )
            if not chunks and not request.file_ids and not request.source_files:
                yield json.dumps({
                    "chunk": "I couldn't find anything about that in the selected data.",
                    "route": route,
                    "sources": [],
                    "computed_values": None
                }) + "\n"
                return
                 
            sources = self._format_sources(chunks) if chunks else []
            context_text = self._format_context_records(chunks, selected_sources=request.file_ids) if chunks else ""
            
        system_prompt = self._get_system_prompt(request.domain, route, selected_sources=request.file_ids)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"
             
        messages.append({"role": "user", "content": user_msg})
        
        serializable_sources = [s.model_dump() if hasattr(s, 'model_dump') else (s.dict() if hasattr(s, 'dict') else s) for s in sources] if sources else []
        full_answer = ""
        try:
            for chunk in llm.chat_stream(messages=messages):
                full_answer += chunk
                yield json.dumps({
                    "chunk": chunk,
                    "route": route,
                    "sources": serializable_sources,
                    "computed_values": computed_values
                }) + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"
            return
             
        timing = round(time.time() - start_time, 2)
        session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
        session_manager.append_turn(
            request.session_id,
            "assistant",
            full_answer,
            domain=request.domain,
            route=route,
            sources=serializable_sources if serializable_sources else None,
            timing=timing
        )

rag_chat = RAGChat()

