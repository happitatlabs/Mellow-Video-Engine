"""
ComfyUI/로컬 서비스 기반 MediaAIAdapter (ENABLE_MEDIA_AI=1일 때).

generate_image → ImageService, generate_video → VideoService 등 위임.
"""
import logging
from pathlib import Path
from typing import Any

from mellow_link.media.adapters.base import MediaAIAdapter
from mellow_link.services.runtime_config import get_comfyui_endpoint, get_output_directories, load_settings
from mellow_link.services.runtime_readiness import assert_media_generation_ready

logger = logging.getLogger(__name__)


class ComfyMediaAIAdapter(MediaAIAdapter):
    """이미지/동영상 생성 등을 기존 ImageService·VideoService에 위임."""

    async def generate_image(self, request: Any, **kwargs: Any) -> Any:
        from mellow_link.media.services.image_service import ImageService
        from mellow_link.media.schemas import ImageRequest
        if not isinstance(request, ImageRequest):
            request = ImageRequest(prompt=getattr(request, "prompt", str(request)))
        settings = load_settings()
        await assert_media_generation_ready(settings)
        endpoint = get_comfyui_endpoint(settings)
        output_dirs = get_output_directories(settings)
        svc = ImageService(
            host=endpoint["host"],
            port=endpoint["port"],
            timeout=float(endpoint["timeout"]),
            output_dir=output_dirs["images"],
        )
        await svc.connect()
        try:
            return await svc._execute_generation(request, **kwargs)
        finally:
            await svc.disconnect()

    async def generate_video(self, request: Any, **kwargs: Any) -> Any:
        from mellow_link.media.services.video_service import VideoService
        settings = load_settings()
        await assert_media_generation_ready(settings)
        endpoint = get_comfyui_endpoint(settings)
        output_dirs = get_output_directories(settings)
        svc = VideoService(
            host=endpoint["host"],
            port=endpoint["port"],
            timeout=float(endpoint["timeout"]),
            output_dir=output_dirs["videos"],
        )
        await svc.connect()
        try:
            return await svc._generate_video_impl(request, **kwargs)
        finally:
            await svc.disconnect()

    async def upscale(self, image_path: str | Path, **kwargs: Any) -> Path:
        # 스텁: 업스케일 워크플로우가 있으면 연결
        raise NotImplementedError("Upscale is not wired yet in ComfyMediaAIAdapter")

    async def tts(self, text: str, output_path: str | Path, **kwargs: Any) -> Path:
        # 스텁: Edge TTS 등 연결 가능
        raise NotImplementedError("TTS is not wired yet in ComfyMediaAIAdapter")
