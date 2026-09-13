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

        opts = options or {}
        # Ensure default behavior for 4GB VRAM
        if "temperature" not in opts:
            opts["temperature"] = settings.llm_temperature
            
        kwargs = {
            "model": active_model,
            "messages": messages,
            "options": opts,
        }
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        else:
            kwargs["keep_alive"] = settings.llm_keep_alive

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
        """Non-streaming chat."""
        client = self._get_client()
        active_model = self._resolve_model(client)
        
        opts = options or {}
        if "temperature" not in opts:
            opts["temperature"] = settings.llm_temperature
            
        kwargs = {
            "model": active_model,
            "messages": messages,
            "options": opts,
        }
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        else:
            kwargs["keep_alive"] = settings.llm_keep_alive_chat

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
        
        opts = options or {}
        if "temperature" not in opts:
            opts["temperature"] = settings.llm_temperature
        if "num_predict" not in opts:
            opts["num_predict"] = settings.llm_num_predict
            
        kwargs = {
            "model": active_model,
            "messages": messages,
            "stream": True,
            "options": opts,
        }
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        else:
            kwargs["keep_alive"] = settings.llm_keep_alive_chat

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
