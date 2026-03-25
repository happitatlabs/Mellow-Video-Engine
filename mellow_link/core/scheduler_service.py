"""
Scheduler Service - 자율 태스크 스케줄러

시간 기반 자율 행동을 위한 스케줄러 서비스입니다.
주기적인 자가 진단, 정보 수집, 예약된 작업을 수행합니다.
"""

import json
import uuid
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from mellow_link.infra.memory_database import (
    MemoryDatabase,
    ScheduledTask,
    get_memory_db
)

logger = logging.getLogger(__name__)


# =============================================================================
# Schedule Expression Parser
# =============================================================================

def parse_schedule_expr(schedule_expr: str) -> Optional[timedelta]:
    """
    스케줄 표현식을 파싱하여 다음 실행 시각을 계산.
    
    지원 형식:
    - interval_seconds: 숫자 (예: "3600" = 1시간마다)
    - cron: "cron:0 */6 * * *" (6시간마다, 추후 확장)
    
    Args:
        schedule_expr: 스케줄 표현식
        
    Returns:
        다음 실행까지의 시간 간격 (timedelta) 또는 None (파싱 실패)
    """
    try:
        # interval_seconds 형식 (숫자)
        if schedule_expr.isdigit():
            seconds = int(schedule_expr)
            return timedelta(seconds=seconds)
        
        # cron 형식 (추후 확장 가능)
        if schedule_expr.startswith("cron:"):
            # 간단한 cron 파싱 (기본적인 패턴만 지원)
            cron_parts = schedule_expr[5:].strip().split()
            if len(cron_parts) >= 5:
                # 분 시 일 월 요일
                # 예: "0 */6 * * *" = 매 6시간마다
                minute, hour, day, month, weekday = cron_parts[:5]
                
                # 간단한 패턴: */N 형식만 지원
                if hour.startswith("*/"):
                    interval_hours = int(hour[2:])
                    return timedelta(hours=interval_hours)
                elif minute.startswith("*/"):
                    interval_minutes = int(minute[2:])
                    return timedelta(minutes=interval_minutes)
        
        logger.warning(f"[SchedulerService] Unsupported schedule expression: {schedule_expr}")
        return None
        
    except Exception as e:
        logger.error(f"[SchedulerService] Failed to parse schedule expression: {e}")
        return None


def calculate_next_run(
    schedule_expr: str,
    last_run_at: Optional[datetime] = None
) -> Optional[datetime]:
    """
    다음 실행 시각 계산.
    
    Args:
        schedule_expr: 스케줄 표현식
        last_run_at: 마지막 실행 시각 (None이면 현재 시각 기준)
        
    Returns:
        다음 실행 시각 또는 None (파싱 실패)
    """
    delta = parse_schedule_expr(schedule_expr)
    if delta is None:
        return None
    
    base_time = last_run_at or datetime.now()
    return base_time + delta


# =============================================================================
# Scheduler Service
# =============================================================================

class SchedulerService:
    """
    자율 태스크 스케줄러 서비스.
    
    백그라운드에서 주기적으로 실행 대기 중인 태스크를 찾아 실행합니다.
    """

    def __init__(
        self,
        db: Optional[MemoryDatabase] = None,
        agent_brain: Optional[Any] = None,
        check_interval_seconds: int = 60
    ):
        """
        Args:
            db: MemoryDatabase 인스턴스 (None이면 싱글톤 사용)
            agent_brain: AgentBrain 인스턴스 (태스크 실행용)
            check_interval_seconds: 태스크 확인 주기 (초, 기본 60초)
        """
        self.db = db or get_memory_db()
        self.agent_brain = agent_brain
        self.check_interval = check_interval_seconds
        self._is_running = False
        self._task_loop: Optional[asyncio.Task] = None
        logger.info(f"[SchedulerService] Initialized (check_interval: {check_interval_seconds}s)")

    async def start(self) -> None:
        """
        스케줄러 백그라운드 루프 시작.
        """
        if self._is_running:
            logger.warning("[SchedulerService] Already running")
            return
        
        self._is_running = True
        self._task_loop = asyncio.create_task(self._run_loop())
        
        # 자율 진단 작업 등록 (매일 자정에 실행)
        self._register_diagnosis_task()
        # 진화 트리거 등록 (ENABLE_EVOLUTION_TRIGGER 또는 프로토콜 evolution_trigger.enabled)
        self._register_evolution_trigger_task()
        # ✅ verified: 경험 장부 인사이트 분석 (매 6시간)
        self._register_log_analyzer_task()
        # ✅ verified: 진화 적용 후 피드백 루프 (매 30분)
        self._register_evolution_feedback_task()
        # ✅ verified: 인사이트 → SMART 목표 자동 생성 (매 12시간)
        self._register_goal_generation_task()

        logger.info("[SchedulerService] Started")
    
    def _register_diagnosis_task(self) -> None:
        """
        성능 자가 진단 작업을 스케줄러에 등록 (중복 방지).
        """
        try:
            # 이미 "성능 자가 진단" 태스크가 있으면 중복 등록 스킵
            existing = self.db.get_all_scheduled_tasks(status=None)
            for t in existing:
                if t.task_name == "성능 자가 진단":
                    logger.info(
                        "[SchedulerService] Diagnosis task already registered, skipping duplicate"
                    )
                    return
            
            # 매일 24시간마다 실행 (86400초)
            task_id = self.add_task(
                task_name="성능 자가 진단",
                task_type="diagnosis_task",
                schedule_expr="86400",  # 24시간마다
                args={},
                initial_delay_seconds=0
            )
            
            if task_id:
                logger.info(f"[SchedulerService] Diagnosis task registered: {task_id}")
            else:
                logger.warning("[SchedulerService] Failed to register diagnosis task")
                
        except Exception as e:
            logger.warning(f"[SchedulerService] Failed to register diagnosis task: {e}")

    def _register_evolution_trigger_task(self) -> None:
        """진화 트리거 스케줄 태스크 등록 (ENABLE_EVOLUTION_TRIGGER 또는 프로토콜)."""
        try:
            from mellow_link.core.evolution_trigger import (
                is_evolution_trigger_enabled,
                get_evolution_trigger_schedule_seconds,
            )
            if not is_evolution_trigger_enabled():
                logger.info("[SchedulerService] Evolution trigger disabled, skipping registration")
                return
            existing = self.db.get_all_scheduled_tasks(status=None)
            for t in existing:
                if t.task_name == "진화 트리거":
                    logger.info("[SchedulerService] Evolution trigger already registered")
                    return
            schedule_sec = get_evolution_trigger_schedule_seconds()
            task_id = self.add_task(
                task_name="진화 트리거",
                task_type="evolution_task",
                schedule_expr=str(schedule_sec),
                args={},
                initial_delay_seconds=60,
            )
            if task_id:
                logger.info(f"[SchedulerService] Evolution trigger registered: {task_id} (every {schedule_sec}s)")
            else:
                logger.warning("[SchedulerService] Failed to register evolution trigger")
        except Exception as e:
            logger.warning(f"[SchedulerService] Evolution trigger registration failed: {e}")

    def _register_log_analyzer_task(self) -> None:
        """✅ verified: experience_ledger 기반 인사이트 분석 태스크 등록 (매 6시간)."""
        try:
            existing = self.db.get_all_scheduled_tasks(status=None)
            for t in existing:
                if t.task_name == "경험 장부 인사이트 분석":
                    logger.info("[SchedulerService] Log analyzer task already registered")
                    return
            # 6시간 = 21600초
            task_id = self.add_task(
                task_name="경험 장부 인사이트 분석",
                task_type="log_analyzer_task",
                schedule_expr="21600",
                args={},
                initial_delay_seconds=300,
            )
            if task_id:
                logger.info(f"[SchedulerService] Log analyzer task registered: {task_id}")
            else:
                logger.warning("[SchedulerService] Failed to register log analyzer task")
        except Exception as e:
            logger.warning(f"[SchedulerService] Log analyzer registration failed: {e}")

    def _register_evolution_feedback_task(self) -> None:
        """✅ verified: 적용 후 experience_ledger 모니터링·FAILED 마킹·롤백 권고 (매 30분)."""
        try:
            existing = self.db.get_all_scheduled_tasks(status=None)
            for t in existing:
                if t.task_name == "진화 피드백 루프":
                    logger.info("[SchedulerService] Evolution feedback task already registered")
                    return
            task_id = self.add_task(
                task_name="진화 피드백 루프",
                task_type="evolution_feedback_task",
                schedule_expr="1800",
                args={},
                initial_delay_seconds=120,
            )
            if task_id:
                logger.info(f"[SchedulerService] Evolution feedback task registered: {task_id}")
        except Exception as e:
            logger.warning(f"[SchedulerService] Evolution feedback registration failed: {e}")

    def _register_goal_generation_task(self) -> None:
        """✅ verified: 미처리 인사이트 → Tower SMART 목표 생성 주기 등록 (매 12시간)."""
        try:
            existing = self.db.get_all_scheduled_tasks(status=None)
            for t in existing:
                if t.task_name == "인사이트 기반 목표 생성":
                    logger.info("[SchedulerService] Goal generation task already registered")
                    return
            # 12시간 = 43200초; parent_goal_id는 args_json에서 선택적 전달
            task_id = self.add_task(
                task_name="인사이트 기반 목표 생성",
                task_type="goal_generation_task",
                schedule_expr="43200",
                args={},
                initial_delay_seconds=600,
            )
            if task_id:
                logger.info(f"[SchedulerService] Goal generation task registered: {task_id} (every 12h)")
            else:
                logger.warning("[SchedulerService] Failed to register goal generation task")
        except Exception as e:
            logger.warning(f"[SchedulerService] Goal generation task registration failed: {e}")

    async def stop(self) -> None:
        """
        스케줄러 백그라운드 루프 중지.
        """
        if not self._is_running:
            return
        
        self._is_running = False
        if self._task_loop:
            self._task_loop.cancel()
            try:
                await self._task_loop
            except asyncio.CancelledError:
                pass
        
        logger.info("[SchedulerService] Stopped")

    async def _run_loop(self) -> None:
        """
        메인 스케줄러 루프 (백그라운드 실행).
        
        1분마다 실행 대기 중인 태스크를 찾아 실행합니다.
        """
        logger.info("[SchedulerService] Background loop started")
        
        while self._is_running:
            try:
                # 실행 대기 중인 태스크 조회
                pending_tasks = self.db.get_pending_tasks()
                
                if pending_tasks:
                    logger.info(f"[SchedulerService] Found {len(pending_tasks)} pending tasks")
                    
                    # 각 태스크를 순차적으로 실행 (병렬 실행은 추후 확장)
                    for task in pending_tasks:
                        await self._execute_task(task)
                
                # 다음 확인까지 대기
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                logger.info("[SchedulerService] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[SchedulerService] Loop error: {e}", exc_info=True)
                # 에러 발생 시에도 루프는 계속 실행 (견고성)
                await asyncio.sleep(self.check_interval)

    async def _execute_task(self, task: ScheduledTask) -> None:
        """
        개별 태스크 실행 (지능화된 실패 처리 및 목표 트리 연동).
        
        Args:
            task: 실행할 태스크
        """
        task_id = task.id
        
        try:
            # 목표 트리 연결: 미완료 목표 트리 확인 및 정리
            if task.root_goal_id:
                await self._check_and_resume_goal_tree(task.root_goal_id, task)
            
            # 태스크 상태를 RUNNING으로 변경
            self.db.update_task_result(task_id, status="RUNNING")
            
            logger.info(f"[SchedulerService] Executing task: {task.task_name} ({task_id})")
            
            result = None
            execution_success = False
            
            # AgentBrain이 있으면 태스크 실행
            if self.agent_brain:
                # args_json 파싱
                try:
                    args = json.loads(task.args_json) if task.args_json else {}
                except json.JSONDecodeError:
                    args = {}
                
                # task_type에 따라 실행 방식 결정
                if task.task_type == "agent_task":
                    # AgentBrain.run() 호출
                    user_input = args.get("user_input", task.task_name)
                    context = args.get("context", [])
                    persona = args.get("persona", "")
                    root_goal_id = task.root_goal_id  # 목표 ID 전달
                    
                    result = await self.agent_brain.run(
                        user_input=user_input,
                        context=context,
                        persona=persona,
                        root_goal_id=root_goal_id  # 목표 완료 시 상태 업데이트용
                    )
                    
                elif task.task_type == "diagnosis_task":
                    # 성능 자가 진단 실행
                    from mellow_link.core.diagnosis_service import get_diagnosis_service

                    diagnosis_service = get_diagnosis_service()
                    report = diagnosis_service.run_diagnosis()

                    # 대시보드 출력
                    dashboard_text = diagnosis_service.generate_dashboard_text(report)
                    logger.info(f"[SchedulerService] Diagnosis Report:\n{dashboard_text}")

                    # 성공으로 간주 (진단은 정보 수집이므로)
                    execution_success = True
                    result = None  # AgentResult가 아니므로 None

                elif task.task_type == "evolution_task":
                    # 진화 트리거: Tower 판단 → run_evolution_cycle
                    from mellow_link.core.evolution_trigger import run_evolution_tick

                    success, proposal, msg = await run_evolution_tick()
                    execution_success = success
                    result = None
                    logger.info(f"[SchedulerService] Evolution trigger: {msg}")

                elif task.task_type == "log_analyzer_task":
                    # ✅ verified: 경험 장부 인사이트 분석 (비동기, 비간섭)
                    from mellow_link.services.log_analyzer import run_ledger_insight_analysis
                    await run_ledger_insight_analysis(limit=200)
                    execution_success = True
                    result = None
                    logger.info("[SchedulerService] Log analyzer task completed")

                elif task.task_type == "evolution_feedback_task":
                    # ✅ verified: 적용 전/후 성공률 비교, 에러율 급증 시 FAILED 마킹·롤백 권고
                    from mellow_link.core.evolution_manager import get_evolution_manager
                    em = get_evolution_manager()
                    for log_id, ok, msg in em.run_post_apply_feedback_for_recent_applied(window_hours=2.0):
                        logger.info("[SchedulerService] Evolution feedback %s: %s", log_id, msg)
                    execution_success = True
                    result = None

                elif task.task_type == "goal_generation_task":
                    # ✅ verified: 미처리 인사이트 → Tower SMART 목표 생성 → GoalManager 등록
                    from mellow_link.core.goal_manager import get_goal_manager
                    gm = get_goal_manager()
                    try:
                        args = json.loads(task.args_json) if isinstance(task.args_json, str) and task.args_json else (task.args_json if isinstance(task.args_json, dict) else {})
                    except Exception:
                        args = {}
                    parent_goal_id = args.get("parent_goal_id") or None
                    created_goals = await gm.generate_goals_from_insights(
                        parent_goal_id=parent_goal_id,
                        send_notification=True,
                    )
                    execution_success = True
                    result = None
                    logger.info("[SchedulerService] Goal generation task completed, created %s goals", len(created_goals))

                # finish_reason 기반 실패 처리 (agent_task만 해당)
                if result and hasattr(result, 'finish_reason'):
                    if result.finish_reason == "security_violation":
                        # 보안 위반: 즉시 DISABLED 및 강력한 경고
                        self.db.update_task_result(
                            task_id,
                            status="DISABLED",
                            consecutive_failures=task.consecutive_failures + 1
                        )
                        logger.critical(
                            f"[SchedulerService] SECURITY VIOLATION: Task {task.task_name} ({task_id}) "
                            f"disabled due to security violation. Manual review required!"
                        )
                        return
                    
                    elif result.finish_reason in ("error", "max_turns", "parse_fallback"):
                        # 일반 에러: consecutive_failures 증가
                        new_failures = task.consecutive_failures + 1
                        
                        if new_failures >= 5:
                            # 5회 연속 실패: 자동 DISABLED
                            self.db.update_task_result(
                                task_id,
                                status="DISABLED",
                                consecutive_failures=new_failures
                            )
                            logger.warning(
                                f"[SchedulerService] Task {task.task_name} ({task_id}) "
                                f"auto-disabled after {new_failures} consecutive failures"
                            )
                            return
                        else:
                            # 실패 횟수만 증가 (다음 실행 시도)
                            self.db.update_task_result(
                                task_id,
                                consecutive_failures=new_failures
                            )
                            logger.warning(
                                f"[SchedulerService] Task {task.task_name} failed "
                                f"({new_failures}/5 consecutive failures)"
                            )
                    else:
                        # 성공: consecutive_failures 리셋
                        execution_success = True
                        self.db.update_task_result(
                            task_id,
                            consecutive_failures=0
                        )
                    
                    if result:
                        logger.info(
                            f"[SchedulerService] Task completed: {task.task_name} "
                            f"(turns: {result.total_turns}, reason: {result.finish_reason})"
                        )
                else:
                    logger.warning(f"[SchedulerService] Unknown task_type: {task.task_type}")
            
            # 성공한 경우에만 다음 실행 시각 계산
            if execution_success:
                next_run_at = calculate_next_run(task.schedule_expr, datetime.now())
                
                if next_run_at:
                    # 주기적 작업: 다음 실행 시각 설정 및 ENABLED로 복귀
                    self.db.update_task_result(
                        task_id,
                        next_run_at=next_run_at,
                        status="ENABLED"
                    )
                    logger.debug(f"[SchedulerService] Next run scheduled: {next_run_at.isoformat()}")
                else:
                    # 일회성 작업: DISABLED로 변경
                    self.db.update_task_result(task_id, status="DISABLED")
                    logger.info(f"[SchedulerService] One-time task completed: {task.task_name}")
            else:
                # 실패한 경우에도 다음 실행 시각 계산 (재시도, 단 consecutive_failures는 이미 증가됨)
                next_run_at = calculate_next_run(task.schedule_expr, datetime.now())
                if next_run_at:
                    self.db.update_task_result(
                        task_id,
                        next_run_at=next_run_at,
                        status="ENABLED"
                    )
                
        except Exception as e:
            logger.error(f"[SchedulerService] Task execution failed: {task.task_name} - {e}", exc_info=True)
            
            # 예외 발생 시 consecutive_failures 증가
            new_failures = task.consecutive_failures + 1
            if new_failures >= 5:
                self.db.update_task_result(
                    task_id,
                    status="DISABLED",
                    consecutive_failures=new_failures
                )
            else:
                next_run_at = calculate_next_run(task.schedule_expr, datetime.now())
                if next_run_at:
                    self.db.update_task_result(
                        task_id,
                        next_run_at=next_run_at,
                        status="ENABLED",
                        consecutive_failures=new_failures
                    )

    async def _check_and_resume_goal_tree(
        self,
        root_goal_id: str,
        task: ScheduledTask
    ) -> None:
        """
        목표 트리 확인 및 재개/정리 로직.
        
        동일 태스크의 미완료(IN_PROGRESS) 목표 트리가 있으면 재개하거나 정리합니다.
        
        Args:
            root_goal_id: 루트 목표 ID
            task: 현재 태스크
        """
        try:
            from mellow_link.core.goal_manager import get_goal_manager
            
            goal_manager = get_goal_manager()
            root_goal = goal_manager.db.get_goal(root_goal_id)
            
            if not root_goal:
                return
            
            # 목표가 IN_PROGRESS 상태인지 확인
            if root_goal.status == "IN_PROGRESS":
                # 미완료 목표 트리가 있으면 정리 (FAILED로 마킹 또는 재개 결정)
                children = goal_manager.db.get_children_goals(root_goal_id)
                incomplete_children = [
                    child for child in children
                    if child.status not in ("DONE", "FAILED")
                ]
                
                if incomplete_children:
                    # 미완료 자식이 있으면 루트를 FAILED로 마킹 (새 실행 시작)
                    logger.info(
                        f"[SchedulerService] Previous goal tree {root_goal_id} incomplete, "
                        f"marking as FAILED to start fresh execution"
                    )
                    goal_manager.update_goal_status(root_goal_id, "FAILED")
                else:
                    # 모든 자식이 완료되었으면 루트도 완료 처리
                    goal_manager.update_goal_status(root_goal_id, "DONE")
                    
        except Exception as e:
            logger.warning(f"[SchedulerService] Failed to check goal tree: {e}")

    def add_task(
        self,
        task_name: str,
        task_type: str,
        schedule_expr: str,
        args: Optional[Dict[str, Any]] = None,
        initial_delay_seconds: int = 0,
        root_goal_id: Optional[str] = None
    ) -> Optional[str]:
        """
        새 예약 태스크 추가.
        
        Args:
            task_name: 작업 명칭
            task_type: 작업 유형 (예: "agent_task")
            schedule_expr: 스케줄 표현식 (예: "3600" = 1시간마다)
            args: 작업 실행 시 필요한 매개변수
            initial_delay_seconds: 첫 실행까지의 지연 시간 (초)
            root_goal_id: 연결된 목표 트리 루트 ID (선택사항)
            
        Returns:
            생성된 태스크 ID 또는 None (실패 시)
        """
        try:
            task_id = str(uuid.uuid4())
            args_json = json.dumps(args or {}, ensure_ascii=False)
            
            # 첫 실행 시각 계산
            first_run = datetime.now() + timedelta(seconds=initial_delay_seconds)
            
            task = ScheduledTask(
                id=task_id,
                task_name=task_name,
                task_type=task_type,
                schedule_expr=schedule_expr,
                args_json=args_json,
                next_run_at=first_run,
                status="ENABLED",
                consecutive_failures=0,
                root_goal_id=root_goal_id,
                created_at=datetime.now()
            )
            
            if self.db.add_scheduled_task(task):
                logger.info(
                    f"[SchedulerService] Task added: {task_name} "
                    f"(first_run: {first_run.isoformat()}, root_goal: {root_goal_id})"
                )
                return task_id
            else:
                return None
                
        except Exception as e:
            logger.error(f"[SchedulerService] Failed to add task: {e}")
            return None

    def disable_task(self, task_id: str) -> bool:
        """
        태스크 비활성화.
        
        Args:
            task_id: 태스크 ID
            
        Returns:
            성공 여부
        """
        try:
            return self.db.update_task_result(task_id, status="DISABLED")
        except Exception as e:
            logger.error(f"[SchedulerService] Failed to disable task: {e}")
            return False

    def enable_task(self, task_id: str) -> bool:
        """
        태스크 활성화.
        
        Args:
            task_id: 태스크 ID
            
        Returns:
            성공 여부
        """
        try:
            return self.db.update_task_result(task_id, status="ENABLED")
        except Exception as e:
            logger.error(f"[SchedulerService] Failed to enable task: {e}")
            return False

    def get_all_tasks(self, status: Optional[str] = None) -> List[ScheduledTask]:
        """
        모든 예약된 태스크 조회.
        
        Args:
            status: 상태 필터 (None이면 모든 상태)
            
        Returns:
            태스크 리스트
        """
        return self.db.get_all_scheduled_tasks(status=status)


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_scheduler_instance: Optional[SchedulerService] = None


def get_scheduler_service(
    db: Optional[MemoryDatabase] = None,
    agent_brain: Optional[Any] = None
) -> SchedulerService:
    """
    SchedulerService 싱글톤 인스턴스 반환.
    
    Args:
        db: MemoryDatabase 인스턴스 (첫 호출 시에만 적용)
        agent_brain: AgentBrain 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        SchedulerService 인스턴스
    """
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = SchedulerService(db=db, agent_brain=agent_brain)
    return _scheduler_instance
