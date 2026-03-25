"""
Media integration boundary for image/video features.

core startup and routing should depend on this package instead of
importing image/video wiring directly.
"""

from .bootstrap import (
    initialize_media_services,
    register_media_services,
    shutdown_media_services,
)
from .facade import (
    media_enabled,
    include_media_router,
    media_runtime_lines,
    media_health_snapshot,
    media_status_snapshot,
)

__all__ = [
    "initialize_media_services",
    "register_media_services",
    "shutdown_media_services",
    "media_enabled",
    "include_media_router",
    "media_runtime_lines",
    "media_health_snapshot",
    "media_status_snapshot",
]
