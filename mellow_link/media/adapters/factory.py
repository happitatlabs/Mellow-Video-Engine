"""
미디어 어댑터 Factory.

OFF면 외부 호출/키 로드/초기화하지 않음. Null 어댑터 반환.
"""
import logging
from typing import Optional

from mellow_link.media.adapters.base import (
    MediaComputeAdapter,
    MediaAIAdapter,
    MediaUploadAdapter,
)
from mellow_link.media.adapters.compute_ffmpeg import LocalFFmpegComputeAdapter
from mellow_link.media.adapters.compute_null import NullMediaComputeAdapter
from mellow_link.media.adapters.ai_null import NullMediaAIAdapter
from mellow_link.media.adapters.ai_comfy import ComfyMediaAIAdapter
from mellow_link.media.adapters.upload_null import NullUploadAdapter

logger = logging.getLogger(__name__)

_compute_instance: Optional[MediaComputeAdapter] = None
_ai_instance: Optional[MediaAIAdapter] = None
_upload_instance: Optional[MediaUploadAdapter] = None


def get_media_compute() -> MediaComputeAdapter:
    """
    ENABLE_MEDIA_COMPUTE=0 → NullMediaComputeAdapter (호출 시 정책 차단).
    ENABLE_MEDIA_COMPUTE=1 → LocalFFmpegComputeAdapter (ENABLE_FFMPEG=0이면 내부에서 차단).
    """
    global _compute_instance
    if _compute_instance is not None:
        return _compute_instance
    try:
        from mellow_link.config.settings import get_settings
        if not get_settings().allow_media_compute():
            _compute_instance = NullMediaComputeAdapter()
            logger.info("[MediaFactory] Using NullMediaComputeAdapter (ENABLE_MEDIA_COMPUTE=0)")
        else:
            _compute_instance = LocalFFmpegComputeAdapter()
            logger.info("[MediaFactory] Using LocalFFmpegComputeAdapter")
    except Exception as e:
        logger.warning("[MediaFactory] allow_media_compute check failed, defaulting to Null: %s", e)
        _compute_instance = NullMediaComputeAdapter()
    return _compute_instance


def get_media_ai() -> MediaAIAdapter:
    """
    ENABLE_MEDIA_AI=0 → NullMediaAIAdapter (정책 차단, 메시지에 플래그명 포함).
    ENABLE_MEDIA_AI=1 → ComfyMediaAIAdapter (ImageService/VideoService 위임).
    OFF일 때 외부 호출/키 로드/초기화 없음.
    """
    global _ai_instance
    if _ai_instance is not None:
        return _ai_instance
    try:
        from mellow_link.config.settings import get_settings
        if not get_settings().allow_media_ai():
            _ai_instance = NullMediaAIAdapter()
            logger.info("[MediaFactory] Using NullMediaAIAdapter (ENABLE_MEDIA_AI=0)")
        else:
            _ai_instance = ComfyMediaAIAdapter()
            logger.info("[MediaFactory] Using ComfyMediaAIAdapter")
    except Exception as e:
        logger.warning("[MediaFactory] allow_media_ai check failed, defaulting to Null: %s", e)
        _ai_instance = NullMediaAIAdapter()
    return _ai_instance


def get_media_uploader() -> MediaUploadAdapter:
    """
    ENABLE_MEDIA_UPLOAD=0 → NullUploadAdapter (no-op, 로그에 이유).
    ENABLE_MEDIA_UPLOAD=1 → 실구현(현재는 Null; 추후 YouTube/S3 등 연결).
    OFF일 때 외부 호출 없음.
    """
    global _upload_instance
    if _upload_instance is not None:
        return _upload_instance
    try:
        from mellow_link.config.settings import get_settings
        if not get_settings().allow_media_upload():
            _upload_instance = NullUploadAdapter()
            logger.info("[MediaFactory] Using NullUploadAdapter (ENABLE_MEDIA_UPLOAD=0)")
        else:
            # 실구현 없음: 업로드도 Null (추후 확장)
            _upload_instance = NullUploadAdapter()
            logger.info("[MediaFactory] Using NullUploadAdapter (no real upload impl yet)")
    except Exception as e:
        logger.warning("[MediaFactory] allow_media_upload check failed, defaulting to Null: %s", e)
        _upload_instance = NullUploadAdapter()
    return _upload_instance
