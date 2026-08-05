from fastapi import FastAPI
from app.core.config import settings

import app.schema  # ensures all domain packs (pharmacy, etc.) register on startup
from app.api import routes, kb

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(routes.router)
app.include_router(kb.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to LLM-Konnect API. Visit /docs for Swagger UI."}

@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}
