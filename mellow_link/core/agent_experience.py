"""
Agent 경험 기록 / 아카이빙 / 분석 트리거.

AgentBrain에서 경험 메모리 관련 로직을 분리한 헬퍼 클래스.
AgentBrain은 이 클래스의 인스턴스를 생성하여 경험 관리를 위임한다.

의존성:
  - agent_schemas.py : AgentResult, AgentStep
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from weakref import WeakSet

from mellow_link.core.agent_schemas import AgentResult, AgentStep

logger = logging.getLogger(__name__)


class ExperienceHelper:
    """
    AgentBrain의 경험 기록/아카이빙/분석 로직을 캡슐화한 헬퍼 클래스.

    사용법:
        helper = ExperienceHelper(
            archiver=archiver_instance,
            log_analyzer=log_analyzer_instance,
            experience_provider=experience_provider_instance,
            llm_service=llm_service_instance,
            analysis_interval=10,
            diagnosis_interval=50,
        )
    """

    _instances: "WeakSet[ExperienceHelper]" = WeakSet()

    def __init__(
        self,
        *,
        archiver=None,
        log_analyzer=None,
        experience_provider=None,
        llm_service=None,
        enable_memory_archiving: bool = True,
        analysis_interval: int = 10,
        diagnosis_interval: int = 50,
    ):
        self._archiver = archiver
        self._log_analyzer = log_analyzer
        self._experience_provider = experience_provider
        self._llm = llm_service
        self._enable_memory_archiving = enable_memory_archiving
        self._task_completion_count = 0
        self._analysis_interval = analysis_interval
        self._diagnosis_interval = diagnosis_interval
        self._analysis_tasks: set = set()   # GC 방지
        self._diagnosis_tasks: set = set()  # GC 방지
        self._ledger_tasks: set = set()     # 종료 시 정리할 background ledger tasks
        self.__class__._instances.add(self)

    def build_context_summary(
        self,
        context: Optional[List[Dict[str, str]]],
        persona: str,
    ) -> str:
        """
        컨텍스트 요약 생성 (아카이빙용).

        Args:
            context: 대화 히스토리
            persona: 페르소나 텍스트

        Returns:
            컨텍스트 요약 문자열
        """
        parts = []

        if persona:
            parts.append(f"페르소나: {persona[:100]}...")

        if context:
            parts.append(f"이전 대화 히스토리: {len(context)}개 메시지")

        return " | ".join(parts) if parts else "기본 컨텍스트"

    async def record_experience_ledger(
        self,
        run_state: Dict[str, Any],
        start_time: datetime,
        steps: List[AgentStep],
        user_input: str,
    ) -> None:
        """
        ✅ verified: 경험 장부 훅 — timestamp, intent_type, is_success, latency_ms, used_tools, error_message
        비동기로 기록하며, 실패해도 메인 플로우에 영향 없음.
        """
        try:
            result = run_state.get("result")
            error = run_state.get("error")
            now = datetime.now()
            timestamp = now
            intent_type = (user_input or "chat")[:2000]
            latency_ms = (now - start_time).total_seconds() * 1000.0
            used_tools = [
                s.action.tool for s in (steps or [])
                if getattr(s, "action", None) and s.action is not None
            ]
            is_success = 1 if (result and result.finish_reason == "finish_tool") else 0
            if error is not None:
                is_success = 0
            error_message = (str(error)[:2000]) if error else None

            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: db.record_ledger_entry(
                    timestamp=timestamp,
                    intent_type=intent_type,
                    is_success=is_success,
                    latency_ms=latency_ms,
                    used_tools=used_tools,
                    error_message=error_message,
                ),
            )
        except Exception as e:
            logger.debug("[AgentBrain] Experience ledger record failed: %s", e)

    def schedule_record_experience_ledger(
        self,
        run_state: Dict[str, Any],
        start_time: datetime,
        steps: List[AgentStep],
        user_input: str,
    ) -> None:
        """Schedule ledger recording without blocking the main response path."""
        task = asyncio.create_task(
            self.record_experience_ledger(run_state, start_time, steps, user_input)
        )
        self._ledger_tasks.add(task)
        task.add_done_callback(self._ledger_tasks.discard)

    async def shutdown(self) -> None:
        """Drain background helper tasks to avoid pending-task warnings on shutdown."""
        pending = [
            task
            for task in (
                list(self._ledger_tasks)
                + list(self._analysis_tasks)
                + list(self._diagnosis_tasks)
            )
            if not task.done()
        ]
        if not pending:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    @classmethod
    async def shutdown_all(cls) -> None:
        helpers = list(cls._instances)
        if not helpers:
            return
        await asyncio.gather(*(helper.shutdown() for helper in helpers), return_exceptions=True)

    async def archive_experience(
        self,
        user_input: str,
        context_summary: str,
        result: AgentResult,
        start_time: datetime,
    ) -> None:
        """
        경험 메모리 아카이빙 (비동기, 실패해도 메인 플로우에 영향 없음).
        
        BENCH_PROFILE 모드에서는 인사이트 생성 비활성화.

        Args:
            user_input: 사용자 입력
            context_summary: 컨텍스트 요약
            result: AgentResult
            start_time: 시작 시간
        """
        if not self._enable_memory_archiving or not self._archiver:
            return

        try:
            from mellow_link.infra.archiver import TaskData

            task_data = TaskData(
                user_input=user_input,
                context_summary=context_summary,
                agent_result=result,
                start_time=start_time,
                end_time=datetime.now(),
            )

            experience_id = await self._archiver.archive(task_data)
            if experience_id:
                logger.info("[ExperienceHelper] experience_archived experience_id=%s", experience_id)

                # BENCH_PROFILE 모드에서는 인사이트 생성 비활성화
                try:
                    import os
                    from mellow_link.config import get_settings
                    settings = get_settings()
                    bench_profile = getattr(settings, "bench_profile", False) or os.getenv("BENCH_PROFILE", "").strip().lower() in ("1", "true", "yes")
                except Exception:
                    bench_profile = False
                
                if not bench_profile:
                    # 태스크 완료 카운트 증가 및 주기적 분석/진단 실행 (백그라운드)
                    self._task_completion_count += 1

                    # 주기적 로그 분석 실행
                    if self._task_completion_count >= self._analysis_interval:
                        # 백그라운드에서 실행하여 태스크 결과 반환 지연 방지
                        # 태스크를 멤버 세트에 보관하여 GC 경고 방지
                        analysis_task = asyncio.create_task(self._trigger_analysis())
                        self._analysis_tasks.add(analysis_task)
                        analysis_task.add_done_callback(self._analysis_tasks.discard)  # 완료 시 자동 제거

                    # 주기적 성능 진단 실행 (매 50회 완료 시)
                    if self._task_completion_count >= self._diagnosis_interval:
                        diagnosis_task = asyncio.create_task(self._trigger_diagnosis())
                        self._diagnosis_tasks.add(diagnosis_task)
                        diagnosis_task.add_done_callback(self._diagnosis_tasks.discard)
                        self._task_completion_count = 0  # 카운터 리셋 (진단 후)
                else:
                    logger.debug("[ExperienceHelper] BENCH_PROFILE mode: skipping insight generation")

        except Exception as e:
            # 아카이빙 실패는 로그만 남기고 메인 플로우는 계속 진행
            logger.warning("[ExperienceHelper] archive_experience_failed error=%s", e)

    async def _trigger_analysis(self) -> None:
        """
        주기적 로그 분석 트리거 (비동기, 실패해도 메인 플로우에 영향 없음).
        """
        if not self._log_analyzer:
            return

        try:
            logger.info("[AgentBrain] Triggering periodic log analysis...")
            insights = await self._log_analyzer.analyze(llm_service=self._llm)

            if insights:
                logger.info(f"[AgentBrain] Analysis complete: {len(insights)} insights generated")

                # 높은 신뢰도의 통찰을 ExperienceProvider에 전달하여 가중치 적용
                high_confidence_insights = [
                    insight for insight in insights
                    if insight.confidence >= 0.7
                ]

                if high_confidence_insights and self._experience_provider:
                    # ExperienceProvider의 통찰 캐시 갱신
                    self._experience_provider.refresh_insights_cache()
                    logger.debug(
                        f"[AgentBrain] High-confidence insights available: "
                        f"{len(high_confidence_insights)} items (cache refreshed)"
                    )

        except Exception as e:
            logger.warning(f"[AgentBrain] Failed to trigger analysis: {e}")

    async def _trigger_diagnosis(self) -> None:
        """
        주기적 성능 진단 트리거 (비동기, 실패해도 메인 플로우에 영향 없음).
        """
        try:
            from mellow_link.core.diagnosis_service import get_diagnosis_service

            logger.info("[AgentBrain] Triggering periodic performance diagnosis...")
            diagnosis_service = get_diagnosis_service()
            report = diagnosis_service.run_diagnosis()

            # 대시보드 출력
            dashboard_text = diagnosis_service.generate_dashboard_text(report)
            logger.info(f"[AgentBrain] Performance Diagnosis Report:\n{dashboard_text}")

            # ExperienceProvider에 진단 요약 연결 준비
            if self._experience_provider:
                # 진단 리포트의 요약을 ExperienceProvider에 전달할 수 있도록 준비
                # (실제 주입은 ExperienceProvider에서 필요 시 조회하도록 구현)
                logger.debug(
                    f"[AgentBrain] Diagnosis summary available: "
                    f"{report.health_status} status"
                )

        except Exception as e:
            logger.warning(f"[AgentBrain] Failed to trigger diagnosis: {e}")
