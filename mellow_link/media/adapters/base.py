"""
미디어 어댑터 인터페이스 정의.

- MediaComputeAdapter: 로컬 전용 (ffmpeg 등)
- MediaAIAdapter: 외부/모델 호출 가능
- MediaUploadAdapter: 외부 네트워크 업로드
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class MediaComputeAdapter(ABC):
    """로컬 전용 미디어 연산. FFmpeg 등. 외부 네트워크/API 호출 없음."""

    @abstractmethod
    def probe_duration_seconds(self, video_path: str | Path) -> Optional[float]:
        """동영상 길이(초). ffprobe/ffmpeg 사용. 불가 시 None."""
        ...

    @abstractmethod
    def extend_video_if_needed(
        self,
        input_path: str | Path,
        *,
        target_duration: float = 12.0,
        fps: int = 8,
        mode: str = "boomerang",
        overlap_seconds: float = 0.35,
    ) -> Path:
        """짧은 동영상을 target_duration까지 연장. 실패 시 원본 경로 반환 또는 예외."""
        ...

    @abstractmethod
    def stabilize_video_drift(
        self,
        input_path: str | Path,
        *,
        strength: float = 0.18,
    ) -> Path:
        """줌/드리프트 완화용 보수적 후처리. 실패 시 원본 경로 반환 또는 예외."""
        ...

    @abstractmethod
    def create_ambient_loop_from_image(
        self,
        image_path: str | Path,
        output_path: str | Path,
        *,
        target_duration: float = 12.0,
        fps: int = 8,
        strength: float = 0.18,
        motion_profile: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """정적 이미지를 고정 카메라 ambient loop 영상으로 생성."""
        ...

    @abstractmethod
    def transcode_video(
        self,
        input_path: str | Path,
        output_path: str | Path,
        **kwargs: Any,
    ) -> Path:
        """동영상 트랜스코딩. 실패 시 예외."""
        ...

    @abstractmethod
    def generate_thumbnail(
        self,
        input_path: str | Path,
        output_path: str | Path,
        **kwargs: Any,
    ) -> Path:
        """썸네일 생성."""
        ...

    @abstractmethod
    def merge_audio(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        output_path: str | Path,
        **kwargs: Any,
    ) -> Path:
        """동영상+오디오 합성."""
        ...

    @abstractmethod
    def extract_frames(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        **kwargs: Any,
    ) -> List[Path]:
        """프레임 추출."""
        ...


class MediaAIAdapter(ABC):
    """미디어 AI: 이미지/동영상 생성, upscale, TTS 등. 외부·모델 호출 가능."""

    @abstractmethod
    async def generate_image(self, request: Any, **kwargs: Any) -> Any:
        """이미지 생성 (ComfyUI/API 등)."""
        ...

    @abstractmethod
    async def generate_video(self, request: Any, **kwargs: Any) -> Any:
        """동영상 생성."""
        ...

    @abstractmethod
    async def upscale(self, image_path: str | Path, **kwargs: Any) -> Path:
        """이미지 업스케일."""
        ...

    @abstractmethod
    async def tts(self, text: str, output_path: str | Path, **kwargs: Any) -> Path:
        """TTS (음성 합성)."""
        ...


class MediaUploadAdapter(ABC):
    """미디어 업로드: YouTube, S3, Drive 등. 외부 네트워크."""

    @abstractmethod
    async def upload_youtube(
        self,
        video_path: str | Path,
        **kwargs: Any,
    ) -> Optional[str]:
        """YouTube 업로드. 성공 시 URL 또는 비디오 ID."""
        ...

    @abstractmethod
    async def upload_s3(
        self,
        file_path: str | Path,
        bucket: str,
        key: str,
        **kwargs: Any,
    ) -> Optional[str]:
        """S3 업로드. 성공 시 URL."""
        ...

    @abstractmethod
    async def upload_drive(
        self,
        file_path: str | Path,
        **kwargs: Any,
    ) -> Optional[str]:
        """Google Drive 업로드. 성공 시 URL 또는 파일 ID."""
        ...
