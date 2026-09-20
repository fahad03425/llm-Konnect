"""Module 1.1 — Local LLM inference (Ollama wrapper). Month 1."""

import os
from typing import Any, Dict, Generator, List, Optional
from app.core.config import settings

class LLMService:
    def __init__(self):
        # Local model defaults
        self.model = settings.llm_model
        self.host = settings.ollama_host

    def _get_client(self):
        try:
            import ollama
            return ollama.Client(host=self.host)
        except ImportError:
            raise ImportError("Please install ollama package: pip install ollama")

    def _resolve_model(self, client) -> str:
        """Resolve the best available local model from Ollama."""
        try:
            res = client.list()
            installed = []
            if hasattr(res, 'models'):
                installed = [getattr(m, 'model', None) or getattr(m, 'name', '') for m in res.models]
            elif isinstance(res, dict) and 'models' in res:
                installed = [m.get('model') or m.get('name') for m in res['models']]

            if not installed:
                return self.model

            # 1. Exact match
            if self.model in installed:
                return self.model

            # 2. Base name match (e.g. qwen3 matches qwen3:4b)
            base_model = self.model.split(':')[0].lower()
            for m in installed:
                if m.split(':')[0].lower() == base_model:
                    return m

            # 3. Preference hierarchy: fast non-thinking instruct models first
            for pref in ["qwen2.5", "gemma3", "gemma", "llama3.2", "qwen", "llama", "mistral"]:
                for m in installed:
                    if pref in m.lower():
                        return m

            return installed[0]
        except Exception:
            return self.model

    @staticmethod
    def _clean_output(text: str) -> str:
        """Strip internal reasoning blocks like <think>...</think> if emitted by the model."""
        import re
        if not text:
            return ""
        # If closing </think> is present, reasoning is everything before it
        if '</think>' in text:
            text = text.split('</think>')[-1]
        # If opening <think> is present without closing
        if '<think>' in text:
            text = text.split('<think>')[0]
        # Regex clean any remaining tags
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        return text.strip()

    def _get_default_options(self, custom_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        num_threads = max(4, min(16, os.cpu_count() or 6))
        opts: Dict[str, Any] = {
            "temperature": settings.llm_temperature,
            "num_predict": settings.llm_num_predict,
            "num_ctx": 1536,
            "num_thread": num_threads,
            "top_k": 20,
            "top_p": 0.85
        }
        if custom_opts:
            opts.update(custom_opts)
        return opts

    def list_installed_models(self) -> List[str]:
        """List all installed local models in Ollama."""
        try:
            client = self._get_client()
            res = client.list()
            installed = []
            if hasattr(res, 'models'):
                installed = [getattr(m, 'model', None) or getattr(m, 'name', '') for m in res.models]
            elif isinstance(res, dict) and 'models' in res:
                installed = [m.get('model') or m.get('name') for m in res['models']]
            return [m for m in installed if m]
        except Exception:
            return [self.model]

    def set_active_model(self, model_name: str) -> str:
        """Set the active LLM model."""
        self.model = model_name
        settings.llm_model = model_name
        return self.model

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        keep_alive: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Non-streaming generation."""
        client = self._get_client()
        active_model = self._resolve_model(client)
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        opts = self._get_default_options(options)
        kwargs = {
            "model": active_model,
            "messages": messages,
            "options": opts,
            "keep_alive": keep_alive or settings.llm_keep_alive
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
    ) -> str:
        """Non-streaming fast chat."""
        client = self._get_client()
        active_model = self._resolve_model(client)
        
        opts = self._get_default_options(options)
        kwargs = {
            "model": active_model,
            "messages": messages,
            "options": opts,
            "keep_alive": keep_alive or settings.llm_keep_alive_chat
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
    ) -> Generator[str, None, None]:
        """Streaming chat completion for interactive use."""
        client = self._get_client()
        active_model = self._resolve_model(client)
        
        opts = self._get_default_options(options)
        kwargs = {
            "model": active_model,
            "messages": messages,
            "stream": True,
            "options": opts,
            "keep_alive": keep_alive or settings.llm_keep_alive_chat
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
