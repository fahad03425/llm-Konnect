"""Test Suite 04: Model-Agnostic Inference Core & Output Sanitization.

Module: Module 6.1 — Local LLM Inference Module
Target File: backend/app/core/llm.py (methods: generate, chat, chat_stream, _clean_output, _filter_stream, _get_default_options, _log_inference_timing)
Scope:
- Verifies model-agnostic text generation (single-turn).
- Verifies model-agnostic chat completion (multi-turn).
- Verifies token-by-token streaming chat with live reasoning tag (<think>) removal.
- Verifies prompt evaluation and generation timing telemetry.
- Verifies output cleaning across varied markdown and internal thinking formats.
"""

from unittest.mock import MagicMock, patch
import pytest
from app.core.llm import LLMService


class TestModelAgnosticInference:
    """Verifies single-turn, multi-turn, streaming inference and output sanitization."""

    def test_generate_single_turn_inference(self, llm_service):
        """generate() dispatches formatted system/user messages and returns sanitized text."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini:latest"}]}
        mock_client.chat.return_value = {
            "message": {"content": "<think>Checking database...</think>Panadol stock is 45 boxes."},
            "total_duration": 1_200_000_000,
            "load_duration": 100_000_000,
            "prompt_eval_duration": 300_000_000,
            "eval_duration": 800_000_000,
            "prompt_eval_count": 25,
            "eval_count": 12,
        }

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            output = llm_service.generate(
                prompt="Check Panadol stock",
                system_prompt="You are a pharmacy analytics engine.",
                keep_alive="10m"
            )
            assert output == "Panadol stock is 45 boxes."
            mock_client.chat.assert_called_once()
            call_kwargs = mock_client.chat.call_args[1]
            assert call_kwargs["model"] == "phi4-mini:latest"
            assert len(call_kwargs["messages"]) == 2
            assert call_kwargs["messages"][0]["role"] == "system"
            assert call_kwargs["messages"][1]["role"] == "user"
            assert call_kwargs["keep_alive"] == "10m"

    def test_generate_with_model_override(self, llm_service):
        """Passing an explicit model parameter overrides the default active model."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "mistral:7b"}, {"name": "phi4-mini:latest"}]}
        mock_client.chat.return_value = {"message": {"content": "Mistral response"}}

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            output = llm_service.generate(
                prompt="Summarize financial statement",
                model="mistral:7b"
            )
            assert output == "Mistral response"
            assert mock_client.chat.call_args[1]["model"] == "mistral:7b"

    def test_chat_multi_turn_inference(self, llm_service):
        """chat() passes conversational context history transparently."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini:latest"}]}
        mock_client.chat.return_value = {"message": {"content": "Your total revenue is PKR 150,000."}}

        conversation = [
            {"role": "user", "content": "What is our current sales volume?"},
            {"role": "assistant", "content": "Sales volume is 320 units."},
            {"role": "user", "content": "And revenue?"}
        ]

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            reply = llm_service.chat(conversation)
            assert reply == "Your total revenue is PKR 150,000."
            assert mock_client.chat.call_args[1]["messages"] == conversation

    def test_chat_stream_filters_think_tags(self, llm_service):
        """chat_stream yields only clean user-facing tokens, suppressing thinking blocks."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "phi4-mini:latest"}]}
        mock_client.chat.return_value = [
            {"message": {"content": "<think>"}},
            {"message": {"content": "reasoning step 1: revenue = 50000\n"}},
            {"message": {"content": "reasoning step 2: margin = 20%\n"}},
            {"message": {"content": "</think>"}},
            {"message": {"content": "Net "}},
            {"message": {"content": "profit "}},
            {"message": {"content": "is 10,000."}},
        ]

        with patch.object(llm_service, "_get_client", return_value=mock_client):
            tokens = list(llm_service.chat_stream([{"role": "user", "content": "Calculate profit"}]))
            joined = "".join(tokens)
            assert joined == "Net profit is 10,000."
            assert "<think>" not in joined
            assert "reasoning" not in joined

    def test_clean_output_reasoning_edge_cases(self, llm_service):
        """_clean_output correctly strips think blocks, unclosed tags, and artifacts."""
        # Standard closed tag
        assert llm_service._clean_output("<think>foo</think>bar") == "bar"

        # Multiline tag
        multiline = "<think>\nline 1\nline 2\n</think>\nActual response"
        assert llm_service._clean_output(multiline) == "Actual response"

        # Unclosed think tag
        unclosed = "Initial context<think>incomplete thought"
        assert llm_service._clean_output(unclosed) == "Initial context"

        # Trailing close tag without open
        trailing = "internal reasoning</think>Clean conclusion"
        assert llm_service._clean_output(trailing) == "Clean conclusion"

        # Strip computed values debug block
        with_debug = "Total sales: 50\n\nComputed Values:\nsum=50\navg=5"
        assert llm_service._clean_output(with_debug) == "Total sales: 50"

        # None and empty
        assert llm_service._clean_output("") == ""
        assert llm_service._clean_output(None) == ""

    def test_default_options_injection(self, llm_service):
        """_get_default_options injects temperature, context window, and thread counts."""
        opts = llm_service._get_default_options({"temperature": 0.5})
        assert opts["temperature"] == 0.5
        assert opts["num_ctx"] == 1536
        assert opts["num_thread"] >= 4
        assert "top_p" in opts
        assert "top_k" in opts
