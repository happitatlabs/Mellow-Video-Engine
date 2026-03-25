# Mellow-Link 엔진 및 AIruntime 소개서 (개발자 공유용)

기준 시점: 2026-03-05  
대상: Mellow-Link에 새로 합류하는 백엔드/AI/클라이언트 개발자

---

## 1) 한 줄 요약

Mellow-Link는 **오케스트레이션 엔진(내부)**과 **Chat Runtime API(외부 계약)**를 분리한 구조이며,  
챗봇은 Runtime API만 호출하고 내부 판단엔진(GM/OoC 로직)은 비공개로 유지합니다.

---

## 2) 현재 아키텍처 핵심

### 엔진(내부)
- FSM 기반 상태 제어: `IDLE | TEXT | IMAGE | ERROR`
- Orchestrator 중심으로 LLM/Image/Video/RAG/보안 정책을 조율
- ReAct 기반 Agent Brain, Tool Registry, Memory/RAG, 보안 게이트 포함
- 내부 구현 디렉터리: `mellow_link/core`, `mellow_link/services`, `mellow_link/infra`

### AIruntime(외부 계약)
- 외부에 공개하는 인터페이스는 최소 2개:
1. `POST /runtime/turn`
2. `GET /runtime/status`
- 내부 개념(FSM/GM/Tool/Adapter) 직접 노출 금지
- 응답은 `speech/passage/ooc/clarify` 중심의 UI 친화 포맷으로 정규화

---

## 3) AIruntime 파이프라인 (engine-backed 기준)

현재 정식 경로는 2단계입니다.

1. **GM Decision 단계 (내부)**
- 입력: user text + character_id
- 출력: `intent`, `confidence`, `slots`, `state_summary`, `user_action`, `needs_clarify`
- 출력 형식: JSON 강제
- 목적: 대화 의도/행동 해석, 애매하면 clarify 유도

2. **Character Render 단계 (내부)**
- 입력: GM 결과 + 유저 메시지 + character_id + tier
- 출력: `speech`, `passage`, `ooc`
- 목적: 캐릭터 톤으로 최종 발화 생성

즉, 클라이언트는 최종 결과만 받고, GM 판단 과정은 API 뒤에 숨깁니다.

---

## 4) 요청하신 운영 방향 반영안

### A. 챗봇에서 GM(OoC) 판단엔진 비공개
- 유지 원칙: 판단 로직은 엔진 내부 모듈로 유지
- 외부 노출: Runtime API만 노출
- 장점: 클라이언트 단순화, 보안/정책 일관성, 내부 교체 용이

### B. 캐릭터 AI는 외부 연결형
- 선택지 1: 경량 로컬 AI (Ollama/llama.cpp 계열)
- 선택지 2: 상용 LLM(OpenAI/Anthropic 등)
- 연결 방식: Character Render 단계의 모델 백엔드를 어댑터로 교체
- 핵심: API 계약(`turn/status`)은 그대로 유지해 코어만 교체 가능

---

## 5) 구현 상태 기준 계약 (요약)

### `POST /runtime/turn`
- 입력: `session_id`, `user.id`, `input.text`, `context(character_id, model_tier_requested, ...)`
- 출력:
1. `turn`: `speech`, `passage`, `ooc`, `clarify`
2. `state`: `session_id`, `state_version`, `system_state`, `model_tier_effective`
3. `meta`: `trace_id`, `runtime_impl`, `latency_ms`, `engine_ref(optional)`

### `GET /runtime/status`
- 출력: `runtime(impl/version/uptime)`, `health(system_state/last_error/degraded)`, `counters`, `time`

---

## 6) 구현체 스위치 포인트

- Runtime 구현체 2종:
1. `engine-backed` (정식 2단 파이프라인)
2. `llm-only` (경량/대체 경로)
- 라우터는 `app_state.runtime_impl` 값을 읽어 구현체를 선택 (미설정 시 `engine-backed`)

---

## 7) 다른 개발자가 바로 이해해야 할 경계

1. 앱/챗봇은 Runtime API만 호출한다.  
2. GM/OoC/도구/FSM은 엔진 내부 상세이므로 앱 코드에서 참조하지 않는다.  
3. Character 모델은 로컬/상용 교체 가능하지만 응답 스키마는 유지한다.  
4. 장애 추적은 `meta.trace_id` 기준으로 한다.

---

## 8) 온보딩 시 권장 읽기 순서

1. `mellow_link/docs/Chat_Runtime_API_openapi.yaml`
2. `mellow_link/routers/runtime.py`
3. `mellow_link/runtime/engine_backed_adapter.py`
4. `mellow_link/runtime/prompts/gm_prompt.txt`
5. `mellow_link/runtime/prompts/character_prompt.txt`
6. `mellow_link/Mellow_Link_Spec.md`

---

## 9) 결론

현재 구조는 이미 **“판단엔진 내부 비공개 + API 계약 고정 + 캐릭터 AI 백엔드 교체 가능”** 방향에 맞춰져 있습니다.  
따라서 다음 단계는 Runtime API를 기준 계약으로 고정하고, Character Render 백엔드 어댑터를 로컬 경량/상용 LLM 양쪽으로 확장하는 것입니다.

