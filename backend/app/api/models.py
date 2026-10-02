"""API endpoints for Local LLM Inference Management (Module 6.1)."""

import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.core.llm import llm

router = APIRouter(prefix="/api/models", tags=["models"])


class SelectModelRequest(BaseModel):
    model: str = Field(..., description="Name of local model to activate")


class LoadModelRequest(BaseModel):
    model: Optional[str] = Field(None, description="Model to load into memory (defaults to active/resolved model)")
    keep_alive: Optional[str] = Field("30m", description="Duration to keep model in memory (e.g. 5m, 30m, 1h, -1)")


class UnloadModelRequest(BaseModel):
    model: Optional[str] = Field(None, description="Model to unload from memory (defaults to active model)")


class PullModelRequest(BaseModel):
    model: str = Field(..., description="Model name or tag to pull from Ollama library (e.g. 'phi4-mini', 'qwen2.5:3b')")
    stream: Optional[bool] = Field(True, description="Whether to stream download progress")


@router.get("")
@router.get("/")
def list_models(detailed: bool = Query(False, description="Include detailed model metadata (size, parameter count, etc.)")):
    """List all installed local models, active model, and hardware overview."""
    try:
        if detailed:
            models = llm.list_installed_models_detailed()
        else:
            models = llm.list_installed_models()

        return {
            "active_model": llm.model,
            "models": models,
            "hardware": llm.detect_hardware()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hardware")
def get_hardware_info(refresh: bool = Query(False, description="Force hardware re-scan")):
    """Inspect GPU, VRAM, and CPU to return recommended models (e.g. Phi-4-mini vs Qwen 2.5)."""
    try:
        hw = llm.detect_hardware(force_refresh=refresh)
        return {
            "hardware": hw,
            "active_model": llm.model
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/running")
def list_running_models():
    """List all models currently loaded into memory/VRAM with their memory footprint."""
    try:
        running = llm.get_running_models()
        return {
            "running_models": running,
            "count": len(running)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info/{model_name:path}")
def get_model_info(model_name: str):
    """Retrieve detailed parameters and architecture information for a specific model."""
    try:
        return llm.get_model_info(model_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/select")
def select_model(payload: SelectModelRequest):
    """Switch active LLM model used across the application."""
    try:
        active = llm.set_active_model(payload.model)
        return {
            "status": "ok",
            "active_model": active,
            "message": f"Active model successfully switched to '{active}'"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/load")
def load_model(payload: LoadModelRequest):
    """Explicitly pre-warm / load a model into VRAM/RAM for fast inference."""
    try:
        res = llm.load_model(model_name=payload.model, keep_alive=payload.keep_alive or "30m")
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/unload")
def unload_model(payload: UnloadModelRequest):
    """Explicitly evict / unload a model from memory to free VRAM/RAM."""
    try:
        res = llm.unload_model(model_name=payload.model)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pull")
def pull_model(payload: PullModelRequest):
    """Pull a model from Ollama library with streaming progress (NDJSON) or synchronous return."""
    if payload.stream:
        def _stream_generator():
            for progress in llm.pull_model_stream(payload.model):
                yield json.dumps(progress) + "\n"

        return StreamingResponse(_stream_generator(), media_type="application/x-ndjson")
    else:
        try:
            return llm.pull_model(payload.model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{model_name:path}")
def delete_model(model_name: str):
    """Delete a local model from Ollama storage."""
    try:
        success = llm.delete_model(model_name)
        return {"status": "ok", "deleted": model_name, "success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
