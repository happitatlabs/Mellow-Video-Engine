"""
Memory Archiver - 영속적 경험 메모리 시스템의 아카이버

ReAct 루프가 끝난 뒤, 전체 로그를 분석하여 핵심 교훈을 추출하고 DB에 저장합니다.

리플렉션 루프 (Reflection Loop):
1. Capture (포착): Task 완료 시 Thought, Action, Observation 전체 트레이스 수집
2. Distill (증류): LLM에게 전체 로그 전달하여 lessons_learned 추출
3. Self-Critique (자가 비판): 실패 시 에러 패턴 분석하여 critique_tag 부여
4. Save (기록): 요약된 데이터와 통계를 SQLite에 저장
"""

import json
import logging
import uuid
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

from mellow_link.infra.archiver_schemas import AgentResult, AgentStep
from mellow_link.infra.memory_database import (
    MemoryDatabase,
    ExperienceRecord,
    get_memory_db
)

logger = logging.getLogger(__name__)


# =============================================================================
# 데이터 구조
# =============================================================================

@dataclass
class TaskData:
    """아카이브할 태스크 데이터."""
    user_input: str  # 사용자 입력
    context_summary: str  # 실행 당시의 핵심 상황 및 제약사항
    agent_result: AgentResult  # ReAct 루프 결과
    start_time: Optional[datetime] = None  # 시작 시간
    end_time: Optional[datetime] = None  # 종료 시간


# =============================================================================
# Memory Archiver
# =============================================================================

class MemoryArchiver:
    """
    영속적 경험 메모리 아카이버.
    
    ReAct 루프 완료 후 전체 로그를 분석하여 핵심 교훈을 추출하고 저장합니다.
    """

    def __init__(
        self,
        db: Optional[MemoryDatabase] = None,
        llm_service: Optional[Any] = None
    ):
        """
        Args:
            db: MemoryDatabase 인스턴스 (None이면 싱글톤 사용)
            llm_service: LLM 서비스 인스턴스 (lessons_learned 추출용)
        """
        self.db = db or get_memory_db()
        self.llm_service = llm_service
        logger.info("[MemoryArchiver] Initialized")

    async def archive(self, task_data: TaskData) -> Optional[str]:
        """
        태스크 데이터를 아카이브합니다.
        
        리플렉션 루프:
        1. Capture: 전체 트레이스 수집
        2. Distill: LLM으로 lessons_learned 추출
        3. Self-Critique: 실패 시 에러 태깅
        4. Save: DB에 저장
        
        Args:
            task_data: 아카이브할 태스크 데이터
            
        Returns:
            저장된 경험 ID 또는 None (실패 시)
        """
        try:
            # 1. Capture: 전체 트레이스 수집
            experience_id = str(uuid.uuid4())
            task_intent = self._extract_task_intent(task_data.user_input)
            task_hash = MemoryDatabase.compute_task_hash(
                task_intent,
                task_data.context_summary
            )
            
            # action_steps를 JSON 문자열로 변환
            action_steps_json = self._serialize_steps(task_data.agent_result.steps)
            
            # 성공 여부 판단
            is_success = self._determine_success(task_data.agent_result)
            
            # 2. Distill: LLM으로 lessons_learned 추출
            lessons_learned = None
            if self.llm_service:
                lessons_learned = await self._distill_lessons(
                    task_data,
                    action_steps_json
                )
            else:
                # LLM이 없으면 간단한 요약 생성
                lessons_learned = self._simple_lesson_summary(task_data)
            
            # 3. Self-Critique: 실패 시 에러 태깅 / 복구 성공 시 RECOVERY_SUCCESS 태깅
            critique_tag = None
            if is_success and getattr(task_data.agent_result, "recovery_success", False):
                critique_tag = "#RECOVERY_SUCCESS"
            elif not is_success:
                critique_tag = self._analyze_failure_pattern(
                    task_data.agent_result
                )
            
            # 4. Save: DB에 저장
            record = ExperienceRecord(
                id=experience_id,
                task_intent=task_intent,
                task_hash=task_hash,
                context_summary=task_data.context_summary,
                action_steps=action_steps_json,
                final_outcome=task_data.agent_result.answer,
                is_success=1 if is_success else 0,
                critique_tag=critique_tag,
                lessons_learned=lessons_learned,
                created_at=task_data.end_time or datetime.now()
            )
            
            success = self.db.save_experience(record)
            
            # 도구 통계 업데이트
            self._update_tool_stats(task_data.agent_result.steps)
            
            if success:
                logger.info(f"[MemoryArchiver] Archived experience: {experience_id}")
                return experience_id
            else:
                logger.error("[MemoryArchiver] Failed to save experience")
                return None
                
        except Exception as e:
            logger.error(f"[MemoryArchiver] Archive failed: {e}", exc_info=True)
            return None

    def _extract_task_intent(self, user_input: str) -> str:
        """
        사용자 입력에서 작업 의도 추출.
        
        Args:
            user_input: 사용자 입력
            
        Returns:
            작업 의도 요약 (예: "API 도구 생성")
        """
        # 간단한 추출: 첫 100자 또는 첫 줄
        intent = user_input.strip().split('\n')[0]
        if len(intent) > 100:
            intent = intent[:97] + "..."
        return intent

    def _serialize_steps(self, steps: List[AgentStep]) -> str:
        """
        AgentStep 리스트를 JSON 문자열로 변환.
        
        Args:
            steps: AgentStep 리스트
            
        Returns:
            JSON 문자열
        """
        try:
            steps_data = []
            for step in steps:
                step_dict = {
                    "turn": step.turn,
                    "thought": step.thought,
                    "action": None,
                    "observation": step.observation
                }
                
                if step.action:
                    step_dict["action"] = {
                        "tool": step.action.tool,
                        "args": step.action.args
                    }
                
                steps_data.append(step_dict)
            
            return json.dumps(steps_data, ensure_ascii=False, indent=2)
            
        except Exception as e:
            logger.error(f"[MemoryArchiver] Failed to serialize steps: {e}")
            return json.dumps([])

    def _determine_success(self, result: AgentResult) -> bool:
        """
        태스크 성공 여부 판단.
        
        Args:
            result: AgentResult
            
        Returns:
            성공 여부
        """
        # finish_reason 기반 판단
        if result.finish_reason == "finish_tool":
            return True
        elif result.finish_reason in ("security_violation", "error"):
            return False
        elif result.finish_reason == "max_turns":
            # max_turns 도달은 부분 실패로 간주
            return False
        else:
            # 기본적으로 성공으로 간주 (finish_tool이면 성공)
            return True

    async def _distill_lessons(
        self,
        task_data: TaskData,
        action_steps_json: str
    ) -> str:
        """
        LLM을 활용하여 핵심 교훈(lessons_learned) 추출.
        
        ✅ verified: Exponential Backoff 재시도 로직 적용 (최대 3회)
        ✅ verified: 연결 실패 시 한국어 에러 메시지 반환
        
        Args:
            task_data: 태스크 데이터
            action_steps_json: 액션 스텝 JSON 문자열
            
        Returns:
            핵심 교훈 텍스트 (실패 시 간단한 요약)
        """
        if not self.llm_service:
            return self._simple_lesson_summary(task_data)
        
        # LLM 프롬프트 구성
        prompt = f"""다음은 Mellow-Link 에이전트가 수행한 작업의 전체 로그입니다.

사용자 요청: {task_data.user_input}

컨텍스트: {task_data.context_summary}

ReAct 루프 시퀀스:
{action_steps_json}

최종 결과: {task_data.agent_result.answer}
종료 사유: {task_data.agent_result.finish_reason}

이 작업에서 배운 핵심 교훈을 한 문단으로 요약해주세요.
- 다음에 유사한 작업을 수행할 때 참고할 수 있는 실용적인 인사이트를 제공하세요.
- 구체적이고 실행 가능한 조언을 포함하세요.
- 실패한 경우, 실패 원인과 개선 방안을 명확히 제시하세요.

핵심 교훈:"""
        
        # Exponential Backoff 재시도 로직 (최대 3회)
        max_retries = 3
        base_delay = 1.0  # 초기 지연 시간 (초)
        
        for attempt in range(max_retries):
            try:
                # LLMService 연결 상태 확인
                if hasattr(self.llm_service, 'is_ready'):
                    if not self.llm_service.is_ready():
                        # 연결이 끊어진 경우 재연결 시도
                        if hasattr(self.llm_service, 'connect'):
                            try:
                                await self.llm_service.connect()
                                logger.info(f"[MemoryArchiver] LLMService 재연결 성공 (시도 {attempt + 1}/{max_retries})")
                            except Exception as conn_e:
                                logger.warning(f"[MemoryArchiver] LLMService 재연결 실패: {conn_e}")
                                if attempt < max_retries - 1:
                                    delay = base_delay * (2 ** attempt)
                                    logger.info(f"[MemoryArchiver] {delay:.1f}초 후 재시도...")
                                    await asyncio.sleep(delay)
                                    continue
                                else:
                                    # 최종 실패 시 한국어 에러 메시지 반환
                                    error_msg = f"[연결 오류] LLM 서비스에 연결할 수 없습니다. 간단한 요약을 사용합니다."
                                    logger.error(f"[MemoryArchiver] {error_msg}")
                                    return f"{error_msg}\n\n{self._simple_lesson_summary(task_data)}"
                
                # LLM 호출 시도
                if hasattr(self.llm_service, 'generate'):
                    result = await self.llm_service.generate(
                        prompt=prompt,
                        # 아카이버는 사후 요약이므로 경량 모드 사용 (연결 안정성/지연 완화)
                        mode="fast",
                        max_tokens=220,
                        temperature=0.7
                    )
                    if hasattr(result, 'content'):
                        content = result.content.strip()
                        if content:
                            logger.info(f"[MemoryArchiver] 교훈 추출 성공 (시도 {attempt + 1}/{max_retries})")
                            return content
                    return str(result).strip() if result else self._simple_lesson_summary(task_data)
                    
                elif hasattr(self.llm_service, 'chat'):
                    messages = [
                        {"role": "system", "content": "당신은 경험 분석 전문가입니다."},
                        {"role": "user", "content": prompt}
                    ]
                    response = await self.llm_service.chat(messages=messages)
                    if hasattr(response, 'text'):
                        content = response.text.strip()
                        if content:
                            logger.info(f"[MemoryArchiver] 교훈 추출 성공 (시도 {attempt + 1}/{max_retries})")
                            return content
                    return str(response).strip() if response else self._simple_lesson_summary(task_data)
                else:
                    # 지원되지 않는 LLMService 인터페이스
                    logger.warning("[MemoryArchiver] LLMService 인터페이스를 지원하지 않습니다.")
                    return self._simple_lesson_summary(task_data)
                    
            except Exception as e:
                error_msg = str(e)
                is_connection_error = any(keyword in error_msg.lower() for keyword in [
                    "connection", "connect", "not connected", "unreachable", 
                    "timeout", "연결", "접속", "연결 실패"
                ])
                
                if is_connection_error and attempt < max_retries - 1:
                    # 연결 오류인 경우 Exponential Backoff로 재시도
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"[MemoryArchiver] LLM 연결 오류 (시도 {attempt + 1}/{max_retries}): {error_msg}. "
                        f"{delay:.1f}초 후 재시도..."
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    # 최종 실패 또는 비연결 오류인 경우
                    if attempt == max_retries - 1:
                        # 최종 실패 시 한국어 에러 메시지 반환
                        final_error_msg = (
                            f"[연결 오류] LLM 서비스 호출에 실패했습니다 (시도 {max_retries}회). "
                            f"간단한 요약을 사용합니다."
                        )
                        logger.warning(f"[MemoryArchiver] {final_error_msg} (최종 오류: {error_msg})")
                        return f"{final_error_msg}\n\n{self._simple_lesson_summary(task_data)}"
                    else:
                        # 비연결 오류는 즉시 폴백
                        logger.warning(f"[MemoryArchiver] 교훈 추출 실패(폴백): {error_msg}")
                        return self._simple_lesson_summary(task_data)
        
        # 모든 재시도 실패 시 폴백
        logger.warning(f"[MemoryArchiver] 모든 재시도 실패. 간단한 요약을 사용합니다.")
        return self._simple_lesson_summary(task_data)

    def _simple_lesson_summary(self, task_data: TaskData) -> str:
        """
        LLM 없이 간단한 교훈 요약 생성 (폴백).
        
        Args:
            task_data: 태스크 데이터
            
        Returns:
            간단한 교훈 요약
        """
        result = task_data.agent_result
        
        if result.finish_reason == "finish_tool":
            return f"작업이 성공적으로 완료되었습니다. 총 {result.total_turns}턴이 소요되었습니다."
        elif result.finish_reason == "max_turns":
            return f"최대 턴 수({result.total_turns})에 도달하여 작업이 중단되었습니다. 더 효율적인 접근 방법이 필요합니다."
        elif result.finish_reason == "security_violation":
            return "보안 정책 위반으로 작업이 차단되었습니다. 안전한 도구와 방법을 사용해야 합니다."
        else:
            return f"작업이 {result.finish_reason}로 종료되었습니다."

    def _analyze_failure_pattern(self, result: AgentResult) -> str:
        """
        실패 패턴 분석 및 태그 생성.
        
        Args:
            result: AgentResult
            
        Returns:
            실패 원인 태그 (예: #API_Error, #Logic_Error 등)
        """
        # finish_reason 기반 태깅
        if result.finish_reason == "security_violation":
            return "#Security_Violation"
        elif result.finish_reason == "max_turns":
            return "#Max_Turns_Exceeded"
        elif result.finish_reason == "error":
            # 마지막 observation에서 에러 패턴 분석
            if result.steps:
                last_obs = result.steps[-1].observation.lower()
                if "api" in last_obs or "http" in last_obs or "connection" in last_obs:
                    return "#API_Error"
                elif "permission" in last_obs or "access" in last_obs:
                    return "#Permission_Error"
                elif "not found" in last_obs or "file" in last_obs:
                    return "#Resource_Error"
                elif "json" in last_obs or "parse" in last_obs:
                    return "#Parse_Error"
                else:
                    return "#Unknown_Error"
            return "#Error"
        else:
            return "#Unknown_Failure"

    def _update_tool_stats(self, steps: List[AgentStep]) -> None:
        """
        도구 통계 업데이트 (정확한 누적 방식).
        
        각 도구 호출을 개별적으로 집계하여 use_count와 success_count가
        실제 호출 횟수만큼 정확히 누적되도록 함.
        
        Args:
            steps: AgentStep 리스트
        """
        # 각 스텝의 도구 사용 통계 수집
        tool_calls = []  # [(tool_name, is_success, runtime_ms, error_msg), ...]
        
        for step in steps:
            if step.action:
                tool_name = step.action.tool
                if tool_name == "finish":
                    continue  # finish는 통계에서 제외
                
                # 성공 여부 판단 (observation에 에러가 없으면 성공)
                observation_lower = step.observation.lower()
                is_success = not any(
                    keyword in observation_lower
                    for keyword in ["error", "failed", "exception", "실패", "차단"]
                )
                
                # 실행 시간 추정 (실제로는 측정된 값이 필요하지만, 여기서는 간단히 처리)
                runtime_ms = 100.0  # 기본값 (실제로는 측정 필요)
                
                # 에러 메시지 추출 (실패 시)
                error_msg = None
                if not is_success:
                    # observation에서 에러 메시지 추출 (간단한 추출)
                    if "error" in observation_lower or "실패" in observation_lower:
                        error_msg = step.observation[:200]  # 처음 200자만
                
                tool_calls.append((tool_name, is_success, runtime_ms, error_msg))
        
        # 도구별로 그룹화하여 각 호출마다 개별 업데이트 (정확한 누적)
        tool_groups = {}  # {tool_name: [(is_success, runtime_ms, error_msg), ...]}
        for tool_name, is_success, runtime_ms, error_msg in tool_calls:
            if tool_name not in tool_groups:
                tool_groups[tool_name] = []
            tool_groups[tool_name].append((is_success, runtime_ms, error_msg))
        
        for tool_name, calls in tool_groups.items():
            for is_success, runtime_ms, error_msg in calls:
                self.db.update_tool_stat(
                    tool_name=tool_name,
                    is_success=is_success,
                    runtime_ms=runtime_ms,
                    error_msg=error_msg if not is_success else None
                )


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_archiver_instance: Optional[MemoryArchiver] = None


def get_archiver(
    db: Optional[MemoryDatabase] = None,
    llm_service: Optional[Any] = None
) -> MemoryArchiver:
    """
    MemoryArchiver 싱글톤 인스턴스 반환.
    
    Args:
        db: MemoryDatabase 인스턴스 (첫 호출 시에만 적용)
        llm_service: LLM 서비스 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        MemoryArchiver 인스턴스
    """
    global _archiver_instance
    if _archiver_instance is None:
        _archiver_instance = MemoryArchiver(db=db, llm_service=llm_service)
    return _archiver_instance
