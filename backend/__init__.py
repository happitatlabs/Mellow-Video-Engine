"""
Mellow-Video-Engine Backend
===========================
오디오 분석 및 비디오 합성 엔진.

Modules:
- audio_engine: faster-whisper 기반 가사 정렬
- video_engine: ffmpeg-python 기반 비디오 합성
"""

from .audio_engine import (
    LyricAligner,
    LyricSegment,
    WordSegment,
    ModelSize,
    merge_segments,
    split_long_segments,
    format_timestamp,
)

from .video_engine import (
    VideoComposer,
    ClipInfo,
    SubtitleEntry,
    RenderConfig,
    ASSGenerator,
)

__all__ = [
    # Audio Engine
    "LyricAligner",
    "LyricSegment",
    "WordSegment",
    "ModelSize",
    "merge_segments",
    "split_long_segments",
    "format_timestamp",
    # Video Engine
    "VideoComposer",
    "ClipInfo",
    "SubtitleEntry",
    "RenderConfig",
    "ASSGenerator",
]
