"""
Mellow-Link - System Router

Endpoints: /, /health, /status, /vram, /vram-status, /metrics, /favicon.ico, /ui, /mellow-link/init
"""

import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mellow_link import app_state
from mellow_link.config import get_settings
from mellow_link.infra import (
    get_db, User, UserRole, AgentFolder, ChatSession,
    ensure_user_has_folders,
)
from mellow_link.modules import get_module_registry
from mellow_link.services import get_vtuber_relay, get_rag_service
from mellow_link.media import media_health_snapshot, media_status_snapshot
from mellow_link.utils import get_avatar_status, DEFAULT_AVATAR_WS_PORT

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class StatusResponse(BaseModel):
    """System status response."""
    state: str
    is_running: bool
    queue_size: int
    active_tasks: int
    services: Dict[str, str]
    vram: Optional[Dict[str, Any]] = None
    uptime_seconds: float


# =============================================================================
# Static Pages
# =============================================================================

@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(os.path.join(app_state.static_dir or ".", "favicon.ico"))


@router.get("/ui", include_in_schema=False)
async def serve_ui() -> FileResponse:
    """Serve the product home UI."""
    return FileResponse(os.path.join(app_state.static_dir or ".", "ui_home.html"))


@router.get("/index.html", include_in_schema=False)
async def serve_legacy_ui() -> FileResponse:
    """Serve the legacy chat UI."""
    return FileResponse(os.path.join(app_state.static_dir or ".", "index.html"))


@router.get("/runtime-console", include_in_schema=False)
async def serve_runtime_console() -> FileResponse:
    """Serve the runtime-only user chat UI."""
    return FileResponse(os.path.join(app_state.static_dir or ".", "runtime_console.html"))


@router.get("/runtime-operator", include_in_schema=False)
async def serve_runtime_operator() -> FileResponse:
    """Serve the runtime-only operator UI."""
    return FileResponse(os.path.join(app_state.static_dir or ".", "runtime_operator.html"))


@router.get("/", tags=["System"], include_in_schema=False)
async def root():
    """Root endpoint - redirect to /ui."""
    return RedirectResponse(url="/runtime-console")


# =============================================================================
# Health & Status
# =============================================================================

@router.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint."""
    services_health = {}

    if app_state.llm_service:
        services_health["llm"] = await app_state.llm_service.health_check()
    if app_state.doc_service:
        services_health["document"] = await app_state.doc_service.health_check()
    if app_state.vram_watchdog:
        services_health["vram"] = await app_state.vram_watchdog.health_check()
    services_health.update(await media_health_snapshot())

    orchestrator_health = {}
    if app_state.orchestrator:
        orchestrator_health = await app_state.orchestrator.health_check()

    return {
        "healthy": all(
            s.get("healthy", False) if isinstance(s, dict) else s
            for s in services_health.values()
        ),
        "timestamp": datetime.now().isoformat(),
        "orchestrator": orchestrator_health,
        "services": services_health
    }


@router.get("/api/modules", tags=["System"])
async def list_modules():
    registry = get_module_registry()
    return {
        "modules": [
            {
                "module_id": m.manifest.module_id,
                "name": m.manifest.name,
                "description": m.manifest.description,
                "run_kind": m.manifest.run_kind,
                "start_path": m.manifest.start_path,
                "icon": m.manifest.icon,
            }
            for m in registry.list_modules()
            if m.manifest.visible_in_ui
        ]
    }


@router.get("/status", response_model=StatusResponse, tags=["System"])
async def get_status():
    """Get current system status including VRAM."""
    vram_info = None
    if app_state.vram_watchdog:
        gpu_info = app_state.vram_watchdog.get_last_info()
        if gpu_info:
            vram_info = gpu_info.to_dict()

    services = {}
    if app_state.llm_service:
        services["llm"] = app_state.llm_service.get_status().name
    if app_state.doc_service:
        services["document"] = app_state.doc_service.get_status().name
    services.update(media_status_snapshot())

    health = await app_state.orchestrator.health_check() if app_state.orchestrator else {}

    return StatusResponse(
        state=app_state.orchestrator.get_state().name if app_state.orchestrator else "NOT_INITIALIZED",
        is_running=health.get("is_running", False),
        queue_size=health.get("queue_size", 0),
        active_tasks=health.get("active_tasks", 0),
        services=services,
        vram=vram_info,
        uptime_seconds=health.get("uptime_seconds", 0)
    )


# =============================================================================
# VRAM
# =============================================================================

@router.get("/vram", tags=["System"])
async def vram_status():
    """
    Get detailed VRAM status.

    Returns GPU info, current status, and thresholds.
    """
    if not app_state.vram_watchdog:
        return {"available": False, "message": "VRAM Watchdog not initialized"}

    gpu_info = await app_state.vram_watchdog.force_check()
    if not gpu_info:
        return {"available": False, "message": "No GPU detected"}

    return {
        "available": True,
        "status": app_state.vram_watchdog.get_status().name,
        "gpu": gpu_info.to_dict(),
        "thresholds": {
            "warning": app_state.vram_watchdog.warning_threshold,
            "critical": app_state.vram_watchdog.critical_threshold
        }
    }


@router.get("/vram-status", tags=["System"])
async def vram_status_simple():
    """
    Get simplified VRAM status for dashboard display.

    Returns status name, usage percent, and memory info.
    """
    if not app_state.vram_watchdog:
        return {"status": "unavailable"}

    gpu_info = app_state.vram_watchdog.get_last_info()
    if not gpu_info:
        return {"status": "no_gpu"}

    return {
        "status": app_state.vram_watchdog.get_status().name,
        "usage_percent": gpu_info.usage_percent,
        "used_mb": gpu_info.used_memory_mb,
        "total_mb": gpu_info.total_memory_mb,
        "free_mb": gpu_info.free_memory_mb
    }


# =============================================================================
# Metrics
# =============================================================================

@router.get("/metrics", tags=["System"])
async def get_metrics():
    """Get orchestrator metrics."""
    if not app_state.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return app_state.orchestrator.get_metrics()


# =============================================================================
# Mellow-Link Init (Session bootstrap)
# =============================================================================

@router.get("/mellow-link/init", tags=["Mellow-Link"])
async def mellow_link_init(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Initialize Mellow-Link session structure for user.

    Returns:
    - folders: User's folders (auto-created if none exist)
    - avatar_status: VTuber connection status
    - is_admin: Whether user is admin (has Secretary folder)
    """
    if not authorization:
        return {
            "success": False,
            "folders": [],
            "avatar_status": {"connected": False},
            "is_admin": False
        }

    token = authorization.replace("Bearer ", "").strip()

    if token.startswith("guest_"):
        return {
            "success": True,
            "folders": [],
            "avatar_status": {"connected": False},
            "is_admin": False,
            "is_guest": True
        }

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role", UserRole.USER.value)

        if not username:
            return {"success": False, "error": "Invalid token"}

        user = db.query(User).filter(User.username == username).first()
        if not user:
            return {"success": False, "error": "User not found"}

        folders = ensure_user_has_folders(db, user.id, role=user.role)

        folder_list = []
        for f in folders:
            session_count = db.query(ChatSession).filter(
                ChatSession.folder_id == f.id,
                ChatSession.is_active == True
            ).count()
            folder_list.append({
                "id": f.id,
                "name": f.name,
                "icon": f.icon,
                "system_prompt": f.system_prompt,
                "use_rag": f.use_rag,
                "is_creative": f.is_creative,
                "session_count": session_count
            })

        avatar_port = app_state.settings.avatar_ws_port if app_state.settings else DEFAULT_AVATAR_WS_PORT
        avatar_status = get_avatar_status(port=avatar_port)
        relay = get_vtuber_relay()

        is_admin = user.role == UserRole.ADMIN.value

        return {
            "success": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role
            },
            "folders": folder_list,
            "avatar_status": {
                "service": avatar_status,
                "relay_connected": relay.is_connected if relay else False
            },
            "is_admin": is_admin,
            "is_guest": False
        }

    except Exception as e:
        logger.error(f"[MellowLink] Init error: {e}")
        return {"success": False, "error": str(e)}


