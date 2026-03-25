"""
State Definitions for Mellow-Link FSM

This module defines all possible states and priorities for the orchestration system.
The FSM (Finite State Machine) uses these states to manage GPU resource allocation
and task scheduling between LLM and Image generation workloads.

Extracted from legacy:
    - state_machine.py: ChatState enum
    - chat_api.py: Mode definitions
"""

from enum import Enum, auto
from typing import Dict, Set


class SystemState(Enum):
    """
    Finite State Machine states for the orchestrator.

    State Transitions:
        IDLE -> TEXT: When LLM task is requested
        IDLE -> IMAGE: When image generation task is requested
        TEXT -> IDLE: When LLM task completes
        TEXT -> IMAGE: When LLM completes and image task is queued (with cooldown)
        IMAGE -> IDLE: When image generation completes
        IMAGE -> TEXT: When image completes and LLM task is queued (with cooldown)
        ANY -> ERROR: When critical failure occurs
        ERROR -> IDLE: After error recovery

    Resource Allocation:
        IDLE: Minimal GPU usage, ready for any task
        TEXT: Full VRAM allocated to LLM (Ollama)
        IMAGE: Full VRAM allocated to Image Gen (ComfyUI)
        ERROR: Emergency state, all GPU tasks suspended
    """

    IDLE = auto()    # System idle, waiting for tasks
    TEXT = auto()    # LLM processing active (Ollama)
    IMAGE = auto()   # Image generation active (ComfyUI)
    ERROR = auto()   # Error state, requires recovery

    def is_gpu_active(self) -> bool:
        """Check if this state uses GPU resources."""
        return self in (SystemState.TEXT, SystemState.IMAGE)

    def can_accept_task(self) -> bool:
        """Check if system can accept new tasks in this state."""
        return self in (SystemState.IDLE,)

    @classmethod
    def from_string(cls, value: str) -> 'SystemState':
        """Create SystemState from string value."""
        mapping = {
            'idle': cls.IDLE,
            'text': cls.TEXT,
            'image': cls.IMAGE,
            'error': cls.ERROR,
        }
        return mapping.get(value.lower(), cls.IDLE)


class TaskPriority(Enum):
    """
    Task priority levels for the scheduling queue.

    Priority Rules:
        - CRITICAL: Interrupt current task if possible (emergency only)
        - HIGH: Execute immediately after current task
        - NORMAL: Standard FIFO queue processing
        - LOW: Background tasks, execute when system is idle

    Lower numeric value = higher priority (for PriorityQueue compatibility)
    """

    CRITICAL = 0   # Highest priority - system critical tasks
    HIGH = 1       # High priority - user-initiated urgent tasks
    NORMAL = 2     # Normal priority - standard requests
    LOW = 3        # Low priority - background/batch processing

    def __lt__(self, other: 'TaskPriority') -> bool:
        """Enable comparison for PriorityQueue sorting."""
        if isinstance(other, TaskPriority):
            return self.value < other.value
        return NotImplemented

    def __le__(self, other: 'TaskPriority') -> bool:
        """Enable comparison for PriorityQueue sorting."""
        if isinstance(other, TaskPriority):
            return self.value <= other.value
        return NotImplemented

    @classmethod
    def from_string(cls, value: str) -> 'TaskPriority':
        """Create TaskPriority from string value."""
        mapping = {
            'critical': cls.CRITICAL,
            'high': cls.HIGH,
            'normal': cls.NORMAL,
            'low': cls.LOW,
        }
        return mapping.get(value.lower(), cls.NORMAL)


class TransitionResult(Enum):
    """
    Result codes for state transitions.

    Used by Orchestrator to report transition outcomes to callers.
    """

    SUCCESS = auto()            # Transition completed successfully
    BLOCKED = auto()            # Transition blocked (resource busy)
    INVALID_TRANSITION = auto() # Requested transition not allowed
    COOLDOWN_ACTIVE = auto()    # Must wait before transition (GPU cooldown)
    ERROR = auto()              # Transition failed due to error

    def is_success(self) -> bool:
        """Check if transition was successful."""
        return self == TransitionResult.SUCCESS

    def is_retriable(self) -> bool:
        """Check if transition can be retried."""
        return self in (TransitionResult.BLOCKED, TransitionResult.COOLDOWN_ACTIVE)


class ProcessingMode(Enum):
    """
    LLM processing modes.

    Ported from legacy chat_api.py mode handling.

    Modes:
        FAST: Quick responses using lightweight model (Qwen 3B)
        THINKING: Deep reasoning using main model (Qwen 14B)
        RESEARCH: Web search + main model for factual queries
        AUTO: Automatic selection based on query analysis
    """

    FAST = "fast"
    THINKING = "thinking"
    RESEARCH = "research"
    AUTO = "auto"

    def uses_main_model(self) -> bool:
        """Check if this mode uses the main (larger) model."""
        return self in (ProcessingMode.THINKING, ProcessingMode.RESEARCH)

    def uses_web_search(self) -> bool:
        """Check if this mode performs web search."""
        return self == ProcessingMode.RESEARCH

    @classmethod
    def from_string(cls, value: str) -> 'ProcessingMode':
        """Create ProcessingMode from string value."""
        mapping = {
            'fast': cls.FAST,
            'thinking': cls.THINKING,
            'research': cls.RESEARCH,
            'auto': cls.AUTO,
        }
        # 기본값을 FAST로 변경: 일반적인 경우 빠른 모델 사용
        return mapping.get(value.lower(), cls.FAST)


class ChatState(Enum):
    """
    Chat processing pipeline states.

    Ported from legacy state_machine.py ChatState.
    Represents stages in the chat processing pipeline.

    Pipeline Flow:
        IDLE -> ANALYZING -> RETRIEVING (optional) -> GENERATING -> GENERATING_RESPONSE -> COMPLETED
                    |                                      |              |
                    +--------------------------------------+--------------+-> ERROR
        
    ✅ verified: GENERATING_RESPONSE 상태 추가 - 작업 완료 후 결과 보고 단계 명확화
    """

    IDLE = "idle"                      # Waiting for input
    ANALYZING = "analyzing"            # Analyzing query, determining RAG/mode
    RETRIEVING = "retrieving"          # Fetching documents from RAG
    GENERATING = "generating"          # LLM is generating response (ReAct loop)
    GENERATING_RESPONSE = "generating_response"  # Generating final response report after task completion
    COMPLETED = "completed"            # Response generation finished
    ERROR = "error"                    # Error occurred during processing

    def is_processing(self) -> bool:
        """Check if currently processing a request."""
        return self in (ChatState.ANALYZING, ChatState.RETRIEVING, ChatState.GENERATING, ChatState.GENERATING_RESPONSE)

    def is_terminal(self) -> bool:
        """Check if this is a terminal state."""
        return self in (ChatState.COMPLETED, ChatState.ERROR)


class ResourceType(Enum):
    """
    Types of resources managed by the orchestrator.

    Used for resource tracking and allocation.
    """

    GPU_VRAM = auto()    # GPU video memory
    GPU_COMPUTE = auto() # GPU compute units
    CPU = auto()         # CPU resources
    RAM = auto()         # System memory
    DISK = auto()        # Disk I/O

    def is_gpu_resource(self) -> bool:
        """Check if this is a GPU resource."""
        return self in (ResourceType.GPU_VRAM, ResourceType.GPU_COMPUTE)


class TaskType(Enum):
    """
    Types of tasks the orchestrator can handle.

    Maps to services and required system states.
    """

    LLM_CHAT = "llm_chat"           # Standard chat completion
    LLM_STREAM = "llm_stream"       # Streaming chat completion
    IMAGE_GENERATE = "image_gen"    # Image generation (ComfyUI)
    IMAGE_UPSCALE = "image_upscale" # Image upscaling
    DOCUMENT_PROCESS = "doc_proc"   # Document processing (CPU)
    RAG_SEARCH = "rag_search"       # RAG vector search
    WEB_SEARCH = "web_search"       # Web search

    def required_state(self) -> SystemState:
        """Get the required system state for this task type."""
        mapping = {
            TaskType.LLM_CHAT: SystemState.TEXT,
            TaskType.LLM_STREAM: SystemState.TEXT,
            TaskType.IMAGE_GENERATE: SystemState.IMAGE,
            TaskType.IMAGE_UPSCALE: SystemState.IMAGE,
            TaskType.DOCUMENT_PROCESS: SystemState.IDLE,
            TaskType.RAG_SEARCH: SystemState.IDLE,
            TaskType.WEB_SEARCH: SystemState.IDLE,
        }
        return mapping.get(self, SystemState.IDLE)

    def is_gpu_task(self) -> bool:
        """Check if this task requires GPU."""
        return self.required_state().is_gpu_active()

    def default_priority(self) -> TaskPriority:
        """Get default priority for this task type."""
        mapping = {
            TaskType.LLM_CHAT: TaskPriority.NORMAL,
            TaskType.LLM_STREAM: TaskPriority.NORMAL,
            TaskType.IMAGE_GENERATE: TaskPriority.NORMAL,
            TaskType.IMAGE_UPSCALE: TaskPriority.LOW,
            TaskType.DOCUMENT_PROCESS: TaskPriority.LOW,
            TaskType.RAG_SEARCH: TaskPriority.HIGH,
            TaskType.WEB_SEARCH: TaskPriority.NORMAL,
        }
        return mapping.get(self, TaskPriority.NORMAL)


# =============================================================================
# State Transition Matrix
# =============================================================================

# Valid state transitions: from_state -> set of valid to_states
STATE_TRANSITIONS: Dict[SystemState, Set[SystemState]] = {
    SystemState.IDLE: {SystemState.TEXT, SystemState.IMAGE, SystemState.ERROR},
    SystemState.TEXT: {SystemState.IDLE, SystemState.IMAGE, SystemState.ERROR},
    SystemState.IMAGE: {SystemState.IDLE, SystemState.TEXT, SystemState.ERROR},
    SystemState.ERROR: {SystemState.IDLE},
}


def is_valid_transition(from_state: SystemState, to_state: SystemState) -> bool:
    """
    Check if a state transition is valid.

    Args:
        from_state: Current state
        to_state: Target state

    Returns:
        True if transition is allowed
    """
    if from_state == to_state:
        return True  # No-op transitions are valid

    valid_targets = STATE_TRANSITIONS.get(from_state, set())
    return to_state in valid_targets


# =============================================================================
# Chat State Transitions
# =============================================================================

CHAT_STATE_TRANSITIONS: Dict[ChatState, Set[ChatState]] = {
    ChatState.IDLE: {ChatState.ANALYZING, ChatState.ERROR},
    ChatState.ANALYZING: {ChatState.RETRIEVING, ChatState.GENERATING, ChatState.ERROR},
    ChatState.RETRIEVING: {ChatState.GENERATING, ChatState.ERROR},
    ChatState.GENERATING: {ChatState.GENERATING_RESPONSE, ChatState.ERROR},
    ChatState.GENERATING_RESPONSE: {ChatState.COMPLETED, ChatState.ERROR},
    ChatState.COMPLETED: {ChatState.IDLE},
    ChatState.ERROR: {ChatState.IDLE},
}


def is_valid_chat_transition(from_state: ChatState, to_state: ChatState) -> bool:
    """
    Check if a chat state transition is valid.

    Args:
        from_state: Current chat state
        to_state: Target chat state

    Returns:
        True if transition is allowed
    """
    if from_state == to_state:
        return True

    valid_targets = CHAT_STATE_TRANSITIONS.get(from_state, set())
    return to_state in valid_targets
