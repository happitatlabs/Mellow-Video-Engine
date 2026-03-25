"""
미디어 어댑터 정책 차단·no-op 검증.

- ENABLE_MEDIA_AI=0: generate_image 호출 시 정책 차단 메시지에 플래그명 포함
- ENABLE_MEDIA_UPLOAD=0: upload 호출이 외부로 나가지 않음(no-op) + 로그로 이유 남김
- ENABLE_MEDIA_COMPUTE=1 & ENABLE_FFMPEG=0: ffmpeg 호출 경로 차단(명확한 에러)
"""
import asyncio
import logging
import os
import unittest
from unittest.mock import patch

from mellow_link.config.settings import clear_settings_cache, get_settings


def _set_env(key: str, val: str) -> None:
    os.environ[key] = val


def _unset_env(key: str) -> None:
    os.environ.pop(key, None)


class TestMediaAIAdapterBlock(unittest.TestCase):
    """ENABLE_MEDIA_AI=0: generate_image 호출 시 정책 차단 메시지에 플래그명 포함."""

    def setUp(self):
        clear_settings_cache()
        _unset_env("ENABLE_MEDIA_AI")
        _set_env("ENABLE_MEDIA_AI", "0")
        clear_settings_cache()
        # Factory 캐시 초기화
        import mellow_link.adapters.media.factory as factory
        factory._ai_instance = None

    def tearDown(self):
        clear_settings_cache()
        _unset_env("ENABLE_MEDIA_AI")

    def test_generate_image_raises_with_flag_name(self):
        from mellow_link.adapters.media import get_media_ai
        adapter = get_media_ai()
        self.assertFalse(get_settings().allow_media_ai())
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(adapter.generate_image(None))
        self.assertIn("ENABLE_MEDIA_AI", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))

    def test_direct_image_service_generate_raises_when_media_ai_disabled(self):
        """서비스를 직접 호출해도 ENABLE_MEDIA_AI=0이면 동일하게 RuntimeError."""
        from mellow_link.core.schemas import ImageRequest
        from mellow_link.services.image_service import ImageService
        req = ImageRequest(prompt="test")
        svc = ImageService()
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(svc.generate(req))
        self.assertIn("ENABLE_MEDIA_AI", str(ctx.exception))

    def test_direct_image_service_generate_image_raises_when_media_ai_disabled(self):
        """ImageService.generate_image() 직접 호출 시에도 플래그 차단."""
        from mellow_link.core.schemas import ImageRequest
        from mellow_link.services.image_service import ImageService
        req = ImageRequest(prompt="test")
        svc = ImageService()
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(svc.generate_image(req))
        self.assertIn("ENABLE_MEDIA_AI", str(ctx.exception))

    def test_direct_video_service_generate_video_raises_when_media_ai_disabled(self):
        """VideoService.generate_video() 직접 호출 시에도 플래그 차단."""
        from mellow_link.core.schemas import VideoRequest
        from mellow_link.services.video_service import VideoService
        # VideoRequest needs image_path; use a dummy path (will fail later, but we want block at adapter)
        req = VideoRequest(prompt="motion", image_path="/nonexistent/image.png")
        svc = VideoService()
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(svc.generate_video(req))
        self.assertIn("ENABLE_MEDIA_AI", str(ctx.exception))


class TestMediaUploadNoOp(unittest.TestCase):
    """ENABLE_MEDIA_UPLOAD=0: upload 호출이 외부로 나가지 않음(no-op) + 로그로 이유."""

    def setUp(self):
        clear_settings_cache()
        _set_env("ENABLE_MEDIA_UPLOAD", "0")
        clear_settings_cache()
        import mellow_link.adapters.media.factory as factory
        factory._upload_instance = None

    def tearDown(self):
        clear_settings_cache()
        _unset_env("ENABLE_MEDIA_UPLOAD")

    def test_upload_s3_returns_none_and_logs_reason(self):
        import mellow_link.adapters.media.factory as factory
        factory._upload_instance = None
        from mellow_link.adapters.media import get_media_uploader
        adapter = get_media_uploader()
        self.assertFalse(get_settings().allow_media_upload())
        with self.assertLogs("mellow_link.adapters.media.upload_null", level=logging.INFO) as cm:
            result = asyncio.run(adapter.upload_s3("/tmp/fake.mp4", "bucket", "key"))
        self.assertIsNone(result)
        self.assertTrue(
            any("ENABLE_MEDIA_UPLOAD" in m for m in cm.output),
            msg=f"Expected ENABLE_MEDIA_UPLOAD in log output: {cm.output}",
        )


class TestFFmpegBlockWhenComputeOnFfmpegOff(unittest.TestCase):
    """ENABLE_MEDIA_COMPUTE=1 & ENABLE_FFMPEG=0: ffmpeg 호출 경로 차단(명확한 에러)."""

    def setUp(self):
        clear_settings_cache()
        _set_env("ENABLE_MEDIA_COMPUTE", "1")
        _set_env("ENABLE_FFMPEG", "0")
        clear_settings_cache()
        import mellow_link.adapters.media.factory as factory
        factory._compute_instance = None

    def tearDown(self):
        clear_settings_cache()
        _unset_env("ENABLE_MEDIA_COMPUTE")
        _unset_env("ENABLE_FFMPEG")

    def test_transcode_video_raises_with_ffmpeg_flag(self):
        from mellow_link.adapters.media import get_media_compute
        adapter = get_media_compute()
        self.assertTrue(get_settings().allow_media_compute())
        self.assertFalse(get_settings().allow_ffmpeg())
        with self.assertRaises(RuntimeError) as ctx:
            adapter.transcode_video("/nonexistent/in.mp4", "/tmp/out.mp4")
        self.assertIn("ENABLE_FFMPEG", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))
