"""
Audio Processor Module
======================
State 1: Audio Analysis (Lyric Sync)
Handles audio transcription using faster-whisper and timestamp extraction.

Features:
- Vocal isolation using Demucs (removes background music for better ASR)
- Automatic speech recognition using faster-whisper
- Word-level timestamp extraction
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from core.fsm_manager import StateHandler
from core.model_manager import ModelType, ModelContext
from core.project_state import LyricSegment, AssetStatus

if TYPE_CHECKING:
    from core.project_state import ProjectState
    from core.model_manager import ModelManager
    from core.fsm_manager import FSMManager

logger = logging.getLogger(__name__)


class AudioProcessor:
    """
    Processes audio files for lyric extraction and timing synchronization.

    Features:
    - Vocal isolation using Demucs (Facebook Research)
    - Automatic speech recognition using faster-whisper
    - Word-level timestamp extraction
    - Segment grouping based on natural pauses
    - Confidence scoring for quality assessment
    """

    def __init__(
        self,
        model_manager: ModelManager,
        config: dict,
    ):
        """
        Initialize AudioProcessor.

        Args:
            model_manager: ModelManager instance for VRAM management
            config: Whisper configuration from settings.yaml
        """
        self.model_manager = model_manager
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._temp_vocals_path: Optional[Path] = None

    async def isolate_vocals(
        self,
        audio_path: Path,
        output_dir: Optional[Path] = None,
        model_name: str = "htdemucs",
    ) -> Optional[Path]:
        """
        Demucs를 사용하여 오디오에서 보컬을 분리합니다.

        Args:
            audio_path: 입력 오디오 파일 경로 (mp3, wav 등)
            output_dir: 출력 디렉토리 (None이면 임시 디렉토리 사용)
            model_name: Demucs 모델 이름 (기본: htdemucs - GPU 최적화)

        Returns:
            분리된 보컬 파일 경로 (vocals.wav) 또는 실패 시 None
        """
        self.logger.info(f"Starting vocal isolation with Demucs ({model_name})...")

        try:
            # 임시 출력 디렉토리 생성
            if output_dir is None:
                output_dir = Path(tempfile.mkdtemp(prefix="demucs_"))

            # Demucs 명령어 구성
            # --two-stems=vocals: 보컬만 분리 (더 빠름)
            # -d cuda: GPU 사용 (RTX 5070 Ti)
            # -n htdemucs: 고품질 모델
            cmd = [
                "python", "-m", "demucs",
                "--two-stems=vocals",  # 보컬만 분리 (속도 향상)
                "-d", "cuda",          # GPU 사용
                "-n", model_name,      # 모델 선택
                "-o", str(output_dir), # 출력 디렉토리
                str(audio_path),       # 입력 파일
            ]

            self.logger.info(f"Running Demucs: {' '.join(cmd)}")

            # 비동기로 Demucs 실행
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                self.logger.error(f"Demucs failed with return code {process.returncode}")
                self.logger.error(f"stderr: {stderr.decode('utf-8', errors='ignore')}")
                return None

            # 출력 파일 경로 찾기
            # Demucs 출력 구조: output_dir/model_name/track_name/vocals.wav
            track_name = audio_path.stem
            vocals_path = output_dir / model_name / track_name / "vocals.wav"

            if not vocals_path.exists():
                # 다른 가능한 경로 시도
                possible_paths = list(output_dir.rglob("vocals.wav"))
                if possible_paths:
                    vocals_path = possible_paths[0]
                else:
                    self.logger.error(f"Vocals file not found in {output_dir}")
                    return None

            self.logger.info(f"Vocal isolation complete: {vocals_path}")
            self._temp_vocals_path = vocals_path
            return vocals_path

        except FileNotFoundError:
            self.logger.warning("Demucs not found. Please install with: pip install demucs")
            return None
        except Exception as e:
            self.logger.exception(f"Vocal isolation failed: {e}")
            return None

    def cleanup_temp_vocals(self) -> None:
        """임시 보컬 파일 및 디렉토리 정리"""
        if self._temp_vocals_path and self._temp_vocals_path.exists():
            try:
                # 상위 임시 디렉토리 찾기 (demucs_ prefix로 시작하는 디렉토리)
                temp_dir = self._temp_vocals_path.parent
                while temp_dir.name and not temp_dir.name.startswith("demucs_"):
                    temp_dir = temp_dir.parent

                if temp_dir.name.startswith("demucs_"):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    self.logger.info(f"Cleaned up temp directory: {temp_dir}")
                else:
                    # 개별 파일만 삭제
                    self._temp_vocals_path.unlink(missing_ok=True)
                    self.logger.info(f"Cleaned up temp vocals: {self._temp_vocals_path}")

            except Exception as e:
                self.logger.warning(f"Failed to cleanup temp vocals: {e}")
            finally:
                self._temp_vocals_path = None

    async def analyze_audio(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        min_segment_duration: float = 2.0,
        max_segment_duration: float = 10.0,
        use_vocal_isolation: bool = True,
    ) -> list[LyricSegment]:
        """
        Analyze audio file and extract lyrics with timestamps.

        Args:
            audio_path: Path to audio file (mp3, wav, etc.)
            language: Language code (e.g., 'ko', 'en'). None for auto-detect.
            min_segment_duration: Minimum segment duration in seconds
            max_segment_duration: Maximum segment duration in seconds
            use_vocal_isolation: Demucs 보컬 분리 사용 여부 (기본: True)

        Returns:
            List of LyricSegment objects with timing information
        """
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self.logger.info(f"Analyzing audio: {audio_path}")

        # =========================================================================
        # Step 1: Vocal Isolation with Demucs (NEW)
        # =========================================================================
        whisper_input_path = audio_path  # 기본값: 원본 오디오

        if use_vocal_isolation:
            self.logger.info("Attempting vocal isolation with Demucs...")
            vocals_path = await self.isolate_vocals(audio_path)

            if vocals_path and vocals_path.exists():
                whisper_input_path = vocals_path
                self.logger.info(f"Using isolated vocals for Whisper: {vocals_path}")
            else:
                self.logger.warning("Vocal isolation failed, using original audio as fallback")

        # =========================================================================
        # Step 2: Whisper Transcription
        # =========================================================================
        segments: list[LyricSegment] = []

        try:
            # Load Whisper model with VRAM management
            async with ModelContext(
                self.model_manager,
                ModelType.WHISPER,
                self.config,
                auto_unload=True,
            ) as model:
                # Transcribe audio with enhanced precision settings
                self.logger.info("Starting transcription with enhanced precision settings...")

                # Determine language for initial prompt
                if language is None:
                    # Will be auto-detected, but prepare Korean prompt as default
                    initial_prompt_text = "한국어 노래 가사"  # 딱 핵심 키워드만!
                elif language == "ko":
                    initial_prompt_text = "한국어 노래 가사"  # 딱 핵심 키워드만!
                else:
                    # For other languages, use generic prompt
                    initial_prompt_text = f"This is a {language} song. Track the lyrics to the end even if the background music is loud."

                transcribe_segments, info = model.transcribe(
                    str(whisper_input_path),  # 보컬 분리된 파일 또는 원본
                    language=language,
                    word_timestamps=True,
                    # Decoding Options 강화: 더 정확한 가사 인식
                    beam_size=3,  # 여러 가능성을 동시에 검토
                    patience=2.0,  # 더 끈기 있게 가사 찾기
                    initial_prompt=initial_prompt_text,  # 한국어 가사 힌트 제공
                    # VAD Filter 완화: 반주 구간도 듣도록 관대하게 설정
                    vad_filter=True,  # VAD 필터 활성화하되 관대한 설정
                    vad_parameters=dict(
                        min_silence_duration_ms=1000,  # 1초 이상 침묵이어야만 구간 분리 (관대하게)
                    ),
                    # Segment 처리 개선: 가사 연결성 강화
                    condition_on_previous_text=True,  # 앞뒤 문맥 파악하여 가사 연결
                    no_speech_threshold=0.6,  # 목소리가 작아도 무음으로 판단하지 않도록 기준 완화
                    # 추가 정밀도 옵션
                    compression_ratio_threshold=2.4,  # 반복 감지 임계값
                    temperature=0.0,  # 결정적 디코딩 (일관성 향상)
                )

                self.logger.info(
                    f"Detected language: {info.language} "
                    f"(probability: {info.language_probability:.2f})"
                )

                # Process segments
                current_segment_text = []
                current_start = None
                current_end = None
                current_confidence = []

                for segment in transcribe_segments:
                    # Get word-level timing if available
                    if hasattr(segment, "words") and segment.words:
                        for word in segment.words:
                            if current_start is None:
                                current_start = word.start
                                current_end = word.end
                                current_segment_text.append(word.word.strip())
                                current_confidence.append(word.probability)
                            else:
                                # Check if we should start a new segment
                                gap = word.start - current_end
                                duration = current_end - current_start

                                # Natural break conditions
                                should_break = (
                                    gap > 0.5 or  # Pause > 0.5s
                                    duration >= max_segment_duration or
                                    (duration >= min_segment_duration and gap > 0.3)
                                )

                                if should_break and current_segment_text:
                                    # Save current segment
                                    segments.append(LyricSegment(
                                        text=" ".join(current_segment_text).strip(),
                                        start_time=current_start,
                                        end_time=current_end,
                                        confidence=sum(current_confidence) / len(current_confidence),
                                    ))

                                    # Start new segment
                                    current_segment_text = [word.word.strip()]
                                    current_start = word.start
                                    current_end = word.end
                                    current_confidence = [word.probability]
                                else:
                                    current_segment_text.append(word.word.strip())
                                    current_end = word.end
                                    current_confidence.append(word.probability)
                    else:
                        # Fallback: use segment-level timing
                        segments.append(LyricSegment(
                            text=segment.text.strip(),
                            start_time=segment.start,
                            end_time=segment.end,
                            confidence=getattr(segment, "avg_logprob", 0.9),
                        ))

                # Don't forget the last segment
                if current_segment_text:
                    segments.append(LyricSegment(
                        text=" ".join(current_segment_text).strip(),
                        start_time=current_start,
                        end_time=current_end,
                        confidence=sum(current_confidence) / len(current_confidence),
                    ))

        finally:
            # =========================================================================
            # Step 3: Cleanup temp vocals file
            # =========================================================================
            if use_vocal_isolation:
                self.cleanup_temp_vocals()

        self.logger.info(f"Extracted {len(segments)} lyric segments")
        return segments

    async def refine_segments_with_llm(
        self,
        segments: list[LyricSegment],
        llm_client: any,
        language: str = "ko",
    ) -> list[LyricSegment]:
        """
        Optional: Use LLM to refine transcription quality.

        Args:
            segments: Raw segments from Whisper
            llm_client: LLM client for refinement
            language: Target language

        Returns:
            Refined lyric segments
        """
        # Placeholder for LLM refinement logic
        # This can correct common ASR errors, fix punctuation, etc.
        self.logger.info("LLM refinement not yet implemented, returning original segments")
        return segments

    def merge_short_segments(
        self,
        segments: list[LyricSegment],
        min_duration: float = 2.0,
    ) -> list[LyricSegment]:
        """
        Merge very short segments for better visual grouping.

        Args:
            segments: List of lyric segments
            min_duration: Minimum desired segment duration

        Returns:
            Merged segments
        """
        if not segments:
            return segments

        merged = []
        buffer = None

        for segment in segments:
            if buffer is None:
                buffer = LyricSegment(
                    text=segment.text,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    confidence=segment.confidence,
                )
            elif buffer.duration < min_duration:
                # Merge with buffer
                buffer.text = f"{buffer.text} {segment.text}".strip()
                buffer.end_time = segment.end_time
                buffer.confidence = (buffer.confidence + segment.confidence) / 2
            else:
                merged.append(buffer)
                buffer = LyricSegment(
                    text=segment.text,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    confidence=segment.confidence,
                )

        if buffer:
            merged.append(buffer)

        return merged

    def get_audio_duration(self, audio_path: Path) -> float:
        """
        Get audio file duration in seconds.

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds
        """
        try:
            import json

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data["format"]["duration"])
        except Exception as e:
            self.logger.warning(f"Could not get audio duration via ffprobe: {e}")

        # Fallback: estimate from file size (rough)
        return 0.0

    def preprocess_lyrics(self, full_lyrics: str, min_segment_length: int = 3) -> list[str]:
        """
        전처리기: 사용자가 제공한 가사를 세그먼트로 분리.

        Args:
            full_lyrics: 사용자가 입력한 전체 가사 텍스트
            min_segment_length: 최소 세그먼트 길이 (이보다 짧으면 이전/다음과 합침)

        Returns:
            분리된 가사 세그먼트 리스트
        """
        if not full_lyrics or not full_lyrics.strip():
            return []

        # 프롬프트 오염 텍스트 필터링
        contamination_patterns = [
            "배경음악이 커도",
            "가사를 끝까지 추적",
            "음악 소리가 커도",
            "Track the lyrics",
            "This is a",
            "song. Track",
        ]

        cleaned_lyrics = full_lyrics
        for pattern in contamination_patterns:
            cleaned_lyrics = cleaned_lyrics.replace(pattern, "")

        # 줄바꿈, 쉼표, 마침표를 기준으로 분리
        import re

        # 먼저 줄바꿈으로 분리
        lines = cleaned_lyrics.split('\n')

        segments = []
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 쉼표와 마침표로 추가 분리
            parts = re.split(r'[,，.。]', line)

            for part in parts:
                part = part.strip()
                if part:
                    segments.append(part)

        # 너무 짧은 세그먼트 병합
        merged_segments = []
        buffer = ""

        for segment in segments:
            if len(segment) < min_segment_length:
                # 짧은 세그먼트는 버퍼에 추가
                if buffer:
                    buffer += " " + segment
                else:
                    buffer = segment
            else:
                # 긴 세그먼트는 버퍼와 합쳐서 저장
                if buffer:
                    merged_segments.append((buffer + " " + segment).strip())
                    buffer = ""
                else:
                    merged_segments.append(segment)

        # 마지막 버퍼 처리
        if buffer:
            if merged_segments:
                # 마지막 세그먼트와 합치기
                merged_segments[-1] = (merged_segments[-1] + " " + buffer).strip()
            else:
                merged_segments.append(buffer)

        self.logger.info(f"Preprocessed {len(segments)} raw segments into {len(merged_segments)} final segments")
        return merged_segments

    def align_lyrics_to_timeline(
        self,
        lyrics_segments: list[str],
        whisper_segments: list,
        total_speech_duration: float,
    ) -> list[LyricSegment]:
        """
        타임라인 강제 매칭: Whisper가 찾은 타임라인을 사용자 가사에 배분.

        Args:
            lyrics_segments: 전처리된 가사 세그먼트 리스트
            whisper_segments: Whisper가 찾은 세그먼트 (타임라인 정보만 사용)
            total_speech_duration: Whisper가 찾은 전체 음성 구간 길이

        Returns:
            타임라인이 배분된 LyricSegment 리스트
        """
        if not lyrics_segments:
            self.logger.warning("No lyrics segments provided for alignment")
            return []

        # Whisper 세그먼트에서 실제 음성 구간 추출
        speech_segments = []
        for seg in whisper_segments:
            if hasattr(seg, "start") and hasattr(seg, "end"):
                # 프롬프트 오염 텍스트 필터링
                text = getattr(seg, "text", "").strip()
                if any(pattern in text for pattern in ["배경음악", "가사를 끝까지", "Track the lyrics"]):
                    continue

                speech_segments.append((seg.start, seg.end))

        # 전체 음성 구간 계산
        if speech_segments:
            actual_start = min(start for start, _ in speech_segments)
            actual_end = max(end for _, end in speech_segments)
            actual_duration = actual_end - actual_start
        else:
            # Fallback: total_speech_duration 사용
            actual_start = 0.0
            actual_duration = total_speech_duration
            actual_end = actual_duration

        self.logger.info(
            f"Aligning {len(lyrics_segments)} lyrics segments to "
            f"{actual_duration:.2f}s speech duration"
        )

        # 가사 세그먼트 개수에 맞춰 타임라인 배분
        aligned_segments = []
        segment_count = len(lyrics_segments)

        if segment_count == 0:
            return []

        # 각 세그먼트당 할당할 시간 계산
        time_per_segment = actual_duration / segment_count

        for i, lyric_text in enumerate(lyrics_segments):
            start_time = actual_start + (i * time_per_segment)
            end_time = actual_start + ((i + 1) * time_per_segment)

            aligned_segments.append(LyricSegment(
                text=lyric_text.strip(),
                start_time=start_time,
                end_time=end_time,
                confidence=0.95,  # 사용자 제공 가사이므로 높은 신뢰도
            ))

        self.logger.info(f"Aligned {len(aligned_segments)} segments with forced timeline")
        return aligned_segments

    async def analyze_audio_with_user_lyrics(
        self,
        audio_path: Path,
        user_lyrics: str,
        language: Optional[str] = None,
        use_vocal_isolation: bool = True,
    ) -> list[LyricSegment]:
        """
        사용자 제공 가사를 기반으로 타임라인을 재설정.

        Args:
            audio_path: 오디오 파일 경로
            user_lyrics: 사용자가 제공한 전체 가사 텍스트
            language: 언어 코드
            use_vocal_isolation: Demucs 보컬 분리 사용 여부 (기본: True)

        Returns:
            타임라인이 재설정된 LyricSegment 리스트
        """
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self.logger.info(f"Analyzing audio with user-provided lyrics: {audio_path}")

        # 1. 사용자 가사 전처리
        lyrics_segments = self.preprocess_lyrics(user_lyrics)

        if not lyrics_segments:
            self.logger.warning("No valid lyrics segments after preprocessing")
            return []

        # =========================================================================
        # Step 2: Vocal Isolation with Demucs (NEW)
        # =========================================================================
        whisper_input_path = audio_path  # 기본값: 원본 오디오

        if use_vocal_isolation:
            self.logger.info("Attempting vocal isolation with Demucs...")
            vocals_path = await self.isolate_vocals(audio_path)

            if vocals_path and vocals_path.exists():
                whisper_input_path = vocals_path
                self.logger.info(f"Using isolated vocals for Whisper: {vocals_path}")
            else:
                self.logger.warning("Vocal isolation failed, using original audio as fallback")

        # =========================================================================
        # Step 3: Whisper로 타임라인만 추출 (텍스트는 무시)
        # =========================================================================
        whisper_segments = []
        total_speech_duration = 0.0

        try:
            async with ModelContext(
                self.model_manager,
                ModelType.WHISPER,
                self.config,
                auto_unload=True,
            ) as model:
                self.logger.info("Extracting timeline from Whisper (ignoring text)...")

                # Determine language for initial prompt
                if language is None:
                    initial_prompt_text = "한국어 노래 가사"
                elif language == "ko":
                    initial_prompt_text = "한국어 노래 가사"
                else:
                    initial_prompt_text = f"This is a {language} song."

                transcribe_segments, info = model.transcribe(
                    str(whisper_input_path),  # 보컬 분리된 파일 또는 원본
                    language=language,
                    word_timestamps=True,
                    beam_size=3,
                    patience=2.0,
                    initial_prompt=initial_prompt_text,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=1000),
                    condition_on_previous_text=True,
                    no_speech_threshold=0.6,
                    compression_ratio_threshold=2.4,
                    temperature=0.0,
                )

                # Whisper 세그먼트에서 타임라인만 추출
                for segment in transcribe_segments:
                    # 프롬프트 오염 텍스트 필터링
                    text = getattr(segment, "text", "").strip()
                    if any(pattern in text for pattern in ["배경음악", "가사를 끝까지", "Track the lyrics", "This is a"]):
                        continue

                    if hasattr(segment, "start") and hasattr(segment, "end"):
                        whisper_segments.append(segment)
                        total_speech_duration = max(total_speech_duration, segment.end)

        finally:
            # =========================================================================
            # Step 4: Cleanup temp vocals file
            # =========================================================================
            if use_vocal_isolation:
                self.cleanup_temp_vocals()

        # =========================================================================
        # Step 5: 타임라인 강제 매칭
        # =========================================================================
        aligned_segments = self.align_lyrics_to_timeline(
            lyrics_segments,
            whisper_segments,
            total_speech_duration,
        )

        self.logger.info(f"Created {len(aligned_segments)} aligned segments from user lyrics")
        return aligned_segments


class AudioAnalysisHandler(StateHandler):
    """
    FSM Handler for AUDIO_ANALYSIS state.
    Integrates AudioProcessor with the FSM workflow.
    """

    def __init__(
        self,
        fsm: FSMManager,
        model_manager: ModelManager,
        config: dict,
    ):
        super().__init__(fsm)
        self.model_manager = model_manager
        self.config = config
        self.processor: Optional[AudioProcessor] = None

    async def enter(self, project: ProjectState) -> None:
        """Initialize audio processor on state entry."""
        self.logger.info("Entering AUDIO_ANALYSIS state")
        self.processor = AudioProcessor(self.model_manager, self.config)

    async def execute(self, project: ProjectState, user_lyrics: Optional[str] = None) -> tuple[bool, str]:
        """
        Execute audio analysis.

        Args:
            project: 프로젝트 상태
            user_lyrics: 사용자가 제공한 전체 가사 텍스트 (선택적)

        Returns:
            (success, next_trigger) tuple
        """
        if not project.audio_file_path:
            self.logger.error("No audio file specified in project")
            return False, "analysis_failed"

        try:
            # Get audio duration
            duration = self.processor.get_audio_duration(project.audio_file_path)
            project.audio_duration = duration
            self.logger.info(f"Audio duration: {duration:.2f} seconds")

            # 사용자 가사가 제공된 경우: 강제 정렬 사용
            if user_lyrics and user_lyrics.strip():
                self.logger.info("Using user-provided lyrics with forced alignment")
                segments = await self.processor.analyze_audio_with_user_lyrics(
                    project.audio_file_path,
                    user_lyrics,
                    language=project.metadata.translations.get("source_language") or "ko",
                    use_vocal_isolation=True,  # Demucs 사용
                )
            else:
                # 기존 방식: Whisper 자동 전사
                self.logger.info("Using Whisper automatic transcription")
                segments = await self.processor.analyze_audio(
                    project.audio_file_path,
                    language=project.metadata.translations.get("source_language"),
                    use_vocal_isolation=True,  # Demucs 사용
                )
                # Merge short segments
                segments = self.processor.merge_short_segments(segments)

            # Add segments to project
            for segment in segments:
                project.add_lyric_segment(segment)

            self.logger.info(f"Analysis complete: {len(segments)} segments extracted")

            return True, "analysis_complete"

        except Exception as e:
            self.logger.exception(f"Audio analysis failed: {e}")
            return False, "analysis_failed"

    async def exit(self, project: ProjectState) -> None:
        """Cleanup on state exit."""
        self.logger.info("Exiting AUDIO_ANALYSIS state")
        self.processor = None

        # Ensure Whisper model is unloaded
        if self.model_manager.is_model_loaded(ModelType.WHISPER):
            await self.model_manager.unload_model(ModelType.WHISPER)
