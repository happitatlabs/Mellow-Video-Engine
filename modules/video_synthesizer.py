"""
Video Synthesizer Module
========================
State 3: Motion Synthesis
Converts static images to video clips using SVD, LTX-2, or ComfyUI workflows.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.project_state import ProjectState
    from core.model_manager import ModelManager
    from core.fsm_manager import FSMManager

from core.fsm_manager import StateHandler
from core.project_state import VideoClip, ImageAsset, AssetStatus

logger = logging.getLogger(__name__)


class VideoSynthesizer:
    """
    Synthesizes video clips from static images.

    Features:
    - Image-to-video generation via ComfyUI (SVD, LTX-2)
    - Motion type selection based on mood
    - Loop and crossfade preparation
    - Batch processing with progress tracking
    """

    # Motion type configurations
    MOTION_PRESETS = {
        "slow_zoom": {
            "description": "Slow zoom in/out",
            "frames": 49,
            "motion_bucket_id": 80,
            "augmentation_level": 0.3,
        },
        "slow_pan": {
            "description": "Slow horizontal pan",
            "frames": 49,
            "motion_bucket_id": 100,
            "augmentation_level": 0.4,
        },
        "dynamic_pan": {
            "description": "Dynamic camera movement",
            "frames": 49,
            "motion_bucket_id": 150,
            "augmentation_level": 0.5,
        },
        "parallax": {
            "description": "Depth-based parallax effect",
            "frames": 49,
            "motion_bucket_id": 120,
            "augmentation_level": 0.4,
        },
        "ambient": {
            "description": "Subtle ambient motion (clouds, water)",
            "frames": 49,
            "motion_bucket_id": 60,
            "augmentation_level": 0.2,
        },
    }

    def __init__(
        self,
        comfyui_client,
        config: dict,
        output_dir: Path,
    ):
        """
        Initialize VideoSynthesizer.

        Args:
            comfyui_client: ComfyUI client instance
            config: Video generation configuration
            output_dir: Directory to save generated videos
        """
        self.comfyui = comfyui_client
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(self.__class__.__name__)

    def select_motion_type(self, mood: str, scene_description: str = "") -> str:
        """
        Select appropriate motion type based on mood and scene.

        Args:
            mood: Overall mood of the segment
            scene_description: Optional scene description

        Returns:
            Motion type key
        """
        mood_lower = mood.lower()

        # Simple mood-to-motion mapping
        if any(word in mood_lower for word in ["calm", "peaceful", "serene"]):
            return "slow_zoom"
        elif any(word in mood_lower for word in ["sad", "melancholic", "lonely"]):
            return "slow_pan"
        elif any(word in mood_lower for word in ["energetic", "exciting", "dynamic"]):
            return "dynamic_pan"
        elif any(word in mood_lower for word in ["dreamy", "ethereal", "floating"]):
            return "parallax"
        elif any(word in mood_lower for word in ["natural", "outdoor", "sky"]):
            return "ambient"
        else:
            return "slow_zoom"  # Default

    def build_video_workflow(
        self,
        image_path: Path,
        motion_type: str = "slow_zoom",
        fps: int = 24,
        duration: float = 4.0,
    ) -> dict:
        """
        Build ComfyUI workflow for image-to-video generation.

        Args:
            image_path: Path to source image
            motion_type: Motion preset key
            fps: Frames per second
            duration: Desired duration in seconds

        Returns:
            ComfyUI workflow dictionary
        """
        preset = self.MOTION_PRESETS.get(motion_type, self.MOTION_PRESETS["slow_zoom"])
        frames = min(int(duration * fps), preset["frames"])

        # SVD/LTX-2 workflow structure
        # This is a simplified template - actual workflow would be more complex
        workflow = {
            "1": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": str(image_path),
                },
            },
            "2": {
                "class_type": "ImageOnlyCheckpointLoader",
                "inputs": {
                    "ckpt_name": self.config.get("model", "svd_xt.safetensors"),
                },
            },
            "3": {
                "class_type": "SVD_img2vid_Conditioning",
                "inputs": {
                    "clip_vision": ["2", 1],
                    "init_image": ["1", 0],
                    "vae": ["2", 2],
                    "width": 1280,
                    "height": 720,
                    "video_frames": frames,
                    "motion_bucket_id": preset["motion_bucket_id"],
                    "fps": fps,
                    "augmentation_level": preset["augmentation_level"],
                },
            },
            "4": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["3", 1],
                    "latent_image": ["3", 2],
                    "seed": -1,
                    "steps": 20,
                    "cfg": 2.5,
                    "sampler_name": "euler",
                    "scheduler": "karras",
                    "denoise": 1.0,
                },
            },
            "5": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["4", 0],
                    "vae": ["2", 2],
                },
            },
            "6": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["5", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "mellow_video",
                    "format": "video/h264-mp4",
                    "pingpong": False,
                    "save_output": True,
                },
            },
        }

        return workflow

    async def generate_video_clip(
        self,
        image: ImageAsset,
        motion_type: str,
        duration: float = 4.0,
        progress_callback: Optional[callable] = None,
    ) -> VideoClip:
        """
        Generate a video clip from an image.

        Args:
            image: Source image asset
            motion_type: Motion type preset
            duration: Desired clip duration
            progress_callback: Optional progress callback

        Returns:
            Generated VideoClip object
        """
        clip = VideoClip(
            source_image_id=image.id,
            duration=duration,
            fps=self.config.get("fps", 24),
            motion_type=motion_type,
            motion_params=self.MOTION_PRESETS.get(motion_type, {}),
            status=AssetStatus.GENERATING,
        )

        try:
            # Build workflow
            workflow = self.build_video_workflow(
                image_path=image.file_path,
                motion_type=motion_type,
                fps=clip.fps,
                duration=duration,
            )

            # Generate via ComfyUI
            async def cb(event_type, data):
                if progress_callback:
                    await progress_callback(event_type, data)

            result = await self.comfyui.queue_prompt(workflow, cb)

            # Download result
            if result.get("videos"):
                vid_info = result["videos"][0]
                save_path = self.output_dir / f"{clip.id}.mp4"

                await self.comfyui.download_output(
                    filename=vid_info["filename"],
                    subfolder=vid_info["subfolder"],
                    output_type=vid_info["type"],
                    save_path=save_path,
                )

                clip.file_path = save_path
                clip.status = AssetStatus.GENERATED
                clip.generation_time = datetime.now()

                self.logger.info(f"Generated video: {save_path}")
            else:
                raise RuntimeError("No video output from ComfyUI")

        except Exception as e:
            self.logger.error(f"Video generation failed: {e}")
            clip.status = AssetStatus.FAILED
            clip.error_message = str(e)

        return clip

    async def generate_all_clips(
        self,
        project: ProjectState,
        mood: str = "",
        progress_callback: Optional[callable] = None,
    ) -> list[VideoClip]:
        """
        Generate video clips for all confirmed images.

        Args:
            project: Project state
            mood: Overall mood for motion selection
            progress_callback: Progress callback

        Returns:
            List of generated VideoClip objects
        """
        clips = []
        confirmed_images = project.get_confirmed_images()

        if not confirmed_images:
            self.logger.warning("No confirmed images to process")
            return clips

        total = len(confirmed_images)
        self.logger.info(f"Generating {total} video clips")

        for i, image in enumerate(confirmed_images):
            self.logger.info(f"Processing image {i+1}/{total}: {image.id}")

            # Select motion type
            motion_type = self.select_motion_type(mood, image.prompt)

            # Calculate duration based on linked segments
            duration = 4.0  # Default
            if image.segment_ids:
                # Find linked segments and calculate total duration
                linked_duration = 0.0
                for seg in project.lyrics_segments:
                    if seg.id in image.segment_ids:
                        linked_duration += seg.duration
                if linked_duration > 0:
                    duration = min(linked_duration, 8.0)  # Cap at 8 seconds

            # Generate clip
            async def clip_progress(event_type, data):
                if progress_callback:
                    await progress_callback(i, total, event_type, data)

            clip = await self.generate_video_clip(
                image=image,
                motion_type=motion_type,
                duration=duration,
                progress_callback=clip_progress,
            )

            # Add to project
            project.add_video_clip(clip)

            # Update segment references
            for seg_id in image.segment_ids:
                project.update_lyric_segment(seg_id, assigned_video_id=clip.id)

            clips.append(clip)

        successful = sum(1 for c in clips if c.status == AssetStatus.GENERATED)
        self.logger.info(f"Generated {successful}/{total} clips successfully")

        return clips

    def prepare_for_composition(
        self,
        clips: list[VideoClip],
        total_duration: float,
    ) -> list[dict]:
        """
        Prepare clip sequence for composition with looping/crossfade.

        Args:
            clips: List of video clips
            total_duration: Target total duration (e.g., 3:30 = 210 seconds)

        Returns:
            List of composition instructions
        """
        if not clips:
            return []

        composition = []
        current_time = 0.0
        clip_index = 0
        crossfade_duration = 1.0  # seconds

        while current_time < total_duration:
            clip = clips[clip_index % len(clips)]

            if clip.status != AssetStatus.GENERATED or not clip.file_path:
                clip_index += 1
                continue

            instruction = {
                "clip_id": clip.id,
                "file_path": str(clip.file_path),
                "start_time": current_time,
                "duration": clip.duration,
                "crossfade_in": crossfade_duration if current_time > 0 else 0,
                "crossfade_out": crossfade_duration,
                "loop_count": 1,
            }

            # If this is the last iteration and we need to extend
            remaining = total_duration - current_time
            if remaining < clip.duration * 2:
                # Extend with loop
                loops_needed = int(remaining / clip.duration) + 1
                instruction["loop_count"] = loops_needed
                instruction["duration"] = remaining

            composition.append(instruction)
            current_time += clip.duration - crossfade_duration
            clip_index += 1

        return composition


# ============================================================================
# FSM Handler
# ============================================================================

class MotionSynthesisHandler(StateHandler):
    """FSM Handler for MOTION_SYNTHESIS state."""

    def __init__(
        self,
        fsm: FSMManager,
        comfyui_client,
        config: dict,
        output_dir: Path,
    ):
        super().__init__(fsm)
        self.comfyui_client = comfyui_client
        self.config = config
        self.output_dir = output_dir
        self.synthesizer: Optional[VideoSynthesizer] = None

    async def enter(self, project: ProjectState) -> None:
        """Initialize video synthesizer."""
        self.logger.info("Entering MOTION_SYNTHESIS state")

        self.synthesizer = VideoSynthesizer(
            comfyui_client=self.comfyui_client,
            config=self.config,
            output_dir=self.output_dir,
        )

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        """Execute video clip generation."""
        try:
            self.logger.info("Starting motion synthesis...")

            # Generate clips for all confirmed images
            clips = await self.synthesizer.generate_all_clips(
                project=project,
                mood=project.metadata.mood,
            )

            if not clips:
                self.logger.error("No clips were generated")
                return False, "synthesis_failed"

            successful = sum(1 for c in clips if c.status == AssetStatus.GENERATED)
            if successful == 0:
                return False, "synthesis_failed"

            # Prepare composition sequence
            composition = self.synthesizer.prepare_for_composition(
                clips=[c for c in clips if c.status == AssetStatus.GENERATED],
                total_duration=project.audio_duration,
            )

            self.logger.info(
                f"Motion synthesis complete: {successful} clips, "
                f"{len(composition)} composition segments"
            )

            return True, "synthesis_complete"

        except Exception as e:
            self.logger.exception(f"Motion synthesis failed: {e}")
            return False, "synthesis_failed"

    async def exit(self, project: ProjectState) -> None:
        """Cleanup."""
        self.logger.info("Exiting MOTION_SYNTHESIS state")
        self.synthesizer = None
