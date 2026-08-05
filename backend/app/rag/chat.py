"""Module 6.5 — RAG Chatbot Core."""

import time
import json
from typing import Generator, List, Dict, Any, Optional

from app.core.config import settings
from app.core.llm import llm
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
            "Be concise.\n\n"
        )
        
        if route == RouteType.RAG:
            base_prompt += (
                "CRITICAL: Answer ONLY using the provided Context Records. "
                "Do NOT use external knowledge. "
                "Do NOT perform mathematical calculations (summing, averaging, counting) on the records. "
                "If the answer requires adding up numbers or is not in the context, explicitly say "
                "'I couldn't find anything about that in your data' or 'I cannot calculate that from the records'."
            )
        elif route == RouteType.ANALYTICS:
            base_prompt += (
                "CRITICAL: The user asked a numeric or aggregate question. "
                "The actual answer has been calculated by the Analytics Engine and provided as 'Computed Values'. "
                "You MUST NARRATE the Computed Values exactly as provided. "
                "Do NOT recalculate or guess numbers."
            )
        elif route == RouteType.CHITCHAT:
            base_prompt += (
                "You are an offline assistant for analyzing local business and inventory data. "
                "Answer greetings briefly and explain that you can help lookup records or summarize aggregates from their data."
            )
            
        return base_prompt

    def _format_sources(self, retrieved_chunks) -> List[SourceReference]:
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
            
            label = ", ".join(label_parts) if label_parts else f"Record {c.source_row}"
            
            sources.append(SourceReference(
                source_file=meta.get("source_file", "unknown"),
                source_row=c.source_row,
                label=label
            ))
        return sources

    def ask(self, request: ChatRequest) -> ChatResponse:
        """End-to-end non-streaming RAG pipeline."""
        start_time = time.time()
        
        question = self._normalize_question(request.question)
        route = classify_route(question)
        filters = extract_filters(question, request.domain)
        
        computed_values = None
        sources = []
        context_text = ""
        
        if route == RouteType.CHITCHAT:
            # Skip retrieval for greetings
            pass
            
        elif route == RouteType.ANALYTICS:
            # 1. Retrieve (to get metadata for the fallback)
            # In Module 6.6, this will be a direct DB query.
            chunks = self.kb.search(question, top_k=20, filters=filters, domain=request.domain)
            records = [c.metadata for c in chunks]
            
            # 2. Compute using fallback
            computed_values, source_rows = self.analytics_router.compute(question, filters, records)
            if computed_values:
                context_text = "Computed Values from Analytics Engine:\n" + json.dumps(computed_values, indent=2)
                if chunks:
                     # Only keep sources matching the rows used
                     sources = self._format_sources([c for c in chunks if c.source_row in source_rows])
            else:
                 context_text = "No records found to compute the answer."
                 
        elif route == RouteType.RAG:
            # RAG route: strict lookup
            chunks = self.kb.search(question, top_k=settings.retrieval_top_k, filters=filters, domain=request.domain)
            
            if not chunks:
                 # Short-circuit without LLM call to save time
                 return ChatResponse(
                     answer="I couldn't find anything about that in your data.",
                     route=route,
                     sources=[],
                     computed_values=None,
                     session_id=request.session_id,
                     timing=time.time() - start_time
                 )
                 
            sources = self._format_sources(chunks)
            context_text = "Context Records:\n" + "\n".join([f"- {c.text}" for c in chunks])
            
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
             
        session_manager.append_turn(request.session_id, "user", question)
        session_manager.append_turn(request.session_id, "assistant", answer)
        
        return ChatResponse(
            answer=answer,
            route=route,
            sources=sources,
            computed_values=computed_values,
            session_id=request.session_id,
            timing=time.time() - start_time
        )
        
    def ask_stream(self, request: ChatRequest) -> Generator[str, None, None]:
         """End-to-end streaming RAG pipeline."""
         question = self._normalize_question(request.question)
         route = classify_route(question)
         filters = extract_filters(question, request.domain)
         
         computed_values = None
         sources = []
         context_text = ""
         
         if route == RouteType.CHITCHAT:
             pass
         elif route == RouteType.ANALYTICS:
             chunks = self.kb.search(question, top_k=20, filters=filters, domain=request.domain)
             records = [c.metadata for c in chunks]
             computed_values, source_rows = self.analytics_router.compute(question, filters, records)
             if computed_values:
                 context_text = "Computed Values from Analytics Engine:\n" + json.dumps(computed_values, indent=2)
                 if chunks:
                     sources = self._format_sources([c for c in chunks if c.source_row in source_rows])
             else:
                 context_text = "No records found to compute the answer."
                 
         elif route == RouteType.RAG:
             chunks = self.kb.search(question, top_k=settings.retrieval_top_k, filters=filters, domain=request.domain)
             if not chunks:
                  yield json.dumps({
                      "chunk": "I couldn't find anything about that in your data.",
                      "route": route,
                      "sources": [],
                      "computed_values": None
                  })
                  return
                  
             sources = self._format_sources(chunks)
             context_text = "Context Records:\n" + "\n".join([f"- {c.text}" for c in chunks])
             
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
                 # Yield JSON encoded chunks for SSE
                 yield json.dumps({
                     "chunk": chunk,
                     "route": route,
                     "sources": [s.dict() for s in sources],
                     "computed_values": computed_values
                 }) + "\n"
         except Exception as e:
              yield json.dumps({"error": str(e)}) + "\n"
              return
              
         session_manager.append_turn(request.session_id, "user", question)
         session_manager.append_turn(request.session_id, "assistant", full_answer)

rag_chat = RAGChat()
