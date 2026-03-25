"""
Core Module - Mellow-Link Orchestration System

This module contains the core logic for the AI orchestration system:
- State management (FSM)
- Event-driven messaging
- Main orchestrator coordination
- Request/Response schemas
- Security & Admin bootstrapping
- Agent Brain (ReAct loop)
- Experience Provider (memory retrieval)
"""

from .states import SystemState, TaskPriority, TransitionResult
from .events import Event, TaskEvent, StateChangeEvent, EventType
from .orchestrator import Orchestrator, ChatContext
from .schemas import ImageRequest
from .agent_schemas import AgentAction, AgentStep, AgentResult
from .agent_brain import AgentBrain
from .experience_provider import ExperienceProvider, get_experience_provider
from .complexity_evaluator import ComplexityEvaluator, get_complexity_evaluator
from .checkpoint_manager import CheckpointManager, get_checkpoint_manager
from .goal_planner import GoalPlanner, get_goal_planner
from .goal_manager import GoalManager, get_goal_manager
from .log_analyzer import ActionLogAnalyzer, get_log_analyzer
from .scheduler_service import SchedulerService, get_scheduler_service, calculate_next_run
from .diagnosis_service import (
    DiagnosisService,
    get_diagnosis_service,
    KPIMetrics,
    ExtendedKPIMetrics,
    RecurrenceDetail,
    DiagnosisReport,
)
from .recovery_manager import RecoveryManager, get_recovery_manager, RecoverySuggestion
from .tool_forge import (
    ToolForge,
    get_tool_forge,
    ForgeResult,
    BatchForgeResult,
    NeedDetectionResult,
    ASTSecurityAnalyzer,
    run_ast_security_check,
    IntegrityGuard,
    IntegrityResult,
)
from .dynamic_registry import DynamicToolRegistry, get_dynamic_registry
from .evolution_manager import EvolutionManager, EvolutionProposal, SecurityError, get_evolution_manager
from .evolution_factory import get_evolution_service, reset_evolution_service_cache
from .evolution_facade import EvolutionFacade, run_evolution_cycle_via_facade
from .evolution_facade_schemas import EvolutionResponse, DisabledReason
from .evolution_trigger import run_evolution_tick, is_evolution_trigger_enabled
from .provider_factory import get_client, generate_async
from .test_forge import TestForge, get_test_forge
from .agent_group import (
    AgentGroup,
    SpecialistConfig,
    get_agent_group,
    get_specialist_factory,
)
from .security import (
    bootstrap_admin_account,
    check_admin_exists,
    create_admin_user,
    get_admin_user,
    safe_get_password_hash,
    is_admin_user,
    is_superuser,
)

__all__ = [
    # States
    "SystemState",
    "TaskPriority",
    "TransitionResult",
    # Events
    "Event",
    "TaskEvent",
    "StateChangeEvent",
    "EventType",
    # Orchestrator
    "Orchestrator",
    "ChatContext",
    # Schemas
    "ImageRequest",
    # Agent Brain
    "AgentBrain",
    "AgentResult",
    "AgentStep",
    "AgentAction",
    # Experience Provider
    "ExperienceProvider",
    "get_experience_provider",
    # Complexity Evaluator
    "ComplexityEvaluator",
    "get_complexity_evaluator",
    # Checkpoint Manager
    "CheckpointManager",
    "get_checkpoint_manager",
    # Goal Planner
    "GoalPlanner",
    "get_goal_planner",
    # Goal Manager
    "GoalManager",
    "get_goal_manager",
    # Action Log Analyzer
    "ActionLogAnalyzer",
    "get_log_analyzer",
    # Scheduler Service
    "SchedulerService",
    "get_scheduler_service",
    "calculate_next_run",
    # Diagnosis Service (Phase 5 확장)
    "DiagnosisService",
    "get_diagnosis_service",
    "KPIMetrics",
    "ExtendedKPIMetrics",
    "RecurrenceDetail",
    "DiagnosisReport",
    # Recovery Manager
    "RecoveryManager",
    "get_recovery_manager",
    "RecoverySuggestion",
    # Tool Forge (Phase 4 → Phase 5)
    "ToolForge",
    "get_tool_forge",
    "ForgeResult",
    "BatchForgeResult",
    "NeedDetectionResult",
    "ASTSecurityAnalyzer",
    "run_ast_security_check",
    "IntegrityGuard",
    "IntegrityResult",
    # Dynamic Registry (Phase 4)
    "DynamicToolRegistry",
    "get_dynamic_registry",
    # Evolution Manager (Phase 5)
    "EvolutionManager",
    "EvolutionProposal",
    "SecurityError",
    "get_evolution_manager",
    "get_evolution_service",
    "reset_evolution_service_cache",
    "EvolutionFacade",
    "run_evolution_cycle_via_facade",
    "EvolutionResponse",
    "DisabledReason",
    "run_evolution_tick",
    "is_evolution_trigger_enabled",
    "get_client",
    "generate_async",
    # Test Forge (Phase 5)
    "TestForge",
    "get_test_forge",
    # Agent Group (Phase 6)
    "AgentGroup",
    "SpecialistConfig",
    "get_agent_group",
    "get_specialist_factory",
    # Security
    "bootstrap_admin_account",
    "check_admin_exists",
    "create_admin_user",
    "get_admin_user",
    "safe_get_password_hash",
    "is_admin_user",
    "is_superuser",
]
