"""
ENABLE_MEDIA_AI=0일 때 사용. 모든 AI 호출을 정책 차단(명확한 메시지에 플래그명 포함).
"""
import logging
from pathlib import Path
from typing import Any

from mellow_link.media.adapters.base import MediaAIAdapter

logger = logging.getLogger(__name__)

_BLOCK_MSG = (
    "ENABLE_MEDIA_AI=0. 미디어 AI(이미지/동영상 생성, upscale, TTS)가 비활성화되어 있습니다. "
    "사용하려면 ENABLE_MEDIA_AI=1로 설정하세요."
)


class NullMediaAIAdapter(MediaAIAdapter):
    """미디어 AI 비활성화 시 사용. generate_image 등 모든 호출에서 정책 차단."""

    async def generate_image(self, request: Any, **kwargs: Any) -> Any:
        logger.info("[NullMediaAIAdapter] generate_image 차단: %s", _BLOCK_MSG)
        raise RuntimeError(_BLOCK_MSG)

    async def generate_video(self, request: Any, **kwargs: Any) -> Any:
        logger.info("[NullMediaAIAdapter] generate_video 차단: %s", _BLOCK_MSG)
        raise RuntimeError(_BLOCK_MSG)

    async def upscale(self, image_path: str | Path, **kwargs: Any) -> Path:
        logger.info("[NullMediaAIAdapter] upscale 차단: %s", _BLOCK_MSG)
        raise RuntimeError(_BLOCK_MSG)

    async def tts(self, text: str, output_path: str | Path, **kwargs: Any) -> Path:
        logger.info("[NullMediaAIAdapter] tts 차단: %s", _BLOCK_MSG)
        raise RuntimeError(_BLOCK_MSG)
