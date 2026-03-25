"""ENABLE_MEDIA_COMPUTE=0일 때 사용하는 No-Op/차단 컴퓨트 어댑터."""
from pathlib import Path
from typing import Any, List, Optional

from mellow_link.media.adapters.base import MediaComputeAdapter

_MSG = "ENABLE_MEDIA_COMPUTE=0. 미디어 로컬 연산이 비활성화되어 있습니다."


class NullMediaComputeAdapter(MediaComputeAdapter):
    """미디어 연산 비활성화 시 사용. 모든 호출에서 정책 차단."""

    def probe_duration_seconds(self, video_path: str | Path) -> Optional[float]:
        return None

    def extend_video_if_needed(
        self,
        input_path: str | Path,
        *,
        target_duration: float = 12.0,
        fps: int = 8,
        mode: str = "boomerang",
        overlap_seconds: float = 0.35,
    ) -> Path:
        raise RuntimeError(_MSG)

    def stabilize_video_drift(
        self,
        input_path: str | Path,
        *,
        strength: float = 0.18,
    ) -> Path:
        raise RuntimeError(_MSG)

    def create_ambient_loop_from_image(
        self,
        image_path: str | Path,
        output_path: str | Path,
        *,
        target_duration: float = 12.0,
        fps: int = 8,
        strength: float = 0.18,
        motion_profile: Optional[dict] = None,
    ) -> Path:
        raise RuntimeError(_MSG)

    def transcode_video(self, input_path: str | Path, output_path: str | Path, **kwargs: Any) -> Path:
        raise RuntimeError(_MSG)

    def generate_thumbnail(self, input_path: str | Path, output_path: str | Path, **kwargs: Any) -> Path:
        raise RuntimeError(_MSG)

    def merge_audio(
        self, video_path: str | Path, audio_path: str | Path, output_path: str | Path, **kwargs: Any
    ) -> Path:
        raise RuntimeError(_MSG)

    def extract_frames(self, input_path: str | Path, output_dir: str | Path, **kwargs: Any) -> List[Path]:
        raise RuntimeError(_MSG)
