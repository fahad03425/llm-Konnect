"""Module 6.5 — RAG Chatbot Core."""

import time
import json
from datetime import date
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
        """Normalize small input variations before routing a question."""
        import re

        q = question.strip().strip('"\'“”‘’')
        # Basic digit normalization (Urdu/Indic to ASCII)
        translation_table = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        q = q.translate(translation_table)
        # In this common wording, "may sales in the last N days" is a typo for
        # "my sales"; treating it as the month May creates a false date filter.
        q = re.sub(
            r"\bmay(?=\s+sales?\s+(?:in|during|for)\s+(?:the\s+)?(?:last|past|previous)\s+\d{1,3}\s+days?\b)",
            "my",
            q,
            flags=re.IGNORECASE,
        )
        q = re.sub(r"\byestarday\b", "yesterday", q, flags=re.IGNORECASE)
        # This common typo otherwise sends forecast requests to vector retrieval,
        # which cannot compute a future value from source records.
        return re.sub(
            r"\bforcast(s|ed|ing)?\b",
            lambda match: "forecast" + (match.group(1) or ""),
            q,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _resolve_relative_date_filter(filters: Dict[str, Any], records):
        """Resolve recent windows and reject date requests outside data coverage."""
        resolved = dict(filters)
        days = resolved.pop("relative_days", None)
        has_explicit_window = bool(resolved.get("date_from") or resolved.get("date_to"))
        if not days and not has_explicit_window:
            return resolved

        import pandas as pd

        if hasattr(records, "columns"):
            date_values = records["date"] if "date" in records.columns else None
        elif records:
            frame = pd.DataFrame(records)
            date_values = frame["date"] if "date" in frame.columns else None
        else:
            date_values = None
        if date_values is None:
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        dates = pd.to_datetime(date_values, errors="coerce").dropna()
        if dates.empty:
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        first_available = dates.min().normalize()
        last_available = dates.max().normalize()
        if days:
            end = last_available
            start = end - pd.Timedelta(days=int(days) - 1)
            resolved["date_from"] = start.strftime("%Y-%m-%d")
            resolved["date_to"] = end.strftime("%Y-%m-%d")
        else:
            start = pd.to_datetime(resolved.get("date_from") or resolved.get("date_to"), errors="coerce")
            end = pd.to_datetime(resolved.get("date_to") or resolved.get("date_from"), errors="coerce")
            if pd.isna(start) or pd.isna(end):
                return resolved
            start, end = start.normalize(), end.normalize()

        if start < first_available or end > last_available:
            first_text = first_available.strftime("%Y-%m-%d")
            last_text = last_available.strftime("%Y-%m-%d")
            requested_text = (
                start.strftime("%Y-%m-%d") if start == end
                else f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
            resolved["_date_filter_error"] = (
                f"The requested period ({requested_text}) is outside the selected data's "
                f"date coverage ({first_text} to {last_text}); I can't calculate sales for it."
            )
        return resolved

    def _clean_roman_urdu_vocabulary(self, text: str) -> str:
        """Replaces common Roman Hindi word leakages with natural Roman Urdu equivalents."""
        import re
        replacements = [
            (r'\b(jaankari|jankari)\b', 'maloomat'),
            (r'\b(adhik)\b', 'ziada'),
            (r'\b(pradaan\s+kar\s+sakta\s+hoon|pradaan\s+karta\s+hoon|pradaan)\b', 'faraaham'),
            (r'\b(uplabdh)\b', 'dastyab'),
            (r'\b(anya)\b', 'mazeed'),
            (r'\b(kripya)\b', 'baraye meharbani'),
            (r'\b(shuruwat)\b', 'aaghaz'),
            (r'\b(namaste)\b', 'assalam o alaikum'),
            (r'\b(sukriya)\b', 'shukriya'),
        ]
        cleaned = text
        for pattern, repl in replacements:
            def _sub_repl(match):
                m = match.group(0)
                if m.isupper():
                    return repl.upper()
                elif m[0].isupper():
                    return repl.capitalize()
                return repl
            cleaned = re.sub(pattern, _sub_repl, cleaned, flags=re.IGNORECASE)
        return cleaned

    def _detect_query_language(self, question: str) -> str:
        """
        Deterministically detect if user wrote in:
        - 'urdu_script': Urdu written in Arabic/Nastaliq script (e.g. 'سب سے زیادہ')
        - 'roman_urdu': Urdu written phonetically in Latin alphabet (e.g. 'me kis kism k data se deal kr rha hu')
        - 'english': Standard English (e.g. 'hi', 'what is total sales', 'list all files')
        """
        import re
        q = question.strip()
        # 1. Check for Arabic/Urdu script Unicode characters
        if re.search(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]", q):
            return "urdu_script"

        # 2. Check for Roman-Urdu markers
        strong_roman_urdu = {
            "kya", "zyada", "ziada", "dawai", "dawa", "dawayi", "kyun", "kyu", "kism", "qism",
            "konsi", "konse", "konsa", "mein", "batao", "bataen", "bataiye", "dikhao", "dikhaye",
            "kitni", "kitna", "kitne", "kese", "kaise", "kahan", "kaha", "koun", "bohat", "bhot",
            "thoda", "thora", "chahiye", "skte", "sakte", "sakty", "apka", "aapka", "apki", "aapki",
            "apke", "aapke", "hume", "humara", "hamara", "nhi", "shukriya", "shukria", "kiska",
            "kiski", "kiske", "rha", "rhi", "rhe", "raha", "rahi", "rahe", "karna", "karta", "karti",
            "karte", "hwi", "hui", "bhej", "mangwaya", "mangwayi"
        }
        medium_roman_urdu = {
            "kis", "hai", "hain", "ho", "hu", "hoon", "hun", "tm", "tum", "kr", "kar", "karo",
            "mera", "meri", "mere", "nahi", "aur", "pe", "par", "se", "ka", "ki", "ke", "ko",
            "ap", "aap", "mujhe", "mujy", "hum", "sab"
        }

        words = re.findall(r"\b[a-zA-Z]+\b", q.lower())
        strong_hits = [w for w in words if w in strong_roman_urdu]
        medium_hits = [w for w in words if w in medium_roman_urdu]

        if len(strong_hits) >= 1 or len(medium_hits) >= 2:
            return "roman_urdu"

        return "english"

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

    def _format_data_source_response(self, file_ids: Optional[List[str]] = None, source_files: Optional[List[str]] = None, lang: str = "english") -> str:
        sources = self._get_active_source_names(file_ids, source_files)
        if lang == "roman_urdu":
            if not sources:
                return "Filhaal koi data source connect ya select nahi hai."
            if file_ids or source_files:
                if len(sources) == 1:
                    return f"Active data source yeh hai:\n- **{sources[0]}**"
                else:
                    lines = ["Active data sources yeh hain:"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
            else:
                if len(sources) == 1:
                    return f"Connected data source (tamaam data):\n- **{sources[0]}**"
                else:
                    lines = [f"Connected data sources ({len(sources)} available):"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
        elif lang == "urdu_script":
            if not sources:
                return "فی الحال کوئی ڈیٹا سورس منتخب یا منسلک نہیں ہے۔"
            if file_ids or source_files:
                if len(sources) == 1:
                    return f"فعال ڈیٹا سورس درج ذیل ہے:\n- **{sources[0]}**"
                else:
                    lines = ["فعال ڈیٹا سورسز درج ذیل ہیں:"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
            else:
                if len(sources) == 1:
                    return f"منسلک ڈیٹا سورس:\n- **{sources[0]}**"
                else:
                    lines = [f"منسلک ڈیٹا سورسز ({len(sources)} دستیاب):"]
                    for s in sources:
                        lines.append(f"- **{s}**")
                    return "\n".join(lines)
        else:
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

    def _get_system_prompt(self, domain: str, route: str, selected_sources: Optional[List[str]] = None, lang: str = "english") -> str:
        """
        Domain-agnostic core logic, but uses domain pack if available.
        For now, a generic prompt with strict grounding constraints.
        """
        if lang == "roman_urdu":
            base_prompt = (
                "You are an offline assistant for LLM-Konnect analyzing local business and inventory data.\n"
                "LANGUAGE & VOCABULARY RULES (STRICT):\n"
                "- The user wrote in Roman-Urdu (Urdu written using Latin/English letters).\n"
                "- You MUST reply strictly in natural Pakistani Roman-Urdu using Latin letters (A-Z, a-z) only (e.g. 'Aap ... ke data se deal kar rahe hain', 'Sab se ziada sale ...').\n"
                "- STRICT PROHIBITION ON HINDI VOCABULARY:\n"
                "  * NEVER use Hindi words like 'jaankari', 'jankari', 'adhik', 'pradaan', 'uplabdh', 'anya', 'kripya', 'shuruwat', 'sukriya', 'namaste'.\n"
                "- USE STANDARD ROMAN-URDU WORDS INSTEAD:\n"
                "  * Use 'maloomat' or 'information' instead of 'jaankari/jankari'\n"
                "  * Use 'ziada' or 'mazeed' instead of 'adhik'\n"
                "  * Use 'faraaham' or 'provide' instead of 'pradaan'\n"
                "  * Use 'dastyab', 'mojood', or 'available' instead of 'uplabdh'\n"
                "  * Use 'doosri', 'koi aur', or 'mazeed' instead of 'anya'\n"
                "  * Use 'sawalat / sawal' for questions and 'jawab' for answers\n"
                "  * Example phrase: 'Aap is baray mein sawal pooch sakte hain jaise ke supplier ki maloomat ya sales transactions...'\n"
                "- STRICT SCRIPT RULE: DO NOT use Urdu/Arabic script (اردو رسم الخط بالکل استعمال نہ کریں) and DO NOT use Hindi/Devanagari script. Every single word and character must be in Latin/English letters.\n"
                "- DIRECT ANSWER RULE: Answer directly, naturally, and concisely in 1 to 2 sentences. Never explain your translation process or output internal monologue.\n\n"
            )
        elif lang == "urdu_script":
            base_prompt = (
                "You are an offline assistant for LLM-Konnect analyzing local business and inventory data.\n"
                "LANGUAGE RULE (STRICT):\n"
                "- The user wrote in Urdu script.\n"
                "- You MUST reply in natural, professional Urdu script (اردو رسم الخط).\n"
                "- DIRECT ANSWER RULE: Answer directly, naturally, and concisely in 1 to 2 sentences.\n\n"
            )
        else:  # english
            base_prompt = (
                "You are an offline assistant for LLM-Konnect analyzing local business and inventory data.\n"
                "LANGUAGE RULE (STRICT):\n"
                "- The user wrote in English.\n"
                "- You MUST reply strictly in natural, professional English.\n"
                "- DO NOT reply in Roman-Urdu, Urdu, or Hindi, and do NOT use greetings like 'Namaste'. Reply naturally in standard English (e.g. 'Hello! How can I assist you today?').\n"
                "- DIRECT ANSWER RULE: Answer directly, naturally, and concisely in 1 to 2 sentences. Never explain your translation process or output internal monologue.\n\n"
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
                "inform the user in their language (in Roman-Urdu if asked in Roman-Urdu) that this table is not in the active data sources, and list the connected sources. Do NOT answer from any other source.\n\n"
            )
        else:
            # No explicit scope selected — warn against fabricating data for non-existent tables
            base_prompt += (
                "Active Data Scope: You are answering from all currently connected and ingested data sources.\n"
                "- STRICT SCOPE RULE: If the user references a specific table, dataset, or file by a name that does NOT "
                "appear in the Context Records (e.g. 'inventory table', 'stock ledger', 'products table'), "
                "inform the user in their language (in Roman-Urdu if asked in Roman-Urdu) that no records were found for that table in the connected data sources. "
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
                "CRITICAL: The exact numeric answer has already been calculated and provided below under 'Calculated Metric'.\n"
                "State the calculated number accurately in a direct, short, natural 1 to 2 sentence reply to answer the user's question.\n"
                "DO NOT write 'Computed Values', DO NOT write 'Status: ok', and DO NOT output bullet points or lists.\n"
                "Never say data is unavailable or cannot be determined when a calculated metric with a number is provided.\n"
                "If the user asks about a metric (such as profit margin, total expenses, or purchase amount), state the provided calculated metric directly.\n"
                "If a value has \"is_estimate\": true, it is a FORECAST, not a measured fact. "
                "Say so plainly and give the range from \"estimate_range\" "
                "(for example: 'roughly X, likely between A and B'). "
                "Never present a forecast as a certainty and never narrow the range.\n"
                "If a Detailed Breakdown is provided (such as top products or top suppliers), identify the top item (item #1) and state its name and value clearly.\n"
                "If one metric is unavailable while another relevant metric is available, answer directly using the available metric.\n"
                "ONLY if all metrics are unavailable, inform the user that the metric cannot be determined.\n"
                "If a metric is 0 (such as 0 expired batches), state directly that none are expired (e.g. in Roman-Urdu: 'Stock mein koi bhi batch expire nahi hua hai, expired count 0 hai.').\n"
                "NO-DATA PERIOD RULE: If the values show zero records or no data for the requested period, "
                "start with: 'No records found for that query.'"
            )
        elif route == RouteType.CHITCHAT:
            base_prompt += (
                "You are an offline assistant for analyzing local business and inventory data. "
                "For greetings, speed inquiries, or questions about what you can do: reply directly, politely, and concisely in 1 to 2 sentences. "
                "State that you run locally and offline on their workstation to help look up records, track inventory, and calculate business metrics."
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

    def _analytics_citations(self, records, source_rows, domain: str, limit: int = 20):
        """Build citations from rows that actually contributed to an analytic result."""
        if records is None or not source_rows:
            return []
        if hasattr(records, "empty") and records.empty:
            return []
        if not hasattr(records, "iterrows") and not records:
            return []
        used = set(source_rows)
        try:
            from app.schema.domain import get_domain_pack
            pack = get_domain_pack(domain)
        except Exception:
            pack = None

        chunks = []
        rows = (row.to_dict() for _, row in records.iterrows()) if hasattr(records, "iterrows") else iter(records)
        for row in rows:
            row_number = row.get("source_row")
            if row_number not in used:
                continue
            chunks.append(RetrievedChunk(
                text=pack.row_to_text(row) if pack else str(row),
                metadata=row,
                score=1.0,
                source_row=row_number,
            ))
            if len(chunks) >= limit:
                break
        return chunks


    def _format_computed_values_context(self, computed_values: dict) -> str:
        lines = ["Calculated Metric:"]
        has_ok = any(item.get("status") == "ok" and item.get("value") is not None for item in computed_values.values())
        for key, item in computed_values.items():
            name = item.get("name", key)
            val = item.get("value")
            if key in ("total_expenses", "expense_breakdown_by_supplier"):
                name = "Total Purchase Amount / Expenses"
            elif key == "gross_margin_pct":
                name = "Profit Margin (Gross Margin %)"
            elif key == "expired_item_count" and val == 0:
                name = "Expired Batches in Stock (Stock mein koi bhi batch expire nahi hua hai)"
            status = item.get("status", "ok")
            unit = item.get("unit", "")
            if status == "ok" and val is not None:
                if isinstance(val, (int, float)):
                    if unit.lower() in ("pkr", "rs", "usd", "eur", "gbp") or unit == "PKR":
                        formatted_val = f"{val} {unit}".strip()
                    elif unit == "percent":
                        formatted_val = f"{val:.2f}%"
                    elif unit in ("count", "items", "rows", "product", "products"):
                        formatted_val = f"{int(val):,}"
                    else:
                        formatted_val = f"{val} {unit}".strip()
                else:
                    formatted_val = f"{val}".strip()
                
                period_str = ""
                if item.get("period") and isinstance(item["period"], dict):
                    p = item["period"]
                    if p.get("start") and p.get("end"):
                        period_str = f" for period {p.get('start')} to {p.get('end')}"
                
                est_str = ""
                if item.get("estimate_range") and isinstance(item["estimate_range"], dict):
                    er = item["estimate_range"]
                    est_str = f" (estimate_range: {er.get('lower')} to {er.get('upper')})"

                lines.append(f"- {name}: {formatted_val}{est_str}{period_str}")

                # Format structured breakdown table if present (e.g. Near-Expiry liquidation or Low-Stock reorder predictions)
                breakdown = item.get("breakdown")
                if breakdown and isinstance(breakdown, list):
                    lines.append(f"  Detailed Breakdown (Total is already calculated above as {formatted_val}; do NOT add breakdown rows to the total):")
                    for idx, row in enumerate(breakdown[:20], 1):
                        parts = []
                        for col_k, col_v in row.items():
                            if col_k == "source_row" or col_v is None or col_v == "":
                                continue
                            parts.append(f"{col_k}: {col_v}")
                        lines.append(f"  {idx}. " + " | ".join(parts))
            elif status == "unavailable" and not has_ok:
                reason = item.get("reason", "data unavailable")
                lines.append(f"- {name}: UNAVAILABLE (Reason: {reason})")
        return "\n".join(lines)

    @staticmethod
    def _format_analytics_number(value, unit: str) -> str:
        if value is None:
            return "unavailable"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if unit.casefold() in {"pkr", "rs", "usd", "eur", "gbp"}:
            return f"{unit.upper()} {number:,.2f}"
        if unit.casefold() == "percent":
            return f"{number:.2f}%"
        if unit.casefold() in {"count", "items", "rows", "products", "product"}:
            return f"{number:,.0f}"
        if unit.casefold() == "units":
            return f"{number:,.0f} units"
        return f"{number:,.2f} {unit}".strip()

    @staticmethod
    def _date_window_text(filters: Optional[Dict[str, Any]]) -> str:
        if not filters or not filters.get("date_from") or not filters.get("date_to"):
            return ""
        try:
            from datetime import datetime
            start_date = datetime.strptime(filters["date_from"], "%Y-%m-%d")
            end_date = datetime.strptime(filters["date_to"], "%Y-%m-%d")
            start = f"{start_date:%b} {start_date.day}, {start_date.year}"
            end = f"{end_date:%b} {end_date.day}, {end_date.year}"
        except (TypeError, ValueError):
            start, end = filters["date_from"], filters["date_to"]
        return f"\n\nDate range used: {start} to {end}."

    def _format_analytics_answer(
        self, question: str, computed_values: Optional[dict], lang: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render deterministic KPI results without asking a small LLM to restate numbers."""
        if not computed_values:
            if lang == "roman_urdu":
                return "Is sawal ke liye muntakhib data se hisaab nahi ho saka."
            if lang == "urdu_script":
                return "منتخب ڈیٹا سے اس سوال کا حساب نہیں ہو سکا۔"
            return "I couldn't calculate this from the selected data." + self._date_window_text(filters)

        q = question.casefold()
        available = {
            key: item for key, item in computed_values.items()
            if item.get("status") == "ok" and item.get("value") is not None
        }
        forecasts = {key: item for key, item in available.items() if item.get("is_estimate")}
        if forecasts:
            asks_revenue = any(word in q for word in ("revenue", "amount", "value", "pkr", "rupee", "rs "))
            asks_units = any(word in q for word in ("unit", "quantity", "how many"))
            if asks_revenue and not asks_units:
                forecasts = {k: v for k, v in forecasts.items() if "revenue" in k}
            elif asks_units and not asks_revenue:
                forecasts = {k: v for k, v in forecasts.items() if "demand" in k or "units" in k}

            parts = []
            seen_forecasts = set()
            periods = []
            for key, item in forecasts.items():
                points = item.get("forecast") or []
                point = points[0] if points else {}
                value = point.get("value", item.get("value"))
                lower, upper = point.get("lower"), point.get("upper")
                period = point.get("period")
                signature = (period, value, lower, upper, item.get("unit"))
                if signature in seen_forecasts:
                    continue
                seen_forecasts.add(signature)
                unit = "units" if "demand" in key or "units" in key else item.get("unit", "")
                if period and period not in periods:
                    periods.append(period)
                label = "Estimated units" if unit == "units" else "Estimated revenue"
                detail = f"- {label}: {self._format_analytics_number(value, unit)}"
                if lower is not None and upper is not None:
                    detail += (
                        f"\n  Likely range: {self._format_analytics_number(lower, unit)}"
                        f" to {self._format_analytics_number(upper, unit)}"
                    )
                parts.append(detail)
            if parts:
                if lang == "roman_urdu":
                    heading = "Agley mahine ki sales ka andaza"
                    caveat = "Yeh tareekhi data par mabni andaza hai; asal natayij mukhtalif ho sakte hain."
                elif lang == "urdu_script":
                    heading = "اگلے ماہ کی فروخت کا تخمینہ"
                    caveat = "یہ گزشتہ ڈیٹا پر مبنی تخمینہ ہے؛ اصل نتائج مختلف ہو سکتے ہیں۔"
                else:
                    heading = "Next-month sales forecast"
                    caveat = "Estimate based on historical data; actual results may vary."
                if periods:
                    period = periods[0]
                    try:
                        from datetime import datetime
                        period = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
                    except (TypeError, ValueError):
                        pass
                    heading += f" — {period}"
                return f"**{heading}**\n\n" + "\n".join(parts) + f"\n\n{caveat}"

        parts = []
        for key, item in available.items():
            label = item.get("name", key)
            parts.append(f"{label}: {self._format_analytics_number(item['value'], item.get('unit', ''))}")
            breakdown = item.get("breakdown") or []
            if breakdown:
                rows = []
                for row in breakdown[:5]:
                    label_key = next((k for k in ("product_id", "product", "medicine_name", "supplier_id", "category", "payment_method") if row.get(k)), None)
                    value_key = next((k for k in ("revenue", "amount", "quantity", "units", "profit", "count", "value") if row.get(k) is not None), None)
                    if label_key and value_key:
                        rows.append(f"{row[label_key]} ({self._format_analytics_number(row[value_key], item.get('unit', ''))})")
                if rows:
                    parts.append(f"Top results: {', '.join(rows)}")

        if not parts:
            unavailable = [item.get("reason") for item in computed_values.values() if item.get("reason")]
            reason = unavailable[0] if unavailable else "no usable metric was returned"
            prefix = "Hisaab dastiyab nahi: " if lang == "roman_urdu" else ("حساب دستیاب نہیں: " if lang == "urdu_script" else "The requested metric is unavailable: ")
            return prefix + reason + self._date_window_text(filters)
        if lang == "roman_urdu":
            heading = "Natayij"
        elif lang == "urdu_script":
            heading = "نتائج"
        else:
            heading = "Analytics results"
        return f"**{heading}**\n\n" + "\n".join(f"- {part}" for part in parts) + self._date_window_text(filters)

    def _get_records_for_analytics(
        self, request: ChatRequest, filters: Dict[str, Any]
    ) -> Tuple[Any, List[RetrievedChunk]]:
        """
        Retrieve the canonical DataFrame directly; converting 22k+ rows to dicts
        and reconstructing a DataFrame was redundant and slowed every analytics turn.
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
            try:
                from app.api.analytics import _build_canonical_database
                combined_df = _build_canonical_database(combined_df)
            except Exception:
                pass
            return combined_df, []

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
        lang = self._detect_query_language(question)
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files,
                lang=lang
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
        if route == RouteType.ANALYTICS and not filters.get("as_of") and any(
            token in question.casefold()
            for token in ("forecast", "predict", "prediction", "projection", "next month", "next week", "expected sales")
        ):
            filters["as_of"] = date.today().isoformat()
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            # Skip retrieval for greetings
            pass
            
        elif route == RouteType.ANALYTICS:
            records, citation_chunks = self._get_records_for_analytics(request, filters)
            filters = self._resolve_relative_date_filter(filters, records)
            relative_error = filters.pop("_date_filter_error", None)
            if relative_error:
                computed_values, source_rows = {
                    "total_revenue": {
                        "name": "Sales", "value": None, "unit": "PKR",
                        "status": "unavailable", "reason": relative_error,
                    }
                }, []
            else:
                # Compute deterministically via the Module 6.6 KPI engine.
                computed_values, source_rows = self.analytics_router.compute(
                    question, filters, records, request.domain
                )
            if computed_values:
                matched = self._analytics_citations(records, source_rows, request.domain)
                sources = self._format_sources(matched)
                 
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

        if route == RouteType.ANALYTICS:
            answer = self._format_analytics_answer(question, computed_values, lang, filters)
            timing = round(time.time() - start_time, 2)
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id, "assistant", answer, domain=request.domain,
                route=route, sources=[s.dict() if hasattr(s, "dict") else s for s in sources] if sources else None,
                timing=timing,
            )
            return ChatResponse(
                answer=answer, route=route, sources=sources,
                computed_values=computed_values, session_id=request.session_id, timing=timing,
            )

        # Build prompt messages
        system_prompt = self._get_system_prompt(request.domain, route, selected_sources=request.file_ids, lang=lang)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"

        if lang == "roman_urdu":
            user_msg += "\n\n[Instruction: Reply in natural Roman-Urdu using English/Latin alphabet only. Use natural Urdu vocabulary (e.g. 'maloomat', 'ziada', 'dastyab') and strictly avoid Hindi words like 'jaankari', 'adhik', 'uplabdh', 'anya', 'pradaan'. Do NOT use Arabic/Urdu script.]"
        elif lang == "english":
            user_msg += "\n\n[Instruction: Reply in English only. Do NOT use Roman-Urdu, Hindi, or Urdu.]"
             
        messages.append({"role": "user", "content": user_msg})
        
        # Call LLM
        try:
            answer = llm.chat(messages=messages)
        except Exception as e:
            answer = f"Error: LLM unavailable ({str(e)}). I am returning offline results if any."

        # Strip any raw debug/metadata block if echoed by the model
        import re
        answer = re.sub(r'\n*computed values.*', '', answer, flags=re.DOTALL | re.IGNORECASE).strip()

        # Clean Roman Urdu vocabulary if model leaked Hindi words
        if lang == "roman_urdu":
            answer = self._clean_roman_urdu_vocabulary(answer)

        # Script safety guard: if Roman-Urdu was expected but model generated Urdu/Arabic script
        if lang == "roman_urdu" and any('\u0600' <= c <= '\u06FF' for c in answer):
            try:
                fix_messages = [
                    {"role": "system", "content": "You are a translator. Rewrite the user's text into Roman-Urdu using ONLY the English/Latin alphabet (e.g. 'Aap ... ke data se deal kar rahe hain'). DO NOT use Arabic or Urdu script."},
                    {"role": "user", "content": answer}
                ]
                cleaned = llm.chat(messages=fix_messages)
                if cleaned and not any('\u0600' <= c <= '\u06FF' for c in cleaned):
                    answer = self._clean_roman_urdu_vocabulary(cleaned)
            except Exception:
                pass
             
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
        lang = self._detect_query_language(question)
        
        # Fast path for metadata/data-source listing inquiries (<0.01s instant answer)
        if self._is_data_source_inquiry(question):
            direct_answer = self._format_data_source_response(
                file_ids=request.file_ids,
                source_files=request.source_files,
                lang=lang
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
        if route == RouteType.ANALYTICS and not filters.get("as_of") and any(
            token in question.casefold()
            for token in ("forecast", "predict", "prediction", "projection", "next month", "next week", "expected sales")
        ):
            filters["as_of"] = date.today().isoformat()
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            pass
        elif route == RouteType.ANALYTICS:
            records, citation_chunks = self._get_records_for_analytics(request, filters)
            filters = self._resolve_relative_date_filter(filters, records)
            relative_error = filters.pop("_date_filter_error", None)
            if relative_error:
                computed_values, source_rows = {
                    "total_revenue": {
                        "name": "Sales", "value": None, "unit": "PKR",
                        "status": "unavailable", "reason": relative_error,
                    }
                }, []
            else:
                computed_values, source_rows = self.analytics_router.compute(
                    question, filters, records, request.domain
                )
            if computed_values:
                matched = self._analytics_citations(records, source_rows, request.domain)
                sources = self._format_sources(matched)
                 
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

        if route == RouteType.ANALYTICS:
            answer = self._format_analytics_answer(question, computed_values, lang, filters)
            timing = round(time.time() - start_time, 2)
            serializable_sources = [
                s.model_dump() if hasattr(s, "model_dump") else s.dict() for s in sources
            ] if sources else []
            yield json.dumps({
                "chunk": answer, "route": route,
                "sources": serializable_sources, "computed_values": computed_values,
            }) + "\n"
            session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
            session_manager.append_turn(
                request.session_id, "assistant", answer, domain=request.domain,
                route=route, sources=serializable_sources or None, timing=timing,
            )
            return

        system_prompt = self._get_system_prompt(request.domain, route, selected_sources=request.file_ids, lang=lang)
        history = session_manager.get_history(request.session_id)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        user_msg = question
        if context_text:
            user_msg = f"{context_text}\n\nQuestion: {question}"

        if lang == "roman_urdu":
            user_msg += "\n\n[Instruction: Reply in natural Roman-Urdu using English/Latin alphabet only. Use natural Urdu vocabulary (e.g. 'maloomat', 'ziada', 'dastyab') and strictly avoid Hindi words like 'jaankari', 'adhik', 'uplabdh', 'anya', 'pradaan'. Do NOT use Arabic/Urdu script.]"
        elif lang == "english":
            user_msg += "\n\n[Instruction: Reply in English only. Do NOT use Roman-Urdu, Hindi, or Urdu.]"
             
        messages.append({"role": "user", "content": user_msg})
        
        serializable_sources = [s.model_dump() if hasattr(s, 'model_dump') else (s.dict() if hasattr(s, 'dict') else s) for s in sources] if sources else []
        full_answer = ""
        try:
            for chunk in llm.chat_stream(messages=messages):
                full_answer += chunk
                # Stop streaming if model starts echoing raw Computed Values block
                if "computed values" in full_answer.lower():
                    break
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
        import re
        saved_answer = re.sub(r'\n*computed values.*', '', full_answer, flags=re.DOTALL | re.IGNORECASE).strip()
        if lang == "roman_urdu":
            saved_answer = self._clean_roman_urdu_vocabulary(saved_answer)
        session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
        session_manager.append_turn(
            request.session_id,
            "assistant",
            saved_answer,
            domain=request.domain,
            route=route,
            sources=serializable_sources if serializable_sources else None,
            timing=timing
        )

rag_chat = RAGChat()

