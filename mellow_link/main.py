"""
Mellow-Link - Local AI Orchestration System

Main entry point for the FastAPI application.
Orchestrates GPU resource sharing between LLM (Ollama) and Image Generation (ComfyUI).

Usage:
    # Run with uvicorn (default bind 127.0.0.1; external access: MELLOW_API_HOST=0.0.0.0)
    uvicorn main:app --host 127.0.0.1 --port 8000

    # Or run directly
    python -m main
"""

import asyncio
import logging
import os
import sys
import traceback
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime

try:
    from fastapi import FastAPI, HTTPException, Depends, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import JSONResponse, HTMLResponse
    from fastapi import Request
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

# =============================================================================
# Mellow-Link Internal Imports
# =============================================================================

from mellow_link import app_state
from mellow_link.config import get_settings, Settings

from mellow_link.core import (
    SystemState, TaskPriority, TransitionResult,
    TaskEvent, EventType,
    Orchestrator, ChatContext,
    ImageRequest,
    bootstrap_admin_account, is_admin_user,
    SecurityError,
)

from mellow_link.infra import (
    VRAMWatchdog, VRAMStatus, create_watchdog,
    log_event,
    get_db, User, UserRole, AgentFolder, ChatSession, GuestUsage,
    create_default_folders_for_user, ensure_user_has_folders, get_or_create_default_session,
    verify_password, get_password_hash, create_access_token, get_current_user,
    check_guest_limit, increment_guest_usage,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

from mellow_link.services import (
    LLMService, create_llm_service,
    DocumentService, DocumentRequest, DocumentType, create_document_service,
    VTuberRelayService, create_vtuber_relay, get_vtuber_relay, set_vtuber_relay,
    RAGService, RAGSearchResult, create_rag_service, get_rag_service, set_rag_service,
)
from mellow_link.media import (
    initialize_media_services,
    register_media_services,
    shutdown_media_services,
    include_media_router,
    media_runtime_lines,
)

from mellow_link.utils import (
    launch_avatar_service, get_avatar_status,
    is_port_active, DEFAULT_AVATAR_WS_PORT,
)

from mellow_link.dependencies import get_admin_user_for_flow_view, resolve_console_viewer
from mellow_link.modules import get_module_registry


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_fmt = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=log_level,
        format=log_fmt,
        datefmt=date_fmt,
    )
    # 모든 로그 출력(메시지 + traceback)에 민감정보 마스킹 적용
    from mellow_link.utils.sensitive_redact import SensitiveRedactingFormatter
    redacting_formatter = SensitiveRedactingFormatter(fmt=log_fmt, datefmt=date_fmt)
    root = logging.getLogger()
    for h in root.handlers:
        h.setFormatter(redacting_formatter)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# =============================================================================
# VRAM Event Handlers
# =============================================================================

async def on_vram_warning(gpu_info) -> None:
    """Handle VRAM warning threshold crossing."""
    logger.warning(
        f"[VRAM WARNING] Usage: {gpu_info.usage_percent:.1f}% "
        f"({gpu_info.used_memory_mb:.0f}/{gpu_info.total_memory_mb:.0f} MB)"
    )


async def on_vram_critical(gpu_info) -> None:
    """[KILL SWITCH] Handle VRAM critical threshold."""
    msg = f"[VRAM CRITICAL] Usage: {gpu_info.usage_percent:.1f}% ({gpu_info.used_memory_mb}MB) - Triggering Kill Switch"
    logger.error(msg)

    log_event(
        event_type="system_alert",
        message=msg,
        context_metadata={
            "level": "critical",
            "vram_usage": gpu_info.usage_percent,
            "action": "force_idle"
        }
    )

    if app_state.orchestrator:
        logger.critical("VRAM Critical! Forcing Orchestrator to IDLE state...")
        await app_state.orchestrator.request_state_change(
            SystemState.IDLE,
            reason="VRAM_CRITICAL_KILL_SWITCH",
            force=True
        )


async def on_vram_recovery(gpu_info) -> None:
    """Handle VRAM returning to normal levels."""
    logger.info(f"[VRAM NORMAL] Usage back to {gpu_info.usage_percent:.1f}%")


# =============================================================================
# Lifecycle Management
# =============================================================================

async def startup() -> None:
    """
    Initialize all services and the orchestrator.

    Startup Sequence:
        1. Load settings
        2. Setup logging
        3. Bootstrap admin account
        4. Initialize LLM Service (Ollama)
        5. Initialize Image/Video/Document/RAG Services
        6. Start VRAM Watchdog
        7. Initialize Orchestrator
        8. Initialize VTuber Relay
        9. Start background tasks
    """
    # 1. Load settings
    app_state.settings = get_settings()
    settings = app_state.settings

    # 2. Setup logging
    setup_logging(settings.log_level)

    logger.info("=" * 60)
    logger.info("Mellow-Link Starting...")
    logger.info("=" * 60)

    # 2.5. Initialize database
    logger.info("[Startup] Initializing database...")
    try:
        from mellow_link.infra.database import init_db
        init_db()
        logger.info("[Startup] Database initialized")
    except Exception as e:
        logger.warning(f"[Startup] Database initialization failed: {e}")

    # 2.6. Bootstrap admin account
    logger.info("[Startup] Checking admin account...")
    if bootstrap_admin_account():
        logger.info("[Startup] Admin account ready")
    else:
        logger.warning("[Startup] Admin bootstrapping failed")

    # 3. Create directories
    settings.ensure_directories()

    # 4. Initialize LLM Service
    logger.info(f"[Startup] Connecting to Ollama at {settings.ollama_url}...")
    app_state.llm_service = create_llm_service(
        host=settings.ollama_host,
        port=settings.ollama_port,
        timeout=settings.ollama_timeout,
        models={
            "fast": settings.fast_model,
            "thinking": settings.thinking_model,
            "research": settings.research_model,
        }
    )
    try:
        await app_state.llm_service.connect()
        await app_state.llm_service.unload_all_models()
        logger.info("[Startup] LLM Service connected")
    except Exception as e:
        logger.warning(f"[Startup] LLM Service connection failed: {e}")

    # 5. Initialize Image / Video Service
    await initialize_media_services(settings)

    # 6. Initialize Document Service
    logger.info("[Startup] Initializing Document Service...")
    app_state.doc_service = create_document_service(
        output_dir=settings.document_output_dir,
        max_workers=settings.doc_max_workers
    )
    await app_state.doc_service.initialize()
    logger.info("[Startup] Document Service initialized")

    # 6.5. Initialize RAG Service
    logger.info("[Startup] Initializing RAG Service...")
    try:
        app_state.rag_service = await create_rag_service(
            embedding_model="nomic-embed-text",
            chunk_size=500,
            ollama_url=settings.ollama_url
        )
        set_rag_service(app_state.rag_service)
        if app_state.rag_service.is_available():
            logger.info("[Startup] RAG Service initialized")
            loaded_count = await app_state.rag_service.load_chunks_from_db()
            if loaded_count > 0:
                logger.info(f"[Startup] Restored {loaded_count} RAG embeddings from database")
        else:
            logger.warning("[Startup] RAG Service initialized but embeddings unavailable")
    except Exception as e:
        logger.warning(f"[Startup] RAG Service initialization failed: {e}")
        app_state.rag_service = None

    # 7. Initialize VRAM Watchdog
    logger.info("[Startup] Initializing VRAM Watchdog...")
    app_state.vram_watchdog = create_watchdog(
        warning_threshold=settings.vram_warning_threshold,
        critical_threshold=settings.vram_critical_threshold,
        poll_interval=settings.vram_poll_interval,
        device_id=settings.gpu_device_id
    )
    app_state.vram_watchdog.on_warning(on_vram_warning)
    app_state.vram_watchdog.on_critical(on_vram_critical)
    app_state.vram_watchdog.on_recovery(on_vram_recovery)

    if VRAMWatchdog.is_gpu_available():
        await app_state.vram_watchdog.start()
        logger.info("[Startup] VRAM Watchdog started")
    else:
        logger.warning("[Startup] No GPU detected - VRAM Watchdog disabled")

    # 7.5. Security Integrity Check
    logger.info("[Startup] Running security integrity verification...")
    try:
        from mellow_link.core.tool_forge import IntegrityGuard
        integrity = IntegrityGuard.verify()
        if integrity.ok:
            logger.info("[Startup] Security integrity verified")
        else:
            logger.critical(
                "[Startup] SECURITY INTEGRITY VIOLATION DETECTED! "
                "tool_forge.py 보안 상수가 변조되었을 수 있습니다: %s",
                integrity.violations,
            )
    except Exception as e:
        logger.warning("[Startup] Security integrity check failed: %s", e)

    # 8. Initialize Orchestrator
    logger.info("[Startup] Initializing Orchestrator...")
    app_state.orchestrator = Orchestrator()
    await app_state.orchestrator.initialize()

    app_state.orchestrator.register_service("llm", app_state.llm_service)
    app_state.orchestrator.register_service("chat", app_state.llm_service)
    app_state.orchestrator.register_service("text", app_state.llm_service)
    register_media_services()
    app_state.orchestrator.register_service("document", app_state.doc_service)

    # 9. Initialize VTuber Relay Service
    if settings.vtuber_relay_enabled == 1:
        logger.info("[Startup] Initializing VTuber Relay Service...")
        avatar_ws_url = settings.avatar_ws_url.rstrip('/')
        if not avatar_ws_url.endswith('/client-ws'):
            avatar_ws_url = f"{avatar_ws_url}/client-ws"

        _vtuber_relay = create_vtuber_relay(
            ws_url=avatar_ws_url,
            reconnect_interval=5.0
        )
        set_vtuber_relay(_vtuber_relay)

        try:
            await _vtuber_relay.start()
            logger.info(f"[Startup] VTuber Relay started (target: {settings.avatar_ws_url})")
        except Exception as e:
            logger.warning(f"[Startup] VTuber Relay start failed (will retry): {e}")
    else:
        logger.info("[Startup] VTuber Relay Service disabled (VTUBER_RELAY_ENABLED=0)")
        set_vtuber_relay(None)

    logger.info("=" * 60)
    logger.info("Mellow-Link Ready!")
    logger.info(f"  Ollama:   {settings.ollama_url}")
    for line in media_runtime_lines(settings):
        logger.info(line)
    if settings.vtuber_relay_enabled == 1:
        logger.info(f"  VTuber:   {settings.avatar_ws_url}")
    else:
        logger.info(f"  VTuber:   DISABLED (VTUBER_RELAY_ENABLED=0)")
    logger.info(f"  API:      http://{settings.api_host}:{settings.api_port}")
    logger.info("=" * 60)

    # 10. Autonomous agent (opt-in)
    try:
        # Check both environment variable (legacy) and settings flag
        env_enabled = (os.getenv("ENABLE_AUTONOMOUS_AGENT") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
        if env_enabled or getattr(settings, "enable_autonomous_agent", False):
            from mellow_link.core.autonomous_agent import run_autonomous_loop
            interval = max(300, int((os.getenv("AUTONOMOUS_INTERVAL_SECONDS") or "7200").strip()))
            app_state.autonomous_agent_task = asyncio.create_task(
                run_autonomous_loop(orchestrator=app_state.orchestrator, shutdown_event=app_state.shutdown_event, interval_seconds=interval)
            )
            logger.info("[Startup] Autonomous agent task started (interval=%ds)", interval)
        else:
            logger.info("[Startup] Autonomous agent disabled (ENABLE_AUTONOMOUS_AGENT=0 or enable_autonomous_agent=False)")
    except Exception as e:
        logger.warning(f"[Startup] Autonomous agent init failed: {e}")

    # 10-1. Workspace scanner
    try:
        if getattr(settings, "enable_workspace_scanner", True) and app_state.llm_service and app_state.orchestrator:
            from mellow_link.core.workspace_scanner import run_periodic_workspace_scan
            from mellow_link.core.agent_brain import AgentBrain

            scan_brain = AgentBrain(
                llm_service=app_state.llm_service,
                max_turns=5,
                model_mode="fast"
            )

            scan_interval = max(60, int((os.getenv("WORKSPACE_SCAN_INTERVAL_SECONDS") or "3600").strip()))
            asyncio.create_task(
                run_periodic_workspace_scan(
                    llm_service=app_state.llm_service,
                    agent_brain=scan_brain,
                    interval_seconds=scan_interval,
                    shutdown_event=app_state.shutdown_event
                )
            )
            logger.info("[Startup] Workspace scanner started (interval=%ds)", scan_interval)
        else:
            if not getattr(settings, "enable_workspace_scanner", True):
                logger.info("[Startup] Workspace scanner disabled (enable_workspace_scanner=False)")
            else:
                logger.info("[Startup] Workspace scanner disabled (LLM unavailable)")
    except Exception as e:
        logger.warning(f"[Startup] Workspace scanner init failed: {e}")

    # 11. SchedulerService
    try:
        if settings.enable_scheduler:
            from mellow_link.core.scheduler_service import get_scheduler_service
            from mellow_link.core.agent_brain import AgentBrain
            sched = get_scheduler_service(
                agent_brain=AgentBrain(llm_service=app_state.llm_service) if (app_state.orchestrator and app_state.llm_service) else None
            )
            asyncio.create_task(sched.start())
            logger.info("[Startup] SchedulerService started")
        else:
            logger.info("[Startup] SchedulerService disabled")
    except Exception as e:
        logger.warning(f"[Startup] SchedulerService init failed: {e}")

    # 12. Metrics collector (async flush; no request-path DB write when enabled)
    try:
        from mellow_link.core.metrics_collector import init_metrics_collector
        collector = init_metrics_collector(
            enabled=getattr(settings, "metrics_enabled", False),
            async_flush=getattr(settings, "metrics_async_flush", True),
            flush_interval_ms=getattr(settings, "metrics_flush_interval_ms", 500),
            flush_batch_size=getattr(settings, "metrics_flush_batch_size", 50),
            max_queue_size=getattr(settings, "metrics_max_queue_size", 5000),
        )
        if collector:
            collector.start_background_flush()
            logger.info("[Startup] Metrics collector started (async flush)")
        else:
            logger.info("[Startup] Metrics collector disabled (MELLOW_METRICS_ENABLED=0)")
    except Exception as e:
        logger.warning(f"[Startup] Metrics collector init failed: {e}")


async def shutdown() -> None:
    """Gracefully shutdown all services."""
    logger.info("=" * 60)
    logger.info("Mellow-Link Shutting Down...")
    logger.info("=" * 60)

    app_state.shutdown_event.set()

    # Cancel all pending tasks before shutting down services
    try:
        loop = asyncio.get_running_loop()
        if not loop.is_closed():
            # Get all pending tasks (except current one)
            pending_tasks = [t for t in asyncio.all_tasks(loop) if not t.done() and t is not asyncio.current_task()]
            if pending_tasks:
                logger.info(f"[Shutdown] Cancelling {len(pending_tasks)} pending tasks...")
                for task in pending_tasks:
                    task.cancel()
                # Wait for tasks to complete cancellation (with timeout)
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending_tasks, return_exceptions=True),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("[Shutdown] Some tasks did not cancel within timeout")
                except Exception as e:
                    logger.debug(f"[Shutdown] Error waiting for task cancellation: {e}")
    except RuntimeError:
        # No event loop or loop is closed
        pass

    # Stop Autonomous agent
    if app_state.autonomous_agent_task:
        try:
            app_state.autonomous_agent_task.cancel()
            try:
                await app_state.autonomous_agent_task
            except asyncio.CancelledError:
                pass
        except Exception:
            pass
        app_state.autonomous_agent_task = None

    # Stop SchedulerService
    try:
        from mellow_link.core.scheduler_service import get_scheduler_service
        sched = get_scheduler_service()
        await sched.stop()
        logger.info("[Shutdown] SchedulerService stopped")
    except Exception:
        pass

    # Stop VRAM watchdog
    if app_state.vram_watchdog and app_state.vram_watchdog.is_running():
        await app_state.vram_watchdog.stop()
        logger.info("[Shutdown] VRAM Watchdog stopped")

    # Stop VTuber relay
    _vtuber_relay = get_vtuber_relay()
    if _vtuber_relay:
        await _vtuber_relay.stop()
        logger.info("[Shutdown] VTuber Relay stopped")

    # Shutdown orchestrator
    if app_state.orchestrator:
        await app_state.orchestrator.shutdown()
        logger.info("[Shutdown] Orchestrator shutdown")

    # Drain background experience tasks before loop teardown.
    try:
        from mellow_link.core.agent_experience import ExperienceHelper
        await ExperienceHelper.shutdown_all()
        logger.info("[Shutdown] Experience helper tasks drained")
    except Exception:
        pass

    # Disconnect services
    if app_state.llm_service:
        await app_state.llm_service.disconnect()
        logger.info("[Shutdown] LLM Service disconnected")

    for service_name in await shutdown_media_services():
        logger.info("[Shutdown] %s disconnected", service_name)

    if app_state.doc_service:
        await app_state.doc_service.shutdown()
        logger.info("[Shutdown] Document Service shutdown")

    if app_state.rag_service:
        if hasattr(app_state.rag_service, '_executor'):
            app_state.rag_service._executor.shutdown(wait=True)
        logger.info("[Shutdown] RAG Service cleaned up")

    # Metrics collector: flush remaining to DB
    try:
        from mellow_link.core.metrics_collector import shutdown_metrics_collector
        shutdown_metrics_collector()
        logger.info("[Shutdown] Metrics collector stopped and flushed")
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("Mellow-Link Stopped")
    logger.info("=" * 60)


# =============================================================================
# FastAPI Application
# =============================================================================

if FASTAPI_AVAILABLE:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """FastAPI lifespan - startup and shutdown events."""
        await startup()
        yield
        await shutdown()

    # Create FastAPI app
    app = FastAPI(
        title="Mellow-Link",
        description="Local AI Orchestration - GPU sharing between LLM and Image Generation",
        version="0.1.0",
        lifespan=lifespan
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "[GLOBAL CATCH] 서버 내부 오류: url=%s error=%s",
            request.url,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "서버 내부 오류가 발생했습니다."},
        )

    # CORS middleware (settings.cors_origins 사용; "*"이면 credentials 불가)
    _settings = get_settings()
    _origins = _settings.cors_origins.strip()
    if _origins == "*":
        _cors_origins = ["*"]
        _cors_credentials = False
    else:
        _cors_origins = [o.strip() for o in _origins.split(",") if o.strip()] or ["*"]
        _cors_credentials = True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=_cors_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static file mounting
    project_root = os.environ.get("MELLOW_LINK_PROJECT_ROOT") or os.environ.get("PROJECT_ROOT")

    if project_root:
        logger.info(f"[Startup] 프로젝트 루트 기준 경로 사용: {project_root}")
        base_dir = project_root
        mellow_link_dir = os.path.join(project_root, "mellow_link")
        _static_dir = os.path.join(mellow_link_dir, "static")
        _outputs_dir = os.path.join(mellow_link_dir, "outputs")
    else:
        logger.info(f"[Startup] 현재 파일 기준 경로 사용 (fallback)")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        _static_dir = os.path.join(base_dir, "static")
        _outputs_dir = os.path.join(base_dir, "outputs")

    # Store paths in app_state for routers to use
    app_state.static_dir = _static_dir
    app_state.outputs_dir = _outputs_dir

    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")
        logger.info(f"[Startup] Static files mounted from: {_static_dir}")
    else:
        logger.warning(f"[Startup] Static directory not found at: {_static_dir}")

    os.makedirs(_outputs_dir, exist_ok=True)
    app.mount("/outputs", StaticFiles(directory=_outputs_dir), name="outputs")
    logger.info(f"[Startup] Outputs directory mounted from: {_outputs_dir}")

    # =========================================================================
    # Register Routers
    # =========================================================================

    from mellow_link.routers.auth import router as auth_router
    from mellow_link.routers.folders import router as folders_router
    from mellow_link.routers.chat import router as chat_router
    from mellow_link.routers.runs import router as runs_router
    from mellow_link.routers.runtime import router as runtime_router
    from mellow_link.routers.generation import router as generation_router
    from mellow_link.routers.system import router as system_router
    from mellow_link.routers.avatar import router as avatar_router
    from mellow_link.routers.admin import router as admin_router
    from mellow_link.routers.evolution import router as evolution_router
    from mellow_link.routers.telegram import router as telegram_router
    from mellow_link.routers.autonomous import router as autonomous_router
    from mellow_link.routers.monitor import router as monitor_router

    app.include_router(auth_router)
    app.include_router(folders_router)
    app.include_router(chat_router)
    app.include_router(generation_router)
    include_media_router(app, app_state.settings or get_settings())
    app.include_router(system_router)
    app.include_router(avatar_router)
    app.include_router(admin_router)
    app.include_router(evolution_router)
    app.include_router(telegram_router)
    app.include_router(autonomous_router)
    app.include_router(monitor_router)
    app.include_router(runs_router)
    app.include_router(runtime_router)
    for module in get_module_registry().list_modules():
        app.include_router(module.router)

    def _static_html_path(name: str) -> Path:
        """Use same static dir as mounted at startup (project_root or __file__)."""
        return Path(_static_dir) / name

    @app.get("/dev-console", tags=["Dev"], response_class=HTMLResponse)
    async def dev_console_view():
        """Dev Console UI (State-Centric 상세 뷰)."""
        html_path = _static_html_path("dev_console.html")
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="dev_console.html not found")
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @app.get("/dev-dashboard", tags=["Dev"], response_class=HTMLResponse)
    async def dev_dashboard_view():
        """Dev Dashboard UI (Run List + Timeline/Events 요약 뷰)."""
        html_path = _static_html_path("dev_dashboard.html")
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="dev_dashboard.html not found")
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @app.get("/operator-console", tags=["Dev"], response_class=HTMLResponse)
    async def operator_console_view():
        """Operator Console UI (운영자 뷰: Chain Highlight, Compressed Status, Todo/Risk/Activity)."""
        html_path = _static_html_path("operator_console.html")
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="operator_console.html not found")
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @app.get("/user-console", tags=["Dev"], response_class=HTMLResponse)
    async def user_console_view(viewer=Depends(resolve_console_viewer)):
        """User Console UI (사용자 뷰: Summary-first 진행/결과 표시)."""
        html_path = _static_html_path("user_console.html")
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="user_console.html not found")
        html = html_path.read_text(encoding="utf-8")
        role = str((viewer or {}).get("role") or "anonymous")
        authenticated = "true" if bool((viewer or {}).get("authenticated")) else "false"
        username = str((viewer or {}).get("username") or "anonymous").replace("'", "\\'")
        inject = (
            "<script>"
            f"window.__CONSOLE_ROLE__='{role}';"
            f"window.__CONSOLE_AUTHENTICATED__={authenticated};"
            f"window.__CONSOLE_USERNAME__='{username}';"
            "</script>"
        )
        html = html.replace("<head>", "<head>\n    " + inject, 1)
        return HTMLResponse(content=html)

    # =========================================================================
    # Monitor endpoints that need special dependencies
    # (They use get_admin_user_for_flow_view which supports query-param auth)
    # =========================================================================

    @app.get("/monitor/flow/view", tags=["Monitor"], dependencies=[Depends(get_admin_user_for_flow_view)])
    async def monitor_flow_view():
        """전용 분석 페이지 HTML 반환 (Admin 전용)."""
        mellow_link_dir = Path(__file__).resolve().parent
        if mellow_link_dir.name != "mellow_link":
            base_dir = mellow_link_dir.parent
            mellow_link_dir = base_dir / "mellow_link"
        static_dir = mellow_link_dir / "static"
        html_path = static_dir / "flow_monitor.html"
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="flow_monitor.html not found")
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @app.get("/monitor/flow/detail/{event_id}", tags=["Monitor"], dependencies=[Depends(get_admin_user_for_flow_view)])
    async def monitor_flow_detail(event_id: str):
        """단일 이벤트 상세 HTML 반환 (Admin 전용)."""
        from mellow_link.infra.memory_database import get_memory_db
        import html as html_module
        db = get_memory_db()
        events = db.get_monitor_flow_timeline(since_minutes=10080, limit=500)
        ev = next((e for e in events if e.get("id") == event_id), None)
        if not ev:
            raise HTTPException(status_code=404, detail="Event not found")

        def esc(s: str) -> str:
            return html_module.escape(str(s) if s is not None else "")

        ev_type = ev.get("type", "CHAT")
        icon = "🚫" if ev_type == "EVOLUTION" and ev.get("is_approved") is False else {
            "CHAT": "🤖", "EVOLUTION": "🏛️", "INSIGHT": "💡", "GOAL": "🎯"
        }.get(ev_type, "•")

        parts = [f'<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>Flow Detail</title>',
                 '<script src="https://cdn.tailwindcss.com"></script>',
                 '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">',
                 '<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>',
                 '</head><body class="bg-[#0f0f23] text-gray-100 p-6 font-sans">']

        parts.append(f'<h1 class="text-xl font-bold mb-4">{icon} {esc(ev_type)} · {esc(ev.get("time", ""))}</h1>')

        if ev_type == "CHAT":
            parts.append(f'<div class="space-y-2 mb-4"><p><b>의도:</b> {esc(ev.get("task_intent", ""))}</p>')
            parts.append(f'<p><b>성공:</b> {"✅" if ev.get("is_success") else "❌"}</p>')
            parts.append(f'<p><b>도구:</b> {esc(", ".join(ev.get("used_tools") or []))}</p>')
            if ev.get("error_message"):
                parts.append(f'<p class="text-red-300"><b>오류:</b> {esc(ev.get("error_message", ""))}</p>')
            raw_steps = ev.get("action_steps")
            if raw_steps:
                try:
                    import json as json_module
                    steps_data = json_module.loads(raw_steps) if isinstance(raw_steps, str) else raw_steps
                    if isinstance(steps_data, list):
                        parts.append('<h2 class="text-lg font-semibold mt-4 mb-2">Action Steps</h2>')
                        parts.append('<div class="space-y-2">')
                        for step in steps_data:
                            if isinstance(step, dict):
                                parts.append(f'<div class="bg-gray-800 p-3 rounded-lg">')
                                parts.append(f'<p class="text-sm"><b>Tool:</b> {esc(step.get("tool", ""))}</p>')
                                if step.get("args"):
                                    parts.append(f'<pre class="text-xs bg-gray-900 p-2 rounded mt-1">{esc(str(step.get("args", "")))}</pre>')
                                if step.get("observation"):
                                    obs = str(step.get("observation", ""))[:500]
                                    parts.append(f'<pre class="text-xs bg-gray-900 p-2 rounded mt-1 text-green-400">{esc(obs)}</pre>')
                                parts.append('</div>')
                        parts.append('</div>')
                except Exception:
                    parts.append(f'<pre class="text-xs bg-gray-800 p-2 rounded">{esc(str(raw_steps)[:2000])}</pre>')
            parts.append('</div>')

        elif ev_type == "EVOLUTION":
            parts.append(f'<div class="space-y-2 mb-4">')
            parts.append(f'<p><b>대상:</b> {esc(ev.get("target_file", ""))}</p>')
            parts.append(f'<p><b>요청:</b> {esc(ev.get("user_request", ""))}</p>')
            parts.append(f'<p><b>승인:</b> {"✅" if ev.get("is_approved") else "❌"}</p>')
            critique = ev.get("critique", "")
            if critique:
                parts.append(f'<p><b>비평:</b> {esc(critique[:500])}</p>')
            proposed_code = ev.get("proposed_code", "")
            if proposed_code:
                parts.append(f'<h2 class="text-lg font-semibold mt-4 mb-2">Proposed Code</h2>')
                parts.append(f'<pre><code class="language-python">{esc(proposed_code[:3000])}</code></pre>')
            parts.append('</div>')

        elif ev_type == "INSIGHT":
            parts.append(f'<div class="space-y-2 mb-4">')
            parts.append(f'<p><b>패턴:</b> {esc(ev.get("pattern", ""))}</p>')
            parts.append(f'<p><b>빈도:</b> {ev.get("frequency", 0)}</p>')
            parts.append(f'<p><b>설명:</b> {esc(ev.get("description", ""))}</p>')
            parts.append('</div>')

        parts.append('<script>hljs.highlightAll();</script></body></html>')
        return HTMLResponse(content="\n".join(parts))


# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Main entry point for running the application."""
    if not FASTAPI_AVAILABLE:
        print("FastAPI not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)

    import uvicorn

    settings = get_settings()

    print(f"Starting Mellow-Link on {settings.api_host}:{settings.api_port}")

    uvicorn.run(
        "mellow_link.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
        log_level="info"
    )


if __name__ == "__main__":
    main()
