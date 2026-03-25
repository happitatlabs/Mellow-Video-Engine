"""
Infrastructure Module - Mellow-Link

This module contains infrastructure components:
- GPU/VRAM monitoring
- System resource management
- Hardware abstraction layer
- Event logging (JSONL + DB)
- Database models & authentication
- Persistent experience memory system
"""

from .watchdog import VRAMWatchdog, VRAMStatus, create_watchdog
from .event_logger import log_event, log_intent, SBMA_INTENTS, EVENTS_DIR, EVENTS_FILE
from .memory_database import (
    MemoryDatabase,
    ExperienceRecord,
    ToolStatRecord,
    GoalRecord,
    BehaviorInsight,
    ScheduledTask,
    get_memory_db,
)
from .archiver import (
    MemoryArchiver,
    TaskData,
    get_archiver,
)
from .database import (
    # Engine & Session
    engine,
    SessionLocal,
    Base,
    init_db,
    get_db,
    # Role Enum
    UserRole,
    # Models
    User,
    AgentFolder,
    UserMemory,
    ChatSession,
    ChatMessage,
    FolderDocument,
    MessageFeedback,
    TempResource,
    EventLog,
    DailyUsage,
    GuestUsage,
    DocumentChunk,
    AgentRun,
    AgentRunEvent,
    # Helper Functions
    create_default_folders_for_user,
    ensure_user_has_folders,
    get_or_create_default_session,
    # Auth Functions
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
    get_current_user_optional,
    get_or_create_guest_user,
    get_token_payload,
    # Guest Functions
    get_guest_usage_today,
    increment_guest_usage,
    check_guest_limit,
    # Constants
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from .run_events import (
    create_run,
    emit_event,
    get_run_events,
    get_run_snapshot,
    EVENT_TYPE_RUN_STARTED,
    EVENT_TYPE_PLAN_CREATED,
    EVENT_TYPE_TODO_STARTED,
    EVENT_TYPE_TODO_DONE,
    EVENT_TYPE_TOOL_STARTED,
    EVENT_TYPE_TOOL_DONE,
    EVENT_TYPE_LOG,
    EVENT_TYPE_RUN_FINISHED,
    EVENT_TYPE_ERROR,
)

__all__ = [
    # Watchdog
    "VRAMWatchdog",
    "VRAMStatus",
    "create_watchdog",
    # Event Logger
    "log_event",
    "log_intent",
    "SBMA_INTENTS",
    "EVENTS_DIR",
    "EVENTS_FILE",
    # Memory Database
    "MemoryDatabase",
    "ExperienceRecord",
    "ToolStatRecord",
    "GoalRecord",
    "BehaviorInsight",
    "ScheduledTask",
    "get_memory_db",
    # Memory Archiver
    "MemoryArchiver",
    "TaskData",
    "get_archiver",
    # Database Engine & Session
    "engine",
    "SessionLocal",
    "Base",
    "init_db",
    "get_db",
    # Role Enum
    "UserRole",
    # Models
    "User",
    "AgentFolder",
    "UserMemory",
    "ChatSession",
    "ChatMessage",
    "FolderDocument",
    "MessageFeedback",
    "TempResource",
    "EventLog",
    "DailyUsage",
    "GuestUsage",
    "DocumentChunk",
    "AgentRun",
    "AgentRunEvent",
    # Run Events
    "create_run",
    "emit_event",
    "get_run_events",
    "get_run_snapshot",
    "EVENT_TYPE_RUN_STARTED",
    "EVENT_TYPE_PLAN_CREATED",
    "EVENT_TYPE_TODO_STARTED",
    "EVENT_TYPE_TODO_DONE",
    "EVENT_TYPE_TOOL_STARTED",
    "EVENT_TYPE_TOOL_DONE",
    "EVENT_TYPE_LOG",
    "EVENT_TYPE_RUN_FINISHED",
    "EVENT_TYPE_ERROR",
    # Helper Functions
    "create_default_folders_for_user",
    "ensure_user_has_folders",
    "get_or_create_default_session",
    # Auth Functions
    "verify_password",
    "get_password_hash",
    "create_access_token",
    "get_current_user",
    "get_current_user_optional",
    "get_or_create_guest_user",
    "get_token_payload",
    # Guest Functions
    "get_guest_usage_today",
    "increment_guest_usage",
    "check_guest_limit",
    # Constants
    "ACCESS_TOKEN_EXPIRE_MINUTES",
]
