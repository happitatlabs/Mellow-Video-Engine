"""
보호자 시스템: 2차 검수 모듈 (Guardian System)

두 모드:
- PolicyGuardian: 폐쇄망 기본. 규칙 기반 심사(비용/파일범위/위험패턴). LLM 호출 없음.
- AIGuardian: ENABLE_GUARDIAN_APIS=1일 때만. 기존 LLM 기반 Tower/Verdict/Audit.

Factory get_guardian_service()가 ENABLE_GUARDIAN_APIS에 따라 자동 선택.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any, List, Dict, Tuple, Literal

from mellow_link.infra.memory_database import (
    BehaviorInsight,
    ExperienceRecord,
    MemoryDatabase,
    get_memory_db,
)
from mellow_link.infra.env_loader import get_guardian_config
from mellow_link.core.risk_classifier import classify_code_risk_level
from mellow_link.core.null_providers import log_airgap_block

logger = logging.getLogger(__name__)

# 시스템 로그 (비용/한도/서킷브레이커 이벤트)
_system_logger: Optional[logging.Logger] = None
_CIRCUIT_BREAKER: Dict[str, datetime] = {}  # provider -> suspended_until
_ESTIMATE_COST_PER_1K = {"anthropic": 0.003, "openai": 0.005}  # USD/1K tokens (대략)


def _get_system_logger() -> logging.Logger:
    """logs/system.log에 기록하는 전용 로거."""
    global _system_logger
    if _system_logger is not None:
        return _system_logger
    _system_logger = logging.getLogger("mellow_link.system")
    _system_logger.setLevel(logging.INFO)
    if not _system_logger.handlers:
        base = Path(__file__).resolve().parent.parent  # mellow_link
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "system.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        _system_logger.addHandler(fh)
    return _system_logger


def _log_system(event: str, detail: str = "") -> None:
    """시스템 이벤트를 logs/system.log에 기록."""
    try:
        _get_system_logger().info(f"[Guardian] {event} {detail}".strip())
    except Exception:
        pass


# PolicyGuardian 정책 결정 (스펙: docs/AI/41_POLICY_GUARDIAN_SPEC.md)
PolicyDecision = Literal["APPROVE", "REJECT", "NEED_AI_REVIEW"]


@dataclass
class AuditResult:
    """
    감사 결과.
    risk_score: 0-100. 70 이상이면 자동 적용 차단.
    policy_decision: PolicyGuardian만 설정. APPROVE / REJECT / NEED_AI_REVIEW.
    """
    is_approved: bool
    critique: str
    refined_recommendation: str
    guardian_actually_ran: bool = True  # False면 로컬 승인/스킵 (is_verified=0)
    risk_score: int = 0  # 0-100, 70 이상이면 자동 적용 차단
    policy_decision: Optional[PolicyDecision] = None  # PolicyGuardian 한정. AIGuardian은 None.


# =============================================================================
# Guardian Base (공통 인터페이스)
# =============================================================================

class GuardianBase(ABC):
    """PolicyGuardian / AIGuardian 공통 인터페이스."""

    @abstractmethod
    async def audit_insight(
        self,
        insight: BehaviorInsight,
        raw_logs: Optional[List[ExperienceRecord]] = None,
        raw_logs_text: Optional[str] = None,
    ) -> AuditResult:
        ...

    @abstractmethod
    async def audit_tool_code(self, tool_name: str, description: str, code: str) -> AuditResult:
        ...

    @abstractmethod
    async def audit_evolution_proposal(
        self, target_file: str, proposed_code: str, reason: str
    ) -> AuditResult:
        ...

    @abstractmethod
    async def audit_autonomous_ethics(
        self, tools_created: str, info_collected: str
    ) -> Tuple[bool, str, str]:
        ...

    def audit_evolution_proposal_sync(
        self, target_file: str, proposed_code: str, reason: str
    ) -> AuditResult:
        """동기 래퍼 (EvolutionManager용)."""
        try:
            return asyncio.run(self.audit_evolution_proposal(target_file, proposed_code, reason))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self.audit_evolution_proposal(target_file, proposed_code, reason)
                )
            finally:
                loop.close()


# =============================================================================
# PolicyGuardian (폐쇄망 기본 — 규칙 기반, LLM 없음)
# =============================================================================

def _policy_is_path_allowed(target_file: str) -> Tuple[bool, str]:
    """대상 파일이 허용 범위(project_root 내)인지 검사. (파일범위 검사)"""
    if not target_file or not target_file.strip():
        return True, ""
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        root = getattr(s, "project_root", None)
        if root is None:
            return True, ""
        root_path = Path(str(root)).resolve()
        candidate = Path(target_file).resolve()
        if not candidate.is_absolute():
            return True, ""
        try:
            candidate.relative_to(root_path)
            return True, ""
        except ValueError:
            return False, f"대상 파일이 허용 범위 밖: {target_file}"
    except Exception:
        return True, ""


class PolicyGuardian(GuardianBase):
    """
    규칙 기반 심사. 비용/파일범위/위험패턴 검사. LLM 호출 없음.
    폐쇄망(ENABLE_GUARDIAN_APIS=0) 기본.
    """

    async def audit_insight(
        self,
        insight: BehaviorInsight,
        raw_logs: Optional[List[ExperienceRecord]] = None,
        raw_logs_text: Optional[str] = None,
    ) -> AuditResult:
        return AuditResult(
            is_approved=True,
            critique="PolicyGuardian(ENABLE_GUARDIAN_APIS=0): 규칙 기반만 사용. LLM 호출 없음. 로컬 분석 신뢰.",
            refined_recommendation=insight.recommendation,
            guardian_actually_ran=False,
            policy_decision="APPROVE",
        )

    async def audit_tool_code(
        self, tool_name: str, description: str, code: str
    ) -> AuditResult:
        level, reason = classify_code_risk_level(code or "")
        if level == 3:
            return AuditResult(
                is_approved=False,
                critique=f"PolicyGuardian: REJECT. Level 3 위험 패턴. ({reason})",
                refined_recommendation="",
                guardian_actually_ran=False,
                risk_score=100,
                policy_decision="REJECT",
            )
        if level == 2:
            return AuditResult(
                is_approved=False,
                critique="PolicyGuardian: NEED_AI_REVIEW (level=2). Operator 또는 AIGuardian 승인 필요.",
                refined_recommendation="",
                guardian_actually_ran=False,
                risk_score=50,
                policy_decision="NEED_AI_REVIEW",
            )
        return AuditResult(
            is_approved=True,
            critique="PolicyGuardian: 규칙 기반 검수 통과 (level=1). LLM 검수 없음.",
            refined_recommendation="",
            guardian_actually_ran=False,
            risk_score=0,
            policy_decision="APPROVE",
        )

    async def audit_evolution_proposal(
        self, target_file: str, proposed_code: str, reason: str
    ) -> AuditResult:
        # 파일범위 검사
        allowed, path_msg = _policy_is_path_allowed(target_file)
        if not allowed:
            return AuditResult(
                is_approved=False,
                critique=f"PolicyGuardian: {path_msg}",
                refined_recommendation="",
                guardian_actually_ran=False,
                policy_decision="REJECT",
            )
        level, level_reason = classify_code_risk_level(proposed_code or "")
        if level == 3:
            return AuditResult(
                is_approved=False,
                critique=f"PolicyGuardian: REJECT. Level 3 위험 패턴. {level_reason}",
                refined_recommendation="",
                guardian_actually_ran=False,
                risk_score=100,
                policy_decision="REJECT",
            )
        if level == 2:
            return AuditResult(
                is_approved=False,
                critique="PolicyGuardian: NEED_AI_REVIEW (level=2). Operator 또는 AIGuardian 승인 필요.",
                refined_recommendation="",
                guardian_actually_ran=False,
                risk_score=50,
                policy_decision="NEED_AI_REVIEW",
            )
        return AuditResult(
            is_approved=True,
            critique="PolicyGuardian: 규칙 기반 검수 통과 (level=1). LLM 검수 없음.",
            refined_recommendation="",
            guardian_actually_ran=False,
            risk_score=0,
            policy_decision="APPROVE",
        )

    async def audit_autonomous_ethics(
        self, tools_created: str, info_collected: str
    ) -> Tuple[bool, str, str]:
        return (
            False,
            "PolicyGuardian: 윤리 검수는 AIGuardian 필요(ENABLE_GUARDIAN_APIS=1).",
            "폐쇄망",
        )


# =============================================================================
# AIGuardian (ENABLE_GUARDIAN_APIS=1일 때 — LLM 기반 Tower/Verdict/Audit)
# =============================================================================

class AIGuardian(GuardianBase):
    """
    LLM 기반 보호자. ENABLE_GUARDIAN_APIS=1일 때만 사용.
    Tower/Verdict/Audit(Claude, GPT 등)로 2차 검수. 보안·예산 안전장치 적용.
    """

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        provider: Optional[str] = None,
        db: Optional[MemoryDatabase] = None,
    ):
        """
        Args:
            anthropic_api_key: Anthropic API 키 (None이면 env에서 로드)
            openai_api_key: OpenAI API 키 (None이면 env에서 로드)
            provider: "anthropic" | "openai" (None이면 env에서 로드)
            db: MemoryDatabase (api_usage_logs 저장용)
        """
        cfg = get_guardian_config()
        a_key, o_key, prov, max_cost, max_tokens = cfg[0], cfg[1], cfg[2], cfg[3], cfg[4]
        self._anthropic_key = anthropic_api_key or a_key
        self._openai_key = openai_api_key or o_key
        self._provider = (provider or prov).strip().lower()
        self._max_daily_cost = max_cost or 0.0
        self._max_daily_tokens = max_tokens or 0
        self._db = db or get_memory_db()

        if self._provider not in ("anthropic", "openai"):
            self._provider = "anthropic"

        try:
            from mellow_link.config.settings import get_settings
            if not get_settings().allow_guardian_api():
                self._anthropic_key = None
                self._openai_key = None
        except Exception:
            pass

        logger.info(
            f"[AIGuardian] Initialized (provider={self._provider}, "
            f"max_daily_cost={self._max_daily_cost}, max_daily_tokens={self._max_daily_tokens})"
        )

    def _is_available(self) -> bool:
        """Guardian API 사용 가능 여부."""
        if self._provider == "anthropic":
            return bool(self._anthropic_key)
        return bool(self._openai_key)

    def _is_path_blocked(self, provider: Optional[str] = None) -> bool:
        """
        서킷 브레이커: 경로가 차단되었는지 반환.
        provider 미지정 시 self._provider 사용.
        True = 차단됨 (요청 불가), False = 통과 가능.
        """
        global _CIRCUIT_BREAKER
        prov = (provider or self._provider).strip().lower()
        until = _CIRCUIT_BREAKER.get(prov)
        if until is None:
            return False
        if datetime.now() >= until:
            _CIRCUIT_BREAKER.pop(prov, None)
            _log_system("CIRCUIT_RESET", f"provider={prov}")
            return False
        return True

    def _trip_circuit(self, duration_minutes: int = 30) -> None:
        """429 등으로 서킷 트립."""
        global _CIRCUIT_BREAKER
        until = datetime.now() + timedelta(minutes=duration_minutes)
        _CIRCUIT_BREAKER[self._provider] = until
        _log_system(
            "CIRCUIT_TRIPPED",
            f"provider={self._provider} suspended until {until.isoformat()}"
        )

    def _check_quota(self, provider: Optional[str] = None) -> Tuple[bool, str]:
        """
        쿼터 확인. 한도 초과 시 SKIP.
        provider 미지정 시 self._provider 사용.
        Fail-Safe: max_daily_cost, max_daily_tokens가 둘 다 0이면 차단 (명시적 허용 필요).

        Returns:
            (allowed, reason)
        """
        prov = (provider or self._provider).strip().lower()
        s = None
        try:
            from mellow_link.config.settings import get_settings
            s = get_settings()
        except Exception:
            pass
        max_cost = self._max_daily_cost
        if prov == "openai" and s:
            max_cost = getattr(s, "max_daily_cost_openai", 0.0) or 0.0
        elif prov == "anthropic" and s:
            max_cost = getattr(s, "max_daily_cost", 0.0) or self._max_daily_cost

        if max_cost <= 0 and self._max_daily_tokens <= 0:
            _log_system("QUOTA_BLOCK", "예산 한도 미설정(둘 다 0). Fail-Safe로 Guardian API 차단.")
            return False, "예산 한도 미설정. MAX_DAILY_COST 또는 MAX_DAILY_TOKENS를 양수로 설정하세요."

        usage = self._db.get_daily_usage(prov)
        if max_cost > 0 and usage["cost"] >= max_cost:
            _log_system("QUOTA_EXCEEDED", f"provider={prov} daily cost {usage['cost']:.4f} >= {max_cost}")
            return False, f"일일 비용 한도 초과 (사용: {usage['cost']:.4f} USD)"
        if self._max_daily_tokens > 0 and usage["token_count"] >= self._max_daily_tokens:
            _log_system("QUOTA_EXCEEDED", f"provider={prov} daily tokens {usage['token_count']} >= {self._max_daily_tokens}")
            return False, f"일일 토큰 한도 초과 (사용: {usage['token_count']})"
        return True, ""

    def _select_auditor_for_code(self, code: str) -> Tuple[str, int, str]:
        """
        Tiered Auditing: 코드 위험도에 따라 검수관 선택.

        Returns:
            (provider, level, reason) - "openai"|"anthropic", 1|2|3, 사유
        """
        try:
            from mellow_link.config.settings import get_settings
            s = get_settings()
            if not getattr(s, "enable_tiered_auditing", True):
                return self._provider, 0, "tiered_disabled"
        except Exception:
            pass
        level, reason = classify_code_risk_level(code or "")
        if level == 1:
            if self._openai_key:
                return "openai", 1, reason
            return self._provider, 1, f"openai_unavail_fallback:{reason}"
        return "anthropic", level, reason

    def _should_skip_external_api(self, insight: BehaviorInsight) -> Tuple[bool, str]:
        """
        선별적 호출: confidence < 0.85 또는 단순 반복 패턴이면 로컬 승인으로 대체.
        
        Returns:
            (skip, reason)
        """
        if insight.confidence < 0.85:
            return True, f"confidence {insight.confidence} < 0.85, 로컬 승인"
        # 단순 반복 패턴 휴리스틱
        finding = (insight.finding or "").lower()
        simple_phrases = [
            "성공률이 낮습니다",
            "도구 실패",
            "실행 시간이 길습니다",
            "가장 빈번한 실패",
        ]
        if any(p in finding for p in simple_phrases) and len(finding) < 120:
            return True, "단순 반복 패턴, 로컬 승인"
        return False, ""

    def _build_raw_logs_text(
        self,
        raw_logs: Optional[List[ExperienceRecord]] = None,
        raw_logs_text: Optional[str] = None
    ) -> str:
        """원본 로그 텍스트 구성."""
        if raw_logs_text:
            return raw_logs_text[:4000]
        if raw_logs:
            lines = []
            for exp in raw_logs[:5]:
                lines.append(f"[{exp.task_intent}]")
                lines.append(f"  action_steps: {(exp.action_steps or '')[:500]}...")
                lines.append(f"  lessons: {exp.lessons_learned or 'N/A'}")
            return "\n".join(lines)[:4000]
        return "(원본 로그 없음)"

    async def audit_insight(
        self,
        insight: BehaviorInsight,
        raw_logs: Optional[List[ExperienceRecord]] = None,
        raw_logs_text: Optional[str] = None
    ) -> AuditResult:
        """
        반성문(Insight)을 외부 감사관에게 검증 요청.
        
        쿼터 초과, 서킷 브레이커, 선별적 필터링 시 외부 API를 호출하지 않고
        로컬 결과만 반환합니다. 보호자 부재 시 is_verified=0, confidence -= 0.2.
        """
        try:
            from mellow_link.config.settings import get_settings
            if not get_settings().allow_guardian_api():
                log_airgap_block("AIGuardian.audit_insight", "ENABLE_GUARDIAN_APIS")
                return AuditResult(
                    is_approved=False,
                    critique="ENABLE_GUARDIAN_APIS=0. Guardian API 비활성화(폐쇄망). 로컬 분석만 사용.",
                    refined_recommendation=insight.recommendation,
                    guardian_actually_ran=False,
                )
        except Exception:
            pass
        logs_text = self._build_raw_logs_text(raw_logs, raw_logs_text)

        if not self._is_available():
            logger.debug("[AIGuardian] API 키 미설정, 감사 건너뜀")
            return AuditResult(
                is_approved=False,
                critique="Guardian API 키가 설정되지 않음. 로컬 분석만 신뢰.",
                refined_recommendation=insight.recommendation,
                guardian_actually_ran=False,
            )

        # 선별적 호출: confidence 낮거나 단순 패턴이면 SKIP
        skip, skip_reason = self._should_skip_external_api(insight)
        if skip:
            _log_system("PRIORITY_SKIP", skip_reason)
            return AuditResult(
                is_approved=True,
                critique=f"로컬 승인 ({skip_reason})",
                refined_recommendation=insight.recommendation,
                guardian_actually_ran=False,
            )

        # 쿼터 확인
        allowed, quota_reason = self._check_quota()
        if not allowed:
            _log_system("QUOTA_SKIP", quota_reason)
            return AuditResult(
                is_approved=False,
                critique=f"한도 초과로 감사 SKIP: {quota_reason}",
                refined_recommendation=insight.recommendation,
                guardian_actually_ran=False,
            )

        # 서킷 브레이커
        if self._is_path_blocked():
            _log_system("CIRCUIT_SKIP", f"provider={self._provider} 일시 중단 중")
            return AuditResult(
                is_approved=False,
                critique="Guardian API 일시 중단(429 회복 대기). 로컬 분석만 사용.",
                refined_recommendation=insight.recommendation,
                guardian_actually_ran=False,
            )

        prompt = self._build_audit_prompt(insight, logs_text)

        try:
            if self._provider == "anthropic":
                result = await self._audit_via_anthropic(prompt, insight.recommendation)
            else:
                result = await self._audit_via_openai(prompt, insight.recommendation)
            return result
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate limit" in err_str or "too many requests" in err_str:
                self._trip_circuit(30)
                _log_system("CIRCUIT_TRIPPED", f"429 detected: {str(e)[:100]}")
            logger.warning(f"[AIGuardian] Audit API failed: {e}", exc_info=False)
            return AuditResult(
                is_approved=False,
                critique=f"감사 API 오류: {str(e)[:200]}. 로컬 분석 결과만 사용.",
                refined_recommendation=insight.recommendation,
                guardian_actually_ran=False,
            )

    async def audit_tool_code(
        self,
        tool_name: str,
        description: str,
        code: str,
    ) -> AuditResult:
        """
        도구 코드 보호자 2차 검수 (Tool Forge용).
        
        "이 도구의 로직이 안전하고 효율적인가?" 검토.
        
        Args:
            tool_name: 도구 이름
            description: 도구 설명
            code: 제안된 Python 코드
            
        Returns:
            AuditResult (is_approved, critique, refined_recommendation)
        """
        try:
            from mellow_link.config.settings import get_settings
            if not get_settings().allow_guardian_api():
                log_airgap_block("AIGuardian.audit_tool_code", "ENABLE_GUARDIAN_APIS")
                return AuditResult(
                    is_approved=False,
                    critique="ENABLE_GUARDIAN_APIS=0. Guardian API 비활성화(폐쇄망). 감사 불가.",
                    refined_recommendation="",
                    guardian_actually_ran=False,
                )
        except Exception:
            pass
        if not self._is_available():
            return AuditResult(
                is_approved=False,
                critique="Guardian API 미설정. 감사 불가 시 실행 불가(Fail-Closed).",
                refined_recommendation="",
                guardian_actually_ran=False,
            )

        auditor, level, reason = self._select_auditor_for_code(code)
        allowed, quota_reason = self._check_quota(auditor)
        if not allowed:
            return AuditResult(
                is_approved=False,
                critique=f"Guardian 쿼터 초과. 감사 불가 시 실행 불가(Fail-Closed): {quota_reason}",
                refined_recommendation="",
                guardian_actually_ran=False,
            )

        if auditor == "openai" and not self._openai_key:
            auditor = self._provider
        if self._is_path_blocked(auditor):
            if auditor == "openai" and self._anthropic_key and not self._is_path_blocked("anthropic"):
                allowed_fb, quota_fb = self._check_quota("anthropic")
                if not allowed_fb:
                    return AuditResult(
                        is_approved=False,
                        critique=f"Guardian 폴백 대상(anthropic) 쿼터 초과: {quota_fb}",
                        refined_recommendation="",
                        guardian_actually_ran=False,
                    )
                auditor = "anthropic"
            else:
                return AuditResult(
                    is_approved=False,
                    critique=f"Guardian {auditor} 일시 중단(서킷 브레이커). 감사 불가.",
                    refined_recommendation="",
                    guardian_actually_ran=False,
                )
        logger.info("[AIGuardian] audit_tool_code level=%s auditor=%s reason=%s", level, auditor, reason)

        prompt = f"""너는 시니어 소프트웨어 아키텍트이자 감사관이다.
에이전트가 제안한 동적 도구 코드가 안전하고 효율적인지 검토하라.

## 도구 이름
{tool_name}

## 도구 설명
{description}

## 제안된 코드
```python
{code[:6000]}
```

## 요청사항
1. os, subprocess, eval 등 위험 API 사용 여부 확인 (이미 AST에서 차단됐을 수 있음).
2. 로직이 설명과 일치하는지, 비효율적이거나 취약한 패턴이 있는지 검토.
3. 안전하고 효율적이면 승인, 문제 있으면 거부하라.

## 출력 형식 (JSON만)
{{
  "is_approved": true 또는 false,
  "critique": "검토 의견",
  "refined_recommendation": "거부 시 수정 제안, 승인 시 빈 문자열"
}}"""

        try:
            if auditor == "anthropic":
                return await self._audit_via_anthropic(prompt, "")
            return await self._audit_via_openai(prompt, "")
        except Exception as e:
            logger.warning(f"[AIGuardian] audit_tool_code failed: {e}")
            return AuditResult(
                is_approved=False,
                critique=f"Guardian 오류: {str(e)[:150]}. 감사 불가 시 실행 불가(Fail-Closed).",
                refined_recommendation="",
                guardian_actually_ran=False,
            )

    async def audit_evolution_proposal(
        self,
        target_file: str,
        proposed_code: str,
        reason: str,
    ) -> AuditResult:
        """
        진화(자기 수정) 제안 2차 검수 (Phase 5 EvolutionManager용).
        
        제안된 코드의 논리적 오류, 보안 위협, 비효율 패턴을 검토합니다.
        기술 검토: Guardian Integration ✅ verified
        C1: Level 3 위험 패턴은 LLM 호출 없이 즉시 거부 (프롬프트 인젝션 원천 차단).
        """
        try:
            from mellow_link.config.settings import get_settings
            if not get_settings().allow_guardian_api():
                log_airgap_block("AIGuardian.audit_evolution_proposal", "ENABLE_GUARDIAN_APIS")
                return AuditResult(
                    is_approved=False,
                    critique="ENABLE_GUARDIAN_APIS=0. Guardian API 비활성화(폐쇄망). 진화 검수 불가.",
                    refined_recommendation="",
                    guardian_actually_ran=False,
                )
        except Exception:
            pass
        if not self._is_available():
            return AuditResult(
                is_approved=False,
                critique="Guardian API 미설정. 감사 불가 시 실행 불가(Fail-Closed).",
                refined_recommendation="",
                guardian_actually_ran=False,
            )

        # ✅ C1: risk_classifier Level 3 → LLM 호출 전 하드 블록 (Jailbreak 방어)
        level, level_reason = classify_code_risk_level(proposed_code or "")
        if level == 3:
            return AuditResult(
                is_approved=False,
                critique=f"[HARD_BLOCK] Level 3 위험 패턴 감지: {level_reason}",
                refined_recommendation="",
                guardian_actually_ran=False,
                risk_score=100,
            )

        auditor, level, reason = self._select_auditor_for_code(proposed_code)
        allowed, quota_reason = self._check_quota(auditor)
        if not allowed:
            return AuditResult(
                is_approved=False,
                critique=f"Guardian 쿼터 초과. 감사 불가 시 실행 불가(Fail-Closed): {quota_reason}",
                refined_recommendation="",
                guardian_actually_ran=False,
            )

        if auditor == "openai" and not self._openai_key:
            auditor = self._provider
        if self._is_path_blocked(auditor):
            if auditor == "openai" and self._anthropic_key and not self._is_path_blocked("anthropic"):
                allowed_fb, quota_fb = self._check_quota("anthropic")
                if not allowed_fb:
                    return AuditResult(
                        is_approved=False,
                        critique=f"Guardian 폴백 대상(anthropic) 쿼터 초과: {quota_fb}",
                        refined_recommendation="",
                        guardian_actually_ran=False,
                    )
                auditor = "anthropic"
            else:
                return AuditResult(
                    is_approved=False,
                    critique=f"Guardian {auditor} 일시 중단(서킷 브레이커). 감사 불가.",
                    refined_recommendation="",
                    guardian_actually_ran=False,
                )
        logger.info("[AIGuardian] audit_evolution_proposal level=%s auditor=%s reason=%s", level, auditor, reason)

        # ✅ verified: 2차 검수 고도화 — Side Effects·보안 체크리스트·위험 점수
        prompt = f"""너는 시니어 소프트웨어 아키텍트이자 보안 감사관이다.
에이전트가 제안한 자기 수정(self-modification) 코드를 엄격히 검토하라.

## 대상 파일
{target_file}

## 수정 사유
{reason}

## 제안된 코드
```python
{proposed_code[:6000]}
```

## 필수 검토 (보안 체크리스트)
1. **경로 탈출(Path Traversal)**: open(), Path(), __file__ 등으로 sandbox 밖 접근 가능성.
2. **임의 코드 실행(RCE)**: eval, exec, __import__, subprocess, os.system, compile() 등 사용 여부.
3. **리소스 고갈(Resource Exhaustion)**: 무한 루프, 대용량 메모리 할당, 무제한 파일/네트워크 I/O 가능성.
4. **기존 아키텍처와의 충돌(Side Effects)**: 수정된 코드가 기존 모듈/API 계약을 깨거나, 예기치 않은 호출 경로를 만드는지 시뮬레이션하라.

## 위험 점수 (Risk Score, 0-100)
- 0-30: 안전, 31-50: 주의, 51-69: 검토 권고, 70-100: 자동 적용 차단(거부 권고).
- 위 체크리스트 위반 시 70 이상 부여. is_approved는 risk_score < 70이고 논리·보안 문제가 없을 때만 true.

## 출력 형식 (JSON만)
{{
  "is_approved": true 또는 false,
  "critique": "검토 의견 (위반 항목·근거 포함)",
  "refined_recommendation": "거부 시 수정 제안, 승인 시 빈 문자열",
  "risk_score": 0 이상 100 이하 정수
}}"""

        try:
            if auditor == "anthropic":
                result = await self._audit_via_anthropic(prompt, "")
            else:
                result = await self._audit_via_openai(prompt, "")
            # 위험 점수 70 이상이면 자동 적용 차단
            if getattr(result, "risk_score", 0) >= 70:
                result = AuditResult(
                    is_approved=False,
                    critique=(result.critique or "") + " [자동 적용 차단: 위험 점수 " + str(getattr(result, "risk_score", 0)) + "]",
                    refined_recommendation=result.refined_recommendation,
                    guardian_actually_ran=result.guardian_actually_ran,
                    risk_score=getattr(result, "risk_score", 0),
                )
            return result
        except Exception as e:
            logger.warning(f"[AIGuardian] audit_evolution_proposal failed: {e}")
            return AuditResult(
                is_approved=False,
                critique=f"Guardian 오류: {str(e)[:150]}. 감사 불가 시 실행 불가(Fail-Closed).",
                refined_recommendation="",
                guardian_actually_ran=False,
            )

    async def audit_autonomous_ethics(
        self,
        tools_created: str,
        info_collected: str,
    ) -> Tuple[bool, str, str]:
        """
        자율 작업 결과물의 윤리·저작권 2차 검수 (Anthropic/OpenAI).
        Tower(Gemini)가 자기 계획을 검토하던 구조를 폐기하고, 외부 모델이 독립 검수.

        Returns:
            (ethics_approved, review, violations)
        """
        try:
            from mellow_link.config.settings import get_settings
            if not get_settings().allow_guardian_api():
                log_airgap_block("AIGuardian.audit_autonomous_ethics", "ENABLE_GUARDIAN_APIS")
                return False, "ENABLE_GUARDIAN_APIS=0. Guardian API 비활성화(폐쇄망).", "폐쇄망"
        except Exception:
            pass
        if not self._is_available():
            return False, "Guardian API 미설정. 감사 불가 시 승인 불가(Fail-Closed).", "API 미설정"

        if self._is_path_blocked():
            return False, "Guardian 일시 중단. 감사 불가 시 승인 불가(Fail-Closed).", "서킷 브레이커"

        allowed, quota_reason = self._check_quota()
        if not allowed:
            return False, f"Guardian 쿼터 초과: {quota_reason}", "쿼터 초과"

        prompt = f"""너는 윤리 및 저작권 감사관이다.
자율 에이전트가 생성한 작업 결과물에 대해 윤리·권리 검토를 수행하라.

## 1. 제작된 도구
{tools_created or "(없음)"}

## 2. 수집한 정보
{(info_collected or "(없음)")[:2000]}

## 검토 항목
1. 제작된 도구가 시스템 보안 및 윤리 가이드를 준수하는가?
2. 스크랩된 정보가 타인의 저작권이나 권리를 침해하지 않는가?

## 출력 (JSON만, 다른 설명 금지)
{{
  "is_approved": true 또는 false,
  "critique": "검토 의견 (위반 사항 발견 시 포함)",
  "refined_recommendation": "위반 사항 요약 또는 없음"
}}"""

        try:
            if self._provider == "anthropic":
                result = await self._audit_via_anthropic(prompt, "")
            else:
                result = await self._audit_via_openai(prompt, "")

            violations = (result.refined_recommendation or result.critique or "")[:500] if not result.is_approved else "없음"
            return result.is_approved, result.critique or "", violations
        except Exception as e:
            logger.warning(f"[AIGuardian] audit_autonomous_ethics failed: {e}")
            return False, f"감사 API 오류: {str(e)[:150]}", "API 예외"

    def _build_audit_prompt(self, insight: BehaviorInsight, logs_text: str) -> str:
        """감사용 프롬프트 구성."""
        return f"""너는 시니어 소프트웨어 아키텍트이자 감사관이다.
아래의 '어린 에이전트'(Mellow-Link)가 작성한 자기 성찰 결과가 원본 로그에 비추어 볼 때 논리적으로 타당한지 검토하라.

## 어린 에이전트의 성찰 (finding)
{insight.finding}

## 어린 에이전트의 권고 (recommendation)
{insight.recommendation}

## 원본 로그 (ReAct 루프 등)
{logs_text}

## 요청사항
1. finding이 원본 로그의 사실과 맞는지 검증하라.
2. recommendation이 논리적으로 타당하고 실행 가능한지 판단하라.
3. "어린아이의 착각"이면 수정 필요, 타당하면 승인하라.

## 출력 형식 (JSON만 출력, 다른 설명 금지)
{{
  "is_approved": true 또는 false,
  "critique": "검토 의견 (한 문단)",
  "refined_recommendation": "승인 시 기존 recommendation 그대로, 수정 필요 시 개선된 권고 문구"
}}"""

    def _estimate_tokens_and_cost(self, prompt: str, response_len: int = 800) -> Tuple[int, float]:
        """대략적 토큰/비용 추정 (영어 4자≈1토큰 가정)."""
        input_tok = max(1, len(prompt) // 4)
        output_tok = max(1, response_len // 4)
        total = input_tok + output_tok
        rate = _ESTIMATE_COST_PER_1K.get(self._provider, 0.004)
        cost = (total / 1000.0) * rate
        return total, cost

    def _record_usage(self, token_count: int, cost: float, endpoint: str = "audit", provider: Optional[str] = None) -> None:
        """API 사용량 기록. provider 미지정 시 self._provider 사용."""
        try:
            prov = (provider or self._provider).strip().lower()
            self._db.save_api_usage(
                provider=prov,
                endpoint=endpoint,
                token_count=token_count,
                cost=cost
            )
        except Exception as e:
            logger.debug(f"[AIGuardian] Failed to record usage: {e}")

    async def _audit_via_anthropic(
        self,
        prompt: str,
        fallback_recommendation: str
    ) -> AuditResult:
        """Anthropic (Claude) API로 감사."""
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            logger.warning("[AIGuardian] anthropic 패키지 미설치")
            return AuditResult(False, "anthropic 미설치", fallback_recommendation, guardian_actually_ran=False)

        async with AsyncAnthropic(api_key=self._anthropic_key) as client:
            try:
                response = await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=800,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
            except Exception as e:
                if hasattr(e, "status_code") and e.status_code == 429:
                    self._trip_circuit(30)
                    _log_system("CIRCUIT_TRIPPED", "Anthropic 429")
                raise

            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            # 사용량 기록 (usage 있는 경우 활용, 없으면 추정)
            inp = getattr(response, "usage", None) and getattr(response.usage, "input_tokens", None)
            out = getattr(response, "usage", None) and getattr(response.usage, "output_tokens", None)
            if inp is not None and out is not None:
                total = inp + out
                cost = (total / 1000.0) * _ESTIMATE_COST_PER_1K.get("anthropic", 0.003)
            else:
                total, cost = self._estimate_tokens_and_cost(prompt, len(text))
            self._record_usage(total, cost, "messages.create", provider="anthropic")

            return self._parse_audit_response(text, fallback_recommendation)

    async def _audit_via_openai(
        self,
        prompt: str,
        fallback_recommendation: str
    ) -> AuditResult:
        """OpenAI (GPT) API로 감사."""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            logger.warning("[AIGuardian] openai 패키지 미설치")
            return AuditResult(False, "openai 미설치", fallback_recommendation, guardian_actually_ran=False)

        try:
            async with AsyncOpenAI(api_key=self._openai_key) as client:
                try:
                    response = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "너는 시니어 소프트웨어 아키텍트이자 감사관이다. JSON만 출력한다."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=800,
                        temperature=0.3,
                    )
                except Exception as e:
                    if hasattr(e, "status_code") and e.status_code == 429:
                        self._trip_circuit(30)
                        _log_system("CIRCUIT_TRIPPED", "OpenAI 429")
                    raise

                text = response.choices[0].message.content or ""

                # 사용량 기록
                usage = getattr(response, "usage", None)
                if usage and hasattr(usage, "total_tokens"):
                    total = usage.total_tokens
                    cost = (total / 1000.0) * _ESTIMATE_COST_PER_1K.get("openai", 0.005)
                else:
                    total, cost = self._estimate_tokens_and_cost(prompt, len(text))
                self._record_usage(total, cost, "chat.completions.create", provider="openai")

                return self._parse_audit_response(text, fallback_recommendation)
        except TypeError:
            # 구버전 OpenAI SDK는 async with 미지원 → 클라이언트 직접 사용
            client = AsyncOpenAI(api_key=self._openai_key)
            try:
                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "너는 시니어 소프트웨어 아키텍트이자 감사관이다. JSON만 출력한다."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=800,
                    temperature=0.3,
                )
            except Exception as e:
                if hasattr(e, "status_code") and e.status_code == 429:
                    self._trip_circuit(30)
                    _log_system("CIRCUIT_TRIPPED", "OpenAI 429")
                raise
            finally:
                try:
                    await client.close()
                except RuntimeError:
                    pass
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage and hasattr(usage, "total_tokens"):
                total = usage.total_tokens
                cost = (total / 1000.0) * _ESTIMATE_COST_PER_1K.get("openai", 0.005)
            else:
                total, cost = self._estimate_tokens_and_cost(prompt, len(text))
            self._record_usage(total, cost, "chat.completions.create", provider="openai")
            return self._parse_audit_response(text, fallback_recommendation)

    def _parse_audit_response(self, text: str, fallback: str) -> AuditResult:
        """API 응답 파싱."""
        try:
            text = text.strip()
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                text = text[start:end].strip()
            elif "```" in text:
                start = text.find("```") + 3
                end = text.find("```", start)
                text = text[start:end].strip()

            data = json.loads(text)
            is_approved = bool(data.get("is_approved", False))
            critique = str(data.get("critique", ""))[:500]
            refined = str(data.get("refined_recommendation", fallback)).strip() or fallback
            risk_score = max(0, min(100, int(data.get("risk_score", 0))))

            return AuditResult(
                is_approved=is_approved,
                critique=critique,
                refined_recommendation=refined,
                risk_score=risk_score,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[AIGuardian] Parse audit response failed: {e}")
            return AuditResult(
                is_approved=False,
                critique=f"응답 파싱 실패: {str(e)[:100]}",
                refined_recommendation=fallback
            )


# =============================================================================
# Factory: ENABLE_GUARDIAN_APIS에 따라 PolicyGuardian / AIGuardian 자동 선택
# =============================================================================

_guardian_instance: Optional[GuardianBase] = None


def get_guardian_service(
    anthropic_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    provider: Optional[str] = None,
    db: Optional[MemoryDatabase] = None,
) -> GuardianBase:
    """
    ENABLE_GUARDIAN_APIS=1 → AIGuardian (LLM 기반).
    ENABLE_GUARDIAN_APIS=0(기본) → PolicyGuardian (규칙 기반, LLM 없음).

    싱글톤: 한 번 선택된 구현체는 프로세스 수명 동안 유지됩니다.
    런타임에 env를 바꿔도 반영되지 않으며, 반영하려면 프로세스 재시작이 필요합니다.
    (납품 환경에서는 재시작 전까지 일관된 정책 유지로 안전.) 스펙: docs/AI/41_POLICY_GUARDIAN_SPEC.md
    """
    global _guardian_instance
    if _guardian_instance is None:
        try:
            from mellow_link.config.settings import get_settings
            if get_settings().allow_guardian_api():
                _guardian_instance = AIGuardian(
                    anthropic_api_key=anthropic_api_key,
                    openai_api_key=openai_api_key,
                    provider=provider,
                    db=db,
                )
                logger.info("[Guardian] Using AIGuardian (ENABLE_GUARDIAN_APIS=1)")
            else:
                _guardian_instance = PolicyGuardian()
                logger.info("[Guardian] Using PolicyGuardian (폐쇄망 기본)")
        except Exception as e:
            logger.warning("[Guardian] allow_guardian_api check failed, defaulting to PolicyGuardian: %s", e)
            _guardian_instance = PolicyGuardian()
    return _guardian_instance
