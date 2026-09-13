import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
import app.schema  # ensures all domain packs (pharmacy, etc.) register on startup
from app.api import routes, kb, chat, analytics, report, files
from app.ingestion.store import KnowledgeBase
from fastapi.middleware.cors import CORSMiddleware

def _warm_embedding_model():
    try:
        kb_inst = KnowledgeBase()
        kb_inst._get_embedder()
        print("[Startup] Embedding model pre-warmed and ready.")
    except Exception as e:
        print(f"[Startup] Embedding model warm-up notice: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm embedding model in background thread for sub-second first-click ingestion
    threading.Thread(target=_warm_embedding_model, daemon=True).start()
    yield

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

@app.get("/")
def read_root():
    return {"message": "Welcome to LLM-Konnect API. Visit /docs for Swagger UI."}

@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}