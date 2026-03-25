"""
Event Definitions for Internal Messaging

This module defines data classes for the event-driven architecture.
Events are used for communication between components through asyncio queues.

Design Pattern:
    - Immutable event objects (dataclasses with frozen=False for flexibility)
    - Type-safe event payloads
    - Unique event IDs for tracking
    - Timestamp for ordering and debugging
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Dict, List
from enum import Enum, auto
import uuid
import json

from .states import TaskPriority, SystemState, ChatState


class EventType(Enum):
    """
    Types of events that can flow through the system.

    Categories:
        - Task events: Task lifecycle (submit, start, complete, fail, cancel)
        - State events: FSM state transitions
        - Resource events: GPU/VRAM monitoring alerts
        - System events: Lifecycle and health
    """

    # Task events
    TASK_SUBMIT = auto()      # New task submitted to queue
    TASK_START = auto()       # Task execution started
    TASK_PROGRESS = auto()    # Task progress update (for long tasks)
    TASK_COMPLETE = auto()    # Task completed successfully
    TASK_FAILED = auto()      # Task failed with error
    TASK_CANCEL = auto()      # Task cancellation requested
    TASK_TIMEOUT = auto()     # Task exceeded time limit

    # State events
    STATE_CHANGE = auto()     # FSM state transition occurred
    STATE_REQUEST = auto()    # State change requested

    # Resource events
    VRAM_WARNING = auto()     # VRAM threshold exceeded (warning level)
    VRAM_CRITICAL = auto()    # VRAM critically low (emergency)
    VRAM_NORMAL = auto()      # VRAM returned to normal levels
    RESOURCE_FREED = auto()   # GPU resources released

    # System events
    STARTUP = auto()          # System starting up
    SHUTDOWN = auto()         # System shutdown requested
    HEALTH_CHECK = auto()     # Health check ping
    ERROR = auto()            # General error event

    # Chat events
    CHAT_START = auto()       # Chat processing started
    CHAT_STREAM = auto()      # Chat streaming chunk
    CHAT_COMPLETE = auto()    # Chat processing completed

    def is_task_event(self) -> bool:
        """Check if this is a task-related event."""
        return self in (
            EventType.TASK_SUBMIT, EventType.TASK_START, EventType.TASK_PROGRESS,
            EventType.TASK_COMPLETE, EventType.TASK_FAILED, EventType.TASK_CANCEL,
            EventType.TASK_TIMEOUT
        )

    def is_resource_event(self) -> bool:
        """Check if this is a resource-related event."""
        return self in (
            EventType.VRAM_WARNING, EventType.VRAM_CRITICAL,
            EventType.VRAM_NORMAL, EventType.RESOURCE_FREED
        )

    def is_critical(self) -> bool:
        """Check if this event requires immediate attention."""
        return self in (
            EventType.VRAM_CRITICAL, EventType.TASK_FAILED,
            EventType.ERROR, EventType.SHUTDOWN
        )


@dataclass
class Event:
    """
    Base event class for all internal messages.

    Attributes:
        event_type: Type of the event
        payload: Optional data associated with the event
        source: Component that generated the event
        event_id: Unique identifier for this event
        timestamp: When the event was created
    """

    event_type: EventType
    payload: Optional[Dict[str, Any]] = None
    source: str = "unknown"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert event to dictionary for serialization.

        Returns:
            Dict containing all event data
        """
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.name,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    def to_json(self) -> str:
        """
        Convert event to JSON string.

        Returns:
            JSON string representation
        """
        data = self.to_dict()
        return json.dumps(data, default=str, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Event':
        """
        Create Event from dictionary.

        Args:
            data: Dictionary with event data

        Returns:
            Event instance
        """
        return cls(
            event_type=EventType[data["event_type"]],
            payload=data.get("payload"),
            source=data.get("source", "unknown"),
            event_id=data.get("event_id", str(uuid.uuid4())),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(),
        )

    def with_payload(self, **kwargs) -> 'Event':
        """
        Create a copy of this event with updated payload.

        Args:
            **kwargs: Key-value pairs to add/update in payload

        Returns:
            New Event with updated payload
        """
        new_payload = {**(self.payload or {}), **kwargs}
        return Event(
            event_type=self.event_type,
            payload=new_payload,
            source=self.source,
            event_id=self.event_id,
            timestamp=self.timestamp,
        )


@dataclass
class TaskEvent(Event):
    """
    Event representing a task request or status update.

    Attributes:
        task_id: Unique identifier for the task
        task_type: Type of task (llm, image, document)
        priority: Task priority level
        request_data: Input data for the task
        result_data: Output data (populated on completion)
        error_message: Error details (populated on failure)
    """

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = "unknown"
    priority: TaskPriority = TaskPriority.NORMAL
    request_data: Optional[Dict[str, Any]] = None
    result_data: Optional[Any] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        """Set default event type if not specified."""
        if self.event_type is None:
            self.event_type = EventType.TASK_SUBMIT

    def is_gpu_task(self) -> bool:
        """
        Check if this task requires GPU resources.

        Returns:
            True if task_type is 'llm', 'chat', 'image', or 'comfyui'
        """
        gpu_types = {'llm', 'chat', 'text', 'image', 'comfyui'}
        return self.task_type.lower() in gpu_types

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary including task-specific fields."""
        base = super().to_dict()
        base.update({
            "task_id": self.task_id,
            "task_type": self.task_type,
            "priority": self.priority.name,
            "request_data": self.request_data,
            "result_data": str(self.result_data)[:500] if self.result_data else None,
            "error_message": self.error_message,
        })
        return base

    def mark_started(self) -> 'TaskEvent':
        """Create a copy marking task as started."""
        return TaskEvent(
            event_type=EventType.TASK_START,
            task_id=self.task_id,
            task_type=self.task_type,
            priority=self.priority,
            request_data=self.request_data,
            source=self.source,
        )

    def mark_completed(self, result: Any = None) -> 'TaskEvent':
        """Create a copy marking task as completed."""
        return TaskEvent(
            event_type=EventType.TASK_COMPLETE,
            task_id=self.task_id,
            task_type=self.task_type,
            priority=self.priority,
            request_data=self.request_data,
            result_data=result,
            source=self.source,
        )

    def mark_failed(self, error: str) -> 'TaskEvent':
        """Create a copy marking task as failed."""
        return TaskEvent(
            event_type=EventType.TASK_FAILED,
            task_id=self.task_id,
            task_type=self.task_type,
            priority=self.priority,
            request_data=self.request_data,
            error_message=error,
            source=self.source,
        )


@dataclass
class StateChangeEvent(Event):
    """
    Event representing a FSM state transition.

    Attributes:
        previous_state: State before transition
        new_state: State after transition
        trigger_task_id: Task that triggered the transition (if any)
        transition_reason: Human-readable reason for transition
    """

    previous_state: Optional[SystemState] = None
    new_state: Optional[SystemState] = None
    trigger_task_id: Optional[str] = None
    transition_reason: str = ""

    def __post_init__(self):
        """Initialize event_type to STATE_CHANGE."""
        if self.event_type is None:
            self.event_type = EventType.STATE_CHANGE

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary including state-specific fields."""
        base = super().to_dict()
        base.update({
            "previous_state": self.previous_state.name if self.previous_state else None,
            "new_state": self.new_state.name if self.new_state else None,
            "trigger_task_id": self.trigger_task_id,
            "transition_reason": self.transition_reason,
        })
        return base

    def is_to_error(self) -> bool:
        """Check if this transition is to ERROR state."""
        return self.new_state == SystemState.ERROR

    def is_from_error(self) -> bool:
        """Check if this transition is from ERROR state."""
        return self.previous_state == SystemState.ERROR


@dataclass
class VRAMEvent(Event):
    """
    Event representing VRAM status updates.

    Attributes:
        current_usage_mb: Current VRAM usage in MB
        total_vram_mb: Total available VRAM in MB
        usage_percent: Usage as percentage (0-100)
        process_breakdown: VRAM usage by process (optional)
        temperature_c: GPU temperature in Celsius (optional)
    """

    current_usage_mb: float = 0.0
    total_vram_mb: float = 0.0
    usage_percent: float = 0.0
    process_breakdown: Optional[Dict[str, float]] = None
    temperature_c: Optional[float] = None
    device_id: int = 0
    device_name: str = ""

    def __post_init__(self):
        """Calculate usage percent if not provided."""
        if self.usage_percent == 0.0 and self.total_vram_mb > 0:
            self.usage_percent = (self.current_usage_mb / self.total_vram_mb) * 100

    def is_critical(self, threshold: float = 95.0) -> bool:
        """
        Check if VRAM usage is at critical level.

        Args:
            threshold: Percentage threshold for critical status

        Returns:
            True if usage_percent >= threshold
        """
        return self.usage_percent >= threshold

    def is_warning(self, threshold: float = 80.0) -> bool:
        """
        Check if VRAM usage is at warning level.

        Args:
            threshold: Percentage threshold for warning status

        Returns:
            True if usage_percent >= threshold
        """
        return self.usage_percent >= threshold

    @property
    def free_memory_mb(self) -> float:
        """Calculate free VRAM in MB."""
        return max(0, self.total_vram_mb - self.current_usage_mb)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary including VRAM-specific fields."""
        base = super().to_dict()
        base.update({
            "current_usage_mb": round(self.current_usage_mb, 2),
            "total_vram_mb": round(self.total_vram_mb, 2),
            "free_memory_mb": round(self.free_memory_mb, 2),
            "usage_percent": round(self.usage_percent, 2),
            "temperature_c": self.temperature_c,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "process_breakdown": self.process_breakdown,
        })
        return base


@dataclass
class ChatEvent(Event):
    """
    Event representing chat processing updates.

    Attributes:
        session_id: Chat session identifier
        message_id: Message identifier (if available)
        chat_state: Current chat pipeline state
        content: Message content or chunk
        rag_used: Whether RAG was used
        mode: Processing mode used
    """

    session_id: Optional[int] = None
    message_id: Optional[int] = None
    chat_state: ChatState = ChatState.IDLE
    content: str = ""
    rag_used: bool = False
    mode: str = "thinking"
    processing_time: Optional[float] = None

    def __post_init__(self):
        """Set default event type based on chat state."""
        if self.event_type is None:
            if self.chat_state == ChatState.COMPLETED:
                self.event_type = EventType.CHAT_COMPLETE
            elif self.chat_state == ChatState.GENERATING:
                self.event_type = EventType.CHAT_STREAM
            else:
                self.event_type = EventType.CHAT_START

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary including chat-specific fields."""
        base = super().to_dict()
        base.update({
            "session_id": self.session_id,
            "message_id": self.message_id,
            "chat_state": self.chat_state.value,
            "content_preview": self.content[:100] if self.content else "",
            "rag_used": self.rag_used,
            "mode": self.mode,
            "processing_time": self.processing_time,
        })
        return base


@dataclass
class ErrorEvent(Event):
    """
    Event representing an error occurrence.

    Attributes:
        error_type: Type/category of error
        error_message: Human-readable error description
        stack_trace: Full stack trace (optional)
        recoverable: Whether the error is recoverable
        context: Additional context about the error
    """

    error_type: str = "unknown"
    error_message: str = ""
    stack_trace: Optional[str] = None
    recoverable: bool = True
    context: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Set event type to ERROR."""
        self.event_type = EventType.ERROR

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary including error-specific fields."""
        base = super().to_dict()
        base.update({
            "error_type": self.error_type,
            "error_message": self.error_message,
            "stack_trace": self.stack_trace,
            "recoverable": self.recoverable,
            "context": self.context,
        })
        return base


# =============================================================================
# Event Factory Functions
# =============================================================================

def create_task_event(
    task_type: str,
    request_data: Dict[str, Any],
    priority: TaskPriority = TaskPriority.NORMAL,
    source: str = "api"
) -> TaskEvent:
    """
    Factory function to create a TaskEvent.

    Args:
        task_type: Type of task (llm, image, etc.)
        request_data: Input data for the task
        priority: Task priority
        source: Event source

    Returns:
        TaskEvent ready for submission
    """
    return TaskEvent(
        event_type=EventType.TASK_SUBMIT,
        task_type=task_type,
        request_data=request_data,
        priority=priority,
        source=source,
    )


def create_state_change_event(
    previous: SystemState,
    new: SystemState,
    reason: str = "",
    task_id: Optional[str] = None
) -> StateChangeEvent:
    """
    Factory function to create a StateChangeEvent.

    Args:
        previous: Previous state
        new: New state
        reason: Reason for transition
        task_id: Task that triggered the transition

    Returns:
        StateChangeEvent
    """
    return StateChangeEvent(
        event_type=EventType.STATE_CHANGE,
        previous_state=previous,
        new_state=new,
        transition_reason=reason,
        trigger_task_id=task_id,
        source="orchestrator",
    )


def create_vram_event(
    usage_mb: float,
    total_mb: float,
    event_type: EventType = EventType.VRAM_WARNING,
    device_id: int = 0,
    device_name: str = ""
) -> VRAMEvent:
    """
    Factory function to create a VRAMEvent.

    Args:
        usage_mb: Current VRAM usage in MB
        total_mb: Total VRAM in MB
        event_type: Type of VRAM event
        device_id: GPU device ID
        device_name: GPU device name

    Returns:
        VRAMEvent
    """
    return VRAMEvent(
        event_type=event_type,
        current_usage_mb=usage_mb,
        total_vram_mb=total_mb,
        device_id=device_id,
        device_name=device_name,
        source="watchdog",
    )
