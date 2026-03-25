"""
Settings Management for Mellow-Link

Centralized configuration using pydantic-settings for environment variable
support, validation, and type safety.

Usage:
    from mellow_link.config.settings import get_settings

    settings = get_settings()
    print(settings.ollama_host)
"""

from typing import Optional, List
from pathlib import Path
from functools import lru_cache
import os

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field, field_validator, model_validator, AliasChoices
    PYDANTIC_V2 = True
except ImportError:
    # Fallback for pydantic v1
    from pydantic import BaseSettings, Field, validator, root_validator
    AliasChoices = None
    PYDANTIC_V2 = False

# Force output_dir to be inside mellow_link package
_MELLOW_LINK_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent
_REPO_ROOT = _MELLOW_LINK_DIR.parent
_ENV_FILE = _REPO_ROOT / ".env"
_FORCED_OUTPUT_DIR = _MELLOW_LINK_DIR / "outputs"
_REPO_ENV_OVERRIDE_KEYS = {
    "ENABLE_MEDIA_AI",
    "ENABLE_MEDIA_COMPUTE",
    "ENABLE_FFMPEG",
}


def _preload_repo_env() -> None:
    if not _ENV_FILE.exists():
        return
    try:
        for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            normalized = value.strip().strip('"').strip("'")
            if key in _REPO_ENV_OVERRIDE_KEYS:
                os.environ[key] = normalized
                continue
            if key not in os.environ:
                os.environ[key] = normalized
    except Exception:
        pass


_preload_repo_env()


class Settings(BaseSettings):
    """
    Main settings class for Mellow-Link using pydantic-settings.

    All settings can be overridden via environment variables with the
    MELLOW_ prefix. For example:
        - MELLOW_OLLAMA_HOST=192.168.1.100
        - MELLOW_COMFYUI_PORT=8189
        - MELLOW_MODEL_DIR=/path/to/models

    Attributes:
        model_dir: Directory for AI models (Ollama, ComfyUI checkpoints)
        data_dir: Directory for application data (documents, outputs)

        ollama_host: Ollama server hostname
        ollama_port: Ollama server port
        ollama_timeout: Request timeout for Ollama (seconds)

        comfyui_host: ComfyUI server hostname
        comfyui_port: ComfyUI server port
        comfyui_timeout: Request timeout for ComfyUI (seconds)

        vram_warning_threshold: VRAM % to trigger warning
        vram_critical_threshold: VRAM % to trigger critical alert
        vram_poll_interval: Seconds between VRAM checks

        api_host: FastAPI server host
        api_port: FastAPI server port
        api_debug: Enable debug mode

        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """

    # ==================== Directory Settings ====================
    model_dir: Path = Field(
        default=Path("./models"),
        description="Directory for AI models",
        validation_alias="MELLOW_MODEL_DIR" if PYDANTIC_V2 else None
    )
    data_dir: Path = Field(
        default=Path("./data"),
        description="Directory for application data",
        validation_alias="MELLOW_DATA_DIR" if PYDANTIC_V2 else None
    )
    output_dir: Path = Field(
        default=Path("./outputs"),
        description="Directory for generated outputs",
        validation_alias="MELLOW_OUTPUT_DIR" if PYDANTIC_V2 else None
    )

    # ==================== Ollama (LLM) Settings ====================
    ollama_host: str = Field(
        default="localhost",
        description="Ollama server hostname",
        validation_alias="MELLOW_OLLAMA_HOST" if PYDANTIC_V2 else None
    )
    ollama_port: int = Field(
        default=11434,
        ge=1,
        le=65535,
        description="Ollama server port"
    )
    ollama_timeout: float = Field(
        default=30.0,
        ge=1.0,
        description="Ollama request timeout in seconds"
    )
    research_timeout: float = Field(
        default=90.0,
        ge=1.0,
        description="Dedicated Ollama timeout for research inference requests"
    )

    # Ollama model configuration
    fast_model: str = Field(
        default="qwen2.5:7b",
        description="Lightweight model for quick responses",
        validation_alias="MELLOW_LLM_FAST_MODEL" if PYDANTIC_V2 else None
    )
    thinking_model: str = Field(
        default="qwen3.5:9b",
        description="Main model for deep reasoning",
        validation_alias="MELLOW_LLM_THINKING_MODEL" if PYDANTIC_V2 else None
    )
    research_model: str = Field(
        default="qwen3.5:9b",
        description="Model for research/web search tasks"
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        description="Model for embeddings",
        validation_alias="MELLOW_LLM_EMBEDDING_MODEL" if PYDANTIC_V2 else None
    )

    # ==================== ComfyUI (Image) Settings ====================
    comfyui_host: str = Field(
        default="localhost",
        description="ComfyUI server hostname",
        validation_alias="MELLOW_COMFYUI_HOST" if PYDANTIC_V2 else None
    )
    comfyui_port: int = Field(
        default=8188,
        ge=1,
        le=65535,
        description="ComfyUI server port"
    )
    comfyui_timeout: float = Field(
        default=600.0,
        ge=1.0,
        description="ComfyUI request timeout in seconds"
    )

    # ComfyUI default checkpoint
    default_checkpoint: str = Field(
        default="flux1-dev-fp8.safetensors",
        description="Default Stable Diffusion checkpoint"
    )

    # ==================== VRAM Watchdog Settings ====================
    vram_warning_threshold: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        description="VRAM % to trigger warning",
        validation_alias="MELLOW_VRAM_WARNING_THRESHOLD" if PYDANTIC_V2 else None
    )
    vram_critical_threshold: float = Field(
        default=95.0,
        ge=0.0,
        le=100.0,
        description="VRAM % to trigger critical alert",
        validation_alias="MELLOW_VRAM_CRITICAL_THRESHOLD" if PYDANTIC_V2 else None
    )
    vram_poll_interval: float = Field(
        default=2.0,
        ge=0.5,
        description="Seconds between VRAM checks"
    )
    gpu_device_id: int = Field(
        default=0,
        ge=0,
        description="GPU device index to monitor"
    )

    # ==================== Orchestrator Settings ====================
    gpu_cooldown_seconds: float = Field(
        default=2.0,
        ge=0.0,
        description="Cooldown between GPU state transitions"
    )
    max_queue_size: int = Field(
        default=100,
        ge=1,
        description="Maximum pending tasks in queue"
    )

    # ==================== API Server Settings ====================
    # 기본 127.0.0.1로 외부 노출 차단. 외부 접근 시 MELLOW_API_HOST=0.0.0.0 설정
    api_host: str = Field(
        default="127.0.0.1",
        description="FastAPI server host (default 127.0.0.1 for security)",
        validation_alias=AliasChoices("SERVER_HOST", "MELLOW_API_HOST") if (PYDANTIC_V2 and AliasChoices) else None
    )
    server_host: str = Field(
        default="127.0.0.1",
        description="Server host (alias for api_host)",
        validation_alias="SERVER_HOST" if PYDANTIC_V2 else None
    )
    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="FastAPI server port",
        validation_alias=AliasChoices("SERVER_PORT", "MELLOW_API_PORT") if (PYDANTIC_V2 and AliasChoices) else None
    )
    server_port: int = Field(
        default=8002,
        ge=1,
        le=65535,
        description="Server port (alias for api_port)",
        validation_alias="SERVER_PORT" if PYDANTIC_V2 else None
    )
    api_debug: bool = Field(
        default=False,
        description="Enable API debug mode",
        validation_alias=AliasChoices("DEBUG", "MELLOW_DEBUG") if (PYDANTIC_V2 and AliasChoices) else None
    )
    debug: bool = Field(
        default=False,
        description="Debug mode (alias for api_debug)",
        validation_alias="DEBUG" if PYDANTIC_V2 else None
    )
    app_title: str = Field(
        default="Aventurine v3",
        description="Application title",
        validation_alias="APP_TITLE" if PYDANTIC_V2 else None
    )
    cors_origins: str = Field(
        default="*",
        description="Allowed CORS origins (comma-separated string or * for all)"
    )

    # ==================== Document Service Settings ====================
    doc_max_workers: int = Field(
        default=2,
        ge=1,
        le=8,
        description="Thread pool workers for document generation"
    )
    template_dir: Path = Field(
        default=Path("./templates"),
        description="Directory for document templates"
    )
    
    # ==================== Docs Auto Injection Settings ====================
    docs_auto_enabled: bool = Field(
        default=True,
        description="Enable automatic docs injection (0 to disable)",
        validation_alias=AliasChoices("DOCS_AUTO_ENABLED", "MELLOW_DOCS_AUTO_ENABLED") if (PYDANTIC_V2 and AliasChoices) else None
    )

    # ==================== Experimental Environment Flags ====================
    # 실험 환경에서 불필요한 백그라운드 작업들을 비활성화하기 위한 플래그들
    enable_autonomous_agent: bool = Field(
        default=False,
        description="Enable autonomous agent background loop (0 to disable)",
        validation_alias=AliasChoices("ENABLE_AUTONOMOUS_AGENT", "MELLOW_ENABLE_AUTONOMOUS_AGENT") if (PYDANTIC_V2 and AliasChoices) else None
    )
    enable_workspace_scanner: bool = Field(
        default=False,
        description="Enable periodic workspace scanning (0 to disable)",
        validation_alias=AliasChoices("ENABLE_WORKSPACE_SCANNER", "MELLOW_ENABLE_WORKSPACE_SCANNER") if (PYDANTIC_V2 and AliasChoices) else None
    )
    enable_rag_background_indexing: bool = Field(
        default=True,
        description="Enable RAG background indexing (0 to disable, search-only mode)",
        validation_alias=AliasChoices("ENABLE_RAG_BACKGROUND_INDEXING", "MELLOW_ENABLE_RAG_BACKGROUND_INDEXING") if (PYDANTIC_V2 and AliasChoices) else None
    )
    enable_tool_forge: bool = Field(
        default=True,
        description="Enable ToolForge (autonomous tool generation/validation) (0 to disable)",
        validation_alias=AliasChoices("ENABLE_TOOL_FORGE", "MELLOW_ENABLE_TOOL_FORGE") if (PYDANTIC_V2 and AliasChoices) else None
    )
    enable_model_unload_on_idle: bool = Field(
        default=True,
        description="Enable model unload when transitioning to IDLE (0 to disable, useful for benchmarks)",
        validation_alias=AliasChoices("ENABLE_MODEL_UNLOAD_ON_IDLE", "MELLOW_ENABLE_MODEL_UNLOAD_ON_IDLE") if (PYDANTIC_V2 and AliasChoices) else None
    )
    obs_max_chars: int = Field(
        default=1200,
        ge=100,
        le=10000,
        description="Maximum characters for observation output (dict/list will be serialized and truncated)",
        validation_alias=AliasChoices("OBS_MAX_CHARS", "MELLOW_OBS_MAX_CHARS") if (PYDANTIC_V2 and AliasChoices) else None
    )
    
    bench_profile: bool = Field(
        default=False,
        description="Benchmark profile mode: disable experience insight generation and extra tool calls",
        validation_alias=AliasChoices("BENCH_PROFILE", "MELLOW_BENCH_PROFILE") if (PYDANTIC_V2 and AliasChoices) else None
    )

    # ==================== Logging Settings ====================
    log_level: str = Field(
        default="INFO",
        description="Logging level",
        validation_alias="MELLOW_LOG_LEVEL" if PYDANTIC_V2 else None
    )

    # ==================== Security (Agent Policy) ====================
    if PYDANTIC_V2:
        security_level: str = Field(
            default="NORMAL",
            description="Security level for local agent tools (EASY/NORMAL/HARD)",
            validation_alias=AliasChoices("SECURITY_LEVEL", "MELLOW_SECURITY_LEVEL") if AliasChoices else None,
        )
    else:
        security_level: str = Field(
            default="NORMAL",
            description="Security level for local agent tools (EASY/NORMAL/HARD)",
            env=["SECURITY_LEVEL", "MELLOW_SECURITY_LEVEL"],
        )

    # ==================== Tool Output Limits (p95 latency) ====================
    fs_list_max_items: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Max items returned by list_directory (cap before sending to model)",
        validation_alias="FS_LIST_MAX_ITEMS" if PYDANTIC_V2 else None
    )
    fs_recent_max_items: int = Field(
        default=30,
        ge=1,
        le=200,
        description="Max items for recent-modified / recent-files style tools",
        validation_alias="FS_RECENT_MAX_ITEMS" if PYDANTIC_V2 else None
    )
    sys_proc_max_items: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Max items for process-list style tools (future use)",
        validation_alias="SYS_PROC_MAX_ITEMS" if PYDANTIC_V2 else None
    )

    # ==================== Authentication Settings ====================
    guest_access_code: str = Field(
        default="lucky777",
        description="Access code for guest login",
        validation_alias="GUEST_ACCESS_CODE" if PYDANTIC_V2 else None
    )
    guest_token_expire_hours: int = Field(
        default=24,
        ge=1,
        description="Guest token expiry in hours",
        validation_alias="GUEST_TOKEN_EXPIRE_HOURS" if PYDANTIC_V2 else None
    )
    api_key: str = Field(
        default="",
        description="API key for external access",
        validation_alias="API_KEY" if PYDANTIC_V2 else None
    )
    
    # ==================== RBAC Settings ====================
    limit_admin: int = Field(
        default=-1,
        description="Daily usage limit for admin (-1 for unlimited)",
        validation_alias="LIMIT_ADMIN" if PYDANTIC_V2 else None
    )
    limit_user: int = Field(
        default=150,
        ge=-1,
        description="Daily usage limit for user (-1 for unlimited)",
        validation_alias="LIMIT_USER" if PYDANTIC_V2 else None
    )
    limit_guest: int = Field(
        default=20,
        ge=-1,
        description="Daily usage limit for guest (-1 for unlimited)",
        validation_alias="LIMIT_GUEST" if PYDANTIC_V2 else None
    )

    # ==================== Guardian Agents (2차 검수 모듈) ====================
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key for Claude (Guardian audit)",
        validation_alias="ANTHROPIC_API_KEY" if PYDANTIC_V2 else None
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for GPT-4o (Guardian audit)",
        validation_alias="OPENAI_API_KEY" if PYDANTIC_V2 else None
    )
    google_api_key: str = Field(
        default="",
        description="Google API key for Gemini (Tower/관제)",
        validation_alias="GOOGLE_API_KEY" if PYDANTIC_V2 else None
    )
    guardian_provider: str = Field(
        default="anthropic",
        description="Default Guardian provider: anthropic (Claude) or openai (GPT)",
        validation_alias="GUARDIAN_PROVIDER" if PYDANTIC_V2 else None
    )
    agent_provider: str = Field(
        default="openai",
        description="Main agent provider for Verdict (판결)",
        validation_alias="AGENT_PROVIDER" if PYDANTIC_V2 else None
    )
    tower_model: str = Field(
        default="gemini-2.5-flash",
        description="Tower (관제) 모델 - Gemini 2.5 Flash (1.5-pro deprecated)",
        validation_alias="TOWER_MODEL" if PYDANTIC_V2 else None
    )
    verdict_model: str = Field(
        default="gpt-4o",
        description="Verdict (판결) 모델 - 코드 수정안 생성",
        validation_alias="VERDICT_MODEL" if PYDANTIC_V2 else None
    )
    audit_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Audit (검수) 모델 - Claude Sonnet 4 (3.5 deprecated 2025-08)",
        validation_alias="AUDIT_MODEL" if PYDANTIC_V2 else None
    )
    max_daily_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Guardian/Audit(Anthropic) 일일 최대 비용(USD). 0=제한 없음. 양수=허용 한도.",
        validation_alias="MAX_DAILY_COST" if PYDANTIC_V2 else None
    )
    max_daily_tokens: int = Field(
        default=0,
        ge=0,
        description="Guardian API 일일 최대 토큰 수. 0=제한 없음. 양수=허용 한도.",
        validation_alias="MAX_DAILY_TOKENS" if PYDANTIC_V2 else None
    )
    max_daily_cost_google: float = Field(
        default=0.0,
        ge=0.0,
        description="Tower(Gemini) 일일 최대 비용(USD). 0=제한 없음. 양수=허용 한도.",
        validation_alias="MAX_DAILY_COST_GOOGLE" if PYDANTIC_V2 else None
    )
    max_daily_cost_openai: float = Field(
        default=0.0,
        ge=0.0,
        description="Verdict(OpenAI) 일일 최대 비용(USD). 0=제한 없음. 양수=허용 한도.",
        validation_alias="MAX_DAILY_COST_OPENAI" if PYDANTIC_V2 else None
    )
    enable_tiered_auditing: bool = Field(
        default=True,
        description="하이브리드 검수: Level1=GPT, Level2+=Claude. False면 기존 방식(단일 검수관).",
        validation_alias="ENABLE_TIERED_AUDITING" if PYDANTIC_V2 else None
    )
    telegram_bot_token: str = Field(
        default="",
        description="Telegram Bot Token (VIP 모바일 알림)",
        validation_alias="TELEGRAM_BOT_TOKEN" if PYDANTIC_V2 else None
    )
    telegram_chat_id: str = Field(
        default="",
        description="Telegram Chat ID (Admin 휴대폰)",
        validation_alias="TELEGRAM_CHAT_ID" if PYDANTIC_V2 else None
    )
    telegram_webhook_secret: str = Field(
        default="",
        description="Telegram Webhook Secret (X-Telegram-Bot-Api-Secret-Token). setWebhook 시 설정한 값과 일치해야 함.",
        validation_alias="TELEGRAM_WEBHOOK_SECRET" if PYDANTIC_V2 else None
    )
    enable_mobile_notify: bool = Field(
        default=False,
        description="결재 보고서 생성 시 Telegram 알림 활성화",
        validation_alias="ENABLE_MOBILE_NOTIFY" if PYDANTIC_V2 else None
    )
    public_base_url: str = Field(
        default="",
        description="Flow 상세 보기 링크용 공개 베이스 URL (예: https://your-host). 설정 시 중요 Evolution 알림에 Detail 버튼 포함",
        validation_alias="MELLOW_PUBLIC_BASE_URL" if PYDANTIC_V2 else None
    )
    enable_scheduler: bool = Field(
        default=False,
        description="SchedulerService 백그라운드 기동 여부 (자율 진단·진화 트리거 등)",
        validation_alias="ENABLE_SCHEDULER" if PYDANTIC_V2 else None
    )
    enable_evolution_trigger: bool = Field(
        default=False,
        description="Scheduler가 주기적으로 진화 필요 여부 판단 후 Evolution 트리거. EVOLUTION_PROTOCOL.evolution_trigger.enabled도 적용",
        validation_alias="ENABLE_EVOLUTION_TRIGGER" if PYDANTIC_V2 else None
    )
    enable_evolution_adapter: bool = Field(
        default=False,
        description="Evolution 기능 어댑터 활성화. 0=비활성(DisabledEvolutionService). 1차 게이트는 ENABLE_GUARDIAN_APIS.",
        validation_alias="ENABLE_EVOLUTION_ADAPTER" if PYDANTIC_V2 else None
    )

    # ==================== Metrics (Performance Stability) ====================
    metrics_enabled: bool = Field(
        default=False,
        description="Enable request-path metrics collection (TTFT, TPS, tokens). Default OFF.",
        validation_alias="MELLOW_METRICS_ENABLED" if PYDANTIC_V2 else None
    )
    metrics_async_flush: bool = Field(
        default=True,
        description="Flush metrics to DB in background; no write on request path. Default ON when metrics enabled.",
        validation_alias="MELLOW_METRICS_ASYNC_FLUSH" if PYDANTIC_V2 else None
    )
    metrics_flush_interval_ms: int = Field(
        default=500,
        ge=100,
        le=30000,
        description="Background metrics flush interval in ms.",
        validation_alias="MELLOW_METRICS_FLUSH_INTERVAL_MS" if PYDANTIC_V2 else None
    )
    metrics_flush_batch_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Max metrics to flush per batch.",
        validation_alias="MELLOW_METRICS_FLUSH_BATCH_SIZE" if PYDANTIC_V2 else None
    )
    metrics_max_queue_size: int = Field(
        default=5000,
        ge=100,
        le=100_000,
        description="Max in-memory metrics queue size; overflow drops oldest. Prevents unbounded growth.",
        validation_alias="MELLOW_METRICS_MAX_QUEUE_SIZE" if PYDANTIC_V2 else None
    )

    # ==================== Web Search Settings ====================
    enable_web_search: bool = Field(
        default=False,
        description="웹 검색 기능 활성화 여부. 폐쇄망 기본값 OFF. Gate: allow_web_search()",
        validation_alias=AliasChoices("ENABLE_WEB_SEARCH", "MELLOW_ENABLE_WEB_SEARCH") if (PYDANTIC_V2 and AliasChoices) else ("MELLOW_ENABLE_WEB_SEARCH" if PYDANTIC_V2 else None)
    )

    # ==================== 폐쇄망 Feature Flags (Air-Gapped Network Toggle) ====================
    # env 기반, 기본값 폐쇄망 안전(OFF). Gate API: allow_outbound_http(), allow_guardian_api() 등
    enable_outbound_http: bool = Field(
        default=False,
        description="외부 HTTP 허용 (SecurityManager/agent_tools). 0=차단(폐쇄망 기본)",
        validation_alias="ENABLE_OUTBOUND_HTTP" if PYDANTIC_V2 else None
    )
    enable_guardian_apis: bool = Field(
        default=False,
        description="Guardian APIs (Google/OpenAI/Anthropic) 허용. 0=차단(폐쇄망 기본)",
        validation_alias="ENABLE_GUARDIAN_APIS" if PYDANTIC_V2 else None
    )
    enable_telegram: bool = Field(
        default=False,
        description="Telegram 알림/웹훅 허용. 0=차단(폐쇄망 기본)",
        validation_alias="ENABLE_TELEGRAM" if PYDANTIC_V2 else None
    )
    enable_edge_tts: bool = Field(
        default=False,
        description="EdgeTTS(MS 클라우드 TTS) 허용. 0=차단(폐쇄망 기본)",
        validation_alias="ENABLE_EDGE_TTS" if PYDANTIC_V2 else None
    )
    enable_media_compute: bool = Field(
        default=True,
        description="미디어 로컬 연산(ffmpeg 트랜스코딩 등) 허용. 0=차단",
        validation_alias="ENABLE_MEDIA_COMPUTE" if PYDANTIC_V2 else None
    )
    enable_media_ai: bool = Field(
        default=False,
        description="미디어 AI(이미지/동영상 생성, upscale, TTS 등) 허용. 0=차단(폐쇄망 기본)",
        validation_alias="ENABLE_MEDIA_AI" if PYDANTIC_V2 else None
    )
    enable_media_upload: bool = Field(
        default=False,
        description="미디어 업로드(YouTube/S3/Drive 등) 허용. 0=차단(폐쇄망 기본)",
        validation_alias="ENABLE_MEDIA_UPLOAD" if PYDANTIC_V2 else None
    )
    enable_ffmpeg: bool = Field(
        default=True,
        description="FFmpeg/ffprobe 호출 허용. ENABLE_MEDIA_COMPUTE=1이어도 0이면 ffmpeg 경로 차단",
        validation_alias="ENABLE_FFMPEG" if PYDANTIC_V2 else None
    )

    # ==================== Observation-first (Agent) ====================
    observation_strict_modes: str = Field(
        default="thinking,research",
        description="Comma-separated modes where finish requires at least one tool Observation. Fast mode never requires.",
        validation_alias="MELLOW_OBSERVATION_STRICT_MODES" if PYDANTIC_V2 else None
    )

    # ==================== Prompt templates (no mid-sentence truncation) ====================
    prompt_template_mode: bool = Field(
        default=False,
        description="Use mode-specific mini prompt templates and section-based assembly (drop whole sections). Default OFF.",
        validation_alias="MELLOW_PROMPT_TEMPLATE_MODE" if PYDANTIC_V2 else None
    )
    prompt_history_max_turns_fast: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Max recent history turns in fast mode prompt.",
        validation_alias="MELLOW_PROMPT_HISTORY_MAX_TURNS_FAST" if PYDANTIC_V2 else None
    )
    prompt_history_max_turns_thinking: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Max recent history turns in thinking/research mode prompt.",
        validation_alias="MELLOW_PROMPT_HISTORY_MAX_TURNS_THINKING" if PYDANTIC_V2 else None
    )
    prompt_memories_max: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Max user memory items to inject in prompt.",
        validation_alias="MELLOW_PROMPT_MEMORIES_MAX" if PYDANTIC_V2 else None
    )

    # ==================== Avatar Service Settings ====================
    vtuber_relay_enabled: int = Field(
        default=1,
        ge=0,
        le=1,
        description="Enable VTuber Relay Service (0=disabled, 1=enabled)",
        validation_alias="VTUBER_RELAY_ENABLED" if PYDANTIC_V2 else None
    )
    avatar_ws_port: int = Field(
        default=12393,
        ge=1,
        le=65535,
        description="Avatar service WebSocket port",
        validation_alias="AVATAR_WS_PORT" if PYDANTIC_V2 else None
    )
    avatar_ws_url: str = Field(
        default="ws://localhost:12393",
        description="Avatar service WebSocket URL",
        validation_alias="AVATAR_WS_URL" if PYDANTIC_V2 else None
    )
    avatar_electron_exe: Optional[str] = Field(
        default=None,
        description="Full path to open-llm-vtuber Electron executable (e.g. for admin login launch). Set via MELLOW_AVATAR_ELECTRON_EXE.",
        validation_alias="MELLOW_AVATAR_ELECTRON_EXE" if PYDANTIC_V2 else None
    )
    avatar_auto_launch_enabled: bool = Field(
        default=True,
        description="Auto-launch avatar service on admin login (0=disabled)",
        validation_alias=AliasChoices("AVATAR_AUTO_LAUNCH_ENABLED", "MELLOW_AVATAR_AUTO_LAUNCH_ENABLED") if (PYDANTIC_V2 and AliasChoices) else None
    )

    # ==================== Pydantic Configuration ====================
    if PYDANTIC_V2:
        model_config = SettingsConfigDict(
            env_file=str(_ENV_FILE),  # repo-root .env
            env_file_encoding="utf-8",  # encoding settings
            extra="ignore",  # ignore undefined variables in .env without error
            case_sensitive=False
        )
    else:
        class Config:
            env_file = str(_ENV_FILE)
            env_file_encoding = "utf-8"
            case_sensitive = False

    # ==================== Validators ====================
    if PYDANTIC_V2:
        @field_validator("vram_critical_threshold")
        @classmethod
        def critical_must_exceed_warning(cls, v, info):
            warning = info.data.get("vram_warning_threshold", 80.0)
            if v <= warning:
                raise ValueError(
                    f"Critical threshold ({v}) must be greater than warning ({warning})"
                )
            return v

        @field_validator("model_dir", "data_dir", "output_dir", "template_dir", mode="before")
        @classmethod
        def convert_to_path(cls, v):
            if isinstance(v, str):
                return Path(v)
            return v

        @field_validator("security_level", mode="before")
        @classmethod
        def normalize_security_level(cls, v):
            if isinstance(v, str):
                s = v.strip().upper()
                if s in {"EASY", "NORMAL", "HARD"}:
                    return s
            return "NORMAL"
        
        @field_validator("docs_auto_enabled", "enable_autonomous_agent", "enable_workspace_scanner", "enable_rag_background_indexing", "enable_tool_forge", "enable_model_unload_on_idle", "enable_outbound_http", "enable_web_search", "enable_guardian_apis", "enable_telegram", "enable_edge_tts", "enable_media_compute", "enable_media_ai", "enable_media_upload", "enable_ffmpeg", mode="before")
        @classmethod
        def parse_bool_flag(cls, v):
            """Convert string "0"/"1" to boolean."""
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                v_lower = v.strip().lower()
                if v_lower in ("0", "false", "no", "off", "disabled"):
                    return False
                if v_lower in ("1", "true", "yes", "on", "enabled"):
                    return True
            return bool(v) if v is not None else True

        @model_validator(mode="after")
        def clear_guardian_keys_when_apis_disabled(self):
            """ENABLE_GUARDIAN_APIS=0 이면 메모리에서 Guardian 키를 비워 둠 (설정돼 있어도 무시)."""
            if not self.enable_guardian_apis:
                object.__setattr__(self, "anthropic_api_key", "")
                object.__setattr__(self, "openai_api_key", "")
                object.__setattr__(self, "google_api_key", "")
            return self
    else:
        @validator("vram_critical_threshold")
        def critical_must_exceed_warning(cls, v, values):
            warning = values.get("vram_warning_threshold", 80.0)
            if v <= warning:
                raise ValueError(
                    f"Critical threshold ({v}) must be greater than warning ({warning})"
                )
            return v

        @validator("model_dir", "data_dir", "output_dir", "template_dir", pre=True)
        def convert_to_path(cls, v):
            if isinstance(v, str):
                return Path(v)
            return v

        @validator("security_level", pre=True)
        def normalize_security_level(cls, v):
            if isinstance(v, str):
                s = v.strip().upper()
                if s in {"EASY", "NORMAL", "HARD"}:
                    return s
            return "NORMAL"
        
        @validator("docs_auto_enabled", "enable_autonomous_agent", "enable_workspace_scanner", "enable_rag_background_indexing", "enable_tool_forge", "enable_model_unload_on_idle", "enable_outbound_http", "enable_web_search", "enable_guardian_apis", "enable_telegram", "enable_edge_tts", "enable_media_compute", "enable_media_ai", "enable_media_upload", "enable_ffmpeg", pre=True)
        def parse_bool_flag(cls, v):
            """Convert string "0"/"1" to boolean."""
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                v_lower = v.strip().lower()
                if v_lower in ("0", "false", "no", "off", "disabled"):
                    return False
                if v_lower in ("1", "true", "yes", "on", "enabled"):
                    return True
            return bool(v) if v is not None else True

        @root_validator
        def clear_guardian_keys_when_apis_disabled(cls, values):
            """ENABLE_GUARDIAN_APIS=0 이면 메모리에서 Guardian 키를 비워 둠."""
            if not values.get("enable_guardian_apis", True):
                values["anthropic_api_key"] = ""
                values["openai_api_key"] = ""
                values["google_api_key"] = ""
            return values

    # ==================== Computed Properties ====================
    @property
    def ollama_url(self) -> str:
        """Full Ollama API URL."""
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def comfyui_url(self) -> str:
        """Full ComfyUI API URL."""
        return f"http://{self.comfyui_host}:{self.comfyui_port}"

    @property
    def comfyui_ws_url(self) -> str:
        """ComfyUI WebSocket URL."""
        return f"ws://{self.comfyui_host}:{self.comfyui_port}/ws"

    @property
    def image_output_dir(self) -> Path:
        """
        Directory for generated images.
        FORCED to mellow_link/outputs/images regardless of .env settings.
        """
        return _FORCED_OUTPUT_DIR / "images"

    @property
    def video_output_dir(self) -> Path:
        """
        Directory for generated videos.
        FORCED to mellow_link/outputs/videos regardless of .env settings.
        """
        return _FORCED_OUTPUT_DIR / "videos"

    @property
    def document_output_dir(self) -> Path:
        """
        Directory for generated documents.
        FORCED to mellow_link/outputs/documents regardless of .env settings.
        """
        return _FORCED_OUTPUT_DIR / "documents"

    # ==================== Methods ====================
    def ensure_directories(self) -> None:
        """Create all required directories if they don't exist."""
        directories = [
            self.model_dir,
            self.data_dir,
            _FORCED_OUTPUT_DIR,  # Force outputs inside mellow_link/outputs
            self.image_output_dir,  # mellow_link/outputs/images
            self.video_output_dir,  # mellow_link/outputs/videos
            self.document_output_dir,  # mellow_link/outputs/documents
            self.template_dir,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    def get_cors_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if isinstance(self.cors_origins, str):
            if self.cors_origins == "*":
                return ["*"]
            return [origin.strip() for origin in self.cors_origins.split(",")]
        return self.cors_origins if isinstance(self.cors_origins, list) else ["*"]
    
    def get_limit_for_role(self, role: str) -> int:
        """Get daily request limit for a given role."""
        limits = {
            "admin": self.limit_admin,
            "user": self.limit_user,
            "guest": self.limit_guest,
        }
        return limits.get(role.lower(), self.limit_guest)

    # ==================== 폐쇄망 Gate API (한 곳에서 판정) ====================
    def allow_outbound_http(self) -> bool:
        """외부 HTTP 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_outbound_http)

    def allow_web_search(self) -> bool:
        """웹 검색 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_web_search)

    def allow_guardian_api(self) -> bool:
        """Guardian APIs (Gemini/OpenAI/Anthropic) 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_guardian_apis)

    def allow_telegram(self) -> bool:
        """Telegram 알림/웹훅 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_telegram)

    def allow_edge_tts(self) -> bool:
        """EdgeTTS(MS 클라우드 TTS) 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_edge_tts)

    def allow_media_compute(self) -> bool:
        """미디어 로컬 연산(트랜스코딩 등) 허용 여부."""
        return bool(self.enable_media_compute)

    def allow_media_ai(self) -> bool:
        """미디어 AI(이미지/동영상 생성, upscale, TTS) 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_media_ai)

    def allow_media_upload(self) -> bool:
        """미디어 업로드(YouTube/S3/Drive) 허용 여부. 폐쇄망 기본 False."""
        return bool(self.enable_media_upload)

    def allow_ffmpeg(self) -> bool:
        """FFmpeg/ffprobe 호출 허용 여부. allow_media_compute와 별도."""
        return bool(self.enable_ffmpeg)

    def to_dict(self) -> dict:
        """Export settings to dictionary."""
        return {
            "model_dir": str(self.model_dir),
            "data_dir": str(self.data_dir),
            "output_dir": str(self.output_dir),
            "ollama": {
                "host": self.ollama_host,
                "port": self.ollama_port,
                "url": self.ollama_url,
                "timeout": self.ollama_timeout,
                "models": {
                    "fast": self.fast_model,
                    "thinking": self.thinking_model,
                    "research": self.research_model,
                }
            },
            "comfyui": {
                "host": self.comfyui_host,
                "port": self.comfyui_port,
                "url": self.comfyui_url,
                "timeout": self.comfyui_timeout,
                "default_checkpoint": self.default_checkpoint,
            },
            "vram": {
                "warning_threshold": self.vram_warning_threshold,
                "critical_threshold": self.vram_critical_threshold,
                "poll_interval": self.vram_poll_interval,
                "device_id": self.gpu_device_id,
            },
            "api": {
                "host": self.api_host,
                "port": self.api_port,
                "debug": self.api_debug,
            },
            "log_level": self.log_level,
            "security_level": self.security_level,
            "video_output_dir": str(self.video_output_dir),
        }


# =============================================================================
# Global Settings Access
# =============================================================================

@lru_cache()
def get_settings() -> Settings:
    """
    Get the global settings instance (cached singleton).

    Uses lru_cache to ensure only one Settings instance is created.
    Settings are loaded from environment variables and .env file.

    Returns:
        Global Settings instance

    Example:
        settings = get_settings()
        print(settings.ollama_url)
    """
    return Settings()


def clear_settings_cache() -> None:
    """
    Clear the settings cache to force reload.

    Useful for testing or when environment variables change.
    Evolution 서비스 캐시도 함께 리셋하여, 다음 get_evolution_service() 호출 시 재판정되도록 함.
    """
    get_settings.cache_clear()
    try:
        from mellow_link.core.evolution_factory import reset_evolution_service_cache
        reset_evolution_service_cache()
    except Exception:
        pass


def configure(custom_settings: Settings) -> Settings:
    """
    Configure with custom settings (bypasses cache).

    Note: This doesn't update the cached settings.
    Use clear_settings_cache() first if needed.

    Args:
        custom_settings: Custom Settings instance

    Returns:
        The provided settings instance
    """
    return custom_settings


# =============================================================================
# Convenience Functions
# =============================================================================

def get_ollama_config() -> dict:
    """Get Ollama configuration as dict."""
    s = get_settings()
    return {
        "host": s.ollama_host,
        "port": s.ollama_port,
        "timeout": s.ollama_timeout,
        "models": {
            "fast": s.fast_model,
            "thinking": s.thinking_model,
            "research": s.research_model,
        }
    }


def get_comfyui_config() -> dict:
    """Get ComfyUI configuration as dict."""
    s = get_settings()
    return {
        "host": s.comfyui_host,
        "port": s.comfyui_port,
        "timeout": s.comfyui_timeout,
        "output_dir": s.image_output_dir,
    }


def get_vram_config() -> dict:
    """Get VRAM watchdog configuration as dict."""
    s = get_settings()
    return {
        "warning_threshold": s.vram_warning_threshold,
        "critical_threshold": s.vram_critical_threshold,
        "poll_interval": s.vram_poll_interval,
        "device_id": s.gpu_device_id,
    }

settings = Settings()
