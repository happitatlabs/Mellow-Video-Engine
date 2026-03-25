"""
LLM Service - Ollama Integration

This module provides integration with Ollama for local LLM inference.
Supports streaming responses, context management, and multiple models.

Features:
    - Streaming and non-streaming generation
    - Context/history management
    - Multiple model support (fast/thinking modes)
    - GPU lock integration with Orchestrator

Connection:
    - Default: http://localhost:11434
    - Uses Ollama REST API
"""

import asyncio
import aiohttp
import json
import logging
import os
import re
import time
import uuid
import traceback
from typing import Optional, Dict, Any, List, AsyncGenerator, Callable, Awaitable, Union

# Optional callbacks for metrics (TTFT, TPS, tokens); never block request path
OnFirstTokenT = Optional[Callable[[float], None]]  # ttft_ms
OnDoneT = Optional[Callable[[int, int, float], None]]  # tokens_out, tokens_in, duration_sec
from dataclasses import dataclass, field
from enum import Enum, auto
from datetime import datetime

logger = logging.getLogger(__name__)


# =============================================================================
# Enums and Data Classes
# =============================================================================

class LLMStatus(Enum):
    """LLM service status."""

    DISCONNECTED = auto()  # Not connected to Ollama
    CONNECTED = auto()     # Connected, ready for requests
    GENERATING = auto()    # Currently generating response
    ERROR = auto()         # Error state


class ModelType(Enum):
    """Model type for different use cases."""

    FAST = "fast"           # Lightweight model for quick responses
    THINKING = "thinking"   # Main model for deep reasoning
    RESEARCH = "research"   # Model with web search capability


@dataclass
class ChatMessage:
    """
    Single message in conversation.

    Attributes:
        role: 'system', 'user', or 'assistant'
        content: Message content
        timestamp: When message was created
    """
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, str]:
        """Convert to Ollama message format."""
        return {"role": self.role, "content": self.content}


@dataclass
class ChatContext:
    """
    Conversation context for chat sessions.

    Attributes:
        system_prompt: System instructions
        messages: Conversation history
        max_history: Maximum messages to retain
    """
    system_prompt: str = ""
    messages: List[ChatMessage] = field(default_factory=list)
    max_history: int = 20

    def add_message(self, role: str, content: str) -> None:
        """Add message to history."""
        self.messages.append(ChatMessage(role=role, content=content))
        # Trim history if needed
        if len(self.messages) > self.max_history:
            # Keep system context intact, trim oldest user/assistant pairs
            self.messages = self.messages[-self.max_history:]

    def get_messages(self) -> List[Dict[str, str]]:
        """Get messages in Ollama format."""
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend([m.to_dict() for m in self.messages])
        return msgs

    def clear(self) -> None:
        """Clear conversation history."""
        self.messages.clear()


@dataclass
class LLMRequest:
    """
    Request structure for LLM inference.

    Attributes:
        prompt: User prompt or message
        model: Model name (e.g., 'llama3', 'mistral')
        system_prompt: Optional system message
        context: Conversation context for multi-turn
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        stream: Whether to stream response
    """

    prompt: str
    model: str = "llama3"
    system_prompt: Optional[str] = None
    context: Optional[List[int]] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    stream: bool = True


@dataclass
class LLMResponse:
    """
    Response structure from LLM inference.

    Attributes:
        text: Generated text response
        model: Model that generated the response
        context: Updated context for multi-turn
        tokens_generated: Number of tokens generated
        generation_time_ms: Time to generate in milliseconds
        is_complete: Whether generation finished normally
        tool_calls: List of tool calls if model supports function calling
    """

    text: str
    model: str
    context: Optional[List[int]] = None
    tokens_generated: int = 0
    generation_time_ms: float = 0.0
    is_complete: bool = True
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class GenerationResult:
    """
    Result from LLM generation.

    Attributes:
        content: Generated text
        model: Model used
        total_duration_ms: Total time including loading
        eval_count: Number of tokens generated
        eval_duration_ms: Time spent generating
        prompt_eval_count: Number of prompt tokens
        tool_calls: List of tool calls if model supports function calling
    """
    content: str
    model: str
    total_duration_ms: float = 0.0
    eval_count: int = 0
    eval_duration_ms: float = 0.0
    prompt_eval_count: int = 0
    done_reason: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None

    @property
    def tokens_per_second(self) -> float:
        """Calculate generation speed."""
        if self.eval_duration_ms > 0:
            return (self.eval_count / self.eval_duration_ms) * 1000
        return 0.0


class LLMServiceError(Exception):
    """Exception for LLM service failures."""
    pass


class LLMServiceTimeoutError(LLMServiceError):
    """Structured timeout raised when an LLM request exceeds its effective timeout."""

    def __init__(
        self,
        *,
        mode: str,
        model: str,
        timeout_seconds: float,
        effective_timeout_source: str,
        elapsed_ms: int,
        prompt_chars: int,
    ) -> None:
        self.mode = mode
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.effective_timeout_source = effective_timeout_source
        self.elapsed_ms = elapsed_ms
        self.prompt_chars = prompt_chars
        super().__init__(
            "LLM request timed out "
            f"(mode={mode}, model={model}, timeout_seconds={timeout_seconds}, "
            f"effective_timeout_source={effective_timeout_source}, elapsed_ms={elapsed_ms})"
        )


# =============================================================================
# LLM Service Class
# =============================================================================

class LLMService:
    """
    Service for LLM inference via Ollama.

    Handles:
        - Streaming and non-streaming generation
        - Multiple model management
        - Context/history tracking
        - GPU resource coordination

    Usage:
        service = LLMService()
        await service.connect()

        # Non-streaming
        result = await service.generate("Hello!")

        # Streaming
        async for chunk in service.generate_stream("Hello!"):
            print(chunk, end="")

        await service.disconnect()
    """

    DEFAULT_HOST: str = "localhost"
    DEFAULT_PORT: int = 11434
    DEFAULT_TIMEOUT: float = 120.0  # 120 seconds (increased for long inference)

    # Model configuration (설치된 모델에 맞게 설정)
    DEFAULT_MODELS = {
        ModelType.FAST: "qwen2.5:7b",      # Fast path는 VRAM 절약 우선
        ModelType.THINKING: "qwen3.5:9b",  # Tool Calling 공식 지원
        ModelType.RESEARCH: "qwen3.5:9b",  # Tool Calling 공식 지원
    }

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        models: Optional[Dict[ModelType, str]] = None,
        keep_alive: Optional[str] = None
    ):
        """
        Initialize LLM Service.

        Args:
            host: Ollama server hostname
            port: Ollama server port
            timeout: Request timeout in seconds
            models: Model mapping for different types
            keep_alive: Model keep_alive duration (e.g., "5m", "10m", "-1" for infinite)
                        If None, reads from OLLAMA_KEEP_ALIVE environment variable
        """
        self.host = host
        self.port = port
        self.timeout = timeout

        self._base_url: str = f"http://{host}:{port}"
        self._status: LLMStatus = LLMStatus.DISCONNECTED
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_loop: Optional[asyncio.AbstractEventLoop] = None

        # Model configuration
        self._models = models or self.DEFAULT_MODELS.copy()
        self._current_model: Optional[str] = None

        # Keep-alive configuration
        # Priority: parameter > environment variable > default ("5m")
        if keep_alive is None:
            keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "30s")
        self._keep_alive: str = keep_alive

        # Context management
        self._contexts: Dict[str, ChatContext] = {}
        self._default_context: ChatContext = ChatContext()

        # Generation tracking
        self._is_generating: bool = False
        self._cancel_requested: bool = False
        
        # Reconnection lock to prevent concurrent reconnects
        self._reconnect_lock: asyncio.Lock = asyncio.Lock()

    # ==================== Connection Management ====================

    def _session_matches_current_loop(self) -> bool:
        if not self._session or self._session.closed:
            return False
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        session_loop = getattr(self._session, "_loop", None) or self._session_loop
        return session_loop is None or session_loop is current_loop

    async def _ensure_session(self) -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError as e:
            raise RuntimeError("LLMService requires a running asyncio task/loop") from e

        if self._session and not self._session.closed:
            session_loop = getattr(self._session, "_loop", None) or self._session_loop
            if session_loop is not None and session_loop is not current_loop:
                logger.warning(
                    "[LLMService] Recreating HTTP session because event loop changed (old=%s, new=%s)",
                    id(session_loop),
                    id(current_loop),
                )
                await self._cleanup_session(reason="session loop mismatch")
                self._status = LLMStatus.DISCONNECTED

        if not self._session or self._session.closed:
            logger.info(f"[LLMService] Creating HTTP session for Ollama at {self._base_url}")
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            self._session_loop = current_loop
            logger.debug(f"[LLMService] Created session, timeout={self.timeout}s")

    async def _prepare_model_for_request(self, requested_model: str) -> None:
        """
        Ensure only one Ollama model stays resident on GPU at a time.
        If the requested model differs from the currently tracked model, unload the old one first.
        """
        active_model = self._current_model
        if active_model and active_model != requested_model:
            logger.info("[LLMService] Switching model %s -> %s; unloading previous model first", active_model, requested_model)
            await self.unload_model()
        await self.cleanup_stale_models(current_model=requested_model)
        self._current_model = requested_model

    def _should_unload_after_request(self) -> bool:
        return os.getenv("ENABLE_MODEL_UNLOAD_ON_IDLE", "1").strip().lower() not in {"0", "false", "no", "off", "disabled"}

    async def _run_ollama_command(self, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "ollama",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode("utf-8", errors="ignore"), stderr.decode("utf-8", errors="ignore")

    async def list_loaded_models(self) -> List[str]:
        """
        Return currently loaded Ollama models from `ollama ps`.
        """
        try:
            rc, stdout, stderr = await self._run_ollama_command("ps")
            if rc != 0:
                logger.debug("[LLMService] `ollama ps` failed: %s", stderr.strip())
                return []
            models: List[str] = []
            for raw_line in stdout.splitlines():
                line = raw_line.strip()
                if not line or line.lower().startswith("name") or line.startswith("MODEL"):
                    continue
                cols = re.split(r"\s{2,}", line)
                if cols:
                    model_name = cols[0].strip()
                    if model_name and model_name.lower() != "name":
                        models.append(model_name)
            return models
        except FileNotFoundError:
            logger.warning("[LLMService] `ollama` executable not found; stale model cleanup skipped")
            return []
        except Exception as e:
            logger.debug("[LLMService] Failed to list loaded models: %s", e)
            return []

    async def cleanup_stale_models(self, current_model: Optional[str] = None) -> List[str]:
        """
        Stop all loaded Ollama models except the current model to keep GPU residency to 0-1 models.
        Returns the list of models that remain after cleanup.
        """
        loaded_models = await self.list_loaded_models()
        stale_models = [name for name in loaded_models if current_model is None or name != current_model]
        for model_name in stale_models:
            try:
                rc, _, stderr = await self._run_ollama_command("stop", model_name)
                if rc == 0:
                    logger.info("[LLMService] Stale Ollama model stopped: %s", model_name)
                else:
                    logger.warning("[LLMService] Failed to stop stale Ollama model %s: %s", model_name, stderr.strip())
            except Exception as e:
                logger.warning("[LLMService] Error stopping stale Ollama model %s: %s", model_name, e)
        remaining = await self.list_loaded_models()
        logger.info("[LLMService] Ollama loaded models after cleanup: %s", remaining if remaining else "(none)")
        return remaining

    async def unload_all_models(self) -> List[str]:
        """
        Clean-slate startup helper: stop every loaded Ollama model.
        """
        self._current_model = None
        return await self.cleanup_stale_models(current_model=None)

    async def connect(self) -> bool:
        """
        Establish connection to Ollama server.
        
        Uses a SINGLE persistent HTTP client session for the app lifetime.
        Creates session if not exists, reuses existing session if valid.

        Returns:
            True if connection successful

        Raises:
            ConnectionError: If Ollama server is unreachable
        """
        await self._ensure_session()
        if self._session and not self._session.closed:
            logger.debug("[LLMService] Reusing existing session")
        
        try:
            # Test connection with existing session
            test_url = f"{self._base_url}/api/tags"
            logger.debug(f"[LLMService] Testing connection to {test_url}")
            async with self._session.get(test_url) as resp:
                logger.debug(f"[LLMService] Response status: {resp.status}")
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"[LLMService] Ollama returned status {resp.status}: {error_text[:200]}")
                    raise ConnectionError(f"Ollama returned status {resp.status}: {error_text[:200]}")
                data = await resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                logger.info(f"[LLMService] Connected. Available models: {models}")

            self._status = LLMStatus.CONNECTED
            logger.info(f"[LLMService] Connection successful, status set to CONNECTED")
            return True

        except aiohttp.ClientConnectorError as e:
            logger.error(
                f"[LLMService] Connection failed (ClientConnectorError): {e}. "
                f"Check if Ollama is running at {self._base_url}"
            )
            self._status = LLMStatus.ERROR
            # Do NOT close session on connection error - keep it for retry
            raise ConnectionError(f"Failed to connect to Ollama at {self._base_url}: {e}")
        except aiohttp.ClientError as e:
            logger.error(f"[LLMService] Connection failed (ClientError): {e}", exc_info=True)
            self._status = LLMStatus.ERROR
            # Do NOT close session on connection error - keep it for retry
            raise ConnectionError(f"Failed to connect to Ollama: {e}")
        except asyncio.TimeoutError as e:
            logger.error(
                f"[LLMService] Connection timeout after {self.timeout}s. "
                f"Check if Ollama is responding at {self._base_url}"
            )
            self._status = LLMStatus.ERROR
            # Do NOT close session on timeout - keep it for retry
            raise ConnectionError(f"Connection to Ollama timed out after {self.timeout}s")
        except Exception as e:
            logger.error(f"[LLMService] Unexpected connection error: {e}", exc_info=True)
            self._status = LLMStatus.ERROR
            raise
    
    async def _cleanup_session(self, reason: str = "unknown") -> None:
        """
        Clean up HTTP session safely.
        
        Called only during shutdown/disconnect. Session is persistent during app lifetime.
        
        Args:
            reason: Reason for cleanup (for debugging)
        """
        if self._session and not self._session.closed:
            try:
                # Check if event loop is available
                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_closed():
                        logger.warning("[LLMService] Event loop is closed, cannot cleanup session properly")
                        self._session = None
                        return
                except RuntimeError:
                    # No event loop available
                    logger.warning("[LLMService] Cannot access event loop, skipping session cleanup")
                    self._session = None
                    return
                
                await self._session.close()
                logger.debug(f"[LLMService] Session closed (reason: {reason})")
            except RuntimeError as e:
                # Event loop is closed
                logger.warning(f"[LLMService] Event loop closed during cleanup: {e}")
            except Exception as e:
                logger.warning(f"[LLMService] Error during session cleanup: {e}")
            finally:
                self._session = None
                self._session_loop = None
    
    def __del__(self):
        """소멸자: 세션이 열려있으면 경고 로그만 출력 (비동기 정리는 불가능)"""
        if hasattr(self, '_session') and self._session and not self._session.closed:
            # __del__에서는 async를 호출할 수 없으므로 경고만 출력
            logger.warning(
                "[LLMService] Session was not closed before destruction. "
                "Please call disconnect() explicitly or use async context manager."
            )

    async def disconnect(self) -> None:
        """Close connection to Ollama server and cleanup session."""
        logger.info("[LLMService] Disconnecting...")
        # Acquire lock to prevent concurrent operations during shutdown
        async with self._reconnect_lock:
            await self._cleanup_session(reason="disconnect() called")
            self._status = LLMStatus.DISCONNECTED
        logger.info("[LLMService] Disconnected")

    async def health_check(self) -> bool:
        """
        Check if Ollama server is healthy.
        
        Updates internal status based on check result.

        Returns:
            True if server responds
        """
        if not self._session or self._session.closed:
            self._status = LLMStatus.DISCONNECTED
            return False

        try:
            async with self._session.get(
                f"{self._base_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    # 상태가 DISCONNECTED였다면 CONNECTED로 복구
                    if self._status == LLMStatus.DISCONNECTED:
                        self._status = LLMStatus.CONNECTED
                    return True
                else:
                    self._status = LLMStatus.DISCONNECTED
                    return False
        except Exception:
            self._status = LLMStatus.DISCONNECTED
            return False

    def get_status(self) -> LLMStatus:
        """Get current service status."""
        return self._status

    def is_ready(self) -> bool:
        """Check if service is ready to accept requests."""
        return self._status == LLMStatus.CONNECTED

    def is_available(self) -> bool:
        """Check if service is available (alias for orchestrator compatibility)."""
        return self._status in (LLMStatus.CONNECTED, LLMStatus.GENERATING)

    async def _ensure_connected(self, max_retries: int = 1) -> bool:
        """
        Ensure connection to Ollama server with reconnect lock.
        
        Uses a lock to prevent concurrent reconnection attempts.
        Reuses existing session if valid, creates new session only if needed.
        
        Args:
            max_retries: Maximum retry attempts (default: 1)
            
        Returns:
            True if connected (or reconnected), False if failed
        """
        # Fast path: already connected
        if self._session_matches_current_loop() and self._status == LLMStatus.CONNECTED:
            return True
        
        # Acquire reconnect lock to prevent concurrent reconnects
        if self._reconnect_lock.locked():
            logger.debug("[LLMService] Reconnect already in progress, waiting...")
            # Wait for ongoing reconnect, then check status
            async with self._reconnect_lock:
                return self._session_matches_current_loop() and self._status == LLMStatus.CONNECTED
        
        async with self._reconnect_lock:
            # Double-check after acquiring lock
            if self._session_matches_current_loop() and self._status == LLMStatus.CONNECTED:
                return True
            
            logger.warning(
                f"[LLMService] Connection check failed - "
                f"session={'reusable' if self._session_matches_current_loop() else ('closed' if (not self._session or self._session.closed) else 'loop-mismatch')}, "
                f"status={self._status.name}, "
                f"attempting auto-reconnect (max_retries={max_retries})..."
            )
            
            for attempt in range(max_retries + 1):
                try:
                    logger.info(f"[LLMService] Auto-reconnect attempt {attempt + 1}/{max_retries + 1}...")
                    await self.connect()
                    if self.is_ready():
                        logger.info(f"[LLMService] Auto-reconnect successful (attempt {attempt + 1})")
                        return True
                    else:
                        logger.warning(
                            f"[LLMService] Auto-reconnect attempt {attempt + 1} completed but is_ready() returned False. "
                            f"Status: {self._status.name}"
                        )
                except ConnectionError as e:
                    logger.error(
                        f"[LLMService] Auto-reconnect attempt {attempt + 1} failed (ConnectionError): {e}"
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(0.5)  # 짧은 대기 후 재시도
                except Exception as e:
                    logger.error(
                        f"[LLMService] Auto-reconnect attempt {attempt + 1} failed (unexpected error): {e}",
                        exc_info=True
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(0.5)  # 짧은 대기 후 재시도
            
            logger.error(
                f"[LLMService] Auto-reconnect failed after all attempts. "
                f"Final status: {self._status.name}, "
                f"Session: {'closed' if (not self._session or self._session.closed) else 'open'}"
            )
            return False

    # ==================== Model Management ====================

    async def list_models(self) -> List[str]:
        """List available models on Ollama server."""
        if not self._session:
            return []

        try:
            async with self._session.get(f"{self._base_url}/api/tags") as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return [m.get("name", "") for m in data.get("models", [])]
        except Exception as e:
            logger.error(f"[LLMService] Failed to list models: {e}")
            return []

    async def load_model(self, model_name: str) -> bool:
        """
        Pre-load a model into VRAM.

        Args:
            model_name: Name of model to load

        Returns:
            True if model loaded successfully
        """
        if not self._session:
            return False

        try:
            logger.info(f"[LLMService] Loading model: {model_name} (keep_alive: {self._keep_alive})")
            # Send a minimal generate request to load model
            async with self._session.post(
                f"{self._base_url}/api/generate",
                json={"model": model_name, "prompt": "", "keep_alive": self._keep_alive}
            ) as resp:
                if resp.status == 200:
                    self._current_model = model_name
                    logger.info(f"[LLMService] Model loaded: {model_name}")
                    return True
                return False
        except Exception as e:
            logger.error(f"[LLMService] Failed to load model: {e}")
            return False

    async def unload_model(self) -> bool:
        """
        Unload current model from VRAM.
        
        Note: This method does NOT close the session or change connection status.
        It only unloads the model from VRAM while keeping the session alive.

        Returns:
            True if model was unloaded or already unloaded
        """
        if not self._current_model:
            return True  # 이미 언로드된 상태로 간주
        
        if not self._session:
            logger.debug(f"[LLMService] Cannot unload model {self._current_model}: session not available")
            self._current_model = None  # 세션이 없으면 모델 참조만 제거
            return True
        
        # 세션이 닫혔는지 확인
        if self._session.closed:
            logger.debug(f"[LLMService] Session is closed, cannot unload model {self._current_model}. Model reference cleared.")
            self._current_model = None  # 세션이 닫혔으면 모델 참조만 제거
            # 세션이 닫혔으면 상태도 DISCONNECTED로 변경하지 않음 (이미 다른 이유로 닫힌 것)
            return True  # 세션이 닫혔으면 이미 정리된 것으로 간주

        try:
            logger.info(f"[LLMService] Unloading model: {self._current_model}")
            # Set keep_alive to 0 to unload immediately
            # 중요: 세션은 유지하고 모델만 언로드
            async with self._session.post(
                f"{self._base_url}/api/generate",
                json={"model": self._current_model, "prompt": "", "keep_alive": 0}
            ) as resp:
                if resp.status == 200:
                    logger.info(f"[LLMService] Model unloaded: {self._current_model}")
                    self._current_model = None
                    # 세션은 유지하고 상태도 CONNECTED 유지 (모델만 언로드)
                    return True
                logger.warning(f"[LLMService] Model unload returned status {resp.status}")
                return False
        except Exception as e:
            # 세션이 닫혔거나 네트워크 오류인 경우 조용히 처리
            if "Session is closed" in str(e) or "closed" in str(e).lower():
                logger.debug(f"[LLMService] Session was closed during unload: {e}")
                self._current_model = None  # 모델 참조 제거
                # 세션이 닫혔지만 상태는 변경하지 않음 (다른 곳에서 처리)
                return True  # 세션이 닫혔으면 이미 정리된 것으로 간주
            logger.error(f"[LLMService] Failed to unload model: {e}")
            return False

    async def pull_model(self, model_name: str) -> bool:
        """
        Pull a model from Ollama registry.

        Args:
            model_name: Model to pull

        Returns:
            True if pull successful
        """
        if not self._session:
            return False

        try:
            logger.info(f"[LLMService] Pulling model: {model_name}")
            async with self._session.post(
                f"{self._base_url}/api/pull",
                json={"name": model_name}
            ) as resp:
                if resp.status != 200:
                    return False
                # Stream response to track progress
                async for line in resp.content:
                    if line:
                        data = json.loads(line)
                        status = data.get("status", "")
                        logger.debug(f"[LLMService] Pull status: {status}")
                return True
        except Exception as e:
            logger.error(f"[LLMService] Failed to pull model: {e}")
            return False

    def get_model_for_mode(self, mode: str) -> str:
        """
        Get model name for processing mode.

        Args:
            mode: 'fast' (quick), 'thinking' (deep), 'thinking-lite' (capped analysis), 'research' (web+deep), or 'auto'
                 'auto'는 현재 fast로 처리 (향후 쿼리 분석으로 개선 가능)
                 'thinking-lite'는 thinking 모델을 사용하되 출력과 도구 호출이 제한됨

        Returns:
            Model name string
        """
        mode_map = {
            "fast": ModelType.FAST,
            "thinking": ModelType.THINKING,
            "thinking-lite": ModelType.THINKING,  # thinking-lite는 thinking 모델 사용
            "research": ModelType.RESEARCH,
            "auto": ModelType.FAST,  # auto 모드는 기본적으로 빠른 모델 사용
        }
        # 기본값을 FAST로 변경: 일반적인 경우 빠른 모델 사용
        model_type = mode_map.get(mode.lower(), ModelType.FAST)
        return self._models.get(model_type, self._models[ModelType.FAST])

    def set_model(self, model_type: ModelType, model_name: str) -> None:
        """
        Set model for a specific type.

        Args:
            model_type: Type of model to set
            model_name: Ollama model name
        """
        self._models[model_type] = model_name
        logger.info(f"[LLMService] Set {model_type.value} model to: {model_name}")

    def get_current_model(self) -> Optional[str]:
        """Get name of currently loaded model."""
        return self._current_model

    def _resolve_request_timeout(
        self,
        *,
        mode: str,
        request_timeout_seconds: Optional[float],
    ) -> tuple[float, str]:
        """Resolve the effective timeout applied at the HTTP client request layer."""
        if request_timeout_seconds is not None:
            return float(request_timeout_seconds), "http_client"
        return float(self.timeout), "http_client"

    # ==================== Generation ====================

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        mode: str = "fast",
        context_id: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        auto_unload: bool = True,
        **kwargs
    ) -> GenerationResult:
        """
        Generate response (non-streaming).

        Args:
            prompt: User prompt
            system_prompt: System instructions
            mode: Processing mode (fast=quick, thinking=deep, research=web+deep)
            context_id: Context ID for history tracking
            tools: Optional list of tools in OpenAI Function Calling format
            **kwargs: Additional Ollama parameters

        Returns:
            GenerationResult with response

        Raises:
            LLMServiceError: If generation fails
        """
        # Auto-reconnect if not ready
        if not await self._ensure_connected(max_retries=1):
            raise LLMServiceError("LLMService not connected and auto-reconnect failed")

        model = self.get_model_for_mode(mode)
        request_timeout_seconds = kwargs.pop("request_timeout_seconds", None)
        effective_timeout_seconds, effective_timeout_source = self._resolve_request_timeout(
            mode=mode,
            request_timeout_seconds=request_timeout_seconds,
        )
        await self._prepare_model_for_request(model)
        self._status = LLMStatus.GENERATING
        self._is_generating = True

        start_time = time.time()
        prompt_chars = len(prompt or "")

        try:
            # Get or create context
            context = self._get_context(context_id)
            if system_prompt:
                context.system_prompt = system_prompt

            # Build messages
            messages = context.get_messages()
            messages.append({"role": "user", "content": prompt})

            # Prepare request
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": self._keep_alive,
                **kwargs
            }
            
            # Add num_ctx limit for VRAM optimization (if not already specified)
            if "options" not in payload:
                payload["options"] = {}
            if "num_ctx" not in payload["options"]:
                payload["options"]["num_ctx"] = 4096  # Limit context size to reduce VRAM usage
            
            # Add tools if provided (Ollama Native Tool Calling)
            if tools:
                payload["tools"] = tools
                logger.debug(f"[LLMService] Added {len(tools)} tools to request")

            logger.info(
                "[LLMService] Generating with %s (mode: %s, timeout_seconds=%s, effective_timeout_source=%s)",
                model,
                mode,
                effective_timeout_seconds,
                effective_timeout_source,
            )

            # Session is ensured by _ensure_connected() above
            async with self._session.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=effective_timeout_seconds),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise LLMServiceError(f"Generation failed: {error_text}")

                data = await resp.json()

            # Extract response
            message = data.get("message", {})
            content = message.get("content", "")
            
            # Extract tool_calls if present
            tool_calls = None
            if "tool_calls" in message:
                tool_calls = message.get("tool_calls", [])
                logger.debug(f"[LLMService] Received {len(tool_calls)} tool calls")

            # Update context
            context.add_message("user", prompt)
            context.add_message("assistant", content)

            result = GenerationResult(
                content=content,
                model=model,
                total_duration_ms=data.get("total_duration", 0) / 1_000_000,
                eval_count=data.get("eval_count", 0),
                eval_duration_ms=data.get("eval_duration", 0) / 1_000_000,
                prompt_eval_count=data.get("prompt_eval_count", 0),
                done_reason=data.get("done_reason", ""),
                tool_calls=tool_calls,
            )

            logger.info(
                f"[LLMService] Generated {result.eval_count} tokens "
                f"in {result.total_duration_ms:.0f}ms "
                f"({result.tokens_per_second:.1f} t/s)"
            )

            return result
        except asyncio.TimeoutError as exc:
            elapsed_ms = round((time.time() - start_time) * 1000)
            logger.warning(
                "[LLMService] Generation timeout mode=%s model=%s timeout_seconds=%s "
                "effective_timeout_source=%s elapsed_ms=%s prompt_chars=%s",
                mode,
                model,
                effective_timeout_seconds,
                effective_timeout_source,
                elapsed_ms,
                prompt_chars,
            )
            raise LLMServiceTimeoutError(
                mode=mode,
                model=model,
                timeout_seconds=effective_timeout_seconds,
                effective_timeout_source=effective_timeout_source,
                elapsed_ms=elapsed_ms,
                prompt_chars=prompt_chars,
            ) from exc

        finally:
            self._is_generating = False
            self._status = LLMStatus.CONNECTED
            if auto_unload and self._should_unload_after_request() and self._current_model == model:
                await self.unload_model()
                await self.cleanup_stale_models(current_model=None)

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        mode: str = "fast",
        context_id: Optional[str] = None,
        on_first_token: OnFirstTokenT = None,
        on_done: OnDoneT = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Generate response with streaming.

        Args:
            prompt: User prompt
            system_prompt: System instructions
            mode: Processing mode
            context_id: Context ID for history
            on_first_token: Optional callback(ttft_ms) when first chunk is yielded (metrics).
            on_done: Optional callback(tokens_out, tokens_in, duration_sec) when stream ends (metrics).
            **kwargs: Additional Ollama parameters

        Yields:
            Text chunks as they're generated

        Raises:
            LLMServiceError: If generation fails
        """
        # [복구 패치] 세션이 끊어졌으면 자동 재연결
        if not await self._ensure_connected(max_retries=1):
            raise LLMServiceError("LLMService not connected and auto-reconnect failed")

        model = self.get_model_for_mode(mode)
        await self._prepare_model_for_request(model)
        self._status = LLMStatus.GENERATING
        self._is_generating = True
        self._cancel_requested = False

        full_response = ""
        t_start = time.perf_counter()
        first_token_sent = False
        done_data: Optional[Dict[str, Any]] = None
        request_id = str(uuid.uuid4())

        try:
            # Get or create context
            context = self._get_context(context_id)
            if system_prompt:
                context.system_prompt = system_prompt

            # Build messages
            messages = context.get_messages()
            messages.append({"role": "user", "content": prompt})

            # Prepare request
            payload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": self._keep_alive,
                **kwargs
            }
            
            # Add num_ctx limit for VRAM optimization (if not already specified)
            if "options" not in payload:
                payload["options"] = {}
            if "num_ctx" not in payload["options"]:
                payload["options"]["num_ctx"] = 4096  # Limit context size to reduce VRAM usage

            logger.info(f"[LLMService] Streaming with {model} (mode: {mode})")

            # Session is ensured by _ensure_connected() above
            async with self._session.post(
                f"{self._base_url}/api/chat",
                json=payload
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise LLMServiceError(f"Generation failed: {error_text}")

                async for line in resp.content:
                    if self._cancel_requested:
                        logger.info("[LLMService] Generation cancelled")
                        break

                    if line:
                        try:
                            data = json.loads(line)
                            if "message" in data:
                                chunk = data["message"].get("content", "")
                                if chunk:
                                    if not first_token_sent:
                                        first_token_sent = True
                                        ttft_ms = (time.perf_counter() - t_start) * 1000
                                        try:
                                            from mellow_link.core.metrics_collector import get_metrics_collector
                                            coll = get_metrics_collector()
                                            if coll:
                                                coll.push_ttft(ttft_ms, request_id)
                                                coll.push_ttft_measured(True, f"{request_id}_tm")
                                        except Exception as e:
                                            logger.debug("[LLMService] metrics push_ttft failed: %s", e)
                                        if on_first_token:
                                            try:
                                                on_first_token(ttft_ms)
                                            except Exception as e:
                                                logger.debug("[LLMService] on_first_token callback error: %s", e)
                                    full_response += chunk
                                    yield chunk

                            if data.get("done", False):
                                done_data = data
                                break

                        except json.JSONDecodeError:
                            continue

            # Update context with full response
            context.add_message("user", prompt)
            context.add_message("assistant", full_response)

            if done_data is not None:
                eval_count = done_data.get("eval_count", 0)
                prompt_eval_count = done_data.get("prompt_eval_count", 0)
                eval_duration_ns = done_data.get("eval_duration", 0) or 0
                duration_sec = eval_duration_ns / 1e9 if eval_duration_ns else (time.perf_counter() - t_start)
                tps = (eval_count / duration_sec) if duration_sec > 0 else 0.0
                try:
                    from mellow_link.core.metrics_collector import get_metrics_collector
                    coll = get_metrics_collector()
                    if coll:
                        coll.push_tokens(prompt_eval_count, eval_count, request_id)
                        coll.push_tps(tps, f"{request_id}_tps")
                except Exception as e:
                    logger.debug("[LLMService] metrics push_tokens/tps failed: %s", e)
                if on_done:
                    try:
                        on_done(eval_count, prompt_eval_count, duration_sec)
                    except Exception as e:
                        logger.debug("[LLMService] on_done callback error: %s", e)

            logger.info(f"[LLMService] Stream complete: {len(full_response)} chars")

        finally:
            self._is_generating = False
            self._status = LLMStatus.CONNECTED
            if self._should_unload_after_request() and self._current_model == model:
                await self.unload_model()
                await self.cleanup_stale_models(current_model=None)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "llama3",
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Chat completion with message history.

        Args:
            messages: List of {"role": "user/assistant", "content": "..."}
            model: Model to use for chat
            tools: Optional list of tools in OpenAI Function Calling format
            **kwargs: Additional generation parameters

        Returns:
            LLMResponse with assistant's reply
        """
        # [복구 패치] 세션이 끊어졌으면 자동 재연결
        if not await self._ensure_connected(max_retries=1):
            raise LLMServiceError("LLMService not connected and auto-reconnect failed")

        await self._prepare_model_for_request(model)

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._keep_alive,
            **kwargs
        }
        
        # Add tools if provided (Ollama Native Tool Calling)
        if tools:
            payload["tools"] = tools
            logger.debug(f"[LLMService] Added {len(tools)} tools to chat request")
        
        # Add num_ctx limit for VRAM optimization (if not already specified)
        if "options" not in payload:
            payload["options"] = {}
        if "num_ctx" not in payload["options"]:
            payload["options"]["num_ctx"] = 4096  # Limit context size to reduce VRAM usage

        try:
            start_mono = time.monotonic()

            # 디버깅: 요청 페이로드 확인
            logger.debug(f"[LLMService] Chat request - model: {model}, messages count: {len(messages)}, tools: {len(tools) if tools else 0}")
            if messages:
                logger.debug(f"[LLMService] First message role: {messages[0].get('role')}, content length: {len(messages[0].get('content', ''))}")

            # Session is ensured by _ensure_connected() above
            try:
                async with self._session.post(
                    f"{self._base_url}/api/chat",
                    json=payload
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[LLMService] Chat API error - status: {resp.status}, error: {error_text[:500]}")
                        raise LLMServiceError(f"Chat failed: {error_text}")

                    data = await resp.json()
            except RuntimeError as e:
                if "Timeout context manager should be used inside a task" not in str(e):
                    raise
                logger.warning("[LLMService] Detected stale aiohttp session; resetting and retrying chat once")
                await self._cleanup_session(reason="chat runtime timeout-context mismatch")
                self._status = LLMStatus.DISCONNECTED
                if not await self._ensure_connected(max_retries=1):
                    raise LLMServiceError("LLMService session reset failed")
                async with self._session.post(
                    f"{self._base_url}/api/chat",
                    json=payload
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[LLMService] Chat API error after session reset - status: {resp.status}, error: {error_text[:500]}")
                        raise LLMServiceError(f"Chat failed: {error_text}")
                    data = await resp.json()
            
            # ⚠️ 중요: 빈 응답 진단을 위해 INFO 레벨로 로깅
            logger.info(f"[LLMService] Ollama response keys: {list(data.keys())}")
            
            if "message" in data:
                message = data["message"]
                logger.info(f"[LLMService] Message keys: {list(message.keys())}")
                content_preview = str(message.get('content', ''))
                content_length = len(content_preview)
                logger.info(f"[LLMService] Message content length: {content_length}, preview: {content_preview[:200]}")
                
                # tool_calls 확인
                if "tool_calls" in message:
                    tool_calls_data = message.get("tool_calls", [])
                    tool_calls_count = len(tool_calls_data) if tool_calls_data else 0
                    logger.info(f"[LLMService] Tool calls count: {tool_calls_count}")
                    if tool_calls_data:
                        logger.info(f"[LLMService] First tool call: {json.dumps(tool_calls_data[0], ensure_ascii=False)[:300]}")
                else:
                    logger.info("[LLMService] No tool_calls key in message")
                
                # 전체 응답 데이터 로깅 (빈 응답인 경우)
                if content_length == 0 and not message.get("tool_calls"):
                    logger.warning(
                        f"[LLMService] 빈 응답 감지 (1차). AgentBrain 폴백/재시도에서 복구될 수 있습니다.\n"
                        f"[LLMService] 응답 데이터:\n{json.dumps(data, ensure_ascii=False, indent=2)[:1000]}"
                    )
            else:
                logger.error(f"[LLMService] ⚠️ 'message' 키가 없음! 전체 응답: {json.dumps(data, ensure_ascii=False)[:500]}")

            message = data.get("message", {})
            content = message.get("content", "")
            
            # content가 None이면 빈 문자열로 변환
            if content is None:
                content = ""
                logger.warning("[LLMService] Message content is None, converting to empty string")
            
            # Extract tool_calls if present
            tool_calls = None
            if "tool_calls" in message:
                tool_calls = message.get("tool_calls", [])
                if tool_calls:
                    logger.info(f"[LLMService] Received {len(tool_calls)} tool calls in chat")
                else:
                    logger.debug("[LLMService] tool_calls key exists but is empty")
            
            # 빈 응답이고 tool_calls도 없는 경우 상세 경고
            if not content and not tool_calls:
                logger.warning(
                    f"[LLMService] 빈 응답 반환 (model={model}, status={data.get('done', 'unknown')}, "
                    f"done_reason={data.get('done_reason', 'unknown')}). "
                    "AgentBrain에서 단계적 폴백을 진행합니다."
                )

            generation_time = (time.monotonic() - start_mono) * 1000
            tokens_out = data.get("eval_count", 0)
            tokens_in = data.get("prompt_eval_count", 0)
            duration_sec = (time.monotonic() - start_mono)
            tps_approx = (tokens_out / duration_sec) if duration_sec > 0 else 0.0

            # Phase 1 metrics (async queue only; no DB write in request path)
            try:
                from mellow_link.core.metrics_collector import get_metrics_collector
                coll = get_metrics_collector()
                if coll:
                    req_id = str(uuid.uuid4())
                    coll.push_infer_ms(generation_time, req_id)
                    coll.push("TOKENS_IN", float(tokens_in), "tokens", metric_id=f"{req_id}_in")
                    coll.push("TOKENS_OUT", float(tokens_out), "tokens", metric_id=f"{req_id}_out")
                    coll.push_tps_approx(tps_approx, f"{req_id}_tps_approx")
                    coll.push("TTFT_MS", -1.0, "ms", metric_id=f"{req_id}_ttft")  # not measured
                    coll.push_ttft_measured(False, f"{req_id}_ttft_m")
            except Exception as e:
                logger.debug("[LLMService] Metrics push (chat) failed: %s", e)

            return LLMResponse(
                text=content,
                model=model,
                tokens_generated=tokens_out,
                generation_time_ms=generation_time,
                is_complete=True,
                tool_calls=tool_calls
            )
        finally:
            if self._should_unload_after_request() and self._current_model == model:
                await self.unload_model()
                await self.cleanup_stale_models(current_model=None)
        

    async def execute(self, request_data: Dict[str, Any]) -> str:
        """
        Execute method for orchestrator compatibility.

        Args:
            request_data: Dict with generation parameters

        Returns:
            Generated text
        """
        result = await self.generate(
            prompt=request_data.get("prompt", ""),
            system_prompt=request_data.get("system_prompt", ""),
            mode=request_data.get("mode", "thinking"),
            context_id=request_data.get("context_id"),
        )
        return result.content

    # ==================== Context Management ====================

    def _get_context(self, context_id: Optional[str]) -> ChatContext:
        """Get or create context by ID."""
        if context_id is None:
            return self._default_context

        if context_id not in self._contexts:
            self._contexts[context_id] = ChatContext()

        return self._contexts[context_id]

    def create_context(
        self,
        context_id: str = None,
        system_prompt: str = "",
        max_history: int = 20
    ) -> ChatContext:
        """
        Create a new conversation context.

        Args:
            context_id: Unique context identifier
            system_prompt: System instructions
            max_history: Maximum messages to retain

        Returns:
            New ChatContext
        """
        context = ChatContext(
            system_prompt=system_prompt,
            max_history=max_history
        )
        if context_id:
            self._contexts[context_id] = context
        return context

    def get_context(self, context_id: str) -> Optional[ChatContext]:
        """Get existing context by ID."""
        return self._contexts.get(context_id)

    def clear_context(self, context_id: str) -> bool:
        """Clear a specific context."""
        if context_id in self._contexts:
            self._contexts[context_id].clear()
            return True
        return False

    def delete_context(self, context_id: str) -> bool:
        """Delete a context."""
        if context_id in self._contexts:
            del self._contexts[context_id]
            return True
        return False

    def list_contexts(self) -> List[str]:
        """List all context IDs."""
        return list(self._contexts.keys())

    # ==================== Utilities ====================

    def cancel_generation(self) -> bool:
        """Request cancellation of current generation."""
        if self._is_generating:
            self._cancel_requested = True
            logger.info("[LLMService] Cancellation requested")
            return True
        return False

    async def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific model.

        Args:
            model_name: Model to query

        Returns:
            Model information dict
        """
        if not self._session:
            return None

        try:
            async with self._session.post(
                f"{self._base_url}/api/show",
                json={"name": model_name}
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"[LLMService] Failed to get model info: {e}")
            return None

    async def embeddings(
        self,
        text: str,
        model: Optional[str] = None
    ) -> List[float]:
        """
        Generate embeddings for text.

        Args:
            text: Text to embed
            model: Model to use (defaults to current)

        Returns:
            Embedding vector
        """
        if not self._session:
            raise LLMServiceError("Not connected")

        model = model or self._current_model or self._models[ModelType.THINKING]

        try:
            async with self._session.post(
                f"{self._base_url}/api/embeddings",
                json={"model": model, "prompt": text}
            ) as resp:
                if resp.status != 200:
                    raise LLMServiceError(f"Embedding failed: {resp.status}")
                data = await resp.json()
                return data.get("embedding", [])
        except Exception as e:
            logger.error(f"[LLMService] Embedding error: {e}")
            raise

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Args:
            text: Text to estimate

        Returns:
            Approximate token count (rough estimate: ~4 chars per token)
        """
        return len(text) // 4

    def to_dict(self) -> Dict[str, Any]:
        """Export service state as dictionary."""
        return {
            "status": self._status.name,
            "host": self.host,
            "port": self.port,
            "current_model": self._current_model,
            "is_generating": self._is_generating,
            "models": {k.value: v for k, v in self._models.items()},
            "active_contexts": len(self._contexts),
        }


# =============================================================================
# Factory Function
# =============================================================================

def create_llm_service(
    host: str = "localhost",
    port: int = 11434,
    timeout: float = 30.0,
    models: Optional[Dict[str, str]] = None
) -> LLMService:
    """
    Factory function to create LLMService.

    Args:
        host: Ollama hostname
        port: Ollama port
        timeout: Request timeout
        models: Model mapping (mode -> model_name)

    Returns:
        Configured LLMService instance
    """
    model_mapping = None
    if models:
        model_mapping = {
            ModelType.FAST: models.get("fast", LLMService.DEFAULT_MODELS[ModelType.FAST]),
            ModelType.THINKING: models.get("thinking", LLMService.DEFAULT_MODELS[ModelType.THINKING]),
            ModelType.RESEARCH: models.get("research", LLMService.DEFAULT_MODELS[ModelType.RESEARCH]),
        }

    return LLMService(
        host=host,
        port=port,
        timeout=timeout,
        models=model_mapping
    )
