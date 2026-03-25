"""
Log Analyzer Service - experience_ledger 기반 인사이트 분석

experience_ledger 최근 N건을 로드하여 에러 패턴·도구 실패·지연 이슈를 그룹화하고,
behavior_insights 테이블에 구조화된 인사이트로 저장합니다.
스케줄러에서 주기적(예: 6시간) 호출되며, 비동기·비간섭으로 동작합니다.

✅ verified: experience_ledger → 그룹화 → behavior_insights 저장
"""

import json
import logging
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from mellow_link.infra.memory_database import (
    MemoryDatabase,
    ExperienceRecord,
    BehaviorInsight,
    get_memory_db,
)

logger = logging.getLogger(__name__)

# 스케줄 기본값: 최근 200건 분석
DEFAULT_LEDGER_LIMIT = 200


def _normalize_error_key(msg: Optional[str]) -> str:
    """에러 메시지 그룹핑용 키 (첫 줄 또는 앞 120자)."""
    if not msg or not str(msg).strip():
        return "(no message)"
    s = str(msg).strip()
    first_line = s.split("\n")[0].strip() if "\n" in s else s
    return (first_line[:120] + "…") if len(first_line) > 120 else first_line


def _parse_used_tools(record: ExperienceRecord) -> List[str]:
    """ExperienceRecord에서 used_tools 리스트 추출 (JSON 또는 빈 리스트)."""
    raw = getattr(record, "used_tools", None) or ""
    if not raw:
        return []
    try:
        if isinstance(raw, list):
            return list(raw)
        out = json.loads(raw)
        return list(out) if isinstance(out, list) else []
    except Exception:
        return []


async def run_ledger_insight_analysis(
    limit: int = DEFAULT_LEDGER_LIMIT,
    db: Optional[MemoryDatabase] = None,
) -> List[BehaviorInsight]:
    """
    ✅ verified: 경험 장부 인사이트 분석 (비동기, 메인 채팅 비간섭).

    - experience_ledger에서 최근 N건 로드
    - 동일 error_message / 실패한 used_tools 패턴 그룹화
    - 요약을 behavior_insights에 구조화된 인사이트로 저장 (Tower LLM 활용용)

    Returns:
        저장된 인사이트 리스트
    """
    db = db or get_memory_db()
    insights: List[BehaviorInsight] = []

    try:
        entries = db.get_recent_ledger_entries(limit=limit)
        if not entries:
            logger.debug("[LogAnalyzer] No ledger entries to analyze")
            return insights

        # 실패 건만 필터
        failed = [e for e in entries if e.is_success == 0]
        success_count = len(entries) - len(failed)

        # 1) error_message 패턴 그룹화
        error_groups: Counter = Counter()
        for e in failed:
            key = _normalize_error_key(getattr(e, "error_message", None) or e.final_outcome)
            error_groups[key] += 1

        for err_key, count in error_groups.most_common(10):
            if count < 2 and not err_key.strip():
                continue
            finding = f"에러 패턴 ({count}건): {err_key[:200]}"
            recommendation = json.dumps(
                {
                    "pattern_type": "error_message",
                    "sample": err_key[:500],
                    "count": count,
                    "total_failed": len(failed),
                    "ts": datetime.now().isoformat(),
                },
                ensure_ascii=False,
            )
            insight = BehaviorInsight(
                id=str(uuid.uuid4()),
                pattern_type="failure_pattern",
                finding=finding,
                recommendation=recommendation,
                confidence=min(0.5 + count * 0.05, 0.95),
                is_applied=0,
                is_verified_by_guardian=0,
                created_at=datetime.now(),
            )
            if db.save_insight(insight):
                insights.append(insight)

        # 2) 실패한 used_tools 패턴 (실패 건에서 사용된 도구 빈도)
        tool_fail_counts: Counter = Counter()
        for e in failed:
            for t in _parse_used_tools(e):
                tool_fail_counts[t] += 1

        for tool_name, fail_count in tool_fail_counts.most_common(10):
            finding = f"도구 '{tool_name}' 실패 {fail_count}건 (최근 실패 케이스)"
            recommendation = json.dumps(
                {
                    "pattern_type": "tool_failure",
                    "tool_name": tool_name,
                    "fail_count": fail_count,
                    "total_failed": len(failed),
                    "ts": datetime.now().isoformat(),
                },
                ensure_ascii=False,
            )
            insight = BehaviorInsight(
                id=str(uuid.uuid4()),
                pattern_type="tool_performance",
                finding=finding,
                recommendation=recommendation,
                confidence=min(0.5 + fail_count * 0.03, 0.9),
                is_applied=0,
                is_verified_by_guardian=0,
                created_at=datetime.now(),
            )
            if db.save_insight(insight):
                insights.append(insight)

        # 3) 지연(Latency) 요약 인사이트 (선택)
        latencies = [
            getattr(e, "latency_ms", None) or 0.0
            for e in entries
            if getattr(e, "latency_ms", None) is not None
        ]
        if latencies:
            latencies.sort(reverse=True)
            p95 = latencies[int(len(latencies) * 0.05)] if len(latencies) > 1 else latencies[0]
            finding = f"최근 {len(entries)}건 중 응답 지연 P95: {p95:.0f}ms"
            recommendation = json.dumps(
                {
                    "pattern_type": "latency",
                    "p95_ms": round(p95, 2),
                    "sample_size": len(latencies),
                    "success_count": success_count,
                    "failed_count": len(failed),
                    "ts": datetime.now().isoformat(),
                },
                ensure_ascii=False,
            )
            insight = BehaviorInsight(
                id=str(uuid.uuid4()),
                pattern_type="tool_performance",
                finding=finding,
                recommendation=recommendation,
                confidence=0.6,
                is_applied=0,
                is_verified_by_guardian=0,
                created_at=datetime.now(),
            )
            if db.save_insight(insight):
                insights.append(insight)

        logger.info("[LogAnalyzer] Ledger insight analysis saved: %d insights", len(insights))
        return insights

    except Exception as e:
        logger.warning("[LogAnalyzer] Ledger insight analysis failed: %s", e)
        return insights
