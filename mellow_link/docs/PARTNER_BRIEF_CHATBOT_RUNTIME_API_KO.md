# 외부 파트너 공유 문서 (사이드 프로젝트: 챗봇 개발)

기준일: 2026-03-05  
문서 목적: 파트너가 챗봇을 연동할 때 필요한 공개 계약만 전달

---

## 1. 프로젝트 개요

본 프로젝트는 **챗봇 UI/클라이언트**와 **AI 엔진**을 분리한 구조입니다.  
파트너 측 챗봇은 아래 Runtime API만 연동하면 됩니다.

- `POST /runtime/turn`
- `GET /runtime/status`

내부 판단/오케스트레이션 로직은 공개 범위가 아닙니다.

---

## 2. 연동 원칙

1. 챗봇은 Runtime API 2개만 호출합니다.
2. 응답 렌더링은 `speech`, `passage`, `ooc`, `clarify` 기준으로 처리합니다.
3. 오류 화면에는 `error.message`와 `trace_id`를 함께 표시합니다.
4. 모델/엔진 구현 변경이 있어도 API 계약은 유지됩니다.

---

## 3. API 요약

## `POST /runtime/turn`
- 용도: 사용자 1턴 입력 처리
- 요청 필수:
  - `session_id`
  - `user.id`
  - `input.text`
- 요청 선택:
  - `context.character_id`
  - `context.model_tier_requested` (`free | pro | auto`)
  - `context.client_turn_id`

- 성공 응답 핵심:
  - `turn.speech`: 기본 답변 텍스트
  - `turn.passage`: 서술형/장문 블록
  - `turn.ooc`: 메타 안내(선택)
  - `turn.clarify`: 되묻기(확신 낮을 때)
  - `state.model_tier_effective`: 실제 적용 tier
  - `meta.trace_id`: 추적 ID

## `GET /runtime/status`
- 용도: 운영 상태 확인
- 응답 핵심:
  - `runtime.impl` (`engine-backed` 또는 `llm-only`)
  - `health.system_state`
  - `health.last_error`
  - `counters.p95_latency_ms`

---

## 4. 요청/응답 예시

```json
POST /runtime/turn
{
  "session_id": "sess_demo_001",
  "user": { "id": "u_demo" },
  "input": { "text": "오늘 컨디션 어때?" },
  "context": {
    "character_id": "char_mellow",
    "model_tier_requested": "auto",
    "client_turn_id": "web_0001"
  }
}
```

```json
200 OK
{
  "turn": {
    "id": "turn_ab12cd34",
    "speech": "좋아, 오늘은 꽤 안정적이야.",
    "passage": "캐릭터가 가볍게 미소를 지으며 고개를 끄덕였다.",
    "ooc": null,
    "actions": null,
    "clarify": null
  },
  "state": {
    "session_id": "sess_demo_001",
    "state_version": 12,
    "system_state": "IDLE",
    "model_tier_effective": "free"
  },
  "meta": {
    "trace_id": "trc_20260305_1234abcd",
    "runtime_impl": "engine-backed",
    "latency_ms": 842.1,
    "created_at": "2026-03-05T02:10:30Z",
    "engine_ref": "optional_ref"
  }
}
```

---

## 5. 에러 처리 가이드

- 공통 에러 바디:
  - `error.code`
  - `error.message`
  - `error.trace_id`
- 파트너 UI 권장:
  - 사용자: `error.message` 표시
  - 운영 로그: `trace_id` 저장

---

## 6. 역할 분담 (경계)

- 파트너(챗봇 개발):
  - 메시지 입력/출력 UI
  - 세션 관리, 재시도, 오류 표시
  - `trace_id` 로깅

- 엔진 제공측:
  - Runtime API 운영
  - 모델/판단엔진/정책 관리
  - 안정성/성능/모니터링

---

## 7. 캐릭터 AI 정책

캐릭터 응답 생성 백엔드는 운영 상황에 따라 교체될 수 있습니다.
- 로컬 경량 모델
- 상용 LLM

단, 파트너가 사용하는 Runtime API 계약은 동일하게 유지됩니다.

---

## 8. 초기 연동 체크리스트

1. `POST /runtime/turn` 호출 및 기본 답변 렌더링 확인
2. `clarify` 케이스 UI 처리 확인
3. `GET /runtime/status`를 운영 화면에 연결
4. 실패 응답에서 `trace_id` 수집 확인
5. `model_tier_effective` 기준 표시 로직 확인

