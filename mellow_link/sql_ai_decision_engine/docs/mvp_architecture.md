# SQL-AI Decision Engine MVP Architecture

## 목표
- SQL + Rule Engine + AI Interpretation 구조의 최소 실행 MVP 제공
- 입력 타입 4종(자연어, 지표, 테이블, 로그)을 수용 가능한 아키텍처 확보
- Phase 1은 자연어/지표 입력 우선 구현, 테이블/로그는 인터페이스만 준비

## 핵심 원칙
1. SQL은 사실 계층이다.
2. Rule Engine은 수치 비교만 수행한다.
3. AI는 해석만 수행하고 판정을 뒤집지 않는다.
4. 출력은 판정 근거가 추적 가능해야 한다.

## 파이프라인
입력 수집 -> 입력 타입 판별 -> 입력 핸들러 -> 공통 정규화 스키마 -> SQL 템플릿 선택/실행 -> Rule 평가 -> AI 해석 -> 리포트 반환

## 입력 확장 전략
- `natural_language_handler`, `metric_handler`는 구현 완료
- `table_handler`, `log_handler`는 Phase 2용 인터페이스(`NotImplementedError`)로 준비

## 정규화 스키마
```json
{
  "analysis_type": "root_cause",
  "target": "customer_refund",
  "period": {
    "start": "2026-03-01",
    "end": "2026-03-31"
  },
  "metrics": ["refund_rate", "inquiry_growth"],
  "filters": {},
  "signals": ["환불 증가", "문의 증가"],
  "source_types": ["natural_language", "metric"]
}
```

## API
- `POST /analyze`
  - 구현: `natural_language`, `metric`
  - 준비: `table`, `log`는 `501 Not Implemented`

## 실행
```bash
cd D:/AI_Project/mellow_link/sql_ai_decision_engine
uvicorn app.main:app --reload
```

## 테스트
```bash
pytest -q
```
