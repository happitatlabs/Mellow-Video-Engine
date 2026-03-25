"""
Agent 데이터 구조: ReAct 루프에서 사용하는 핵심 dataclass 정의.

정의는 infra/archiver_schemas.py에 두고, 여기서 re-export하여
의존성 방향(routers/core → infra)을 유지합니다.
기존 코드: from mellow_link.core.agent_schemas import AgentAction, AgentStep, AgentResult
"""
from mellow_link.infra.archiver_schemas import AgentAction, AgentStep, AgentResult

__all__ = ["AgentAction", "AgentStep", "AgentResult"]
