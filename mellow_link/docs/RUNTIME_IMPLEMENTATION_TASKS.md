# Chat Runtime API — 구현 작업 항목

체크리스트(E)를 **프론트/백 분리**한 구현 작업 항목으로 변환.  
(설계 문서 변경 없음, 구현 방향만 정리)

---

## 계약 고정 문장

- Runtime V1 currently guarantees a stable contract, state/status semantics, core regression coverage, minimal operational observability, and an isolated post-response experience ledger bridge.
- The bridge is intentionally limited to experience ledger recording only and does not affect degraded, last_error, HTTP status, or response body on failure.
- Archiver, insight generation, and diagnosis chaining remain explicitly out of scope.
- state_version은 같은 세션의 성공 turn 완료 시 1씩 증가한다.
- model_tier_effective는 요청 tier와 분리된 실제 적용 tier다.
- `/runtime/status`는 운영용 최소 상태 노출 API다.
- 모든 필수 키는 nullable이어도 항상 포함한다.
- Runtime 최소 운영 관측은 요청/응답 추적(trace_id), 상태 변화(degraded), 마지막 오류(last_error), 구현체(runtime_impl) 네 축을 기본으로 한다.
- Runtime ledger bridge는 성공 turn 이후에만 실행되는 후행 부수효과로 취급한다. 실패는 degraded, last_error, HTTP status, 응답 바디에 영향을 주지 않는다.
- 1차 Runtime ledger bridge의 `intent_type`은 사용자 입력 축약본을 임시 매핑한 값이며, 최종 의미 모델은 추후 별도 정리한다.

## CI 기본 세트

- Runtime 기본 회귀 세트는 `runtime-core`로 고정한다.
- 포함 대상:
  - `mellow_chat_runtime/tests/test_runtime_api_contract.py`
  - `mellow_chat_runtime/tests/test_runtime_state_and_status.py`
- 기본 실행 명령:
  - `python -m pytest -q mellow_chat_runtime\tests\test_runtime_api_contract.py mellow_chat_runtime\tests\test_runtime_state_and_status.py`
- 실행 정책:
  - `pull_request`에서 항상 실행
  - `main` 브랜치 `push`에서 항상 실행
  - 실패 시 merge 방어선으로 취급

---

## 백엔드

| # | 작업 | 설명 |
|---|------|------|
| B1 | 앱은 **오직** POST /runtime/turn, GET /runtime/status만 노출 | 기존 채팅/엔진 전용 엔드포인트는 Runtime API와 분리 유지 |
| B2 | UI용 응답 필드만 반환 | turn.speech, turn.passage, turn.ooc, turn.clarify만 사용. 엔진 내부 개념(FSM/GM/Tool/Adapter) 미노출 |
| B3 | client_turn_id 수신·저장 | 요청 context.client_turn_id 수신 후 로그/메트릭에만 사용 (응답에는 meta.trace_id 반환) |
| B4 | trace_id 생성·전파 | Runtime ingress(요청 진입점)에서 생성, adapter·engine 호출 시 전파, 에러 시 error.trace_id 포함 |
| B5 | state_version 관리 | 세션 단위 원자적 증분, persistence 레이어에서 커밋. 409 CONFLICT 시 state_version 충돌 처리 |
| B6 | model_tier_requested → model_tier_effective | 요청은 받되, 권한/쿼터에 따라 effective 결정. 응답 state.model_tier_effective 항상 명시 |
| B7 | 에러 응답 공통화 | error.code, error.message, trace_id를 모든 4xx/5xx 응답 바디에 동일 구조로 반환 |
| B8 | GET /runtime/status 구현 | system_state, last_error, degraded, p95_latency_ms 등 운영/디버그 필드 반환 |
| B9 | Work Adapter 라우팅 금지 | SQL/파일/자동코딩 등 Work Adapter 라우팅을 Runtime API 레이어에서 **하지 않음** (앱도 미구현) |
| B10 | runtime_impl 스위치 | 동일 앱 빌드로 runtime_impl=engine-backed / llm-only 전환 가능하도록 설정 또는 라우팅 분기 |

---

## 프론트(앱)

| # | 작업 | 설명 |
|---|------|------|
| F1 | API 호출 제한 | POST /runtime/turn, GET /runtime/status만 호출. 엔진 전용/레거시 채팅 URL 직접 호출 금지 |
| F2 | UI 렌더링 데이터 소스 | turn.speech, turn.passage, turn.ooc, turn.clarify만 사용. 그 외 필드는 디버그/메타용 |
| F3 | 엔진 내부 개념 미참조 | FSM/GM/Tool/Adapter 등 엔진 용어를 UI 코드/문자열에 사용하지 않음 |
| F4 | client_turn_id 전송 | 매 요청 context.client_turn_id 설정. 응답 meta.trace_id 로그 저장 |
| F5 | state_version 보관 | 응답 state.state_version 저장 후, 중복 전송/순서 꼬임 디버깅에만 사용 |
| F6 | model_tier 표시 | model_tier_requested는 전송, **표시는 state.model_tier_effective 기준** |
| F7 | 에러 UI 공통화 | error.code, error.message, trace_id를 사용자/운영 화면에 분리 표시 |
| F8 | 운영자 화면 | system_state, last_error, degraded, p95_latency_ms 우선 노출 (GET /runtime/status 기반) |
| F9 | Work Adapter 미구현 | SQL/파일/자동코딩 라우팅 기능을 앱에서 **구현하지 않음** |
| F10 | 코어 교체 검증 | 동일 앱 빌드로 runtime_impl=engine-backed와 llm-only 스위치 후 동작 검증 |

---

## 제약 요약

- **앱**: Work Adapter(SQL/파일/자동코딩) 라우팅 절대 금지.
- **Runtime API**: 엔진 내부(FSM/GM/Tools) 노출 금지. Engine 내부 ID는 meta.engine_ref optional만 노출.
