import gradio as gr
import asyncio
import logging
from pathlib import Path
import sys
import json
from typing import List, Dict, Any, Optional

# Import engine components
from main import MellowVideoEngine, State
from core.fsm_manager import FSMManager

# Logger setup
logger = logging.getLogger("MellowWeb")

# =============================================================================
# Constants
# =============================================================================
MAX_SCENES = 20  # 최대 씬 개수 (visibility로 제어)


# =============================================================================
# Helper Functions for Scene Plan Display
# =============================================================================

def format_scene_plans_for_display(project) -> str:
    """
    Format scene plans as editable JSON text for user review.
    (Legacy function - kept for backward compatibility)

    Returns:
        Formatted JSON string of scene plans
    """
    if not project:
        return "[]"

    scene_plans = []

    # Try different attribute names
    if hasattr(project, 'scene_plans') and project.scene_plans:
        scene_plans = project.scene_plans
    elif hasattr(project, 'visual_plans') and project.visual_plans:
        scene_plans = project.visual_plans

    if not scene_plans:
        return "[]"

    # Format as pretty JSON for easy editing
    try:
        return json.dumps(scene_plans, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to format scene plans: {e}")
        return "[]"


def get_scene_plans_data(project) -> List[Dict[str, Any]]:
    """
    Get scene plans data with lyrics for storyboard display.

    Returns:
        List of scene plan dictionaries with lyrics attached
    """
    if not project:
        return []

    scene_plans = []

    # Get scene plans
    if hasattr(project, 'scene_plans') and project.scene_plans:
        scene_plans = project.scene_plans
    elif hasattr(project, 'visual_plans') and project.visual_plans:
        scene_plans = project.visual_plans

    if not scene_plans:
        return []

    # Get lyrics segments for matching
    lyrics_segments = []
    if hasattr(project, 'lyrics_segments') and project.lyrics_segments:
        lyrics_segments = project.lyrics_segments

    # Combine scene plans with lyrics
    result = []
    for i, plan in enumerate(scene_plans):
        if isinstance(plan, dict):
            scene_data = plan.copy()

            # Match with lyrics segment by index or segment_id
            lyric_text = ""
            start_time = 0.0
            end_time = 0.0
            if i < len(lyrics_segments):
                seg = lyrics_segments[i]
                if hasattr(seg, 'text'):
                    lyric_text = seg.text
                    start_time = getattr(seg, 'start_time', 0.0)
                    end_time = getattr(seg, 'end_time', 0.0)
                elif isinstance(seg, dict):
                    lyric_text = seg.get('text', '')
                    start_time = seg.get('start_time', 0.0)
                    end_time = seg.get('end_time', 0.0)

            # If segment_id exists, try to match
            if not lyric_text and 'segment_id' in scene_data:
                for seg in lyrics_segments:
                    seg_id = getattr(seg, 'id', None) if hasattr(seg, 'id') else (seg.get('id') if isinstance(seg, dict) else None)
                    if seg_id == scene_data.get('segment_id'):
                        lyric_text = getattr(seg, 'text', '') if hasattr(seg, 'text') else (seg.get('text', '') if isinstance(seg, dict) else '')
                        start_time = getattr(seg, 'start_time', 0.0) if hasattr(seg, 'start_time') else (seg.get('start_time', 0.0) if isinstance(seg, dict) else 0.0)
                        end_time = getattr(seg, 'end_time', 0.0) if hasattr(seg, 'end_time') else (seg.get('end_time', 0.0) if isinstance(seg, dict) else 0.0)
                        break

            scene_data['lyric_text'] = lyric_text
            scene_data['start_time'] = start_time
            scene_data['end_time'] = end_time
            scene_data['scene_index'] = i + 1
            result.append(scene_data)

    return result


def format_scene_plans_as_table(project) -> List[List[str]]:
    """
    Format scene plans as a table for gr.Dataframe display.

    Returns:
        List of rows: [[index, visual_prompt, camera_movement, lighting], ...]
    """
    if not project:
        return []

    scene_plans = []

    if hasattr(project, 'scene_plans') and project.scene_plans:
        scene_plans = project.scene_plans
    elif hasattr(project, 'visual_plans') and project.visual_plans:
        scene_plans = project.visual_plans

    if not scene_plans:
        return []

    rows = []
    for i, plan in enumerate(scene_plans):
        if isinstance(plan, dict):
            rows.append([
                str(i + 1),
                plan.get('visual_prompt', '')[:100] + '...' if len(plan.get('visual_prompt', '')) > 100 else plan.get('visual_prompt', ''),
                plan.get('camera_movement', 'static'),
                plan.get('lighting', 'soft'),
            ])

    return rows


def parse_edited_scene_plans(plans_json: str, project) -> bool:
    """
    Parse edited scene plans JSON back into project.

    Args:
        plans_json: JSON string of edited scene plans
        project: Project state object

    Returns:
        True if successful
    """
    if not plans_json.strip() or plans_json.strip() == "[]":
        return False

    try:
        parsed_plans = json.loads(plans_json)

        if not isinstance(parsed_plans, list):
            return False

        # Update project
        if hasattr(project, 'scene_plans'):
            project.scene_plans = parsed_plans
        if hasattr(project, 'visual_plans'):
            project.visual_plans = parsed_plans

        return True

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse scene plans JSON: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to update scene plans: {e}")
        return False


def get_generated_images(project) -> List[str]:
    """
    Get list of generated image paths from project.

    Returns:
        List of image file paths
    """
    if not project:
        return []

    images = []

    # Try generated_images attribute
    if hasattr(project, 'generated_images') and project.generated_images:
        images = project.generated_images

    # Try images dictionary
    elif hasattr(project, 'images') and project.images:
        for key, img_data in project.images.items():
            if isinstance(img_data, dict) and 'path' in img_data:
                images.append(img_data['path'])
            elif isinstance(img_data, str):
                images.append(img_data)

    # Filter to only existing files
    existing = [p for p in images if Path(p).exists()]

    return existing


def get_scene_image(project, scene_index: int) -> Optional[str]:
    """
    Get generated image path for a specific scene.

    Args:
        project: Project state
        scene_index: 0-based scene index

    Returns:
        Image file path or None
    """
    if not project:
        return None

    # Try images dictionary with scene index key
    if hasattr(project, 'images') and project.images:
        key = f"scene_{scene_index}"
        if key in project.images:
            img_data = project.images[key]
            if isinstance(img_data, dict) and 'path' in img_data:
                path = img_data['path']
            elif isinstance(img_data, str):
                path = img_data
            else:
                return None
            if Path(path).exists():
                return path

    # Try generated_images list
    if hasattr(project, 'generated_images') and project.generated_images:
        if scene_index < len(project.generated_images):
            path = project.generated_images[scene_index]
            if Path(path).exists():
                return path

    return None


def get_scene_video(project, scene_index: int) -> Optional[str]:
    """
    Get generated video clip path for a specific scene.

    Args:
        project: Project state
        scene_index: 0-based scene index

    Returns:
        Video file path or None
    """
    if not project:
        return None

    # Try video_clips dictionary
    if hasattr(project, 'video_clips') and project.video_clips:
        key = f"scene_{scene_index}"
        if key in project.video_clips:
            clip_data = project.video_clips[key]
            if isinstance(clip_data, dict) and 'path' in clip_data:
                path = clip_data['path']
            elif isinstance(clip_data, str):
                path = clip_data
            else:
                return None
            if Path(path).exists():
                return path

    # Try generated_clips list
    if hasattr(project, 'generated_clips') and project.generated_clips:
        if scene_index < len(project.generated_clips):
            path = project.generated_clips[scene_index]
            if Path(path).exists():
                return path

    return None


class GradioFSMController:
    """
    Controller that wraps the FSM for Gradio-compatible step-by-step execution.

    Instead of blocking on user input, this controller runs one "phase" at a time
    and returns control to the UI when a checkpoint is reached.
    """

    def __init__(self, engine: MellowVideoEngine):
        self.engine = engine
        self.logger = logging.getLogger(self.__class__.__name__)
        self._checkpoint_entered: set = set()  # 이미 enter()가 호출된 체크포인트 추적

    async def trigger_checkpoint_transition(self, trigger: str) -> bool:
        """
        체크포인트에서 수동으로 FSM 전이를 트리거합니다.

        Args:
            trigger: FSM 트리거 문자열 (예: "lyrics_confirmed")

        Returns:
            전이 성공 여부
        """
        fsm = self.engine.fsm
        project = self.engine.project

        if not fsm or not project:
            self.logger.error("FSM or project not initialized")
            return False

        current_state = fsm.current_state
        self.logger.info(f"Triggering transition from {current_state.name} with trigger: {trigger}")

        # 현재 상태의 핸들러 exit 호출
        handler = fsm._handlers.get(current_state)
        if handler:
            await handler.exit(project)

        # 체크포인트 추적에서 제거
        self._checkpoint_entered.discard(current_state)

        # FSM 전이 실행
        success = await fsm.trigger_transition(trigger, project)

        if success:
            self.logger.info(f"Transition successful: {current_state.name} -> {fsm.current_state.name}")
        else:
            self.logger.error(f"Transition failed for trigger: {trigger}")

        return success

    async def run_until_checkpoint_or_complete(self) -> tuple[State, str]:
        """
        Run the FSM until it reaches a checkpoint or terminal state.

        Unlike the blocking fsm.run(), this method returns immediately
        when a checkpoint is encountered, allowing the UI to update.

        Returns:
            tuple[State, str]: (current_state, status_message)
        """
        fsm = self.engine.fsm
        project = self.engine.project

        if not fsm:
            return State.ERROR, "FSM not initialized"

        while True:
            current_state = fsm.current_state

            # Terminal states - return immediately
            if current_state in (State.COMPLETED, State.ERROR):
                return current_state, f"Terminal state: {current_state.name}"

            # Checkpoint states - return to UI for user interaction
            handler = fsm._handlers.get(current_state)
            if handler and handler.requires_user_input():
                # enter()가 이미 호출되었는지 확인 (중복 호출 방지)
                if current_state not in self._checkpoint_entered:
                    await handler.enter(project)
                    self._checkpoint_entered.add(current_state)
                return current_state, f"Checkpoint: {current_state.name}"

            # Execute the current state
            if handler:
                try:
                    success, next_trigger = await handler.execute(project)

                    if success and next_trigger:
                        await fsm.trigger_transition(next_trigger, project)
                    elif not success:
                        # Handle failure
                        error_trigger = f"{current_state.name.lower()}_failed"
                        if fsm.can_transition(error_trigger, project):
                            await fsm.trigger_transition(error_trigger, project)
                        else:
                            return State.ERROR, f"State {current_state.name} failed"
                except Exception as e:
                    self.logger.exception(f"Error executing state {current_state.name}")
                    return State.ERROR, str(e)
            else:
                self.logger.warning(f"No handler for state {current_state.name}")
                return State.ERROR, f"No handler for {current_state.name}"


async def initialize_engine(audio_file, artist, title, mood, story, full_lyrics=None):
    """
    Initialize engine and create project. Returns engine instance.
    """
    if not audio_file:
        return None, "No audio file provided"

    engine = MellowVideoEngine()

    try:
        await engine.initialize_comfyui()
    except Exception as e:
        return None, f"ComfyUI connection failed: {str(e)}"

    # Create project
    project_name = Path(audio_file).stem
    metadata = {
        "artist": artist,
        "song_title": title,
        "mood": mood,
        "story": story
    }

    engine.create_new_project(
        audio_path=Path(audio_file),
        project_name=project_name,
        metadata=metadata
    )

    # Store full lyrics if provided (for forced alignment)
    if full_lyrics and full_lyrics.strip():
        engine.project.user_provided_lyrics = full_lyrics.strip()
        logger.info("Stored user-provided full lyrics for forced alignment")

    # Create FSM
    engine.fsm = engine._create_fsm()

    return engine, "Engine initialized successfully"


def format_lyrics_for_display(project) -> str:
    """Format lyrics segments as editable text."""
    if not project or not project.lyrics_segments:
        return ""

    lines = []
    for seg in project.lyrics_segments:
        # Format: [start - end] text
        lines.append(f"[{seg.start_time:.2f} - {seg.end_time:.2f}] {seg.text}")

    return "\n".join(lines)


def parse_edited_lyrics(lyrics_text: str, project) -> bool:
    """
    Parse edited lyrics back into project.
    Returns True if successful.

    Note: This function now supports both:
    1. Time-stamped format: [start - end] text
    2. Plain text format: Will trigger forced alignment
    """
    if not lyrics_text.strip():
        return False

    try:
        # Check if lyrics contain time stamps
        has_timestamps = any(line.strip().startswith("[") and "]" in line
                            for line in lyrics_text.strip().split("\n")
                            if line.strip())

        if has_timestamps:
            # Parse time-stamped format
            lines = lyrics_text.strip().split("\n")
            new_segments = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Parse format: [start - end] text
                if line.startswith("[") and "]" in line:
                    bracket_end = line.index("]")
                    time_part = line[1:bracket_end]
                    text_part = line[bracket_end + 1:].strip()

                    # Parse times
                    if " - " in time_part:
                        start_str, end_str = time_part.split(" - ")
                        start_time = float(start_str.strip())
                        end_time = float(end_str.strip())
                    else:
                        continue

                    # Update or create segment
                    from core.project_state import LyricSegment
                    seg = LyricSegment(
                        text=text_part,
                        start_time=start_time,
                        end_time=end_time,
                    )
                    new_segments.append(seg)

            if new_segments:
                project.lyrics_segments = new_segments
                return True
        else:
            # Plain text format - store for forced alignment
            # Remove time stamps if any
            cleaned_text = lyrics_text.strip()
            project.user_provided_lyrics = cleaned_text
            logger.info("Stored user-provided lyrics for forced alignment")
            return True

    except Exception as e:
        logger.error(f"Failed to parse lyrics: {e}")

    return False


# ============================================================================
# Scene-by-Scene Processing Functions (On-Demand, Human-in-the-Loop)
# ============================================================================

def build_flux_workflow(prompt: str, negative_prompt: str = "", seed: int = None,
                        width: int = 1216, height: int = 684, steps: int = 20,
                        cfg: float = 1.0, output_prefix: str = "scene") -> Dict[str, Any]:
    """
    Build a dynamic ComfyUI workflow for Flux GGUF image generation.

    This creates a workflow JSON for Flux-dev GGUF model.
    Uses separate loaders for UNet (GGUF), CLIP (DualCLIPLoader), and VAE.

    Model files (must match exactly):
        - UNet: flux1-dev-Q3_K_S.gguf
        - CLIP L: clip_l.safetensors
        - T5XXL: t5xxl_fp8_e4m3fn.safetensors
        - VAE: ae.safetensors

    Args:
        prompt: Positive text prompt
        negative_prompt: Negative text prompt (Flux typically ignores this)
        seed: Random seed (None for random)
        width: Image width
        height: Image height
        steps: Sampling steps
        cfg: CFG scale (Flux uses low CFG, typically 1.0)
        output_prefix: Output filename prefix

    Returns:
        ComfyUI workflow dictionary (API format)
    """
    import random

    if seed is None:
        seed = random.randint(1, 10**14)

    # Flux GGUF workflow structure (API format)
    # Node connections: [node_id, output_slot_index]
    workflow = {
        # 1. Load UNet (GGUF format)
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {
                "unet_name": "flux1-dev-Q3_K_S.gguf"
            }
        },
        # 2. Load CLIP (Dual: CLIP-L + T5XXL)
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": "clip_l.safetensors",
                "clip_name2": "t5xxl_fp8_e4m3fn.safetensors",
                "type": "flux"
            }
        },
        # 3. Load VAE
        "3": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": "ae.safetensors"
            }
        },
        # 4. CLIP Text Encode (Positive prompt)
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["2", 0]  # From DualCLIPLoader output 0
            }
        },
        # 5. CLIP Text Encode (Negative/Conditioning - Flux uses empty or minimal)
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "",  # Flux typically uses empty negative
                "clip": ["2", 0]
            }
        },
        # 6. Empty Latent Image (SD3/Flux style)
        "6": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            }
        },
        # 7. KSampler
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],        # From UnetLoaderGGUF output 0
                "positive": ["4", 0],     # From positive CLIPTextEncode
                "negative": ["5", 0],     # From negative CLIPTextEncode
                "latent_image": ["6", 0], # From EmptySD3LatentImage
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0
            }
        },
        # 8. VAE Decode
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["7", 0],  # From KSampler output
                "vae": ["3", 0]       # From VAELoader output
            }
        },
        # 9. Save Image
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["8", 0],  # From VAEDecode output
                "filename_prefix": output_prefix
            }
        }
    }

    return workflow


async def generate_single_scene_image(engine_state, is_processing: bool, scene_index: int, prompt: str):
    """
    Generate image for a single scene (on-demand execution).

    Implements resource lock: only one operation can run at a time.
    Uses ComfyUIClient directly with a dynamically built Flux workflow.

    Args:
        engine_state: Engine instance
        is_processing: Current processing state (resource lock)
        scene_index: 0-based scene index (displayed as 1-based to user)
        prompt: Image generation prompt

    Returns:
        tuple: (status_message, image_path or None, is_processing flag)
    """
    # Resource lock check - prevent concurrent operations
    if is_processing:
        return "⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요.", None, True

    if engine_state is None:
        return "세션이 끊어졌습니다. 새로 시작해주세요.", None, False

    engine = engine_state

    if not prompt or not prompt.strip():
        return "프롬프트가 비어있습니다.", None, False

    # User sees 1-based index, internal is 0-based
    user_scene_num = scene_index + 1

    try:
        logger.info(f"[On-Demand] Generating image for scene {user_scene_num} (index={scene_index}): {prompt[:50]}...")

        # Update scene plan prompt if different
        if hasattr(engine.project, 'scene_plans') and engine.project.scene_plans:
            if scene_index < len(engine.project.scene_plans):
                engine.project.scene_plans[scene_index]['visual_prompt'] = prompt
        elif hasattr(engine.project, 'visual_plans') and engine.project.visual_plans:
            if scene_index < len(engine.project.visual_plans):
                engine.project.visual_plans[scene_index]['visual_prompt'] = prompt

        # Use engine's generate_single_image if available
        if hasattr(engine, 'generate_single_image'):
            image_path = await engine.generate_single_image(scene_index, prompt)
        else:
            # Direct ComfyUI workflow execution
            if not engine.comfyui_client or not engine.comfyui_client.is_connected:
                return "⚠️ ComfyUI가 연결되지 않았습니다. ComfyUI 서버를 확인해주세요.", None, False

            # Get image generation settings from engine config
            img_config = engine.settings.get("comfyui", {}).get("image_generation", {})
            width = img_config.get("width", 1280)
            height = img_config.get("height", 720)
            steps = img_config.get("steps", 20)
            cfg = img_config.get("cfg_scale", 7.5)

            # Build output prefix with project name and scene index
            output_prefix = f"{engine.project.project_name}_scene_{scene_index:03d}"

            # Build Flux workflow
            workflow = build_flux_workflow(
                prompt=prompt,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                output_prefix=output_prefix,
            )

            # Queue the workflow
            prompt_id = await engine.comfyui_client.queue_prompt(workflow)

            if not prompt_id:
                return "⚠️ 워크플로우 실행에 실패했습니다. ComfyUI 로그를 확인해주세요.", None, False

            logger.info(f"Queued image generation for scene {user_scene_num}, prompt_id={prompt_id}")

            # Wait for completion and get result
            import asyncio
            max_wait = 900  # 15 minutes timeout (Flux GGUF needs more time)
            poll_interval = 2.0
            waited = 0

            while waited < max_wait:
                # get_history returns the history data for this prompt_id directly
                history = await engine.comfyui_client.get_history(prompt_id)

                if history:
                    outputs = history.get("outputs", {})

                    # Find SaveImage node output (node "7" in our workflow)
                    for node_id, node_output in outputs.items():
                        if "images" in node_output:
                            for img_info in node_output["images"]:
                                filename = img_info.get("filename", "")
                                subfolder = img_info.get("subfolder", "")

                                if filename:
                                    # Download the image bytes
                                    output_dir = Path(engine.settings.get("project", {}).get("assets_dir", "./assets")) / "generated_images"
                                    output_dir.mkdir(parents=True, exist_ok=True)

                                    image_bytes = await engine.comfyui_client.download_image(
                                        filename=filename,
                                        subfolder=subfolder,
                                        folder_type="output",
                                    )

                                    if image_bytes:
                                        # Save to file
                                        image_path = output_dir / filename
                                        with open(image_path, "wb") as f:
                                            f.write(image_bytes)

                                        # Store in project
                                        if not hasattr(engine.project, 'images'):
                                            engine.project.images = {}
                                        engine.project.images[f"scene_{scene_index}"] = {'path': str(image_path)}

                                        logger.info(f"Image saved: {image_path}")
                                        return f"✅ 씬 {user_scene_num} 이미지 생성 완료!", str(image_path), False

                    # If we got history but no images, check if execution completed
                    status = history.get("status", {})
                    if status.get("completed", False) and not outputs:
                        return "⚠️ 이미지 생성은 완료되었지만 결과를 찾을 수 없습니다.", None, False

                await asyncio.sleep(poll_interval)
                waited += poll_interval

            return "⏱️ 이미지 생성 시간이 초과되었습니다. ComfyUI를 확인해주세요.", None, False

        if image_path and Path(image_path).exists():
            return f"✅ 씬 {user_scene_num} 이미지 생성 완료!", str(image_path), False
        else:
            return "이미지 파일을 찾을 수 없습니다.", None, False

    except Exception as e:
        logger.exception(f"Failed to generate image for scene {user_scene_num} (index={scene_index})")
        return f"에러: {str(e)}", None, False


async def generate_single_scene_video(engine_state, is_processing: bool, scene_index: int, image_path: str):
    """
    Generate video clip for a single scene from its image (on-demand execution).

    Implements resource lock: only one operation can run at a time.
    Uses ComfyVideoAgent with SVD workflow for image-to-video generation.

    Args:
        engine_state: Engine instance
        is_processing: Current processing state (resource lock)
        scene_index: 0-based scene index (displayed as 1-based to user)
        image_path: Path to source image

    Returns:
        tuple: (status_message, video_path or None, is_processing flag)
    """
    # Resource lock check - prevent concurrent operations
    if is_processing:
        return "⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요.", None, True

    if engine_state is None:
        return "세션이 끊어졌습니다. 새로 시작해주세요.", None, False

    engine = engine_state

    # User sees 1-based index
    user_scene_num = scene_index + 1

    if not image_path or not Path(image_path).exists():
        return "🖼️ 이미지가 없습니다. 먼저 이미지를 생성해주세요.", None, False

    try:
        logger.info(f"[On-Demand] Generating video for scene {user_scene_num} (index={scene_index}) from {image_path}")

        # Get motion settings from scene plan
        motion_bucket_id = 127  # default (1-255, higher = more motion)
        if hasattr(engine.project, 'scene_plans') and engine.project.scene_plans:
            if scene_index < len(engine.project.scene_plans):
                plan = engine.project.scene_plans[scene_index]
                camera_movement = plan.get('camera_movement', 'static')
                # Map camera movement to motion_bucket_id
                motion_map = {
                    'static': 50,
                    'slow_zoom': 80,
                    'slow_pan': 100,
                    'zoom_in': 120,
                    'zoom_out': 120,
                    'pan_left': 127,
                    'pan_right': 127,
                    'dynamic': 180,
                }
                motion_bucket_id = motion_map.get(camera_movement, 127)

        # Call video generation
        if hasattr(engine, 'generate_single_video'):
            video_path = await engine.generate_single_video(scene_index, image_path, motion_bucket_id)
        else:
            # Use ComfyVideoAgent for SVD image-to-video generation
            from modules.comfy_video_agent import (
                ComfyVideoAgent, ComfyConfig, VideoGenerationParams, WorkflowType
            )

            # Get video generation settings
            video_config = engine.settings.get("comfyui", {}).get("video_generation", {})
            frames = video_config.get("frames", 49)
            fps = video_config.get("fps", 24)

            # Configure ComfyVideoAgent
            config = ComfyConfig(
                host=engine.settings.get("comfyui", {}).get("host", "127.0.0.1"),
                port=engine.settings.get("comfyui", {}).get("port", 8188),
                use_ssl=engine.settings.get("comfyui", {}).get("use_ssl", False),
                svd_workflow_path=Path("workflows/svd_example_api.json"),
            )

            agent = ComfyVideoAgent(config)

            # Connect to ComfyUI
            if not agent.connect():
                return "⚠️ ComfyUI 연결에 실패했습니다.", None, False

            try:
                # Set up generation parameters
                params = VideoGenerationParams(
                    source_image_path=Path(image_path),
                    motion_bucket_id=motion_bucket_id,
                    num_frames=frames,
                    fps=fps,
                    output_prefix=f"{engine.project.project_name}_scene_{scene_index:03d}",
                    output_dir=Path(engine.settings.get("project", {}).get("assets_dir", "./assets")) / "generated_videos",
                )

                # Generate video using SVD workflow
                result = agent.generate_video(
                    workflow_type=WorkflowType.SVD,
                    params=params,
                )

                if result.success and result.output_files:
                    video_path = str(result.output_files[0])
                    # Store in project
                    if not hasattr(engine.project, 'video_clips'):
                        engine.project.video_clips = {}
                    engine.project.video_clips[f"scene_{scene_index}"] = {'path': video_path}
                    logger.info(f"Video generated: {video_path}")
                else:
                    error_msg = result.error_message or "영상 생성에 실패했습니다."
                    logger.error(f"Video generation failed: {error_msg}")
                    return f"⚠️ {error_msg}", None, False

            finally:
                agent.disconnect()

        if video_path and Path(video_path).exists():
            return f"✅ 씬 {user_scene_num} 영상 생성 완료!", video_path, False
        else:
            return "영상 파일을 찾을 수 없습니다.", None, False

    except Exception as e:
        logger.exception(f"Failed to generate video for scene {user_scene_num} (index={scene_index})")
        return f"에러: {str(e)}", None, False


# ============================================================================
# Main Processing Functions (No Auto-Run - Human-in-the-Loop Control)
# ============================================================================

async def start_processing(audio_file, full_lyrics, artist, title, mood, story, progress=gr.Progress()):
    """
    1단계: 엔진 초기화 후 AUDIO_REVIEW 체크포인트까지 실행

    Returns tuple for all UI components
    """
    # Create default scene row updates (all hidden)
    default_scene_updates = []
    for i in range(MAX_SCENES):
        default_scene_updates.extend([
            gr.update(visible=False),  # scene_group
            "",                         # lyrics_md
            "",                         # prompt_input
            None,                       # image_output
            None,                       # video_output
        ])

    if not audio_file:
        yield (
            "노래 파일을 먼저 올려주세요!\n\n왼쪽 위에서 파일을 선택해주세요.",
            None,  # engine_state
            "",    # lyrics_input
            gr.update(visible=False),  # lyric_review_group
            gr.update(visible=False),  # scene_workspace
            gr.update(visible=True),   # start_btn
            None,  # video
            *default_scene_updates,
        )
        return

    # Initialize engine
    yield (
        "시스템을 준비하고 있어요...\n\n잠시만 기다려주세요!",
        None, "",
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        *default_scene_updates,
    )

    engine, init_msg = await initialize_engine(audio_file, artist, title, mood, story, full_lyrics)

    if not engine:
        yield (
            f"연결에 실패했어요.\n\n{init_msg}\n\n관리자에게 문의해주세요!",
            None, "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            None,
            *default_scene_updates,
        )
        return

    yield (
        f"준비 완료!\n\n'{engine.project.project_name}' 노래를 분석하고 있어요...\n\n조금만 기다려주세요 (1~2분 정도)",
        engine, "",
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        *default_scene_updates,
    )

    # Create controller for step-by-step execution
    controller = GradioFSMController(engine)

    # Run until we hit a checkpoint or terminal state
    while True:
        current_state = engine.fsm.current_state

        # Update progress message based on state (한국어)
        status_messages = {
            State.INIT: "시작하는 중...",
            State.AUDIO_ANALYSIS: "노래를 듣고 가사를 찾고 있어요...\n\n1~2분 정도 걸려요",
            State.AUDIO_REVIEW: "가사 분석 완료! 아래에서 확인해주세요.",
            State.VISUAL_SCRIPTING: "장면 설명을 만들고 있어요...\n\n2~3분 정도 걸려요",
            State.VISUAL_SCRIPTING_REVIEW: "장면 설명이 완성됐어요! 확인해주세요.",
            State.VISUAL_RENDERING: "예쁜 그림을 그리고 있어요...\n\n5~10분 정도 걸려요",
            State.VISUAL_REVIEW: "그림이 완성됐어요! 확인해주세요.",
            State.MOTION_SYNTHESIS: "영상을 만들고 있어요...\n\n시간이 좀 걸려요 (10~30분)",
            State.MOTION_REVIEW: "영상 클립이 완성됐어요!",
            State.POST_PROCESSING: "자막을 넣고 마무리하고 있어요...",
            State.POST_REVIEW: "영상이 거의 완성됐어요!",
            State.LOCALIZATION: "마지막 마무리 중...",
            State.COMPLETED: "완성!",
            State.ERROR: "문제가 생겼어요.",
        }

        status_msg = status_messages.get(current_state, f"처리 중: {current_state.name}")

        # Run one phase
        result_state, result_msg = await controller.run_until_checkpoint_or_complete()

        # Check if we reached AUDIO_REVIEW checkpoint
        if result_state == State.AUDIO_REVIEW:
            lyrics_text = format_lyrics_for_display(engine.project)
            segment_count = len(engine.project.lyrics_segments) if engine.project.lyrics_segments else 0

            yield (
                f"가사 분석 완료!\n\n총 {segment_count}개 구절을 찾았어요.\n\n아래에서 가사를 확인하고,\n틀린 부분이 있으면 고쳐주세요.\n\n다 확인했으면 '가사 확인 완료' 버튼을 눌러주세요!",
                engine,
                lyrics_text,
                gr.update(visible=True),   # Show lyric review group
                gr.update(visible=False),  # Hide scene workspace
                gr.update(visible=False),  # Hide start button
                None,
                *default_scene_updates,
            )
            return  # Exit generator - user needs to interact

        # Terminal states
        if result_state == State.COMPLETED:
            video_path = engine.project.final_video_path if engine.project else None
            yield (
                "뮤직비디오가 완성되었어요!\n\n아래에서 영상을 확인하세요!",
                engine,
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=True),
                video_path,
                *default_scene_updates,
            )
            await engine.cleanup_comfyui()
            return

        if result_state == State.ERROR:
            yield (
                f"문제가 생겼어요.\n\n{result_msg}\n\n다시 시도하거나 관리자에게 문의해주세요.",
                engine,
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=True),
                None,
                *default_scene_updates,
            )
            await engine.cleanup_comfyui()
            return

        # Continue running (shouldn't reach here often)
        yield (
            status_msg,
            engine,
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            None,
            *default_scene_updates,
        )
        await asyncio.sleep(0.1)


async def confirm_lyrics_and_continue(engine_state, lyrics_text, progress=gr.Progress()):
    """
    2단계: 가사 확인 후 장면 프롬프트 생성 (VISUAL_SCRIPTING_REVIEW까지)
    그 후 Scene Workspace를 표시
    """
    # Create default scene row updates (all hidden)
    default_scene_updates = []
    for i in range(MAX_SCENES):
        default_scene_updates.extend([
            gr.update(visible=False),  # scene_group
            "",                         # lyrics_md
            "",                         # prompt_input
            None,                       # image_output
            None,                       # video_output
        ])

    if engine_state is None:
        yield (
            "세션이 끊어졌어요.\n\n처음부터 다시 시작해주세요!",
            None, "",
            gr.update(visible=False),  # lyric_review_group
            gr.update(visible=False),  # scene_workspace
            gr.update(visible=True),   # start_btn
            None,
            *default_scene_updates,
        )
        return

    engine = engine_state

    # Parse and apply edited lyrics
    user_lyrics_for_alignment = None
    if lyrics_text:
        success = parse_edited_lyrics(lyrics_text, engine.project)
        if success:
            logger.info("Lyrics updated from user edits")

            # Check if user provided plain text (needs forced alignment)
            if hasattr(engine.project, 'user_provided_lyrics') and engine.project.user_provided_lyrics:
                user_lyrics_for_alignment = engine.project.user_provided_lyrics
                logger.info("User provided plain text lyrics - will perform forced alignment")

    # If user provided plain text lyrics, perform forced alignment
    if user_lyrics_for_alignment and engine.project.audio_file_path:
        yield (
            "가사 확인 완료!\n\n가사에 맞춰 타임라인을 재설정하고 있어요...\n\n잠시만 기다려주세요!",
            engine, lyrics_text,
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            None,
            *default_scene_updates,
        )

        try:
            from modules.audio_processor import AudioProcessor
            from core.model_manager import ModelManager

            model_manager = getattr(engine, 'model_manager', None)
            if not model_manager:
                logger.error("Model manager not available in engine")
                raise RuntimeError("Model manager not available")

            settings = getattr(engine, 'settings', {})
            if isinstance(settings, dict):
                whisper_config = settings.get("models", {}).get("whisper", {})
            else:
                whisper_config = {}

            processor = AudioProcessor(model_manager, whisper_config)
            engine.project.lyrics_segments = []

            aligned_segments = await processor.analyze_audio_with_user_lyrics(
                engine.project.audio_file_path,
                user_lyrics_for_alignment,
                language=engine.project.metadata.translations.get("source_language") or "ko",
            )

            for segment in aligned_segments:
                engine.project.add_lyric_segment(segment)

            logger.info(f"Forced alignment complete: {len(aligned_segments)} segments created")
            lyrics_text = format_lyrics_for_display(engine.project)

        except Exception as e:
            logger.exception(f"Forced alignment failed: {e}")

    yield (
        "가사 확인 완료!\n\n이제 장면 설명을 만들고 있어요...\n\n2~3분 정도 기다려주세요!",
        engine, lyrics_text,
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        *default_scene_updates,
    )

    # Create controller for FSM management
    controller = GradioFSMController(engine)

    # Trigger transition from AUDIO_REVIEW to VISUAL_SCRIPTING
    transition_success = await controller.trigger_checkpoint_transition("lyrics_confirmed")

    if not transition_success:
        yield (
            "상태 전이 실패!\n\n다시 시도해주세요.",
            engine, "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            None,
            *default_scene_updates,
        )
        return

    logger.info(f"FSM transitioned to: {engine.fsm.current_state.name}")

    # Run until VISUAL_SCRIPTING_REVIEW checkpoint
    while True:
        current_state = engine.fsm.current_state

        status_messages = {
            State.VISUAL_SCRIPTING: "가사에 맞는 장면 설명을 만들고 있어요...\n\n2~3분 정도 걸려요",
            State.VISUAL_SCRIPTING_REVIEW: "장면 설명이 완성됐어요!",
            State.ERROR: "문제가 생겼어요.",
        }

        status_msg = status_messages.get(current_state, f"처리 중: {current_state.name}")

        yield (
            status_msg,
            engine, "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            None,
            *default_scene_updates,
        )

        result_state, result_msg = await controller.run_until_checkpoint_or_complete()

        # CRITICAL: Stop at VISUAL_SCRIPTING_REVIEW - Show Scene Workspace
        if result_state == State.VISUAL_SCRIPTING_REVIEW:
            scene_plans_data = get_scene_plans_data(engine.project)
            plan_count = len(scene_plans_data)

            # Build scene row updates
            scene_updates = []
            for i in range(MAX_SCENES):
                if i < plan_count:
                    scene = scene_plans_data[i]
                    lyric_text = scene.get('lyric_text', '')
                    start_time = scene.get('start_time', 0.0)
                    end_time = scene.get('end_time', 0.0)
                    prompt = scene.get('visual_prompt', '')

                    # Format lyrics markdown
                    lyrics_md = f"### 씬 {i+1}\n**[{start_time:.1f}s - {end_time:.1f}s]**\n\n🎵 *\"{lyric_text}\"*"

                    scene_updates.extend([
                        gr.update(visible=True),   # scene_group visible
                        lyrics_md,                  # lyrics_md
                        prompt,                     # prompt_input
                        None,                       # image_output (no image yet)
                        None,                       # video_output (no video yet)
                    ])
                else:
                    scene_updates.extend([
                        gr.update(visible=False),  # scene_group hidden
                        "",                         # lyrics_md
                        "",                         # prompt_input
                        None,                       # image_output
                        None,                       # video_output
                    ])

            yield (
                f"장면 설명 {plan_count}개가 완성됐어요!\n\n아래 '감독 컨트롤 패널'에서 각 씬을 확인하세요.\n\n프롬프트를 수정하고, 씬별로 이미지와 영상을 생성할 수 있어요!",
                engine, "",
                gr.update(visible=False),  # Hide lyric review
                gr.update(visible=True),   # Show scene workspace
                gr.update(visible=False),  # Hide start button
                None,
                *scene_updates,
            )
            return  # Exit - show scene workspace for user control

        if result_state == State.ERROR:
            yield (
                f"장면 설명 생성 중 문제가 생겼어요.\n\n{result_msg}\n\n다시 시도해주세요.",
                engine, "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=True),
                None,
                *default_scene_updates,
            )
            await engine.cleanup_comfyui()
            return

        await asyncio.sleep(0.5)


# NOTE: Batch generation functions removed (Human-in-the-Loop Control System)
# Users must now generate each scene individually using the per-scene buttons.
# This ensures the 5070 Ti remains idle until explicitly requested.


async def finalize_video(engine_state, is_processing: bool, progress=gr.Progress()):
    """
    모든 씬 클립을 합쳐서 최종 영상 생성 (with resource lock)

    Returns:
        tuple: (status_message, video_path or None, is_processing flag)
    """
    # Resource lock check
    if is_processing:
        return "⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요.", None, True

    if engine_state is None:
        return "세션이 끊어졌습니다.", None, False

    engine = engine_state

    # Check if we have any video clips to finalize
    if not hasattr(engine.project, 'video_clips') or not engine.project.video_clips:
        return "🎬 먼저 영상 클립을 생성해주세요.", None, False

    try:
        # Trigger transition to POST_PROCESSING
        controller = GradioFSMController(engine)

        # Skip remaining checkpoints and go to post-processing
        await controller.trigger_checkpoint_transition("visuals_confirmed")

        # Run until completion
        while True:
            result_state, result_msg = await controller.run_until_checkpoint_or_complete()

            # Auto-confirm remaining checkpoints
            if result_state in (State.MOTION_REVIEW, State.POST_REVIEW):
                trigger_map = {
                    State.MOTION_REVIEW: "motion_confirmed",
                    State.POST_REVIEW: "post_confirmed",
                }
                await controller.trigger_checkpoint_transition(trigger_map[result_state])
                await asyncio.sleep(0.1)
                continue

            if result_state == State.COMPLETED:
                video_path = engine.project.final_video_path if engine.project else None
                await engine.cleanup_comfyui()
                return "✅ 뮤직비디오가 완성되었어요!", video_path, False

            if result_state == State.ERROR:
                await engine.cleanup_comfyui()
                return f"문제가 생겼어요: {result_msg}", None, False

            await asyncio.sleep(0.5)

    except Exception as e:
        logger.exception(f"Failed to finalize video: {e}")
        return f"에러: {str(e)}", None, False


# ============================================================================
# UI Layout - Director's Control Panel (감독 컨트롤 패널)
# ============================================================================

# 어르신 친화적 테마 (큰 글씨, 명확한 색상)
custom_css = """
.gradio-container { font-size: 18px !important; }
.gr-button { font-size: 18px !important; padding: 12px 24px !important; }
.gr-input, .gr-textbox textarea { font-size: 16px !important; }
h1 { font-size: 32px !important; }
h2 { font-size: 26px !important; }
h3 { font-size: 22px !important; }
label { font-size: 16px !important; font-weight: bold !important; }

/* Scene Row Styling */
.scene-row {
    border: 2px solid #e1e8ed;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
    background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
}
.scene-row:hover {
    border-color: #667eea;
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
}

/* Batch buttons */
.batch-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    font-weight: bold !important;
}
"""

with gr.Blocks(
    theme=gr.themes.Soft(),
    title="감독 컨트롤 패널 - 뮤직비디오 만들기",
    css=custom_css
) as demo:
    gr.Markdown("""
    # 🎬 감독 컨트롤 패널
    ### 씬 단위로 뮤직비디오를 만들어보세요
    **개별 제어**: 각 씬의 버튼을 눌러 이미지/영상을 생성합니다. (일괄 자동 생성 없음)
    """)

    # Hidden state to persist engine between interactions
    engine_state = gr.State(value=None)

    # Resource lock state: prevents concurrent GPU operations (5070 Ti idle until requested)
    is_processing = gr.State(value=False)

    # =========================================================================
    # Top Section: Input Controls
    # =========================================================================
    with gr.Row():
        # Left Column - Audio & Lyrics Input
        with gr.Column(scale=1):
            audio_input = gr.Audio(
                type="filepath",
                label="🎧 1단계: 노래 파일 올리기",
            )

            # Full Lyrics Input (NEW)
            full_lyrics_input = gr.Textbox(
                label="📜 전체 가사 원문 (선택사항)",
                lines=10,
                placeholder="여기에 전체 가사를 붙여넣으면 타임라인을 더 정확하게 잡아줍니다.\n\n예:\n사랑은 늘 도망가\n너를 만나 행복했어\n그 시절이 그리워\n...",
                info="가사를 직접 입력하면 자동 인식 대신 입력한 가사를 사용합니다."
            )

            with gr.Accordion("📝 노래 정보 입력 (선택사항)", open=False):
                artist_input = gr.Textbox(
                    label="가수 이름",
                    placeholder="예: 임영웅, 송가인"
                )
                title_input = gr.Textbox(
                    label="노래 제목",
                    placeholder="예: 사랑은 늘 도망가"
                )
                mood_input = gr.Textbox(
                    label="느낌/분위기",
                    placeholder="예: 따뜻한, 그리운, 봄날"
                )
                story_input = gr.Textbox(
                    label="원하는 장면 설명",
                    lines=3,
                    placeholder="예: 꽃이 피는 봄날, 푸른 바다, 노을지는 하늘..."
                )

            btn_start = gr.Button(
                "🎬 분석 시작!",
                variant="primary",
                size="lg"
            )

        # Right Column - Status
        with gr.Column(scale=1):
            status_output = gr.Textbox(
                label="📢 진행 상황",
                lines=8,
                interactive=False,
                value="왼쪽에서 노래 파일을 올리고\n'분석 시작' 버튼을 눌러주세요!\n\n전체 가사를 알고 있다면\n가사 입력란에 붙여넣으면\n더 정확한 결과를 얻을 수 있어요."
            )

            # Final Video Output
            video_output = gr.Video(label="🎉 완성된 뮤직비디오")

    # =========================================================================
    # Lyrics Review Section (shown after audio analysis)
    # =========================================================================
    with gr.Group(visible=False) as lyric_review_group:
        gr.Markdown("### ✏️ 가사 확인하기")
        gr.Markdown(
            "아래에 노래 가사가 나왔어요. 틀린 부분이 있으면 수정해주세요.\n"
            "다 확인했으면 **'가사 확인 완료'** 버튼을 눌러주세요!"
        )

        lyrics_input = gr.Textbox(
            label="🎤 가사",
            lines=15,
            max_lines=30,
            interactive=True,
        )

        btn_confirm_lyrics = gr.Button(
            "✅ 가사 확인 완료! 장면 생성하기",
            variant="primary",
            size="lg",
        )

    # =========================================================================
    # Scene Workspace - Director's Control Panel (Human-in-the-Loop)
    # =========================================================================
    with gr.Group(visible=False) as scene_workspace:
        gr.Markdown("## 🎬 씬별 작업대 (Scene-by-Scene Production)")
        gr.Markdown(
            "각 씬의 **[🎨 이미지 생성]** 버튼을 눌러 개별적으로 이미지를 생성하세요.\n"
            "이미지가 생성되면 **[🎬 영상 생성]** 버튼이 활성화됩니다.\n\n"
            "⚠️ **한 번에 하나의 작업만 가능합니다** (GPU 리소스 보호)"
        )

        # Processing indicator
        processing_indicator = gr.Markdown(
            "🟢 **대기 중** - 버튼을 눌러 작업을 시작하세요",
            elem_id="processing_indicator"
        )

        # Final assembly button (only after clips are ready)
        with gr.Row():
            btn_finalize = gr.Button(
                "🎉 최종 영상 합치기 (모든 클립 완성 후)",
                variant="primary",
                size="lg",
            )

        gr.Markdown("---")

        # Scene Rows - Pre-generate MAX_SCENES rows (controlled by visibility)
        scene_groups = []
        scene_lyrics_mds = []
        scene_prompt_inputs = []
        scene_image_outputs = []
        scene_video_outputs = []
        scene_gen_image_btns = []
        scene_gen_video_btns = []

        for i in range(MAX_SCENES):
            with gr.Group(visible=False, elem_classes="scene-row") as scene_group:
                with gr.Row():
                    # Left Column: Planning (기획)
                    with gr.Column(scale=1):
                        lyrics_md = gr.Markdown(
                            value=f"### 씬 {i+1}\n*가사가 여기에 표시됩니다*",
                        )
                        prompt_input = gr.Textbox(
                            label="이미지 프롬프트",
                            lines=4,
                            interactive=True,
                            placeholder="LLM이 생성한 프롬프트가 여기에 표시됩니다.\n직접 수정할 수 있어요.",
                        )
                        btn_gen_image = gr.Button(
                            f"🎨 씬 {i+1} 이미지 생성",
                            variant="primary",
                            size="sm",
                        )

                    # Center Column: Visualization (시각화)
                    with gr.Column(scale=1):
                        image_output = gr.Image(
                            label="생성된 이미지",
                            interactive=False,
                            height=256,
                        )
                        btn_gen_video = gr.Button(
                            f"🎬 씬 {i+1} 영상 생성",
                            variant="primary",
                            size="sm",
                        )

                    # Right Column: Motion (영상화)
                    with gr.Column(scale=1):
                        video_output = gr.Video(
                            label="완성된 클립",
                            height=256,
                        )

                scene_groups.append(scene_group)
                scene_lyrics_mds.append(lyrics_md)
                scene_prompt_inputs.append(prompt_input)
                scene_image_outputs.append(image_output)
                scene_video_outputs.append(video_output)
                scene_gen_image_btns.append(btn_gen_image)
                scene_gen_video_btns.append(btn_gen_video)

    # =========================================================================
    # Event Handlers (Human-in-the-Loop Control System)
    # =========================================================================

    # Build output list for start_processing
    start_outputs = [
        status_output,
        engine_state,
        lyrics_input,
        lyric_review_group,
        scene_workspace,
        btn_start,
        video_output,
    ]
    # Add scene row components
    for i in range(MAX_SCENES):
        start_outputs.extend([
            scene_groups[i],
            scene_lyrics_mds[i],
            scene_prompt_inputs[i],
            scene_image_outputs[i],
            scene_video_outputs[i],
        ])

    # Start button -> run until AUDIO_REVIEW
    btn_start.click(
        fn=start_processing,
        inputs=[audio_input, full_lyrics_input, artist_input, title_input, mood_input, story_input],
        outputs=start_outputs,
    )

    # Confirm lyrics button -> run until VISUAL_SCRIPTING_REVIEW, then show scene workspace
    btn_confirm_lyrics.click(
        fn=confirm_lyrics_and_continue,
        inputs=[engine_state, lyrics_input],
        outputs=start_outputs,
    )

    # =========================================================================
    # On-Demand Scene Generation (Individual Buttons with Resource Lock)
    # =========================================================================

    # Helper to update processing indicator
    def get_processing_indicator(is_busy: bool) -> str:
        if is_busy:
            return "🔴 **작업 중** - GPU가 이미지/영상을 생성하고 있습니다. 잠시 기다려주세요..."
        else:
            return "🟢 **대기 중** - 버튼을 눌러 작업을 시작하세요"

    # Individual scene IMAGE generation buttons (with resource lock)
    for i in range(MAX_SCENES):
        def make_image_handler(idx):
            async def handler(engine_state, proc_state, prompt):
                """
                On-demand image generation for a single scene.
                Implements resource lock to prevent concurrent GPU operations.
                """
                # Set processing state to True (lock GPU)
                if proc_state:
                    return (
                        "⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요.",
                        None,
                        True,
                        get_processing_indicator(True)
                    )

                # Generate image (with lock)
                status, image_path, new_proc_state = await generate_single_scene_image(
                    engine_state, False, idx, prompt
                )

                return (
                    status,
                    image_path,
                    new_proc_state,
                    get_processing_indicator(new_proc_state)
                )
            return handler

        scene_gen_image_btns[i].click(
            fn=make_image_handler(i),
            inputs=[engine_state, is_processing, scene_prompt_inputs[i]],
            outputs=[status_output, scene_image_outputs[i], is_processing, processing_indicator],
        )

    # Individual scene VIDEO generation buttons (with resource lock)
    # NOTE: Video button should only work when image exists
    for i in range(MAX_SCENES):
        def make_video_handler(idx):
            async def handler(engine_state, proc_state, image):
                """
                On-demand video generation for a single scene.
                Requires image to exist first.
                """
                # Resource lock check
                if proc_state:
                    return (
                        "⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요.",
                        None,
                        True,
                        get_processing_indicator(True)
                    )

                # Check if image exists
                if image is None:
                    return (
                        "🖼️ 이미지가 없습니다. 먼저 [🎨 이미지 생성] 버튼을 눌러주세요.",
                        None,
                        False,
                        get_processing_indicator(False)
                    )

                # Get image path
                image_path = image if isinstance(image, str) else getattr(image, 'name', None)

                # Generate video (with lock)
                status, video_path, new_proc_state = await generate_single_scene_video(
                    engine_state, False, idx, image_path
                )

                return (
                    status,
                    video_path,
                    new_proc_state,
                    get_processing_indicator(new_proc_state)
                )
            return handler

        scene_gen_video_btns[i].click(
            fn=make_video_handler(i),
            inputs=[engine_state, is_processing, scene_image_outputs[i]],
            outputs=[status_output, scene_video_outputs[i], is_processing, processing_indicator],
        )

    # =========================================================================
    # Final Video Assembly (with resource lock)
    # =========================================================================

    async def finalize_with_lock(engine_state, proc_state):
        """Wrapper for finalize_video with resource lock and indicator update."""
        if proc_state:
            return (
                "⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요.",
                None,
                True,
                get_processing_indicator(True)
            )

        status, video_path, new_proc_state = await finalize_video(engine_state, False)
        return (
            status,
            video_path,
            new_proc_state,
            get_processing_indicator(new_proc_state)
        )

    btn_finalize.click(
        fn=finalize_with_lock,
        inputs=[engine_state, is_processing],
        outputs=[status_output, video_output, is_processing, processing_indicator],
    )


if __name__ == "__main__":
    # share=True로 외부 접속 가능한 링크 생성
    demo.queue().launch(
        inbrowser=True,
        share=True,  # 외부 링크 생성 (*.gradio.live)
        server_name="0.0.0.0",  # 로컬 네트워크에서도 접속 가능
    )
