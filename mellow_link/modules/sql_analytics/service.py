from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

_SQL_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "sql_ai_decision_engine"
if str(_SQL_ENGINE_ROOT) not in sys.path:
    sys.path.append(str(_SQL_ENGINE_ROOT))

from app.schemas.request import AnalyzeRequest
from app.services.analysis_pipeline import AnalysisPipeline


class SQLAnalyticsService:
    SUPPORTED_RISK_KEYWORDS = (
        "환불", "문의", "이탈", "리스크", "위험", "이상", "징후", "증가", "감소", "추세", "변화", "경고",
    )
    SCHEMA_KEYWORDS = (
        "테이블", "컬럼", "열", "스키마", "구조", "데이터 구조", "필드", "어떤 데이터", "무슨 테이블",
    )
    EXAMPLE_RISK_QUESTIONS = (
        "지난 30일 환불률이 가장 높은 세그먼트는?",
        "최근 문의 증가율이 높아진 구간이 있는가?",
        "이탈률이 경고 수준인 구간과 다음 확인 항목은?",
    )

    def __init__(self) -> None:
        self._pipeline = AnalysisPipeline()

    def analyze(self, question: str, input_type: str = "natural_language") -> dict:
        req = AnalyzeRequest(query=question, input_type=input_type)
        return self._pipeline.run(req)

    def classify_question(self, question: str) -> str:
        text = (question or "").strip().lower()
        if any(keyword in text for keyword in self.SCHEMA_KEYWORDS):
            return "schema_like"
        if any(keyword in text for keyword in self.SUPPORTED_RISK_KEYWORDS):
            return "risk_analysis"
        return "unsupported_other"

    def analyze_question(self, question: str, input_type: str = "natural_language") -> Dict[str, Any]:
        intent = self.classify_question(question)
        if intent == "risk_analysis":
            result = self.analyze(question=question, input_type=input_type)
            return {
                "intent": intent,
                "result": result,
                "summary": self.format_user_summary(result=result, question=question),
                "supported": True,
            }

        return {
            "intent": intent,
            "result": None,
            "summary": self.format_unsupported_summary(question=question, intent=intent),
            "supported": False,
        }

    def format_user_summary(self, result: dict, question: str) -> str:
        decision = str(result.get("decision") or "normal")
        normalized = result.get("normalized_request") or {}
        filters = getattr(normalized, "filters", None) or normalized.get("filters") or {}
        sql_results = result.get("sql_results") or {}
        rows = sql_results.get("rows") or []
        rule_results = result.get("rule_results") or []
        matched = [item for item in rule_results if item.get("matched")]
        metrics = self._extract_metric_parts(rows)
        decision_text = self._decision_korean(decision)
        metric_text = ", ".join(metrics[:3]) if metrics else "핵심 지표 정보가 충분하지 않습니다."
        evidence_items = self._extract_rule_messages(matched)

        conclusion = (
            f"질문 기준 판단은 {decision_text}입니다."
            if decision == "normal"
            else f"질문 기준 판단은 {decision_text}으로, 우선 확인이 필요합니다."
        )

        segment = filters.get("segment") or "all"
        summary_items = [
            f"분석 대상 질문은 '{question[:80]}'이며 현재 세그먼트는 {segment}입니다.",
            f"이번 실행에서 사용한 핵심 지표는 {metric_text}",
            f"결정 상태는 {decision} ({decision_text}) 입니다.",
        ]
        issue_items = evidence_items or ["규칙 임계치를 넘는 항목은 확인되지 않았습니다."]
        action_items = (
            [
                "먼저 기준치를 넘긴 지표를 기간별로 다시 조회하세요.",
                "그 다음 세그먼트별 비교와 원본 테이블 드릴다운을 확인하세요.",
                "재현되는 패턴이 있으면 운영 룰과 임계치를 재점검하세요.",
            ]
            if matched
            else [
                "비교 기간을 넓혀 추세를 다시 확인하세요.",
                "다른 세그먼트나 질문으로 재실행해 기준을 보강하세요.",
            ]
        )

        return self._render_sections(conclusion, summary_items, issue_items, action_items)

    def format_unsupported_summary(self, question: str, intent: str) -> str:
        if intent == "schema_like":
            conclusion = "이 질문은 현재 SQL Analytics 모듈의 지원 범위를 벗어납니다."
            summary_items = [
                "이 모듈은 테이블/컬럼 조회가 아니라 환불·문의·이탈 같은 리스크 분석 전용입니다.",
                f"질문 '{question[:80]}'은 스키마 탐색 성격으로 분류되었습니다.",
            ]
            issue_items = [
                "현재 엔진에는 테이블/컬럼 메타데이터 조회 경로가 없습니다.",
                "스키마 질문을 리스크 분석으로 흘리면 오해를 유발할 수 있습니다.",
            ]
        else:
            conclusion = "이 질문은 현재 SQL Analytics 모듈이 처리하는 분석 범위를 벗어납니다."
            summary_items = [
                "이 모듈은 범용 SQL 어시스턴트가 아니라 리스크 분석 전용입니다.",
                f"질문 '{question[:80]}'은 환불·문의·이탈 분석 질문으로 해석되지 않았습니다.",
            ]
            issue_items = [
                "지원 범위를 벗어난 질문은 정확한 SQL 해석 결과를 보장할 수 없습니다.",
                "현재 엔진은 환불·문의 증가·이탈 지표 중심으로만 동작합니다.",
            ]

        action_items = [
            "질문을 환불률, 문의 증가율, 이탈률, 이상 징후 중심으로 다시 작성하세요.",
            "예: " + self.EXAMPLE_RISK_QUESTIONS[0],
            "예: " + self.EXAMPLE_RISK_QUESTIONS[1],
        ]
        return self._render_sections(conclusion, summary_items, issue_items, action_items)

    def _decision_korean(self, decision: str) -> str:
        return {
            "high_risk": "고위험 상태",
            "warning": "주의 상태",
            "normal": "정상 범위",
        }.get(decision, decision)

    def _extract_metric_parts(self, rows: List[Dict[str, Any]]) -> List[str]:
        metric_parts: list[str] = []
        if not rows:
            return metric_parts
        row = rows[0]
        metric_labels = {
            "refund_rate": "환불률",
            "inquiry_growth": "문의 증가율",
            "churn_rate": "이탈률",
        }
        for key, label in metric_labels.items():
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value, (int, float)):
                metric_parts.append(f"{label} {value * 100:.1f}%")
            else:
                metric_parts.append(f"{label} {value}")
        return metric_parts

    def _extract_rule_messages(self, matched: List[Dict[str, Any]]) -> List[str]:
        items: List[str] = []
        for item in matched[:3]:
            message = str(item.get("message") or "").strip()
            if message:
                items.append(message)
        return items

    def _render_sections(
        self,
        conclusion: str,
        summary_items: List[str],
        issue_items: List[str],
        action_items: List[str],
    ) -> str:
        def section(title: str, items: List[str]) -> str:
            lines = [title]
            lines.extend(f"- {item}" for item in items if item)
            return "\n".join(lines)

        return "\n\n".join(
            [
                section("한 줄 결론", [conclusion]),
                section("핵심 요약", summary_items[:3]),
                section("주요 쟁점", issue_items[:3]),
                section("다음 액션", action_items[:3]),
            ]
        )
