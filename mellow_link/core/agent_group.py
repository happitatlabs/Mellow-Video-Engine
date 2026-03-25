"""
Agent Group - 다중 에이전트 오케스트레이션 (Phase 6)

단일 AgentBrain을 확장하여 역할을 분담할 수 있는 AgentGroup을 제공합니다.
CoderAgent, TesterAgent, ArchitectAgent 등 페르소나와 도구 셋이 특화된 서브 에이전트 팩토리.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Specialist 정의 (팩토리용)
# ═══════════════════════════════

@dataclass
class SpecialistConfig:
    """전문가 에이전트 설정: 페르소나 + 선택적 도구 필터."""
    name: str
    persona: str
    description: str = ""


# 기본 전문가 페르소나 (hypothetical: 실제 협업 구조는 추후 고도화)
CODER_PERSONA = """\
너는 Mellow-Link의 Coder 전문가다.
역할: 코드 작성·리팩터링·버그 수정. 제안할 때는 구체적인 코드 스니펫과 파일 경로를 명시한다.
도구: read_file, write_file, list_directory, propose_new_tool 등을 우선 사용한다.
"""

TESTER_PERSONA = """\
너는 Mellow-Link의 Tester 전문가다.
역할: 테스트 케이스 설계·검증·회귀 확인. 제안할 때는 검증 가능한 단계와 예상 결과를 명시한다.
도구: read_file, list_directory, run_command(테스트 실행) 등을 우선 사용한다.
"""

ARCHITECT_PERSONA = """\
너는 Mellow-Link의 Architect 전문가다.
역할: 구조 설계·모듈 분리·의존성 정리. 제안할 때는 다이어그램·목록·우선순위를 명시한다.
도구: read_file, list_directory, write_file(설계서) 등을 우선 사용한다.
"""


def get_specialist_factory() -> Dict[str, SpecialistConfig]:
    """전문가 이름 -> SpecialistConfig 팩토리 맵."""
    return {
        "coder": SpecialistConfig(
            name="CoderAgent",
            persona=CODER_PERSONA,
            description="코드 작성·리팩터링·버그 수정",
        ),
        "tester": SpecialistConfig(
            name="TesterAgent",
            persona=TESTER_PERSONA,
            description="테스트 설계·검증·회귀 확인",
        ),
        "architect": SpecialistConfig(
            name="ArchitectAgent",
            persona=ARCHITECT_PERSONA,
            description="구조 설계·모듈 분리·의존성 정리",
        ),
    }


# ═══════════════════════════════════════════════
# Agent Group
# ═══════════════════════════════

class AgentGroup:
    """
    다중 에이전트 오케스트레이션: 하나의 AgentBrain에 전문가별 페르소나를 붙여 실행.
    """

    def __init__(
        self,
        agent_brain: Optional[Any] = None,
        specialists: Optional[Dict[str, SpecialistConfig]] = None,
    ):
        """
        Args:
            agent_brain: 단일 AgentBrain 인스턴스 (None이면 내부에서 생성)
            specialists: 전문가 이름 -> SpecialistConfig (None이면 기본 팩토리 사용)
        """
        self._brain = agent_brain
        self._specialists = specialists or get_specialist_factory()
        if self._brain is None:
            try:
                from mellow_link.core.agent_brain import AgentBrain
                from mellow_link.services.llm_service import get_llm_service
                llm = get_llm_service()
                self._brain = AgentBrain(llm_service=llm)
            except Exception as e:
                logger.warning("[AgentGroup] Could not create default AgentBrain: %s", e)
        logger.info("[AgentGroup] Initialized with specialists: %s", list(self._specialists.keys()))

    def list_specialists(self) -> List[str]:
        """등록된 전문가 이름 목록."""
        return list(self._specialists.keys())

    def get_specialist_persona(self, name: str) -> Optional[str]:
        """전문가 페르소나 문자열 (없으면 None)."""
        cfg = self._specialists.get(name)
        return cfg.persona if cfg else None

    async def run_with_specialist(
        self,
        specialist_name: str,
        user_input: str,
        context: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """
        지정한 전문가 페르소나로 AgentBrain.run() 실행.
        
        Args:
            specialist_name: coder, tester, architect 등
            user_input: 사용자 입력
            context: 컨텍스트 메시지
            session_id: 세션 ID
            **kwargs: AgentBrain.run()에 그대로 전달
            
        Returns:
            AgentResult (또는 Brain 미설정 시 에러 메시지)
        """
        if self._brain is None:
            return type("Result", (), {"answer": "[Error] AgentBrain not set", "finish_reason": "error"})()

        persona = self.get_specialist_persona(specialist_name)
        if persona is None:
            return type("Result", (), {
                "answer": f"[Error] Unknown specialist: {specialist_name}. Available: {self.list_specialists()}",
                "finish_reason": "error",
            })()

        logger.info("[AgentGroup] Running with specialist: %s", specialist_name)
        return await self._brain.run(
            user_input=user_input,
            context=context or [],
            persona=persona,
            session_id=session_id,
            **kwargs,
        )


# ═══════════════════════════════════════════════
# Singleton
# ═══════════════════════════════

_agent_group_instance: Optional[AgentGroup] = None


def get_agent_group(
    agent_brain: Optional[Any] = None,
    specialists: Optional[Dict[str, SpecialistConfig]] = None,
) -> AgentGroup:
    """AgentGroup 싱글톤."""
    global _agent_group_instance
    if _agent_group_instance is None:
        _agent_group_instance = AgentGroup(agent_brain=agent_brain, specialists=specialists)
    return _agent_group_instance
