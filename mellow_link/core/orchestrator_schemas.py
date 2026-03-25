"""
Orchestrator 스키마: IntentResult, ChatContext, VALID_TRANSITIONS.

채팅 파이프라인 상태 및 컨텍스트 정의.
ChatState는 states.ChatState(Enum) 단일 소스 사용.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .states import SystemState, ChatState


@dataclass
class IntentResult:
    """
    Result of intent classification.

    Attributes:
        intent: Classified intent type (simple_chat, image_request, document_qa)
        confidence: Confidence score (0.0 ~ 1.0)
        metadata: Additional metadata from classification
    """
    intent: str  # "simple_chat" | "image_request" | "document_qa"
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatContext:
    """
    Context for chat processing pipeline.

    Migrated from legacy StateContext with enhancements for
    the new modular architecture.
    """
    # Input
    user_query: str
    system_prompt: str = ""
    use_rag: bool = False
    rag_collection_name: Optional[str] = None
    user_memories: List[str] = field(default_factory=list)
    session_history: List[Dict[str, str]] = field(default_factory=list)
    mode: str = "fast"  # fast (quick), thinking (deep), research (web+deep), auto

    # Processing state
    should_use_rag: bool = False
    rag_context: str = ""
    rag_sources: List[Dict] = field(default_factory=list)

    # Intent classification results
    intent_result: Optional[IntentResult] = None
    target_service: str = "llm"  # "llm" | "image" | "document"
    refined_prompt: str = ""  # Flux-optimized English prompt for image generation

    # Output
    final_answer: str = ""
    state_info: str = ""
    rag_used: bool = False

    # Metadata (ChatState Enum: states.ChatState 단일 소스)
    current_state: ChatState = ChatState.IDLE
    error_message: str = ""
    processing_time: float = 0.0
    selected_mode: Optional[str] = None
    prompt_category: Optional[str] = None  # "tool" | "chat" | etc. (for auto mode routing)


# Valid state transitions: from_state -> set of valid to_states
VALID_TRANSITIONS: Dict[SystemState, set] = {
    SystemState.IDLE: {SystemState.TEXT, SystemState.IMAGE, SystemState.ERROR},
    SystemState.TEXT: {SystemState.IDLE, SystemState.IMAGE, SystemState.ERROR},
    SystemState.IMAGE: {SystemState.IDLE, SystemState.TEXT, SystemState.ERROR},
    SystemState.ERROR: {SystemState.IDLE},
}
