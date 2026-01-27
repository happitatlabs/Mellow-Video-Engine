"""
Compositor Module
=================
State 4: Post-Processing & Overlay
Handles video compositing with FFmpeg including titles, credits, and lyrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.project_state import ProjectState
    from core.fsm_manager import FSMManager

from core.fsm_manager import StateHandler
from core.project_state import VideoClip, LyricSegment, AssetStatus

logger = logging.getLogger(__name__)


@dataclass
class TextOverlay:
    """Configuration for a text overlay."""
    text: str
    x: str = "(w-text_w)/2"  # FFmpeg expression for center
    y: str = "h-100"         # Near bottom
    start_time: float = 0.0
    end_time: Optional[float] = None
    font_file: Optional[str] = None
    font_size: int = 48
    font_color: str = "white"
    border_color: str = "black"
    border_width: int = 2
    fade_in: float = 0.0
    fade_out: float = 0.0


@dataclass
class SubtitleEntry:
    """SRT subtitle entry."""
    index: int
    start_time: float
    end_time: float
    text: str

    def to_srt_format(self) -> str:
        """Convert to SRT format string."""
        def format_time(seconds: float) -> str:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds % 1) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        return (
            f"{self.index}\n"
            f"{format_time(self.start_time)} --> {format_time(self.end_time)}\n"
            f"{self.text}\n"
        )


class FFmpegCompositor:
    """
    FFmpeg-based video compositor.

    Features:
    - Video concatenation with crossfade
    - Text overlay (titles, credits)
    - Subtitle burning (lyrics)
    - Audio mixing
    - Hardware acceleration support
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        config: dict = None,
    ):
        """
        Initialize FFmpegCompositor.

        Args:
            ffmpeg_path: Path to ffmpeg executable
            ffprobe_path: Path to ffprobe executable
            config: FFmpeg configuration from settings.yaml
        """
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

        # Verify FFmpeg is available
        self._verify_ffmpeg()

    def _verify_ffmpeg(self) -> None:
        """Verify FFmpeg is installed and accessible."""
        try:
            result = subprocess.run(
                [self.ffmpeg, "-version"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                version_line = result.stdout.split("\n")[0]
                self.logger.info(f"FFmpeg found: {version_line}")
            else:
                raise RuntimeError("FFmpeg not working properly")
        except FileNotFoundError:
            raise RuntimeError(
                f"FFmpeg not found at '{self.ffmpeg}'. "
                "Please install FFmpeg or provide the correct path."
            )

    async def concatenate_clips(
        self,
        clips: list[dict],
        output_path: Path,
        crossfade_duration: float = 1.0,
    ) -> Path:
        """
        Concatenate video clips with crossfade transitions.

        Args:
            clips: List of clip dictionaries with file_path and duration
            output_path: Output video path
            crossfade_duration: Crossfade duration in seconds

        Returns:
            Path to concatenated video
        """
        if not clips:
            raise ValueError("No clips to concatenate")

        if len(clips) == 1:
            # Single clip, just copy
            return await self._copy_video(clips[0]["file_path"], output_path)

        # Build complex filter for crossfade
        inputs = []
        filter_complex = []

        for i, clip in enumerate(clips):
            inputs.extend(["-i", str(clip["file_path"])])

        # Generate crossfade filter
        # [0:v][1:v]xfade=transition=fade:duration=1:offset=3[v01];
        # [v01][2:v]xfade=transition=fade:duration=1:offset=6[v012];...

        current_output = "[0:v]"
        current_duration = clips[0].get("duration", 4.0)

        for i in range(1, len(clips)):
            next_input = f"[{i}:v]"
            output_label = f"[v{i}]"
            offset = current_duration - crossfade_duration

            filter_complex.append(
                f"{current_output}{next_input}xfade=transition=fade:"
                f"duration={crossfade_duration}:offset={offset:.3f}{output_label}"
            )

            current_output = output_label
            current_duration = offset + clips[i].get("duration", 4.0)

        # Final output label
        final_label = current_output.strip("[]")

        # Build command
        cmd = [self.ffmpeg, "-y"]
        cmd.extend(inputs)
        cmd.extend([
            "-filter_complex", ";".join(filter_complex),
            "-map", f"[{final_label}]",
            "-c:v", self.config.get("video_codec", "libx264"),
            "-preset", self.config.get("preset", "medium"),
            "-crf", "18",
            str(output_path),
        ])

        await self._run_ffmpeg(cmd)
        return output_path

    async def add_audio(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
    ) -> Path:
        """
        Add audio track to video.

        Args:
            video_path: Input video path
            audio_path: Audio file path
            output_path: Output path

        Returns:
            Path to output video
        """
        cmd = [
            self.ffmpeg, "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", self.config.get("audio_codec", "aac"),
            "-b:a", self.config.get("audio_bitrate", "192k"),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(output_path),
        ]

        await self._run_ffmpeg(cmd)
        return output_path

    async def add_text_overlay(
        self,
        video_path: Path,
        overlays: list[TextOverlay],
        output_path: Path,
    ) -> Path:
        """
        Add text overlays to video.

        Args:
            video_path: Input video path
            overlays: List of TextOverlay configurations
            output_path: Output path

        Returns:
            Path to output video
        """
        if not overlays:
            return video_path

        # Build drawtext filters
        filters = []

        for overlay in overlays:
            # Escape text for FFmpeg
            escaped_text = (
                overlay.text
                .replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
            )

            filter_parts = [
                f"drawtext=text='{escaped_text}'",
                f"x={overlay.x}",
                f"y={overlay.y}",
                f"fontsize={overlay.font_size}",
                f"fontcolor={overlay.font_color}",
                f"borderw={overlay.border_width}",
                f"bordercolor={overlay.border_color}",
            ]

            if overlay.font_file:
                filter_parts.append(f"fontfile='{overlay.font_file}'")

            # Time-based enable
            if overlay.end_time:
                filter_parts.append(
                    f"enable='between(t,{overlay.start_time},{overlay.end_time})'"
                )
            elif overlay.start_time > 0:
                filter_parts.append(f"enable='gte(t,{overlay.start_time})'")

            filters.append(":".join(filter_parts))

        filter_string = ",".join(filters)

        cmd = [
            self.ffmpeg, "-y",
            "-i", str(video_path),
            "-vf", filter_string,
            "-c:v", self.config.get("video_codec", "libx264"),
            "-c:a", "copy",
            str(output_path),
        ]

        await self._run_ffmpeg(cmd)
        return output_path

    async def burn_subtitles(
        self,
        video_path: Path,
        srt_path: Path,
        output_path: Path,
        style: dict = None,
    ) -> Path:
        """
        Burn subtitles into video.

        Args:
            video_path: Input video path
            srt_path: SRT subtitle file path
            output_path: Output path
            style: Optional ASS style overrides

        Returns:
            Path to output video
        """
        # Build subtitle filter with styling
        style_parts = []
        if style:
            if style.get("font_size"):
                style_parts.append(f"FontSize={style['font_size']}")
            if style.get("font_color"):
                style_parts.append(f"PrimaryColour={style['font_color']}")
            if style.get("outline_color"):
                style_parts.append(f"OutlineColour={style['outline_color']}")
            if style.get("outline_width"):
                style_parts.append(f"Outline={style['outline_width']}")
            if style.get("margin_v"):
                style_parts.append(f"MarginV={style['margin_v']}")

        style_string = ",".join(style_parts) if style_parts else ""

        # Escape path for FFmpeg
        escaped_srt = str(srt_path).replace("\\", "/").replace(":", "\\:")

        if style_string:
            filter_str = f"subtitles='{escaped_srt}':force_style='{style_string}'"
        else:
            filter_str = f"subtitles='{escaped_srt}'"

        cmd = [
            self.ffmpeg, "-y",
            "-i", str(video_path),
            "-vf", filter_str,
            "-c:v", self.config.get("video_codec", "libx264"),
            "-c:a", "copy",
            str(output_path),
        ]

        await self._run_ffmpeg(cmd)
        return output_path

    async def _copy_video(self, input_path: str, output_path: Path) -> Path:
        """Copy video without re-encoding."""
        cmd = [
            self.ffmpeg, "-y",
            "-i", str(input_path),
            "-c", "copy",
            str(output_path),
        ]
        await self._run_ffmpeg(cmd)
        return output_path

    async def _run_ffmpeg(self, cmd: list[str]) -> None:
        """Run FFmpeg command asynchronously."""
        self.logger.debug(f"Running: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            self.logger.error(f"FFmpeg error: {error_msg}")
            raise RuntimeError(f"FFmpeg failed: {error_msg}")

        self.logger.debug("FFmpeg command completed successfully")


class Compositor:
    """
    High-level compositor for music video post-processing.

    Orchestrates FFmpeg operations for:
    - Video assembly
    - Title cards
    - Credits
    - Lyrics subtitles
    """

    def __init__(
        self,
        ffmpeg_config: dict,
        fonts_config: dict,
        overlay_config: dict,
        output_dir: Path,
    ):
        """
        Initialize Compositor.

        Args:
            ffmpeg_config: FFmpeg configuration
            fonts_config: Font configuration for text
            overlay_config: Overlay text templates
            output_dir: Output directory
        """
        self.ffmpeg = FFmpegCompositor(
            ffmpeg_path=ffmpeg_config.get("path", "ffmpeg"),
            config=ffmpeg_config,
        )
        self.fonts_config = fonts_config
        self.overlay_config = overlay_config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(self.__class__.__name__)

    def generate_srt(
        self,
        segments: list[LyricSegment],
        output_path: Path,
    ) -> Path:
        """
        Generate SRT subtitle file from lyric segments.

        Args:
            segments: List of lyric segments
            output_path: Output SRT file path

        Returns:
            Path to generated SRT file
        """
        entries = []

        for i, segment in enumerate(segments, start=1):
            if segment.text.strip():
                entry = SubtitleEntry(
                    index=i,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    text=segment.text,
                )
                entries.append(entry)

        # Write SRT file
        with open(output_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(entry.to_srt_format())
                f.write("\n")

        self.logger.info(f"Generated SRT with {len(entries)} entries: {output_path}")
        return output_path

    def build_title_overlay(
        self,
        metadata: dict,
        video_duration: float,
    ) -> list[TextOverlay]:
        """
        Build title card text overlays.

        Args:
            metadata: Project metadata
            video_duration: Total video duration

        Returns:
            List of TextOverlay objects
        """
        overlays = []
        title_config = self.overlay_config.get("title_template", {})

        fade_in = title_config.get("fade_in", 1.5)
        duration = title_config.get("duration", 4.0)
        fade_out = title_config.get("fade_out", 1.5)

        # Main title
        if metadata.get("title"):
            overlays.append(TextOverlay(
                text=metadata["title"],
                x="(w-text_w)/2",
                y="(h-text_h)/2-50",
                start_time=0,
                end_time=duration + fade_in,
                font_file=self.fonts_config.get("title", {}).get("path"),
                font_size=self.fonts_config.get("title", {}).get("size", 72),
                font_color=self.fonts_config.get("title", {}).get("color", "white"),
                fade_in=fade_in,
                fade_out=fade_out,
            ))

        # Artist name
        if metadata.get("artist"):
            overlays.append(TextOverlay(
                text=metadata["artist"],
                x="(w-text_w)/2",
                y="(h-text_h)/2+30",
                start_time=0.5,
                end_time=duration + fade_in + 0.5,
                font_file=self.fonts_config.get("subtitle", {}).get("path"),
                font_size=self.fonts_config.get("subtitle", {}).get("size", 36),
                font_color=self.fonts_config.get("subtitle", {}).get("color", "white"),
                fade_in=fade_in,
            ))

        return overlays

    def build_credits_overlay(
        self,
        metadata: dict,
        video_duration: float,
    ) -> list[TextOverlay]:
        """
        Build end credits text overlays.

        Args:
            metadata: Project metadata
            video_duration: Total video duration

        Returns:
            List of TextOverlay objects
        """
        overlays = []
        credits_config = self.overlay_config.get("credits_template", {})
        lines = credits_config.get("lines", [])

        start_time = video_duration - 10  # Last 10 seconds
        y_offset = 200

        for i, line_template in enumerate(lines):
            # Format template with metadata
            text = line_template.format(**metadata)

            overlays.append(TextOverlay(
                text=text,
                x="(w-text_w)/2",
                y=f"h-{y_offset - i * 40}",
                start_time=start_time + i * 0.5,
                font_file=self.fonts_config.get("subtitle", {}).get("path"),
                font_size=24,
                font_color="white",
            ))

        return overlays

    async def compose_final_video(
        self,
        project: ProjectState,
        progress_callback: Optional[callable] = None,
    ) -> Path:
        """
        Compose the final music video.

        Args:
            project: Project state with all assets
            progress_callback: Optional progress callback

        Returns:
            Path to final video
        """
        self.logger.info("Starting final video composition")

        # Temporary files
        temp_dir = self.output_dir / "temp"
        temp_dir.mkdir(exist_ok=True)

        try:
            # Step 1: Concatenate video clips
            if progress_callback:
                await progress_callback("concat", 0, 5)

            clips = [
                {"file_path": c.file_path, "duration": c.duration}
                for c in project.video_clips.values()
                if c.status == AssetStatus.GENERATED and c.file_path
            ]

            if not clips:
                raise ValueError("No video clips available for composition")

            concat_path = temp_dir / "concat.mp4"
            await self.ffmpeg.concatenate_clips(clips, concat_path)

            # Step 2: Add audio
            if progress_callback:
                await progress_callback("audio", 1, 5)

            if project.audio_file_path:
                with_audio_path = temp_dir / "with_audio.mp4"
                await self.ffmpeg.add_audio(
                    concat_path,
                    project.audio_file_path,
                    with_audio_path,
                )
            else:
                with_audio_path = concat_path

            # Step 3: Add title overlay
            if progress_callback:
                await progress_callback("title", 2, 5)

            title_overlays = self.build_title_overlay(
                metadata=project.metadata.to_dict(),
                video_duration=project.audio_duration,
            )

            if title_overlays:
                with_title_path = temp_dir / "with_title.mp4"
                await self.ffmpeg.add_text_overlay(
                    with_audio_path,
                    title_overlays,
                    with_title_path,
                )
            else:
                with_title_path = with_audio_path

            # Step 4: Add credits
            if progress_callback:
                await progress_callback("credits", 3, 5)

            credits_overlays = self.build_credits_overlay(
                metadata=project.metadata.to_dict(),
                video_duration=project.audio_duration,
            )

            if credits_overlays:
                with_credits_path = temp_dir / "with_credits.mp4"
                await self.ffmpeg.add_text_overlay(
                    with_title_path,
                    credits_overlays,
                    with_credits_path,
                )
            else:
                with_credits_path = with_title_path

            # Step 5: Burn lyrics subtitles
            if progress_callback:
                await progress_callback("subtitles", 4, 5)

            srt_path = temp_dir / "lyrics.srt"
            self.generate_srt(project.lyrics_segments, srt_path)

            final_path = self.output_dir / f"{project.project_name}.mp4"

            lyrics_style = self.fonts_config.get("lyrics", {})
            await self.ffmpeg.burn_subtitles(
                with_credits_path,
                srt_path,
                final_path,
                style={
                    "font_size": lyrics_style.get("size", 48),
                    "outline_width": lyrics_style.get("outline_width", 2),
                    "margin_v": 80,
                },
            )

            if progress_callback:
                await progress_callback("complete", 5, 5)

            self.logger.info(f"Final video composed: {final_path}")
            return final_path

        finally:
            # Cleanup temp files (optional)
            pass


# ============================================================================
# FSM Handler
# ============================================================================

class PostProcessingHandler(StateHandler):
    """FSM Handler for POST_PROCESSING state."""

    def __init__(
        self,
        fsm: FSMManager,
        ffmpeg_config: dict,
        fonts_config: dict,
        overlay_config: dict,
        output_dir: Path,
    ):
        super().__init__(fsm)
        self.ffmpeg_config = ffmpeg_config
        self.fonts_config = fonts_config
        self.overlay_config = overlay_config
        self.output_dir = output_dir
        self.compositor: Optional[Compositor] = None

    async def enter(self, project: ProjectState) -> None:
        """Initialize compositor."""
        self.logger.info("Entering POST_PROCESSING state")

        self.compositor = Compositor(
            ffmpeg_config=self.ffmpeg_config,
            fonts_config=self.fonts_config,
            overlay_config=self.overlay_config,
            output_dir=self.output_dir,
        )

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        """Execute video composition."""
        try:
            self.logger.info("Starting post-processing...")

            async def progress_cb(stage, current, total):
                self.logger.info(f"Composition progress: {stage} ({current}/{total})")

            final_path = await self.compositor.compose_final_video(
                project=project,
                progress_callback=progress_cb,
            )

            project.final_video_path = final_path
            self.logger.info(f"Post-processing complete: {final_path}")

            return True, "processing_complete"

        except Exception as e:
            self.logger.exception(f"Post-processing failed: {e}")
            return False, "processing_failed"

    async def exit(self, project: ProjectState) -> None:
        """Cleanup."""
        self.logger.info("Exiting POST_PROCESSING state")
        self.compositor = None
