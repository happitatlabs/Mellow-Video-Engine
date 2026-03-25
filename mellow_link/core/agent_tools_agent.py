"""
Agent Tools - 에이전트 제어 도구: finish, propose_new_tool + Evolution Ledger 헬퍼.
"""
import logging
from typing import Optional

from mellow_link.core.tool_registry import tool
from mellow_link.core.agent_tools_base import (
    _get_security,
    _pm,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 6. 에이전트 제어
# ═══════════════════════════════════════════════

@tool(category="agent")
def finish(summary: str) -> str:
    """
    현재 작업을 완료하고 최종 결과를 반환합니다.
    에이전트 루프가 이 도구의 호출을 감지하면 실행을 종료합니다.
    
    [REPORTING_PROTOCOL] 보고 시 반드시 다음을 준수하라:
    
    1. 원본 데이터 포함:
       - Observation에서 취득한 실제 데이터를 반드시 포함하라.
       - "확인했다", "알아봤다" 같은 추상적 표현 금지.
       - 구체적 액션 기술: "A 파일의 B 코드를 C로 수정함"
    
    2. 중요 패턴 리스트:
       - 발견한 중복, 취약점, 개선점 등을 리스트 형식으로 나열하라.
       - 각 항목은 원자 단위로 쪼개서 제공하라.
    
    3. 승인 가능한 형태:
       - 멜로우 파트너가 즉시 ✅ verified를 누를 수 있도록 정보를 구조화하라.
       - '무엇을 고쳤는가'와 '그로 인해 어떤 리스크가 사라졌는가'를 명확히 비교하라.
    
    4. [APPROVAL_REQUIRED] 섹션:
       - 보고서 끝에 반드시 [APPROVAL_REQUIRED] 섹션을 생성하라.
       - 변경 사항 요약과 리스크 제거 효과를 명확히 제시하라.

    5. 6단계 작업 보고 형식 (Observation 기반 작업일 때만 적용):
       - [1단계] 요청 해석
       - [2단계] 실행 액션
       - [3단계] 핵심 Observation
       - [4단계] 검증 상태 (✅ verified 태그 필수)
       - [5단계] 리스크/한계
       - [6단계] 최종 답변
    
    Args:
        summary: 최종 보고서 (위 프로토콜 준수 필수)
    """
    return f"[FINISH] {summary}"


# ═══════════════════════════════════════════════
# Phase 4: 동적 도구 확장 (propose_new_tool)
# ═══════════════════════════════════════════════

@tool(category="agent")
def propose_new_tool(
    tool_name: str,
    description: str,
    code: str,
    parameters_json: str = "{}",
) -> str:
    """
    새로운 도구를 제안하여 검증·등록합니다.
    에이전트가 필요한 도구가 없다고 판단할 때 호출합니다.
    code는 반드시 tool_name과 동일한 이름의 함수를 정의하는 Python 코드여야 합니다.
    검증 통과 시 custom_tools/에 저장되어 이후 세션에서 사용 가능합니다.

    Args:
        tool_name: 도구(함수) 이름 (영문/숫자/밑줄 권장)
        description: 도구 설명 (LLM 프롬프트에 노출됨)
        code: 함수를 정의하는 Python 코드 문자열
        parameters_json: 파라미터 스키마 JSON (예: {"x": {"type": "string"}, "y": {"type": "int", "default": 0}})
    """
    # 벤치마크 모드에서 ToolForge 비활성화 확인
    try:
        from mellow_link.config import get_settings
        settings = get_settings()
        if not getattr(settings, "enable_tool_forge", True):
            return "[Error] ToolForge가 비활성화되어 있습니다 (ENABLE_TOOL_FORGE=0). 벤치마크 모드에서는 도구 생성이 불가능합니다."
    except Exception:
        # 설정 로드 실패 시 환경 변수 직접 확인
        import os
        if os.getenv("ENABLE_TOOL_FORGE", "").strip().lower() in {"0", "false", "no", "off", "disabled"}:
            return "[Error] ToolForge가 비활성화되어 있습니다 (ENABLE_TOOL_FORGE=0). 벤치마크 모드에서는 도구 생성이 불가능합니다."
    
    try:
        from mellow_link.core.tool_forge import get_tool_forge
        from mellow_link.core.dynamic_registry import get_dynamic_registry
        from pathlib import Path

        forge = get_tool_forge()
        custom_dir = Path(__file__).resolve().parent.parent / "custom_tools"
        custom_dir.mkdir(parents=True, exist_ok=True)

        result = forge.validate_and_register(
            tool_name=tool_name.strip(),
            description=description.strip(),
            code=code.strip(),
            parameters_json=(parameters_json or "{}").strip(),
            author_agent_id=None,
            write_to_custom_tools_dir=custom_dir,
        )

        if not result.success:
            msg = result.message or "; ".join(result.errors or ["검증 실패"])
            return f"[Error] 도구 검증 실패: {msg}"

        # 동적 레지스트리 hot-reload
        try:
            dyn = get_dynamic_registry()
            count = dyn.reload_custom_tools()
            return f"[완료] 도구 '{tool_name}' 검증·등록됨 (ID: {result.tool_id}). 동적 도구 {count}개 로드됨."
        except Exception as e:
            return f"[완료] 도구 '{tool_name}' 검증·등록됨 (ID: {result.tool_id}). 동적 리로드 중 오류: {e}"
    except Exception as e:
        logger.exception("[propose_new_tool] Failed")
        return f"[Error] propose_new_tool 실패: {e}"


# -----------------------------------------------------------------------------
# Evolution Ledger - 자가 학습용 진화 원장 (evolution_manager 연동)
# -----------------------------------------------------------------------------


def save_evolution_result(
    proposal_id: str,
    target_file: str,
    user_request: str,
    verdict_code: str,
    audit_critique: str,
    status: str,  # SUCCESS | FAIL | REJECTED
    token_usage: Optional[int] = None,
    cost: Optional[float] = None,
    latency: Optional[float] = None,
) -> bool:
    """
    자가발전 사이클 완료 시 결과를 진화 원장에 저장.
    pre_flight_check 실패, Audit 거부, 성공 모두 기록하여 학습 데이터로 활용.
    token_usage, cost, latency는 가성비 분석용.
    """
    try:
        from mellow_link.core.database import save_evolution_record
        return save_evolution_record(
            proposal_id=proposal_id,
            target_file=target_file,
            user_request=user_request,
            verdict_code=verdict_code,
            audit_critique=audit_critique,
            status=status,
            token_usage=token_usage,
            cost=cost,
            latency=latency,
        )
    except Exception as e:
        logger.warning("[agent_tools] save_evolution_result failed: %s", e)
        return False


def analyze_cost_efficiency(
    target_file: Optional[str] = None,
    limit: int = 100,
) -> tuple:
    """
    특정 파일/작업 유형별 '성공 1건당 평균 소모 비용' 및
    가성비가 떨어지는(실패 많고 토큰 많이 쓰는) 패턴 반환.
    Returns:
        (avg_cost_per_success, worst_ratio, worst_patterns)
    """
    try:
        from mellow_link.core.database import analyze_cost_efficiency as _analyze
        return _analyze(target_file=target_file, limit=limit)
    except Exception as e:
        logger.warning("[agent_tools] analyze_cost_efficiency failed: %s", e)
        return 0.0, 0.0, []


def get_cost_efficiency_briefing(cost: float, target_file: Optional[str] = None) -> str:
    """결재 보고서용 가성비 브리핑 문자열."""
    try:
        from mellow_link.core.database import get_cost_efficiency_briefing as _brief
        return _brief(cost=cost, target_file=target_file)
    except Exception as e:
        logger.warning("[agent_tools] get_cost_efficiency_briefing failed: %s", e)
        return f"이번 작업으로 약 ${cost:.4f}의 비용이 소모되었습니다." if cost > 0 else ""


def predict_low_roi(target_file: Optional[str] = None) -> tuple:
    """
    현재 요청이 성공 확률 낮고 비용만 높을 것으로 예상되는지 판단.
    Returns:
        (is_low_roi, reason_message)
    """
    try:
        from mellow_link.core.database import predict_low_roi as _predict
        return _predict(target_file=target_file)
    except Exception as e:
        logger.warning("[agent_tools] predict_low_roi failed: %s", e)
        return False, ""


def get_past_failure_context(target_file: Optional[str] = None, limit: int = 3) -> str:
    """
    특정 파일 수정 전, 과거의 실패 사례와 감사 피드백을 최대 limit건 요약하여 반환.
    Tower 프롬프트 주입용. target_file이 None이면 전체 최근 실패 사례 반환.
    """
    try:
        from mellow_link.core.database import fetch_past_failure_context
        return fetch_past_failure_context(target_file=target_file, limit=limit)
    except Exception as e:
        logger.warning("[agent_tools] get_past_failure_context failed: %s", e)
        return ""
