"""
Evolution 데이터 구조: SecurityError, EvolutionProposal.

삼권분립 파이프라인 결과 및 보안 예외 정의.
"""
from dataclasses import dataclass
from typing import Optional


class SecurityError(Exception):
    """보안 정책 위반 시 발생. 샌드박스 미설정, 경로 차단 등."""


@dataclass
class EvolutionProposal:
    """삼권분립 파이프라인 결과."""
    id: str
    user_request: str
    tower_report: str = ""       # Step 1: 관제 분석 보고서
    verdict_target_file: str = ""
    verdict_proposed_code: str = ""
    verdict_reason: str = ""     # Step 2: 판결 코드 수정안
    audit_approved: bool = False
    audit_critique: str = ""
    audit_refined: str = ""      # Step 3: 검수 결과
    created_at: str = ""
    error: Optional[str] = None
    plan_pending: bool = False   # True: Tower만 완료, 진행 승인 대기
    cost_efficiency_briefing: str = ""  # 가성비 브리핑
    root_goal_id: Optional[str] = None  # 목표 주도 진화 — 연결된 목표 ID
    audit_risk_score: int = 0    # Guardian 위험 점수 0-100 (고도화 검수)
