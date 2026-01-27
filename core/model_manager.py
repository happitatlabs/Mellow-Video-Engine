"""
Model Manager - VRAM Management
===============================
Handles loading and unloading of AI models to prevent OOM errors.
Critical for 16GB VRAM constraint.
"""

from __future__ import annotations

import gc
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type variable for model instances
T = TypeVar("T")


class ModelType(Enum):
    """Types of models managed by the system."""
    WHISPER = "whisper"
    LLM = "llm"
    IMAGE_GEN = "image_gen"  # Flux, SD, etc.
    VIDEO_GEN = "video_gen"  # SVD, LTX-2


@dataclass
class ModelInfo:
    """Information about a loaded model."""
    model_type: ModelType
    model_name: str
    estimated_vram_gb: float
    instance: Any = None
    is_loaded: bool = False
    load_count: int = 0


class ModelLoader(ABC):
    """Abstract base class for model loaders."""

    @abstractmethod
    def load(self, config: dict) -> Any:
        """Load the model and return the instance."""
        pass

    @abstractmethod
    def unload(self, instance: Any) -> None:
        """Unload the model and free resources."""
        pass

    @abstractmethod
    def get_vram_estimate(self) -> float:
        """Get estimated VRAM usage in GB."""
        pass


class WhisperLoader(ModelLoader):
    """Loader for Whisper speech recognition model."""

    def __init__(self, model_size: str = "large-v3"):
        self.model_size = model_size
        self._vram_estimates = {
            "tiny": 1.0,
            "base": 1.5,
            "small": 2.5,
            "medium": 5.0,
            "large-v2": 10.0,
            "large-v3": 10.0,
        }

    def load(self, config: dict) -> Any:
        """Load faster-whisper model."""
        try:
            from faster_whisper import WhisperModel

            model_size = config.get("model_size", self.model_size)
            device = config.get("device", "cuda")
            compute_type = config.get("compute_type", "float16")

            logger.info(f"Loading Whisper model: {model_size} on {device}")

            model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
            )

            logger.info("Whisper model loaded successfully")
            return model

        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise

    def unload(self, instance: Any) -> None:
        """Unload Whisper model."""
        if instance is not None:
            del instance
            self._cleanup_gpu_memory()
            logger.info("Whisper model unloaded")

    def get_vram_estimate(self) -> float:
        return self._vram_estimates.get(self.model_size, 10.0)

    def _cleanup_gpu_memory(self) -> None:
        """Force GPU memory cleanup."""
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except ImportError:
            pass


class LLMLoader(ModelLoader):
    """Loader for Local LLM (via Ollama or vLLM)."""

    def __init__(self, provider: str = "ollama"):
        self.provider = provider

    def load(self, config: dict) -> Any:
        """Load LLM client (returns API client, not the model itself)."""
        provider = config.get("provider", self.provider)

        if provider == "ollama":
            return self._load_ollama_client(config)
        elif provider == "vllm":
            return self._load_vllm_client(config)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def _load_ollama_client(self, config: dict) -> Any:
        """Create Ollama client."""
        try:
            import httpx

            base_url = config.get("base_url", "http://localhost:11434")
            model_name = config.get("model_name", "llama3.1:8b")

            logger.info(f"Connecting to Ollama at {base_url}, model: {model_name}")

            # Return a simple client wrapper
            return {
                "type": "ollama",
                "base_url": base_url,
                "model_name": model_name,
                "client": httpx.AsyncClient(base_url=base_url, timeout=300.0),
            }

        except ImportError:
            logger.error("httpx not installed. Run: pip install httpx")
            raise

    def _load_vllm_client(self, config: dict) -> Any:
        """Create vLLM client."""
        base_url = config.get("base_url", "http://localhost:8000")
        model_name = config.get("model_name", "")

        logger.info(f"Connecting to vLLM at {base_url}")

        return {
            "type": "vllm",
            "base_url": base_url,
            "model_name": model_name,
        }

    def unload(self, instance: Any) -> None:
        """Unload LLM client."""
        if instance and isinstance(instance, dict):
            client = instance.get("client")
            if client and hasattr(client, "aclose"):
                # Note: Should be called in async context
                pass
            logger.info("LLM client unloaded")

    def get_vram_estimate(self) -> float:
        # LLM runs externally (Ollama/vLLM), minimal local VRAM
        return 0.5


class ModelManager:
    """
    Central manager for AI model lifecycle.

    Ensures only one model is loaded at a time to prevent OOM.
    Handles model loading, unloading, and VRAM tracking.
    """

    def __init__(self, max_vram_gb: float = 16.0, aggressive_gc: bool = True):
        """
        Initialize ModelManager.

        Args:
            max_vram_gb: Maximum VRAM budget in GB
            aggressive_gc: Whether to aggressively clean GPU memory
        """
        self.max_vram_gb = max_vram_gb
        self.aggressive_gc = aggressive_gc

        self._models: dict[ModelType, ModelInfo] = {}
        self._loaders: dict[ModelType, ModelLoader] = {
            ModelType.WHISPER: WhisperLoader(),
            ModelType.LLM: LLMLoader(),
        }
        self._current_vram_usage: float = 0.0

        logger.info(f"ModelManager initialized with {max_vram_gb}GB VRAM budget")

    def register_loader(self, model_type: ModelType, loader: ModelLoader) -> None:
        """Register a custom model loader."""
        self._loaders[model_type] = loader
        logger.debug(f"Registered loader for {model_type.value}")

    def get_current_vram_usage(self) -> float:
        """Get current estimated VRAM usage in GB."""
        return self._current_vram_usage

    def get_available_vram(self) -> float:
        """Get available VRAM in GB."""
        return self.max_vram_gb - self._current_vram_usage

    def is_model_loaded(self, model_type: ModelType) -> bool:
        """Check if a model is currently loaded."""
        return model_type in self._models and self._models[model_type].is_loaded

    async def load_model(
        self,
        model_type: ModelType,
        config: dict,
        force_reload: bool = False,
    ) -> Any:
        """
        Load a model, unloading others if necessary.

        Args:
            model_type: Type of model to load
            config: Model configuration
            force_reload: Force reload even if already loaded

        Returns:
            The loaded model instance
        """
        # Check if already loaded
        if model_type in self._models and self._models[model_type].is_loaded:
            if not force_reload:
                logger.info(f"{model_type.value} already loaded, reusing")
                self._models[model_type].load_count += 1
                return self._models[model_type].instance

        # Get loader
        loader = self._loaders.get(model_type)
        if not loader:
            raise ValueError(f"No loader registered for {model_type.value}")

        # Check VRAM requirement
        required_vram = loader.get_vram_estimate()
        if required_vram > self.max_vram_gb:
            raise RuntimeError(
                f"{model_type.value} requires {required_vram}GB VRAM, "
                f"but max budget is {self.max_vram_gb}GB"
            )

        # Unload other models if needed
        if self._current_vram_usage + required_vram > self.max_vram_gb:
            logger.info(
                f"VRAM budget exceeded, unloading existing models. "
                f"Current: {self._current_vram_usage}GB, Required: {required_vram}GB"
            )
            await self.unload_all()

        # Load the model
        logger.info(f"Loading {model_type.value} (estimated {required_vram}GB VRAM)")

        try:
            instance = loader.load(config)

            self._models[model_type] = ModelInfo(
                model_type=model_type,
                model_name=config.get("model_name", model_type.value),
                estimated_vram_gb=required_vram,
                instance=instance,
                is_loaded=True,
                load_count=1,
            )
            self._current_vram_usage += required_vram

            logger.info(
                f"{model_type.value} loaded. "
                f"VRAM usage: {self._current_vram_usage:.1f}/{self.max_vram_gb}GB"
            )

            return instance

        except Exception as e:
            logger.error(f"Failed to load {model_type.value}: {e}")
            raise

    async def unload_model(self, model_type: ModelType) -> None:
        """
        Unload a specific model.

        Args:
            model_type: Type of model to unload
        """
        if model_type not in self._models or not self._models[model_type].is_loaded:
            logger.debug(f"{model_type.value} not loaded, nothing to unload")
            return

        model_info = self._models[model_type]
        loader = self._loaders.get(model_type)

        logger.info(f"Unloading {model_type.value}")

        if loader:
            loader.unload(model_info.instance)

        self._current_vram_usage -= model_info.estimated_vram_gb
        self._current_vram_usage = max(0, self._current_vram_usage)

        model_info.instance = None
        model_info.is_loaded = False

        if self.aggressive_gc:
            self._aggressive_cleanup()

        logger.info(
            f"{model_type.value} unloaded. "
            f"VRAM usage: {self._current_vram_usage:.1f}/{self.max_vram_gb}GB"
        )

    async def unload_all(self) -> None:
        """Unload all loaded models."""
        logger.info("Unloading all models")

        for model_type in list(self._models.keys()):
            await self.unload_model(model_type)

        if self.aggressive_gc:
            self._aggressive_cleanup()

        self._current_vram_usage = 0.0
        logger.info("All models unloaded")

    def get_model(self, model_type: ModelType) -> Optional[Any]:
        """
        Get a loaded model instance.

        Args:
            model_type: Type of model to retrieve

        Returns:
            Model instance if loaded, None otherwise
        """
        if model_type in self._models and self._models[model_type].is_loaded:
            return self._models[model_type].instance
        return None

    def _aggressive_cleanup(self) -> None:
        """Perform aggressive GPU memory cleanup."""
        gc.collect()

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                torch.cuda.ipc_collect()
                logger.debug("PyTorch CUDA cache cleared")
        except ImportError:
            pass

        # Force Python GC multiple times
        for _ in range(3):
            gc.collect()

    def get_status(self) -> dict:
        """Get current model manager status."""
        return {
            "max_vram_gb": self.max_vram_gb,
            "current_vram_usage_gb": self._current_vram_usage,
            "available_vram_gb": self.get_available_vram(),
            "loaded_models": {
                mt.value: {
                    "name": info.model_name,
                    "vram_gb": info.estimated_vram_gb,
                    "load_count": info.load_count,
                }
                for mt, info in self._models.items()
                if info.is_loaded
            },
        }


# ============================================================================
# Context Manager for Model Usage
# ============================================================================

class ModelContext:
    """
    Context manager for safe model usage.

    Usage:
        async with ModelContext(manager, ModelType.WHISPER, config) as model:
            result = model.transcribe(audio)
    """

    def __init__(
        self,
        manager: ModelManager,
        model_type: ModelType,
        config: dict,
        auto_unload: bool = True,
    ):
        self.manager = manager
        self.model_type = model_type
        self.config = config
        self.auto_unload = auto_unload
        self._model = None

    async def __aenter__(self) -> Any:
        self._model = await self.manager.load_model(self.model_type, self.config)
        return self._model

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.auto_unload:
            await self.manager.unload_model(self.model_type)
        return False
