# Runtime UI Migration Map

## 화면별 현황과 대체 매핑

| 현재 화면 | 현재 직접 호출 | 내부 개념 의존 | Runtime 대체 | 비고 |
|---|---|---|---|---|
| `static/index.html` + `static/js/chat.js` | `/chat/ask`, `/chat/sessions/*`, `/chat/research/execute`, `/runs/*/events` | `run_meta`, `plan_created`, `todo_*`, `tool_*`, versioning, patch/evolution payload | `POST /runtime/turn`, `GET /runtime/status` | 1차 전환 핵심 대상 |
| `static/user_console.html` | `/runs/{run_id}`, `/runs/{run_id}/events`, `/runs` | run 상태/이벤트 모델 | 별도 유지 또는 Runtime용 사용자 콘솔로 축소 | run 콘솔과 chat runtime은 역할 분리 |
| `static/operator_console.html` | `/api/dev/runs/*`, `/runs/{run_id}/control` | run 제어/운영 이벤트 | `GET /runtime/status` 기반 최소 운영 화면 | Runtime 운영 화면은 제어보다 상태 관측 우선 |
| `static/dev_console.html` | `/api/dev/runs/*`, raw/events | raw trace, prompt/response, tool usage | Runtime 범위 밖 | dev 전용으로 유지 |
| 모듈 시작 화면들 | `/modules/*/runs`, `/chat/upload-temp` | run 생성 payload | 별도 유지 | module UX와 runtime chat는 별도 트랙 |

## 사용자 UI 계약 전환 규칙

- 입력: `session_id`, `user.id`, `input.text`, `context.character_id`, `context.model_tier_requested`
- 출력 렌더링: `turn.speech`, `turn.passage`, `turn.ooc`, `turn.clarify`
- 상태 표시: `state.system_state`, `state.state_version`, `state.model_tier_effective`, `meta.trace_id`
- 오류 표시: `error.message`, `error.trace_id`

## 제거 대상 내부 개념

- `run_meta`
- `plan_created`, `todo_started`, `todo_done`
- `tool_started`, `tool_done`
- raw prompt/response
- patch/evolution/task_block 전용 payload
- `/chat/sessions/*` 기반 message version 복원 로직

## 1차 적용 방침

1. 레거시 `index.html`은 즉시 제거하지 않는다.
2. Runtime 계약만 사용하는 신규 화면 `runtime-console`, `runtime-operator`를 먼저 제공한다.
3. 사용자 레벨 렌더링은 turn 타입만 사용한다.
4. 운영 화면은 `/runtime/status`만 사용한다.
5. `engine-backed`와 `llm-only`는 같은 화면에서 `runtime.impl`만 다르게 보여야 한다.
