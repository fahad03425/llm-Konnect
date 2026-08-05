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

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        keep_alive: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Non-streaming generation."""
        client = self._get_client()
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        opts = options or {}
        # Ensure default behavior for 4GB VRAM
        if "temperature" not in opts:
            opts["temperature"] = settings.llm_temperature
            
        kwargs = {
            "model": self.model,
            "messages": messages,
            "options": opts,
        }
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        else:
            kwargs["keep_alive"] = settings.llm_keep_alive

        try:
            response = client.chat(**kwargs)
            return response["message"]["content"]
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
        
        opts = options or {}
        if "temperature" not in opts:
            opts["temperature"] = settings.llm_temperature
            
        kwargs = {
            "model": self.model,
            "messages": messages,
            "options": opts,
        }
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        else:
            kwargs["keep_alive"] = settings.llm_keep_alive_chat

        try:
            response = client.chat(**kwargs)
            return response["message"]["content"]
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
        
        opts = options or {}
        if "temperature" not in opts:
            opts["temperature"] = settings.llm_temperature
        if "num_predict" not in opts:
            opts["num_predict"] = settings.llm_num_predict
            
        kwargs = {
            "model": self.model,
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
            for chunk in stream:
                if "message" in chunk and "content" in chunk["message"]:
                    yield chunk["message"]["content"]
        except Exception as e:
            # We want to be graceful if Ollama is down
            raise RuntimeError(f"Ollama chat failed: {str(e)}")

llm = LLMService()
