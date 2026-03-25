"""
Agent Brain: Mellow-Link의 자율 사고 엔진 (ReAct Loop).

흐름:
  User Input → [THINK] LLM 추론 → [PARSE] JSON 액션 추출
             → [ACT] 도구 실행   → [OBSERVE] 결과 기록
             → finish 호출 또는 max_turns 도달 시 종료

의존성:
  - services/llm_service.py : LLM 호출 (Ollama chat API)
  - core/tool_registry.py   : 도구 목록 + 실행
  - core/agent_tools.py     : 실제 도구 함수 (import 시 자동 등록)

분리된 모듈:
  - core/agent_schemas.py     : 데이터 구조 (AgentAction, AgentStep, AgentResult)
  - core/agent_path_utils.py  : 경로 정규화 / 보안 검증
  - core/agent_prompts.py     : 시스템 프롬프트 빌드 + 미션 로딩
  - core/agent_parsers.py     : LLM 출력 파싱 + 보고서 포맷팅
  - core/agent_experience.py  : 경험 기록 / 아카이빙 / 분석 트리거
"""
import asyncio
import gc
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mellow_link.core.constants import (
    LOG_TRUNCATE_LEN,
    MAX_OBSERVATION_SIZE,
    MAX_SYSTEM_PROMPT_CHARS,
    MAX_TOOLS_SCHEMA_CHARS,
)
from mellow_link.core.security_manager import SecurityBlocked

# ── 분리된 모듈에서 import ──
from mellow_link.core.agent_schemas import AgentAction, AgentStep, AgentResult
from mellow_link.core.agent_path_utils import (
    BASE_PATH,
    _get_workspace_root,
    _normalize_path,
    _validate_path_in_workspace,
    _normalize_and_validate_path_args,
    _validate_path_args_for_tool,
)
from mellow_link.core.agent_prompts import (
    build_system_prompt,
    _validate_experience_advisory_and_append_disclaimer,
    _is_expansion_request,
    _is_long_form_request,
    _get_expansion_level,
)
from mellow_link.core.agent_parsers import (
    parse_action,
    _has_valid_tool_execution,
    _collect_core_observations,
    _build_six_step_report,
    _should_enforce_structured_report,
    _extract_limitations,
    _extract_success_pattern,
    _save_success_insight,
    validate_response_requires_action,
)
from mellow_link.core.agent_experience import ExperienceHelper
import os

logger = logging.getLogger(__name__)


def _is_routing_hit(query: str) -> Optional[str]:
    """
    라우팅 히트 감지: 특정 질문 패턴이 경량 도구에 직접 매핑되는지 확인.
    
    Returns:
        매핑된 도구 이름 또는 None
    """
    q = query.lower().strip()
    
    # 시간 관련
    if any(kw in q for kw in ["몇 시", "현재 시간", "시간 알려", "time", "what time"]):
        return "get_time"
    
    # 작업 디렉토리
    if any(kw in q for kw in ["작업 디렉토리", "현재 작업 디렉토리", "현재 디렉토리", "cwd", "current directory", "working directory"]):
        return "get_cwd"
    
    # 시스템 정보
    if any(kw in q for kw in ["시스템 정보", "시스템 상태", "시스템 조회", "system info", "system information"]):
        return "get_system_snapshot"
    
    # 메모리/디스크 (시스템 정보로 매핑)
    if any(kw in q for kw in ["메모리", "디스크", "memory", "disk", "시스템 메모리", "디스크 사용량"]):
        return "get_system_snapshot"
    
    # 프로세스 목록
    if any(kw in q for kw in ["프로세스 목록", "프로세스", "process", "processes", "실행 중인 프로세스"]):
        return "list_processes"
    
    # 파일/디렉토리 목록 (list_directory는 경량 도구가 아니지만 명확한 라우팅 히트)
    if any(kw in q for kw in ["파일 목록", "디렉토리 목록", "현재 디렉토리의 파일", "list files", "list directory"]):
        if "프로세스" not in q:  # 프로세스 목록과 구분
            return "list_directory"
    
    return None


def _cap_observation_size(observation: Any, max_chars: int = MAX_OBSERVATION_SIZE) -> str:
    """
    Observation 크기를 제한하여 프롬프트 블로트 방지.

    Args:
        observation: 원본 observation (str, dict, list 등)
        max_chars: 최대 문자 수 (기본값: MAX_OBSERVATION_SIZE)
    
    Returns:
        크기 제한된 문자열 (dict/list는 JSON 직렬화 후 제한)
    """
    try:
        from mellow_link.config import get_settings
        settings = get_settings()
        max_chars = getattr(settings, "obs_max_chars", max_chars)
    except Exception as e:
        logger.debug("[AgentBrain] get_settings for obs_max_chars failed, checking env: %s", e)
        import os
        env_value = os.getenv("OBS_MAX_CHARS", "").strip()
        if env_value:
            try:
                max_chars = int(env_value)
            except ValueError as e:
                logger.debug("[AgentBrain] MELLOW_MAX_OBSERVATION_CHARS invalid, using default: %s", e)
    
    # dict/list인 경우 JSON 직렬화
    if isinstance(observation, (dict, list)):
        try:
            obs_str = json.dumps(observation, ensure_ascii=False, default=str)
        except Exception as e:
            logger.debug("[AgentBrain] Observation JSON serialize failed, using str(): %s", e)
            obs_str = str(observation)
    else:
        obs_str = str(observation)
    
    original_len = len(obs_str)
    
    # 크기 제한 적용
    if original_len <= max_chars:
        return obs_str
    
    # 초과 시 잘라내고 마커 추가
    truncated = obs_str[:max_chars]
    marker = f"\n[TRUNCATED_OBS] original_len={original_len}"
    # 마커를 포함해도 max_chars를 넘지 않도록 조정
    available_chars = max_chars - len(marker)
    if available_chars > 0:
        truncated = obs_str[:available_chars] + marker
    else:
        truncated = marker
    
    return truncated

# ═══════════════════════════════════════════════
# 하위 호환성: 분리된 모듈에서 re-export
# (이 파일에서 직접 import하는 외부 코드를 위해 유지)
# ═══════════════════════════════════════════════
# NOTE: AgentAction, AgentStep, AgentResult, BASE_PATH, build_system_prompt,
#       parse_action 등은 상단 import로 이 네임스페이스에 존재합니다.
#       `from mellow_link.core.agent_brain import AgentResult` 등 기존 코드 호환.


# --- 이하 삭제된 코드는 각 모듈로 이동 ---
# 경로 유틸: agent_path_utils.py
# 데이터 구조: agent_schemas.py
# 시스템 프롬프트: agent_prompts.py
# 출력 파서: agent_parsers.py
# 경험 관리: agent_experience.py


# ═══════════════════════════════════════════════
# Agent Brain (ReAct Loop)
# ═══════════════════════════════════════════════

class AgentBrain:
    """
    LLM과 ToolRegistry를 연결하는 ReAct 사고 엔진.

    사용법:
        brain = AgentBrain(llm_service=..., max_turns=10)
        result = await brain.run("workspace 파일 목록을 보여줘")
    """

    def __init__(
        self,
        llm_service,
        *,
        max_turns: int = 10,
        model_mode: str = "thinking",
        context_window: int = 20,
        enable_memory_archiving: bool = True,
        enable_experience_retrieval: bool = True,
        analysis_interval: int = 10,
    ):
        """
        Args:
            llm_service: LLMService 인스턴스 (chat 메서드 필요).
            max_turns: 최대 ReAct 루프 횟수 (무한 루프 방지).
            model_mode: LLM 모드 ("fast", "thinking", "research").
            context_window: 히스토리 최대 메시지 수.
            enable_memory_archiving: 경험 메모리 아카이빙 활성화 여부.
            enable_experience_retrieval: 경험 소환 기능 활성화 여부.
            analysis_interval: ActionLogAnalyzer 실행 주기 (N회 태스크 완료마다, 기본 10).
        """
        self._llm = llm_service
        self._max_turns = max_turns
        self._model_mode = model_mode
        self._context_window = context_window
        self._enable_memory_archiving = enable_memory_archiving
        self._enable_experience_retrieval = enable_experience_retrieval

        # 도구 등록 (import 시 자동 등록됨) + 동적 도구 레지스트리 (Phase 4)
        import mellow_link.core.agent_tools  # noqa: F401
        from mellow_link.core.dynamic_registry import get_dynamic_registry
        self._registry = get_dynamic_registry()
        
        # 아카이버 초기화 (지연 로딩)
        self._archiver = None
        if self._enable_memory_archiving:
            try:
                from mellow_link.infra.archiver import get_archiver
                self._archiver = get_archiver(llm_service=llm_service)
            except Exception as e:
                logger.warning(f"[AgentBrain] Failed to initialize archiver: {e}")
                self._enable_memory_archiving = False
        
        # 경험 제공자 초기화 (지연 로딩)
        self._experience_provider = None
        if self._enable_experience_retrieval:
            try:
                from mellow_link.core.experience_provider import get_experience_provider
                self._experience_provider = get_experience_provider()
            except Exception as e:
                logger.warning(f"[AgentBrain] Failed to initialize experience provider: {e}")
                self._enable_experience_retrieval = False
        
        # 체크포인트 매니저 초기화 (지연 로딩)
        self._checkpoint_manager = None
        try:
            from mellow_link.core.checkpoint_manager import get_checkpoint_manager
            self._checkpoint_manager = get_checkpoint_manager()
        except Exception as e:
            logger.warning(f"[AgentBrain] Failed to initialize checkpoint manager: {e}")
        
        # 로그 분석기 초기화 (지연 로딩)
        self._log_analyzer = None
        try:
            from mellow_link.core.log_analyzer import get_log_analyzer
            self._log_analyzer = get_log_analyzer(llm_service=llm_service)
        except Exception as e:
            logger.warning(f"[AgentBrain] Failed to initialize log analyzer: {e}")

        # 경험 헬퍼 초기화 (아카이빙/분석/진단 위임)
        self._experience_helper = ExperienceHelper(
            archiver=self._archiver,
            log_analyzer=self._log_analyzer,
            experience_provider=self._experience_provider,
            llm_service=llm_service,
            enable_memory_archiving=self._enable_memory_archiving,
            analysis_interval=analysis_interval,
            diagnosis_interval=50,
        )

    def _should_escalate_fast_tool_call(self, tool_name: str) -> bool:
        """
        Decide whether a FAST-mode tool call should escalate to THINKING.

        Lightweight inspection tools should stay on FAST to avoid promoting simple
        file discovery into the heavier 9B path.
        """
        lightweight_tools = {
            "list_directory",
            "read_file",
            "read_docs_file",
            "list_docs",
            "find_files",
            "glob_search",
            "search_files",
            "get_file_info",
            "get_cwd",
            "get_time",
            "get_system_snapshot",
            "list_processes",
            "security_status",
            "list_tools",
            "inspect_system_status",
            "search_memory",
            "get_user_feedback",
            "get_my_work_history",
        }
        heavy_reasoning_tools = {
            "run_command",
            "generate_report",
            "propose_new_tool",
            "analyze_text",
            "web_search",
            "create_image",
            "animate_image",
            "write_file",
            "move_file",
            "delete_file",
            "cleanup_file",
            "create_directory",
        }
        if tool_name in lightweight_tools:
            return False
        if tool_name in heavy_reasoning_tools:
            return True
        return False

    # ──────────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────────

    async def run(
        self,
        user_input: str,
        context: Optional[List[Dict[str, str]]] = None,
        persona: str = "",
        session_id: Optional[str] = None,
        require_at_least_one_tool: bool = True,
        root_goal_id: Optional[str] = None,
        mode: str = "fast",
        session_state: Optional[Dict[str, Any]] = None,
        is_admin: bool = False,
    ) -> AgentResult:
        """
        ReAct 루프 실행.

        Args:
            user_input: 사용자 입력.
            context: 이전 대화 히스토리 (없으면 빈 리스트).
            persona: 페르소나 텍스트.
            session_id: 세션 ID (체크포인트 복구/저장용, 선택사항).
            require_at_least_one_tool: True이면 finish 호출 전 최소 1회 유효한 도구 호출+Observation 필수 (보통 mode가 thinking/research일 때만).
            mode: "fast" | "thinking" | "research" (prompt template / strict observation 시 사용).
            session_state: Orchestrator가 넘긴 세션 스코프 상태(있으면 fast_fallback_used 등 요청 간 유지). 없으면 run 단위(기존 동작).
            is_admin: Admin 사용자 여부 (Admin persona tone 허용).

        Returns:
            AgentResult with final answer and step history.
        """
        # ✅ verified: 경험 장부 훅 — 성공/실패 무관 try..finally에서 기록
        start_time = datetime.now()
        run_state: Dict[str, Any] = {"result": None, "error": None, "fast_fallback_used": False}
        # Fallback tracking: session-scoped if session_state provided, else run-scoped
        fallback_state: Dict[str, Any] = session_state if session_state is not None else run_state
        steps: List[AgentStep] = []
        
        # Run ID 가져오기 (session_state에서 또는 None)
        run_id = None
        if session_state:
            run_id = session_state.get("run_id")
        if not run_id and hasattr(self, "_current_run_id"):
            run_id = self._current_run_id
        
        # 이벤트 발행 헬퍼 (run_id가 있을 때만)
        # 모듈 레벨 import로 성능 개선
        try:
            from mellow_link.infra.run_events import emit_event as _emit_event
        except ImportError:
            _emit_event = None
        
        def emit_if_enabled(event_type: str, payload: Dict[str, Any]):
            if run_id and _emit_event:
                try:
                    _emit_event(run_id, event_type, payload)
                except Exception as e:
                    logger.debug(f"[AgentBrain] Failed to emit event {event_type}: {e}")
        
        # plan_only: "계획만/실행하지 마/먼저 계획" 요청 시 실행 단계(T3) 진입 금지
        from mellow_link.core.output_sanitizer import is_plan_only, is_execution_approval
        plan_only = bool(session_state.get("plan_only")) if session_state else False
        plan_only = plan_only or is_plan_only(user_input)
        if session_state and is_execution_approval(user_input):
            session_state["plan_approved"] = True
        plan_only = plan_only and not bool(session_state.get("plan_approved") if session_state else False)
        if plan_only and run_id:
            logger.info("[AgentBrain] plan_only=true: T3 도구 실행 루프 진입 안 함, 계획 생성 후 즉시 종료")

        # run_started 이벤트 및 Run 컨텍스트 (Guardian NEED_AI_REVIEW 시 승인 대기용)
        if run_id:
            try:
                from mellow_link.infra.run_context import set_run_context
                set_run_context(run_id, None)
            except ImportError:
                pass
            emit_if_enabled("run_started", {
                "user_input": user_input[:LOG_TRUNCATE_LEN],
                "mode": mode,
                "session_id": session_id,
                "plan_only": plan_only,
            })

        # ── VRAM_SELF_KILL: VRAM 95% 초과 시 자동 종료 및 GC ──
        try:
            vram_status = await self._check_vram_and_kill_if_critical()
            if vram_status == "KILLED":
                # Self-Kill이 발생했으면 즉시 종료
                # Output sanitization은 VRAM 크리티컬 메시지에는 불필요 (시스템 메시지)
                return AgentResult(
                    answer="[VRAM CRITICAL] VRAM 사용량이 95%를 초과하여 프로세스를 안전하게 종료했습니다. 가비지 컬렉션을 수행했습니다.",
                    steps=steps,
                    total_turns=0,
                    finish_reason="vram_critical_self_kill",
                    recovery_success=False,
                    total_infer_ms=run_state.get("total_infer_ms", 0.0),
                )
        except Exception as e:
            logger.warning(f"[VRAM_SELF_KILL] VRAM 체크 실패 (계속 진행): {e}")

        try:
            # 세션 ID 생성 (없으면 자동 생성)
            if session_id is None:
                import uuid
                session_id = str(uuid.uuid4())
            run_state["session_id"] = session_id
            # 0-0. 체크포인트 복구 (중단된 세션이 있는지 확인)
            restored_steps: List[AgentStep] = []
            resume_from_step = 0
            is_resuming = False
            restored_max_turns = None
            restored_task_intent = None

            if self._checkpoint_manager:
                try:
                    # CheckpointManager.load_checkpoint()는 security_violation 시 None 반환하여 이미 차단함
                    checkpoint = self._checkpoint_manager.load_checkpoint(session_id)
                    if checkpoint and checkpoint.get("status") in ("RUNNING", "PAUSED"):
                        restored_steps = self._checkpoint_manager.restore_history(checkpoint)
                        resume_from_step = checkpoint.get("current_step", 0)
                        restored_max_turns = checkpoint.get("original_max_turns")
                        restored_task_intent = checkpoint.get("task_intent", user_input)
                        is_resuming = True
                        logger.info(
                            f"[AgentBrain] Resuming session {session_id} from step {resume_from_step} "
                            f"(status: {checkpoint.get('status')}, original_max_turns: {restored_max_turns})"
                        )
                except Exception as e:
                    logger.warning(f"[AgentBrain] Failed to load checkpoint: {e}")

            # 0-1. 복잡도 평가 및 적응형 턴 제한 계산
            # 복구 중이면 체크포인트에 저장된 max_turns를 우선 사용
            if is_resuming and restored_max_turns is not None:
                dynamic_max_turns = restored_max_turns
                logger.info(f"[AgentBrain] Using restored max_turns: {dynamic_max_turns}")
            else:
                # 새 세션이면 복잡도 평가 수행
                dynamic_max_turns = self._max_turns  # 기본값은 생성자에서 설정된 값
                past_failure_bonus = 0

                try:
                    from mellow_link.core.complexity_evaluator import get_complexity_evaluator

                    # 사용 가능한 도구 개수 확인
                    try:
                        available_tools_count = len(self._registry._tools) if hasattr(self._registry, '_tools') else 0
                    except Exception as e:
                        logger.debug("[AgentBrain] Failed to get available_tools_count, using 0: %s", e)
                        available_tools_count = 0

                    # 경험 메모리에서 과거 실패 기록 확인 (Optional)
                    if self._enable_experience_retrieval and self._experience_provider:
                        try:
                            from mellow_link.infra.memory_database import MemoryDatabase
                            context_summary = self._experience_helper.build_context_summary(context, persona)
                            task_hash = MemoryDatabase.compute_task_hash(user_input, context_summary)
                            past_experiences = self._experience_provider.retrieve_relevant_experiences(
                                task_intent=user_input,
                                task_hash=task_hash,
                                limit=5
                            )
                            failure_count = sum(1 for exp in past_experiences if exp.is_success == 0)
                            if failure_count > 0:
                                past_failure_bonus = 5
                                logger.info(f"[AgentBrain] 과거 실패 기록 {failure_count}건 발견, 턴 수 +5 추가")
                        except Exception as e:
                            logger.debug(f"[AgentBrain] Failed to check past failures: {e}")

                    evaluator = get_complexity_evaluator()
                    dynamic_max_turns = evaluator.calculate_limit(
                        user_input=user_input,
                        available_tools_count=available_tools_count,
                        past_failure_bonus=past_failure_bonus
                    )
                    logger.info(
                        f"[AgentBrain] 이번 작업의 난이도를 고려하여 {dynamic_max_turns}턴을 할당합니다. "
                        f"(기본: {self._max_turns}, 복잡도: {evaluator.evaluate_complexity_level(user_input)})"
                    )
                except Exception as e:
                    logger.warning(f"[AgentBrain] Failed to evaluate complexity, using default {self._max_turns} turns: {e}")
                    dynamic_max_turns = self._max_turns

            # 0-0. Plan intent 감지 및 plan_created 이벤트 발행
            from mellow_link.core.output_sanitizer import detect_plan_intent
            is_plan_request = detect_plan_intent(user_input)
            if is_plan_request and run_id:
                # Plan 요청 시 plan_created 이벤트 강제 발행
                plan_todos = [
                    {"todo_id": "T1", "title": "요청 파싱", "status": "pending"},
                    {"todo_id": "T2", "title": "웹 검색 (필요시)", "status": "pending"},
                    {"todo_id": "T3", "title": "비교/분석", "status": "pending"},
                    {"todo_id": "T4", "title": "답변 초안 작성", "status": "pending"},
                    {"todo_id": "T5", "title": "요약 및 완료", "status": "pending"},
                    {"todo_id": "T6", "title": "메트릭 저장", "status": "pending"},
                    {"todo_id": "T7", "title": "완료", "status": "pending"},
                ]
                from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED
                emit_if_enabled(EVENT_TYPE_PLAN_CREATED, {"todos": plan_todos})
                logger.info("[PLAN_CREATED] emitted todos=%d", len(plan_todos))
                logger.info(f"[AgentBrain] Plan intent detected -> plan_created event emitted")
            
            # 0-1. Todo 시작 (T1: 요청 파싱)
            if run_id:
                try:
                    from mellow_link.infra.run_context import set_run_context
                    set_run_context(run_id, "T1")
                except ImportError:
                    pass
            emit_if_enabled("todo_started", {"todo_id": "T1", "title": "요청 파싱"})
            
            # 0-2. 경험 소환 (과거 성공/실패 사례 검색)
            # FAST 모드에서는 경험 어드바이저리 비활성화 (프롬프트 크기 최적화)
            experience_advisory = ""
            task_hash = None
            effective_mode = (mode or getattr(self, "_model_mode", "fast") or "fast").strip().lower()
            
            # Todo 완료 (T1) 및 시작 (T2: 모드 선택)
            emit_if_enabled("todo_done", {"todo_id": "T1", "title": "요청 파싱", "status": "completed"})
            if run_id:
                try:
                    from mellow_link.infra.run_context import set_run_context
                    set_run_context(run_id, "T2")
                except ImportError:
                    pass
            emit_if_enabled("todo_started", {"todo_id": "T2", "title": "모드 선택"})
            if effective_mode != "fast" and self._enable_experience_retrieval and self._experience_provider:
                try:
                    from mellow_link.infra.memory_database import MemoryDatabase
                    if task_hash is None:
                        task_hash = MemoryDatabase.compute_task_hash(
                            user_input,
                            self._experience_helper.build_context_summary(context, persona)
                        )
                    experience_advisory = self._experience_provider.get_experience_advisory(
                        task_intent=user_input,
                        task_hash=task_hash,
                        limit=3
                    )
                    if experience_advisory:
                        logger.info("[AgentBrain] Retrieved past experiences for guidance")
                except Exception as e:
                    logger.warning(f"[AgentBrain] Failed to retrieve experiences: {e}")
            elif effective_mode == "fast":
                logger.debug("[AgentBrain] FAST mode: experience_advisory disabled for prompt size optimization")

            # 1. 시스템 프롬프트 구성 (mode별 미니 템플릿은 MELLOW_PROMPT_TEMPLATE_MODE=1 시)
            # effective_mode는 위에서 이미 계산됨 (experience_advisory에서)
            tools_json = self._registry.get_tools_prompt()
            recent_for_prompt = None
            if context and isinstance(context, list):
                recent_for_prompt = context[-10:]  # last 10 messages; builder will cap by history_max_turns
            
            # Long-form Output Policy: 확장 요청 감지 (Progressive Disclosure)
            expansion_level = _get_expansion_level(user_input) if user_input else 0
            force_expanded = expansion_level >= 1
            
            # thinking-lite 모드 감지
            is_thinking_lite = effective_mode == "thinking-lite"
            
            if force_expanded:
                logger.info(f"[AgentBrain] Expansion request detected (level={expansion_level}), OUTPUT_POLICY will use expansion template and max_tokens increased")
            if is_thinking_lite:
                logger.info("[AgentBrain] Thinking-lite mode detected, OUTPUT_POLICY will use thinking-lite template and tool calls capped at 1")
            
            # Pass registry for thinking mode compact summary
            system_prompt = build_system_prompt(
                tools_json,
                persona=persona,
                mode=effective_mode,
                recent_history=recent_for_prompt,
                use_template_mode=None,  # read from settings
                registry=self._registry if effective_mode in ("thinking", "thinking-lite") else None,
                user_input=user_input,
                force_expanded=force_expanded,
                expansion_level=expansion_level,
                is_thinking_lite=is_thinking_lite,
            )
            
            # Todo 완료 (T2: 모드 선택)
            emit_if_enabled("todo_done", {
                "todo_id": "T2",
                "title": "모드 선택",
                "status": "completed",
                "detail": f"Selected mode: {effective_mode}",
            })
            
            # Ollama Native Tool Calling을 위한 도구 스키마 준비
            # FAST 모드에서는 도구 스키마 비활성화 (프롬프트 크기 최적화)
            # 단, 에스컬레이션 후에는 THINKING 모드이므로 도구 스키마 활성화
            if effective_mode == "fast":
                tools_schema = []  # FAST 모드에서는 빈 리스트
                logger.debug("[AgentBrain] FAST mode: tools_schema disabled for prompt size optimization")
            else:
                tools_schema = self._registry.get_tools_schema()
            if plan_only:
                tools_schema = []
                logger.debug("[AgentBrain] plan_only: 도구 스키마 비활성화 (T3 미진입)")

            # ⚠️ 중요: 프롬프트 크기 확인
            system_prompt_length = len(system_prompt)
            tools_json_length = len(tools_json)
            experience_advisory_length = len(experience_advisory) if experience_advisory else 0

            # FAST 모드 전용 상세 디버그 로그
            if effective_mode == "fast":
                history_chars = sum(len(str(m.get('content', ''))) for m in (recent_for_prompt or []))
                tools_schema_count = len(tools_schema) if tools_schema else 0
                logger.info(
                    f"[AgentBrain] FAST mode prompt composition: "
                    f"system_prompt={system_prompt_length:,} chars, "
                    f"experience_advisory={experience_advisory_length:,} chars, "
                    f"history={history_chars:,} chars, "
                    f"tools_schema_count={tools_schema_count}"
                )
            else:
                logger.info(f"[AgentBrain] System prompt length: {system_prompt_length:,} chars, Tools JSON: {tools_json_length:,} chars")

            # 프롬프트가 너무 길면 경고
            if system_prompt_length > MAX_SYSTEM_PROMPT_CHARS:
                logger.warning(
                    f"[AgentBrain] ⚠️ 시스템 프롬프트가 매우 깁니다 ({system_prompt_length:,} chars). "
                    "Ollama가 처리하지 못할 수 있습니다."
                )
            
            # ⚠️ 중요: 도구 스키마 크기 확인
            if tools_schema:
                schema_json = json.dumps(tools_schema, ensure_ascii=False)
                schema_length = len(schema_json)
                logger.info(f"[AgentBrain] Tools schema count: {len(tools_schema)}, JSON size: {schema_length:,} chars")
                
                # 도구 스키마가 너무 크면 경고
                if schema_length > MAX_TOOLS_SCHEMA_CHARS:
                    logger.warning(
                        f"[AgentBrain] ⚠️ 도구 스키마가 매우 큽니다 ({schema_length:,} chars). "
                        "일부 도구를 제외하거나 스키마를 단순화해야 할 수 있습니다."
                    )
                
                tool_names = [t.get("function", {}).get("name", "unknown") for t in tools_schema[:10]]
                logger.info(f"[AgentBrain] Tools: {', '.join(tool_names)}{'...' if len(tools_schema) > 10 else ''}")
            else:
                if effective_mode != "fast":
                    logger.warning("[AgentBrain] ⚠️ 도구 스키마가 비어있습니다!")
            
            if experience_advisory:
                # Registry Validation: 과거 경험 텍스트에 현재 존재하지 않는 도구명이 있으면 경고 문구 삽입
                valid_tool_names = self._registry.get_tool_names()
                experience_advisory = _validate_experience_advisory_and_append_disclaimer(
                    experience_advisory, valid_tool_names
                )
                system_prompt = f"{experience_advisory}\n\n{system_prompt}"

            # 2. 히스토리 초기화
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": system_prompt},
            ]
            
            # ⚠️ 중요: 첫 메시지 확인
            logger.info(f"[AgentBrain] First user message length: {len(user_input)} chars")
            logger.info(f"[AgentBrain] Total messages count: {len(messages)}")
            
            # 전체 메시지 크기 계산
            total_message_size = sum(len(str(m.get('content', ''))) for m in messages)
            logger.info(f"[AgentBrain] Total message content size: {total_message_size:,} chars")
            
            # 메시지가 너무 크면 경고
            if total_message_size > 100000:  # 100KB 이상
                logger.warning(
                    f"[AgentBrain] ⚠️ 전체 메시지 크기가 매우 큽니다 ({total_message_size:,} chars). "
                    "Ollama가 처리하지 못할 수 있습니다."
                )
            # Auto docs: strict trigger, allowlist, quota, cooldown, cache
            # Only enabled in thinking/research modes (not fast)
            auto_docs_injected = False
            if is_resuming and restored_steps:
                original_input = restored_task_intent if restored_task_intent else user_input
                messages.append({"role": "user", "content": original_input})
                for step in restored_steps:
                    if step.thought:
                        messages.append({"role": "assistant", "content": step.thought})
                    if step.observation:
                        messages.append({"role": "user", "content": f"[Observation] {step.observation}"})
            else:
                if context:
                    messages.extend(context[-self._context_window:])
                messages.append({"role": "user", "content": user_input})
                try:
                    from mellow_link.core.agent_docs_auto import try_auto_read_docs
                    auto_docs = try_auto_read_docs(session_id, user_input, mode=mode)
                    if auto_docs:
                        messages.append({"role": "system", "content": auto_docs})
                        auto_docs_injected = True
                except Exception as e:
                    logger.debug("[AgentBrain] agent_docs_auto failed: %s", e)

            # 복구된 스텝이 있으면 사용, 없으면 빈 리스트 (경험 장부 훅에서 사용)
            steps = restored_steps.copy() if restored_steps else []
            retry_count = 0
            max_retries = 2
            recovery_attempts_this_run = 0
            recovery_success_used = False
            context_summary = self._experience_helper.build_context_summary(context, persona)

            # PATCH: 라우팅 히트 최적화 - 단일 도구 호출 후 즉시 finish
            routing_tool = _is_routing_hit(user_input)
            if routing_tool:
                logger.info(f"[AgentBrain] Routing hit detected: {routing_tool} for query: {user_input[:50]}...")
                try:
                    # 단일 도구만 호출
                    tool_args = {}
                    if routing_tool == "list_processes":
                        tool_args = {"limit": 20, "offset": 0}
                    
                    observation = await self._registry.execute(routing_tool, tool_args)
                    observation = _cap_observation_size(observation)
                    
                    # 즉시 finish (도구 결과를 최종 답변으로 사용)
                    raw_summary = observation
                    
                    step = AgentStep(turn=1, thought=f"[Routing Hit] {routing_tool} called",
                                   action=AgentAction(tool=routing_tool, args=tool_args),
                                   observation=observation)
                    steps.append(step)

                    # Final render: sanitization + persona styling (admin only)
                    try:
                        from mellow_link.core.output_sanitizer import render_final_answer
                        active_persona_id = None
                        if persona and ("aventurine" in persona.lower() or "에브" in persona or "eve" in persona.lower()):
                            active_persona_id = "aventurine"
                        summary = render_final_answer(
                            raw_summary,
                            is_admin=is_admin,
                            persona_id=active_persona_id,
                            mode=effective_mode,
                            llm_service=self._llm if is_admin else None  # Admin일 때만 LLM 사용
                        )
                    except Exception as e:
                        logger.warning(f"[AgentBrain] Final render failed (routing_hit): {e}")
                        summary = raw_summary  # 실패 시 원본 사용

                    result = AgentResult(
                        answer=summary,
                        steps=steps,
                        total_turns=1,
                        finish_reason="routing_hit",
                        recovery_success=False,
                        total_infer_ms=run_state.get("total_infer_ms", 0.0),
                    )
                    run_state["result"] = result
                    
                    # BENCH_PROFILE이 아닐 때만 경험 아카이빙
                    try:
                        from mellow_link.config import get_settings
                        settings = get_settings()
                        bench_profile = getattr(settings, "bench_profile", False) or os.getenv("BENCH_PROFILE", "").strip().lower() in ("1", "true", "yes")
                    except Exception as e:
                        logger.debug("[AgentBrain] Failed to read bench_profile, assuming False: %s", e)
                        bench_profile = False
                    
                    if not bench_profile:
                        await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                    else:
                        logger.debug("[AgentBrain] BENCH_PROFILE mode: skipping experience archiving")
                    
                    return result
                except Exception as e:
                    logger.warning(f"[AgentBrain] Routing hit tool execution failed: {e}, falling back to normal flow")
                    # 실패 시 정상 플로우로 폴백

            # PATCH A: FAST 모드 max_turns를 2로 제한
            if effective_mode == "fast":
                effective_max_turns = 2
                logger.info(f"[AgentBrain] FAST mode: max_turns capped at 2 (was {dynamic_max_turns})")
            # PATCH: thinking-lite 모드 max_turns를 2로 제한 (도구 호출 1개 + finish)
            elif is_thinking_lite:
                effective_max_turns = 2
                logger.info(f"[AgentBrain] THINKING-LITE mode: max_turns capped at 2 (was {dynamic_max_turns})")
            else:
                effective_max_turns = dynamic_max_turns
            
            start_turn = resume_from_step + 1 if is_resuming else 1
            
            # thinking-lite 모드: 도구 호출 횟수 추적
            tool_call_count = 0
            max_tool_calls_lite = 1  # thinking-lite 모드에서 최대 1개 도구 호출
            
            # FAST 모드 에스컬레이션 추적 (요청당 1회만)
            fast_escalated = False
            
            # 라우팅 히트 플래그 (analyze_text 차단용)
            is_routing_hit_mode = routing_tool is not None
            
            # 빈 응답 연속 발생 카운터 (무한 루프 방지)
            consecutive_empty_responses = 0
            max_consecutive_empty = 3  # 연속 3회 빈 응답 시 종료

            # Observation-first: 최대 1회 재프롬프트 후 observation_required_not_met 종료
            observation_reject_count = 0

            # [PLAN_ONLY] T3(도구 실행 루프) 진입 금지: 계획 생성 후 즉시 종료
            if plan_only:
                plan_todos_ack = [
                    {"todo_id": "T1", "title": "요청 파싱", "status": "pending"},
                    {"todo_id": "T2", "title": "웹 검색 (필요시)", "status": "pending"},
                    {"todo_id": "T3", "title": "비교/분석", "status": "pending"},
                    {"todo_id": "T4", "title": "답변 초안 작성", "status": "pending"},
                    {"todo_id": "T5", "title": "요약 및 완료", "status": "pending"},
                    {"todo_id": "T6", "title": "메트릭 저장", "status": "pending"},
                    {"todo_id": "T7", "title": "완료", "status": "pending"},
                ]
                if run_id:
                    from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED
                    emit_if_enabled(EVENT_TYPE_PLAN_CREATED, {"todos": plan_todos_ack})
                if session_state is not None and isinstance(session_state, dict):
                    session_state["pending_plan"] = True
                steps.append(AgentStep(
                    turn=0,
                    thought="[Plan] 계획 생성됨 (T1~T7)",
                    action=AgentAction(tool="plan_created", args={}),
                    observation="계획이 생성되었습니다.",
                ))
                if run_id:
                    emit_if_enabled("run_finished", {"success": True, "summary": "계획이 생성되었습니다."})
                if self._checkpoint_manager:
                    try:
                        self._checkpoint_manager.clear_checkpoint(session_id, mark_completed=True)
                    except Exception as e:
                        logger.debug(f"[AgentBrain] Failed to clear checkpoint: {e}")
                result = AgentResult(
                    answer="계획이 생성되었습니다.",
                    steps=steps,
                    total_turns=0,
                    finish_reason="plan_created_ack",
                    recovery_success=False,
                    total_infer_ms=run_state.get("total_infer_ms", 0.0),
                )
                run_state["result"] = result
                await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                return result

            # Todo 시작 (T3: 도구 실행) — Guardian NEED_AI_REVIEW 시 이 todo에서 대기
            if run_id:
                try:
                    from mellow_link.infra.run_context import set_run_context
                    set_run_context(run_id, "T3")
                except ImportError:
                    pass
            emit_if_enabled("todo_started", {"todo_id": "T3", "title": "도구 실행 (필요시)"})
            
            # [STANDARD_TOOL_CALLING] while True 루프로 변경하되 max_turns는 유지
            turn = start_turn - 1
            run_control = session_state.get("run_control") if isinstance(session_state, dict) else None
            pause_logged = False
            while turn < effective_max_turns:
                # Operator control: pause/resume/abort (run_control은 runs.py에서 주입)
                if isinstance(run_control, dict):
                    if run_control.get("abort_requested"):
                        emit_if_enabled("run_finished", {
                            "success": False,
                            "finish_reason": "operator_abort",
                            "summary": "Run aborted by operator",
                        })
                        result = AgentResult(
                            answer="운영자 요청으로 실행이 중단되었습니다.",
                            steps=steps,
                            total_turns=turn,
                            finish_reason="operator_abort",
                            recovery_success=False,
                            total_infer_ms=run_state.get("total_infer_ms", 0.0),
                        )
                        run_state["result"] = result
                        await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                        return result
                    while run_control.get("paused"):
                        if not pause_logged:
                            emit_if_enabled("log", {
                                "level": "info",
                                "message": "Run paused by operator",
                            })
                            pause_logged = True
                        await asyncio.sleep(0.5)
                        if run_control.get("abort_requested"):
                            break
                    if run_control.get("abort_requested"):
                        continue
                    if pause_logged and not run_control.get("paused"):
                        emit_if_enabled("log", {
                            "level": "info",
                            "message": "Run resumed by operator",
                        })
                        pause_logged = False

                turn += 1
                
                # 로그 이벤트 (중요한 단계)
                emit_if_enabled("log", {
                    "level": "info",
                    "message": f"Turn {turn}/{effective_max_turns}",
                })
                # ── VRAM_SELF_KILL: 각 턴 시작 전 VRAM 체크 ──
                try:
                    vram_status = await self._check_vram_and_kill_if_critical()
                    if vram_status == "KILLED":
                        # Self-Kill이 발생했으면 즉시 종료
                        logger.critical(f"[VRAM_SELF_KILL] Turn {turn}에서 Self-Kill 발생. 루프 종료.")
                        return AgentResult(
                            answer=f"[VRAM CRITICAL] Turn {turn}에서 VRAM 사용량이 95%를 초과하여 프로세스를 안전하게 종료했습니다. 가비지 컬렉션을 수행했습니다.",
                            steps=steps,
                            total_turns=turn - 1,
                            finish_reason="vram_critical_self_kill",
                            recovery_success=False,
                            total_infer_ms=run_state.get("total_infer_ms", 0.0),
                        )
                except Exception as e:
                    logger.warning(f"[VRAM_SELF_KILL] Turn {turn} VRAM 체크 실패 (계속 진행): {e}")
                
                # ── 마지막 턴 경고: finish 도구를 호출하도록 유도 ──
                if turn == effective_max_turns:
                    final_turn_warning = (
                        "\n[중요] 이것은 마지막 턴입니다. "
                        "작업을 완료하려면 finish 도구를 호출하거나, tool_calls 없이 응답하여 자동 종료할 수 있습니다. "
                        "finish 도구를 사용하면 최종 요약을 제공할 수 있습니다."
                    )
                    messages.append({"role": "user", "content": final_turn_warning})
                
                # ── THINK: LLM 호출 ──
                # Log debug info before LLM call (only for first turn)
                if turn == 1:
                    self._log_llm_call_debug(effective_mode, messages, auto_docs_injected)
                # prompt_stats 이벤트 (Dev Console Raw Data 탭용)
                try:
                    prompt_text = "\n".join([m.get("content", "") for m in messages])
                    prompt_chars = len(prompt_text)
                    system_msg = next((m for m in messages if m.get("role") == "system"), None)
                    system_chars = len(system_msg.get("content", "")) if system_msg else 0
                    user_chars = sum(len(m.get("content", "")) for m in messages if m.get("role") == "user")
                    assistant_chars = sum(len(m.get("content", "")) for m in messages if m.get("role") == "assistant")
                    num_ctx = None
                    if hasattr(self._llm, "get_context_size"):
                        try:
                            num_ctx = self._llm.get_context_size()
                        except Exception as e:
                            logger.debug("[AgentBrain] get_context_size() failed: %s", e)
                    elif hasattr(self._llm, "_context_size"):
                        num_ctx = getattr(self._llm, "_context_size", None)
                    emit_if_enabled("prompt_stats", {
                        "turn": turn,
                        "estimated_tokens": prompt_chars // 4,
                        "system_chars": system_chars,
                        "user_chars": user_chars,
                        "assistant_chars": assistant_chars,
                        "num_ctx": num_ctx,
                    })
                except Exception as e:
                    logger.debug("[AgentBrain] prompt_stats emit failed: %s", e)
                # Max tokens 결정 (THINKING/THINKING-LITE 모드에서만 적용)
                max_tokens = self._determine_max_tokens(
                    effective_mode=effective_mode,
                    user_input=user_input,
                    force_expanded=force_expanded,
                    expansion_level=expansion_level,
                    is_thinking_lite=is_thinking_lite,
                )
                
                llm_response, tool_calls, infer_ms = await self._call_llm(
                    messages,
                    tools=tools_schema,
                    session_state=fallback_state,
                    mode=effective_mode,
                    max_tokens=max_tokens,
                )
                # 누적 추론 시간 (ReAct 루프에서 여러 번 호출될 수 있음)
                if infer_ms is not None:
                    run_state.setdefault("total_infer_ms", 0.0)
                    run_state["total_infer_ms"] += infer_ms

                # [LLM_RESPONSE_VALIDATION] LLM 응답 검증
                # [NATIVE_TOOL_CALLING] tool_calls가 있으면 빈 텍스트도 유효한 응답
                has_tool_calls = tool_calls and len(tool_calls) > 0

                # ⚠️ 중요: tool_calls가 있으면 빈 텍스트도 정상 (Native Tool Calling)
                if has_tool_calls:
                    # Native Tool Calling 모드: text가 빈 문자열이어도 정상
                    if not llm_response or not isinstance(llm_response, str):
                        llm_response = ""
                    logger.info(f"[Turn {turn}] ✅ Native Tool Calling: {len(tool_calls)} tool_calls, text length: {len(llm_response)}")
                    consecutive_empty_responses = 0  # tool_calls가 있으면 리셋
                    retry_count = 0  # 성공 시 재시도 카운터도 리셋
                    # tool_calls가 있으면 빈 응답 체크를 건너뛰고 계속 진행 (아래 코드로)
                else:
                    # tool_calls가 없을 때만 빈 응답 체크
                    if not llm_response or not isinstance(llm_response, str):
                        logger.error(f"[Turn {turn}] LLM returned invalid response: type={type(llm_response)}, value={repr(llm_response)}")
                        consecutive_empty_responses += 1
                    elif len(llm_response.strip()) == 0:
                        logger.warning(f"[Turn {turn}] LLM returned empty response")
                        consecutive_empty_responses += 1
                    else:
                        # 정상 응답이면 빈 응답 카운터 리셋
                        consecutive_empty_responses = 0
                        retry_count = 0
                    
                    # 연속 빈 응답이 너무 많으면 종료 (tool_calls 없을 때만)
                    if consecutive_empty_responses >= max_consecutive_empty:
                        logger.error(
                            f"[Turn {turn}] 연속 {consecutive_empty_responses}회 빈 응답 발생 (tool_calls 없음). "
                            "Ollama 서버 또는 모델에 문제가 있을 수 있습니다. 루프를 종료합니다."
                        )
                        result = AgentResult(
                            answer=(
                                f"[오류] Ollama가 연속으로 빈 응답을 반환했습니다 ({consecutive_empty_responses}회). "
                                "Ollama 서버가 정상적으로 실행 중인지, 모델이 제대로 로드되었는지 확인해주세요. "
                                "해결 방법: 1) Ollama 서버 재시작, 2) 모델 재로드 (ollama run qwen2.5:7b), "
                                "3) 타임아웃 증가 확인"
                            ),
                            total_infer_ms=run_state.get("total_infer_ms", 0.0),
                            steps=steps,
                            total_turns=turn,
                            finish_reason="consecutive_empty_responses",
                            recovery_success=False,
                        )
                        run_state["result"] = result
                        await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                        return result
                    
                    # 빈 응답일 때 재시도 (최대 1회, tool_calls 없을 때만)
                    if consecutive_empty_responses > 0 and retry_count < max_retries:
                        retry_count += 1
                        logger.warning(f"[Turn {turn}] Retrying LLM call due to empty response (attempt {retry_count}/{max_retries})")
                        await asyncio.sleep(1)  # 짧은 대기 후 재시도
                        continue
                    elif consecutive_empty_responses > 0:
                        llm_response = ""  # 재시도 실패 시 빈 문자열로 처리
                
                logger.info("[Turn %d] LLM: %s", turn, (llm_response[:LOG_TRUNCATE_LEN] if llm_response else "(empty)"))
                
                # [NATIVE_TOOL_CALLING] Ollama Native Tool Calling 처리
                action = None
                if tool_calls and len(tool_calls) > 0:
                    # thinking-lite 모드: 도구 호출 제한 확인
                    if is_thinking_lite:
                        if tool_call_count >= max_tool_calls_lite:
                            logger.warning(
                                f"[AgentBrain] THINKING-LITE mode: Tool call limit reached ({tool_call_count}/{max_tool_calls_lite}). "
                                "Forcing finish tool call."
                            )
                            # finish 도구로 강제 전환
                            function_name = "finish"
                            tool_call = {
                                "function": {
                                    "name": "finish",
                                    "arguments": json.dumps({
                                        "summary": f"[THINKING-LITE] 도구 호출 제한에 도달했습니다. 지금까지의 분석 결과를 요약합니다:\n{llm_response[:500] if llm_response else '분석 완료'}"
                                    }, ensure_ascii=False)
                                }
                            }
                        else:
                            tool_call = tool_calls[0]
                            function_name = tool_call.get("function", {}).get("name", "")
                            tool_call_count += 1
                            logger.info(f"[AgentBrain] THINKING-LITE mode: Tool call {tool_call_count}/{max_tool_calls_lite}: {function_name}")
                    else:
                        # 일반 모드: 첫 번째 tool_call 사용
                        tool_call = tool_calls[0]
                        function_name = tool_call.get("function", {}).get("name", "")
                    function_args_str = tool_call.get("function", {}).get("arguments", "{}")
                    
                    try:
                        function_args = json.loads(function_args_str) if isinstance(function_args_str, str) else function_args_str
                        action = AgentAction(tool=function_name, args=function_args)
                        logger.info(f"[Turn {turn}] Native Tool Call: {function_name} with args: {function_args}")
                    except Exception as e:
                        logger.warning(f"[Turn {turn}] Failed to parse tool_call: {e}")
                        # 폴백: 기존 parse_action 사용
                        action = parse_action(llm_response)
                else:
                    # [STANDARD_TOOL_CALLING] tool_calls가 없으면 표준에 따라 자동 종료 고려
                    # 하지만 finish 도구를 명시적으로 호출하는 경우도 있으므로 텍스트에서 파싱 시도
                    # ── PARSE: 액션 추출 ──
                    # [LLM_RESPONSE_VALIDATION] 빈 응답일 때는 파싱 시도하지 않음
                    if not llm_response or len(llm_response.strip()) == 0:
                        action = None
                        logger.warning(f"[Turn {turn}] Skipping parse_action due to empty response")
                    else:
                        action = parse_action(llm_response)
                    
                    # [STANDARD_TOOL_CALLING] tool_calls가 없고 action도 None이면 자동 종료 (표준)
                    if action is None and not tool_calls:
                        # [PLAN_INTENT] Turn 1에서 plan_created는 이미 발행됨 → "No tool_calls"로 종료하지 않고 결정적 ack로 종료
                        if turn == 1 and is_plan_request:
                            steps.append(AgentStep(
                                turn=1,
                                thought="[Plan] 계획 생성됨 (T1~T7)",
                                action=AgentAction(tool="plan_created", args={}),
                                observation="계획이 생성되었습니다. (T1~T7)",
                            ))
                            short_ack = "계획이 생성되었습니다. 할 일 목록(T1~T7)을 확인해 주세요."
                            if self._checkpoint_manager:
                                try:
                                    self._checkpoint_manager.clear_checkpoint(session_id, mark_completed=True)
                                except Exception as e:
                                    logger.debug(f"[AgentBrain] Failed to clear checkpoint: {e}")
                            result = AgentResult(
                                answer=short_ack,
                                steps=steps,
                                total_turns=1,
                                finish_reason="plan_created_ack",
                                recovery_success=recovery_success_used,
                                total_infer_ms=run_state.get("total_infer_ms", 0.0),
                            )
                            run_state["result"] = result
                            await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                            return result
                        logger.info(f"[Turn {turn}] No tool_calls and no action detected - ending loop (standard behavior)")
                        # LLM 응답을 최종 답변으로 사용
                        summary = llm_response if llm_response else "작업이 완료되었습니다."
                        
                        # Final render: sanitization + persona styling (admin only)
                        raw_summary = summary
                        try:
                            from mellow_link.core.output_sanitizer import render_final_answer
                            active_persona_id = None
                            if persona and ("aventurine" in persona.lower() or "에브" in persona or "eve" in persona.lower()):
                                active_persona_id = "aventurine"
                            summary = render_final_answer(
                                raw_summary,
                                is_admin=is_admin,
                                persona_id=active_persona_id,
                                mode=effective_mode,
                                llm_service=self._llm if is_admin else None  # Admin일 때만 LLM 사용
                            )
                        except Exception as e:
                            logger.warning(f"[AgentBrain] Final render failed (no_tool_calls): {e}")
                            summary = raw_summary  # 실패 시 원본 사용

                        # 체크포인트 삭제
                        if self._checkpoint_manager:
                            try:
                                self._checkpoint_manager.clear_checkpoint(session_id, mark_completed=True)
                            except Exception as e:
                                logger.debug(f"[AgentBrain] Failed to clear checkpoint: {e}")
                        
                        result = AgentResult(
                            answer=summary,
                            steps=steps,
                            total_turns=turn,
                            finish_reason="no_tool_calls",
                            recovery_success=recovery_success_used,
                            total_infer_ms=run_state.get("total_infer_ms", 0.0),
                        )
                        run_state["result"] = result
                        await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                        return result

                if action is None:
                    # JSON 파싱 실패 또는 Action 비움 + Thought만 긴 경우 → Validator로 재추론 요청
                    correction_msg = validate_response_requires_action(llm_response, action)
                    if not correction_msg:
                        # 불완전한 JSON 감지 (닫는 } 누락, args 누락 등)
                        has_json_start = "{" in llm_response and '"tool"' in llm_response
                        is_incomplete = has_json_start and (llm_response.count("{") > llm_response.count("}") or '"args"' not in llm_response)
                        
                        if is_incomplete:
                            correction_msg = (
                                "[JSON 형식 오류] 불완전한 JSON이 감지되었습니다.\n"
                                "문제: JSON이 완전하지 않습니다 (닫는 } 누락 또는 args 누락).\n"
                                "\n"
                                "올바른 형식 (그대로 따라라):\n"
                                '{"tool":"finish","args":{"summary":"최종 답변"}}\n'
                                "\n"
                                "주의사항:\n"
                                "- JSON은 반드시 완전한 형태여야 합니다 (닫는 } 포함)\n"
                                "- finish 도구도 반드시 args를 포함해야 합니다\n"
                                "- 마크다운/코드펜스/설명/잡담 금지\n"
                                "- JSON 외의 어떤 텍스트도 함께 출력 금지"
                            )
                        else:
                            correction_msg = (
                                "[JSON 형식 오류] 유효한 JSON을 찾을 수 없습니다.\n"
                                "너는 반드시 '단 하나의 완전한 JSON 오브젝트'만 출력해야 한다.\n"
                                "\n"
                                "도구를 쓸 때 예시(그대로 따라라):\n"
                                '{"tool":"tool_name","args":{"arg_name":"value"}}\n'
                                "\n"
                                "할 일이 없으면 반드시 finish를 호출하라:\n"
                                '{"tool":"finish","args":{"summary":"최종 답변"}}\n'
                                "\n"
                                "주의사항:\n"
                                "- 마크다운/코드펜스/설명/잡담 금지\n"
                                "- JSON 외의 어떤 텍스트도 함께 출력 금지\n"
                                "- JSON은 반드시 완전한 형태여야 합니다"
                            )
                    retry_count += 1
                    if retry_count > max_retries:
                        # 재시도 한도 초과: LLM 응답 자체를 최종 답변으로 사용
                        logger.warning("[Turn %d] Parse failed %d times, using raw response", turn, retry_count)
                        steps.append(AgentStep(turn=turn, thought=llm_response))
                        
                        # 체크포인트 삭제 (작업 완료)
                        if self._checkpoint_manager:
                            try:
                                self._checkpoint_manager.clear_checkpoint(session_id, mark_completed=True)
                            except Exception as e:
                                logger.debug(f"[AgentBrain] Failed to clear checkpoint: {e}")
                        
                        # Final render: sanitization + persona styling (admin only)
                        try:
                            from mellow_link.core.output_sanitizer import render_final_answer
                            active_persona_id = None
                            if persona and ("aventurine" in persona.lower() or "에브" in persona or "eve" in persona.lower()):
                                active_persona_id = "aventurine"
                            sanitized_answer = render_final_answer(
                                llm_response,
                                is_admin=is_admin,
                                persona_id=active_persona_id,
                                mode=effective_mode,
                                llm_service=self._llm if is_admin else None  # Admin일 때만 LLM 사용
                            )
                        except Exception as e:
                            logger.warning(f"[AgentBrain] Final render failed (parse_fallback): {e}")
                            sanitized_answer = llm_response  # 실패 시 원본 사용
                        
                        result = AgentResult(
                            answer=sanitized_answer,
                            steps=steps,
                            total_turns=turn,
                            finish_reason="parse_fallback",
                            recovery_success=recovery_success_used,
                            total_infer_ms=run_state.get("total_infer_ms", 0.0),
                        )
                        run_state["result"] = result
                        await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                        return result

                    # [STANDARD_TOOL_CALLING] 표준 형식으로 메시지 추가
                    assistant_message = {"role": "assistant", "content": llm_response}
                    if tool_calls and len(tool_calls) > 0:
                        assistant_message["tool_calls"] = tool_calls
                    messages.append(assistant_message)
                    messages.append({"role": "user", "content": correction_msg})
                    steps.append(AgentStep(turn=turn, thought=llm_response))
                    logger.info("[Turn %d] Self-correction requested (retry %d/%d)", turn, retry_count, max_retries)
                    continue

                # 파싱 성공: 재시도 카운터 리셋
                retry_count = 0

                # PATCH B: FAST 모드에서는 복합 추론이 필요한 무거운 tool_call만 THINKING으로 승격
                if effective_mode == "fast" and action is not None and action.tool != "finish":
                    should_escalate_fast = self._should_escalate_fast_tool_call(action.tool)
                    if should_escalate_fast and not fast_escalated:
                        # 첫 번째 tool_call 감지: THINKING 모드로 에스컬레이션
                        fast_escalated = True
                        effective_mode = "thinking"
                        
                        # 메트릭 기록
                        try:
                            from mellow_link.core.metrics_collector import get_metrics_collector
                            coll = get_metrics_collector()
                            if coll:
                                coll.push("FAST_ESCALATED_TO_THINKING", 1.0, "count")
                        except Exception as e:
                            logger.debug(f"[AgentBrain] Metrics collector push failed: {e}")
                        
                        logger.info(f"[FAST] heavy tool_call detected ({action.tool}) -> escalating to THINKING (1/1)")
                        
                        # 프롬프트 재구성 (THINKING 모드용)
                        # 에스컬레이션 시에는 확장 모드 감지 (원본 user_input 사용)
                        expansion_level_escalated = _get_expansion_level(user_input) if user_input else 0
                        force_expanded_escalated = expansion_level_escalated >= 1
                        system_prompt = build_system_prompt(
                            tools_json,
                            persona=persona,
                            mode=effective_mode,
                            recent_history=recent_for_prompt,
                            use_template_mode=None,
                            registry=self._registry if effective_mode in ("thinking", "thinking-lite") else None,
                            user_input=user_input,
                            force_expanded=force_expanded_escalated,
                            expansion_level=expansion_level_escalated,
                            is_thinking_lite=False,  # 에스컬레이션은 thinking 모드로
                        )
                        tools_schema = self._registry.get_tools_schema()
                        if plan_only and tools_schema:
                            tools_schema = [
                                t for t in tools_schema
                                if (t.get("function") or {}).get("name") != "propose_new_tool"
                            ]
                        
                        # 메시지 재구성: 시스템 프롬프트 업데이트
                        messages = [msg for msg in messages if msg.get("role") != "system"]
                        messages.insert(0, {"role": "system", "content": system_prompt})
                        
                        # 에스컬레이션 알림 메시지 추가
                        escalation_msg = (
                            "[모드 전환] FAST 모드에서 도구 호출이 감지되어 THINKING 모드로 전환되었습니다. "
                            "이제 도구를 사용할 수 있습니다. 이전에 요청한 도구 호출을 다시 시도해주세요."
                        )
                        messages.append({"role": "user", "content": escalation_msg})
                        
                        # 이 턴의 tool_call은 실행하지 않고 continue (다음 턴에서 THINKING 모드로 재시도)
                        continue
                    elif should_escalate_fast:
                        # 이미 에스컬레이션된 경우: tool 실행 차단하고 안전한 메시지로 종료
                        try:
                            from mellow_link.core.metrics_collector import get_metrics_collector
                            coll = get_metrics_collector()
                            if coll:
                                coll.push("FAST_TOOLCALL_BLOCKED", 1.0, "count")
                        except Exception as e:
                            logger.debug(f"[AgentBrain] Metrics collector push failed: {e}")
                        
                        logger.warning(f"[FAST] tool_call ({action.tool}) blocked after escalation - ending safely")
                    else:
                        logger.info(f"[FAST] lightweight tool_call detected ({action.tool}) -> staying in FAST")
                        
                        # 안전한 종료 메시지
                        safe_message = (
                            f"FAST 모드에서는 도구 호출이 제한됩니다. "
                            f"'{action.tool}' 도구가 필요하시면 thinking 모드로 요청해주세요."
                        )
                        
                        result = AgentResult(
                            answer=safe_message,
                            steps=steps,
                            total_turns=turn,
                            finish_reason="fast_toolcall_blocked",
                            recovery_success=False,
                            total_infer_ms=run_state.get("total_infer_ms", 0.0),
                        )
                        run_state["result"] = result
                        await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                        return result

                # ── FINISH 감지 ──
                if action.tool == "finish":
                    # TASK 강제: 최소 1회 유효한 도구 호출+Observation 없으면 finish 거부 (할루시네이션 방지)
                    if require_at_least_one_tool and not _has_valid_tool_execution(steps):
                        observation_reject_count += 1
                        if observation_reject_count >= 2:
                            # One re-prompt already done; exit with observation_required_not_met and record metric
                            try:
                                from mellow_link.core.metrics_collector import get_metrics_collector
                                coll = get_metrics_collector()
                                if coll:
                                    coll.push_observation_violation(request_id=session_id)
                            except Exception as e:
                                logger.debug("[AgentBrain] Metrics collector push failed: %s", e)
                            # Output sanitization은 시스템 메시지에는 불필요
                            result = AgentResult(
                                answer="도구를 한 번 이상 실행한 뒤 결론을 내야 합니다. 지금은 그렇게 하지 않아 응답을 완료할 수 없습니다.",
                                steps=steps,
                                total_turns=turn,
                                finish_reason="observation_required_not_met",
                                recovery_success=False,
                                total_infer_ms=run_state.get("total_infer_ms", 0.0),
                            )
                            run_state["result"] = result
                            return result
                        block_msg = (
                            "finish 호출 거부: 아직 한 번도 도구를 실행하지 않았습니다. "
                            "Thought에서 '분석했다/확인했다'라고 말하는 것은 허용되지 않습니다. "
                            "반드시 read_file, list_directory 등 도구를 한 번 이상 호출하고, "
                            "tool 결과를 받은 후에만 finish를 호출하세요. "
                            "지금은 도구 하나를 호출하는 JSON만 출력하세요."
                        )
                        # [STANDARD_TOOL_CALLING] 표준 형식으로 메시지 추가
                        assistant_message = {"role": "assistant", "content": llm_response}
                        if tool_calls and len(tool_calls) > 0:
                            assistant_message["tool_calls"] = tool_calls
                        messages.append(assistant_message)
                        messages.append({
                            "role": "tool",
                            "tool_name": "finish",
                            "content": block_msg,
                        })
                        steps.append(AgentStep(turn=turn, thought=llm_response, action=action, observation="[finish 거부: 도구 미실행]"))
                        logger.warning("[Turn %d] Finish blocked: no valid tool execution yet (reject %d/2)", turn, observation_reject_count)
                        continue

                    # todo_done 이벤트 (T4: 결과 요약)
                    emit_if_enabled("todo_done", {
                        "todo_id": "T4",
                        "title": "결과 요약",
                        "status": "completed",
                        "summary": action.args.get("summary", "")[:LOG_TRUNCATE_LEN],
                    })
                    
                    raw_summary = action.args.get("summary", llm_response)
                    if _should_enforce_structured_report(steps):
                        raw_summary = _build_six_step_report(
                            user_input=user_input,
                            model_summary=raw_summary,
                            steps=steps,
                            finish_reason="finish_tool",
                        )
                        logger.info(
                            "[AgentBrain] finish summary 강제 정규화 완료 "
                            "(core_observations=%d)",
                            len(_collect_core_observations(steps)),
                        )
                    
                    # [STANDARD_TOOL_CALLING] finish 도구 호출도 표준 형식으로 기록
                    assistant_message = {"role": "assistant", "content": llm_response}
                    if tool_calls and len(tool_calls) > 0:
                        assistant_message["tool_calls"] = tool_calls
                    messages.append(assistant_message)
                    messages.append({
                        "role": "tool",
                        "tool_name": "finish",
                        "content": raw_summary,  # 이벤트/로그에는 원본 사용
                    })
                    
                    step = AgentStep(turn=turn, thought=llm_response, action=action, observation="[종료]")
                    steps.append(step)
                    
                    # todo_done 이벤트 (T5: 완료) - 원본 summary 사용
                    emit_if_enabled("todo_done", {
                        "todo_id": "T5",
                        "title": "완료",
                        "status": "completed",
                    })
                    
                    # run_finished 이벤트 발행 - 원본 summary 사용 (페르소나 스타일링 전)
                    emit_if_enabled("run_finished", {
                        "success": True,
                        "summary": raw_summary[:500],  # 원본 사용
                        "total_turns": turn,
                        "finish_reason": "finish_tool",
                    })
                    
                    # Final render: sanitization + persona styling (admin only)
                    # 이벤트 발행 후에만 페르소나 스타일링 적용
                    try:
                        from mellow_link.core.output_sanitizer import render_final_answer
                        active_persona_id = None
                        if persona and ("aventurine" in persona.lower() or "에브" in persona or "eve" in persona.lower()):
                            active_persona_id = "aventurine"
                        summary = render_final_answer(
                            raw_summary,
                            is_admin=is_admin,
                            persona_id=active_persona_id,
                            mode=effective_mode,
                            llm_service=self._llm if is_admin else None  # Admin일 때만 LLM 사용
                        )
                    except Exception as e:
                        logger.warning(f"[AgentBrain] Final render failed (finish_tool): {e}")
                        summary = raw_summary  # 실패 시 원본 사용
                    
                    # 체크포인트 삭제 (작업 완료)
                    if self._checkpoint_manager:
                        try:
                            self._checkpoint_manager.clear_checkpoint(session_id, mark_completed=True)
                        except Exception as e:
                            logger.debug(f"[AgentBrain] Failed to clear checkpoint: {e}")
                    
                    # 목표 상태 업데이트 (작업 완료 시)
                    if root_goal_id:
                        try:
                            from mellow_link.core.goal_manager import get_goal_manager
                            goal_manager = get_goal_manager()
                            # finish_tool로 완료된 경우 DONE으로 업데이트
                            goal_manager.update_goal_status(root_goal_id, "DONE")
                            logger.info(f"[AgentBrain] Goal status updated to DONE: {root_goal_id}")
                        except Exception as e:
                            logger.warning(f"[AgentBrain] Failed to update goal status: {e}")
                    
                    # Phase 5: 행동 후 한계 자동 추출
                    _limitations = _extract_limitations(steps, action.args)

                    # Phase 6: 성공 패턴 기록 (Positive Reinforcement)
                    success_pattern = _extract_success_pattern(steps, user_input)
                    if success_pattern:
                        _save_success_insight(success_pattern)

                    result = AgentResult(
                        answer=summary,
                        steps=steps,
                        total_turns=turn,
                        finish_reason="finish_tool",
                        recovery_success=recovery_success_used,
                        limitations=_limitations,
                        total_infer_ms=run_state.get("total_infer_ms", 0.0),
                    )
                    run_state["result"] = result
                    await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                    return result

                # PATCH: 라우팅 히트 모드에서 analyze_text 차단
                if is_routing_hit_mode and action.tool == "analyze_text":
                    logger.warning(f"[AgentBrain] Routing hit mode: blocking analyze_text call")
                    err_msg = "[차단] 라우팅 히트 모드에서는 analyze_text 호출이 허용되지 않습니다. 도구 결과를 그대로 finish하세요."
                    steps.append(AgentStep(turn=turn, thought=llm_response, action=action, observation=err_msg))
                    assistant_message = {"role": "assistant", "content": llm_response}
                    if tool_calls and len(tool_calls) > 0:
                        assistant_message["tool_calls"] = tool_calls
                    messages.append(assistant_message)
                    messages.append({
                        "role": "tool",
                        "tool_name": action.tool if action else "unknown",
                        "content": err_msg,
                    })
                    continue

                # ── STRICT_TOOL_EXECUTION_GATEKEEPER: 레지스트리 외 도구 호출 원천 봉쇄 ──
                # [TOOL_WHITELIST_SYNC] 레지스트리에서 동적으로 가져오되, 명시적 화이트리스트와 병합하여 모든 커스텀 도구 포함 보장
                registry_tools = set(self._registry.get_tool_names())
                # 명시적 화이트리스트: agent_tools.py의 @tool 데코레이터가 붙은 모든 도구
                # (동적 도구는 레지스트리에 자동 포함되므로 여기서는 기본 도구만 명시)
                explicit_whitelist = {
                    "read_file", "write_file", "list_directory", "generate_report", "cleanup_file",
                    "inspect_system_status", "run_command", "get_evolution_proposals_summary", "security_status",
                    "search_memory", "create_image", "animate_image", "speak", "finish", "propose_new_tool",
                    "get_cost_efficiency_briefing", "get_past_failure_context", "get_kpi_dashboard",
                    "get_cwd", "get_time", "get_system_snapshot", "list_processes"  # 경량 도구 추가
                }
                if plan_only:
                    explicit_whitelist = explicit_whitelist - {"propose_new_tool"}
                # 레지스트리 도구 + 명시적 화이트리스트 병합 (동적 도구 포함 보장)
                valid_tool_names = list(registry_tools | explicit_whitelist)
                if action.tool not in valid_tool_names:
                    # [ENHANCED_HALLUCINATION_PREVENTION] 할루시네이션된 도구명에 대한 명확한 차단 메시지
                    # 유사한 도구명 제안 (typo 보정)
                    similar_tools = [
                        name for name in valid_tool_names 
                        if action.tool.lower() in name.lower() or name.lower() in action.tool.lower()
                    ]
                    suggestion_text = ""
                    if similar_tools:
                        suggestion_text = f"\n유사한 도구명: {', '.join(similar_tools[:3])}"
                    
                    err_msg = (
                        f"[ERROR] 할루시네이션 감지: '{action.tool}'은(는) 존재하지 않는 도구입니다.\n"
                        f"⚠️ 학습 데이터의 가상 도구를 호출하지 마세요. 오직 아래 목록의 도구만 사용 가능합니다:\n"
                        f"{', '.join(sorted(valid_tool_names))}\n"
                        f"{suggestion_text}\n"
                        f"실패 원인을 분석하고 올바른 도구/인자로 재시도하세요."
                    )
                    logger.critical("\033[91m[STRICT_TOOL_GATE] Invalid tool call blocked: '%s' (valid: %s)\033[0m", action.tool, ", ".join(sorted(valid_tool_names)))
                    steps.append(AgentStep(turn=turn, thought=llm_response, action=action, observation=err_msg))
                    # [STANDARD_TOOL_CALLING] 표준 형식으로 메시지 추가
                    assistant_message = {"role": "assistant", "content": llm_response}
                    if tool_calls and len(tool_calls) > 0:
                        assistant_message["tool_calls"] = tool_calls
                    messages.append(assistant_message)
                    messages.append({
                        "role": "tool",
                        "tool_name": action.tool if action else "unknown",
                        "content": err_msg,
                    })
                    continue

                # ── PATH_NORMALIZATION_AND_VALIDATION: 경로 자동 정규화 및 검증 ──
                workspace_root = _get_workspace_root()
                base_path_str = str(workspace_root.resolve())
                
                # 경로 정규화 및 검증 수행
                normalized_args, path_error, correction_msg = _normalize_and_validate_path_args(
                    workspace_root, action.tool, action.args
                )
                
                if path_error is not None:
                    rejected_path, error_detail = path_error
                    # 예시 경로 생성 (경로 구분자 포함)
                    example_path = str(workspace_root / "file.txt")
                    err_msg = (
                        f"[ERROR] 경로 검증 실패: '{rejected_path}'\n"
                        f"사유: {error_detail}\n"
                        f"⚠️ 모든 작업은 반드시 {base_path_str} 내부에서 이루어져야 합니다.\n"
                        f"상대 경로(., workspace/)를 사용할 수 있지만, 시스템이 자동으로 절대 경로로 변환합니다.\n"
                        f"예: 'workspace/file.txt' → '{example_path}'"
                    )
                    logger.critical("\033[91m[PATH_GATE_BLOCKED] Invalid path blocked: '%s' (tool=%s, detail=%s)\033[0m", 
                                   rejected_path, action.tool, error_detail)
                    steps.append(AgentStep(turn=turn, thought=llm_response, action=action, observation=err_msg))
                    # [STANDARD_TOOL_CALLING] 표준 형식으로 에러 메시지 추가
                    assistant_message = {"role": "assistant", "content": llm_response}
                    if tool_calls and len(tool_calls) > 0:
                        assistant_message["tool_calls"] = tool_calls
                    messages.append(assistant_message)
                    messages.append({
                        "role": "tool",
                        "tool_name": action.tool if action else "unknown",
                        "content": err_msg,
                    })
                    continue
                
                # 정규화된 인자로 교체
                action.args = normalized_args
                logger.debug(f"[PATH_NORMALIZATION] 경로 정규화 완료 (tool={action.tool}, args={normalized_args})")
                
                # 경로가 수정되었을 경우 메시지 저장 (Observation에 추가할 예정)
                path_correction_message = correction_msg

                # ── VRAM_OPTIMIZATION: 이미지 생성 전 LLM 컨텍스트 정리 ──
                # 이미지/비디오 생성은 VRAM을 많이 사용하므로, LLM 컨텍스트를 미리 정리하여 메모리 확보
                if action.tool in ("create_image", "animate_image"):
                    # 히스토리를 더 공격적으로 트리밍 (시스템 프롬프트 + 최근 3턴만 유지)
                    if len(messages) > 4:  # system + 최근 3턴 (user/assistant 쌍)
                        system_msg = messages[0]
                        # 최근 3턴만 유지 (6개 메시지: user/assistant 쌍 3개)
                        recent_messages = messages[-6:] if len(messages) > 6 else messages[1:]
                        messages = [system_msg] + recent_messages
                        logger.info(f"[VRAM_OPTIMIZATION] 이미지 생성 전 LLM 컨텍스트 정리: {len(messages)}개 메시지로 축소")
                    
                    # LLM 서비스의 컨텍스트도 정리 (가능한 경우)
                    try:
                        if hasattr(self._llm, '_contexts') and isinstance(self._llm._contexts, dict):
                            # 모든 컨텍스트의 메시지 수를 제한
                            for context_id, context in self._llm._contexts.items():
                                if hasattr(context, 'messages') and len(context.messages) > 3:
                                    # 최근 3개 메시지만 유지
                                    context.messages = context.messages[-3:]
                                    logger.debug(f"[VRAM_OPTIMIZATION] LLM 컨텍스트 '{context_id}' 정리 완료")
                    except Exception as e:
                        logger.debug(f"[VRAM_OPTIMIZATION] LLM 컨텍스트 정리 실패 (무시): {e}")

                # ── ACT: 도구 실행 + 에러 복구 레이어 ──
                try:
                    # tool_started 이벤트 발행
                    tool_start_ts = time.time()
                    args_summary = {}
                    if action.args:
                        # 민감한 정보 제거한 args 요약
                        args_summary = {k: str(v)[:100] + "..." if len(str(v)) > 100 else v 
                                       for k, v in action.args.items()}
                    emit_if_enabled("tool_started", {
                        "tool_name": action.tool,
                        "args_summary": args_summary,
                        "turn": turn,
                    })
                    
                    observation = await self._registry.execute(action.tool, action.args)
                    
                    # tool_done 이벤트 발행
                    tool_duration_ms = (time.time() - tool_start_ts) * 1000
                    observation_capped = _cap_observation_size(observation, max_chars=500)  # 이벤트용으로 더 짧게
                    emit_if_enabled("tool_done", {
                        "tool_name": action.tool,
                        "success": not observation.startswith("[Error]"),
                        "duration_ms": tool_duration_ms,
                        "observation_preview": observation_capped[:LOG_TRUNCATE_LEN],
                        "turn": turn,
                    })
                    
                    # [cite: 2026-02-09] 경로가 수정되었을 경우 성공 메시지를 Observation 앞에 추가
                    if path_correction_message:
                        observation = f"{path_correction_message}\n\n{observation}"
                        logger.info(f"[PATH_CORRECTION] {path_correction_message}")
                    
                    # Observation 크기 제한 (프롬프트 블로트 방지)
                    observation = _cap_observation_size(observation)
                    
                    # 도구가 [Error] 문자열을 반환한 경우 복구 제안 요청 (최대 2회)
                    if observation.startswith("[Error]") and recovery_attempts_this_run < 2:
                        try:
                            from mellow_link.core.recovery_manager import get_recovery_manager
                            rm = get_recovery_manager()
                            available = self._registry.get_tool_names()
                            suggestion = rm.get_recovery_suggestion(
                                action.tool, observation, recovery_attempts_this_run, available
                            )
                            if suggestion:
                                if suggestion.action == "retry":
                                    recovery_attempts_this_run += 1
                                    obs2 = await self._registry.execute(action.tool, action.args)
                                    if not obs2.startswith("[Error]"):
                                        observation = obs2
                                        recovery_success_used = True
                                        logger.info("[Turn %d] Recovery (retry) succeeded", turn)
                                elif suggestion.action == "use_fallback" and suggestion.fallback_tool:
                                    recovery_attempts_this_run += 1
                                    obs2 = await self._registry.execute(
                                        suggestion.fallback_tool, action.args
                                    )
                                    if not obs2.startswith("[Error]"):
                                        observation = f"[Recovery: used {suggestion.fallback_tool}] {obs2}"
                                        recovery_success_used = True
                                        logger.info(
                                            "[Turn %d] Recovery (fallback %s) succeeded",
                                            turn, suggestion.fallback_tool
                                        )
                        except Exception as rec_ex:
                            logger.debug("[AgentBrain] Recovery attempt failed: %s", rec_ex)
                    logger.info("[Turn %d] Thought: %s", turn, (llm_response or "")[:300])
                    logger.info("[Turn %d] Observation: %s", turn, observation[:LOG_TRUNCATE_LEN])
                except (SecurityBlocked, PermissionError) as e:
                    # Security violations are not recoverable by retrying.
                    # Immediately halt the loop and require human intervention.
                    alert = f"[SECURITY ALERT] 차단된 작업: {action.tool}. 사유: {e}"
                    # ANSI red for terminal; safe to appear as plain text in logs if unsupported.
                    logger.critical("\033[91m%s\033[0m", alert)

                    step = AgentStep(
                        turn=turn,
                        thought=llm_response,
                        action=action,
                        observation=alert,
                    )
                    steps.append(step)

                    # 체크포인트를 PAUSED 상태로 저장 (보안 위반으로 중단)
                    if self._checkpoint_manager:
                        try:
                            self._checkpoint_manager.pause_checkpoint(
                                session_id=session_id,
                                task_intent=user_input,
                                step=turn,
                                history=steps,
                                pause_reason="security_violation",
                                original_max_turns=effective_max_turns
                            )
                        except Exception as e:
                            logger.debug(f"[AgentBrain] Failed to pause checkpoint: {e}")
                    
                    result = AgentResult(
                        answer=f"보안 문제로 작업을 중단합니다.\n{alert}",
                        steps=steps,
                        total_turns=turn,
                        finish_reason="security_violation",
                        recovery_success=False,
                        total_infer_ms=run_state.get("total_infer_ms", 0.0),
                    )
                    run_state["result"] = result
                    await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
                    return result

                step = AgentStep(
                    turn=turn,
                    thought=llm_response,
                    action=action,
                    observation=observation,
                )
                steps.append(step)
                
                # ── CHECKPOINT: 현재 상태 저장 ──
                if self._checkpoint_manager:
                    try:
                        self._checkpoint_manager.save_checkpoint(
                            session_id=session_id,
                            task_intent=user_input,
                            step=turn,
                            history=steps,
                            status="RUNNING",
                            original_max_turns=effective_max_turns
                        )
                    except Exception as e:
                        logger.debug(f"[AgentBrain] Failed to save checkpoint: {e}")

                # ── OBSERVE: 결과를 히스토리에 추가 (Ollama 표준 형식) ──
                # [STANDARD_TOOL_CALLING] Ollama 표준 형식: assistant 메시지에 tool_calls 포함
                assistant_message = {"role": "assistant", "content": llm_response}
                if tool_calls and len(tool_calls) > 0:
                    assistant_message["tool_calls"] = tool_calls
                messages.append(assistant_message)
                
                # [FORCED_SELF_CORRECTION] 에러 발생 시 자동으로 get_past_failure_context 호출 및 주입
                # FAST 모드에서는 비활성화 (프롬프트 크기 최적화)
                enhanced_observation = observation
                if effective_mode != "fast" and (observation.startswith("[Error]") or "[ERROR]" in observation.upper()):
                    try:
                        from mellow_link.core.agent_tools import get_past_failure_context
                        failure_context = get_past_failure_context(target_file=None, limit=3)
                        if failure_context and failure_context.strip():
                            enhanced_observation = (
                                f"{observation}\n\n"
                                f"[과거 실패 패턴 분석]\n{failure_context}\n\n"
                                f"[지시] 위 실패 패턴을 참고하여 동일한 오류를 피하고 올바른 도구/인자로 재시도하라."
                            )
                            logger.info("[AgentBrain] 자동으로 과거 실패 컨텍스트 주입 완료")
                    except Exception as e:
                        logger.debug(f"[AgentBrain] get_past_failure_context 자동 호출 실패: {e}")
                elif effective_mode == "fast":
                    logger.debug("[AgentBrain] FAST mode: skipping get_past_failure_context auto-injection")
                
                # [STANDARD_TOOL_CALLING] Ollama 표준 형식: tool role 사용
                tool_name = action.tool if action else "unknown"
                messages.append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": enhanced_observation,
                })
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[Observation 반영 의무] 직전 Observation을 반드시 분석하고 다음 Action을 선택하라. "
                            "종료 시 finish.summary에는 핵심 Observation을 누락 없이 포함하고, "
                            "Observation 기반 작업일 때만 6단계 작업 보고 형식([1단계]~[6단계])과 ✅ verified 태그를 사용하라."
                        ),
                    }
                )

                # 히스토리 트리밍 (시스템 프롬프트는 유지)
                # 이미지 생성 직후에는 공격적 트리밍 적용 (VRAM 절약)
                is_after_image_generation = (
                    action and action.tool in ("create_image", "animate_image")
                )
                messages = self._trim_history(messages, aggressive=is_after_image_generation)

            # ── MAX TURNS 초과 ──
            logger.warning("Agent loop reached max turns (%d)", effective_max_turns)
            
            # [AUTO_FINISH_ON_MAX_TURNS] 최대 턴 수 도달 시 finish 도구를 자동으로 호출하여 정리
            # 마지막 Thought가 JSON 형태로 출력된 경우를 방지하고, 지금까지의 작업을 요약하여 finish 호출
            try:
                summary = await self._generate_summary_for_max_turns(user_input, steps, persona)
                logger.info("[AgentBrain] 최대 턴 수 도달: 자동 finish summary 생성 완료")
            except Exception as e:
                logger.warning(f"[AgentBrain] 자동 summary 생성 실패, 폴백 사용: {e}")
                last_thought = steps[-1].thought if steps else ""
                # JSON 형태의 Thought를 정리
                if last_thought.strip().startswith("{") and "tool" in last_thought:
                    summary = f"[최대 턴 수({effective_max_turns}) 도달] 작업이 완료되지 않았습니다. 지금까지 수행한 작업을 확인해주세요."
                else:
                    summary = f"[최대 턴 수({effective_max_turns}) 도달] {last_thought[:LOG_TRUNCATE_LEN]}"
            if _should_enforce_structured_report(steps):
                summary = _build_six_step_report(
                    user_input=user_input,
                    model_summary=summary,
                    steps=steps,
                    finish_reason="max_turns",
                )
            # Final render: sanitization + persona styling (admin only)
            raw_summary = summary
            try:
                from mellow_link.core.output_sanitizer import render_final_answer
                active_persona_id = None
                if persona and ("aventurine" in persona.lower() or "에브" in persona or "eve" in persona.lower()):
                    active_persona_id = "aventurine"
                summary = render_final_answer(
                    raw_summary,
                    is_admin=is_admin,
                    persona_id=active_persona_id,
                    mode=effective_mode,
                    llm_service=self._llm if is_admin else None  # Admin일 때만 LLM 사용
                )
            except Exception as e:
                logger.warning(f"[AgentBrain] Final render failed (max_turns): {e}")
                summary = raw_summary  # 실패 시 원본 사용
            
            if self._checkpoint_manager:
                try:
                    self._checkpoint_manager.pause_checkpoint(
                        session_id=session_id,
                        task_intent=user_input,
                        step=effective_max_turns,
                        history=steps,
                        pause_reason="max_turns",
                        original_max_turns=effective_max_turns
                    )
                except Exception as e:
                    logger.debug(f"[AgentBrain] Failed to pause checkpoint: {e}")
            
            # 목표 상태 업데이트 (max_turns 도달 시 - 부분 완료로 간주)
            if root_goal_id:
                try:
                    from mellow_link.core.goal_manager import get_goal_manager
                    goal_manager = get_goal_manager()
                    # max_turns 도달은 부분 완료로 간주하여 IN_PROGRESS 유지 또는 FAILED로 설정 가능
                    # 여기서는 IN_PROGRESS 유지 (재시도 가능)
                    logger.info(f"[AgentBrain] Goal {root_goal_id} reached max_turns, status remains IN_PROGRESS")
                except Exception as e:
                    logger.warning(f"[AgentBrain] Failed to check goal status: {e}")
            
            result = AgentResult(
                answer=summary,
                steps=steps,
                total_turns=effective_max_turns,
                finish_reason="max_turns",
                recovery_success=recovery_success_used,
                total_infer_ms=run_state.get("total_infer_ms", 0.0),
            )
            run_state["result"] = result
            await self._experience_helper.archive_experience(user_input, context_summary, result, start_time)
            return result
        except Exception as e:
            run_state["error"] = e
            # error 이벤트 발행
            emit_if_enabled("error", {
                "message": str(e)[:500],
                "type": type(e).__name__,
            })
            # run_finished 이벤트 (실패)
            emit_if_enabled("run_finished", {
                "success": False,
                "summary": f"Error: {str(e)[:500]}",
                "finish_reason": "error",
            })
            raise
        finally:
            # BENCH_PROFILE 모드에서는 경험 장부 기록도 비활성화 (벤치마크 메트릭 왜곡 방지)
            try:
                import os
                from mellow_link.config import get_settings
                settings = get_settings()
                bench_profile = getattr(settings, "bench_profile", False) or os.getenv("BENCH_PROFILE", "").strip().lower() in ("1", "true", "yes")
            except Exception as e:
                logger.debug("[AgentBrain] Failed to read bench_profile in finally, assuming False: %s", e)
                bench_profile = False
            
            if not bench_profile:
                try:
                    self._experience_helper.schedule_record_experience_ledger(
                        run_state, start_time, steps, user_input
                    )
                except Exception as _e:
                    logger.debug("[AgentBrain] Ledger record schedule failed: %s", _e)
            else:
                logger.debug("[AgentBrain] BENCH_PROFILE mode: skipping experience ledger record")

    # ──────────────────────────────────────────
    # LLM 호출 (Ollama chat API 사용)
    # ──────────────────────────────────────────

    def _is_empty_llm_response(self, text: Any) -> bool:
        """Strict empty response: zero/whitespace-only or length < 10. Used for fast fallback policy."""
        if text is None or not isinstance(text, str):
            return True
        s = text.strip()
        return len(s) == 0 or len(s) < 10

    def _log_llm_call_debug(self, mode: str, messages: List[Dict[str, str]], auto_docs_injected: bool) -> None:
        """Log debug information before LLM call (for TTFT performance analysis)."""
        try:
            debug_mode = (mode or self._model_mode or "fast").strip().lower()
            model = self._llm.get_model_for_mode(debug_mode)
            # Calculate prompt length
            prompt_text = "\n".join([m.get("content", "") for m in messages])
            prompt_chars = len(prompt_text)
            # Rough token estimate (1 token ≈ 4 chars for Korean/English mix)
            estimated_tokens = prompt_chars // 4
            
            # Get num_ctx if available
            num_ctx = None
            try:
                if hasattr(self._llm, 'get_context_size'):
                    num_ctx = self._llm.get_context_size()
                elif hasattr(self._llm, '_context_size'):
                    num_ctx = self._llm._context_size
            except Exception as e:
                logger.debug("[AgentBrain] get_context_size (TTFT_DEBUG) failed: %s", e)
            
            # FAST 모드에서 프롬프트 구성 요소별 크기 로깅
            if mode == "fast":
                system_msg = next((m for m in messages if m.get("role") == "system"), None)
                system_chars = len(system_msg.get("content", "")) if system_msg else 0
                user_chars = sum(len(m.get("content", "")) for m in messages if m.get("role") == "user")
                assistant_chars = sum(len(m.get("content", "")) for m in messages if m.get("role") == "assistant")
                logger.info(
                    f"[TTFT_DEBUG] effective_mode={mode}, model={model}, "
                    f"num_ctx={num_ctx}, total_prompt_chars={prompt_chars}, "
                    f"system_chars={system_chars}, user_chars={user_chars}, assistant_chars={assistant_chars}, "
                    f"estimated_tokens={estimated_tokens}, docs_auto_injected={auto_docs_injected}"
                )
            else:
                logger.info(
                    f"[TTFT_DEBUG] effective_mode={mode}, model={model}, "
                    f"num_ctx={num_ctx}, prompt_chars={prompt_chars}, "
                    f"estimated_tokens={estimated_tokens}, docs_auto_injected={auto_docs_injected}"
                )
        except Exception as e:
            logger.debug(f"[TTFT_DEBUG] Failed to log debug info: {e}")

    def _determine_max_tokens(
        self,
        effective_mode: str,
        user_input: Optional[str] = None,
        force_expanded: bool = False,
        expansion_level: int = 0,
        is_thinking_lite: bool = False,
    ) -> Optional[int]:
        """
        THINKING/THINKING-LITE 모드에서 max_tokens 결정.
        
        Args:
            effective_mode: 현재 모드 ("fast", "thinking", "thinking-lite", "research")
            user_input: 사용자 입력 (장문 감지용)
            force_expanded: 확장 모드 여부
            expansion_level: 확장 레벨 (0=summary, 1=v1, 2=v2, 3=v3)
            is_thinking_lite: thinking-lite 모드 여부
        
        Returns:
            max_tokens 값 (None이면 기본값 사용)
        """
        # thinking-lite 모드
        if is_thinking_lite or effective_mode == "thinking-lite":
            lite_max = int(os.getenv("THINKING_LITE_MAX_TOKENS", "900"))
            logger.info(f"[_determine_max_tokens] THINKING-LITE mode: max_tokens={lite_max}")
            return lite_max
        
        # THINKING 모드에서만 적용
        if effective_mode != "thinking":
            return None
        
        # 환경변수에서 설정 읽기
        default_max = int(os.getenv("THINKING_MAX_TOKENS_DEFAULT", "900"))
        expanded_max_v1 = int(os.getenv("THINKING_MAX_TOKENS_EXPANDED", "1200"))
        expanded_max_v2 = int(os.getenv("THINKING_MAX_TOKENS_EXPANDED_V2", "1800"))
        expanded_max_v3 = int(os.getenv("THINKING_MAX_TOKENS_EXPANDED_V3", "2400"))
        
        # 확장 레벨에 따라 다른 값 사용
        if expansion_level == 3:
            logger.info(f"[_determine_max_tokens] THINKING mode (expand v3): max_tokens={expanded_max_v3}")
            return expanded_max_v3
        elif expansion_level == 2:
            logger.info(f"[_determine_max_tokens] THINKING mode (expand v2): max_tokens={expanded_max_v2}")
            return expanded_max_v2
        elif expansion_level == 1 or force_expanded:
            logger.info(f"[_determine_max_tokens] THINKING mode (expand v1): max_tokens={expanded_max_v1}")
            return expanded_max_v1
        
        # 장문 요청이면 기본값 사용
        if user_input and _is_long_form_request(user_input):
            logger.info(f"[_determine_max_tokens] THINKING mode (long-form summary): max_tokens={default_max}")
            return default_max
        
        # 그 외에는 None 반환 (기본값 사용)
        return None

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        session_state: Optional[Dict[str, Any]] = None,
        mode: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, Optional[List[Dict[str, Any]]], Optional[float]]:
        """
        LLM에 메시지 리스트를 보내고 응답 텍스트와 tool_calls를 반환.

        LLMService.chat()을 우선 사용하고, 없으면 generate() 폴백.
        Ollama Native Tool Calling을 지원합니다.
        Fast fallback to thinking model: at most one per session (session_state["fast_fallback_used"]).

        Returns:
            (text, tool_calls, infer_ms) 튜플. 
            - text: LLM 응답 텍스트
            - tool_calls: 도구 호출 목록 (None일 수 있음)
            - infer_ms: LLM 추론 시간 (ms, None일 수 있음)
        """
        # LLMService 연결 확인 및 자동 재연결
        # _ensure_connected()를 사용하여 더 견고한 재연결 로직 사용
        if hasattr(self._llm, 'is_ready'):
            is_ready_result = self._llm.is_ready()
            if not is_ready_result:
                current_status = getattr(self._llm, '_status', 'UNKNOWN')
                llm_session_state = "closed" if (hasattr(self._llm, '_session') and
                                                self._llm._session and
                                                self._llm._session.closed) else "open"
                logger.warning(
                    f"[_call_llm] LLMService not ready (status={current_status}, session={llm_session_state}), "
                    "attempting auto-reconnect..."
                )
                if hasattr(self._llm, '_ensure_connected'):
                    # _ensure_connected()는 세션 상태를 더 잘 확인하고 재연결을 시도함
                    logger.info("[_call_llm] Calling _ensure_connected()...")
                    reconnect_result = await self._llm._ensure_connected(max_retries=2)  # 재시도 횟수 증가
                    logger.info(f"[_call_llm] _ensure_connected() returned: {reconnect_result}")
                    if not reconnect_result:
                        final_status = getattr(self._llm, '_status', 'UNKNOWN')
                        final_session = "closed" if (hasattr(self._llm, '_session') and 
                                                      self._llm._session and 
                                                      self._llm._session.closed) else "open"
                        error_msg = "LLMService not connected and auto-reconnect failed"
                        logger.error(
                            f"[_call_llm] {error_msg}. "
                            f"Final status: {final_status}, "
                            f"Final session: {final_session}"
                        )
                        raise Exception(f"{error_msg}. Please ensure Ollama is running.")
                    logger.info("[_call_llm] LLMService auto-reconnected successfully")
                elif hasattr(self._llm, 'connect'):
                    # _ensure_connected()가 없으면 직접 connect() 호출 (하위 호환성)
                    try:
                        await self._llm.connect()
                        if not self._llm.is_ready():
                            raise Exception("Connection attempt failed")
                        logger.info("[_call_llm] LLMService reconnected successfully")
                    except Exception as e:
                        error_msg = f"LLMService not connected and reconnect failed: {e}"
                        logger.error(f"[_call_llm] {error_msg}")
                        raise Exception(f"{error_msg}. Please ensure Ollama is running.")
                else:
                    logger.error("[_call_llm] LLMService does not have _ensure_connected() method")
                    raise Exception("LLMService does not support auto-reconnect")
            # (is_ready_result True면 재연결 없이 진행)
        
        effective_mode = (mode or self._model_mode or "fast").strip().lower()
        model = self._llm.get_model_for_mode(effective_mode)
        tool_calls = None

        infer_ms = None  # LLM 추론 시간 (ms)
        
        # Max tokens 옵션 준비 (Ollama는 options.num_predict 사용)
        chat_kwargs = {}
        if max_tokens is not None:
            chat_kwargs["options"] = {"num_predict": max_tokens}
            # TTFT_DEBUG 로깅
            if os.getenv("TTFT_DEBUG", "").strip().lower() in ("1", "true", "yes"):
                logger.info(f"[_call_llm] TTFT_DEBUG: max_tokens={max_tokens} (num_predict={max_tokens})")
        
        try:
            if hasattr(self._llm, "chat"):
                # Retry once if connection fails
                try:
                    response = await self._llm.chat(
                        messages=messages, 
                        model=model,
                        tools=tools,  # Ollama Native Tool Calling
                        **chat_kwargs
                    )
                except Exception as e:
                    # If request fails, ensure connected and retry once
                    if "not connected" in str(e).lower() or "connection" in str(e).lower():
                        logger.warning(f"[_call_llm] Request failed, ensuring connection and retrying once: {e}")
                        if hasattr(self._llm, '_ensure_connected'):
                            await self._llm._ensure_connected(max_retries=1)
                        response = await self._llm.chat(
                            messages=messages, 
                            model=model,
                            tools=tools,
                            **chat_kwargs
                        )
                    else:
                        raise
                
                # generation_time_ms 추출 (LLMResponse에서)
                if hasattr(response, 'generation_time_ms'):
                    infer_ms = response.generation_time_ms
                elif hasattr(response, 'eval_duration_ms'):
                    infer_ms = response.eval_duration_ms
                
                # tool_calls 먼저 확인 (Native Tool Calling 우선)
                if hasattr(response, 'tool_calls') and response.tool_calls:
                    tool_calls = response.tool_calls
                    logger.info(f"[_call_llm] ✅ Received {len(tool_calls)} tool calls from LLM (Native Tool Calling)")
                    # tool_calls가 있으면 text가 빈 문자열이어도 정상
                    text = response.text if hasattr(response, 'text') else ""
                    if not text:
                        text = ""  # 빈 문자열로 명시적 설정
                        logger.info("[_call_llm] Empty text with tool_calls - this is normal for Native Tool Calling")
                else:
                    # tool_calls가 없으면 text 확인
                    text = response.text if hasattr(response, 'text') else str(response) if response else ""
                    tool_calls = None

                # [EMPTY_RESPONSE_RECOVERY] tool schema 과대/모델 불안정으로 빈 응답이 오면 단계적 폴백
                # Empty = strip empty OR length < 10. 1) compact tools 2) no tools 3) fast->thinking once per session
                if (not tool_calls) and self._is_empty_llm_response(text):
                    logger.warning(
                        f"[_call_llm] Empty chat response from model={model} with tools={len(tools) if tools else 0}. "
                        "Trying staged fallback."
                    )

                    # 1) 도구 수 축소 (파일 탐색/요약에 필요한 핵심 도구만 유지)
                    compact_tools = None
                    if tools:
                        allowed_tool_names = {
                            "list_directory",
                            "read_file",
                            "search_files",
                            "find_files",
                            "glob_search",
                            "get_file_info",
                            "finish",
                        }
                        compact_tools = [
                            t for t in tools
                            if (t.get("function", {}) or {}).get("name") in allowed_tool_names
                        ]
                        if not compact_tools:
                            compact_tools = tools[: min(12, len(tools))]

                    if compact_tools is not None and len(compact_tools) < (len(tools) if tools else 0):
                        logger.info(
                            f"[_call_llm] Retrying with compact tools: {len(compact_tools)}/{len(tools)}"
                        )
                        compact_response = await self._llm.chat(
                            messages=messages,
                            model=model,
                            tools=compact_tools,
                            **chat_kwargs
                        )
                        compact_tool_calls = getattr(compact_response, "tool_calls", None)
                        compact_text = getattr(compact_response, "text", "") or ""
                        if compact_tool_calls:
                            logger.info(
                                f"[_call_llm] Compact-tools retry recovered tool_calls: {len(compact_tool_calls)}"
                            )
                            compact_infer_ms = getattr(compact_response, 'generation_time_ms', None) or getattr(compact_response, 'eval_duration_ms', None)
                            return compact_text.strip(), compact_tool_calls, compact_infer_ms
                        if isinstance(compact_text, str) and compact_text.strip():
                            logger.info("[_call_llm] Compact-tools retry recovered non-empty text response")
                            compact_infer_ms = getattr(compact_response, 'generation_time_ms', None) or getattr(compact_response, 'eval_duration_ms', None)
                            return compact_text.strip(), None, compact_infer_ms

                    # 2) 도구 없이 재시도
                    logger.info("[_call_llm] Retrying without tools")
                    no_tools_response = await self._llm.chat(
                        messages=messages,
                        model=model,
                        tools=None,
                        **chat_kwargs
                    )
                    no_tools_calls = getattr(no_tools_response, "tool_calls", None)
                    no_tools_text = getattr(no_tools_response, "text", "") or ""
                    no_tools_infer_ms = getattr(no_tools_response, 'generation_time_ms', None) or getattr(no_tools_response, 'eval_duration_ms', None)
                    if no_tools_calls:
                        logger.info(
                            f"[_call_llm] No-tools retry unexpectedly returned tool_calls: {len(no_tools_calls)}"
                        )
                        return no_tools_text.strip(), no_tools_calls, no_tools_infer_ms
                    if isinstance(no_tools_text, str) and no_tools_text.strip():
                        logger.info("[_call_llm] No-tools retry recovered non-empty text response")
                        return no_tools_text.strip(), None, no_tools_infer_ms

                    # 3) fast 모드에서만 상위 모델 1회 폴백 (세션당 1회만, fail closed)
                    if effective_mode == "fast":
                        used = session_state.get("fast_fallback_used", False) if session_state else False
                        if used:
                            try:
                                from mellow_link.core.metrics_collector import get_metrics_collector
                                coll = get_metrics_collector()
                                if coll:
                                    coll.push("FAST_FALLBACK_BLOCKED", 1.0, "count", metric_id=(session_state or {}).get("session_id") or "run")
                            except Exception as e:
                                logger.debug("[AgentBrain] FAST_FALLBACK_BLOCKED metrics push failed: %s", e)
                            # Do not fallback again
                        else:
                            thinking_model = self._llm.get_model_for_mode("thinking")
                            if thinking_model and thinking_model != model:
                                if session_state is not None:
                                    session_state["fast_fallback_used"] = True
                                try:
                                    from mellow_link.core.metrics_collector import get_metrics_collector
                                    coll = get_metrics_collector()
                                    if coll:
                                        coll.push("FAST_FALLBACK_TRIGGERED", 1.0, "count", metric_id=(session_state or {}).get("session_id") or "run")
                                except Exception as e:
                                    logger.debug("[AgentBrain] FAST_FALLBACK_TRIGGERED metrics push failed: %s", e)
                                logger.warning(
                                    f"[_call_llm] Escalating empty-response fallback model: {model} -> {thinking_model}"
                                )
                                upgraded_response = await self._llm.chat(
                                    messages=messages,
                                    model=thinking_model,
                                    tools=None,
                                    **chat_kwargs
                                )
                                upgraded_calls = getattr(upgraded_response, "tool_calls", None)
                                upgraded_text = getattr(upgraded_response, "text", "") or ""
                                upgraded_infer_ms = getattr(upgraded_response, 'generation_time_ms', None) or getattr(upgraded_response, 'eval_duration_ms', None)
                                if upgraded_calls:
                                    return upgraded_text.strip(), upgraded_calls, upgraded_infer_ms
                                if isinstance(upgraded_text, str) and upgraded_text.strip():
                                    return upgraded_text.strip(), None, upgraded_infer_ms
            else:
                # fallback: generate (히스토리를 단일 프롬프트로 합침)
                combined = "\n".join(
                    f"[{m['role']}] {m['content']}" for m in messages
                )
                result = await self._llm.generate(
                    prompt=combined,
                    mode=self._model_mode,
                    tools=tools  # Ollama Native Tool Calling
                )
                text = result.content if hasattr(result, 'content') else str(result) if result else ""
                # infer_ms 추출 (GenerationResult에서)
                if hasattr(result, 'eval_duration_ms'):
                    infer_ms = result.eval_duration_ms
                elif hasattr(result, 'generation_time_ms'):
                    infer_ms = result.generation_time_ms
                # tool_calls 추출
                if hasattr(result, 'tool_calls') and result.tool_calls:
                    tool_calls = result.tool_calls
                    logger.debug(f"[_call_llm] Received {len(tool_calls)} tool calls from generate()")
            
            # [LLM_RESPONSE_VALIDATION] 응답 검증 및 정규화
            # ⚠️ 중요: tool_calls가 있으면 text가 빈 문자열이어도 정상 (Native Tool Calling)
            if tool_calls and len(tool_calls) > 0:
                # Native Tool Calling 모드: text가 빈 문자열이어도 정상
                if not text:
                    text = ""
                else:
                    text = text.strip()
                logger.info(f"[_call_llm] ✅ Native Tool Calling: {len(tool_calls)} tool_calls, text length: {len(text)}")
                return text, tool_calls, infer_ms
            
            # tool_calls가 없는 경우에만 text 검증
            if not text or not isinstance(text, str):
                logger.error(f"[_call_llm] Invalid response type: {type(text)}, value: {repr(text)}")
                # 응답 객체 자체를 확인
                if 'response' in locals() and hasattr(response, '__dict__'):
                    logger.error(f"[_call_llm] Response object attributes: {list(response.__dict__.keys())}")
                    logger.error(f"[_call_llm] Response.text={getattr(response, 'text', 'N/A')}, tool_calls={getattr(response, 'tool_calls', 'N/A')}")
                logger.error("[_call_llm] ⚠️ 빈 응답 + tool_calls 없음 - Ollama가 응답을 생성하지 못했습니다")
                return "", None, infer_ms
            
            # 빈 문자열이나 공백만 있는 경우 (tool_calls 없음)
            text = text.strip()
            if len(text) == 0:
                logger.error("[_call_llm] ⚠️ LLM returned empty response with no tool_calls")
                return "", None, infer_ms
            
            return text, tool_calls, infer_ms
            
        except Exception as e:
            logger.error(f"[_call_llm] LLM call failed: {e}", exc_info=True)
            # 예외 발생 시 빈 문자열 반환 (예외 전파 방지)
            return "", None, infer_ms

    async def _generate_summary_for_max_turns(
        self, user_input: str, steps: List[AgentStep], persona: str = ""
    ) -> str:
        """
        [AUTO_FINISH_ON_MAX_TURNS] 최대 턴 수 도달 시 지금까지의 작업을 요약하여 finish summary 생성.
        
        마지막 Thought가 JSON 형태로 출력된 경우를 방지하고, 지금까지 수행한 작업을 정리하여
        사용자에게 의미있는 답변을 제공.
        """
        if not steps:
            return f"[최대 턴 수 도달] 작업을 시작하지 못했습니다."
        
        # 지금까지 수행한 작업 요약
        executed_tools = []
        successful_observations = []
        for step in steps:
            if step.action:
                executed_tools.append(f"- {step.action.tool}: {step.action.args}")
            if step.observation and not step.observation.startswith("[Error]") and not step.observation.startswith("[차단]"):
                # 성공적인 관찰만 수집 (최대 3개)
                if len(successful_observations) < 3:
                    obs_short = step.observation[:100] + "..." if len(step.observation) > 100 else step.observation
                    successful_observations.append(obs_short)
        
        summary_parts = [
            f"최대 턴 수에 도달하여 작업을 중단했습니다.",
            f"사용자 요청: {user_input[:100]}",
        ]
        
        if executed_tools:
            summary_parts.append(f"\n수행한 작업 ({len(executed_tools)}개):")
            summary_parts.extend(executed_tools[:5])  # 최대 5개만 표시
        
        if successful_observations:
            summary_parts.append(f"\n확인된 결과:")
            summary_parts.extend(successful_observations)
        
        summary_parts.append(f"\n⚠️ 작업이 완전히 완료되지 않았을 수 있습니다. 필요시 다시 시도해주세요.")
        
        return "\n".join(summary_parts)

    async def _apply_persona_to_summary(self, summary: str, persona: str) -> str:
        """
        [PERSONA APPLICATION] finish 도구 호출 후 summary에 페르소나를 적용.
        
        Thought 단계에서는 페르소나가 적용되지 않았으므로, finish 시점에만 페르소나 스타일로 변환.
        """
        if not summary or not persona:
            return summary
        
        persona_prompt = f"""다음 기술적 요약을 페르소나 스타일로 변환하라.

[페르소나 지침]
{persona}

[원본 요약]
{summary}

[지시사항]
- 원본 요약의 핵심 내용은 유지하되, 페르소나 스타일의 말투로 재작성하라.
- 기술적 정보는 정확하게 보존하되, 표현 방식만 페르소나에 맞게 변경하라.
- 페르소나의 특징(말투, 표현 방식 등)을 반영하라.

[변환된 요약]"""
        
        try:
            messages = [
                {"role": "system", "content": "너는 페르소나 스타일 변환 전문가다. 기술적 내용을 정확히 유지하면서 페르소나 스타일로만 변환하라."},
                {"role": "user", "content": persona_prompt}
            ]
            model = self._llm.get_model_for_mode("fast")  # 빠른 모드 사용
            if hasattr(self._llm, "chat"):
                response = await self._llm.chat(messages=messages, model=model)
                return response.text.strip()
            else:
                combined = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
                result = await self._llm.generate(prompt=combined, mode="fast")
                return result.content.strip()
        except Exception as e:
            logger.warning(f"[AgentBrain] 페르소나 적용 중 오류 발생: {e}")
            return summary  # 실패 시 원본 반환

    # ──────────────────────────────────────────
    # VRAM Self-Kill 및 GC
    # ──────────────────────────────────────────

    async def _check_vram_and_kill_if_critical(self, threshold: float = 95.0) -> Optional[str]:
        """
        [VRAM_SELF_KILL] VRAM 사용량을 체크하고, 임계값(기본 95%)을 초과하면 자동 종료 및 GC 수행.
        
        Args:
            threshold: VRAM 임계값 (기본 95%)
            
        Returns:
            "KILLED": Self-Kill이 발생했음
            None: 정상 상태 (계속 진행 가능)
        """
        try:
            # VRAMWatchdog 직접 인스턴스화하여 사용 (순환 import 방지)
            from mellow_link.infra.watchdog import VRAMWatchdog
            
            # VRAMWatchdog가 GPU를 사용할 수 있는지 확인
            if not VRAMWatchdog.is_gpu_available():
                return None
            
            # 임시 watchdog 인스턴스 생성하여 현재 사용량만 체크
            temp_watchdog = VRAMWatchdog(
                warning_threshold=threshold - 5.0,
                critical_threshold=threshold,
                poll_interval=1.0
            )
            
            # 현재 VRAM 사용량 조회
            gpu_info = await temp_watchdog.get_current_usage()
            if not gpu_info:
                # GPU 정보를 가져올 수 없으면 스킵
                return None
            
            usage_percent = gpu_info.usage_percent
            
            # 임계값 초과 체크
            if usage_percent >= threshold:
                logger.critical(
                    f"[VRAM_SELF_KILL] 🚨 VRAM 사용량 {usage_percent:.1f}%가 임계값 {threshold}%를 초과했습니다. "
                    f"Self-Kill 및 가비지 컬렉션을 수행합니다."
                )
                
                # 1. 가비지 컬렉션 강제 실행 (여러 번 수행하여 더 철저하게 정리)
                try:
                    collected_1 = gc.collect()
                    collected_2 = gc.collect()  # 두 번째 GC로 순환 참조 해제
                    total_collected = collected_1 + collected_2
                    logger.info(f"[VRAM_SELF_KILL] 가비지 컬렉션 완료: {total_collected}개 객체 해제 (1차: {collected_1}, 2차: {collected_2})")
                except Exception as gc_error:
                    logger.warning(f"[VRAM_SELF_KILL] GC 실행 중 오류: {gc_error}")
                
                # 2. 추가 메모리 정리 시도
                try:
                    # LLM 컨텍스트 정리 (가능한 경우)
                    if hasattr(self._llm, '_contexts') and isinstance(self._llm._contexts, dict):
                        cleared_count = 0
                        for context_id, context in list(self._llm._contexts.items()):
                            if hasattr(context, 'clear'):
                                context.clear()
                                cleared_count += 1
                        if cleared_count > 0:
                            logger.info(f"[VRAM_SELF_KILL] LLM 컨텍스트 {cleared_count}개 클리어 완료")
                except Exception as ctx_error:
                    logger.debug(f"[VRAM_SELF_KILL] 컨텍스트 정리 실패 (무시): {ctx_error}")
                
                # 3. Self-Kill: 프로세스 종료 신호 반환
                logger.critical(
                    f"[VRAM_SELF_KILL] 프로세스를 안전하게 종료합니다. "
                    f"(VRAM: {usage_percent:.1f}%, 사용량: {gpu_info.used_memory_mb:.0f}MB / {gpu_info.total_memory_mb:.0f}MB)"
                )
                
                # "KILLED"를 반환하여 호출자가 처리하도록 함
                return "KILLED"
            
            # 정상 범위 내
            if usage_percent >= threshold - 5.0:  # 90% 이상이면 경고 로그
                logger.warning(
                    f"[VRAM_SELF_KILL] VRAM 사용량이 높습니다: {usage_percent:.1f}% "
                    f"(임계값: {threshold}%)"
                )
            
            return None
            
        except ImportError:
            # VRAMWatchdog를 import할 수 없으면 스킵
            logger.debug("[VRAM_SELF_KILL] VRAMWatchdog를 사용할 수 없습니다 (스킵)")
            return None
        except Exception as e:
            # VRAM 체크 중 오류 발생 시 경고만 하고 계속 진행
            logger.warning(f"[VRAM_SELF_KILL] VRAM 체크 중 오류 발생 (계속 진행): {e}")
            return None

    # ──────────────────────────────────────────
    # 히스토리 관리
    # ──────────────────────────────────────────

    def _trim_history(self, messages: List[Dict[str, str]], aggressive: bool = False) -> List[Dict[str, str]]:
        """
        컨텍스트 윈도우 초과 시 오래된 메시지를 잘라냄.
        시스템 프롬프트(첫 메시지)는 항상 유지.
        
        Args:
            messages: 메시지 리스트
            aggressive: True이면 더 공격적으로 트리밍 (VRAM 절약용, 이미지 생성 시 사용)
        """
        if len(messages) <= self._context_window + 1 and not aggressive:
            return messages

        system_msg = messages[0]  # system prompt
        
        if aggressive:
            # 공격적 트리밍: 최근 3턴만 유지 (시스템 프롬프트 + 최근 6개 메시지)
            if len(messages) > 7:
                recent = messages[-6:]
                return [system_msg] + recent
            return messages
        
        recent = messages[-(self._context_window):]
        return [system_msg] + recent

    # ──────────────────────────────────────────
    # 경험 관리 (ExperienceHelper로 위임)
    # ──────────────────────────────────────────
    # _build_context_summary, _record_experience_ledger, _archive_experience,
    # _trigger_analysis, _trigger_diagnosis 는 ExperienceHelper에 위임됨.
    # run() 메서드 내에서 self._experience_helper.xxx() 호출로 사용.
