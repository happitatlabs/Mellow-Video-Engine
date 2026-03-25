# Fast / Thinking / Research 모드 전환 기준 및 로컬 에이전트 적합성

## 1. 모드 정의 및 역할

| 모드 | 목적 | 사용 모델 | Observation 강제 | 히스토리 턴 | 비고 |
|------|------|-----------|-------------------|-------------|------|
| **fast** | 짧은 응답, 간단한 질의 | `MELLOW_LLM_FAST_MODEL` (예: qwen2.5:7b) | **아니오** | 2턴 | 도구 없이 finish 허용, 빈 응답 시 thinking 모델로 1회 폴백 |
| **thinking** | 심층 추론, 계획/분석/보고 | `MELLOW_LLM_THINKING_MODEL` (예: qwen2.5:7b) | **예** | 3턴 | 최소 1회 유효 도구 호출+Observation 후 finish |
| **research** | 최신 정보·사실 확인, 웹 검색 | `MELLOW_LLM_RESEARCH_MODEL` (예: qwen2.5:7b) | **예** | 3턴 | thinking과 동일 + **요청 시 웹 검색 결과 주입**, web_search 도구 안내 |
| **auto** | 자동 선택 | (쿼리 분석 결과에 따라 fast 또는 thinking) | (선택된 모드에 따름) | (선택된 모드에 따름) | **현재 research로는 자동 전환되지 않음** |

- **Observation 강제**: `MELLOW_OBSERVATION_STRICT_MODES` (기본 `thinking,research`)에 포함된 모드에서만, finish 전 최소 1회 유효한 도구 호출 및 실질적인 Observation 필수.
- **프롬프트**: `MELLOW_PROMPT_TEMPLATE_MODE=1` 이면 모드별 미니 템플릿 사용 (FAST_MIN / THINKING_MIN / RESEARCH_MIN). RESEARCH_MIN은 현재 THINKING_MIN과 동일.

---

## 2. 모드 전환 기준 (구현 위치)

### 2.1 사용자가 모드를 지정한 경우

- **API**: `mode` 파라미터로 `fast` | `thinking` | `research` | `auto` 전달.
- **동작**: `auto`가 아니면 그대로 `selected_mode = context.mode` 로 사용.

### 2.2 auto 모드일 때 (쿼리 기반 자동 선택)

구현: `core/orchestrator_chat.py` → `_select_mode_for_query(query)`.

| 조건 | 선택 결과 |
|------|-----------|
| **딥 키워드 포함** | **thinking** |
| **한글 자음 비율 > 50%** (ㅋㅎㄷ 등 짧은 감탄) | **fast** |
| **쿼리 길이 < 50자** | **fast** |
| 그 외 | **thinking** |

**딥 키워드 (한글)**  
`분석`, `리포트`, `전망`, `전략`, `계획`, `설계`, `비교`, `평가`, `검토`, `연구`, `조사`, `탐구`

**딥 키워드 (영문)**  
`analysis`, `report`, `strategy`, `plan`, `research`, `compare`, `evaluate`, `review`, `investigate`

- **research는 auto에서 선택되지 않음.** 사용자가 명시적으로 `mode=research` 를 보낼 때만 research 경로(웹 검색 결과 주입 등)가 동작함.

### 2.3 Research 모드 시 추가 동작

- `_generate_response()` 에서 `selected_mode == "research"` 이면:
  - `_perform_web_search(context.user_query)` 로 사전 웹 검색 수행.
  - 결과를 프롬프트 상단에 "=== 웹 검색 결과 (최신 정보) ===" 형태로 주입.
  - 시스템 안내 문구 추가: "Research 모드입니다. … web_search 도구를 사용하세요."
- 웹 검색 실행은 **ChatPipelineProcessor** 경로를 탈 때만 수행됨. 아래 “현재 채팅 경로” 참고.

---

## 3. 채팅 경로와 모드 전달 (갭 → 수정 반영)

- **의도된 파이프라인**:  
  `ChatContext(mode=...)` → `ChatPipelineProcessor.process_chat()` / `process_chat_stream()` → `_analyze_request()` 에서 auto면 `_select_mode_for_query()` 호출 → `selected_mode` 설정 → RAG/이미지 분기 후 `_generate_response()` 또는 에이전트 호출 시 `selected_mode` 사용.
- **실제 채팅 엔드포인트** (`routers/chat.py`):
  - **`/chat/ask`**: body에서 `mode` 를 읽고, 유효하지 않으면 `"fast"`로 정규화한 뒤 **`run_agent(..., mode=mode)`** 로 전달.
  - **`/chat` (레거시)**: **`run_agent(..., mode=request.mode or "fast")`** 로 전달.
- **수정 반영** (현재):
  - **`/chat/ask`**: body `mode` 기본값을 `"fast"`로 통일하고, 유효한 값(`fast`|`thinking`|`research`|`auto`)만 허용. **`run_agent(..., mode=mode)` 로 전달.**
  - **`/chat`**: **`run_agent(..., mode=request.mode or "fast")`** 로 전달.
  - **`run_agent`**: `mode=="auto"` 이면 `_chat_pipeline._select_mode_for_query(user_input)` 로 fast/thinking 중 하나로 결정한 뒤 사용.
  - 따라서 사용자 지정/자동 선택이 LLM·Observation 정책·프롬프트에 반영됨.

---

## 4. 로컬 에이전트에 모드가 어울리는지 검토

### 4.1 로컬 에이전트 특성 (요약)

- **workspace 샌드박스**: `mellow_link/workspace/` 중심 파일/디렉터리 작업.
- **도구**: read_file, write_file, list_directory, RAG/문서, 이미지 생성 등. **웹 검색(web_search)은 선택 기능.**
- **용도**: 대화형 작업 지시, 문서 기반 QA, 코드/파일 정리, 이미지 생성 등.

### 4.2 모드별 적합성

| 모드 | 적합성 | 비고 |
|------|--------|------|
| **fast** | ✅ 적합 | 짧은 질의·인사·단순 확인에 적합. 도구 없이 finish 허용되어 응답 속도 우선 시 유리. 로컬에서 “빠른 한 마디” 용도로 잘 맞음. |
| **thinking** | ✅ 적합 | “분석해줘”, “계획 세워줘”, “비교해줘”, “리포트 작성” 등 **도구를 쓰고 결론을 내야 하는 작업**에 적합. Observation 강제로 허위 완료 방지. 로컬 에이전트의 핵심 시나리오. |
| **research** | ⚠️ 조건부 | **의도**: 최신 정보·사실 확인·웹 수집. **로컬 환경**: 웹 검색 도구/API가 없거나 제한적이면 “research” 선택 시에도 웹 결과가 비어 있을 수 있음. RAG는 “이미 적재된 문서” 기반이므로 “최신 웹”과는 다름. **정리**: research는 “웹 검색 인프라가 있는 배포 환경”에서 의미 있고, 로컬 전용이면 thinking + RAG로 충분할 수 있음. |
| **auto** | ✅ 적합 (로직은 유지) | 짧은 문장/감탄 → fast, 분석/계획/리포트 키워드 → thinking 으로 나누는 현재 기준은 로컬 사용에도 합리적. 다만 **현재는 이 결과가 run_agent에 전달되지 않는 갭이 있음.** |

### 4.3 권장 사항 (로컬 에이전트 기준)

1. **모드 전달 수정**:  
   `/chat/ask`, `/chat` 에서 요청의 `mode`(또는 auto인 경우 파이프라인에서 결정한 `selected_mode`)를 **`run_agent(..., mode=...)` 에 반드시 전달**하도록 수정하는 것을 권장.  
   그러면 사용자 지정/자동 선택이 실제 LLM·Observation 정책·프롬프트에 반영됨.

2. **Research 모드**:  
   - 로컬에서 웹 검색을 쓰지 않는다면: research 모드를 UI에서 숨기거나 “최신 정보 검색(웹 필요)” 안내와 함께 선택 옵션으로 두는 정도가 적절.  
   - 웹 검색 도구를 로컬에서 사용할 경우: 현재처럼 research 시 사전 웹 검색 + web_search 도구 안내 유지.

3. **기본 모드**:  
   - “빠른 응답”을 우선하면 기본 `fast` 유지.  
   - “도구를 꼭 쓰는 에이전트”를 우선하면 기본을 `thinking` 또는 `auto`로 두는 것도 가능.

4. **Auto에서 research 자동 선택**:  
   - “최신 뉴스”, “지금 시세” 같은 키워드에서만 research를 자동 선택하도록 키워드를 추가할 수 있음.  
   - 로컬에서 웹이 없으면 auto는 fast/thinking만 쓰는 현재 방식이 단순하고 안전함.

---

## 5. 참고: 설정·코드 위치

| 항목 | 위치 |
|------|------|
| 모드별 모델 | `config/settings.py`: `fast_model`, `thinking_model`, `research_model` |
| Observation 강제 모드 | `config/settings.py`: `observation_strict_modes` (기본 `thinking,research`) |
| 모드별 히스토리 턴 | `prompt_history_max_turns_fast` (2), `prompt_history_max_turns_thinking` (3) |
| 자동 모드 선택 | `core/orchestrator_chat.py`: `_select_mode_for_query()` |
| Research 웹 검색 | `core/orchestrator_chat.py`: `_perform_web_search()`, `_generate_response()` |
| 에이전트 모드 적용 | `core/agent_brain.py`: `run(..., mode=)`, `build_system_prompt(..., mode=)` |
| run_agent 진입점 | `core/orchestrator.py`: `run_agent(..., mode="fast")` |
| 채팅 요청/모드 | `routers/chat.py`: `ChatRequest.mode`, `/chat/ask` body `mode` (현재 run_agent에 미전달) |

---

이 문서는 **모드 전환 기준 정리**와 **로컬 에이전트에의 적합성 검토**를 다룹니다. 채팅 라우터에서 `run_agent`에 `mode`를 전달하고, `auto` 시 쿼리 기반 선택을 적용하는 수정이 반영되어 있습니다.
