"""
ENABLE_MEDIA_UPLOAD=0일 때 사용. 업로드 no-op, 로그로 이유 남김.
"""
import logging
from pathlib import Path
from typing import Any, Optional

from mellow_link.media.adapters.base import MediaUploadAdapter

logger = logging.getLogger("mellow_link.adapters.media.upload_null")

_REASON = "ENABLE_MEDIA_UPLOAD=0. 미디어 업로드(YouTube/S3/Drive)가 비활성화되어 있습니다. 외부로 전송하지 않습니다."


class NullUploadAdapter(MediaUploadAdapter):
    """미디어 업로드 비활성화 시 사용. no-op, 로그에 이유 기록."""

    async def upload_youtube(self, video_path: str | Path, **kwargs: Any) -> Optional[str]:
        logger.info("[NullUploadAdapter] upload_youtube no-op: %s", _REASON)
        return None

    async def upload_s3(
        self,
        file_path: str | Path,
        bucket: str,
        key: str,
        **kwargs: Any,
    ) -> Optional[str]:
        logger.info("[NullUploadAdapter] upload_s3 no-op: %s", _REASON)
        return None

    async def upload_drive(self, file_path: str | Path, **kwargs: Any) -> Optional[str]:
        logger.info("[NullUploadAdapter] upload_drive no-op: %s", _REASON)
        return None
