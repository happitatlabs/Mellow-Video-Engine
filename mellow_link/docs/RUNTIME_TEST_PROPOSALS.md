# Chat Runtime API — 최소 테스트 5개 제안

구현 검증용 최소 테스트 제안. (실제 테스트 코드는 별도 test_*.py로 작성)

---

## 1. Required fields validation

- **대상**: POST /runtime/turn
- **내용**: session_id, user.id, input.text 누락 시 400 BAD_REQUEST.
- **방법**: (session_id 없음), (user 없음), (user.id 없음), (input 없음), (input.text 없음) 각각 요청 후 status_code 400, body.error.code == "BAD_REQUEST" 검증.
- **선택**: input.text 빈 문자열, user.id 빈 문자열 등 형식 오류도 400으로 통일할지 스펙에 명시 후 테스트.

---

## 2. trace_id 생성/전파

- **대상**: POST /runtime/turn (성공/실패 모두)
- **내용**: 응답 meta.trace_id 존재, 형식(예: trc_YYYYMMDD_*) 일치. 에러 응답 시 body.error.trace_id 동일 값.
- **방법**: turn 200 응답에서 meta.trace_id 존재 및 패턴 검증. 503 등 에러 시 detail.error.trace_id == meta.trace_id(또는 요청 시 전파한 trace_id) 검증.
- **선택**: adapter 내부 로그/스팬에 trace_id가 전달되는지 단위 테스트로 검증.

---

## 3. model_tier_requested → model_tier_effective 정책

- **대상**: POST /runtime/turn
- **내용**: context.model_tier_requested (free|pro|auto)에 따라 state.model_tier_effective가 free 또는 pro로 결정. 권한 미충족 시 pro 요청도 effective=free 등으로 다운그레이드.
- **방법**: (1) model_tier_requested 생략 시 model_tier_effective == "free". (2) pro 요청 시 권한 있으면 "pro", 없으면 "free". (3) 응답 state.model_tier_effective 항상 존재.
- **선택**: auto 처리(자동 선택) 시 effective가 free/pro 중 하나인지 검증.

---

## 4. clarify 발생 조건 (confidence < threshold)

- **대상**: LLM-only Runtime (또는 clarify를 지원하는 구현)
- **내용**: intent/confidence 파싱 후 confidence < threshold(예: 0.65)이면 turn.clarify가 채워지고, speech는 빈 문자열 또는 안내 문구. clarify.reason 예: intent_confidence_low.
- **방법**: confidence를 낮추는 입력(모호한 문장 등)으로 turn 호출 → 응답에 turn.clarify != null, (선택) turn.speech 빈 문자열 또는 고정 문구. threshold 이상 입력 시 turn.clarify == null.
- **선택**: Engine-backed에서 clarify 미구현이면 이 테스트는 llm_only 어댑터에만 적용.

---

## 5. GET /runtime/status 값 반환

- **대상**: GET /runtime/status
- **내용**: 200 응답에 runtime.impl, runtime.version, runtime.uptime_sec, health.system_state, health.last_error, health.degraded, (선택) counters.turns_1m, counters.p95_latency_ms, time 존재.
- **방법**: GET /runtime/status 호출 후 JSON 구조 및 타입 검증. system_state는 enum (IDLE|TEXT|IMAGE|ERROR). engine-backed일 때 orchestrator FSM 상태와 일치하는지 검증.
- **선택**: 런타임 기동 직후 uptime_sec >= 0, turn 1회 이상 후 turns_1m >= 1 등.

---

## 테스트 파일 제안 위치

- `mellow_link/tests/test_runtime_api.py` (또는 `test_runtime_turn.py`, `test_runtime_status.py` 분리)
- 필수: 1(validation), 2(trace_id), 5(status).  
- 정책/구현 연동: 3(model_tier), 4(clarify)는 해당 구현체가 있을 때 포함.
