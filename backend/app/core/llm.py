"""Module 6.1 — Local LLM Inference Module.
Manages the locally running language model through Ollama.
Handles hardware detection, model pulling, loading and unloading, model switching,
and provides a single internal model-agnostic inference interface.
"""

import os
import csv
import io
import re
import subprocess
import json
import tempfile
import threading
import sys
import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        # Local model defaults from config
        self.model = self._read_saved_model() or settings.llm_model
        settings.llm_model = self.model
        self.host = settings.ollama_host
        self._cached_hw: Optional[Dict[str, Any]] = None
        self._model_lock = threading.Lock()

    @staticmethod
    def _model_settings_path() -> Path:
        base = Path(settings.storage_dir)
        if not base.is_absolute():
            base = Path(__file__).resolve().parents[3] / base
        return base / "model_settings.json"

    @classmethod
    def _read_saved_model(cls) -> Optional[str]:
        try:
            value = json.loads(cls._model_settings_path().read_text(encoding="utf-8")).get("active_model")
            return value.strip() if isinstance(value, str) and value.strip() else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    @classmethod
    def find_ollama_executable(cls) -> Optional[str]:
        """Find path to local Ollama executable if installed."""
        import shutil
        found = shutil.which("ollama")
        if found and Path(found).is_file():
            return str(found)

        candidates = []
        if sys.platform == "win32":
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            prog_files = os.environ.get("ProgramFiles", "")
            prog_files_x86 = os.environ.get("ProgramFiles(x86)", "")
            user_profile = os.environ.get("USERPROFILE", "")

            candidates.extend([
                Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe" if local_appdata else None,
                Path(prog_files) / "Ollama" / "ollama.exe" if prog_files else None,
                Path(prog_files_x86) / "Ollama" / "ollama.exe" if prog_files_x86 else None,
                Path(user_profile) / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe" if user_profile else None,
            ])
        else:
            candidates.extend([
                Path("/usr/local/bin/ollama"),
                Path("/usr/bin/ollama"),
                Path(Path.home() / ".ollama" / "bin" / "ollama"),
            ])

        for c in candidates:
            if c and c.is_file():
                return str(c)
        return None

    def is_ollama_alive(self, timeout: float = 1.0) -> bool:
        """Check if Ollama server responds on configured host."""
        import urllib.request
        try:
            url = self.host.rstrip("/") + "/api/tags"
            req = urllib.request.Request(url, headers={"User-Agent": "LLM-Konnect"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def ensure_ollama_running(self, wait_timeout: float = 6.0) -> bool:
        """
        Check if Ollama is running. If not, attempt to launch it as a background process
        and wait until it is ready.
        """
        if self.is_ollama_alive(timeout=0.8):
            return True

        # Only auto-launch if host points to localhost / loopback
        parsed_host = self.host.lower()
        if not ("127.0.0.1" in parsed_host or "localhost" in parsed_host or "::1" in parsed_host):
            return False

        ollama_exe = self.find_ollama_executable()
        if not ollama_exe:
            print("[Ollama] Autostart notice: ollama executable not found in PATH or standard installation paths.")
            return False

        try:
            print(f"[Ollama] Starting local Ollama server from {ollama_exe}...")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000

            subprocess.Popen(
                [ollama_exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True if sys.platform != "win32" else False,
            )
        except Exception as e:
            print(f"[Ollama] Failed to launch Ollama process: {e}")
            return False

        # Poll until responsive or timeout
        import time
        start_time = time.time()
        while time.time() - start_time < wait_timeout:
            time.sleep(0.5)
            if self.is_ollama_alive(timeout=0.5):
                print("[Ollama] Local Ollama server is now running and responsive.")
                return True

        return self.is_ollama_alive(timeout=0.5)

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

        # Metal uses unified memory; keep recommendations in the small-model tier.
        if not hw_info["gpu_available"] and sys.platform == "darwin":
            try:
                if torch.backends.mps.is_available():
                    hw_info["gpu_available"] = True
                    hw_info["gpu_name"] = "Apple Metal (unified memory)"
            except (NameError, AttributeError):
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
                devices = [(row[0].strip(), int(row[1].strip()))
                           for row in csv.reader(io.StringIO(out)) if len(row) >= 2]
                if devices:
                    name, memory = max(devices, key=lambda device: device[1])
                    hw_info["gpu_available"] = True
                    hw_info["gpu_name"] = name
                    hw_info["vram_mb"] = memory
            except Exception:
                pass

        # Windows AMD/Intel adapters may be usable by Ollama without PyTorch CUDA.
        # AdapterRAM is advisory and can be capped by Windows; never use it to
        # promote a model to a larger tier than the reported capacity.
        if not hw_info["gpu_available"] and sys.platform == "win32":
            try:
                output = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"],
                    text=True, timeout=2, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                adapters = json.loads(output)
                if isinstance(adapters, dict):
                    adapters = [adapters]
                adapters = [adapter for adapter in adapters
                            if any(vendor in adapter.get("Name", "").lower()
                                   for vendor in ("amd", "radeon", "intel", "nvidia"))]
                if adapters:
                    adapter = max(adapters, key=lambda item: item.get("AdapterRAM") or 0)
                    hw_info["gpu_available"] = True
                    hw_info["gpu_name"] = adapter["Name"]
                    hw_info["vram_mb"] = int((adapter.get("AdapterRAM") or 0) / (1024 * 1024))
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
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

        # Resource readings are advisory; Ollama controls actual device offloading.
        hw_info["ram_mb"] = None
        hw_info["available_ram_mb"] = None
        hw_info["available_vram_mb"] = None
        try:
            import psutil
            memory = psutil.virtual_memory()
            hw_info["ram_mb"] = int(memory.total / (1024 * 1024))
            hw_info["available_ram_mb"] = int(memory.available / (1024 * 1024))
        except (ImportError, OSError):
            pass
        if hw_info["gpu_available"]:
            try:
                rows = csv.reader(io.StringIO(subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name,memory.free", "--format=csv,noheader,nounits"],
                    text=True, timeout=2, stderr=subprocess.DEVNULL)))
                hw_info["available_vram_mb"] = next(
                    int(row[1].strip()) for row in rows
                    if len(row) >= 2 and row[0].strip() == hw_info["gpu_name"])
            except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
                pass
        hw_info["recommendations_advisory"] = True
        self._cached_hw = hw_info
        return hw_info

    # =========================================================================
    # Model Resolution & Discovery
    # =========================================================================
    @staticmethod
    def _installed_names(client) -> List[str]:
        response = client.list()
        models = response.models if hasattr(response, "models") else response.get("models", [])
        return [name for item in models
                if (name := (item.get("model") or item.get("name") if isinstance(item, dict)
                             else getattr(item, "model", None) or getattr(item, "name", None)))]

    @staticmethod
    def _match_model(target: str, installed: List[str]) -> Optional[str]:
        """Respect tags; an untagged name uses Ollama's :latest alias only."""
        canonical = target if ":" in target else target + ":latest"
        for name in installed:
            if name == target or name == canonical:
                return name
        # A size tag may have an explicit quantization/instruct suffix.
        if ":" in target:
            return next((name for name in installed if name.startswith(target + "-")), None)
        return None

    def _resolve_model(self, client, override_model: Optional[str] = None, *, installed: Optional[List[str]] = None) -> str:
        target = override_model or self.model
        if installed is None:
            installed = self._installed_names(client)
        matched = self._match_model(target, installed)
        if matched:
            return matched
        if override_model:
            raise ValueError(f"Model '{target}' is not installed. Download it first.")
        # Only fallback to recommended tags, never to an arbitrary larger family member.
        for preference in self.detect_hardware().get("recommended_models", []):
            matched = self._match_model(preference, installed)
            if matched:
                return matched
        raise ValueError(f"Model '{target}' is not installed and no recommended model is available. Download a suggested model.")

    def resolve_chat_model(self, language: Optional[str] = None) -> str:
        """Keep language policy here and use the selected model when the preference is absent."""
        client = self._get_client()
        installed = self._installed_names(client)
        if language in ("roman_urdu", "urdu_script"):
            preferred = self._match_model("llama3.2:3b", installed)
            if preferred:
                return preferred
        return self._resolve_model(client, installed=installed)

    def get_status(self, installed: Optional[List[str]] = None) -> Dict[str, Any]:
        """Connectivity is independent of whether any models have been downloaded."""
        try:
            client = self._get_client()
            if installed is None:
                installed = self._installed_names(client)
            try:
                resolved = self._resolve_model(client, installed=installed)
            except ValueError:
                resolved = None
            return {"available": True, "resolved_model": resolved, "error": None}
        except Exception as exc:
            return {"available": False, "resolved_model": None, "error": str(exc)}

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

    @staticmethod
    def _log_inference_timing(operation: str, model: str, response: Any) -> None:
        """Log Ollama's own load, prompt-evaluation, and generation timings."""
        def metric(name: str) -> Optional[float]:
            value = response.get(name) if isinstance(response, dict) else getattr(response, name, None)
            return round(float(value) / 1_000_000_000, 3) if isinstance(value, (int, float)) else None

        logger.info(
            "ollama_inference_timing operation=%s model=%s total_s=%s load_s=%s prompt_eval_s=%s "
            "generation_s=%s prompt_tokens=%s output_tokens=%s",
            operation, model, metric("total_duration"), metric("load_duration"),
            metric("prompt_eval_duration"), metric("eval_duration"),
            response.get("prompt_eval_count") if isinstance(response, dict) else getattr(response, "prompt_eval_count", None),
            response.get("eval_count") if isinstance(response, dict) else getattr(response, "eval_count", None),
        )

    # =========================================================================
    # Model Management (List, Detailed Info, Active Model, Delete)
    # =========================================================================
    def list_installed_models(self) -> List[str]:
        """List all installed local model names in Ollama."""
        try:
            return self._installed_names(self._get_client())
        except Exception as exc:
            raise RuntimeError(f"Cannot list Ollama models: {exc}") from exc

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
        except Exception as exc:
            raise RuntimeError(f"Cannot list Ollama models: {exc}") from exc

    def set_active_model(self, model_name: str) -> str:
        """Set the active LLM model."""
        model_name = model_name.strip()
        if not model_name:
            raise ValueError("Model name is required")
        client = self._get_client()
        installed = self._installed_names(client)
        matched = self._match_model(model_name, installed)
        if not matched:
            raise ValueError(f"Model '{model_name}' is not installed. Download it first.")
        info = client.show(matched)
        capabilities = info.get("capabilities") if isinstance(info, dict) else getattr(info, "capabilities", None)
        if capabilities and "completion" not in capabilities:
            raise ValueError(f"Model '{matched}' does not support text completion.")
        model_name = matched
        with self._model_lock:
            path = self._model_settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix="model_settings_", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump({"active_model": model_name}, handle)
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
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
        try:
            client = self._get_client()
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
        target_model = model_name or self._resolve_model(client)
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
        except Exception as exc:
            raise RuntimeError(f"Cannot inspect running Ollama models: {exc}") from exc

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
            self._log_inference_timing("chat", active_model, response)
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
        format: Optional[Any] = None,
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
        if format is not None:
            kwargs["format"] = format

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
            def content_chunks():
                for chunk in stream:
                    if chunk.get("done"):
                        self._log_inference_timing("chat_stream", active_model, chunk)
                    if "message" in chunk and "content" in chunk["message"]:
                        yield chunk["message"]["content"]

            yield from self._filter_stream(content_chunks())
        except Exception as e:
            raise RuntimeError(f"Ollama chat failed: {str(e)}")

    @staticmethod
    def _filter_stream(tokens) -> Generator[str, None, None]:
        """Buffer only possible tag prefixes; preserve ordinary streaming latency."""
        pending = ""
        inside = False
        tags = ("<think>", "</think>")
        for token in tokens:
            pending += token
            while pending:
                matches = [(pending.find(tag), tag) for tag in tags if tag in pending]
                if matches:
                    position, tag = min(matches)
                    if position and not inside:
                        yield pending[:position]
                    inside = tag == "<think>"
                    pending = pending[position + len(tag):]
                    continue
                held = max((length for length in range(1, min(len(pending), 7) + 1)
                            if any(tag.startswith(pending[-length:]) for tag in tags)), default=0)
                ready = pending[:-held] if held else pending
                if ready and not inside:
                    yield ready
                pending = pending[-held:] if held else ""
                break
        if pending and not inside:
            yield pending


llm = LLMService()
