"""Module 6.1 — Local LLM Inference Module.
Manages the locally running language model through Ollama.
Handles hardware detection, model pulling, loading and unloading, model switching,
and provides a single internal model-agnostic inference interface.
"""

import os
import re
import subprocess
from typing import Any, Dict, Generator, List, Optional
from app.core.config import settings


class LLMService:
    def __init__(self):
        # Local model defaults from config
        self.model = settings.llm_model
        self.host = settings.ollama_host
        self._cached_hw: Optional[Dict[str, Any]] = None

    def _get_client(self):
        try:
            import ollama
            return ollama.Client(host=self.host)
        except ImportError:
            raise ImportError("Please install ollama package: pip install ollama")

    # =========================================================================
    # Hardware Detection & Profile Recommendation
    # =========================================================================
    def detect_hardware(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Detect system hardware (GPU availability, VRAM, CPU cores) to recommend
        optimal local models (e.g. Phi-4-mini / lightweight models for CPU/low-VRAM,
        Qwen 2.5 / Mistral for GPU-enabled machines).
        """
        if self._cached_hw and not force_refresh:
            return self._cached_hw

        cpu_cores = os.cpu_count() or 4
        hw_info: Dict[str, Any] = {
            "gpu_available": False,
            "gpu_name": None,
            "vram_mb": 0,
            "cpu_cores": cpu_cores,
            "device_type": "cpu",
            "recommended_profile": "cpu_lightweight",
            "recommended_models": ["phi4-mini", "phi3:mini", "llama3.2:3b", "qwen2.5:3b", "qwen2.5:1.5b"],
        }

        # 1. Try PyTorch CUDA if installed
        try:
            import torch
            if torch.cuda.is_available():
                hw_info["gpu_available"] = True
                hw_info["gpu_name"] = torch.cuda.get_device_name(0)
                hw_info["vram_mb"] = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
        except Exception:
            pass

        # 2. Try nvidia-smi if torch CUDA wasn't available
        if not hw_info["gpu_available"]:
            try:
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    text=True,
                    timeout=2,
                    stderr=subprocess.DEVNULL
                )
                parts = [p.strip() for p in out.strip().split(",")]
                if len(parts) >= 2:
                    hw_info["gpu_available"] = True
                    hw_info["gpu_name"] = parts[0]
                    hw_info["vram_mb"] = int(parts[1])
            except Exception:
                pass

        # Determine recommendations based on VRAM / device type
        if hw_info["gpu_available"]:
            vram = hw_info["vram_mb"]
            hw_info["device_type"] = "gpu"
            if vram >= 12000:
                hw_info["recommended_profile"] = "gpu_heavy"
                hw_info["recommended_models"] = ["qwen2.5:14b", "qwen2.5:7b", "mistral:7b", "llama3.1:8b"]
            elif vram >= 5500:
                hw_info["recommended_profile"] = "gpu_standard"
                hw_info["recommended_models"] = ["qwen2.5:7b", "mistral:7b", "llama3.1:8b", "phi4-mini"]
            else:
                hw_info["recommended_profile"] = "gpu_budget"
                hw_info["recommended_models"] = ["qwen2.5:3b", "phi4-mini", "llama3.2:3b", "gemma2:2b"]
        else:
            hw_info["device_type"] = "cpu"
            hw_info["recommended_profile"] = "cpu_lightweight"
            hw_info["recommended_models"] = ["phi4-mini", "phi3:mini", "llama3.2:3b", "qwen2.5:3b", "qwen2.5:1.5b"]

        self._cached_hw = hw_info
        return hw_info

    # =========================================================================
    # Model Resolution & Discovery
    # =========================================================================
    def _resolve_model(self, client, override_model: Optional[str] = None) -> str:
        """Resolve the best available local model from Ollama."""
        target_model = override_model or self.model
        try:
            res = client.list()
            installed = []
            if hasattr(res, "models"):
                installed = [getattr(m, "model", None) or getattr(m, "name", "") for m in res.models]
            elif isinstance(res, dict) and "models" in res:
                installed = [m.get("model") or m.get("name") for m in res["models"]]

            if not installed:
                return target_model

            # 1. Exact match
            if target_model in installed:
                return target_model

            # 2. Base name match (e.g. llama3.2 matches llama3.2:3b)
            base_model = target_model.split(":")[0].lower()
            for m in installed:
                if m.split(":")[0].lower() == base_model:
                    return m

            # 3. Dynamic preference hierarchy matching hardware recommendation
            hw = self.detect_hardware()
            for pref in hw.get("recommended_models", []):
                pref_base = pref.split(":")[0].lower()
                for m in installed:
                    if pref_base in m.lower():
                        return m

            # 4. General fallback hierarchy (ordered by speed, low-end efficiency, and accuracy)
            for pref in ["qwen2.5:1.5b", "qwen2.5:0.5b", "qwen2.5", "llama3.2:1b", "gemma3:1b", "llama3.2", "phi4-mini", "llama", "mistral"]:
                for m in installed:
                    if pref in m.lower():
                        return m

            return installed[0]
        except Exception:
            return target_model

    @staticmethod
    def _clean_output(text: str) -> str:
        """Strip internal reasoning blocks like <think>...</think> if emitted by the model."""
        if not text:
            return ""
        if "</think>" in text:
            text = text.split("</think>")[-1]
        if "<think>" in text:
            text = text.split("<think>")[0]
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"\n+Computed Values:\s*\n.*", "", text, flags=re.DOTALL | re.IGNORECASE)
        return text.strip()

    def _get_default_options(self, custom_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        num_threads = max(4, min(16, os.cpu_count() or 6))
        opts: Dict[str, Any] = {
            "temperature": settings.llm_temperature,
            "num_predict": settings.llm_num_predict,
            "num_ctx": 1536,
            "num_thread": num_threads,
            "top_k": 20,
            "top_p": 0.85,
        }
        if custom_opts:
            opts.update(custom_opts)
        return opts

    # =========================================================================
    # Model Management (List, Detailed Info, Active Model, Delete)
    # =========================================================================
    def list_installed_models(self) -> List[str]:
        """List all installed local model names in Ollama."""
        try:
            client = self._get_client()
            res = client.list()
            installed = []
            if hasattr(res, "models"):
                installed = [getattr(m, "model", None) or getattr(m, "name", "") for m in res.models]
            elif isinstance(res, dict) and "models" in res:
                installed = [m.get("model") or m.get("name") for m in res["models"]]
            return [m for m in installed if m]
        except Exception:
            return [self.model]

    def list_installed_models_detailed(self) -> List[Dict[str, Any]]:
        """List installed models with size, parameter count, family, and quantization details."""
        try:
            client = self._get_client()
            res = client.list()
            items = []
            models_list = getattr(res, "models", None) or (res.get("models") if isinstance(res, dict) else [])
            for m in models_list:
                name = getattr(m, "model", None) or getattr(m, "name", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else str(m))
                size = getattr(m, "size", None) or (m.get("size") if isinstance(m, dict) else 0)
                modified_at = getattr(m, "modified_at", None) or (m.get("modified_at") if isinstance(m, dict) else "")
                details = getattr(m, "details", None) or (m.get("details") if isinstance(m, dict) else {})

                detail_dict = {}
                if details:
                    if hasattr(details, "family"):
                        detail_dict["family"] = getattr(details, "family", "")
                        detail_dict["parameter_size"] = getattr(details, "parameter_size", "")
                        detail_dict["quantization_level"] = getattr(details, "quantization_level", "")
                    elif isinstance(details, dict):
                        detail_dict = details

                items.append({
                    "name": name,
                    "size_bytes": size,
                    "size_gb": round(size / (1024**3), 2) if size else 0,
                    "modified_at": str(modified_at),
                    "details": detail_dict,
                    "is_active": name == self.model
                })
            return items
        except Exception:
            return [{"name": self.model, "size_bytes": 0, "size_gb": 0, "is_active": True}]

    def set_active_model(self, model_name: str) -> str:
        """Set the active LLM model."""
        self.model = model_name
        settings.llm_model = model_name
        return self.model

    def get_model_info(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Fetch model parameters and details from Ollama."""
        target_model = model_name or self.model
        client = self._get_client()
        try:
            res = client.show(target_model)
            if hasattr(res, "model_dump"):
                return res.model_dump()
            elif isinstance(res, dict):
                return res
            return {"model": target_model, "details": str(res)}
        except Exception as e:
            raise RuntimeError(f"Failed to fetch info for model '{target_model}': {str(e)}")

    def delete_model(self, model_name: str) -> bool:
        """Delete an installed model from Ollama."""
        client = self._get_client()
        try:
            client.delete(model_name)
            return True
        except Exception as e:
            raise RuntimeError(f"Failed to delete model '{model_name}': {str(e)}")

    # =========================================================================
    # Model Pulling (Streaming & Non-Streaming)
    # =========================================================================
    def pull_model(self, model_name: str) -> Dict[str, Any]:
        """Pull a model from Ollama library synchronously."""
        client = self._get_client()
        try:
            res = client.pull(model=model_name, stream=False)
            return {"status": "success", "model": model_name, "result": str(res)}
        except Exception as e:
            raise RuntimeError(f"Failed to pull model '{model_name}': {str(e)}")

    def pull_model_stream(self, model_name: str) -> Generator[Dict[str, Any], None, None]:
        """Pull a model from Ollama library yielding progress updates."""
        client = self._get_client()
        try:
            stream = client.pull(model=model_name, stream=True)
            for chunk in stream:
                if isinstance(chunk, dict):
                    status = chunk.get("status", "")
                    completed = chunk.get("completed", 0)
                    total = chunk.get("total", 0)
                else:
                    status = getattr(chunk, "status", "")
                    completed = getattr(chunk, "completed", 0) or 0
                    total = getattr(chunk, "total", 0) or 0

                percent = round((completed / total) * 100, 1) if (total and total > 0) else 0.0
                yield {
                    "model": model_name,
                    "status": status,
                    "completed": completed,
                    "total": total,
                    "percent": percent
                }
        except Exception as e:
            yield {
                "model": model_name,
                "status": f"error: {str(e)}",
                "completed": 0,
                "total": 0,
                "percent": 0.0
            }

    # =========================================================================
    # Model Loading and Unloading (Memory & VRAM Management)
    # =========================================================================
    def load_model(self, model_name: Optional[str] = None, keep_alive: str = "30m") -> Dict[str, Any]:
        """
        Explicitly pre-load a model into VRAM/RAM so subsequent inference is instant.
        """
        client = self._get_client()
        target_model = model_name or self._resolve_model(client)
        try:
            # Trigger load via 0-token ping with keep_alive
            client.generate(model=target_model, prompt="", keep_alive=keep_alive)
            return {
                "status": "loaded",
                "model": target_model,
                "keep_alive": keep_alive
            }
        except Exception as e:
            raise RuntimeError(f"Failed to load model '{target_model}': {str(e)}")

    def unload_model(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Explicitly evict/unload a model from VRAM/RAM immediately (releases resources).
        """
        client = self._get_client()
        target_model = model_name or self.model
        try:
            # Passing keep_alive=0 tells Ollama to immediately unload the model from memory
            client.generate(model=target_model, prompt="", keep_alive=0)
            return {
                "status": "unloaded",
                "model": target_model
            }
        except Exception as e:
            raise RuntimeError(f"Failed to unload model '{target_model}': {str(e)}")

    def get_running_models(self) -> List[Dict[str, Any]]:
        """List all models currently loaded in memory/VRAM."""
        try:
            client = self._get_client()
            if hasattr(client, "ps"):
                res = client.ps()
                items = []
                models_list = getattr(res, "models", None) or (res.get("models") if isinstance(res, dict) else [])
                for m in models_list:
                    name = getattr(m, "model", None) or getattr(m, "name", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else str(m))
                    size_vram = getattr(m, "size_vram", None) or (m.get("size_vram") if isinstance(m, dict) else 0)
                    size = getattr(m, "size", None) or (m.get("size") if isinstance(m, dict) else 0)
                    expires_at = getattr(m, "expires_at", None) or (m.get("expires_at") if isinstance(m, dict) else "")
                    items.append({
                        "name": name,
                        "size_bytes": size,
                        "size_vram_bytes": size_vram,
                        "size_vram_mb": round(size_vram / (1024**2), 1) if size_vram else 0,
                        "expires_at": str(expires_at)
                    })
                return items
            return []
        except Exception:
            return []

    # =========================================================================
    # Model-Agnostic Inference Core (Preserved & Enhanced)
    # =========================================================================
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        keep_alive: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> str:
        """Non-streaming generation."""
        client = self._get_client()
        active_model = self._resolve_model(client, override_model=model)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        opts = self._get_default_options(options)
        kwargs = {
            "model": active_model,
            "messages": messages,
            "options": opts,
            "keep_alive": keep_alive or settings.llm_keep_alive,
        }

        try:
            response = client.chat(**kwargs)
            content = response["message"]["content"]
            return self._clean_output(content)
        except Exception as e:
            raise RuntimeError(f"Ollama inference failed: {str(e)}")

    def chat(
        self,
        messages: List[Dict[str, str]],
        keep_alive: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> str:
        """Non-streaming fast chat."""
        client = self._get_client()
        active_model = self._resolve_model(client, override_model=model)

        opts = self._get_default_options(options)
        kwargs = {
            "model": active_model,
            "messages": messages,
            "options": opts,
            "keep_alive": keep_alive or settings.llm_keep_alive_chat,
        }

        try:
            response = client.chat(**kwargs)
            content = response["message"]["content"]
            return self._clean_output(content)
        except Exception as e:
            raise RuntimeError(f"Ollama inference failed: {str(e)}")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        keep_alive: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Streaming chat completion for interactive use."""
        client = self._get_client()
        active_model = self._resolve_model(client, override_model=model)

        opts = self._get_default_options(options)
        kwargs = {
            "model": active_model,
            "messages": messages,
            "stream": True,
            "options": opts,
            "keep_alive": keep_alive or settings.llm_keep_alive_chat,
        }

        try:
            stream = client.chat(**kwargs)
            inside_think = False
            for chunk in stream:
                if "message" in chunk and "content" in chunk["message"]:
                    token = chunk["message"]["content"]
                    if "<think>" in token:
                        inside_think = True
                        continue
                    if "</think>" in token:
                        inside_think = False
                        continue
                    if not inside_think:
                        yield token
        except Exception as e:
            raise RuntimeError(f"Ollama chat failed: {str(e)}")


llm = LLMService()
