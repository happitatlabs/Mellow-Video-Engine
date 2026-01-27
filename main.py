#!/usr/bin/env python3
"""
Mellow-Video-Engine
===================
Automated Music Video Generation Pipeline

A FSM-based system for generating music videos from audio files.
Features human-in-the-loop checkpoints for quality control.

Modes:
    TUI (Interactive):
        python main.py tui
        python main.py tui song.mp3
        python main.py tui --workflows-dir ./my_workflows

    Headless (CLI):
        python main.py headless song.mp3 --output output.mp4
        python main.py headless song.mp3 --transcribe-only --output lyrics.json

    FSM Pipeline (Original):
        python main.py pipeline --audio path/to/song.mp3 --mood "calm, melancholic"
        python main.py pipeline --project path/to/project.json --resume

Author: Mellow-Video-Engine Team
License: MIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.project_state import ProjectState, ProjectMetadata
from core.fsm_manager import (
    FSMManager,
    State,
    InitStateHandler,
    AudioReviewHandler,
    VisualScriptingReviewHandler,  # New: review LLM prompts before image generation
    VisualReviewHandler,
    MotionReviewHandler,
    PostReviewHandler,
    ErrorStateHandler,
    CompletedStateHandler,
)
from core.model_manager import ModelManager

from modules.audio_processor import AudioAnalysisHandler
from modules.visual_planner import (
    VisualScriptingHandler,   # LLM prompt generation
    VisualRenderingHandler,   # ComfyUI image generation
    ComfyUIClient,
)
from modules.video_synthesizer import MotionSynthesisHandler
from modules.compositor import PostProcessingHandler
from modules.publisher import LocalizationHandler


# ============================================================================
# Configuration Loading
# ============================================================================

def load_config(config_path: Path) -> dict:
    """Load YAML configuration file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict) -> None:
    """Configure logging based on settings."""
    log_config = config.get("logging", {})

    log_level = getattr(logging, log_config.get("level", "INFO"))
    log_format = log_config.get(
        "format",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Setup root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Add file handler if specified
    log_file = log_config.get("file")
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(log_format))
        logging.getLogger().addHandler(file_handler)


# ============================================================================
# Orchestrator
# ============================================================================

class MellowVideoEngine:
    """
    Main orchestrator for the Mellow-Video-Engine pipeline.

    Coordinates FSM, models, and user interaction.
    """

    def __init__(
        self,
        settings_path: Path = None,
        prompts_path: Path = None,
    ):
        """
        Initialize the engine.

        Args:
            settings_path: Path to settings.yaml
            prompts_path: Path to prompts.yaml
        """
        self.logger = logging.getLogger(self.__class__.__name__)

        # Load configurations
        config_dir = PROJECT_ROOT / "config"
        self.settings = load_config(settings_path or config_dir / "settings.yaml")
        self.prompts = load_config(prompts_path or config_dir / "prompts.yaml")

        # Setup logging
        setup_logging(self.settings)

        # Initialize components
        self.model_manager = ModelManager(
            max_vram_gb=self.settings.get("vram", {}).get("max_usage_gb", 16),
            aggressive_gc=self.settings.get("vram", {}).get("gc_aggressive", True),
        )

        self.fsm: Optional[FSMManager] = None
        self.project: Optional[ProjectState] = None
        self.comfyui_client: Optional[ComfyUIClient] = None

        self.logger.info("Mellow-Video-Engine initialized")

    async def initialize_comfyui(self) -> None:
        """Initialize ComfyUI client connection."""
        comfyui_config = self.settings.get("comfyui", {})

        self.comfyui_client = ComfyUIClient(
            host=comfyui_config.get("host", "127.0.0.1"),
            port=comfyui_config.get("port", 8188),
            use_ssl=comfyui_config.get("use_ssl", False),
            timeout=comfyui_config.get("timeout", 300),
        )

        await self.comfyui_client.connect()
        self.logger.info("ComfyUI client connected")

    async def cleanup_comfyui(self) -> None:
        """Cleanup ComfyUI client."""
        if self.comfyui_client:
            await self.comfyui_client.disconnect()
            self.comfyui_client = None

    def _create_fsm(self) -> FSMManager:
        """Create and configure FSM with all handlers."""
        fsm = FSMManager(
            model_manager=self.model_manager,
            auto_save=True,
            save_callback=self._auto_save_project,
        )

        # Directory setup
        assets_dir = PROJECT_ROOT / self.settings.get("project", {}).get("assets_dir", "assets")
        images_dir = assets_dir / "generated_images"
        videos_dir = assets_dir / "generated_videos"
        output_dir = assets_dir / "final_output"

        for d in [images_dir, videos_dir, output_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Register state handlers

        # INIT
        fsm.register_handler(State.INIT, InitStateHandler(fsm))

        # AUDIO_ANALYSIS
        fsm.register_handler(
            State.AUDIO_ANALYSIS,
            AudioAnalysisHandler(
                fsm=fsm,
                model_manager=self.model_manager,
                config=self.settings.get("models", {}).get("whisper", {}),
            ),
        )

        # AUDIO_REVIEW (checkpoint)
        fsm.register_handler(State.AUDIO_REVIEW, AudioReviewHandler(fsm))

        # VISUAL_SCRIPTING: LLM generates scene prompts (JSON)
        fsm.register_handler(
            State.VISUAL_SCRIPTING,
            VisualScriptingHandler(
                fsm=fsm,
                model_manager=self.model_manager,
                prompts_config=self.prompts.get("visual_planning", {}),
                llm_config=self.settings.get("models", {}).get("llm", {}),
                output_dir=images_dir,
            ),
        )

        # VISUAL_SCRIPTING_REVIEW (checkpoint): User reviews/edits prompts BEFORE image generation
        fsm.register_handler(State.VISUAL_SCRIPTING_REVIEW, VisualScriptingReviewHandler(fsm))

        # VISUAL_RENDERING: ComfyUI generates images from confirmed prompts
        fsm.register_handler(
            State.VISUAL_RENDERING,
            VisualRenderingHandler(
                fsm=fsm,
                model_manager=self.model_manager,
                comfyui_config=self.settings.get("comfyui", {}),
                output_dir=images_dir,
            ),
        )

        # VISUAL_REVIEW (checkpoint): User reviews generated images
        fsm.register_handler(State.VISUAL_REVIEW, VisualReviewHandler(fsm))

        # MOTION_SYNTHESIS
        fsm.register_handler(
            State.MOTION_SYNTHESIS,
            MotionSynthesisHandler(
                fsm=fsm,
                comfyui_client=self.comfyui_client,
                config=self.settings.get("comfyui", {}).get("video_generation", {}),
                output_dir=videos_dir,
            ),
        )

        # MOTION_REVIEW (checkpoint)
        fsm.register_handler(State.MOTION_REVIEW, MotionReviewHandler(fsm))

        # POST_PROCESSING
        fsm.register_handler(
            State.POST_PROCESSING,
            PostProcessingHandler(
                fsm=fsm,
                ffmpeg_config=self.settings.get("ffmpeg", {}),
                fonts_config=self.settings.get("fonts", {}),
                overlay_config=self.prompts.get("overlay_text", {}),
                output_dir=output_dir,
            ),
        )

        # POST_REVIEW (checkpoint)
        fsm.register_handler(State.POST_REVIEW, PostReviewHandler(fsm))

        # LOCALIZATION
        fsm.register_handler(
            State.LOCALIZATION,
            LocalizationHandler(
                fsm=fsm,
                model_manager=self.model_manager,
                llm_config=self.settings.get("models", {}).get("llm", {}),
                prompts_config=self.prompts.get("translation", {}),
                youtube_config=self.settings.get("youtube", {}),
                localization_config=self.settings.get("localization", {}),
            ),
        )

        # ERROR
        fsm.register_handler(State.ERROR, ErrorStateHandler(fsm))

        # COMPLETED
        fsm.register_handler(State.COMPLETED, CompletedStateHandler(fsm))

        return fsm

    def _auto_save_project(self, project: ProjectState) -> None:
        """Auto-save project state."""
        if project:
            save_path = PROJECT_ROOT / "assets" / f"{project.project_id}.json"
            project.save_to_file(save_path)
            self.logger.debug(f"Project auto-saved: {save_path}")

    def create_new_project(
        self,
        audio_path: Path,
        project_name: str = None,
        metadata: dict = None,
    ) -> ProjectState:
        """
        Create a new project.

        Args:
            audio_path: Path to audio file
            project_name: Optional project name
            metadata: Optional metadata dictionary

        Returns:
            New ProjectState instance
        """
        project = ProjectState()
        project.audio_file_path = Path(audio_path)
        project.project_name = project_name or audio_path.stem

        if metadata:
            project.metadata = ProjectMetadata(
                title=metadata.get("title", project_name or ""),
                artist=metadata.get("artist", ""),
                song_title=metadata.get("song_title", project_name or ""),
                lyricist=metadata.get("lyricist", ""),
                composer=metadata.get("composer", ""),
                mood=metadata.get("mood", ""),
                story_description=metadata.get("story", ""),
            )

        self.project = project
        self.logger.info(f"Created new project: {project.project_id}")

        return project

    def load_project(self, project_path: Path) -> ProjectState:
        """
        Load existing project.

        Args:
            project_path: Path to project JSON file

        Returns:
            Loaded ProjectState instance
        """
        project = ProjectState.load_from_file(project_path)
        self.project = project
        self.logger.info(f"Loaded project: {project.project_id}")

        return project

    async def run(self) -> None:
        """
        Run the pipeline.

        The pipeline will execute until it reaches a checkpoint
        requiring user input or completes.
        """
        if not self.project:
            raise RuntimeError("No project loaded. Call create_new_project() or load_project() first.")

        try:
            # Initialize ComfyUI connection
            await self.initialize_comfyui()

            # Create FSM
            self.fsm = self._create_fsm()

            # Run FSM
            self.logger.info("Starting pipeline execution")
            await self.fsm.run(self.project)

        except KeyboardInterrupt:
            self.logger.info("Pipeline interrupted by user")
            self.fsm.pause()
        except Exception as e:
            self.logger.exception(f"Pipeline error: {e}")
            raise
        finally:
            # Cleanup
            await self.cleanup_comfyui()
            await self.model_manager.unload_all()

    def provide_user_input(self, trigger: str, data: dict = None) -> None:
        """
        Provide user input to continue from a checkpoint.

        Args:
            trigger: Trigger name (e.g., 'lyrics_confirmed', 'regenerate_visuals')
            data: Optional additional data
        """
        if self.fsm:
            self.fsm.provide_user_input(trigger, data)

    def get_status(self) -> dict:
        """Get current engine status."""
        return {
            "project": {
                "id": self.project.project_id if self.project else None,
                "name": self.project.project_name if self.project else None,
                "state": self.project.current_state if self.project else None,
            },
            "fsm": self.fsm.get_state_info() if self.fsm else None,
            "model_manager": self.model_manager.get_status(),
        }


# ============================================================================
# CLI Interface
# ============================================================================

async def interactive_session(engine: MellowVideoEngine) -> None:
    """
    Run interactive session with user prompts at checkpoints.

    Args:
        engine: MellowVideoEngine instance
    """
    logger = logging.getLogger("interactive")

    while True:
        # Run until checkpoint or completion
        await engine.run()

        # Check state
        if not engine.fsm:
            break

        current_state = engine.fsm.current_state

        if current_state == State.COMPLETED:
            logger.info("Pipeline completed successfully!")
            if engine.project.final_video_path:
                logger.info(f"Final video: {engine.project.final_video_path}")
            break

        if current_state == State.ERROR:
            logger.error("Pipeline encountered an error")
            response = input("Reset and retry? (y/n): ").strip().lower()
            if response == "y":
                engine.fsm.reset(engine.project)
                continue
            else:
                break

        # At a checkpoint
        if engine.fsm.is_at_checkpoint:
            logger.info(f"\n=== Checkpoint: {current_state.name} ===")
            valid_triggers = engine.fsm.get_valid_triggers()
            logger.info(f"Valid actions: {valid_triggers}")

            # Show checkpoint-specific information
            if current_state == State.AUDIO_REVIEW:
                logger.info(f"Extracted {len(engine.project.lyrics_segments)} lyric segments")
                for i, seg in enumerate(engine.project.lyrics_segments[:5]):
                    logger.info(f"  [{seg.start_time:.1f}s - {seg.end_time:.1f}s] {seg.text[:50]}...")
                if len(engine.project.lyrics_segments) > 5:
                    logger.info(f"  ... and {len(engine.project.lyrics_segments) - 5} more")

            elif current_state == State.VISUAL_REVIEW:
                from core.project_state import AssetStatus
                generated = sum(1 for img in engine.project.images.values() if img.status == AssetStatus.GENERATED)
                logger.info(f"Generated {generated} images")

            elif current_state == State.MOTION_REVIEW:
                from core.project_state import AssetStatus
                generated = sum(1 for clip in engine.project.video_clips.values() if clip.status == AssetStatus.GENERATED)
                logger.info(f"Generated {generated} video clips")

            elif current_state == State.POST_REVIEW:
                if engine.project.final_video_path:
                    logger.info(f"Video composed: {engine.project.final_video_path}")

            # Get user input
            print("\nOptions:")
            for i, trigger in enumerate(valid_triggers, 1):
                print(f"  {i}. {trigger}")

            while True:
                choice = input("Enter choice (number or trigger name): ").strip()

                try:
                    # Try as number
                    idx = int(choice) - 1
                    if 0 <= idx < len(valid_triggers):
                        trigger = valid_triggers[idx]
                        break
                except ValueError:
                    # Try as trigger name
                    if choice in valid_triggers:
                        trigger = choice
                        break

                print("Invalid choice. Please try again.")

            # Provide input and continue
            engine.provide_user_input(trigger)


# ============================================================================
# TUI Mode
# ============================================================================

def run_tui_mode(args: argparse.Namespace) -> int:
    """
    Run interactive TUI mode using Textual.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    from ui.app import MellowApp

    try:
        # Create app with configuration
        app = MellowApp(
            config_path=args.config,
            workflows_dir=args.workflows_dir,
            output_dir=args.output_dir,
            initial_audio=args.input_file,
        )

        # Run the TUI
        app.run()
        return 0

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
        return 130

    except Exception as e:
        logging.error(f"Application error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


# ============================================================================
# Headless Mode
# ============================================================================

def run_headless_mode(args: argparse.Namespace) -> int:
    """
    Run pipeline in headless CLI mode.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    from backend.audio_engine import LyricAligner
    from backend.video_engine import RenderConfig, VideoComposer

    logger = logging.getLogger("headless")

    # Validate input
    if not args.input_file:
        logger.error("Input file is required in headless mode")
        return 1

    if not args.input_file.exists():
        logger.error(f"Input file not found: {args.input_file}")
        return 1

    # Setup output
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.output is None:
        if args.transcribe_only:
            args.output = args.output_dir / f"{args.input_file.stem}_lyrics.json"
        else:
            args.output = args.output_dir / f"{args.input_file.stem}_output.mp4"

    logger.info(f"Input: {args.input_file}")
    logger.info(f"Output: {args.output}")
    logger.info(f"Model: {args.model}")

    # =========================================================================
    # Step 1: Transcription
    # =========================================================================

    logger.info("=" * 60)
    logger.info("Step 1: Audio Transcription")
    logger.info("=" * 60)

    def progress_callback(progress: float, status: str) -> None:
        """Print progress to stderr."""
        bar_width = 40
        filled = int(bar_width * progress)
        bar = "=" * filled + "-" * (bar_width - filled)
        print(f"\r[{bar}] {progress*100:5.1f}% {status[:30]:<30}", end="", file=sys.stderr)
        if progress >= 1.0:
            print(file=sys.stderr)

    try:
        aligner = LyricAligner(
            device=args.device,
            compute_type="float16" if args.device == "cuda" else "int8",
        )

        segments = aligner.transcribe(
            audio_path=args.input_file,
            model_size=args.model,
            language=args.language,
            progress_callback=progress_callback,
        )

        logger.info(f"Transcribed {len(segments)} segments")

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return 1

    # =========================================================================
    # Step 2: Save Transcription (if transcribe-only)
    # =========================================================================

    if args.transcribe_only:
        logger.info("=" * 60)
        logger.info("Saving transcription results")
        logger.info("=" * 60)

        try:
            output_data = {
                "audio_file": str(args.input_file),
                "model": args.model,
                "language": args.language,
                "segments": segments,
            }

            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)

            logger.info(f"Saved lyrics to: {args.output}")
            return 0

        except Exception as e:
            logger.error(f"Failed to save transcription: {e}")
            return 1

    # =========================================================================
    # Step 3: Video Generation (requires clips)
    # =========================================================================

    logger.info("=" * 60)
    logger.info("Step 2: Video Generation")
    logger.info("=" * 60)

    # Check for existing clips
    clips_dir = args.output_dir / "clips"
    if not clips_dir.exists():
        logger.warning("No clips directory found. Creating placeholder...")
        logger.warning("In production, clips would be generated via ComfyUI.")
        logger.warning("Skipping video rendering.")

        # Save segments for manual processing
        segments_file = args.output_dir / f"{args.input_file.stem}_segments.json"
        with open(segments_file, "w", encoding="utf-8") as f:
            json.dump({"segments": segments}, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved segments to: {segments_file}")
        logger.info("Run with ComfyUI connected to generate clips, then re-run.")
        return 0

    # Find generated clips
    clip_files = sorted(clips_dir.glob("*.mp4"))
    if not clip_files:
        logger.error("No video clips found in clips directory")
        return 1

    logger.info(f"Found {len(clip_files)} clips")

    # =========================================================================
    # Step 4: Video Rendering
    # =========================================================================

    logger.info("=" * 60)
    logger.info("Step 3: Video Rendering")
    logger.info("=" * 60)

    try:
        # Parse resolution
        width, height = map(int, args.resolution.split("x"))

        config = RenderConfig(
            width=width,
            height=height,
            fps=args.fps,
            transition_type=args.transition,
            transition_duration=args.transition_duration,
        )

        composer = VideoComposer(config=config)

        # Prepare subtitles from segments
        subtitles = [
            {
                "text": seg.get("text", ""),
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
            }
            for seg in segments
        ]

        def render_progress(progress: float, status: str) -> None:
            bar_width = 40
            filled = int(bar_width * (progress / 100))
            bar = "=" * filled + "-" * (bar_width - filled)
            print(f"\r[{bar}] {progress:5.1f}% {status[:30]:<30}", end="", file=sys.stderr)
            if progress >= 100:
                print(file=sys.stderr)

        success = composer.render(
            inputs=clip_files,
            audio=args.input_file,
            subtitles=subtitles,
            output=args.output,
            progress_callback=render_progress,
        )

        if success:
            logger.info(f"Video saved to: {args.output}")
            return 0
        else:
            logger.error("Video rendering failed")
            return 1

    except Exception as e:
        logger.error(f"Rendering failed: {e}")
        return 1


# ============================================================================
# FSM Pipeline Mode (Original)
# ============================================================================

def run_pipeline_mode(args: argparse.Namespace) -> int:
    """
    Run original FSM pipeline mode.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code
    """
    # Validate arguments
    if not args.audio and not args.project:
        logging.error("Either --audio or --project must be specified")
        return 1

    if args.audio and not args.audio.exists():
        logging.error(f"Audio file not found: {args.audio}")
        return 1

    if args.project and not args.project.exists():
        logging.error(f"Project file not found: {args.project}")
        return 1

    # Override debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize engine
    engine = MellowVideoEngine(
        settings_path=args.config,
    )

    # Create or load project
    if args.project:
        engine.load_project(args.project)
    else:
        engine.create_new_project(
            audio_path=args.audio,
            project_name=args.name,
            metadata={
                "artist": args.artist,
                "mood": args.mood,
                "story": args.story,
                "song_title": args.name or args.audio.stem,
            },
        )

    # Run
    if args.non_interactive:
        # Non-interactive mode - auto-confirm all checkpoints
        async def auto_run():
            while True:
                await engine.run()

                if not engine.fsm:
                    break

                current_state = engine.fsm.current_state

                if current_state in (State.COMPLETED, State.ERROR):
                    break

                if engine.fsm.is_at_checkpoint:
                    # Auto-confirm
                    valid_triggers = engine.fsm.get_valid_triggers()
                    # Choose the "confirm" type trigger
                    confirm_trigger = next(
                        (t for t in valid_triggers if "confirm" in t.lower()),
                        valid_triggers[0] if valid_triggers else None,
                    )
                    if confirm_trigger:
                        engine.provide_user_input(confirm_trigger)

        asyncio.run(auto_run())
    else:
        # Interactive mode
        asyncio.run(interactive_session(engine))

    return 0


# ============================================================================
# Argument Parser Setup
# ============================================================================

def create_argument_parser() -> argparse.ArgumentParser:
    """
    Create and configure the argument parser with subcommands.

    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(
        prog="mellow",
        description="Mellow-Video-Engine: AI Music Video Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  tui        Interactive terminal UI (Textual-based)
  headless   Command-line batch processing
  pipeline   Original FSM-based pipeline

Examples:
  %(prog)s tui                              # Launch interactive TUI
  %(prog)s tui song.mp3                     # Launch TUI with audio preloaded
  %(prog)s headless song.mp3 -o output.mp4  # Headless processing
  %(prog)s pipeline --audio song.mp3        # FSM pipeline
        """,
    )

    # Global options
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress non-error output",
    )

    # Subparsers for different modes
    subparsers = parser.add_subparsers(dest="mode", help="Operation mode")

    # =========================================================================
    # TUI Subcommand
    # =========================================================================

    tui_parser = subparsers.add_parser(
        "tui",
        help="Interactive terminal UI",
        description="Launch the Textual-based interactive TUI",
    )
    tui_parser.add_argument(
        "input_file",
        type=Path,
        nargs="?",
        default=None,
        help="Input audio file to preload",
    )
    tui_parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=Path("workflows"),
        help="Workflows directory (default: ./workflows)",
    )
    tui_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory (default: ./output)",
    )
    tui_parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.yaml"),
        help="Configuration file path",
    )

    # =========================================================================
    # Headless Subcommand
    # =========================================================================

    headless_parser = subparsers.add_parser(
        "headless",
        help="Command-line batch processing",
        description="Run pipeline in headless CLI mode",
    )
    headless_parser.add_argument(
        "input_file",
        type=Path,
        help="Input audio file (mp3, wav, flac, m4a, ogg, aac)",
    )
    headless_parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path",
    )
    headless_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory (default: ./output)",
    )
    headless_parser.add_argument(
        "--transcribe-only",
        action="store_true",
        help="Only transcribe audio, skip video generation",
    )
    headless_parser.add_argument(
        "--model",
        type=str,
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        default="large-v3",
        help="Whisper model size (default: large-v3)",
    )
    headless_parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Audio language code (auto-detect if not specified)",
    )
    headless_parser.add_argument(
        "--device",
        type=str,
        choices=["cuda", "cpu"],
        default="cuda",
        help="Compute device (default: cuda)",
    )
    headless_parser.add_argument(
        "--resolution",
        type=str,
        default="1920x1080",
        help="Output resolution (default: 1920x1080)",
    )
    headless_parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Output frame rate (default: 30)",
    )
    headless_parser.add_argument(
        "--transition",
        type=str,
        choices=["fade", "dissolve", "wipeleft", "wiperight", "slideup", "slidedown"],
        default="fade",
        help="Transition type (default: fade)",
    )
    headless_parser.add_argument(
        "--transition-duration",
        type=float,
        default=0.5,
        help="Transition duration in seconds (default: 0.5)",
    )

    # =========================================================================
    # Pipeline Subcommand (Original FSM)
    # =========================================================================

    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help="Original FSM-based pipeline",
        description="Run the full FSM-based video generation pipeline",
    )
    pipeline_parser.add_argument(
        "--audio",
        type=Path,
        help="Path to audio file (mp3, wav)",
    )
    pipeline_parser.add_argument(
        "--project",
        type=Path,
        help="Path to existing project file (JSON)",
    )
    pipeline_parser.add_argument(
        "--name",
        type=str,
        help="Project name",
    )
    pipeline_parser.add_argument(
        "--artist",
        type=str,
        default="",
        help="Artist name",
    )
    pipeline_parser.add_argument(
        "--mood",
        type=str,
        default="",
        help="Mood/atmosphere description",
    )
    pipeline_parser.add_argument(
        "--story",
        type=str,
        default="",
        help="Story/visual concept description",
    )
    pipeline_parser.add_argument(
        "--config",
        type=Path,
        help="Custom settings.yaml path",
    )
    pipeline_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run in non-interactive mode (auto-confirm checkpoints)",
    )
    pipeline_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser


# ============================================================================
# Main Entry Point
# ============================================================================

def main() -> int:
    """
    Main entry point with mode selection.

    Returns:
        Exit code
    """
    # Setup basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    parser = create_argument_parser()
    args = parser.parse_args()

    # Configure logging level
    if hasattr(args, 'quiet') and args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif hasattr(args, 'verbose') and args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Route to appropriate mode
    if args.mode == "tui":
        return run_tui_mode(args)
    elif args.mode == "headless":
        return run_headless_mode(args)
    elif args.mode == "pipeline":
        return run_pipeline_mode(args)
    else:
        # Default: show help or launch TUI
        if len(sys.argv) == 1:
            # No arguments - launch TUI with defaults
            class DefaultArgs:
                input_file = None
                workflows_dir = Path("workflows")
                output_dir = Path("output")
                config = Path("config/settings.yaml")
                verbose = False

            return run_tui_mode(DefaultArgs())
        else:
            parser.print_help()
            return 0


if __name__ == "__main__":
    sys.exit(main())
