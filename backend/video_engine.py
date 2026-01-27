"""
Video Engine - Composition & Rendering
=======================================
ffmpeg-python을 사용한 비디오 합성 엔진.

Features:
- 이미지/비디오 정규화 (1920x1080, 30fps)
- Crossfade(xfade) 트랜지션
- ASS 자막 버닝
- 실시간 진행률 콜백
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import ffmpeg

logger = logging.getLogger(__name__)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class ClipInfo:
    """클립 정보."""
    path: Path
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 30.0
    is_image: bool = False

    @classmethod
    def from_file(cls, path: Path) -> "ClipInfo":
        """파일에서 클립 정보 추출."""
        path = Path(path)
        is_image = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

        if is_image:
            # 이미지는 duration 정보 없음
            probe = ffmpeg.probe(str(path))
            stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
            return cls(
                path=path,
                duration=0,  # 나중에 설정
                width=int(stream["width"]),
                height=int(stream["height"]),
                is_image=True,
            )
        else:
            # 비디오
            probe = ffmpeg.probe(str(path))
            stream = next(s for s in probe["streams"] if s["codec_type"] == "video")

            # FPS 파싱
            fps_str = stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, den = map(int, fps_str.split("/"))
                fps = num / den if den else 30.0
            else:
                fps = float(fps_str)

            return cls(
                path=path,
                duration=float(probe["format"].get("duration", 0)),
                width=int(stream["width"]),
                height=int(stream["height"]),
                fps=fps,
                is_image=False,
            )


@dataclass
class SubtitleEntry:
    """자막 항목."""
    text: str
    start: float
    end: float
    style: str = "Default"

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class RenderConfig:
    """렌더링 설정."""
    # 출력 해상도
    width: int = 1920
    height: int = 1080
    fps: int = 30

    # 트랜지션
    transition_duration: float = 0.5
    transition_type: str = "fade"  # fade, dissolve, wipeleft, wiperight, slideup, slidedown

    # 인코딩
    video_codec: str = "libx264"
    video_bitrate: str = "8M"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    preset: str = "medium"  # ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
    crf: int = 18  # 품질 (0-51, 낮을수록 고품질)

    # 자막 스타일
    subtitle_font: str = "Arial"
    subtitle_fontsize: int = 48
    subtitle_color: str = "&HFFFFFF"  # ASS 색상 (BGR 순서)
    subtitle_outline: int = 2
    subtitle_shadow: int = 1
    subtitle_margin_v: int = 50


# =============================================================================
# ASS Subtitle Generator
# =============================================================================

class ASSGenerator:
    """ASS 자막 파일 생성기."""

    ASS_HEADER = """[Script Info]
Title: Mellow Video Engine Subtitles
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{color},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    @classmethod
    def generate(
        cls,
        subtitles: List[SubtitleEntry],
        output_path: Path,
        config: RenderConfig,
    ) -> Path:
        """
        ASS 자막 파일 생성.

        Args:
            subtitles: 자막 항목 리스트
            output_path: 출력 경로
            config: 렌더링 설정

        Returns:
            생성된 ASS 파일 경로
        """
        output_path = Path(output_path)

        # 헤더 생성
        header = cls.ASS_HEADER.format(
            width=config.width,
            height=config.height,
            font=config.subtitle_font,
            fontsize=config.subtitle_fontsize,
            color=config.subtitle_color,
            outline=config.subtitle_outline,
            shadow=config.subtitle_shadow,
            margin_v=config.subtitle_margin_v,
        )

        # 이벤트 생성
        events = []
        for sub in subtitles:
            start_ts = cls._format_ass_time(sub.start)
            end_ts = cls._format_ass_time(sub.end)

            # 특수 문자 이스케이프
            text = sub.text.replace("\\", "\\\\")
            text = text.replace("\n", "\\N")

            events.append(
                f"Dialogue: 0,{start_ts},{end_ts},{sub.style},,0,0,0,,{text}"
            )

        # 파일 작성
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header)
            f.write("\n".join(events))

        return output_path

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        """초를 ASS 타임스탬프 형식으로 변환 (H:MM:SS.cc)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        centisecs = int((secs % 1) * 100)
        return f"{hours}:{minutes:02d}:{int(secs):02d}.{centisecs:02d}"


# =============================================================================
# VideoComposer - Main Class
# =============================================================================

class VideoComposer:
    """
    ffmpeg-python 기반 비디오 합성 엔진.

    Features:
    - 이미지/비디오 정규화
    - Crossfade 트랜지션
    - 자막 버닝
    - 실시간 진행률 추적

    Usage:
    ```python
    composer = VideoComposer()
    composer.render(
        inputs=[Path("clip1.mp4"), Path("clip2.png")],
        audio=Path("song.mp3"),
        subtitles=[SubtitleEntry("Hello", 0, 2)],
        output=Path("output.mp4"),
        progress_callback=lambda p, s: print(f"{p:.1f}%: {s}"),
    )
    ```
    """

    def __init__(self, config: Optional[RenderConfig] = None) -> None:
        """
        초기화.

        Args:
            config: 렌더링 설정 (None이면 기본값 사용)
        """
        self.config = config or RenderConfig()
        self.logger = logging.getLogger(self.__class__.__name__)

        # 임시 파일 추적
        self._temp_files: List[Path] = []

    def render(
        self,
        inputs: List[Path],
        audio: Path,
        subtitles: List[Union[SubtitleEntry, Dict[str, Any]]],
        output: Path,
        clip_durations: Optional[List[float]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> bool:
        """
        비디오 렌더링.

        Args:
            inputs: 입력 클립 경로 리스트 (이미지 또는 비디오)
            audio: 오디오 파일 경로
            subtitles: 자막 리스트
            output: 출력 파일 경로
            clip_durations: 각 클립의 지속 시간 (이미지용)
            progress_callback: 진행률 콜백 (progress: 0-100, status: str)

        Returns:
            성공 여부
        """
        inputs = [Path(p) for p in inputs]
        audio = Path(audio)
        output = Path(output)

        self.logger.info(f"Rendering {len(inputs)} clips to {output}")

        if progress_callback:
            progress_callback(0, "Preparing...")

        try:
            # 1. 입력 검증
            if not inputs:
                raise ValueError("No input clips provided")

            if not audio.exists():
                raise FileNotFoundError(f"Audio file not found: {audio}")

            # 2. 오디오 정보 가져오기
            audio_info = self._get_media_duration(audio)
            total_duration = audio_info

            # 3. 클립 지속 시간 계산
            if clip_durations is None:
                # 균등 분배
                clip_count = len(inputs)
                transition_total = self.config.transition_duration * (clip_count - 1)
                available_duration = total_duration - transition_total
                clip_durations = [available_duration / clip_count] * clip_count

            # 4. 자막 정규화
            normalized_subtitles = self._normalize_subtitles(subtitles)

            # 5. 자막 파일 생성
            ass_file = None
            if normalized_subtitles:
                ass_file = self._create_temp_file(".ass")
                ASSGenerator.generate(normalized_subtitles, ass_file, self.config)
                self.logger.info(f"Generated subtitle file: {ass_file}")

            if progress_callback:
                progress_callback(5, "Normalizing clips...")

            # 6. 클립 정규화 (해상도, FPS 통일)
            normalized_clips = self._normalize_clips(inputs, clip_durations)

            if progress_callback:
                progress_callback(20, "Building filter graph...")

            # 7. 필터 그래프 구성
            filter_complex, output_labels = self._build_filter_graph(
                normalized_clips,
                ass_file,
            )

            if progress_callback:
                progress_callback(30, "Rendering...")

            # 8. FFmpeg 실행
            success = self._run_ffmpeg(
                normalized_clips,
                audio,
                filter_complex,
                output_labels,
                output,
                total_duration,
                progress_callback,
            )

            return success

        except Exception as e:
            self.logger.exception(f"Render failed: {e}")
            if progress_callback:
                progress_callback(0, f"Error: {e}")
            return False

        finally:
            # 임시 파일 정리
            self._cleanup_temp_files()

    def _normalize_subtitles(
        self,
        subtitles: List[Union[SubtitleEntry, Dict[str, Any]]],
    ) -> List[SubtitleEntry]:
        """자막 데이터 정규화."""
        result = []
        for sub in subtitles:
            if isinstance(sub, SubtitleEntry):
                result.append(sub)
            elif isinstance(sub, dict):
                result.append(SubtitleEntry(
                    text=sub.get("text", ""),
                    start=sub.get("start", 0),
                    end=sub.get("end", 0),
                    style=sub.get("style", "Default"),
                ))
        return result

    def _get_media_duration(self, path: Path) -> float:
        """미디어 파일 길이 조회."""
        try:
            probe = ffmpeg.probe(str(path))
            return float(probe["format"].get("duration", 0))
        except Exception as e:
            self.logger.warning(f"Failed to probe {path}: {e}")
            return 0

    def _normalize_clips(
        self,
        inputs: List[Path],
        durations: List[float],
    ) -> List[Tuple[Path, float]]:
        """
        클립 정규화.

        이미지는 지정된 duration으로 비디오 변환.
        모든 클립을 target 해상도/FPS로 통일.
        """
        normalized = []

        for i, (input_path, duration) in enumerate(zip(inputs, durations)):
            input_path = Path(input_path)
            is_image = input_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

            if is_image:
                # 이미지 -> 비디오 변환
                output_path = self._create_temp_file(".mp4")

                (
                    ffmpeg
                    .input(str(input_path), loop=1, t=duration)
                    .filter("scale", self.config.width, self.config.height, force_original_aspect_ratio="decrease")
                    .filter("pad", self.config.width, self.config.height, "(ow-iw)/2", "(oh-ih)/2")
                    .filter("fps", fps=self.config.fps)
                    .filter("format", "yuv420p")
                    .output(str(output_path), vcodec="libx264", preset="ultrafast", crf=18)
                    .overwrite_output()
                    .run(quiet=True)
                )

                normalized.append((output_path, duration))
                self.logger.debug(f"Converted image {input_path.name} to video ({duration:.2f}s)")

            else:
                # 비디오 정규화
                clip_info = ClipInfo.from_file(input_path)

                # 해상도/FPS가 다르면 변환
                if (clip_info.width != self.config.width or
                    clip_info.height != self.config.height or
                    abs(clip_info.fps - self.config.fps) > 1):

                    output_path = self._create_temp_file(".mp4")

                    (
                        ffmpeg
                        .input(str(input_path))
                        .filter("scale", self.config.width, self.config.height, force_original_aspect_ratio="decrease")
                        .filter("pad", self.config.width, self.config.height, "(ow-iw)/2", "(oh-ih)/2")
                        .filter("fps", fps=self.config.fps)
                        .filter("format", "yuv420p")
                        .output(str(output_path), vcodec="libx264", preset="ultrafast", crf=18)
                        .overwrite_output()
                        .run(quiet=True)
                    )

                    normalized.append((output_path, clip_info.duration))
                else:
                    normalized.append((input_path, clip_info.duration))

        return normalized

    def _build_filter_graph(
        self,
        clips: List[Tuple[Path, float]],
        ass_file: Optional[Path],
    ) -> Tuple[str, Tuple[str, str]]:
        """
        FFmpeg 필터 그래프 구성.

        xfade 필터를 사용한 크로스페이드 트랜지션 구현.

        xfade offset 계산:
        - offset = 출력 타임라인에서 트랜지션이 시작되는 시점
        - 첫 번째 트랜지션: offset = clip[0].duration - transition_duration
        - 두 번째 트랜지션: offset = clip[0] + clip[1] - 2*transition (겹침 고려)
        - N번째 트랜지션: sum(clip[0..N]) - N*transition_duration
        """
        if len(clips) == 1:
            # 단일 클립
            if ass_file:
                ass_path = self._escape_path_for_ffmpeg(ass_file)
                filter_parts = [f"[0:v]subtitles='{ass_path}'[outv]"]
            else:
                filter_parts = ["[0:v]null[outv]"]
            return ";".join(filter_parts), ("outv", "audio")

        # 다중 클립 - xfade 체인
        filter_parts = []
        current_output = "[0:v]"

        # 누적 오프셋 계산
        # xfade offset = 지금까지의 출력 타임라인 길이 - transition_duration
        # 첫 번째 xfade 후 출력 길이 = clip[0] + clip[1] - transition
        # 두 번째 xfade 후 출력 길이 = clip[0] + clip[1] + clip[2] - 2*transition
        cumulative_duration = clips[0][1]  # 첫 클립 길이

        for i in range(len(clips) - 1):
            # 현재 트랜지션 오프셋 = 누적 길이 - 트랜지션 시간
            current_offset = cumulative_duration - self.config.transition_duration

            # 음수 오프셋 방지
            if current_offset < 0:
                self.logger.warning(
                    f"Clip {i} duration ({clips[i][1]:.2f}s) is shorter than "
                    f"transition ({self.config.transition_duration:.2f}s). Adjusting."
                )
                current_offset = max(0, current_offset)

            next_input = f"[{i + 1}:v]"
            output_label = f"[v{i}]" if i < len(clips) - 2 else "[outv_raw]"

            # xfade 필터
            filter_parts.append(
                f"{current_output}{next_input}xfade=transition={self.config.transition_type}:"
                f"duration={self.config.transition_duration}:offset={current_offset:.3f}{output_label}"
            )

            current_output = output_label

            # 다음 클립을 위해 누적 길이 업데이트
            # xfade 후 출력 길이 = 현재까지 길이 + 다음 클립 길이 - 트랜지션 겹침
            cumulative_duration = current_offset + self.config.transition_duration + clips[i + 1][1] - self.config.transition_duration
            # 간단히: cumulative_duration += clips[i + 1][1] - self.config.transition_duration
            cumulative_duration = current_offset + clips[i + 1][1]

        # 자막 필터 추가
        if ass_file:
            ass_path = self._escape_path_for_ffmpeg(ass_file)
            filter_parts.append(f"[outv_raw]subtitles='{ass_path}'[outv]")
        else:
            # 자막 없으면 그대로 출력
            filter_parts[-1] = filter_parts[-1].replace("[outv_raw]", "[outv]")

        filter_complex = ";".join(filter_parts)

        return filter_complex, ("outv", "audio")

    def _run_ffmpeg(
        self,
        clips: List[Tuple[Path, float]],
        audio: Path,
        filter_complex: str,
        output_labels: Tuple[str, str],
        output: Path,
        total_duration: float,
        progress_callback: Optional[Callable[[float, str], None]],
    ) -> bool:
        """
        FFmpeg 실행 및 진행률 추적.

        subprocess.Popen을 사용하여 stderr를 실시간 파싱.
        """
        # 입력 파일 목록
        input_args = []
        for clip_path, _ in clips:
            input_args.extend(["-i", str(clip_path)])

        # 오디오 입력 (마지막 인덱스)
        audio_input_idx = len(clips)
        input_args.extend(["-i", str(audio)])

        # FFmpeg 명령 구성
        cmd = [
            "ffmpeg",
            "-y",  # 덮어쓰기
            "-progress", "pipe:1",  # 진행률 stdout으로
            "-nostats",
            *input_args,
            "-filter_complex", filter_complex,
            "-map", f"[{output_labels[0]}]",  # 비디오 스트림
            "-map", f"{audio_input_idx}:a:0",  # 오디오 스트림 (첫 번째 오디오 트랙)
            "-c:v", self.config.video_codec,
            "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-b:v", self.config.video_bitrate,
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-shortest",
            str(output),
        ]

        self.logger.info(f"FFmpeg filter_complex: {filter_complex[:200]}...")
        self.logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        # 프로세스 실행
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        except FileNotFoundError:
            self.logger.error("FFmpeg not found. Please install FFmpeg and add it to PATH.")
            return False

        # 진행률 파싱
        current_time = 0.0

        def parse_progress():
            nonlocal current_time

            for line in process.stdout:
                line = line.strip()

                # out_time_us 파싱
                if line.startswith("out_time_us="):
                    try:
                        time_us = int(line.split("=")[1])
                        current_time = time_us / 1_000_000
                        if progress_callback and total_duration > 0:
                            progress = min(30 + (current_time / total_duration) * 65, 95)
                            progress_callback(progress, f"Encoding: {current_time:.1f}s / {total_duration:.1f}s")
                    except (ValueError, IndexError):
                        pass

                elif line == "progress=end":
                    if progress_callback:
                        progress_callback(98, "Finalizing...")

        # 백그라운드 스레드에서 진행률 파싱
        progress_thread = threading.Thread(target=parse_progress, daemon=True)
        progress_thread.start()

        # 프로세스 완료 대기
        _, stderr = process.communicate()

        progress_thread.join(timeout=1.0)

        if process.returncode != 0:
            self.logger.error(f"FFmpeg failed: {stderr}")
            return False

        if progress_callback:
            progress_callback(100, "Complete!")

        self.logger.info(f"Render complete: {output}")
        return True

    def _create_temp_file(self, suffix: str) -> Path:
        """임시 파일 생성."""
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="mellow_")
        os.close(fd)
        path = Path(path)
        self._temp_files.append(path)
        return path

    def _cleanup_temp_files(self) -> None:
        """임시 파일 정리."""
        for path in self._temp_files:
            try:
                if path.exists():
                    path.unlink()
            except Exception as e:
                self.logger.warning(f"Failed to delete temp file {path}: {e}")

        self._temp_files.clear()

    @staticmethod
    def _escape_path_for_ffmpeg(path: Path) -> str:
        """
        FFmpeg subtitles 필터용 경로 이스케이프.

        Windows 경로의 경우:
        - 백슬래시 -> 슬래시
        - 콜론 -> \\:  (드라이브 문자)
        - 특수문자 이스케이프
        """
        path_str = str(path)

        # Windows 경로 처리
        if os.name == 'nt':
            # 백슬래시 -> 슬래시
            path_str = path_str.replace("\\", "/")
            # 드라이브 문자의 콜론 이스케이프 (예: C: -> C\\:)
            if len(path_str) >= 2 and path_str[1] == ':':
                path_str = path_str[0] + "\\\\:" + path_str[2:]

        # 특수 문자 이스케이프 (작은따옴표, 대괄호 등)
        path_str = path_str.replace("'", "\\'")
        path_str = path_str.replace("[", "\\[")
        path_str = path_str.replace("]", "\\]")

        return path_str

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def create_thumbnail(
        self,
        video: Path,
        output: Path,
        time: float = 0,
        size: Tuple[int, int] = (1280, 720),
    ) -> bool:
        """비디오에서 썸네일 추출."""
        try:
            (
                ffmpeg
                .input(str(video), ss=time)
                .filter("scale", size[0], size[1])
                .output(str(output), vframes=1)
                .overwrite_output()
                .run(quiet=True)
            )
            return True
        except Exception as e:
            self.logger.error(f"Thumbnail creation failed: {e}")
            return False

    def extract_audio(
        self,
        video: Path,
        output: Path,
        format: str = "mp3",
    ) -> bool:
        """비디오에서 오디오 추출."""
        try:
            (
                ffmpeg
                .input(str(video))
                .output(str(output), acodec="libmp3lame" if format == "mp3" else "copy")
                .overwrite_output()
                .run(quiet=True)
            )
            return True
        except Exception as e:
            self.logger.error(f"Audio extraction failed: {e}")
            return False


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "VideoComposer",
    "ClipInfo",
    "SubtitleEntry",
    "RenderConfig",
    "ASSGenerator",
]
