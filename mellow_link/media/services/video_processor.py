"""
Video Processor - FFmpeg post-processing for short clips.

모든 FFmpeg/ffprobe 호출은 get_media_compute() 어댑터를 통해서만 수행됩니다.
이 모듈에서는 subprocess/ffmpeg 직접 호출을 하지 않습니다.

Goal:
  - Take a short video (e.g., ~3s) and extend it to target duration (e.g., 12s)
    while keeping fps (default 8fps).

Loop modes:
  A) boomerang (ping-pong): forward + reverse + forward ...
  B) crossfade: concatenate repeated clips with xfade overlap

Notes:
  - Defensive: if the compute adapter fails or is disabled,
    we log and return the original input path where appropriate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoopOptions:
    target_duration: float = 12.0
    fps: int = 8
    mode: str = "boomerang"  # "boomerang" | "crossfade"
    overlap_seconds: float = 0.35  # for crossfade


def _get_compute():
    from mellow_link.media.adapters.factory import get_media_compute
    return get_media_compute()


def probe_duration_seconds(video_path: Path | str) -> Optional[float]:
    """
    Return duration in seconds using the media compute adapter (ffprobe/ffmpeg), or None if unavailable.
    """
    try:
        return _get_compute().probe_duration_seconds(video_path)
    except RuntimeError as e:
        logger.warning("[VideoProcessor] probe_duration_seconds blocked or failed: %s", e)
        return None
    except Exception:
        return None


def extend_video_if_needed(
    input_path: str | Path,
    *,
    target_duration: float = 12.0,
    fps: int = 8,
    mode: str = "boomerang",
    overlap_seconds: float = 0.35,
) -> Path:
    """
    Extend a short video to at least target_duration via the media compute adapter.

    On failure or disabled compute (ENABLE_MEDIA_COMPUTE=0 / ENABLE_FFMPEG=0), returns original path.
    """
    in_path = Path(input_path).resolve()
    if not in_path.exists():
        return in_path

    try:
        return _get_compute().extend_video_if_needed(
            in_path,
            target_duration=target_duration,
            fps=fps,
            mode=mode,
            overlap_seconds=overlap_seconds,
        )
    except RuntimeError as e:
        logger.warning(
            "[VideoProcessor] extend_video_if_needed blocked or failed (hint: ENABLE_MEDIA_COMPUTE/ENABLE_FFMPEG): %s. Returning original: %s",
            e,
            in_path,
        )
        return in_path
    except FileNotFoundError:
        logger.warning(
            "[VideoProcessor] ffmpeg missing; returning original: %s (hint: set MELLOW_FFMPEG_BIN_DIR or MELLOW_FFMPEG_PATH)",
            in_path,
        )
        return in_path
    except Exception as e:
        logger.exception("[VideoProcessor] processing failed: %s", e)
        return in_path


def stabilize_video_drift(
    input_path: str | Path,
    *,
    strength: float = 0.18,
) -> Path:
    """
    Apply a conservative FFmpeg stabilization pass to reduce perceived camera drift.

    On failure or disabled compute, returns original path.
    """
    in_path = Path(input_path).resolve()
    if not in_path.exists():
        return in_path

    try:
        return _get_compute().stabilize_video_drift(in_path, strength=strength)
    except RuntimeError as e:
        logger.warning(
            "[VideoProcessor] stabilize_video_drift blocked or failed (hint: ENABLE_MEDIA_COMPUTE/ENABLE_FFMPEG): %s. Returning original: %s",
            e,
            in_path,
        )
        return in_path
    except FileNotFoundError:
        logger.warning(
            "[VideoProcessor] ffmpeg missing during stabilize_video_drift; returning original: %s",
            in_path,
        )
        return in_path
    except Exception as e:
        logger.exception("[VideoProcessor] stabilize_video_drift failed: %s", e)
        return in_path


def create_ambient_loop_from_image(
    image_path: str | Path,
    *,
    output_path: str | Path,
    target_duration: float = 12.0,
    fps: int = 8,
    strength: float = 0.18,
    motion_profile: Optional[dict] = None,
) -> Path:
    """
    Create a locked-camera ambient loop from a single image via the media compute adapter.
    """
    in_path = Path(image_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"image_path not found: {in_path}")
    try:
        return _get_compute().create_ambient_loop_from_image(
            in_path,
            output_path,
            target_duration=target_duration,
            fps=fps,
            strength=strength,
            motion_profile=motion_profile,
        )
    except RuntimeError as e:
        logger.warning(
            "[VideoProcessor] create_ambient_loop_from_image blocked or failed (hint: ENABLE_MEDIA_COMPUTE/ENABLE_FFMPEG): %s",
            e,
        )
        raise
