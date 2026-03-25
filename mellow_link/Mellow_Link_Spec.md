# Mellow-Link: AI Orchestration System Specification

**Version:** 2.0
**Updated:** 2026-02-24
**Status:** Production

---

## 1. System Overview

Mellow-Link는 GPU 리소스 공유를 조율하는 로컬 AI 오케스트레이션 시스템입니다.

### 1.1 Core Responsibilities

| 기능 | 설명 |
|------|------|
| **GPU FSM** | IDLE ↔ TEXT ↔ IMAGE 상태 전환 (VRAM 충돌 방지) |
| **Agent Brain** | ReAct 루프 기반 도구 실행 (thinking/fast/research 모드) |
| **삼권분립** | Tower(분석) → Verdict(판결) → Audit(검수) 자가발전 |
| **듀얼 메모리** | RAG (영구) + In-memory Temp (휘발성) |
| **VTuber Relay** | Mellow-Link → Open-LLM-VTuber TTS 릴레이 |

### 1.2 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           MELLOW-LINK                                │
├─────────────────────────────────────────────────────────────────────┤
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────────┐│
│  │ Routers   │  │   Core    │  │ Services  │  │      Infra        ││
│  │           │  │           │  │           │  │                   ││
│  │ chat.py   │  │orchestrator│ │llm_service│  │ database.py       ││
│  │ runs.py   │  │agent_brain│  │rag_service│  │ memory_database   ││
│  │ auth.py   │  │agent_tools│  │image_svc  │  │ run_events.py     ││
│  │ evolution │  │tool_forge │  │vtuber_relay│ │ watchdog.py       ││
│  └───────────┘  └───────────┘  └───────────┘  └───────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
   ┌─────────┐              ┌─────────┐              ┌─────────────┐
   │ Ollama  │              │ ComfyUI │              │Open-LLM-VTuber│
   │ :11434  │              │ :8188   │              │   :12393      │
   └─────────┘              └─────────┘              └─────────────┘
```

---

## 2. Processing Modes

### 2.1 Mode Definitions

| Mode | Model | Use Case | Tool Access |
|------|-------|----------|-------------|
| **fast** | 경량 모델 | 간단한 대화, 인사 | ❌ |
| **thinking** | 주력 모델 | 분석, 계획, 도구 사용 | ✅ |
| **thinking-lite** | 주력 모델 | 분석만 (도구 불필요) | ❌ |
| **research** | 주력 모델 + 웹 | 최신 정보, 팩트체크 | ✅ (web_search) |
| **auto** | 자동 선택 | 쿼리 기반 모드 결정 | - |

### 2.2 AUTO Mode Selection Logic

```python
# orchestrator_chat.py:_select_mode_for_query()
Priority:
1. prompt_category == "tool"           → thinking
2. plan_intent detected                → thinking
3. deep_keyword detected               → thinking/thinking-lite
4. short message (< 50 chars)          → fast
5. default                             → fast
```

---

## 3. Core Components

### 3.1 Orchestrator (`core/orchestrator.py`)

FSM 기반 GPU 상태 관리 + Chat Pipeline 위임.

```python
class Orchestrator:
    current_state: SystemState  # IDLE | TEXT | IMAGE | ERROR
    _gpu_lock: asyncio.Lock
    agent: AgentBrain
    _chat_pipeline: ChatPipelineProcessor
```

**State Transitions:**
```
IDLE ──→ TEXT ──→ IDLE
  │        │
  │        └──→ IMAGE ──→ IDLE
  │              │
  └──────────────┘

ANY ──→ ERROR ──→ IDLE
```

### 3.2 Agent Brain (`core/agent_brain.py`)

ReAct 루프 기반 LLM 추론 엔진.

**Features:**
- 도구 호출 파싱 (JSON/XML 지원)
- 최대 12턴 루프 (무한루프 방지)
- 세션 상태 추적 (run_id, fast_fallback_used 등)
- Plan intent 감지 → plan_created 이벤트 발행

### 3.3 Agent Tools (분리된 6개 모듈)

| 모듈 | 도구 예시 |
|------|----------|
| `agent_tools_filesystem.py` | read_file, write_file, list_directory |
| `agent_tools_memory.py` | memory_store, memory_search |
| `agent_tools_research.py` | web_search, fetch_url |
| `agent_tools_system.py` | get_system_time, get_current_working_directory |
| `agent_tools_creative.py` | create_image, generate_video |
| `agent_tools_docs.py` | index_document, search_documents |

### 3.4 Tool Forge (`core/tool_forge.py`)

동적 도구 생성 시스템.

```python
forge = get_tool_forge()
result = await forge.forge_tool(
    name="custom_calculator",
    description="수학 계산 수행",
    code="def execute(a, b): return a + b"
)
```

---

## 4. 삼권분립 (Triple-Intelligence Chain)

### 4.1 Pipeline

```
User Request
     │
     ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Tower     │ ──▶ │   Verdict   │ ──▶ │    Audit    │
│ (Gemini)    │     │   (GPT-4)   │     │  (Claude)   │
│             │     │             │     │             │
│ 분석/진단   │     │ 코드 작성   │     │ 보안 검수   │
└─────────────┘     └─────────────┘     └─────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │ Admin Approval  │
                                    │ (UI 승인 버튼)  │
                                    └─────────────────┘
```

### 4.2 Security Sandbox

```python
# 수정 허용 구역
ALLOWED_PATHS = ["services/", "custom_tools/", "workspace/"]

# 수정 금지 구역 (시스템 뇌)
PROTECTED_PATHS = ["core/", "infra/", "main.py", "routers/"]
```

### 4.3 PatchReport 생성

```python
# utils/evolution_to_patch.py
# evolution_report → patch_report (deterministic 변환)

def evolution_report_to_patch_report(evolution_report: Dict) -> Dict:
    # 서버에서 결정적으로 변환 (클라이언트 조작 불가)
    return {
        "type": "patch_report",
        "status": "applied" | "partial" | "rejected",
        "summary": "...",
        "issues": [...],
        "changed_files": [...],
        "regression_guard": [...]
    }
```

---

## 5. 듀얼 메모리 시스템

### 5.1 RAG (영구 저장)

```
/upload/rag → rag_service.py → ChromaDB/LlamaIndex
```

- 문서 임베딩 및 검색
- 컬렉션 기반 분리
- 영구 저장

### 5.2 In-memory Temp (휘발성)

```
/upload/temp → app_state.TEMP_CONTEXT_STORE[session_id]
```

- 세션 스코프 텍스트 메모리
- 서버 재시작 시 소멸
- RAG 인덱싱 오버헤드 없음

---

## 6. Event System

### 6.1 Run Events (`infra/run_events.py`)

```python
EVENT_TYPES = [
    "run_started",      # 실행 시작
    "plan_created",     # 계획 생성
    "todo_started",     # 단계 시작
    "todo_done",        # 단계 완료
    "tool_started",     # 도구 호출 시작
    "tool_done",        # 도구 호출 완료
    "log",              # 일반 로그
    "run_completed",    # 실행 완료
    "run_failed",       # 실행 실패
]
```

### 6.2 프론트엔드 SSE 구독

```javascript
const evtSource = new EventSource(`/runs/${runId}/events`);
evtSource.onmessage = (e) => {
    const event = JSON.parse(e.data);
    // TaskBlock UI 업데이트
};
```

---

## 7. Directory Structure

```
mellow_link/
├── main.py                    # FastAPI 엔트리포인트
├── app_state.py               # 전역 서비스 인스턴스
├── dependencies.py            # FastAPI 의존성
│
├── core/                      # 핵심 로직
│   ├── orchestrator.py        # GPU FSM
│   ├── orchestrator_chat.py   # Chat Pipeline
│   ├── agent_brain.py         # ReAct 루프 (140KB)
│   ├── agent_tools_*.py       # 도구 모듈 (6개)
│   ├── evolution_manager.py   # 삼권분립
│   ├── guardian_service.py    # 보안 감사
│   ├── tool_forge.py          # 도구 생성
│   ├── autonomous_agent.py    # 자율 에이전트
│   ├── states.py              # 상태 정의
│   └── ...
│
├── routers/                   # API 라우터
│   ├── chat.py                # 채팅 API (64KB)
│   ├── runs.py                # 실행 관리
│   ├── auth.py                # 인증
│   ├── evolution.py           # 삼권분립 API
│   └── ...
│
├── services/                  # 외부 서비스 클라이언트
│   ├── llm_service.py         # Ollama LLM
│   ├── rag_service.py         # RAG + 듀얼 메모리
│   ├── image_service.py       # ComfyUI
│   ├── vtuber_relay.py        # VTuber 릴레이
│   └── ...
│
├── infra/                     # 인프라 레이어
│   ├── database.py            # SQLite (users, sessions, messages)
│   ├── memory_database.py     # 경험 원장 (127KB)
│   ├── run_events.py          # 이벤트 시스템
│   ├── watchdog.py            # VRAM 모니터링
│   └── ...
│
├── utils/                     # 유틸리티
│   └── evolution_to_patch.py  # Patch Report 변환
│
├── static/                    # 프론트엔드
│   ├── index.html
│   └── js/
│       ├── app.js
│       ├── chat.js
│       ├── state.js
│       └── ui-render.js
│
├── workspace/                 # 에이전트 샌드박스
├── outputs/                   # 생성물 출력
└── docs/                      # 문서
```

---

## 8. Environment Variables

```ini
# Server (기본값 127.0.0.1; 외부 접근 시 0.0.0.0)
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# LLM
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL_NAME=exaone-local

# 삼권분립
TOWER_MODEL=gemini-2.5-pro
VERDICT_MODEL=gpt-4o
AUDIT_MODEL=claude-3-5-sonnet-20240620

# 자율 에이전트
ENABLE_AUTONOMOUS_AGENT=true
AUTONOMOUS_INTERVAL_SECONDS=7200
ENABLE_EVOLUTION_TRIGGER=true

# 보안
SECURITY_LEVEL=NORMAL  # EASY | NORMAL | HARD
ENABLE_MODEL_UNLOAD_ON_IDLE=true
```

---

## 9. API Quick Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat/ask` | POST | 채팅 메시지 전송 (스트리밍) |
| `/chat/sessions/{id}/messages` | GET | 세션 메시지 조회 |
| `/runs/{id}/events` | GET (SSE) | 실행 이벤트 구독 |
| `/evolution/cycle` | POST | 삼권분립 사이클 실행 |
| `/evolution/apply-from-proposal` | POST | 제안서 적용 |
| `/upload/rag` | POST | RAG 문서 업로드 |
| `/upload/temp` | POST | Temp 컨텍스트 업로드 |

---

*Last updated: 2026-02-24*
