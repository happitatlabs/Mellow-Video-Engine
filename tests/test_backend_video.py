"""
VideoComposer Unit Tests
========================
Tests for backend/video_engine.py functionality.

Covers:
- xfade offset calculation correctness
- Path escaping for FFmpeg subtitles filter
- ASS subtitle generation
- Clip normalization logic
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.video_engine import (
    ASSGenerator,
    ClipInfo,
    RenderConfig,
    SubtitleEntry,
    VideoComposer,
)


# =============================================================================
# Test: xfade Offset Calculation
# =============================================================================

class TestXfadeOffsetCalculation:
    """
    Test suite for xfade transition offset calculation.

    xfade offset formula:
    - First transition: offset = clip[0].duration - transition_duration
    - Nth transition: offset = sum(clip[0..N]) - N * transition_duration

    Example with 3 clips (5s each) and 1s overlap:
    - Transition 0→1: offset = 5 - 1 = 4s
    - Transition 1→2: offset = 4 + 5 - 1 = 8s (cumulative)
    """

    @pytest.fixture
    def composer(self) -> VideoComposer:
        """Create VideoComposer with 1s transition."""
        config = RenderConfig(
            transition_duration=1.0,
            transition_type="fade",
        )
        return VideoComposer(config=config)

    @pytest.fixture
    def three_clips_5s(self, tmp_path: Path) -> List[Tuple[Path, float]]:
        """
        Create 3 normalized clips with 5s duration each.

        Returns list of (path, duration) tuples as expected by _build_filter_graph.
        """
        clips = []
        for i in range(3):
            clip_path = tmp_path / f"clip_{i}.mp4"
            clip_path.write_bytes(b"dummy")
            clips.append((clip_path, 5.0))
        return clips

    def test_xfade_offset_three_clips(
        self,
        composer: VideoComposer,
        three_clips_5s: List[Tuple[Path, float]],
    ):
        """
        Test xfade offsets with 3 clips of 5s duration and 1s overlap.

        Expected offsets:
        - First xfade (clip 0→1): offset = 5 - 1 = 4.000
        - Second xfade (clip 1→2): offset = 4 + 5 - 1 = 8.000
        """
        filter_complex, labels = composer._build_filter_graph(
            clips=three_clips_5s,
            ass_file=None,
        )

        # Extract offsets from filter string using regex
        # Pattern: offset=X.XXX
        offsets = re.findall(r"offset=(\d+\.\d+)", filter_complex)

        assert len(offsets) == 2, f"Expected 2 xfade transitions, got {len(offsets)}"

        # Convert to floats and verify
        offset_values = [float(o) for o in offsets]

        # First transition: 5s - 1s = 4s
        assert offset_values[0] == pytest.approx(4.0, abs=0.01), \
            f"First offset should be 4.0, got {offset_values[0]}"

        # Second transition: 4s + 5s = 8s (cumulative in output timeline)
        assert offset_values[1] == pytest.approx(8.0, abs=0.01), \
            f"Second offset should be 8.0, got {offset_values[1]}"

    def test_xfade_offset_two_clips(self, composer: VideoComposer, tmp_path: Path):
        """Test xfade offset with 2 clips."""
        clips = [
            (tmp_path / "clip_0.mp4", 3.0),
            (tmp_path / "clip_1.mp4", 4.0),
        ]
        for path, _ in clips:
            path.write_bytes(b"dummy")

        filter_complex, _ = composer._build_filter_graph(clips=clips, ass_file=None)

        offsets = re.findall(r"offset=(\d+\.\d+)", filter_complex)
        assert len(offsets) == 1

        # First transition: 3s - 1s = 2s
        assert float(offsets[0]) == pytest.approx(2.0, abs=0.01)

    def test_xfade_offset_four_clips_varying_duration(
        self,
        tmp_path: Path,
    ):
        """Test xfade with 4 clips of varying durations."""
        config = RenderConfig(transition_duration=0.5, transition_type="dissolve")
        composer = VideoComposer(config=config)

        # Clips with durations: 3s, 4s, 2s, 5s
        clips = [
            (tmp_path / "clip_0.mp4", 3.0),
            (tmp_path / "clip_1.mp4", 4.0),
            (tmp_path / "clip_2.mp4", 2.0),
            (tmp_path / "clip_3.mp4", 5.0),
        ]
        for path, _ in clips:
            path.write_bytes(b"dummy")

        filter_complex, _ = composer._build_filter_graph(clips=clips, ass_file=None)

        offsets = re.findall(r"offset=(\d+\.\d+)", filter_complex)
        assert len(offsets) == 3

        offset_values = [float(o) for o in offsets]

        # Transition 0→1: 3.0 - 0.5 = 2.5
        assert offset_values[0] == pytest.approx(2.5, abs=0.01)

        # Transition 1→2: 2.5 + 4.0 = 6.5
        assert offset_values[1] == pytest.approx(6.5, abs=0.01)

        # Transition 2→3: 6.5 + 2.0 = 8.5
        assert offset_values[2] == pytest.approx(8.5, abs=0.01)

    def test_single_clip_no_xfade(self, composer: VideoComposer, tmp_path: Path):
        """Single clip should not have xfade."""
        clip_path = tmp_path / "single.mp4"
        clip_path.write_bytes(b"dummy")
        clips = [(clip_path, 10.0)]

        filter_complex, labels = composer._build_filter_graph(clips=clips, ass_file=None)

        # Should be simple null filter, no xfade
        assert "xfade" not in filter_complex
        assert "[0:v]null[outv]" in filter_complex or "null" in filter_complex

    def test_xfade_filter_type_in_output(
        self,
        tmp_path: Path,
    ):
        """Verify transition type is correctly set in filter."""
        config = RenderConfig(
            transition_duration=0.5,
            transition_type="wipeleft",
        )
        composer = VideoComposer(config=config)

        clips = [
            (tmp_path / "clip_0.mp4", 5.0),
            (tmp_path / "clip_1.mp4", 5.0),
        ]
        for path, _ in clips:
            path.write_bytes(b"dummy")

        filter_complex, _ = composer._build_filter_graph(clips=clips, ass_file=None)

        assert "transition=wipeleft" in filter_complex


# =============================================================================
# Test: Path Escaping for FFmpeg
# =============================================================================

class TestPathEscaping:
    """
    Test suite for FFmpeg subtitles filter path escaping.

    FFmpeg subtitles filter requires special escaping:
    - Backslashes → Forward slashes
    - Drive colons → Escaped (C: → C\\:)
    - Special characters → Escaped
    """

    def test_linux_path_unchanged(self):
        """Standard Linux path should remain mostly unchanged."""
        path = Path("/tmp/subtitles/file.ass")
        escaped = VideoComposer._escape_path_for_ffmpeg(path)

        # On Linux, should be similar but with special char escaping
        assert "tmp" in escaped
        assert "file.ass" in escaped
        # No backslashes in output
        assert "\\" not in escaped or "\\'" in escaped or "\\[" in escaped

    def test_windows_path_with_spaces(self):
        """Windows path with spaces should be properly escaped."""
        path = Path("C:\\Users\\Name\\My Documents\\file.ass")
        escaped = VideoComposer._escape_path_for_ffmpeg(path)

        # Backslashes should become forward slashes
        assert "\\" not in escaped.replace("\\\\:", "").replace("\\'", "").replace("\\[", "").replace("\\]", "")

        # Drive letter colon should be escaped
        if os.name == 'nt':
            assert "C\\\\:" in escaped or "C:" not in escaped

        # Spaces should be preserved
        assert "My Documents" in escaped or "My%20Documents" in escaped

    def test_windows_drive_letter_escaping(self):
        """Windows drive letter should have colon escaped."""
        path = Path("D:\\Projects\\video.ass")
        escaped = VideoComposer._escape_path_for_ffmpeg(path)

        if os.name == 'nt':
            # The colon after drive letter should be escaped
            assert "D\\\\:" in escaped

    def test_path_with_special_characters(self):
        """Paths with special characters should be escaped."""
        path = Path("/tmp/sub's [test].ass")
        escaped = VideoComposer._escape_path_for_ffmpeg(path)

        # Single quotes should be escaped
        assert "\\'" in escaped
        # Brackets should be escaped
        assert "\\[" in escaped
        assert "\\]" in escaped

    def test_complex_windows_path(self):
        """Complex Windows path with multiple special cases."""
        path = Path("C:\\Users\\John's Files\\Project [2024]\\subs.ass")
        escaped = VideoComposer._escape_path_for_ffmpeg(path)

        # Verify escaping occurred
        assert "\\'" in escaped  # Apostrophe escaped
        assert "\\[" in escaped  # Opening bracket escaped
        assert "\\]" in escaped  # Closing bracket escaped

        if os.name == 'nt':
            # Forward slashes used
            assert "/" in escaped

    def test_simple_path_minimal_escaping(self):
        """Simple path without special chars needs minimal escaping."""
        path = Path("/home/user/video/subtitles.ass")
        escaped = VideoComposer._escape_path_for_ffmpeg(path)

        # Should preserve the basic structure
        assert "home" in escaped
        assert "user" in escaped
        assert "subtitles.ass" in escaped


# =============================================================================
# Test: ASS Subtitle Generation
# =============================================================================

class TestASSGeneration:
    """Test ASS subtitle file generation."""

    def test_ass_file_created(self, tmp_path: Path, mock_subtitles: List[SubtitleEntry]):
        """ASS file should be created with correct content."""
        output_path = tmp_path / "test.ass"
        config = RenderConfig()

        ASSGenerator.generate(
            subtitles=mock_subtitles,
            output_path=output_path,
            config=config,
        )

        assert output_path.exists()

        content = output_path.read_text(encoding="utf-8")

        # Check header sections
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content
        assert "[Events]" in content

        # Check dialogue lines
        assert "Dialogue:" in content
        assert "Hello world" in content
        assert "This is a test" in content

    def test_ass_timestamp_format(self, tmp_path: Path):
        """ASS timestamps should be in H:MM:SS.cc format."""
        subtitles = [
            SubtitleEntry(text="Test", start=3661.55, end=3665.0),  # 1:01:01.55
        ]
        output_path = tmp_path / "timestamp_test.ass"
        config = RenderConfig()

        ASSGenerator.generate(subtitles, output_path, config)

        content = output_path.read_text(encoding="utf-8")

        # 3661.55s = 1:01:01.55
        assert "1:01:01.55" in content

    def test_ass_special_character_escaping(self, tmp_path: Path):
        """Special characters in text should be escaped."""
        subtitles = [
            SubtitleEntry(text="Line 1\nLine 2", start=0, end=2),
        ]
        output_path = tmp_path / "escape_test.ass"
        config = RenderConfig()

        ASSGenerator.generate(subtitles, output_path, config)

        content = output_path.read_text(encoding="utf-8")

        # Newlines should become \N in ASS format
        assert "\\N" in content

    def test_ass_format_time(self):
        """Test internal timestamp formatting."""
        # 0 seconds
        assert ASSGenerator._format_ass_time(0) == "0:00:00.00"

        # 1 minute 30.5 seconds
        assert ASSGenerator._format_ass_time(90.5) == "0:01:30.50"

        # 1 hour 5 minutes 30.25 seconds
        assert ASSGenerator._format_ass_time(3930.25) == "1:05:30.25"


# =============================================================================
# Test: RenderConfig
# =============================================================================

class TestRenderConfig:
    """Test RenderConfig dataclass defaults and customization."""

    def test_default_values(self):
        """Default config should have reasonable values."""
        config = RenderConfig()

        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.transition_duration == 0.5
        assert config.video_codec == "libx264"
        assert config.crf == 18

    def test_custom_values(self):
        """Custom config values should be applied."""
        config = RenderConfig(
            width=1280,
            height=720,
            fps=60,
            transition_duration=1.5,
            transition_type="dissolve",
            preset="fast",
        )

        assert config.width == 1280
        assert config.height == 720
        assert config.fps == 60
        assert config.transition_duration == 1.5
        assert config.transition_type == "dissolve"
        assert config.preset == "fast"


# =============================================================================
# Test: ClipInfo
# =============================================================================

class TestClipInfo:
    """Test ClipInfo dataclass and factory method."""

    def test_clipinfo_creation(self, mock_clip_info):
        """ClipInfo should be created with correct values."""
        clip = mock_clip_info(
            path=Path("/test/clip.mp4"),
            duration=10.5,
            width=1920,
            height=1080,
        )

        assert clip.path == Path("/test/clip.mp4")
        assert clip.duration == 10.5
        assert clip.width == 1920
        assert clip.height == 1080
        assert clip.is_image is False

    def test_clipinfo_image_flag(self, mock_clip_info):
        """Image clips should have is_image=True."""
        clip = mock_clip_info(
            path=Path("/test/image.png"),
            duration=5.0,
            is_image=True,
        )

        assert clip.is_image is True

    @pytest.mark.skipif(
        not Path("/usr/bin/ffprobe").exists() and not Path("C:\\ffmpeg\\bin\\ffprobe.exe").exists(),
        reason="FFprobe not available"
    )
    def test_clipinfo_from_file(self, mock_ffmpeg_probe, tmp_path: Path):
        """ClipInfo.from_file should parse file correctly with mocked probe."""
        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"dummy video content")

        clip = ClipInfo.from_file(video_path)

        assert clip.path == video_path
        assert clip.duration == 5.0
        assert clip.width == 1920
        assert clip.height == 1080


# =============================================================================
# Test: VideoComposer Initialization
# =============================================================================

class TestVideoComposerInit:
    """Test VideoComposer initialization and configuration."""

    def test_default_config(self):
        """VideoComposer should use default config if none provided."""
        composer = VideoComposer()

        assert composer.config is not None
        assert composer.config.width == 1920
        assert composer.config.height == 1080

    def test_custom_config(self):
        """VideoComposer should accept custom config."""
        config = RenderConfig(width=1280, height=720)
        composer = VideoComposer(config=config)

        assert composer.config.width == 1280
        assert composer.config.height == 720

    def test_temp_files_tracking(self):
        """VideoComposer should track temp files for cleanup."""
        composer = VideoComposer()

        assert hasattr(composer, "_temp_files")
        assert isinstance(composer._temp_files, list)
        assert len(composer._temp_files) == 0


# =============================================================================
# Test: Subtitle Normalization
# =============================================================================

class TestSubtitleNormalization:
    """Test subtitle data normalization in VideoComposer."""

    def test_normalize_subtitle_entry(self):
        """SubtitleEntry objects should pass through."""
        composer = VideoComposer()

        subtitles = [
            SubtitleEntry(text="Test 1", start=0, end=2),
            SubtitleEntry(text="Test 2", start=3, end=5),
        ]

        normalized = composer._normalize_subtitles(subtitles)

        assert len(normalized) == 2
        assert all(isinstance(s, SubtitleEntry) for s in normalized)

    def test_normalize_dict_subtitles(self):
        """Dict subtitles should be converted to SubtitleEntry."""
        composer = VideoComposer()

        subtitles = [
            {"text": "Test 1", "start": 0, "end": 2},
            {"text": "Test 2", "start": 3, "end": 5, "style": "Italic"},
        ]

        normalized = composer._normalize_subtitles(subtitles)

        assert len(normalized) == 2
        assert all(isinstance(s, SubtitleEntry) for s in normalized)
        assert normalized[0].text == "Test 1"
        assert normalized[1].style == "Italic"

    def test_normalize_mixed_subtitles(self):
        """Mixed SubtitleEntry and dict should be normalized."""
        composer = VideoComposer()

        subtitles = [
            SubtitleEntry(text="Entry 1", start=0, end=2),
            {"text": "Dict 1", "start": 3, "end": 5},
        ]

        normalized = composer._normalize_subtitles(subtitles)

        assert len(normalized) == 2
        assert normalized[0].text == "Entry 1"
        assert normalized[1].text == "Dict 1"
