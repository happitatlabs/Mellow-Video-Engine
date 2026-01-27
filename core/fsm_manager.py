"""
Finite State Machine Manager
============================
Core orchestration logic for the Mellow-Video-Engine workflow.
Implements State Pattern with human-in-the-loop checkpoints.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .project_state import ProjectState
    from .model_manager import ModelManager

logger = logging.getLogger(__name__)


class State(Enum):
    """Workflow states for the music video generation pipeline."""
    INIT = auto()
    AUDIO_ANALYSIS = auto()
    AUDIO_REVIEW = auto()           # Human checkpoint
    # Split VISUAL_PLANNING into two separate states for human-in-the-loop
    VISUAL_SCRIPTING = auto()       # LLM generates scene prompts (JSON)
    VISUAL_SCRIPTING_REVIEW = auto() # Human checkpoint - review/edit prompts
    VISUAL_RENDERING = auto()       # ComfyUI generates images
    VISUAL_REVIEW = auto()          # Human checkpoint - review images
    MOTION_SYNTHESIS = auto()
    MOTION_REVIEW = auto()          # Human checkpoint
    POST_PROCESSING = auto()
    POST_REVIEW = auto()            # Human checkpoint
    LOCALIZATION = auto()
    DEPLOYMENT_READY = auto()
    COMPLETED = auto()
    ERROR = auto()
    # Legacy alias for backwards compatibility
    VISUAL_PLANNING = VISUAL_SCRIPTING


@dataclass
class StateTransition:
    """Represents a valid state transition."""
    from_state: State
    to_state: State
    trigger: str
    guard: Optional[Callable[[ProjectState], bool]] = None
    action: Optional[Callable[[ProjectState], None]] = None


class StateHandler(ABC):
    """Abstract base class for state handlers."""

    def __init__(self, fsm: FSMManager):
        self.fsm = fsm
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def enter(self, project: ProjectState) -> None:
        """Called when entering this state."""
        pass

    @abstractmethod
    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        """
        Execute the main logic for this state.
        Returns: (success: bool, next_trigger: str)
        """
        pass

    @abstractmethod
    async def exit(self, project: ProjectState) -> None:
        """Called when exiting this state."""
        pass

    def requires_user_input(self) -> bool:
        """Override to True for states requiring human review."""
        return False


class FSMManager:
    """
    Finite State Machine Manager for orchestrating the video generation workflow.

    Features:
    - State Pattern implementation
    - Human-in-the-loop checkpoints
    - Async execution support
    - State persistence and recovery
    - Model memory management integration
    """

    # Valid state transitions
    TRANSITIONS: list[StateTransition] = [
        StateTransition(State.INIT, State.AUDIO_ANALYSIS, "start"),
        StateTransition(State.AUDIO_ANALYSIS, State.AUDIO_REVIEW, "analysis_complete"),
        StateTransition(State.AUDIO_ANALYSIS, State.ERROR, "analysis_failed"),
        StateTransition(State.AUDIO_REVIEW, State.VISUAL_SCRIPTING, "lyrics_confirmed"),
        StateTransition(State.AUDIO_REVIEW, State.AUDIO_ANALYSIS, "reanalyze"),
        # VISUAL_SCRIPTING: LLM generates scene prompts
        StateTransition(State.VISUAL_SCRIPTING, State.VISUAL_SCRIPTING_REVIEW, "scripting_complete"),
        StateTransition(State.VISUAL_SCRIPTING, State.ERROR, "scripting_failed"),
        # VISUAL_SCRIPTING_REVIEW: Human reviews/edits prompts before image generation
        StateTransition(State.VISUAL_SCRIPTING_REVIEW, State.VISUAL_RENDERING, "prompts_confirmed"),
        StateTransition(State.VISUAL_SCRIPTING_REVIEW, State.VISUAL_SCRIPTING, "regenerate_prompts"),
        # VISUAL_RENDERING: ComfyUI generates images from confirmed prompts
        StateTransition(State.VISUAL_RENDERING, State.VISUAL_REVIEW, "rendering_complete"),
        StateTransition(State.VISUAL_RENDERING, State.ERROR, "rendering_failed"),
        # VISUAL_REVIEW: Human reviews generated images
        StateTransition(State.VISUAL_REVIEW, State.MOTION_SYNTHESIS, "visuals_confirmed"),
        StateTransition(State.VISUAL_REVIEW, State.VISUAL_RENDERING, "regenerate_images"),
        StateTransition(State.VISUAL_REVIEW, State.VISUAL_SCRIPTING, "regenerate_prompts"),
        StateTransition(State.MOTION_SYNTHESIS, State.MOTION_REVIEW, "synthesis_complete"),
        StateTransition(State.MOTION_SYNTHESIS, State.ERROR, "synthesis_failed"),
        StateTransition(State.MOTION_REVIEW, State.POST_PROCESSING, "motion_confirmed"),
        StateTransition(State.MOTION_REVIEW, State.MOTION_SYNTHESIS, "regenerate_motion"),
        StateTransition(State.POST_PROCESSING, State.POST_REVIEW, "processing_complete"),
        StateTransition(State.POST_PROCESSING, State.ERROR, "processing_failed"),
        StateTransition(State.POST_REVIEW, State.LOCALIZATION, "post_confirmed"),
        StateTransition(State.POST_REVIEW, State.POST_PROCESSING, "redo_post"),
        StateTransition(State.LOCALIZATION, State.DEPLOYMENT_READY, "localization_complete"),
        StateTransition(State.LOCALIZATION, State.ERROR, "localization_failed"),
        StateTransition(State.DEPLOYMENT_READY, State.COMPLETED, "deployed"),
        StateTransition(State.DEPLOYMENT_READY, State.COMPLETED, "skip_deploy"),
        # Recovery transitions
        StateTransition(State.ERROR, State.INIT, "reset"),
    ]

    def __init__(
        self,
        model_manager: Optional[ModelManager] = None,
        auto_save: bool = True,
        save_callback: Optional[Callable[[ProjectState], None]] = None,
    ):
        self.model_manager = model_manager
        self.auto_save = auto_save
        self.save_callback = save_callback

        self._current_state = State.INIT
        self._handlers: dict[State, StateHandler] = {}
        self._user_input_event = asyncio.Event()
        self._user_input_data: dict = {}
        self._is_running = False
        self._pause_requested = False

        # Build transition lookup
        self._transitions: dict[tuple[State, str], StateTransition] = {
            (t.from_state, t.trigger): t for t in self.TRANSITIONS
        }

        logger.info("FSM Manager initialized")

    @property
    def current_state(self) -> State:
        """Get current state."""
        return self._current_state

    @property
    def is_at_checkpoint(self) -> bool:
        """Check if current state is a human checkpoint."""
        return self._current_state.name.endswith("_REVIEW")

    def register_handler(self, state: State, handler: StateHandler) -> None:
        """Register a handler for a specific state."""
        self._handlers[state] = handler
        logger.debug(f"Registered handler for state: {state.name}")

    def get_valid_triggers(self) -> list[str]:
        """Get list of valid triggers from current state."""
        return [
            t.trigger for t in self.TRANSITIONS
            if t.from_state == self._current_state
        ]

    def can_transition(self, trigger: str, project: ProjectState) -> bool:
        """Check if a transition is valid from current state."""
        key = (self._current_state, trigger)
        if key not in self._transitions:
            return False

        transition = self._transitions[key]
        if transition.guard and not transition.guard(project):
            return False

        return True

    async def trigger_transition(
        self,
        trigger: str,
        project: ProjectState,
    ) -> bool:
        """
        Attempt to transition to a new state.

        Args:
            trigger: The transition trigger name
            project: Current project state

        Returns:
            True if transition succeeded, False otherwise
        """
        key = (self._current_state, trigger)

        if key not in self._transitions:
            logger.warning(
                f"Invalid trigger '{trigger}' for state {self._current_state.name}"
            )
            return False

        transition = self._transitions[key]

        # Check guard condition
        if transition.guard and not transition.guard(project):
            logger.warning(
                f"Guard condition failed for transition {transition}"
            )
            return False

        # Exit current state
        if self._current_state in self._handlers:
            await self._handlers[self._current_state].exit(project)

        # Unload models if needed (VRAM management)
        if self.model_manager:
            await self.model_manager.unload_all()

        # Execute transition action
        if transition.action:
            transition.action(project)

        # Record transition
        old_state = self._current_state
        self._current_state = transition.to_state
        project.record_state_transition(
            old_state.name,
            self._current_state.name,
            {"trigger": trigger},
        )

        logger.info(f"State transition: {old_state.name} -> {self._current_state.name}")

        # Enter new state
        if self._current_state in self._handlers:
            await self._handlers[self._current_state].enter(project)

        # Auto-save if enabled
        if self.auto_save and self.save_callback:
            self.save_callback(project)

        return True

    async def run(self, project: ProjectState) -> None:
        """
        Run the FSM until completion or a checkpoint requiring user input.

        Args:
            project: The project state to process
        """
        self._is_running = True
        self._pause_requested = False

        logger.info(f"FSM starting from state: {self._current_state.name}")

        try:
            while self._is_running:
                # Check for pause request
                if self._pause_requested:
                    logger.info("FSM paused by request")
                    break

                # Check if we're at a terminal state
                if self._current_state in (State.COMPLETED, State.ERROR):
                    logger.info(f"FSM reached terminal state: {self._current_state.name}")
                    break

                # Check if handler requires user input
                handler = self._handlers.get(self._current_state)
                if handler and handler.requires_user_input():
                    logger.info(f"Waiting for user input at {self._current_state.name}")
                    await self._wait_for_user_input()

                    # Process user input
                    if "trigger" in self._user_input_data:
                        trigger = self._user_input_data["trigger"]
                        self._user_input_data = {}
                        await self.trigger_transition(trigger, project)
                    continue

                # Execute current state handler
                if handler:
                    success, next_trigger = await handler.execute(project)

                    if success and next_trigger:
                        await self.trigger_transition(next_trigger, project)
                    elif not success:
                        # Transition to error state
                        error_trigger = f"{self._current_state.name.lower()}_failed"
                        if self.can_transition(error_trigger, project):
                            await self.trigger_transition(error_trigger, project)
                        else:
                            # Generic error transition
                            self._current_state = State.ERROR
                            logger.error(f"No error transition defined, forced to ERROR state")
                else:
                    logger.warning(f"No handler for state {self._current_state.name}")
                    break

        except Exception as e:
            logger.exception(f"FSM execution error: {e}")
            self._current_state = State.ERROR
            project.record_state_transition(
                self._current_state.name,
                State.ERROR.name,
                {"error": str(e)},
            )
        finally:
            self._is_running = False

    async def _wait_for_user_input(self) -> None:
        """Wait for user input at checkpoint."""
        self._user_input_event.clear()
        await self._user_input_event.wait()

    def provide_user_input(self, trigger: str, data: dict = None) -> None:
        """
        Provide user input to continue from checkpoint.

        Args:
            trigger: The trigger to fire (e.g., 'lyrics_confirmed', 'regenerate_visuals')
            data: Optional additional data from user
        """
        self._user_input_data = {"trigger": trigger, **(data or {})}
        self._user_input_event.set()
        logger.info(f"User input received: trigger={trigger}")

    def pause(self) -> None:
        """Request FSM to pause after current state."""
        self._pause_requested = True
        logger.info("Pause requested")

    def resume(self) -> None:
        """Resume paused FSM."""
        self._pause_requested = False
        logger.info("Resume requested")

    def reset(self, project: ProjectState) -> None:
        """Reset FSM to initial state."""
        self._current_state = State.INIT
        self._is_running = False
        self._pause_requested = False
        self._user_input_data = {}
        project.record_state_transition("RESET", State.INIT.name, {})
        logger.info("FSM reset to INIT state")

    def get_state_info(self) -> dict:
        """Get current FSM state information."""
        return {
            "current_state": self._current_state.name,
            "is_running": self._is_running,
            "is_paused": self._pause_requested,
            "is_at_checkpoint": self.is_at_checkpoint,
            "valid_triggers": self.get_valid_triggers(),
        }


# ============================================================================
# State Handler Implementations (Skeletons)
# ============================================================================

class InitStateHandler(StateHandler):
    """Handler for INIT state."""

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("Entering INIT state")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        # Validate project has audio file
        if project.audio_file_path and project.audio_file_path.exists():
            return True, "start"
        else:
            self.logger.error("No audio file specified")
            return False, ""

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Exiting INIT state")


class AudioReviewHandler(StateHandler):
    """Handler for AUDIO_REVIEW checkpoint."""

    def requires_user_input(self) -> bool:
        return True

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("Entering AUDIO_REVIEW - waiting for user confirmation")
        # Export lyrics for editing
        lyrics_json = project.export_lyrics_for_editing()
        self.logger.debug(f"Exported {len(project.lyrics_segments)} segments for review")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        # This will be triggered after user provides input
        return True, ""  # Trigger provided by user

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Exiting AUDIO_REVIEW")


class VisualScriptingReviewHandler(StateHandler):
    """Handler for VISUAL_SCRIPTING_REVIEW checkpoint.

    This is where the user reviews and edits LLM-generated scene prompts
    BEFORE triggering ComfyUI image generation.
    """

    def requires_user_input(self) -> bool:
        return True

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("Entering VISUAL_SCRIPTING_REVIEW - waiting for prompt confirmation")
        
        # Scene plans should already be stored in project.scene_plans
        scene_plans = None
        
        # 1. project.scene_plans 확인
        if hasattr(project, 'scene_plans') and project.scene_plans:
            scene_plans = project.scene_plans
            self.logger.info(f"Found {len(scene_plans)} scene plans in project.scene_plans")
        
        # 2. project.visual_plans 확인 (대안)
        elif hasattr(project, 'visual_plans') and project.visual_plans:
            scene_plans = project.visual_plans
            self.logger.info(f"Found {len(scene_plans)} scene plans in project.visual_plans")
        
        # 3. JSON 파일에서 로드 시도 (폴백)
        if not scene_plans:
            import json
            from pathlib import Path
            
            # 프로젝트 출력 디렉토리에서 scene_plans.json 찾기
            possible_paths = [
                Path(project.audio_file_path).parent / "scene_plans.json" if project.audio_file_path else None,
                Path(f"assets/{project.project_id}/scene_plans.json") if hasattr(project, 'project_id') else None,
            ]
            
            for json_path in possible_paths:
                if json_path and json_path.exists():
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            scene_plans = json.load(f)
                        # 프로젝트에도 저장
                        project.scene_plans = scene_plans
                        self.logger.info(f"Loaded {len(scene_plans)} scene plans from {json_path}")
                        break
                    except Exception as e:
                        self.logger.warning(f"Failed to load scene plans from {json_path}: {e}")
        
        if scene_plans:
            self.logger.info(f"Ready to review {len(scene_plans)} scene prompts")
        else:
            self.logger.warning(
                "No scene plans found in project for review. "
                "This may indicate that VisualScriptingHandler did not save the plans correctly."
            )

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        # User will trigger "prompts_confirmed" or "regenerate_prompts"
        return True, ""

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Exiting VISUAL_SCRIPTING_REVIEW - prompts confirmed")


class VisualReviewHandler(StateHandler):
    """Handler for VISUAL_REVIEW checkpoint.

    This is where the user reviews generated images AFTER ComfyUI rendering.
    """

    def requires_user_input(self) -> bool:
        return True

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("Entering VISUAL_REVIEW - waiting for image confirmation")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        return True, ""

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Exiting VISUAL_REVIEW")


class MotionReviewHandler(StateHandler):
    """Handler for MOTION_REVIEW checkpoint."""

    def requires_user_input(self) -> bool:
        return True

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("Entering MOTION_REVIEW - waiting for user confirmation")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        return True, ""

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Exiting MOTION_REVIEW")


class PostReviewHandler(StateHandler):
    """Handler for POST_REVIEW checkpoint."""

    def requires_user_input(self) -> bool:
        return True

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("Entering POST_REVIEW - waiting for user confirmation")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        return True, ""

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Exiting POST_REVIEW")


class ErrorStateHandler(StateHandler):
    """Handler for ERROR state."""

    async def enter(self, project: ProjectState) -> None:
        self.logger.error("Entered ERROR state")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        # Wait for user to reset
        return True, ""

    async def exit(self, project: ProjectState) -> None:
        self.logger.info("Recovering from ERROR state")


class CompletedStateHandler(StateHandler):
    """Handler for COMPLETED state."""

    async def enter(self, project: ProjectState) -> None:
        self.logger.info("=== PIPELINE COMPLETED SUCCESSFULLY ===")
        if project.final_video_path:
            self.logger.info(f"Final video: {project.final_video_path}")

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        return True, ""

    async def exit(self, project: ProjectState) -> None:
        pass
