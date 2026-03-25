"""
Action Log Analyzer - 행동 로그 분석기 [8]

Mellow-Link의 자아 성찰 기능: 누적된 경험 메모리를 분석하여 통찰을 도출하고
시스템 프롬프트에 피드백합니다.

기능:
- ReAct 단계별 파싱: action_steps JSON을 개별 Step(thought, action, observation)으로 분해
- 인과관계 분석: thought->action 정렬 검사, observation 실패 후 다음 thought의 자기 수정 여부
- 삼중 분석: "Thought: A -> Action: B -> Result: C -> Recommendation: D" 구조적 통찰 생성
- 심층 성찰 세션: LLM 기반 인과 추론 후 behavior_insights 테이블에 저장

기술 검토:
- JSON 파싱 및 구조화: ✅ verified
- 인과관계 추론 분석: ⚠️ possible
- 실시간 전략 주입: ❌ hypothetical
"""

import json
import uuid
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

from mellow_link.infra.memory_database import (
    MemoryDatabase,
    ExperienceRecord,
    ToolStatRecord,
    BehaviorInsight,
    get_memory_db
)


# GuardianService는 지연 로딩 (선택적 의존성)

logger = logging.getLogger(__name__)


# =============================================================================
# Insight 데이터 구조 (ExperienceProvider 주입용)
# =============================================================================
#
# BehaviorInsight 필드:
#   - id: UUID (고유 식별자)
#   - pattern_type: "failure_pattern" | "tool_performance"
#   - finding: 발견된 패턴/문제점 (시스템 프롬프트 주입 시 [System Improvement Directives]에 사용)
#   - recommendation: 구체적인 개선 권고 (ExperienceProvider.format_experiences_as_prompt에서 주입)
#   - confidence: 0.0~1.0 (0.7 이상만 ExperienceProvider가 주입)
#   - is_applied: 0=미적용, 1=적용됨
#
# ExperienceProvider는 get_recent_insights(min_confidence=0.7)로 조회 후
# format_experiences_as_prompt()에서 recommendation을 [System Improvement Directives]로 주입합니다.


# =============================================================================
# Action Log Analyzer
# =============================================================================

class ActionLogAnalyzer:
    """
    행동 로그 분석기.
    
    경험 메모리와 도구 통계를 분석하여 통찰을 도출하고 개선 권고를 생성합니다.
    """

    def __init__(
        self,
        db: Optional[MemoryDatabase] = None,
        llm_service: Optional[Any] = None
    ):
        """
        Args:
            db: MemoryDatabase 인스턴스 (None이면 싱글톤 사용)
            llm_service: LLM 서비스 인스턴스 (패턴 분석 및 권고 생성용)
        """
        self.db = db or get_memory_db()
        self.llm_service = llm_service
        logger.info("[ActionLogAnalyzer] Initialized")

    async def analyze(
        self,
        llm_service: Optional[Any] = None
    ) -> List[BehaviorInsight]:
        """
        전체 분석 수행.
        
        실패 패턴 분석과 도구 성능 분석을 수행하여 통찰을 생성합니다.
        
        Args:
            llm_service: LLM 서비스 인스턴스 (None이면 self.llm_service 사용)
            
        Returns:
            생성된 통찰 리스트
        """
        llm = llm_service or self.llm_service
        
        insights = []
        
        try:
            # 1. 실패 패턴 분석
            failure_insights = await self._analyze_failure_patterns(llm)
            insights.extend(failure_insights)
            
            # 2. 심층 성찰 세션 (Thought·Action·Observation 삼중 분석 + 인과관계)
            deep_insights = await self._run_deep_reflection_session(llm)
            insights.extend(deep_insights)
            
            # 3. 도구 성능 분석
            tool_insights = self._analyze_tool_performance()
            insights.extend(tool_insights)
            
            # 4. 보호자 2차 검수 (GuardianService)
            raw_logs = self.db.get_failed_experiences(limit=10)
            for insight in insights:
                try:
                    audit_result = await self._audit_insight_via_guardian(
                        insight, raw_logs
                    )
                    guardian_ran = getattr(audit_result, "guardian_actually_ran", True)
                    insight.is_verified_by_guardian = (
                        1 if (audit_result.is_approved and guardian_ran) else 0
                    )
                    if audit_result.refined_recommendation:
                        insight.recommendation = audit_result.refined_recommendation
                    if not audit_result.is_approved:
                        critique = (getattr(audit_result, "critique", "") or "").lower()
                        # 보호자 부재(API 호출 불가) 시 confidence -= 0.2
                        guardian_absent = any(
                            kw in critique for kw in
                            ["로컬", "skip", "미설정", "오류", "중단", "한도"]
                        )
                        if guardian_absent:
                            insight.confidence = max(0.0, insight.confidence - 0.2)
                        else:
                            insight.confidence = min(insight.confidence, 0.65)
                except Exception as guard_ex:
                    logger.warning(
                        f"[ActionLogAnalyzer] Guardian audit failed (insight kept): {guard_ex}"
                    )
                    insight.is_verified_by_guardian = 0
                    insight.confidence = max(0.0, insight.confidence - 0.2)
            
            # 통찰 저장 (save_insight가 유사 finding 시 갱신(Update)만 수행)
            for insight in insights:
                self.db.save_insight(insight)
            
            logger.info(f"[ActionLogAnalyzer] Analysis complete: {len(insights)} insights generated")
            return insights
            
        except Exception as e:
            logger.error(f"[ActionLogAnalyzer] Analysis failed: {e}")
            return insights

    async def _audit_insight_via_guardian(
        self,
        insight: BehaviorInsight,
        raw_logs: List[ExperienceRecord]
    ):
        """GuardianService로 통찰 2차 검수. 실패 시 예외 발생 없이 결과 반환."""
        try:
            from mellow_link.core.guardian_service import get_guardian_service
            guardian = get_guardian_service()
            return await guardian.audit_insight(insight, raw_logs=raw_logs)
        except Exception as e:
            logger.debug(f"[ActionLogAnalyzer] Guardian unavailable: {e}")
            from types import SimpleNamespace
            return SimpleNamespace(
                is_approved=False,
                critique=str(e)[:200],
                refined_recommendation=insight.recommendation,
                guardian_actually_ran=False,
            )

    # -------------------------------------------------------------------------
    # ReAct 단계별 파싱 (JSON 파싱 및 구조화: ✅ verified)
    # -------------------------------------------------------------------------

    def _parse_action_steps_to_sequence(
        self,
        exp: ExperienceRecord
    ) -> List[Dict[str, Any]]:
        """
        action_steps JSON을 개별 단계(Step)로 분해.
        
        각 단계는 thought, action, observation을 하나의 세트로 묶어 분석 대상으로 삼음.
        
        Args:
            exp: 경험 레코드
            
        Returns:
            [{"turn": int, "thought": str, "action": dict, "observation": str, "is_failed": bool}, ...]
        """
        error_keywords = ["error", "failed", "exception", "실패", "차단", "[error]"]
        
        try:
            # action_steps 파싱 (문자열이면 JSON 파싱, 이미 객체면 그대로 사용)
            if not exp.action_steps:
                return []
            
            if isinstance(exp.action_steps, str):
                steps_raw = json.loads(exp.action_steps)
            elif isinstance(exp.action_steps, (list, dict)):
                steps_raw = exp.action_steps
            else:
                return []
            
            # steps가 리스트인지 확인
            if not isinstance(steps_raw, list):
                return []
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            logger.debug(f"[ActionLogAnalyzer] Failed to parse action_steps in _parse_action_steps_to_sequence: {e}")
            return []
        
        sequence = []
        for step in steps_raw:
            # step이 딕셔너리인지 확인
            if not isinstance(step, dict):
                continue
            
            obs = step.get("observation") or ""
            obs_lower = obs.lower()
            is_failed = any(kw in obs_lower for kw in error_keywords)
            
            sequence.append({
                "turn": step.get("turn", 0),
                "thought": (step.get("thought") or "").strip(),
                "action": step.get("action"),  # {tool, args} or None
                "observation": obs.strip(),
                "is_failed": is_failed,
                "task_intent": exp.task_intent[:150] if exp.task_intent else "",
            })
        
        return sequence

    # -------------------------------------------------------------------------
    # 인과관계 분석 (인과관계 추론 분석: ⚠️ possible)
    # -------------------------------------------------------------------------

    def _analyze_thought_action_alignment(
        self,
        thought: str,
        action: Optional[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        의도 분석: thought에 나타난 계획이 action으로 적절히 이어졌는지 검사.
        
        Returns:
            (is_aligned, reason)
        """
        if not action or not thought:
            return True, ""
        
        tool = action.get("tool", "")
        thought_lower = thought.lower()
        tool_lower = tool.lower()
        
        # thought에 도구명/의도가 언급되었는지
        if tool_lower in thought_lower:
            return True, "thought에 도구 언급"
        
        # finish는 의도가 명확
        if tool == "finish":
            return True, "finish 호출"
        
        # 도구 관련 키워드 매핑 (간단 휴리스틱)
        tool_hints = {
            "search": ["검색", "찾", "search", "조회"],
            "read": ["읽", "read", "열기", "확인"],
            "write": ["쓰", "저장", "write", "생성"],
        }
        for key, hints in tool_hints.items():
            if key in tool_lower and any(h in thought_lower for h in hints):
                return True, f"thought-{key} 키워드 일치"
        
        return False, f"thought에 '{tool}' 도구/의도 언급 부재"

    def _analyze_self_correction_after_failure(
        self,
        failed_obs: str,
        next_thought: Optional[str]
    ) -> Tuple[bool, str]:
        """
        결과 해석: observation이 실패일 때, 다음 thought에서 실패를 반영했는지 분석.
        
        Returns:
            (did_self_correct, reason)
        """
        if not next_thought or not next_thought.strip():
            return False, "다음 thought 없음"
        
        next_lower = next_thought.lower()
        correction_keywords = [
            "다시", "재시도", "retry", "다른", "대안", "fallback",
            "에러", "error", "실패", "failed", "수정", "다르게",
            "확인", "검증", "check", "validate"
        ]
        
        if any(kw in next_lower for kw in correction_keywords):
            return True, "자기 수정 시도 감지"
        
        return False, "실패 반영/자기 수정 흔적 없음"

    def _build_causal_chains_for_reflection(
        self,
        experiences: List[ExperienceRecord]
    ) -> List[Dict[str, Any]]:
        """
        파싱된 시퀀스에서 인과관계 분석용 체인 구성.
        
        각 체인: 실패 단계 + 다음 단계 (자기 수정 여부 분석용)
        """
        chains = []
        
        for exp in experiences:
            seq = self._parse_action_steps_to_sequence(exp)
            
            for i, step in enumerate(seq):
                # step이 딕셔너리인지 확인
                if not isinstance(step, dict):
                    continue
                
                if not step.get("is_failed"):
                    continue
                
                thought = step.get("thought", "")
                action = step.get("action")
                observation = step.get("observation", "")
                
                # thought-action 정렬 검사
                aligned, align_reason = self._analyze_thought_action_alignment(thought, action)
                
                # 자기 수정 여부 (다음 thought)
                next_thought = None
                if i + 1 < len(seq):
                    next_step = seq[i + 1]
                    if isinstance(next_step, dict):
                        next_thought = next_step.get("thought", "")
                
                self_corrected, correct_reason = self._analyze_self_correction_after_failure(
                    observation, next_thought
                )
                
                tool_str = "unknown"
                if isinstance(action, dict):
                    tool_str = action.get("tool", "unknown")
                elif action:
                    tool_str = str(action)
                
                chains.append({
                    "task_intent": step.get("task_intent", ""),
                    "thought": thought[:400],
                    "action_tool": tool_str,
                    "action_args": action.get("args", {}) if action else {},
                    "observation": observation[:300],
                    "thought_action_aligned": aligned,
                    "align_reason": align_reason,
                    "self_corrected": self_corrected,
                    "correct_reason": correct_reason,
                    "next_thought": (next_thought or "")[:200],
                })
        
        return chains

    # -------------------------------------------------------------------------
    # 심층 성찰 세션 (구조적 통찰 + LLM)
    # -------------------------------------------------------------------------

    async def _run_deep_reflection_session(
        self,
        llm_service: Optional[Any]
    ) -> List[BehaviorInsight]:
        """
        LLM을 활용한 '심층 성찰' 세션.
        
        인과 체인(Thought->Action->Result)을 분석하여 구조적 통찰을 생성하고
        behavior_insights 테이블 스키마에 맞춰 저장.
        
        통찰 형식: "Thought: A -> Action: B -> Result: C -> Recommendation: D"
        """
        insights = []
        
        try:
            failed_experiences = self.db.get_failed_experiences(limit=20)
            if len(failed_experiences) < 2:
                return insights
            
            chains = self._build_causal_chains_for_reflection(failed_experiences)
            if not chains:
                return insights
            
            # LLM 심층 성찰
            if llm_service:
                llm_insights = await self._generate_structural_insights(llm_service, chains)
                insights.extend(llm_insights)
            
            # LLM 결과 없을 때 규칙 기반 구조적 통찰 폴백
            if not insights and chains:
                fallback = self._create_structural_insight_fallback(chains)
                if fallback:
                    insights.append(fallback)
            
            return insights
            
        except Exception as e:
            logger.error(f"[ActionLogAnalyzer] Deep reflection session failed: {e}")
            return insights

    async def _generate_structural_insights(
        self,
        llm_service: Any,
        causal_chains: List[Dict[str, Any]]
    ) -> List[BehaviorInsight]:
        """
        인과 체인으로부터 구조적 통찰 생성.
        
        형식: Thought: A -> Action: B -> Result: C -> Recommendation: D
        """
        insights = []
        
        try:
            # 체인 샘플 구성 (최대 6개)
            chain_lines = []
            for i, c in enumerate(causal_chains[:6], 1):
                thought = (c.get("thought") or "")[:150]
                action = c.get("action_tool", "?")
                obs = (c.get("observation") or "")[:120]
                aligned = "정렬됨" if c.get("thought_action_aligned") else "정렬 누락"
                corrected = "자기수정함" if c.get("self_corrected") else "자기수정 없음"
                chain_lines.append(
                    f"[체인 {i}] 의도: {c.get('task_intent', '')[:80]}\n"
                    f"  Thought: {thought}\n"
                    f"  Action: {action}\n"
                    f"  Result: {obs}\n"
                    f"  thought-action: {aligned}, 자기수정: {corrected}"
                )
            chains_text = "\n\n".join(chain_lines)
            
            prompt = f"""다음은 Mellow-Link 에이전트의 실패 시퀀스(인과 체인)입니다.
각 체인은 Thought(의도) -> Action(실행) -> Result(결과) 순으로 이어집니다.

인과 체인 샘플:
{chains_text}

요청사항:
1. 각 체인에서 "Thought: A를 하려 함 -> Action: B 도구 사용 -> Result: C 에러 발생" 형태로 요약하세요.
2. C 에러/실패를 방지하기 위한 구체적 Recommendation(D)을 제시하세요.
   형식: "D 접근법으로 수정 필요" (실행 가능한 지침)

출력 형식 (JSON 배열, 하나의 통합 통찰로):
{{
  "finding": "Thought: [의도요약] -> Action: [도구/행동] -> Result: [에러/결과] -> 종합 패턴",
  "recommendation": "구체적 권고 (D 접근법으로 수정 필요 형식)"
}}

JSON만 출력하세요."""

            if hasattr(llm_service, 'generate'):
                try:
                    result = await llm_service.generate(
                        prompt=prompt,
                        mode="thinking",
                        max_tokens=600,
                        temperature=0.6
                    )
                    # GenerationResult 객체인지 확인
                    if result and hasattr(result, 'content'):
                        response_text = str(result.content) if not isinstance(result.content, str) else result.content
                    elif isinstance(result, str):
                        response_text = result
                    else:
                        response_text = str(result) if result else ""
                except Exception as gen_ex:
                    logger.warning(f"[ActionLogAnalyzer] LLM generate() failed: {gen_ex}")
                    return insights
            elif hasattr(llm_service, 'chat'):
                try:
                    messages = [
                        {"role": "system", "content": "당신은 인과관계 분석 전문가입니다. Thought-Action-Result 체인을 분석하여 구조적 통찰을 도출합니다."},
                        {"role": "user", "content": prompt}
                    ]
                    response = await llm_service.chat(messages=messages)
                    if response and hasattr(response, 'text'):
                        response_text = str(response.text) if not isinstance(response.text, str) else response.text
                    elif isinstance(response, str):
                        response_text = response
                    else:
                        response_text = str(response) if response else ""
                except Exception as chat_ex:
                    logger.warning(f"[ActionLogAnalyzer] LLM chat() failed: {chat_ex}")
                    return insights
            else:
                return insights
            
            # JSON 파싱
            text = response_text.strip() if isinstance(response_text, str) else str(response_text)
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                text = text[start:end].strip()
            elif "```" in text:
                start = text.find("```") + 3
                end = text.find("```", start)
                text = text[start:end].strip()
            
            # JSON 파싱 시도 (실패 시 빈 딕셔너리 반환)
            try:
                data = json.loads(text) if isinstance(text, str) else text
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, TypeError, AttributeError):
                data = {}
            
            finding = data.get("finding", "") if isinstance(data, dict) else ""
            recommendation = data.get("recommendation", "") if isinstance(data, dict) else ""
            
            if finding and recommendation:
                # 구조적 통찰은 pattern_type="causal_chain" (기존 스키마 호환)
                insight = BehaviorInsight(
                    id=str(uuid.uuid4()),
                    pattern_type="failure_pattern",
                    finding=finding,
                    recommendation=recommendation,
                    confidence=min(0.85, 0.5 + len(causal_chains) / 30.0),
                    is_applied=0,
                    created_at=datetime.now()
                )
                insights.append(insight)
                logger.info(f"[ActionLogAnalyzer] Structural insight: {finding[:60]}...")
            
            return insights
                
        except json.JSONDecodeError as e:
            logger.warning(f"[ActionLogAnalyzer] Failed to parse structural insight JSON: {e}")
        except Exception as e:
            logger.error(f"[ActionLogAnalyzer] Structural insight generation failed: {e}")
        
        return insights

    def _create_structural_insight_fallback(
        self,
        causal_chains: List[Dict[str, Any]]
    ) -> Optional[BehaviorInsight]:
        """
        LLM 없이 규칙 기반으로 구조적 통찰 생성 (폴백).
        
        형식: Thought: A -> Action: B -> Result: C -> Recommendation: D
        """
        if not causal_chains:
            return None
        
        # 대표 체인 선택 (thought-action 미정렬 또는 자기수정 없음 우선)
        c = causal_chains[0]
        for chain in causal_chains:
            if not chain.get("thought_action_aligned") or not chain.get("self_corrected"):
                c = chain
                break
        
        thought_sum = (c.get("thought") or "")[:80].replace("\n", " ")
        action_tool = c.get("action_tool", "?")
        obs_sum = (c.get("observation") or "")[:80].replace("\n", " ")
        
        finding = (
            f"Thought: '{thought_sum}' -> Action: '{action_tool}' 도구 사용 -> "
            f"Result: '{obs_sum}' 에러 발생"
        )
        
        aligned = c.get("thought_action_aligned", True)
        corrected = c.get("self_corrected", False)
        
        if not aligned:
            rec = f"도구 호출 전 thought에서 '{action_tool}' 사용 의도를 명시하세요."
        elif not corrected:
            rec = "실패 observation 발생 시 다음 thought에서 에러 반영 및 대안 접근을 시도하세요."
        else:
            rec = f"'{action_tool}' 사용 시 사전 검증과 예외 처리를 강화하세요."
        
        return BehaviorInsight(
            id=str(uuid.uuid4()),
            pattern_type="failure_pattern",
            finding=finding,
            recommendation=rec,
            confidence=0.6,
            is_applied=0,
            created_at=datetime.now()
        )

    # -------------------------------------------------------------------------
    # 기존 메서드 (실패 단계 추출)
    # -------------------------------------------------------------------------

    def _extract_failed_steps_from_experience(
        self,
        exp: ExperienceRecord
    ) -> List[Dict[str, Any]]:
        """
        ExperienceRecord의 action_steps JSON에서 실패한 단계(Thought, Action, Observation) 추출.
        
        실패 단계: observation에 에러/실패 키워드가 포함된 스텝.
        
        Args:
            exp: 경험 레코드
            
        Returns:
            [{"thought": str, "action": dict, "observation": str}, ...] 형태 리스트
        """
        error_keywords = ["error", "failed", "exception", "실패", "차단", "[error]"]
        failed_steps = []
        
        try:
            # action_steps 파싱 (문자열이면 JSON 파싱, 이미 객체면 그대로 사용)
            if not exp.action_steps:
                return failed_steps
            
            if isinstance(exp.action_steps, str):
                steps_raw = json.loads(exp.action_steps)
            elif isinstance(exp.action_steps, (list, dict)):
                steps_raw = exp.action_steps
            else:
                return failed_steps
            
            # steps가 리스트인지 확인
            if not isinstance(steps_raw, list):
                return failed_steps
            
            steps = steps_raw
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            logger.debug(f"[ActionLogAnalyzer] Failed to parse action_steps: {e}")
            return failed_steps
        
        for step in steps:
            # step이 딕셔너리인지 확인
            if not isinstance(step, dict):
                continue
            
            obs = (step.get("observation") or "").lower()
            if any(kw in obs for kw in error_keywords):
                failed_steps.append({
                    "thought": step.get("thought", "")[:500],  # 제한
                    "action": step.get("action"),
                    "observation": (step.get("observation") or "")[:300],  # 제한
                    "task_intent": exp.task_intent[:100] if exp.task_intent else "",
                })
        
        # 에러가 있는 스텝이 없으면 마지막 스텝을 실패 지점으로 간주
        if not failed_steps and steps:
            last = steps[-1]
            if isinstance(last, dict):
                failed_steps.append({
                    "thought": (last.get("thought") or "")[:500],
                    "action": last.get("action"),
                    "observation": (last.get("observation") or "")[:300],
                    "task_intent": exp.task_intent[:100] if exp.task_intent else "",
                })
        
        return failed_steps

    async def _analyze_failure_patterns(
        self,
        llm_service: Optional[Any]
    ) -> List[BehaviorInsight]:
        """
        실패 패턴 분석 및 프롬프트 개선안 생성.
        
        experience_ledger에서 최근 실패한 태스크의 Thought, Action, Observation을
        파싱하여 LLM으로 공통 패턴을 도출하고 개선 권고를 생성합니다.
        
        Args:
            llm_service: LLM 서비스 인스턴스
            
        Returns:
            실패 패턴 관련 통찰 리스트
        """
        insights = []
        
        try:
            # 1. 데이터 수집: 최근 실패 사례
            failed_experiences = self.db.get_failed_experiences(limit=20)
            
            if len(failed_experiences) < 3:
                logger.debug("[ActionLogAnalyzer] Not enough failed experiences for pattern analysis")
                return insights
            
            # 2. 실패 단계 추출 (Thought, Action, Observation)
            all_failed_steps = []
            critique_tags = []
            lessons = []
            
            for exp in failed_experiences:
                steps = self._extract_failed_steps_from_experience(exp)
                all_failed_steps.extend(steps)
                if exp.critique_tag:
                    critique_tags.append(exp.critique_tag)
                if exp.lessons_learned:
                    lessons.append(exp.lessons_learned)
            
            tag_counter = Counter(critique_tags)
            common_tags = tag_counter.most_common(5)
            
            # 3. LLM 분석: 실패 단계 기반 패턴 추출 및 권고 생성
            if llm_service:
                llm_insight = await self._generate_llm_insight(
                    llm_service,
                    failed_experiences,
                    all_failed_steps,
                    common_tags,
                    lessons
                )
                if llm_insight:
                    insights.append(llm_insight)
            
            # 4. 통계 기반 통찰 생성 (LLM 없이 폴백)
            if common_tags:
                stat_insight = self._create_statistical_insight(common_tags, len(failed_experiences))
                if stat_insight:
                    insights.append(stat_insight)
            
            return insights
            
        except Exception as e:
            logger.error(f"[ActionLogAnalyzer] Failed to analyze failure patterns: {e}")
            return insights

    async def _generate_llm_insight(
        self,
        llm_service: Any,
        failed_experiences: List[ExperienceRecord],
        failed_steps: List[Dict[str, Any]],
        common_tags: List[tuple],
        lessons: List[str]
    ) -> Optional[BehaviorInsight]:
        """
        LLM을 사용하여 실패 단계(Thought, Action, Observation) 기반 패턴 분석 및 권고 생성.
        
        Args:
            llm_service: LLM 서비스 인스턴스
            failed_experiences: 실패한 경험 리스트
            failed_steps: 추출된 실패 단계 (thought, action, observation)
            common_tags: 빈도가 높은 태그 리스트
            lessons: 교훈 리스트
            
        Returns:
            생성된 통찰 또는 None
        """
        try:
            tag_summary = ", ".join([f"{tag}({count}회)" for tag, count in common_tags[:3]])
            lessons_sample = "\n".join(lessons[:5]) if lessons else "(없음)"
            
            # 실패 단계 샘플 구성 (Thought, Action, Observation)
            steps_sample_lines = []
            for i, step in enumerate(failed_steps[:8], 1):  # 최대 8개
                thought = (step.get("thought") or "")[:200]
                action = step.get("action")
                obs = (step.get("observation") or "")[:200]
                action_str = ""
                if action:
                    tool = action.get("tool", "")
                    args = action.get("args", {})
                    action_str = f"도구={tool}, args={json.dumps(args, ensure_ascii=False)[:100]}"
                steps_sample_lines.append(
                    f"[실패단계 {i}] 의도: {step.get('task_intent', '')}\n"
                    f"  Thought: {thought}\n"
                    f"  Action: {action_str}\n"
                    f"  Observation: {obs}"
                )
            steps_sample = "\n\n".join(steps_sample_lines) if steps_sample_lines else "(파싱 불가)"
            
            prompt = f"""다음은 Mellow-Link 에이전트의 최근 실패 사례입니다.
각 실패 단계마다 Thought(추론), Action(도구 호출), Observation(실행 결과)가 기록되어 있습니다.

실패 태그 빈도: {tag_summary}

실패한 단계 샘플 (Thought, Action, Observation):
{steps_sample}

주요 교훈 (lessons_learned):
{lessons_sample}

총 실패 사례 수: {len(failed_experiences)}건

요청사항:
1. 위 실패 단계들에서 발견되는 공통된 실패 패턴을 한 문단으로 요약하세요.
2. 이 패턴을 방지하기 위한 구체적인 Recommendation(권고 사항)을 제시하세요.
   (예: "도구 호출 전에 반드시 X를 확인하라", "Y 상황에서는 Z 도구를 우선 사용하라" 등)
   ExperienceProvider가 시스템 프롬프트에 주입하므로 실행 가능한 명령형 지침으로 작성하세요.

출력 형식 (JSON만 출력):
{{
  "pattern": "발견된 공통 패턴 요약",
  "recommendation": "구체적인 권고 사항 (실행 가능한 지침)"
}}"""

            # LLM 호출
            if hasattr(llm_service, 'generate'):
                try:
                    result = await llm_service.generate(
                        prompt=prompt,
                        mode="thinking",
                        max_tokens=500,
                        temperature=0.7
                    )
                    # GenerationResult 객체인지 확인
                    if result and hasattr(result, 'content'):
                        response_text = str(result.content) if not isinstance(result.content, str) else result.content
                    elif isinstance(result, str):
                        response_text = result
                    else:
                        response_text = str(result) if result else ""
                except Exception as gen_ex:
                    logger.warning(f"[ActionLogAnalyzer] LLM generate() failed: {gen_ex}")
                    return None
            elif hasattr(llm_service, 'chat'):
                try:
                    messages = [
                        {"role": "system", "content": "당신은 시스템 분석 전문가입니다. 실패 패턴을 분석하고 개선안을 제시하는 것이 전문입니다."},
                        {"role": "user", "content": prompt}
                    ]
                    response = await llm_service.chat(messages=messages)
                    if response and hasattr(response, 'text'):
                        response_text = str(response.text) if not isinstance(response.text, str) else response.text
                    elif isinstance(response, str):
                        response_text = response
                    else:
                        response_text = str(response) if response else ""
                except Exception as chat_ex:
                    logger.warning(f"[ActionLogAnalyzer] LLM chat() failed: {chat_ex}")
                    return None
            else:
                return None
            
            # JSON 파싱
            try:
                # 코드 블록 제거
                text = response_text.strip() if isinstance(response_text, str) else str(response_text)
                if "```json" in text:
                    start = text.find("```json") + 7
                    end = text.find("```", start)
                    text = text[start:end].strip()
                elif "```" in text:
                    start = text.find("```") + 3
                    end = text.find("```", start)
                    text = text[start:end].strip()
                
                # JSON 파싱 시도 (실패 시 빈 딕셔너리 반환)
                try:
                    data = json.loads(text) if isinstance(text, str) else text
                    if not isinstance(data, dict):
                        data = {}
                except (json.JSONDecodeError, TypeError, AttributeError):
                    data = {}
                
                pattern = data.get("pattern", "") if isinstance(data, dict) else ""
                recommendation = data.get("recommendation", "") if isinstance(data, dict) else ""
                
                if pattern and recommendation:
                    # 신뢰도 계산 (샘플 수 기반)
                    confidence = min(0.9, 0.5 + (len(failed_experiences) / 50.0))
                    
                    insight = BehaviorInsight(
                        id=str(uuid.uuid4()),
                        pattern_type="failure_pattern",
                        finding=pattern,
                        recommendation=recommendation,
                        confidence=confidence,
                        is_applied=0,
                        created_at=datetime.now()
                    )
                    
                    logger.info(f"[ActionLogAnalyzer] LLM insight generated: {pattern[:50]}...")
                    return insight
                    
            except json.JSONDecodeError as e:
                logger.warning(f"[ActionLogAnalyzer] Failed to parse LLM response: {e}")
                return None
            
        except Exception as e:
            logger.error(f"[ActionLogAnalyzer] Failed to generate LLM insight: {e}")
            return None

    def _create_statistical_insight(
        self,
        common_tags: List[tuple],
        total_failures: int
    ) -> Optional[BehaviorInsight]:
        """
        통계 기반 통찰 생성 (LLM 없이).
        
        Args:
            common_tags: 빈도가 높은 태그 리스트
            total_failures: 총 실패 사례 수
            
        Returns:
            생성된 통찰 또는 None
        """
        if not common_tags:
            return None
        
        top_tag, top_count = common_tags[0]
        frequency = top_count / total_failures if total_failures > 0 else 0.0
        
        finding = f"가장 빈번한 실패 원인: {top_tag} ({top_count}/{total_failures}건, {frequency*100:.1f}%)"
        
        # 태그별 권고 매핑
        recommendations_map = {
            "#API_Error": "API 호출 전에 연결 상태를 확인하고, 타임아웃과 재시도 로직을 추가하세요.",
            "#Permission_Error": "파일/디렉토리 접근 전에 권한을 확인하고, 필요한 경우 사용자에게 권한 요청을 하세요.",
            "#Resource_Error": "리소스(파일, 디렉토리 등) 사용 전에 존재 여부를 확인하세요.",
            "#Parse_Error": "JSON 파싱 시 유효성 검사를 추가하고, 예외 처리를 강화하세요.",
            "#Security_Violation": "보안 정책을 준수하고, 위험한 작업은 사전에 차단하세요.",
            "#Max_Turns_Exceeded": "작업을 더 작은 단위로 분해하거나, 더 효율적인 접근 방법을 고려하세요.",
        }
        
        recommendation = recommendations_map.get(
            top_tag,
            f"{top_tag} 관련 실패를 줄이기 위해 사전 검증과 예외 처리를 강화하세요."
        )
        
        # 신뢰도 계산
        confidence = min(0.8, 0.4 + frequency)
        
        insight = BehaviorInsight(
            id=str(uuid.uuid4()),
            pattern_type="failure_pattern",
            finding=finding,
            recommendation=recommendation,
            confidence=confidence,
            is_applied=0,
            created_at=datetime.now()
        )
        
        return insight

    def _analyze_tool_performance(self) -> List[BehaviorInsight]:
        """
        도구 성능 분석 및 개선 권고 생성.
        
        tool_stats에서 성공률이 50% 미만이거나 평균 실행 시간이 임계치를 넘는
        도구를 식별하여 개선 권고를 작성합니다.
        
        Returns:
            도구 성능 관련 통찰 리스트
        """
        insights = []
        
        try:
            # 성능 저조한 도구 조회
            poor_tools = self.db.get_poor_performing_tools(
                success_rate_threshold=0.5,
                avg_runtime_threshold_ms=1000.0
            )
            
            for tool in poor_tools:
                if tool.use_count == 0:
                    continue
                
                success_rate = tool.success_count / tool.use_count
                
                # 통찰 생성 (경고 지침)
                if success_rate < 0.5:
                    finding = (
                        f"도구 '{tool.tool_name}'의 성공률이 낮습니다 "
                        f"({success_rate*100:.1f}%, {tool.success_count}/{tool.use_count}회 성공)"
                    )
                    last_err = (tool.last_error_msg or "N/A")[:150]
                    recommendation = (
                        f"경고: '{tool.tool_name}' 사용 전 반드시 입력 검증을 수행하고, "
                        f"에러 핸들링을 강화하세요. 최근 에러: {last_err}"
                    )
                    confidence = 0.7 if tool.use_count >= 5 else 0.5
                    
                elif tool.avg_runtime_ms > 1000.0:
                    finding = (
                        f"도구 '{tool.tool_name}'의 실행 시간이 길습니다 "
                        f"(평균 {tool.avg_runtime_ms:.0f}ms)"
                    )
                    recommendation = (
                        f"경고: '{tool.tool_name}' 호출 시 지연이 발생할 수 있으므로 "
                        f"비동기 처리 또는 캐싱을 고려하세요."
                    )
                    confidence = 0.6
                else:
                    continue
                
                insight = BehaviorInsight(
                    id=str(uuid.uuid4()),
                    pattern_type="tool_performance",
                    finding=finding,
                    recommendation=recommendation,
                    confidence=confidence,
                    is_applied=0,
                    created_at=datetime.now()
                )
                
                insights.append(insight)
            
            logger.info(f"[ActionLogAnalyzer] Tool performance analysis: {len(insights)} insights")
            return insights
            
        except Exception as e:
            logger.error(f"[ActionLogAnalyzer] Failed to analyze tool performance: {e}")
            return insights


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_analyzer_instance: Optional[ActionLogAnalyzer] = None


def get_log_analyzer(
    db: Optional[MemoryDatabase] = None,
    llm_service: Optional[Any] = None
) -> ActionLogAnalyzer:
    """
    ActionLogAnalyzer 싱글톤 인스턴스 반환.
    
    Args:
        db: MemoryDatabase 인스턴스 (첫 호출 시에만 적용)
        llm_service: LLM 서비스 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        ActionLogAnalyzer 인스턴스
    """
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = ActionLogAnalyzer(db=db, llm_service=llm_service)
    return _analyzer_instance
