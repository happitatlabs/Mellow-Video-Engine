"""
Thin facade between core runtime and optional media features.

Core modules should call these helpers instead of referencing media
services/routes directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import FastAPI

from mellow_link import app_state
from mellow_link.config import Settings

logger = logging.getLogger(__name__)


def media_enabled(settings: Settings | None = None) -> bool:
    s = settings or app_state.settings
    return bool(s and s.allow_media_ai())


def include_media_router(app: FastAPI, settings: Settings | None = None) -> bool:
    """
    Register media routes only when media AI is enabled.

    This keeps the core API surface closer to "fully off" when media is disabled.
    """
    if not media_enabled(settings):
        logger.info("[Startup] Media router disabled (ENABLE_MEDIA_AI=0)")
        return False

    from mellow_link.routers.media_generation import router as media_generation_router

    app.include_router(media_generation_router)
    return True


def media_runtime_lines(settings: Settings | None = None) -> List[str]:
    s = settings or app_state.settings
    if not media_enabled(s):
        return ["  Media:    DISABLED (ENABLE_MEDIA_AI=0)"]
    return [f"  Media:    ENABLED ({s.comfyui_url})"]


async def media_health_snapshot() -> Dict[str, Any]:
    """
    Return media-specific health details.

    Empty dict means "media absent/off" from the core system perspective.
    """
    services_health: Dict[str, Any] = {}
    if not media_enabled():
        return services_health

    if app_state.image_service:
        services_health["image"] = await app_state.image_service.health_check()
    if app_state.video_service:
        services_health["video"] = await app_state.video_service.health_check()
    return services_health


def media_status_snapshot() -> Dict[str, str]:
    """
    Return media-specific runtime statuses.

    Empty dict means "media absent/off" from the core system perspective.
    """
    services: Dict[str, str] = {}
    if not media_enabled():
        return services

    if app_state.image_service:
        services["image"] = app_state.image_service.get_status().name
    if app_state.video_service:
        services["video"] = app_state.video_service.get_status().name
    return services
