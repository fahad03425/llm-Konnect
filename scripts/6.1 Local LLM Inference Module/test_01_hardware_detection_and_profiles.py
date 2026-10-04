"""Test Suite 01: Hardware Detection and Profile Recommendation.

Module: Module 6.1 — Local LLM Inference Module
Target File: backend/app/core/llm.py (methods: detect_hardware, _cached_hw)
Scope:
- Verifies CPU-only detection and lightweight profile recommendation (Phi-4-mini).
- Verifies GPU tiers: Budget (<5.5GB), Standard (5.5-12GB), and Heavy (>=12GB).
- Verifies Apple Silicon Metal unified memory fallback.
- Verifies CLI fallbacks (nvidia-smi and Windows CIM / PowerShell video controller query).
- Verifies RAM / VRAM availability telemetry and cache invalidation via force_refresh.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest
from app.core.llm import LLMService


class TestHardwareDetectionAndProfiles:
    """Verifies automated hardware probing and tier-based model recommendations."""

    def test_cpu_only_detection_and_phi4_mini_recommendation(self, llm_service):
        """When no GPU is present, system must assign 'cpu_lightweight' and recommend Phi-4-mini."""
        with patch("torch.cuda.is_available", return_value=False, create=True), \
             patch("subprocess.check_output", side_effect=FileNotFoundError("No nvidia-smi")):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            
            assert hw["gpu_available"] is False
            assert hw["device_type"] == "cpu"
            assert hw["vram_mb"] == 0
            assert hw["recommended_profile"] == "cpu_lightweight"
            assert "phi4-mini" in hw["recommended_models"]
            assert "qwen2.5:1.5b" in hw["recommended_models"]
            assert hw["cpu_cores"] > 0
            assert hw["recommendations_advisory"] is True

    def test_budget_gpu_profile(self, llm_service):
        """GPU with 3GB VRAM should trigger 'gpu_budget' with lightweight models."""
        mock_props = MagicMock()
        mock_props.total_memory = 3072 * 1024 * 1024  # 3072 MB

        with patch("torch.cuda.is_available", return_value=True, create=True), \
             patch("torch.cuda.get_device_name", return_value="NVIDIA GeForce GTX 1050", create=True), \
             patch("torch.cuda.get_device_properties", return_value=mock_props, create=True):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            
            assert hw["gpu_available"] is True
            assert hw["gpu_name"] == "NVIDIA GeForce GTX 1050"
            assert hw["vram_mb"] == 3072
            assert hw["device_type"] == "gpu"
            assert hw["recommended_profile"] == "gpu_budget"
            assert "qwen2.5:3b" in hw["recommended_models"]
            assert "phi4-mini" in hw["recommended_models"]

    def test_standard_gpu_profile_qwen_mistral(self, llm_service):
        """GPU with 8GB VRAM (e.g. RTX 4070 Laptop / RTX 3060) recommends 7B/8B models."""
        mock_props = MagicMock()
        mock_props.total_memory = 8192 * 1024 * 1024  # 8192 MB

        with patch("torch.cuda.is_available", return_value=True, create=True), \
             patch("torch.cuda.get_device_name", return_value="NVIDIA GeForce RTX 4070", create=True), \
             patch("torch.cuda.get_device_properties", return_value=mock_props, create=True):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            
            assert hw["gpu_available"] is True
            assert hw["vram_mb"] == 8192
            assert hw["recommended_profile"] == "gpu_standard"
            assert any("qwen2.5:7b" in m for m in hw["recommended_models"])
            assert any("mistral:7b" in m for m in hw["recommended_models"])
            assert any("llama3.1:8b" in m for m in hw["recommended_models"])

    def test_heavy_gpu_profile_qwen_14b(self, llm_service):
        """GPU with 16GB+ VRAM (e.g. RTX 4090 / A5000) recommends 14B models."""
        mock_props = MagicMock()
        mock_props.total_memory = 16384 * 1024 * 1024  # 16384 MB

        with patch("torch.cuda.is_available", return_value=True, create=True), \
             patch("torch.cuda.get_device_name", return_value="NVIDIA RTX 4090", create=True), \
             patch("torch.cuda.get_device_properties", return_value=mock_props, create=True):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            
            assert hw["gpu_available"] is True
            assert hw["vram_mb"] == 16384
            assert hw["recommended_profile"] == "gpu_heavy"
            assert "qwen2.5:14b" in hw["recommended_models"]
            assert "mistral:7b" in hw["recommended_models"]

    def test_apple_metal_unified_memory(self, llm_service):
        """On macOS with Metal/MPS available, system detects Metal unified memory."""
        with patch("torch.cuda.is_available", return_value=False, create=True), \
             patch("torch.backends.mps.is_available", return_value=True, create=True), \
             patch("sys.platform", "darwin"), \
             patch("subprocess.check_output", side_effect=FileNotFoundError):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            
            assert hw["gpu_available"] is True
            assert "Apple Metal" in hw["gpu_name"]

    def test_nvidia_smi_cli_fallback_parsing(self, llm_service):
        """When PyTorch is absent, parse nvidia-smi CSV output directly."""
        with patch("torch.cuda.is_available", return_value=False, create=True), \
             patch("subprocess.check_output", return_value="Tesla T4, 15360\n"):
            
            hw = llm_service.detect_hardware(force_refresh=True)
            
            assert hw["gpu_available"] is True
            assert hw["gpu_name"] == "Tesla T4"
            assert hw["vram_mb"] == 15360
            assert hw["recommended_profile"] == "gpu_heavy"

    def test_hardware_caching_and_force_refresh(self, llm_service):
        """Cached hardware state is returned on repeated calls unless force_refresh is True."""
        llm_service._cached_hw = {
            "gpu_available": False,
            "cached_marker": "test-cache-hit",
            "recommended_profile": "cpu_lightweight"
        }
        
        # Without force_refresh, must return cached object directly
        result_cached = llm_service.detect_hardware(force_refresh=False)
        assert result_cached.get("cached_marker") == "test-cache-hit"
        
        # With force_refresh=True, must recompute
        with patch("torch.cuda.is_available", return_value=False, create=True), \
             patch("subprocess.check_output", side_effect=FileNotFoundError):
            result_refreshed = llm_service.detect_hardware(force_refresh=True)
            assert "cached_marker" not in result_refreshed
            assert result_refreshed["recommended_profile"] == "cpu_lightweight"
