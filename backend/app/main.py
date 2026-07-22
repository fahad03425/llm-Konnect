from fastapi import FastAPI
from app.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")
# app.include_router(routes.router) # Will be included in a later stage from app.api.routes

@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}
