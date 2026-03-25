"""
Experience Provider - 경험 소환 및 프롬프트 변환

과거의 성공/실패 사례를 검색하여 현재 ReAct 루프에 지침으로 제공합니다.
검색된 experience_ledger 레코드를 LLM이 이해하기 쉬운 Few-shot Prompt 형태로 변환합니다.
"""

import logging
from typing import Optional, List

from mellow_link.infra.memory_database import (
    MemoryDatabase,
    ExperienceRecord,
    get_memory_db
)

logger = logging.getLogger(__name__)


# =============================================================================
# Experience Provider
# =============================================================================

class ExperienceProvider:
    """
    경험 소환 및 프롬프트 변환 관리자.
    
    과거 경험을 검색하여 Few-shot Prompt 형태로 변환하여 제공합니다.
    """

    def __init__(self, db: Optional[MemoryDatabase] = None):
        """
        Args:
            db: MemoryDatabase 인스턴스 (None이면 싱글톤 사용)
        """
        self.db = db or get_memory_db()
        self._recent_insights: Optional[List] = None  # 최근 통찰 캐시
        logger.info("[ExperienceProvider] Initialized")

    def retrieve_relevant_experiences(
        self,
        task_intent: str,
        task_hash: Optional[str] = None,
        limit: int = 3
    ) -> List[ExperienceRecord]:
        """
        관련 경험 검색 (통찰 기반 가중치 적용).
        
        높은 신뢰도의 통찰이 있는 경우, 해당 패턴과 관련된 경험을 우선적으로 반환합니다.
        
        Args:
            task_intent: 작업 의도
            task_hash: 작업 해시 (선택사항)
            limit: 최대 반환 개수
            
        Returns:
            관련 경험 레코드 리스트 (통찰 기반 가중치 적용)
        """
        try:
            # 기본 경험 검색
            experiences = self.db.get_relevant_experiences(
                task_intent=task_intent,
                task_hash=task_hash,
                limit=limit * 2  # 더 많이 가져와서 필터링
            )
            
            # 복구 성공 사례(RECOVERY_SUCCESS)를 우선 참고하도록 정렬
            experiences.sort(
                key=lambda e: (0 if (e.critique_tag and "RECOVERY_SUCCESS" in e.critique_tag) else 1)
            )
            
            # 높은 신뢰도의 통찰 확인
            high_confidence_insights = self._get_high_confidence_insights()
            
            if high_confidence_insights and experiences:
                # 통찰과 관련된 경험에 가중치 부여
                experiences = self._apply_insight_weights(experiences, high_confidence_insights)
            
            # 최종 limit만큼만 반환
            final_experiences = experiences[:limit]
            logger.debug(f"[ExperienceProvider] Retrieved {len(final_experiences)} experiences")
            return final_experiences
            
        except Exception as e:
            logger.error(f"[ExperienceProvider] Failed to retrieve experiences: {e}")
            return []

    def _get_high_confidence_insights(self) -> List:
        """
        높은 신뢰도의 최근 통찰 조회 (캐시 사용, 시간 필터 적용).
        
        Recency Bias 방지 및 Drift 해결을 위해 7일 이내의 통찰만 반환합니다.
        
        Returns:
            통찰 리스트
        """
        try:
            if self._recent_insights is None:
                from mellow_link.infra.memory_database import BehaviorInsight
                insights = self.db.get_recent_insights(
                    limit=10,
                    min_confidence=0.7,
                    days_threshold=7  # 7일 이내의 통찰만 로드
                )
                self._recent_insights = insights
            return self._recent_insights or []
        except Exception as e:
            logger.debug(f"[ExperienceProvider] Failed to get insights: {e}")
            return []

    def _apply_insight_weights(
        self,
        experiences: List[ExperienceRecord],
        insights: List
    ) -> List[ExperienceRecord]:
        """
        통찰 기반으로 경험에 가중치를 적용하여 재정렬.
        
        Args:
            experiences: 경험 레코드 리스트
            insights: 통찰 리스트
            
        Returns:
            가중치가 적용된 경험 리스트 (재정렬됨)
        """
        # 실패 패턴 통찰 추출
        failure_insights = [
            insight for insight in insights
            if insight.pattern_type == "failure_pattern"
        ]
        
        if not failure_insights:
            return experiences
        
        # 각 경험에 점수 부여
        scored_experiences = []
        for exp in experiences:
            score = 0.0
            
            # 실패 사례이고 통찰과 관련이 있으면 가중치 추가
            if exp.is_success == 0:
                for insight in failure_insights:
                    # critique_tag나 lessons_learned가 통찰의 finding과 관련이 있으면 점수 추가
                    if exp.critique_tag and insight.finding:
                        if exp.critique_tag.lower() in insight.finding.lower():
                            score += insight.confidence * 2.0
                    
                    if exp.lessons_learned and insight.finding:
                        # 간단한 키워드 매칭
                        common_words = set(exp.lessons_learned.lower().split()) & set(insight.finding.lower().split())
                        if len(common_words) >= 2:
                            score += insight.confidence * 1.5
            
            scored_experiences.append((score, exp))
        
        # 점수 순으로 정렬 (높은 순)
        scored_experiences.sort(key=lambda x: x[0], reverse=True)
        
        return [exp for _, exp in scored_experiences]

    def refresh_insights_cache(self) -> None:
        """
        통찰 캐시 갱신 (분석 완료 후 호출).
        """
        self._recent_insights = None
        logger.debug("[ExperienceProvider] Insights cache refreshed")
    
    def _get_diagnosis_summary(self) -> Optional[str]:
        """
        최근 성능 진단 요약 조회 (건강 상태 및 행동 지침 포함).
        
        Returns:
            진단 요약 텍스트 (KPI + 건강 상태 + 행동 지침) 또는 None
        """
        try:
            # 최근 7일 이내의 지표 조회
            recent_metrics = self.db.get_recent_metrics(days=7, limit=20)
            
            if not recent_metrics:
                return None
            
            # 카테고리별로 최신 지표 추출
            latest_by_category = {}
            for metric in recent_metrics:
                category = metric["category"]
                if category not in latest_by_category:
                    latest_by_category[category] = metric
            
            if not latest_by_category:
                return None
            
            tool_metric = latest_by_category.get("TOOL")
            latency_metric = latest_by_category.get("LATENCY")
            goal_metric = latest_by_category.get("GOAL")
            
            # 요약 텍스트: KPI 수치
            summary_lines = []
            if tool_metric:
                summary_lines.append(f"도구 적중률: {tool_metric['value']:.1f}%")
            if latency_metric:
                summary_lines.append(f"평균 지연 시간: {latency_metric['value']:.1f}ms")
            if goal_metric:
                summary_lines.append(f"목표 달성률: {goal_metric['value']:.1f}%")
            
            if not summary_lines:
                return None
            
            # 건강 상태 평가 (DiagnosisService와 동일 기준)
            health_status = self._evaluate_health_from_metrics(
                tool_metric["value"] if tool_metric else 100.0,
                latency_metric["value"] if latency_metric else 0.0,
                goal_metric["value"] if goal_metric else 100.0,
            )
            
            # 건강 상태에 따른 행동 지침 추가
            action_guidance = self._get_health_action_guidance(health_status)
            
            result = "현재 시스템 상태: " + ", ".join(summary_lines)
            result += f"\n건강 상태: {health_status}"
            result += f"\n행동 지침: {action_guidance}"
            return result
            
        except Exception as e:
            logger.debug(f"[ExperienceProvider] Failed to get diagnosis summary: {e}")
            return None
    
    def _evaluate_health_from_metrics(
        self,
        tool_hit_rate: float,
        avg_latency_ms: float,
        goal_completion_rate: float,
    ) -> str:
        """
        지표로부터 건강 상태 판정 (HEALTHY / WARNING / CRITICAL).
        """
        warning_count = 0
        critical_count = 0
        
        if tool_hit_rate < 50.0:
            critical_count += 1
        elif tool_hit_rate < 70.0:
            warning_count += 1
        
        if avg_latency_ms > 10000.0:
            critical_count += 1
        elif avg_latency_ms > 5000.0:
            warning_count += 1
        
        if goal_completion_rate < 30.0:
            critical_count += 1
        elif goal_completion_rate < 50.0:
            warning_count += 1
        
        if critical_count > 0:
            return "CRITICAL"
        if warning_count >= 2:
            return "WARNING"
        return "HEALTHY"
    
    def _get_health_action_guidance(self, health_status: str) -> str:
        """
        건강 상태에 따른 행동 지침 문구 반환.
        """
        if health_status == "CRITICAL":
            return "입력 검증 강화 및 작업 세분화 권고. 복잡한 작업은 작은 단위로 나누어 수행하세요."
        if health_status == "WARNING":
            return "도구 선택 및 턴 수 검토 권고. 불필요한 도구 호출을 줄이고 목표를 명확히 하세요."
        return "현 수준 유지 권고. 일관된 품질로 작업을 이어가세요."

    def format_experiences_as_prompt(
        self,
        experiences: List[ExperienceRecord]
    ) -> str:
        """
        경험 레코드를 Few-shot Prompt 형태로 변환.
        
        높은 신뢰도의 통찰 recommendation을 [System Improvement Directives] 섹션으로 최상단에 주입합니다.
        최근 성능 진단 요약도 포함합니다.
        
        Args:
            experiences: 경험 레코드 리스트
            
        Returns:
            포맷된 프롬프트 문자열 (recommendation 및 진단 요약 포함)
        """
        prompt_parts = []
        
        # 0. 성능 진단 요약 (시스템 건강 상태 인지)
        diagnosis_summary = self._get_diagnosis_summary()
        if diagnosis_summary:
            prompt_parts.append("[System Health Status]")
            prompt_parts.append("=" * 50)
            prompt_parts.append(diagnosis_summary)
            prompt_parts.append("=" * 50)
            prompt_parts.append("")
        
        # 1. 높은 신뢰도의 통찰 recommendation 수집 및 주입
        high_confidence_insights = self._get_high_confidence_insights()
        if high_confidence_insights:
            # 성공 패턴과 실패 패턴 분리
            success_recs = [
                insight.finding
                for insight in high_confidence_insights
                if insight.pattern_type == "success_pattern"
                and insight.finding and insight.confidence >= 0.7
            ]
            improvement_recs = [
                insight.recommendation
                for insight in high_confidence_insights
                if insight.pattern_type != "success_pattern"
                and insight.recommendation and insight.confidence >= 0.7
            ]

            if success_recs:
                prompt_parts.append("[Proven Success Patterns — 검증된 성공 패턴]")
                prompt_parts.append("=" * 50)
                prompt_parts.append("다음 패턴은 과거에 효과적이었습니다. 유사 작업에서 재사용하세요:\n")
                for i, rec in enumerate(success_recs[:3], 1):  # 최대 3개
                    prompt_parts.append(f"{i}. {rec}")
                prompt_parts.append("=" * 50)
                prompt_parts.append("")

            if improvement_recs:
                prompt_parts.append("[System Improvement Directives]")
                prompt_parts.append("=" * 50)
                prompt_parts.append("다음 개선 지침을 반드시 준수하세요:\n")
                for i, rec in enumerate(improvement_recs[:5], 1):  # 최대 5개
                    prompt_parts.append(f"{i}. {rec}")
                prompt_parts.append("=" * 50)
                prompt_parts.append("")
        
        # 2. 과거 경험 지침
        if experiences:
            prompt_parts.append("[Past Experience Advisory]")
            prompt_parts.append("=" * 50)
        
        for i, exp in enumerate(experiences, 1):
            status = "✅ 성공" if exp.is_success == 1 else "❌ 실패"
            
            prompt_parts.append(f"\n[{i}] {status} - {exp.task_intent}")
            
            # 교훈이 있으면 포함
            if exp.lessons_learned:
                prompt_parts.append(f"교훈: {exp.lessons_learned}")
            
            # 실패 사례인 경우 critique_tag 포함
            if exp.is_success == 0 and exp.critique_tag:
                prompt_parts.append(f"주의사항: {exp.critique_tag}")
            
            # 컨텍스트 요약 (간단히)
            if exp.context_summary:
                context_preview = exp.context_summary[:100]
                if len(exp.context_summary) > 100:
                    context_preview += "..."
                prompt_parts.append(f"상황: {context_preview}")
        
        prompt_parts.append("=" * 50)
        prompt_parts.append("\n위 경험을 참고하여 현재 작업을 수행하세요.\n")
        
        return "\n".join(prompt_parts)

    def get_experience_advisory(
        self,
        task_intent: str,
        task_hash: Optional[str] = None,
        limit: int = 3
    ) -> str:
        """
        경험 검색 및 프롬프트 변환을 한 번에 수행.
        
        높은 신뢰도의 통찰 recommendation을 시스템 프롬프트에 주입합니다.
        
        Args:
            task_intent: 작업 의도
            task_hash: 작업 해시 (선택사항)
            limit: 최대 검색 개수
            
        Returns:
            포맷된 경험 지침 프롬프트 (recommendation 포함)
        """
        experiences = self.retrieve_relevant_experiences(
            task_intent=task_intent,
            task_hash=task_hash,
            limit=limit
        )
        
        return self.format_experiences_as_prompt(experiences)

    def format_experiences_as_simple_advisory(
        self,
        experiences: List[ExperienceRecord]
    ) -> str:
        """
        경험을 간단한 지침 형태로 변환 (컴팩트 버전).
        
        Args:
            experiences: 경험 레코드 리스트
            
        Returns:
            간단한 지침 문자열
        """
        if not experiences:
            return ""
        
        advisories = []
        
        for exp in experiences:
            if exp.is_success == 1 and exp.lessons_learned:
                # 성공 사례: 교훈만 포함
                advisories.append(
                    f"과거 유사 작업 교훈: [{exp.task_intent}] 실행 시 {exp.lessons_learned}"
                )
            elif exp.is_success == 0:
                # 실패 사례: 주의사항 포함
                warning = exp.critique_tag or "실패"
                advisories.append(
                    f"과거 유사 작업 경고: [{exp.task_intent}] 실행 시 {warning}에 주의할 것."
                )
        
        if advisories:
            return "\n".join(advisories)
        
        return ""


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_provider_instance: Optional[ExperienceProvider] = None


def get_experience_provider(db: Optional[MemoryDatabase] = None) -> ExperienceProvider:
    """
    ExperienceProvider 싱글톤 인스턴스 반환.
    
    Args:
        db: MemoryDatabase 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        ExperienceProvider 인스턴스
    """
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = ExperienceProvider(db=db)
    return _provider_instance
