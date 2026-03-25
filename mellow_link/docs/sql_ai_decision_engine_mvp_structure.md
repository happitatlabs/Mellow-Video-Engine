# SQL-AI Decision Engine MVP 구조도

- 문서 버전: v0.1
- 작성일: 2026-03-12
- 대상: MVP 설계 초안
- 프로젝트명: SQL-AI Decision Engine

---

## 1. 문서 목적

본 문서는 **SQL-AI Decision Engine**의 MVP 범위를 정의하고, 설계/개발 단계에서 필요한 핵심 구성요소를 구조적으로 정리하기 위한 초안이다.

이 엔진의 목표는 다음과 같다.

1. 사용자가 입력한 **상황**을 분석 가능한 형태로 정리한다.
2. 미리 정의된 **SQL 조회 규칙**으로 정량 데이터를 확보한다.
3. **규칙 엔진**으로 1차 판정을 수행한다.
4. **AI 해석 레이어**가 규칙 결과를 설명 가능한 문장으로 정리한다.
5. 최종적으로 **판정 + 근거 + 해석 + 한계**를 함께 제공한다.

---

## 2. 한 줄 정의

> SQL-AI Decision Engine은 **SQL 기반 규칙 판정**과 **AI 기반 맥락 해석**을 결합하여, 상황 입력에 대해 설명 가능한 분석 결과를 제공하는 하이브리드 의사결정 엔진이다.

---

## 3. MVP 범위

### 3.1 포함 범위

- 자연어 또는 폼 형태의 상황 입력
- 상황을 분석용 파라미터로 정규화
- 사전 정의된 SQL 템플릿 실행
- 기본 규칙 점수화 및 위험도 판정
- AI를 통한 결과 요약 및 원인 해석
- 결과 리포트(JSON/화면용 텍스트) 출력

### 3.2 제외 범위

- 자유로운 SQL 자동 생성 전면 허용
- 자가 학습형 규칙 수정
- 실시간 스트리밍 대시보드
- 복잡한 워크플로우 엔진
- 다중 DB 벤더별 최적화
- 완전 자동 의사결정 실행(예: 실제 승인/차단 자동 반영)

---

## 4. 핵심 설계 원칙

### 4.1 SQL은 사실 계층, AI는 해석 계층

- SQL은 수치와 조건을 조회하는 역할을 담당한다.
- AI는 SQL 결과와 규칙 결과를 바탕으로 설명과 해석을 제공한다.
- AI가 원시 데이터를 임의로 만들어내지 않도록 한다.

### 4.2 규칙 우선, AI 보조

- 1차 판정은 규칙 엔진이 수행한다.
- AI는 판정을 뒤집는 존재가 아니라, **보완 설명**과 **예외 해석**을 제공하는 계층으로 제한한다.

### 4.3 설명 가능성 확보

최종 출력에는 반드시 아래 요소가 포함되어야 한다.

- 판정 결과
- 근거 데이터
- 적용 규칙
- AI 해석
- 한계 또는 추가 확인 필요 사항

### 4.4 안전한 SQL 실행

- MVP에서는 자유 SQL 생성보다 **사전 정의된 SQL 템플릿** 사용을 우선한다.
- 사용자 입력은 파라미터로만 주입하고 문자열 직접 결합을 금지한다.

---

## 5. 전체 구조도

```text
[사용자 입력]
    ↓
[입력 정규화]
    ↓
[상황 해석 / 파라미터 추출]
    ↓
[SQL 템플릿 선택]
    ↓
[SQL 실행기]
    ↓
[규칙 엔진]
    ↓
[AI 해석 레이어]
    ↓
[결과 리포트 생성기]
    ↓
[화면 / API 응답 / 저장]
```

---

## 6. 레이어별 구성

## 6.1 Input Layer

### 역할
사용자가 입력한 상황을 수집한다.

### 입력 형태

- 자연어 텍스트
- 폼 입력
- 파일 업로드(향후 확장)

### 예시

```text
최근 3개월간 고객 이탈이 늘어난 이유를 분석해줘.
환불 증가와 문의 증가가 같이 있는지도 보고 싶어.
```

### 산출물

```json
{
  "raw_input": "최근 3개월간 고객 이탈이 늘어난 이유를 분석해줘. 환불 증가와 문의 증가가 같이 있는지도 보고 싶어."
}
```

---

## 6.2 Normalization Layer

### 역할
입력 문장을 구조화된 분석 파라미터로 변환한다.

### 추출 항목 예시

- 분석 대상
- 기간
- 비교 기준
- 핵심 지표
- 필터 조건

### 예시 산출물

```json
{
  "topic": "고객 이탈",
  "period": "최근 3개월",
  "signals": ["환불 증가", "문의 증가"],
  "comparison": "직전 3개월 대비",
  "target_domain": "customer"
}
```

### MVP 방식

- 1차: 키워드 기반 매핑
- 2차: 필요 시 AI 보조 정규화

---

## 6.3 Query Mapping Layer

### 역할
정규화된 입력을 바탕으로 어떤 SQL 템플릿을 실행할지 결정한다.

### 방식

- 토픽별 SQL 템플릿 사전 정의
- 시그널별 보조 쿼리 선택
- 템플릿 ID 기반 조합

### 예시

```json
{
  "query_set": [
    "Q_CHURN_RATE_01",
    "Q_REFUND_RATE_02",
    "Q_INQUIRY_TREND_03"
  ]
}
```

### 템플릿 예시

```sql
-- Q_CHURN_RATE_01
SELECT month, churn_rate
FROM customer_churn_summary
WHERE month BETWEEN :start_month AND :end_month
ORDER BY month;
```

---

## 6.4 SQL Execution Layer

### 역할
선택된 SQL 템플릿을 안전하게 실행하고 결과를 수집한다.

### 요구사항

- 파라미터 바인딩 필수
- 템플릿 기반 실행
- 실행 로그 기록
- 실패 시 에러 메시지 구조화

### 산출물 예시

```json
{
  "queries": [
    {
      "query_id": "Q_CHURN_RATE_01",
      "status": "success",
      "rows": [
        {"month": "2025-12", "churn_rate": 0.071},
        {"month": "2026-01", "churn_rate": 0.086},
        {"month": "2026-02", "churn_rate": 0.098}
      ]
    }
  ]
}
```

---

## 6.5 Rule Engine Layer

### 역할
SQL 결과를 기준으로 1차 판정과 점수화를 수행한다.

### 규칙 방식

- IF / THEN 규칙
- 점수 누적 방식
- 임계치 기반 등급화

### 예시 규칙

```text
R01. churn_rate > 0.08 이면 risk_score += 30
R02. refund_rate > 0.05 이면 risk_score += 20
R03. inquiry_growth > 0.15 이면 risk_score += 10
R04. 세 규칙 중 2개 이상 충족 시 status = "주의"
R05. 총점 50 이상이면 status = "고위험"
```

### 산출물 예시

```json
{
  "rule_results": {
    "risk_score": 60,
    "status": "고위험",
    "matched_rules": ["R01", "R02", "R03"],
    "metrics": {
      "churn_rate": 0.098,
      "refund_rate": 0.072,
      "inquiry_growth": 0.21
    }
  }
}
```

---

## 6.6 AI Interpretation Layer

### 역할
규칙 결과와 SQL 결과를 바탕으로 사람이 이해할 수 있는 설명을 생성한다.

### 입력 원칙

AI에는 반드시 다음만 전달한다.

- 정규화된 상황
- SQL 결과 요약
- 규칙 엔진 결과
- 제한된 추가 컨텍스트

### AI가 해야 하는 일

- 결과 요약
- 원인 후보 설명
- 예외 가능성 제시
- 추가 확인 포인트 제안

### AI가 하면 안 되는 일

- 실제 수치 변경
- 규칙 결과 임의 뒤집기
- 조회되지 않은 사실 단정

### 예시 입력

```json
{
  "topic": "고객 이탈",
  "rule_results": {
    "risk_score": 60,
    "status": "고위험",
    "matched_rules": ["R01", "R02", "R03"]
  },
  "metrics": {
    "churn_rate": 0.098,
    "refund_rate": 0.072,
    "inquiry_growth": 0.21
  }
}
```

### 예시 출력

```text
최근 3개월 동안 고객 이탈률이 상승했고, 환불률과 문의량도 함께 증가했습니다.
규칙 엔진 기준으로 고위험 상태로 분류됩니다.
이 패턴은 가격 정책 변화, 품질 이슈, 고객 응대 지연 중 하나 이상과 관련될 가능성이 높습니다.
다만 특정 상품군 또는 특정 고객군에 집중된 현상인지 추가 확인이 필요합니다.
```

---

## 6.7 Report Layer

### 역할
최종 결과를 API 응답 또는 화면 표시용 구조로 정리한다.

### 출력 항목

- 요청 요약
- 판정 결과
- 핵심 지표
- 적용 규칙
- AI 해석
- 한계/추가 확인 필요 사항

### 예시 출력 구조

```json
{
  "request_summary": "최근 3개월 고객 이탈 원인 분석",
  "decision": {
    "status": "고위험",
    "risk_score": 60
  },
  "evidence": {
    "churn_rate": 0.098,
    "refund_rate": 0.072,
    "inquiry_growth": 0.21
  },
  "matched_rules": ["R01", "R02", "R03"],
  "ai_summary": "최근 3개월 동안 고객 이탈률이 상승했고...",
  "limitations": [
    "상품군 단위 세부 분석은 아직 포함되지 않음",
    "외부 이벤트(정책/시장 변수)는 현재 반영되지 않음"
  ]
}
```

---

## 7. MVP 데이터 흐름 예시

```text
사용자 입력:
"최근 환불 증가와 고객 이탈의 관계를 보고 싶다"

1) 정규화:
- topic = 고객 이탈
- signals = 환불 증가
- period = 최근 3개월

2) SQL 선택:
- Q_CHURN_RATE_01
- Q_REFUND_RATE_02
- Q_CUSTOMER_SEGMENT_01

3) SQL 실행:
- 이탈률 데이터 조회
- 환불률 데이터 조회
- 고객군별 비중 조회

4) 규칙 판정:
- R01 충족
- R02 충족
- 총점 50
- 상태 = 고위험

5) AI 해석:
- 환불 증가와 이탈 상승이 동시에 나타남
- 서비스/품질 이슈 가능성 제시

6) 리포트 생성:
- 판정 + 근거 + 규칙 + 해석 + 한계 반환
```

---

## 8. 권장 모듈 구조

```text
sql_ai_decision_engine/
├─ app/
│  ├─ input/
│  │  └─ normalizer.py
│  ├─ mapping/
│  │  └─ query_mapper.py
│  ├─ sql/
│  │  ├─ executor.py
│  │  └─ templates/
│  │     ├─ customer_churn.sql
│  │     ├─ refund_rate.sql
│  │     └─ inquiry_trend.sql
│  ├─ rules/
│  │  ├─ rule_engine.py
│  │  └─ rule_definitions.yaml
│  ├─ ai/
│  │  └─ interpreter.py
│  ├─ report/
│  │  └─ report_builder.py
│  └─ api/
│     └─ main.py
└─ tests/
   ├─ test_normalizer.py
   ├─ test_query_mapper.py
   ├─ test_rule_engine.py
   └─ test_report_builder.py
```

---

## 9. MVP API 초안

### 9.1 요청

```http
POST /analyze
Content-Type: application/json
```

```json
{
  "situation": "최근 3개월 고객 이탈 증가 원인을 분석해줘"
}
```

### 9.2 응답

```json
{
  "status": "success",
  "result": {
    "request_summary": "최근 3개월 고객 이탈 증가 원인 분석",
    "decision": {
      "status": "고위험",
      "risk_score": 60
    },
    "matched_rules": ["R01", "R02", "R03"],
    "evidence": {
      "churn_rate": 0.098,
      "refund_rate": 0.072,
      "inquiry_growth": 0.21
    },
    "ai_summary": "환불률과 문의량이 동시에 증가해...",
    "limitations": [
      "세부 상품군 분석 미포함"
    ]
  }
}
```

---

## 10. MVP 개발 우선순위

### Phase 1. 최소 동작

1. 상황 입력 받기
2. 정규화(키워드 기반)
3. SQL 템플릿 3개 실행
4. 규칙 엔진 점수화
5. AI 요약 생성
6. 결과 JSON 반환

### Phase 2. 안정화

1. 규칙 파일 외부화
2. SQL 템플릿 관리 체계 도입
3. 에러 처리 강화
4. 테스트 코드 보강

### Phase 3. 확장

1. 파일 입력 파싱
2. 다중 도메인 지원
3. 설명서/관리 UI
4. 리포트 다운로드

---

## 11. 리스크 및 주의사항

### 11.1 SQL 생성의 자유도

MVP 단계에서는 AI에게 SQL 전체를 자유 생성하게 하면 위험하다.
따라서 아래 원칙을 권장한다.

- AI는 SQL을 직접 생성하지 않는다.
- SQL 템플릿 선택 또는 파라미터 보조만 수행한다.

### 11.2 해석의 과장

AI는 아래 범위 내에서만 해석해야 한다.

- 가능성 제시
- 추가 확인 포인트 제안
- 규칙 결과 설명

단정적 표현은 지양한다.

### 11.3 도메인 종속성

규칙과 SQL은 도메인별로 달라진다.
따라서 MVP는 한 도메인(예: 고객 이탈, 매출 이상 탐지, 환불 분석)만 먼저 고정하는 것이 바람직하다.

---

## 12. 최종 정리

SQL-AI Decision Engine의 MVP는 아래 철학으로 개발한다.

```text
1. 사용자가 상황을 입력한다.
2. 시스템이 이를 구조화한다.
3. SQL이 사실을 조회한다.
4. 규칙 엔진이 1차 판정을 수행한다.
5. AI가 결과를 설명한다.
6. 최종적으로 설명 가능한 리포트를 제공한다.
```

즉, 이 MVP의 핵심은 단순한 "AI 분석기"가 아니라,
**검증 가능한 SQL 기반 판단 위에 AI 해석을 얹는 하이브리드 의사결정 구조**를 먼저 세우는 데 있다.

---

## 13. 다음 작업 제안

다음 단계 문서로 이어질 수 있는 항목은 아래와 같다.

1. SQL 템플릿 정의서
2. 규칙 정의서(rule_definitions)
3. API 명세서
4. 입력 정규화 매핑표
5. 출력 리포트 포맷 정의서

