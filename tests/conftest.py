"""
Test Fixtures for Mellow-Video-Engine
=====================================
Shared fixtures for pytest testing infrastructure.

Provides:
- mock_audio_file: Temporary WAV file for audio tests
- mock_clip_info: Valid ClipInfo object for video tests
- mock_segments: Sample transcription segments
- mock_subtitles: Sample subtitle entries
"""

from __future__ import annotations

import os
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, Generator, List
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Audio Fixtures
# =============================================================================

@pytest.fixture
def mock_audio_file(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Create a temporary dummy WAV file for testing.

    Creates a valid WAV file with:
    - 1 channel (mono)
    - 44100 Hz sample rate
    - 16-bit depth
    - 5 seconds of silence

    Yields:
        Path to the temporary WAV file
    """
    audio_path = tmp_path / "test_audio.wav"

    # WAV parameters
    n_channels = 1
    sample_width = 2  # 16-bit
    framerate = 44100
    n_frames = framerate * 5  # 5 seconds

    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(n_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(framerate)

        # Write silence (zeros)
        silence = b"\x00" * (n_frames * n_channels * sample_width)
        wav_file.writeframes(silence)

    yield audio_path

    # Cleanup
    if audio_path.exists():
        audio_path.unlink()


@pytest.fixture
def mock_audio_file_with_duration(tmp_path: Path):
    """
    Factory fixture to create WAV files with specific durations.

    Usage:
        def test_something(mock_audio_file_with_duration):
            audio_10s = mock_audio_file_with_duration(10.0)
    """
    created_files: List[Path] = []

    def _create_audio(duration: float) -> Path:
        audio_path = tmp_path / f"test_audio_{duration}s.wav"

        n_channels = 1
        sample_width = 2
        framerate = 44100
        n_frames = int(framerate * duration)

        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(n_channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(framerate)
            silence = b"\x00" * (n_frames * n_channels * sample_width)
            wav_file.writeframes(silence)

        created_files.append(audio_path)
        return audio_path

    yield _create_audio

    # Cleanup all created files
    for path in created_files:
        if path.exists():
            path.unlink()


# =============================================================================
# Video/Clip Fixtures
# =============================================================================

@pytest.fixture
def mock_clip_info():
    """
    Create a valid ClipInfo object for testing.

    Returns:
        Factory function that creates ClipInfo with custom parameters
    """
    from backend.video_engine import ClipInfo

    def _create_clip(
        path: Path = Path("/tmp/test_clip.mp4"),
        duration: float = 5.0,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        is_image: bool = False,
    ) -> ClipInfo:
        return ClipInfo(
            path=path,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            is_image=is_image,
        )

    return _create_clip


@pytest.fixture
def mock_clip_files(tmp_path: Path) -> Generator[List[Path], None, None]:
    """
    Create dummy video clip files for testing.

    Note: These are empty files, not actual videos.
    Use with mocked ffmpeg.probe for realistic testing.

    Yields:
        List of 3 dummy clip paths
    """
    clips = []
    for i in range(3):
        clip_path = tmp_path / f"clip_{i:03d}.mp4"
        clip_path.write_bytes(b"dummy video content")
        clips.append(clip_path)

    yield clips

    # Cleanup
    for clip in clips:
        if clip.exists():
            clip.unlink()


# =============================================================================
# Transcription/Segment Fixtures
# =============================================================================

@pytest.fixture
def mock_segments() -> List[Dict[str, Any]]:
    """
    Sample transcription segments matching LyricAligner output format.

    Returns:
        List of segment dictionaries with text, timing, and word data
    """
    return [
        {
            "text": "Hello world",
            "start": 0.0,
            "end": 2.0,
            "confidence": 0.95,
            "words": [
                {"text": "Hello", "start": 0.0, "end": 0.8, "confidence": 0.97},
                {"text": "world", "start": 0.9, "end": 1.8, "confidence": 0.93},
            ],
        },
        {
            "text": "This is a test",
            "start": 2.5,
            "end": 4.5,
            "confidence": 0.92,
            "words": [
                {"text": "This", "start": 2.5, "end": 2.8, "confidence": 0.94},
                {"text": "is", "start": 2.9, "end": 3.1, "confidence": 0.96},
                {"text": "a", "start": 3.2, "end": 3.3, "confidence": 0.90},
                {"text": "test", "start": 3.4, "end": 4.3, "confidence": 0.88},
            ],
        },
        {
            "text": "Final segment here",
            "start": 5.0,
            "end": 7.5,
            "confidence": 0.89,
            "words": [
                {"text": "Final", "start": 5.0, "end": 5.5, "confidence": 0.91},
                {"text": "segment", "start": 5.6, "end": 6.3, "confidence": 0.88},
                {"text": "here", "start": 6.4, "end": 7.3, "confidence": 0.87},
            ],
        },
    ]


@pytest.fixture
def mock_subtitles():
    """
    Sample subtitle entries for VideoComposer testing.

    Returns:
        List of SubtitleEntry objects
    """
    from backend.video_engine import SubtitleEntry

    return [
        SubtitleEntry(text="Hello world", start=0.0, end=2.0),
        SubtitleEntry(text="This is a test", start=2.5, end=4.5),
        SubtitleEntry(text="Final segment here", start=5.0, end=7.5),
    ]


# =============================================================================
# LyricAligner Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_lyric_aligner(mock_segments: List[Dict[str, Any]]):
    """
    Mock LyricAligner that returns immediate results without loading Whisper.

    Patches the transcribe method to return mock_segments instantly.
    """
    with patch("backend.audio_engine.LyricAligner") as MockAligner:
        instance = MagicMock()
        instance.transcribe.return_value = mock_segments
        instance._cleanup_vram.return_value = None
        MockAligner.return_value = instance
        yield instance


@pytest.fixture
def mock_lyric_aligner_class(mock_segments: List[Dict[str, Any]]):
    """
    Provides a mock class for LyricAligner that can be instantiated.

    Usage in tests:
        with patch("backend.audio_engine.LyricAligner", mock_lyric_aligner_class):
            aligner = LyricAligner()
            result = aligner.transcribe(audio_path)
    """
    class MockLyricAligner:
        def __init__(self, *args, **kwargs):
            self.device = kwargs.get("device", "cuda")
            self.compute_type = kwargs.get("compute_type", "float16")
            self._model = None

        def transcribe(
            self,
            audio_path,
            model_size: str = "large-v3",
            language: str = None,
            initial_prompt: str = None,
            progress_callback=None,
        ):
            if progress_callback:
                progress_callback(0.0, "Loading model...")
                progress_callback(0.5, "Transcribing...")
                progress_callback(1.0, "Done")
            return mock_segments

        def _cleanup_vram(self):
            pass

    return MockLyricAligner


# =============================================================================
# VideoComposer Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_ffmpeg_probe():
    """
    Mock ffmpeg.probe to return realistic probe data.
    """
    def _probe(path: str) -> Dict[str, Any]:
        return {
            "format": {
                "duration": "5.0",
                "bit_rate": "8000000",
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "duration": "5.0",
                }
            ],
        }

    with patch("ffmpeg.probe", side_effect=_probe):
        yield _probe


@pytest.fixture
def mock_ffmpeg_run():
    """
    Mock ffmpeg run operations to avoid actual encoding.
    """
    with patch("ffmpeg.run", return_value=None):
        yield


# =============================================================================
# App/UI Fixtures
# =============================================================================

@pytest.fixture
def app_config(tmp_path: Path) -> Dict[str, Path]:
    """
    Create temporary directories for app testing.

    Returns:
        Dict with config_path, workflows_dir, output_dir
    """
    workflows_dir = tmp_path / "workflows"
    output_dir = tmp_path / "output"
    config_dir = tmp_path / "config"

    workflows_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    # Create a dummy workflow file
    workflow_file = workflows_dir / "test_workflow.json"
    workflow_file.write_text('{"nodes": []}')

    return {
        "config_path": config_dir / "settings.yaml",
        "workflows_dir": workflows_dir,
        "output_dir": output_dir,
    }


# =============================================================================
# Async Test Helpers
# =============================================================================

@pytest.fixture
def event_loop_policy():
    """
    Configure event loop for Windows compatibility.
    """
    import asyncio

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    return asyncio.get_event_loop_policy()
