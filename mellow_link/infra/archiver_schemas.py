"""
아키텍처 감리 P3: archiver 의존성 수정.

AgentResult, AgentStep(및 AgentAction)을 infra에 두어
infra → core 역방향 의존을 제거합니다.
core/agent_schemas는 이 모듈을 re-export하여 기존 import 호환을 유지합니다.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentAction:
    """LLM이 선택한 하나의 도구 호출."""
    tool: str
    args: Dict[str, Any]


@dataclass
class AgentStep:
    """ReAct 루프의 한 턴 기록."""
    turn: int
    thought: str
    action: Optional[AgentAction] = None
    observation: str = ""


@dataclass
class AgentResult:
    """에이전트 실행 최종 결과."""
    answer: str
    steps: List[AgentStep] = field(default_factory=list)
    total_turns: int = 0
    finish_reason: str = ""
    recovery_success: bool = False
    limitations: List[str] = field(default_factory=list)
    total_infer_ms: float = 0.0
