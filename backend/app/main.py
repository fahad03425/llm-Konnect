import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
import app.schema  # ensures all domain packs (pharmacy, etc.) register on startup
from app.api import routes, kb, chat, analytics, report, files, anomaly, security
from app.ingestion.store import KnowledgeBase
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Safely pre-warm embedding model and LLM in background daemon thread
    def _prewarm():
        try:
            kb_inst = KnowledgeBase()
            kb_inst._get_embedder()
            print("[Startup] Embedding model pre-warmed and ready.")
        except Exception as e:
            print(f"[Startup] Embedding model warm-up notice: {e}")
        try:
            from app.core.llm import llm
            client = llm._get_client()
            active_model = llm._resolve_model(client)
            print(f"[Startup] Local LLM ({active_model}) ready.")
        except Exception as e:
            print(f"[Startup] Local LLM warm-up notice: {e}")

    threading.Thread(target=_prewarm, daemon=True).start()

    # Start real-time sync worker
    from app.ingestion.sync_worker import sync_worker
    sync_worker.start()

    yield

    # Shutdown real-time sync worker
    sync_worker.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500", "http://127.0.0.1:5173", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
app.include_router(kb.router)
app.include_router(chat.router)
app.include_router(analytics.router)
app.include_router(report.router)
app.include_router(files.router)
app.include_router(anomaly.router)
app.include_router(security.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to LLM-Konnect API. Visit /docs for Swagger UI."}

@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}

@app.get("/api/domains")
@app.get("/domains")
def get_domains():
    from app.schema.domain import registry
    from app.core.config import get_default_domain
    return {
        "default_domain": get_default_domain(),
        "active_domain": get_default_domain(),
        "domains": registry.available_domains(),
        "domain_details": registry.get_domain_details(),
    }