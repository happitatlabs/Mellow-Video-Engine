"""
Audio Engine - Lyric Alignment
==============================
faster-whisper를 사용한 정밀 가사 추출 엔진.

Features:
- 단어 단위 타임스탬프 추출
- VAD 필터링으로 정확도 향상
- VRAM 자동 정리 (ComfyUI와 GPU 공유)
"""

from __future__ import annotations

import gc
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class WordSegment:
    """단어 단위 세그먼트."""
    text: str
    start: float
    end: float
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
        }


@dataclass
class LyricSegment:
    """가사 라인 세그먼트."""
    text: str
    start: float
    end: float
    words: List[WordSegment] = field(default_factory=list)
    confidence: float = 1.0
    is_modified: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LyricSegment":
        words = [
            WordSegment(**w) for w in data.get("words", [])
        ]
        return cls(
            text=data["text"],
            start=data["start"],
            end=data["end"],
            confidence=data.get("confidence", 1.0),
            words=words,
        )


class ModelSize(str, Enum):
    """Whisper 모델 크기."""
    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_V2 = "large-v2"
    LARGE_V3 = "large-v3"


# =============================================================================
# LyricAligner - Main Class
# =============================================================================

class LyricAligner:
    """
    faster-whisper 기반 가사 정렬 엔진.

    Features:
    - 단어 단위 정밀 타임스탬프
    - VAD 필터로 무음 구간 제거
    - VRAM 자동 정리

    Usage:
    ```python
    aligner = LyricAligner()
    segments = aligner.transcribe("song.mp3", model_size="large-v3")
    for seg in segments:
        print(f"[{seg['start']:.2f} - {seg['end']:.2f}] {seg['text']}")
    ```
    """

    # 지원 오디오 확장자
    SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}

    def __init__(
        self,
        device: str = "cuda",
        compute_type: str = "float16",
        download_root: Optional[Path] = None,
    ) -> None:
        """
        초기화.

        Args:
            device: "cuda" 또는 "cpu"
            compute_type: "float16", "int8" 등
            download_root: 모델 다운로드 경로
        """
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root

        self._model = None
        self._current_model_size: Optional[str] = None

        self.logger = logging.getLogger(self.__class__.__name__)

    def transcribe(
        self,
        audio_path: Union[str, Path],
        model_size: str = "large-v3",
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        오디오 파일에서 가사 추출.

        Args:
            audio_path: 오디오 파일 경로
            model_size: Whisper 모델 크기 ("tiny", "base", "small", "medium", "large-v2", "large-v3")
            language: 언어 코드 (None이면 자동 감지)
            initial_prompt: 초기 프롬프트 (컨텍스트 힌트)
            progress_callback: 진행률 콜백 (progress: 0.0-1.0, status: str)

        Returns:
            [{"start": float, "end": float, "text": str, "confidence": float, "words": [...]}, ...]
        """
        audio_path = Path(audio_path)

        # 파일 검증
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if audio_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported audio format: {audio_path.suffix}")

        self.logger.info(f"Transcribing: {audio_path.name} with {model_size}")

        if progress_callback:
            progress_callback(0.0, "Loading model...")

        try:
            # 모델 로드
            self._load_model(model_size)

            if progress_callback:
                progress_callback(0.1, "Transcribing...")

            # 전사 실행
            segments_generator, info = self._model.transcribe(
                str(audio_path),
                language=language,
                initial_prompt=initial_prompt,
                word_timestamps=True,  # 단어 단위 타임스탬프 필수
                vad_filter=True,  # VAD 필터로 정확도 향상
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=400,
                ),
            )

            # 결과 수집
            segments: List[Dict[str, Any]] = []
            total_duration = info.duration if hasattr(info, 'duration') else 0

            for segment in segments_generator:
                # 진행률 업데이트
                if progress_callback and total_duration > 0:
                    progress = min(0.1 + (segment.end / total_duration) * 0.8, 0.9)
                    progress_callback(progress, f"Processing: {segment.text[:30]}...")

                # 단어 추출
                words = []
                if segment.words:
                    for word in segment.words:
                        words.append({
                            "text": word.word.strip(),
                            "start": round(word.start, 3),
                            "end": round(word.end, 3),
                            "confidence": round(word.probability, 3),
                        })

                segments.append({
                    "text": segment.text.strip(),
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "confidence": round(
                        sum(w.probability for w in segment.words) / len(segment.words)
                        if segment.words else 0.0,
                        3
                    ),
                    "words": words,
                })

            self.logger.info(f"Transcription complete: {len(segments)} segments")

            if progress_callback:
                progress_callback(0.95, "Cleaning up...")

            return segments

        finally:
            # VRAM 정리 (ComfyUI와 GPU 공유하므로 필수)
            self._cleanup_vram()

            if progress_callback:
                progress_callback(1.0, "Done")

    def _load_model(self, model_size: str) -> None:
        """모델 로드."""
        # 이미 같은 모델이 로드되어 있으면 스킵
        if self._model is not None and self._current_model_size == model_size:
            self.logger.debug(f"Model {model_size} already loaded")
            return

        # 기존 모델 정리
        self._cleanup_vram()

        self.logger.info(f"Loading Whisper model: {model_size}")

        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=str(self.download_root) if self.download_root else None,
        )
        self._current_model_size = model_size

        self.logger.info(f"Model loaded: {model_size}")

    def _cleanup_vram(self) -> None:
        """
        VRAM 정리.

        Critical: ComfyUI와 GPU를 공유하므로,
        추론이 끝나면 반드시 VRAM을 운영체제에 반환해야 함.
        """
        if self._model is not None:
            self.logger.debug("Cleaning up VRAM...")

            # 모델 삭제
            del self._model
            self._model = None
            self._current_model_size = None

            # PyTorch 캐시 정리
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except ImportError:
                pass

            # 가비지 컬렉션
            gc.collect()

            self.logger.info("VRAM cleaned up")

    def get_info(self, audio_path: Union[str, Path]) -> Dict[str, Any]:
        """
        오디오 파일 정보 조회.

        Args:
            audio_path: 오디오 파일 경로

        Returns:
            {"duration": float, "sample_rate": int, ...}
        """
        import subprocess
        import json

        audio_path = Path(audio_path)

        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            data = json.loads(result.stdout)
            format_info = data.get("format", {})

            return {
                "duration": float(format_info.get("duration", 0)),
                "bit_rate": int(format_info.get("bit_rate", 0)),
                "format": format_info.get("format_name", ""),
            }

        except Exception as e:
            self.logger.warning(f"Failed to get audio info: {e}")
            return {"duration": 0, "bit_rate": 0, "format": ""}


# =============================================================================
# Utility Functions
# =============================================================================

def merge_segments(
    segments: List[Dict[str, Any]],
    max_gap: float = 1.0,
    max_length: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    짧은 세그먼트들을 병합.

    Args:
        segments: 세그먼트 리스트
        max_gap: 병합할 최대 간격 (초)
        max_length: 병합된 세그먼트 최대 길이 (초)

    Returns:
        병합된 세그먼트 리스트
    """
    if not segments:
        return []

    merged = []
    current = segments[0].copy()
    current["words"] = list(current.get("words", []))

    for seg in segments[1:]:
        gap = seg["start"] - current["end"]
        new_length = seg["end"] - current["start"]

        if gap <= max_gap and new_length <= max_length:
            # 병합
            current["text"] = current["text"] + " " + seg["text"]
            current["end"] = seg["end"]
            current["words"].extend(seg.get("words", []))
            current["confidence"] = (current["confidence"] + seg["confidence"]) / 2
        else:
            # 새 세그먼트 시작
            merged.append(current)
            current = seg.copy()
            current["words"] = list(current.get("words", []))

    merged.append(current)
    return merged


def split_long_segments(
    segments: List[Dict[str, Any]],
    max_length: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    긴 세그먼트를 분할.

    Args:
        segments: 세그먼트 리스트
        max_length: 최대 길이 (초)

    Returns:
        분할된 세그먼트 리스트
    """
    result = []

    for seg in segments:
        duration = seg["end"] - seg["start"]

        if duration <= max_length or not seg.get("words"):
            result.append(seg)
            continue

        # 단어 기준으로 분할
        words = seg["words"]
        current_words = []
        current_start = seg["start"]

        for word in words:
            current_words.append(word)
            current_duration = word["end"] - current_start

            if current_duration >= max_length:
                # 새 세그먼트 생성
                result.append({
                    "text": " ".join(w["text"] for w in current_words),
                    "start": current_start,
                    "end": word["end"],
                    "confidence": sum(w["confidence"] for w in current_words) / len(current_words),
                    "words": current_words,
                })
                current_words = []
                current_start = word["end"]

        # 남은 단어들
        if current_words:
            result.append({
                "text": " ".join(w["text"] for w in current_words),
                "start": current_start,
                "end": seg["end"],
                "confidence": sum(w["confidence"] for w in current_words) / len(current_words),
                "words": current_words,
            })

    return result


def format_timestamp(seconds: float) -> str:
    """초를 SRT/ASS 타임스탬프 형식으로 변환."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:01d}:{minutes:02d}:{secs:05.2f}"


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "LyricAligner",
    "LyricSegment",
    "WordSegment",
    "ModelSize",
    "merge_segments",
    "split_long_segments",
    "format_timestamp",
]
