from fastapi import FastAPI
from app.core.config import settings
import app.schema  # ensures all domain packs (pharmacy, etc.) register on startup
from app.api import routes, kb, chat, analytics, report
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(routes.router)
app.include_router(kb.router)
app.include_router(chat.router)
app.include_router(analytics.router)
app.include_router(report.router)
@app.get("/")
def read_root():
    return {"message": "Welcome to LLM-Konnect API. Visit /docs for Swagger UI."}
@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}