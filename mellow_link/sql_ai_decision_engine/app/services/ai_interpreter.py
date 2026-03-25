from __future__ import annotations


def interpret(sql_results: dict, rule_results: list[dict], decision: str) -> str:
    matched = [result for result in rule_results if result.get("matched")]
    if not matched:
        return "규칙 임계치를 넘는 이상 징후는 제한적으로 관측되었습니다. 주요 지표 추세를 추가 관찰하세요."

    messages = ", ".join(item["message"] for item in matched)
    return (
        f"SQL 조회 결과와 규칙 평가 기준으로 현재 상태는 {decision}입니다. "
        f"주요 근거: {messages}. 원인 후보는 정책 변경, 품질 이슈, 응대 지연이며 세그먼트별 재검증이 필요합니다."
    )
