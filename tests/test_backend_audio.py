"""
LyricAligner Unit Tests
=======================
Tests for backend/audio_engine.py functionality.

Covers:
- LyricAligner initialization
- Segment data structures
- Utility functions (merge, split, format)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.audio_engine import (
    LyricAligner,
    LyricSegment,
    ModelSize,
    WordSegment,
    format_timestamp,
    merge_segments,
    split_long_segments,
)


# =============================================================================
# Test: WordSegment
# =============================================================================

class TestWordSegment:
    """Test WordSegment dataclass."""

    def test_word_segment_creation(self):
        """WordSegment should be created with correct values."""
        word = WordSegment(
            text="Hello",
            start=0.0,
            end=0.5,
            confidence=0.95,
        )

        assert word.text == "Hello"
        assert word.start == 0.0
        assert word.end == 0.5
        assert word.confidence == 0.95

    def test_word_segment_duration(self):
        """WordSegment duration property should calculate correctly."""
        word = WordSegment(text="test", start=1.0, end=2.5)

        assert word.duration == 1.5

    def test_word_segment_to_dict(self):
        """WordSegment to_dict should return proper dictionary."""
        word = WordSegment(
            text="World",
            start=0.5,
            end=1.0,
            confidence=0.88,
        )

        result = word.to_dict()

        assert result["text"] == "World"
        assert result["start"] == 0.5
        assert result["end"] == 1.0
        assert result["confidence"] == 0.88


# =============================================================================
# Test: LyricSegment
# =============================================================================

class TestLyricSegment:
    """Test LyricSegment dataclass."""

    def test_lyric_segment_creation(self):
        """LyricSegment should be created with correct values."""
        segment = LyricSegment(
            text="Hello world",
            start=0.0,
            end=2.0,
            confidence=0.92,
        )

        assert segment.text == "Hello world"
        assert segment.start == 0.0
        assert segment.end == 2.0
        assert segment.confidence == 0.92
        assert segment.words == []

    def test_lyric_segment_with_words(self):
        """LyricSegment should contain word segments."""
        words = [
            WordSegment("Hello", 0.0, 0.5, 0.95),
            WordSegment("world", 0.6, 1.0, 0.90),
        ]

        segment = LyricSegment(
            text="Hello world",
            start=0.0,
            end=1.0,
            words=words,
        )

        assert len(segment.words) == 2
        assert segment.words[0].text == "Hello"
        assert segment.words[1].text == "world"

    def test_lyric_segment_duration(self):
        """LyricSegment duration should calculate correctly."""
        segment = LyricSegment(text="test", start=1.0, end=3.5)

        assert segment.duration == 2.5

    def test_lyric_segment_to_dict(self):
        """LyricSegment to_dict should include words."""
        words = [WordSegment("Hello", 0.0, 0.5, 0.95)]
        segment = LyricSegment(
            text="Hello",
            start=0.0,
            end=0.5,
            words=words,
            confidence=0.95,
        )

        result = segment.to_dict()

        assert result["text"] == "Hello"
        assert len(result["words"]) == 1
        assert result["words"][0]["text"] == "Hello"

    def test_lyric_segment_from_dict(self):
        """LyricSegment from_dict should reconstruct properly."""
        data = {
            "text": "Hello world",
            "start": 0.0,
            "end": 2.0,
            "confidence": 0.92,
            "words": [
                {"text": "Hello", "start": 0.0, "end": 0.5, "confidence": 0.95},
                {"text": "world", "start": 0.6, "end": 1.5, "confidence": 0.90},
            ],
        }

        segment = LyricSegment.from_dict(data)

        assert segment.text == "Hello world"
        assert segment.start == 0.0
        assert segment.end == 2.0
        assert len(segment.words) == 2


# =============================================================================
# Test: ModelSize
# =============================================================================

class TestModelSize:
    """Test ModelSize enum."""

    def test_model_sizes_exist(self):
        """All expected model sizes should exist."""
        assert ModelSize.TINY.value == "tiny"
        assert ModelSize.BASE.value == "base"
        assert ModelSize.SMALL.value == "small"
        assert ModelSize.MEDIUM.value == "medium"
        assert ModelSize.LARGE_V2.value == "large-v2"
        assert ModelSize.LARGE_V3.value == "large-v3"


# =============================================================================
# Test: LyricAligner Initialization
# =============================================================================

class TestLyricAlignerInit:
    """Test LyricAligner initialization."""

    def test_default_initialization(self):
        """LyricAligner should initialize with defaults."""
        aligner = LyricAligner()

        assert aligner.device == "cuda"
        assert aligner.compute_type == "float16"
        assert aligner._model is None

    def test_custom_initialization(self):
        """LyricAligner should accept custom parameters."""
        aligner = LyricAligner(
            device="cpu",
            compute_type="int8",
            download_root=Path("/tmp/models"),
        )

        assert aligner.device == "cpu"
        assert aligner.compute_type == "int8"
        assert aligner.download_root == Path("/tmp/models")

    def test_supported_extensions(self):
        """LyricAligner should list supported audio formats."""
        assert ".mp3" in LyricAligner.SUPPORTED_EXTENSIONS
        assert ".wav" in LyricAligner.SUPPORTED_EXTENSIONS
        assert ".flac" in LyricAligner.SUPPORTED_EXTENSIONS
        assert ".m4a" in LyricAligner.SUPPORTED_EXTENSIONS


# =============================================================================
# Test: Utility Functions - merge_segments
# =============================================================================

class TestMergeSegments:
    """Test merge_segments utility function."""

    def test_merge_empty_list(self):
        """Empty list should return empty list."""
        result = merge_segments([])
        assert result == []

    def test_merge_single_segment(self):
        """Single segment should be returned as-is."""
        segments = [
            {"text": "Hello", "start": 0.0, "end": 1.0, "confidence": 0.9, "words": []},
        ]

        result = merge_segments(segments)

        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_merge_close_segments(self):
        """Segments within max_gap should be merged."""
        segments = [
            {"text": "Hello", "start": 0.0, "end": 1.0, "confidence": 0.9, "words": []},
            {"text": "world", "start": 1.5, "end": 2.5, "confidence": 0.8, "words": []},
        ]

        result = merge_segments(segments, max_gap=1.0)

        assert len(result) == 1
        assert result[0]["text"] == "Hello world"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 2.5

    def test_no_merge_distant_segments(self):
        """Segments beyond max_gap should not be merged."""
        segments = [
            {"text": "Hello", "start": 0.0, "end": 1.0, "confidence": 0.9, "words": []},
            {"text": "world", "start": 5.0, "end": 6.0, "confidence": 0.8, "words": []},
        ]

        result = merge_segments(segments, max_gap=1.0)

        assert len(result) == 2

    def test_no_merge_exceeds_max_length(self):
        """Merge should respect max_length limit."""
        segments = [
            {"text": "Hello", "start": 0.0, "end": 8.0, "confidence": 0.9, "words": []},
            {"text": "world", "start": 8.5, "end": 10.0, "confidence": 0.8, "words": []},
        ]

        result = merge_segments(segments, max_gap=1.0, max_length=10.0)

        # Total would be 10s, which equals max_length
        assert len(result) == 1

    def test_merge_preserves_words(self):
        """Merged segments should combine words."""
        segments = [
            {
                "text": "Hello",
                "start": 0.0,
                "end": 1.0,
                "confidence": 0.9,
                "words": [{"text": "Hello", "start": 0.0, "end": 0.5, "confidence": 0.9}],
            },
            {
                "text": "world",
                "start": 1.2,
                "end": 2.0,
                "confidence": 0.8,
                "words": [{"text": "world", "start": 1.2, "end": 1.8, "confidence": 0.8}],
            },
        ]

        result = merge_segments(segments, max_gap=1.0)

        assert len(result) == 1
        assert len(result[0]["words"]) == 2


# =============================================================================
# Test: Utility Functions - split_long_segments
# =============================================================================

class TestSplitLongSegments:
    """Test split_long_segments utility function."""

    def test_split_empty_list(self):
        """Empty list should return empty list."""
        result = split_long_segments([])
        assert result == []

    def test_short_segment_unchanged(self):
        """Short segments should pass through unchanged."""
        segments = [
            {"text": "Hello", "start": 0.0, "end": 2.0, "confidence": 0.9, "words": []},
        ]

        result = split_long_segments(segments, max_length=5.0)

        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_split_long_segment_without_words(self):
        """Long segment without words should not be split."""
        segments = [
            {"text": "Long text", "start": 0.0, "end": 10.0, "confidence": 0.9, "words": []},
        ]

        result = split_long_segments(segments, max_length=5.0)

        # Without words, can't split
        assert len(result) == 1

    def test_split_long_segment_with_words(self):
        """Long segment with words should be split at word boundaries."""
        segments = [
            {
                "text": "Hello world this is a test",
                "start": 0.0,
                "end": 10.0,
                "confidence": 0.9,
                "words": [
                    {"text": "Hello", "start": 0.0, "end": 1.5, "confidence": 0.9},
                    {"text": "world", "start": 1.6, "end": 3.0, "confidence": 0.9},
                    {"text": "this", "start": 3.1, "end": 4.5, "confidence": 0.9},
                    {"text": "is", "start": 4.6, "end": 5.5, "confidence": 0.9},
                    {"text": "a", "start": 5.6, "end": 6.0, "confidence": 0.9},
                    {"text": "test", "start": 6.1, "end": 9.5, "confidence": 0.9},
                ],
            },
        ]

        result = split_long_segments(segments, max_length=5.0)

        # Should be split into multiple segments
        assert len(result) >= 2


# =============================================================================
# Test: Utility Functions - format_timestamp
# =============================================================================

class TestFormatTimestamp:
    """Test format_timestamp utility function."""

    def test_format_zero(self):
        """Zero seconds should format correctly."""
        result = format_timestamp(0)
        assert result == "0:00:00.00"

    def test_format_seconds(self):
        """Seconds should format correctly."""
        result = format_timestamp(30.5)
        assert result == "0:00:30.50"

    def test_format_minutes(self):
        """Minutes should format correctly."""
        result = format_timestamp(90.25)
        assert result == "0:01:30.25"

    def test_format_hours(self):
        """Hours should format correctly."""
        result = format_timestamp(3661.5)
        assert result == "1:01:01.50"

    def test_format_large_value(self):
        """Large values should format correctly."""
        result = format_timestamp(7200)  # 2 hours
        assert result == "2:00:00.00"


# =============================================================================
# Test: LyricAligner with Mock (No GPU)
# =============================================================================

class TestLyricAlignerMocked:
    """Test LyricAligner with mocked Whisper model."""

    def test_file_not_found_error(self, tmp_path: Path):
        """Non-existent file should raise FileNotFoundError."""
        aligner = LyricAligner()

        with pytest.raises(FileNotFoundError):
            aligner.transcribe(tmp_path / "nonexistent.mp3")

    def test_unsupported_format_error(self, tmp_path: Path):
        """Unsupported file format should raise ValueError."""
        unsupported_file = tmp_path / "video.mp4"
        unsupported_file.write_bytes(b"dummy")

        aligner = LyricAligner()

        with pytest.raises(ValueError, match="Unsupported audio format"):
            aligner.transcribe(unsupported_file)

    def test_cleanup_vram_without_model(self):
        """cleanup_vram should work even without loaded model."""
        aligner = LyricAligner()
        aligner._cleanup_vram()  # Should not raise

        assert aligner._model is None

    def test_transcribe_with_mock(
        self,
        mock_audio_file: Path,
        mock_lyric_aligner_class,
    ):
        """Transcription should work with mocked model."""
        # Use the mock class
        with patch("backend.audio_engine.LyricAligner", mock_lyric_aligner_class):
            aligner = mock_lyric_aligner_class()
            result = aligner.transcribe(mock_audio_file)

        assert isinstance(result, list)
        assert len(result) > 0
        assert "text" in result[0]
        assert "start" in result[0]
        assert "end" in result[0]
