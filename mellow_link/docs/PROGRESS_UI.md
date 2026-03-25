# Agent Progress UI

실시간으로 에이전트의 작업 진행 상황을 보여주는 UI 시스템입니다.

## 개요

Agent Progress UI는 에이전트가 작업을 수행하는 동안:
- **작업 목록 (To-dos)**: 계획된 단계들을 체크리스트로 표시
- **현재 작업 하이라이트**: 현재 실행 중인 단계 강조
- **실시간 로그**: 도구 실행, 이벤트 등을 실시간으로 스트리밍
- **카운터**: 도구 호출 수, 파일 탐색 수, 검색 수 등을 표시

## 아키텍처

### 1. 이벤트 스키마

이벤트는 다음 형식을 따릅니다:

```json
{
  "run_id": "run_20260220_001",
  "ts": 1771519999.12,
  "type": "todo_done",
  "payload": {
    "todo_id": "T3",
    "title": "Write tests",
    "detail": "pytest OK"
  }
}
```

**이벤트 타입:**
- `run_started`: 실행 시작
- `plan_created`: 계획 생성 (todos 배열 포함)
- `todo_started`: 작업 시작 (todo_id)
- `todo_done`: 작업 완료 (todo_id, status, optional summary)
- `tool_started`: 도구 실행 시작 (tool_name, args_summary)
- `tool_done`: 도구 실행 완료 (tool_name, success, duration_ms)
- `log`: 로그 메시지 (level, message)
- `run_finished`: 실행 완료 (success, summary)
- `error`: 오류 발생 (message, stack?)

### 2. 데이터베이스 스키마

**agent_runs 테이블:**
- `id`: Primary key
- `run_id`: 고유 실행 ID (string, indexed)
- `session_id`: 세션 ID (optional)
- `status`: 상태 (pending, running, completed, failed)
- `created_at`, `updated_at`: 타임스탬프
- `summary`: 실행 요약 (optional)

**agent_run_events 테이블:**
- `id`: Primary key
- `run_id`: 실행 ID (foreign key, indexed)
- `ts`: Unix 타임스탬프 (float, indexed)
- `type`: 이벤트 타입 (string, indexed)
- `payload_json`: JSON 페이로드 (text)

### 3. API 엔드포인트

#### POST /runs
새로운 실행을 생성하고 `run_id`를 반환합니다.

**Response:**
```json
{
  "run_id": "run_20260220_001",
  "status": "pending"
}
```

#### POST /runs/{run_id}/start
실행을 시작합니다.

**Request Body:**
```json
{
  "user_input": "사용자 요청",
  "session_id": "optional_session_id",
  "mode": "fast"
}
```

#### GET /runs/{run_id}
실행 스냅샷을 조회합니다 (todos + 최근 이벤트 + 카운터).

**Response:**
```json
{
  "run_id": "run_20260220_001",
  "session_id": "session_123",
  "status": "running",
  "created_at": "2026-02-20T12:00:00",
  "updated_at": "2026-02-20T12:01:00",
  "summary": null,
  "todos": [
    {"todo_id": "T1", "title": "요청 파싱", "status": "completed"},
    {"todo_id": "T2", "title": "모드 선택", "status": "completed"},
    {"todo_id": "T3", "title": "도구 실행", "status": "running"}
  ],
  "current_todo_id": "T3",
  "counters": {
    "tool_calls": 5,
    "files_explored": 3,
    "searches": 2
  },
  "last_event_id": 42,
  "last_event_ts": 1771519999.12
}
```

#### GET /runs/{run_id}/events (SSE)
Server-Sent Events로 실시간 이벤트를 스트리밍합니다.

**Query Parameters:**
- `last_event_id` (optional): 마지막 이벤트 ID (커서)
- `last_ts` (optional): 마지막 타임스탬프 (커서)

**SSE 형식:**
```
id: 42
data: {"id": 42, "run_id": "run_20260220_001", "ts": 1771519999.12, "type": "tool_started", "payload": {...}}

```

### 4. 이벤트 발행 통합

`AgentBrain.run()` 메서드에서 자동으로 이벤트가 발행됩니다:

- **run_started**: 실행 시작 시
- **plan_created**: 기본 계획 생성 시 (MVP: deterministic 5단계)
- **todo_started/todo_done**: 각 단계 시작/완료 시
- **tool_started/tool_done**: 도구 실행 전/후
- **log**: 중요 로그 메시지
- **run_finished**: 실행 완료 시 (성공/실패)

`run_id`는 `session_state` 딕셔너리를 통해 전달됩니다:

```python
session_state = {"run_id": run_id}
agent_result = await orchestrator.run_agent(
    user_input,
    session_state=session_state
)
```

### 5. Redaction 및 안전성

이벤트 페이로드는 자동으로:
- **API 키/비밀번호 제거**: 패턴 매칭으로 민감한 정보 마스킹
- **경로 마스킹**: 워크스페이스 외부 절대 경로 마스킹
- **크기 제한**: 페이로드 최대 2KB, 긴 필드는 자동 잘림

## 사용 방법

### 로컬 실행

1. **서버 시작:**
   ```bash
   python -m mellow_link.main
   ```

2. **UI 접속:**
   브라우저에서 `http://localhost:8000/static/progress_ui.html` 접속

3. **실행 시작:**
   - 사용자 요청 입력
   - "시작" 버튼 클릭
   - 실시간 진행 상황 확인

### 프로그래밍 방식

```python
from mellow_link.infra.run_events import create_run, emit_event

# Run 생성
run_id = create_run(session_id="session_123")

# 이벤트 발행
emit_event(run_id, "tool_started", {
    "tool_name": "read_file",
    "args_summary": {"path": "example.py"}
})
```

## 프론트엔드 UI

`static/progress_ui.html` 파일이 포함되어 있으며 다음 기능을 제공합니다:

- **작업 목록 패널**: To-dos 체크리스트, 현재 작업 하이라이트
- **로그 패널**: 실시간 이벤트 스트리밍
- **상태 바**: 실행 상태, 카운터 (도구 호출, 파일 탐색, 검색)
- **재연결 기능**: SSE 연결 끊김 시 자동 재연결
- **스냅샷 로드**: 페이지 새로고침 시 기존 상태 복원

## 향후 개선 사항

1. **LLM 기반 계획 생성**: 현재 deterministic 계획을 LLM이 생성한 계획으로 대체
2. **이벤트 필터링**: UI에서 특정 이벤트 타입만 표시
3. **히스토리 조회**: 과거 실행 기록 조회 및 재생
4. **다중 실행 지원**: 여러 실행을 동시에 모니터링
5. **성능 메트릭**: 실행 시간, 토큰 사용량 등 상세 메트릭 표시

## 문제 해결

### SSE 연결이 끊김
- 브라우저 개발자 도구에서 네트워크 탭 확인
- 서버 로그에서 오류 확인
- "재연결" 버튼 클릭

### 이벤트가 표시되지 않음
- `run_id`가 올바르게 생성되었는지 확인
- AgentBrain에 `session_state`가 전달되었는지 확인
- DB에 이벤트가 저장되었는지 확인 (`agent_run_events` 테이블)

### 성능 이슈
- 이벤트 페이로드 크기 확인 (최대 2KB)
- DB 인덱스 확인 (`run_id`, `ts` 인덱스)
- SSE 폴링 간격 조정 (현재 1초)
