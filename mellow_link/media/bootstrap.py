"""
Media bootstrap helpers.

Keeps image/video startup wiring out of main.py so the core engine only
depends on a narrow initialization boundary.
"""

from __future__ import annotations

import logging

from mellow_link import app_state
from mellow_link.config import Settings
from mellow_link.media.services import create_image_service, create_video_service

logger = logging.getLogger(__name__)


async def initialize_media_services(settings: Settings) -> None:
    """
    Initialize optional image/video services.

    Failures are logged and do not abort core startup.
    """
    app_state.image_service = None
    app_state.video_service = None

    if not settings.allow_media_ai():
        logger.info("[Startup] Media AI disabled (ENABLE_MEDIA_AI=0): skipping ComfyUI image/video services")
        return

    logger.info(f"[Startup] Connecting to ComfyUI at {settings.comfyui_url}...")
    app_state.image_service = create_image_service(
        host=settings.comfyui_host,
        port=settings.comfyui_port,
        timeout=settings.comfyui_timeout,
        output_dir=settings.image_output_dir,
    )
    try:
        await app_state.image_service.connect()
        logger.info("[Startup] Image Service connected")
    except Exception as e:
        logger.warning(f"[Startup] Image Service connection failed: {e}")

    logger.info(f"[Startup] Initializing Video Service at {settings.comfyui_url}...")
    app_state.video_service = create_video_service(
        host=settings.comfyui_host,
        port=settings.comfyui_port,
        timeout=max(900.0, settings.comfyui_timeout),
        output_dir=getattr(settings, "video_output_dir", settings.output_dir / "videos"),
    )
    try:
        await app_state.video_service.connect()
        logger.info("[Startup] Video Service connected")
    except Exception as e:
        logger.warning(f"[Startup] Video Service connection failed: {e}")


def register_media_services() -> None:
    """
    Register optional media services into the orchestrator service registry.

    Service names are preserved for backward compatibility.
    """
    orchestrator = app_state.orchestrator
    if orchestrator is None:
        return

    orchestrator.register_service("image", app_state.image_service)
    orchestrator.register_service("comfyui", app_state.image_service)
    orchestrator.register_service("video", app_state.video_service)


async def shutdown_media_services() -> list[str]:
    """Disconnect optional media services during shutdown."""
    disconnected: list[str] = []
    if app_state.image_service:
        await app_state.image_service.disconnect()
        disconnected.append("Image Service")

    if app_state.video_service:
        await app_state.video_service.disconnect()
        disconnected.append("Video Service")

    return disconnected
