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
        """Trim whitespace and normalize digits (e.g., Urdu to ASCII)."""
        q = question.strip()
        # Basic digit normalization (Urdu/Indic to ASCII)
        translation_table = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        return q.translate(translation_table)

    def _get_system_prompt(self, domain: str, route: str) -> str:
        """
        Domain-agnostic core logic, but uses domain pack if available.
        For now, a generic prompt with strict grounding constraints.
        """
        # Load domain pack for specific vocabulary/rules if needed.
        # But we must keep the core prompt domain-agnostic.
        
        base_prompt = (
            "You are an offline assistant for LLM-Konnect. "
            "Reply in the language the user used (English, Urdu, or Roman-Urdu). "
            "When answering in Urdu, transliterate any English terms into Urdu script (e.g. 'Dataset' -> 'ڈیٹا سیٹ') to avoid left-to-right writing style conflicts. "
            "Be concise.\n\n"
        )
        
        if route == RouteType.RAG:
            base_prompt += (
                "CRITICAL: Answer ONLY using the provided Context Records. "
                "Each record in Context Records specifies its dataset/source filename and row (e.g. [Source File: filename.csv, Row: 12]). "
                "When asked which file, dataset, or source the information comes from, cite these source filenames accurately. "
                "Do NOT use external knowledge. "
                "Do NOT perform mathematical calculations (summing, averaging, counting) on the records. "
                "If the answer requires adding up numbers or is not in the context, explicitly say "
                "'I couldn't find anything about that in your data' or 'I cannot calculate that from the records'."
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
                "ONLY if a metric explicitly has \"status\": \"unavailable\", tell the user that specific metric cannot be determined and state its \"reason\" verbatim."
            )
        elif route == RouteType.CHITCHAT:
            base_prompt += (
                "You are an offline assistant for analyzing local business and inventory data. "
                "Answer greetings briefly and explain that you can help lookup records or summarize aggregates from their data."
            )
            
        return base_prompt

    def _format_context_records(self, chunks) -> str:
        import os
        lines = []
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
                        formatted_val = f"{val:,.2f} {unit}"
                    elif unit == "percent":
                        formatted_val = f"{val:.2f}%"
                    elif unit == "count":
                        formatted_val = f"{int(val):,} transactions"
                    elif unit == "rows":
                        formatted_val = f"{int(val):,} rows"
                    else:
                        formatted_val = f"{int(val):,} {unit}".strip() if isinstance(val, int) or val.is_integer() else f"{val:,.2f} {unit}".strip()
                else:
                    formatted_val = f"{val} {unit}".strip()
                
                period_str = ""
                if item.get("period") and isinstance(item["period"], dict):
                    p = item["period"]
                    if p.get("start") and p.get("end"):
                        period_str = f" for period {p.get('start')} to {p.get('end')}"
                
                rows = item.get("provenance", {}).get("rows_used", "")
                rows_str = f" (computed over {rows:,} matching records)" if rows else ""
                lines.append(f"- {name}: {formatted_val}{period_str}{rows_str}")
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
        import os
        import pandas as pd
        from app.ingestion.registry import file_registry
        from app.schema.domain import get_domain_pack

        target_files = []
        if request.file_ids:
            for fid in request.file_ids:
                rec = file_registry.get_file_by_id(fid)
                if rec and rec.file_path and os.path.exists(rec.file_path):
                    target_files.append(rec)
        elif request.source_files:
            for sf in request.source_files:
                rec = file_registry.get_file_by_path(sf)
                if rec and rec.file_path and os.path.exists(rec.file_path):
                    target_files.append(rec)
                elif os.path.exists(sf):
                    target_files.append(type("TempFileRec", (), {"file_path": sf, "domain": request.domain, "filename": os.path.basename(sf)})())

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
            
            if not chunks:
                # Short-circuit without LLM call to save time
                return ChatResponse(
                    answer="I couldn't find anything about that in the selected data.",
                    route=route,
                    sources=[],
                    computed_values=None,
                    session_id=request.session_id,
                    timing=time.time() - start_time
                )
                 
            sources = self._format_sources(chunks)
            context_text = self._format_context_records(chunks)
            
        # Build prompt messages
        system_prompt = self._get_system_prompt(request.domain, route)
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
            if not chunks:
                yield json.dumps({
                    "chunk": "I couldn't find anything about that in the selected data.",
                    "route": route,
                    "sources": [],
                    "computed_values": None
                }) + "\n"
                return
                 
            sources = self._format_sources(chunks)
            context_text = self._format_context_records(chunks)
            
        system_prompt = self._get_system_prompt(request.domain, route)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"
             
        messages.append({"role": "user", "content": user_msg})
        
        full_answer = ""
        try:
            for chunk in llm.chat_stream(messages=messages):
                full_answer += chunk
                yield json.dumps({
                    "chunk": chunk,
                    "route": route,
                    "sources": [s.dict() for s in sources],
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
            sources=[s.dict() if hasattr(s, 'dict') else s for s in sources] if sources else None,
            timing=timing
        )

rag_chat = RAGChat()

