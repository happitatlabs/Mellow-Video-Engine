"""
Canonical media service implementations.
"""

from .image_service import ImageService, create_image_service
from .video_service import VideoService, create_video_service
from .image_schemas import (
    ImageGenerationError,
    ImageResult,
    ImageStatus,
    MAGIC_HEIGHT,
    MAGIC_WIDTH,
    ProgressCallback,
)
from .video_processor import extend_video_if_needed, probe_duration_seconds

__all__ = [
    "ImageService",
    "create_image_service",
    "VideoService",
    "create_video_service",
    "ImageGenerationError",
    "ImageResult",
    "ImageStatus",
    "MAGIC_HEIGHT",
    "MAGIC_WIDTH",
    "ProgressCallback",
    "extend_video_if_needed",
    "probe_duration_seconds",
]
