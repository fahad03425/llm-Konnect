"""Semantic Intent & Route Classifier for RAG Chatbot.

Uses offline local sentence-transformer embeddings (cached in memory) to semantically
classify user questions into RouteType (ANALYTICS, RAG, CHITCHAT) and specific KPI intents.
This handles arbitrary user phrasing, dialects, slang, and spelling variations in
both English and Roman Urdu within 5-15ms on CPU.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

from app.core.config import settings
from app.ingestion.store import _get_global_embedder

logger = logging.getLogger(__name__)


# Standard canonical anchors representing user queries in English and Roman Urdu
_ANCHOR_DEFINITIONS: List[Tuple[str, str, str]] = [
    # (Route, Intent Key, Anchor Text)
    # Analytics - Sales & Revenue
    ("analytics", "sales_yesterday", "kal ki farukht kitni thi"),
    ("analytics", "sales_yesterday", "yesterday total sales revenue"),
    ("analytics", "sales_yesterday", "kal kitna bika"),
    ("analytics", "sales_yesterday", "kal ki bikri batao"),
    ("analytics", "sales_yesterday", "sales for yesterday"),
    ("analytics", "sales_yesterday", "kal kitni sale huwi hai"),

    ("analytics", "sales_day_before_yesterday", "parsu kitni sale huwi hai"),
    ("analytics", "sales_day_before_yesterday", "parson ki farukht kitni thi"),
    ("analytics", "sales_day_before_yesterday", "parso ka galla kitna tha"),
    ("analytics", "sales_day_before_yesterday", "day before yesterday sales"),
    ("analytics", "sales_day_before_yesterday", "parso kitni bikri hui"),
    ("analytics", "sales_day_before_yesterday", "parsu ki sale batao"),

    ("analytics", "sales_three_days_ago", "tarson ki farukht kitni thi"),
    ("analytics", "sales_three_days_ago", "tarso kitni sale hui"),
    ("analytics", "sales_three_days_ago", "three days ago sales"),

    ("analytics", "sales_today", "aaj ki farukht kitni hai"),
    ("analytics", "sales_today", "today total sales revenue"),
    ("analytics", "sales_today", "aj kitni bikri hui"),
    ("analytics", "sales_today", "today's turnover and sales"),

    ("analytics", "sales_total", "meri kitni sale huwi hai"),
    ("analytics", "sales_total", "what is total sales revenue"),
    ("analytics", "sales_total", "ab tak ki kul bikri"),
    ("analytics", "sales_total", "overall total sales amount"),
    ("analytics", "sales_total", "how much is my total sales"),
    ("analytics", "sales_total", "total turnover kitna hai"),

    ("analytics", "profit_margin", "munafa kitna hua"),
    ("analytics", "profit_margin", "what is my net profit and margin"),
    ("analytics", "profit_margin", "faida aur bachat kitni hui"),
    ("analytics", "profit_margin", "profit and gross margin percentage"),
    ("analytics", "profit_margin", "is mahine ka munafa batao"),

    ("analytics", "loss", "kitna nuqsan hua"),
    ("analytics", "loss", "what was total loss or damages"),
    ("analytics", "loss", "ghata kitna hua"),

    ("analytics", "expenses", "kharcha kitna hua"),
    ("analytics", "expenses", "what are my total expenses and purchases"),
    ("analytics", "expenses", "lagat aur akhrajat batao"),
    ("analytics", "expenses", "total purchase spending"),

    ("analytics", "stock_status", "konsi dawai khatam hone wali hai"),
    ("analytics", "stock_status", "which medicines have low stock"),
    ("analytics", "stock_status", "out of stock medicines"),
    ("analytics", "stock_status", "stock kitna bacha hai"),
    ("analytics", "stock_status", "current inventory levels"),

    ("analytics", "top_products", "sab se zyada bikne wali dawai kaunsi hai"),
    ("analytics", "top_products", "which products are best selling"),
    ("analytics", "top_products", "top 10 highest selling medicines"),
    ("analytics", "top_products", "highest sales volume medicines"),

    ("analytics", "slow_moving", "sab se kam bikne wali dawai"),
    ("analytics", "slow_moving", "slow moving dead stock products"),
    ("analytics", "slow_moving", "which medicines are not selling"),

    ("analytics", "expiry", "near expiry medicines in next 60 days"),
    ("analytics", "expiry", "konsi dawai expire hone wali hai"),
    ("analytics", "expiry", "short expiry batch items"),
    ("analytics", "expiry", "batches expiring soon"),

    ("analytics", "forecast", "aglay mahine kitni sale hogi"),
    ("analytics", "forecast", "what is next month sales forecast"),
    ("analytics", "forecast", "future demand prediction for medicines"),

    # RAG - Record & Entity Lookups
    ("rag", "lookup_invoice", "show me invoice 12345"),
    ("rag", "lookup_invoice", "mujhe bill dikhao"),
    ("rag", "lookup_invoice", "find invoice receipt details"),
    ("rag", "lookup_invoice", "parchi talash karo"),

    ("rag", "lookup_batch", "show batch B-12 details"),
    ("rag", "lookup_batch", "Batch B-12 ki tafseel batao"),
    ("rag", "lookup_batch", "find batch record"),

    ("rag", "lookup_supplier", "supplier ka naam batao"),
    ("rag", "lookup_supplier", "who is the supplier of panadol"),
    ("rag", "lookup_supplier", "vendor details and contact"),

    ("rag", "describe_data", "what tables and columns exist"),
    ("rag", "describe_data", "dataset mein kya data mojood hai"),
    ("rag", "describe_data", "list available files and data sources"),

    # Chit-Chat & Assistant Interactions
    ("chit-chat", "greeting", "hello how are you"),
    ("chit-chat", "greeting", "assalam o alaikum"),
    ("chit-chat", "greeting", "kya haal hai kaisay ho"),
    ("chit-chat", "greeting", "salam"),
    ("chit-chat", "capabilities", "tum kya kar sakte ho"),
    ("chit-chat", "capabilities", "what can you do"),
    ("chit-chat", "capabilities", "kya tm muje kisi b sawal ka jawab de skty ho"),
    ("chit-chat", "capabilities", "who made you and what is your purpose"),
    ("chit-chat", "gratitude", "shukriya bohat bohot shukriya"),
    ("chit-chat", "gratitude", "thank you very much"),
]


class SemanticIntentRouter:
    _instance: Optional["SemanticIntentRouter"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._anchors = _ANCHOR_DEFINITIONS
        self._anchor_embeddings: Optional[np.ndarray] = None
        self._init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SemanticIntentRouter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_anchor_embeddings(self) -> np.ndarray:
        if self._anchor_embeddings is None:
            with self._init_lock:
                if self._anchor_embeddings is None:
                    try:
                        embedder = _get_global_embedder(settings.embedding_model)
                        texts = [item[2] for item in self._anchors]
                        # E5 prefix if applicable
                        prefix = "passage: " if "e5" in settings.embedding_model.lower() else ""
                        if prefix:
                            texts = [prefix + t for t in texts]
                        encoded = embedder.encode(texts, batch_size=32, normalize_embeddings=True)
                        self._anchor_embeddings = np.array(encoded, dtype=np.float32)
                        logger.info("Initialized %d semantic intent anchors.", len(self._anchors))
                    except Exception as e:
                        logger.warning("Failed to initialize semantic intent embeddings: %s", e)
                        return np.empty((0, 0), dtype=np.float32)
        return self._anchor_embeddings

    def match(self, question: str, threshold: float = 0.60) -> Optional[Dict[str, Any]]:
        """
        Embed question and perform fast cosine similarity match against intent anchors.
        Returns matched dict {"route": str, "intent": str, "score": float, "anchor": str} or None.
        """
        q = (question or "").strip()
        if not q or len(q) < 3:
            return None

        try:
            embeddings_matrix = self._ensure_anchor_embeddings()
            if embeddings_matrix.shape[0] == 0:
                return None

            embedder = _get_global_embedder(settings.embedding_model)
            prefix = "query: " if "e5" in settings.embedding_model.lower() else ""
            q_text = prefix + q
            q_emb = embedder.encode([q_text], normalize_embeddings=True)[0]
            q_vec = np.array(q_emb, dtype=np.float32)

            scores = np.dot(embeddings_matrix, q_vec)
            best_idx = int(np.argmax(scores))
            best_score = float(scores[best_idx])

            if best_score >= threshold:
                matched_route, matched_intent, anchor_text = self._anchors[best_idx]
                return {
                    "route": matched_route,
                    "intent": matched_intent,
                    "score": best_score,
                    "anchor": anchor_text,
                }
        except Exception as e:
            logger.debug("Semantic intent routing error: %s", e)
            return None

        return None
