# System Architecture Guidebook
## Aventurin AI Assistant Platform

**Version:** 2.0
**Created:** 2026-01-22
**Updated:** 2026-02-24
**Author:** Claude (Opus 4.5)
**Purpose:** Definitive technical reference for system management, maintenance, and future expansion

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Configuration Settings](#2-configuration-settings)
3. [Communication Protocol](#3-communication-protocol)
4. [Key Troubleshooting History](#4-key-troubleshooting-history)
5. [Stability Guide](#5-stability-guide)
6. [Conditional Persona Logic](#6-conditional-persona-logic)
7. [Future Expansion: Aventurin Character](#7-future-expansion-aventurin-character)

---

# 1. System Overview

## 1.1 Three-Tier Architecture

The system is built on a **three-tier architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TIER 1: AI BRAIN                                   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         OLLAMA LLM SERVER                                │   │
│  │                                                                          │   │
│  │   Port: 11434                                                            │   │
│  │   Models: exaone-local (Mellow-Link), qwen2.5:7b (VTuber Direct)        │   │
│  │   Role: Natural language understanding and generation                    │   │
│  │   API: OpenAI-compatible REST API                                        │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ HTTP REST API
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TIER 2: CORE                                       │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      MELLOW-LINK ORCHESTRATOR                            │   │
│  │                                                                          │   │
│  │   Port: 8000                                                             │   │
│  │   Framework: FastAPI + Uvicorn                                           │   │
│  │   Role: Central orchestration, user management, session handling         │   │
│  │                                                                          │   │
│  │   Key Responsibilities:                                                  │   │
│  │   - User authentication (JWT tokens)                                     │   │
│  │   - Chat session management (SQLite DB)                                  │   │
│  │   - LLM request routing                                                  │   │
│  │   - VTuber relay (WebSocket bridge)                                      │   │
│  │   - VRAM monitoring and GPU state management                             │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ WebSocket (speak command)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           TIER 3: VTUBER AVATAR                                 │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      OPEN-LLM-VTUBER SERVER                              │   │
│  │                                                                          │   │
│  │   Port: 12393                                                            │   │
│  │   Framework: FastAPI + WebSocket                                         │   │
│  │   Role: Avatar rendering, TTS synthesis, lip sync animation              │   │
│  │                                                                          │   │
│  │   Key Components:                                                        │   │
│  │   - Live2D Model Renderer (haruto)                                       │   │
│  │   - EdgeTTS Engine (ko-KR-InJoonNeural)                                  │   │
│  │   - ASR Engine (Sherpa-ONNX SenseVoice) - for direct voice input         │   │
│  │   - WebSocket Handler (client-ws endpoint)                               │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 1.2 Data Flow Diagram

### Primary Flow: Web UI → Voice Output

```
User Input (Browser)
       │
       │ POST /chat/ask
       ▼
┌──────────────────┐
│   Mellow-Link    │
│   FastAPI:8000   │
│                  │
│ 1. Auth check    │
│ 2. Session mgmt  │
│ 3. DB logging    │
└────────┬─────────┘
         │ HTTP POST /api/generate
         ▼
┌──────────────────┐
│     Ollama       │
│   LLM:11434      │
│                  │
│ Model: exaone    │
│ Streaming resp   │
└────────┬─────────┘
         │ Response text (streaming)
         ▼
┌──────────────────┐
│  VTuberRelay     │
│  Service         │
│                  │
│ 1. Text cleanup  │
│ 2. Sentence split│
│ 3. Emotion detect│
└────────┬─────────┘
         │ WebSocket: {"type": "speak", "text": "..."}
         ▼
┌──────────────────┐
│ Open-LLM-VTuber  │
│   WS:12393       │
│                  │
│ 1. Bypass LLM    │
│ 2. Direct TTS    │
└────────┬─────────┘
         │ TTS request
         ▼
┌──────────────────┐
│    EdgeTTS       │
│  (Microsoft)     │
│                  │
│ Voice: InJoon    │
│ Output: MP3      │
└────────┬─────────┘
         │ Audio payload (base64)
         ▼
┌──────────────────┐
│    Frontend      │
│   + Live2D       │
│                  │
│ 1. Audio play    │
│ 2. Lip sync      │
│ 3. Expression    │
└──────────────────┘
         │
         ▼
      Speakers
```

## 1.3 Component Responsibilities

| Component | Responsibility | Does NOT Do |
|-----------|----------------|-------------|
| **Ollama** | LLM inference only | No TTS, no avatar, no auth |
| **Mellow-Link** | Orchestration, auth, sessions, relay, agent tools | No TTS, no avatar rendering |
| **Open-LLM-VTuber** | TTS, avatar, lip sync | Auth handled externally when relay mode |

## 1.4 Processing Modes

Mellow-Link supports multiple processing modes for different use cases:

| Mode | Model | Use Case | Tool Access |
|------|-------|----------|-------------|
| **fast** | Lightweight | Simple chat, greetings | ❌ No |
| **thinking** | Main model | Analysis, planning, tool use | ✅ Yes |
| **thinking-lite** | Main model | Analysis only (no tools) | ❌ No |
| **research** | Main + Web | Latest info, fact-checking | ✅ Yes (web_search) |
| **auto** | Auto-select | Query-based mode selection | Depends |

### AUTO Mode Selection Priority

```
1. prompt_category == "tool"     → thinking
2. plan_intent detected          → thinking
3. deep_keyword detected         → thinking/thinking-lite
4. short message (< 50 chars)    → fast
5. default                       → fast
```

## 1.5 Chat Pipeline Architecture

```
User Request (/chat/ask)
       │
       ▼
┌──────────────────┐
│  ChatPipelineProcessor  │
│  (orchestrator_chat.py) │
└────────┬─────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐  ┌─────────┐
│ Fast  │  │Thinking │
│ Mode  │  │  Mode   │
└───┬───┘  └────┬────┘
    │           │
    │     ┌─────┴─────┐
    │     ▼           ▼
    │  ┌──────┐  ┌────────┐
    │  │Agent │  │Research│
    │  │Brain │  │(+Web)  │
    │  └──┬───┘  └────┬───┘
    │     │           │
    └─────┴───────────┘
              │
              ▼
       Response Stream
```

---

# 2. Configuration Settings

## 2.1 Server Port Mapping

| Service | Port | Protocol | Endpoint |
|---------|------|----------|----------|
| **Mellow-Link API** | 8000 | HTTP | `http://localhost:8000` |
| **Mellow-Link UI** | 8000 | HTTP | `http://localhost:8000/ui` |
| **Open-LLM-VTuber** | 12393 | HTTP/WS | `http://localhost:12393` |
| **VTuber WebSocket** | 12393 | WebSocket | `ws://localhost:12393/client-ws` |
| **Ollama API** | 11434 | HTTP | `http://localhost:11434` |
| **ComfyUI** (optional) | 8188 | HTTP/WS | `http://localhost:8188` |

## 2.2 Physical Absolute Paths

### Project Root
```
D:\AI_Project\
├── mellow_link\              # Core orchestrator
├── Open-LLM-VTuber\          # Avatar service
├── .venv\                    # Python virtual environment
├── network_info.md           # Network configuration reference
├── TROUBLESHOOTING.md        # Problem solutions
└── SYSTEM_ARCHITECTURE_GUIDEBOOK.md  # This document
```

### Mellow-Link Paths
```
D:\AI_Project\mellow_link\
├── main.py                   # FastAPI application entry
├── app_state.py              # Global service instances
├── dependencies.py           # FastAPI dependency injection
├── config\
│   └── settings.py           # Pydantic settings (ports, models, thresholds)
│
├── routers\                  # API route handlers (분리됨)
│   ├── chat.py               # Chat API (64KB) - /chat/*
│   ├── runs.py               # Run management - /runs/*
│   ├── auth.py               # Authentication - /auth/*
│   ├── admin.py              # Admin APIs - /admin/*
│   ├── folders.py            # Folder management - /folders/*
│   ├── evolution.py          # Triple-intelligence - /evolution/*
│   ├── autonomous.py         # Autonomous agent - /autonomous/*
│   ├── avatar.py             # Avatar control - /avatar/*
│   └── generation.py         # Image generation - /generate/*
│
├── core\                     # Core orchestration logic
│   ├── orchestrator.py       # GPU FSM state machine (44KB)
│   ├── orchestrator_chat.py  # Chat pipeline processor (24KB)
│   ├── agent_brain.py        # ReAct loop engine (140KB)
│   ├── agent_tools_base.py   # Base tool definitions
│   ├── agent_tools_filesystem.py  # File operations (42KB)
│   ├── agent_tools_memory.py      # Memory tools (12KB)
│   ├── agent_tools_research.py    # Web search tools (12KB)
│   ├── agent_tools_system.py      # System tools (20KB)
│   ├── agent_tools_creative.py    # Image/video tools (10KB)
│   ├── agent_tools_docs.py        # Document tools (4KB)
│   ├── evolution_manager.py  # Triple-intelligence chain (70KB)
│   ├── guardian_service.py   # Security audit service (36KB)
│   ├── tool_forge.py         # Dynamic tool generation (55KB)
│   ├── dynamic_registry.py   # Tool registry (18KB)
│   ├── autonomous_agent.py   # Autonomous loop (42KB)
│   ├── scheduler_service.py  # Task scheduler (30KB)
│   ├── diagnosis_service.py  # System diagnosis (40KB)
│   ├── experience_provider.py # Memory retrieval (17KB)
│   ├── goal_manager.py       # Goal management (30KB)
│   ├── states.py             # State definitions (12KB)
│   ├── security_manager.py   # Security controls (22KB)
│   └── output_sanitizer.py   # Output cleaning (16KB)
│
├── services\                 # External service clients
│   ├── llm_service.py        # Ollama LLM client (51KB)
│   ├── rag_service.py        # RAG + Dual memory (35KB)
│   ├── image_service.py      # ComfyUI client (38KB)
│   ├── vtuber_relay.py       # VTuber WebSocket relay (26KB)
│   ├── video_service.py      # Video pipeline (31KB)
│   ├── doc_service.py        # Document QA
│   └── notification_service.py # Notifications (25KB)
│
├── infra\                    # Infrastructure layer
│   ├── database.py           # SQLite ORM (18KB)
│   ├── memory_database.py    # Experience ledger (127KB)
│   ├── run_events.py         # SSE event system (26KB)
│   ├── watchdog.py           # VRAM monitoring (26KB)
│   ├── archiver.py           # Archive & logging (21KB)
│   └── workspace_rag_store.py # Workspace RAG (8KB)
│
├── utils\
│   ├── evolution_to_patch.py # Evolution→Patch conversion
│   ├── report_masking.py     # Sensitive data masking
│   └── system_control.py     # System utilities
│
├── static\
│   ├── index.html            # Main Web UI
│   ├── dev_console.html      # Developer console
│   ├── flow_monitor.html     # Admin flow monitor
│   └── js\
│       ├── app.js            # Application initialization
│       ├── auth.js           # Authentication
│       ├── chat.js           # Chat functionality (33KB)
│       ├── state.js          # Global state management
│       ├── ui-render.js      # UI rendering (PatchReport cards)
│       └── folders.js        # Folder management
│
├── workspace\                # Agent sandbox (read/write allowed)
├── custom_tools\             # User-defined tools (write allowed)
├── outputs\                  # Generated content
│   ├── reports\
│   ├── proposals\
│   ├── videos\
│   └── images\
├── docs\                     # Documentation
└── .env                      # Environment configuration
```

### Open-LLM-VTuber Paths
```
D:\AI_Project\Open-LLM-VTuber\
├── conf.yaml                 # Main configuration file
├── run_server.py             # Server entry point
├── src\open_llm_vtuber\
│   ├── server.py             # FastAPI server
│   ├── websocket_handler.py  # WebSocket message router
│   ├── service_context.py    # Dependency injection
│   ├── conversations\
│   │   ├── conversation_handler.py  # Conversation trigger handler
│   │   ├── single_conversation.py   # Single user conversation
│   │   └── tts_manager.py           # TTS task queue
│   ├── tts\
│   │   └── edge_tts.py       # EdgeTTS implementation
│   └── asr\
│       └── sherpa_onnx_asr.py  # ASR implementation
├── live2d\                   # Live2D model files
│   ├── haruto\               # Current active model ★
│   │   └── runtime\
│   │       ├── haruto.model3.json
│   │       ├── haruto.moc3
│   │       └── textures\
│   ├── koharu\
│   ├── mao_pro\
│   └── shizuku\
├── models\                   # AI models (ASR, etc.)
│   └── sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\
├── cache\                    # TTS audio cache
└── chat_history\             # Conversation logs
```

### Model File Absolute Paths (Haruto)

```
Live2D Model Root:
D:\AI_Project\Open-LLM-VTuber\live2d\haruto\

Model Definition:
D:\AI_Project\Open-LLM-VTuber\live2d\haruto\runtime\haruto.model3.json

Model Binary:
D:\AI_Project\Open-LLM-VTuber\live2d\haruto\runtime\haruto.moc3

Textures Directory:
D:\AI_Project\Open-LLM-VTuber\live2d\haruto\runtime\textures\
```

## 2.3 Configuration Files

### Mellow-Link: `.env`
```ini
# Location: D:\AI_Project\mellow_link\.env

# Server
APP_TITLE=Aventurine v3
# 기본값은 127.0.0.1(보안). 외부 접근 시에만 SERVER_HOST=0.0.0.0 또는 MELLOW_API_HOST=0.0.0.0
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DEBUG=true

# LLM
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL_NAME=exaone-local

# Avatar
AVATAR_WS_URL=ws://localhost:12393/client-ws

# Auth
GUEST_ACCESS_CODE=lucky777
LIMIT_ADMIN=-1
LIMIT_USER=150
LIMIT_GUEST=3

# Paths
MELLOW_MODEL_DIR=D:\AI_Hub\Models
MELLOW_DATA_DIR=D:\AI_Hub\Data
MELLOW_OUTPUT_DIR=D:\AI_Hub\Data\outputs

# Security
SECURITY_LEVEL=NORMAL  # EASY / NORMAL / HARD

# Guardian (Triple-Intelligence Chain)
GOOGLE_API_KEY=<your-key>      # Tower (Gemini)
OPENAI_API_KEY=<your-key>      # Verdict (GPT)
ANTHROPIC_API_KEY=<your-key>   # Audit (Claude)
GUARDIAN_PROVIDER=anthropic
TOWER_MODEL=gemini-2.5-pro
VERDICT_MODEL=gpt-4o
AUDIT_MODEL=claude-3-5-sonnet-20240620

# Autopilot (optional)
ENABLE_AUTONOMOUS_AGENT=true
AUTONOMOUS_INTERVAL_SECONDS=7200
ENABLE_EVOLUTION_TRIGGER=true

# Moltbook (deprecated - 사용 중단)
MELLOW_EMERGENCY_LOCKDOWN=true
MOLTBOOK_AUTOPILOT=false
```

### Open-LLM-VTuber: `conf.yaml`
```yaml
# Location: D:\AI_Project\Open-LLM-VTuber\conf.yaml

system_config:
  host: 'localhost'
  port: 12393

character_config:
  live2d_model_name: 'haruto'
  character_name: 'haruto'
  human_name: 'Mellow'

  agent_config:
    conversation_agent_choice: 'basic_memory_agent'
    agent_settings:
      basic_memory_agent:
        llm_provider: 'ollama_llm'
    llm_configs:
      ollama_llm:
        base_url: 'http://localhost:11434/v1'
        model: 'qwen2.5:7b'
        keep_alive: -1

  asr_config:
    asr_model: 'sherpa_onnx_asr'
    sherpa_onnx_asr:
      model_type: 'sense_voice'
      sense_voice: './models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx'

  tts_config:
    tts_model: 'edge_tts'
    edge_tts:
      voice: 'ko-KR-InJoonNeural'
      rate: '-10% \sim -15%'
      pitch: '-2Hz \sim +3Hz'
      volume: '+10%' # volume

  vad_config:
    vad_model: null  # Disabled for no-microphone environment
```

---

## 2.4 Triple-Intelligence Chain (삼권분립)

### Overview

Self-evolution system with three AI models providing checks and balances:

```
User Request (/evolution/cycle)
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Tower     │ ──▶ │   Verdict   │ ──▶ │    Audit    │
│ (Gemini)    │     │   (GPT-4)   │     │  (Claude)   │
│             │     │             │     │             │
│ Analysis    │     │ Code Patch  │     │ Security    │
│ Diagnosis   │     │ Writing     │     │ Review      │
└─────────────┘     └─────────────┘     └─────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │ Admin Approval  │
                                    └─────────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │ File Apply      │
                                    └─────────────────┘
```

### Model Configuration

| Role | Model | Environment Variable |
|------|-------|---------------------|
| **Tower** | gemini-2.5-pro | `TOWER_MODEL` |
| **Verdict** | gpt-4o | `VERDICT_MODEL` |
| **Audit** | claude-3-5-sonnet | `AUDIT_MODEL` |

### Security Sandbox

```python
# Allowed paths (write enabled)
ALLOWED_PATHS = ["services/", "custom_tools/", "workspace/"]

# Protected paths (system brain - read only)
PROTECTED_PATHS = ["core/", "infra/", "main.py", "routers/"]
```

### PatchReport Generation

**File:** `mellow_link/utils/evolution_to_patch.py`

Server-side deterministic conversion from `evolution_report` to `patch_report`:

```python
def evolution_report_to_patch_report(evolution_report: Dict) -> Dict:
    # Deterministic - no fabrication of results
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

## 2.5 Dual Memory System

### RAG (Permanent Storage)

```
POST /upload/rag → rag_service.py → ChromaDB/LlamaIndex
```

- Document embedding and retrieval
- Collection-based separation
- Persistent storage

### In-memory Temp (Volatile)

```
POST /upload/temp → app_state.TEMP_CONTEXT_STORE[session_id]
```

- Session-scoped text memory
- Lost on server restart
- No RAG indexing overhead

---

## 2.6 Language Guardrail Configuration

### Overview

Mellow-Link enforces strict Korean-only output to prevent unwanted language mixing, particularly Chinese character (漢字) leakage from multilingual LLM models.

### Default Assistant Prompt

**File:** `D:\AI_Project\mellow_link\prompts\default_system_prompt.txt`

```
# Role

당신은 '멜로우 링크(Mellow-Link)'의 기본 어시스턴트입니다. 사용자에게 친절하고 정확한 정보를 제공하는 전문적인 한국어 AI입니다.

# STRICT RULES (가장 중요)

1. 언어 제한: 모든 답변은 반드시 '한국어(한글)'로만 작성해야 합니다.

2. 한자 사용 금지: 한자(漢字) 및 중국어 표현(Simplified/Traditional Chinese)을 절대 사용하지 마세요. "直接", "起的名字", "你需要什么帮助？" 같은 표현은 엄격히 금지됩니다.

3. 번역 금지: 영어 명령어나 다른 언어의 질문을 받아도, 답변의 내용은 오직 자연스러운 한국어 문장으로만 구성되어야 합니다.

4. 출력 형식: 답변은 간결하고 명확하게 하며, 불필요한 서론이나 한자 병기는 생략합니다.

# Tone & Style

- 정중하고 친절한 표준어(해요체)를 사용합니다.
- 사용자의 질문에 핵심부터 답변하며, 한국 정서에 맞는 자연스러운 어휘를 선택합니다.
- 답변 내에 특수문자나 이모지는 과도하지 않게, 한국어 문맥을 보조하는 정도로만 사용합니다.

# Emergency Protocol

만약 내부적으로 중국어 토큰이 생성되려 한다면, 즉시 이를 중단하고 가장 적절한 한국어 단어로 대체하여 문장을 완성하세요.
```

### Guardrail Rules Summary

| Rule | Description | Example (Forbidden) |
|------|-------------|---------------------|
| **Korean Only** | All responses must be in Korean (Hangul) | - |
| **No Hanja** | Chinese characters (漢字) strictly prohibited | 直接, 起的名字 |
| **No Chinese** | Simplified/Traditional Chinese forbidden | 你需要什么帮助？ |
| **No Translation Leakage** | Even if asked in English, respond in Korean | - |

### Implementation Points

**1. System Prompt Injection (Language Guardrail)**

**File:** `mellow_link/main.py:2137-2141`

```python
# mandatory_guardrail - Web과 VTuber 모두 적용
mandatory_guardrail = (
    "IMPORTANT RULES:\n"
    "1. LANGUAGE: Korean (한글) ONLY. No English, No Chinese.\n"
    "2. NO HANJA: 한자(漢字) 및 중국어 표현을 절대 사용하지 마세요. (예: 確認 -> 확인)\n"
    ...
)
```

**2. TTS Text Cleaning (Pre-processing)**

**File:** `mellow_link/main.py:2464-2520`

```python
# [CRITICAL FIX] Send response to Avatar for TTS/motion
# Remove markdown, parentheses, Chinese characters
cleaned_response = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned_response)
cleaned_response = re.sub(r'\*([^*]+)\*', r'\1', cleaned_response)
# Pattern to keep only: Korean, basic Latin, numbers, punctuation
cleaned_response = re.sub(r'[^\w\s가-힣.,!?;:()\-\'"]+', '', cleaned_response, flags=re.UNICODE)
```

### Tone Configuration

| Setting | Value |
|---------|-------|
| **Speech Level** | 해요체 (Polite informal) |
| **Style** | 친절하고 정중 (Friendly and polite) |
| **Response Priority** | 핵심부터 답변 (Answer the core first) |
| **Emoji Usage** | Minimal, context-supporting only |

### Emergency Protocol Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    TOKEN GENERATION FLOW                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   LLM generates token                                           │
│          │                                                      │
│          ▼                                                      │
│   ┌──────────────┐    YES    ┌─────────────────────────────┐   │
│   │ Is Chinese?  │──────────▶│ STOP & Replace with Korean  │   │
│   └──────────────┘           └─────────────────────────────┘   │
│          │ NO                                                   │
│          ▼                                                      │
│   Output token                                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Persona-Specific Language Rules

| Persona | Language | Speech Level |
|---------|----------|--------------|
| **Default Assistant** | Korean only | 해요체 (Polite) |
| **Aventurine (VTuber)** | Korean only | 반말 (Casual) |

---

# 3. Communication Protocol

## 3.1 Mellow-Link → VTuber: SpeakDirect Protocol

### Overview

When Mellow-Link receives an LLM response, it **relays** the text to Open-LLM-VTuber for TTS synthesis. This uses the **SpeakDirect** protocol which bypasses the VTuber's internal LLM.

### Message Format

```json
{
    "type": "speak",
    "text": "안녕, 친구. 오늘 기분이 어때?",
    "emotion": "neutral",
    "priority": 1,
    "metadata": {
        "session_id": 42,
        "folder_name": "Secretary",
        "source": "llm_response"
    }
}
```

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | **YES** | Must be `"speak"` for direct TTS |
| `text` | string | **YES** | Text to synthesize (max 150 chars recommended) |
| `emotion` | string | No | `neutral`, `happy`, `sad`, `surprised`, `angry` |
| `priority` | int | No | 1=normal, 2=high (Secretary), 3=urgent |
| `metadata` | object | No | Context for logging/debugging |

### Implementation: Sender (Mellow-Link)

**File:** `mellow_link/services/vtuber_relay.py`

```python
# Line 479-542: _send_to_vtuber()
async def _send_to_vtuber(self, message: VTuberMessage) -> bool:
    """Send message via WebSocket with sentence splitting."""

    # Split long text (max 150 chars per message)
    sentences = self._split_into_sentences(text, max_length=150)

    # Limit to 5 sentences
    if len(sentences) > 5:
        sentences = sentences[:5]

    for i, sentence in enumerate(sentences):
        payload = {
            "type": "speak",           # ← Critical: bypasses VTuber LLM
            "text": sentence.strip(),
            "emotion": message.emotion,
            "priority": message.priority,
            "metadata": message.metadata
        }

        await self._websocket.send(json.dumps(payload))

        # 2 second delay between sentences
        if i < len(sentences) - 1:
            await asyncio.sleep(2.0)
```

### Implementation: Receiver (Open-LLM-VTuber)

**File:** `Open-LLM-VTuber/src/open_llm_vtuber/conversations/conversation_handler.py`

```python
# Line 36-58: handle_conversation_trigger()
async def handle_conversation_trigger(msg_type: str, data: dict, ...):
    """Handle triggers that start a conversation"""

    # [CRITICAL] Handle 'speak' type - Bypass LLM, go straight to TTS
    if msg_type == "speak":
        text = data.get("text", "")
        if not text or not text.strip():
            return

        # Process directly to TTS without LLM
        current_conversation_tasks[client_uid] = asyncio.create_task(
            process_speak_direct(
                context=context,
                websocket_send=send_func,
                client_uid=client_uid,
                text=text,
                session_emoji=session_emoji,
            )
        )
        return  # Early return - don't process through LLM
```

## 3.2 Broadcast Logic

### When Broadcast is Used

1. **Group conversations:** Multiple clients in same chat group
2. **System announcements:** Server-wide notifications
3. **Relay responses:** When Mellow-Link sends to VTuber

### Broadcast Implementation

**File:** `websocket_handler.py`

```python
# Handler registration includes websocket_send_override parameter
async def handle_conversation_trigger(
    ...,
    websocket_send_override: Optional[Callable] = None,  # Broadcast override
):
    # Use broadcast function if provided, otherwise use direct send
    send_func = websocket_send_override or websocket.send_text
```

**File:** `chat_group.py`

```python
async def broadcast_to_group(
    group_id: str,
    message: str,
    client_connections: Dict[str, WebSocket],
    chat_group_manager: ChatGroupManager,
    exclude_client: Optional[str] = None
):
    """Send message to all clients in a group"""
    group = chat_group_manager.groups.get(group_id)
    if not group:
        return

    for member_uid in group.members:
        if member_uid == exclude_client:
            continue
        if member_uid in client_connections:
            await client_connections[member_uid].send_text(message)
```

## 3.3 Message Type Reference

| Type | Direction | Purpose | Handler |
|------|-----------|---------|---------|
| `speak` | Mellow→VTuber | Direct TTS (bypass LLM) | `process_speak_direct()` |
| `text-input` | Client→VTuber | Text chat (uses LLM) | `process_single_conversation()` |
| `mic-audio-data` | Client→VTuber | Audio stream chunk | `_handle_audio_data()` |
| `mic-audio-end` | Client→VTuber | Audio stream complete | `_handle_conversation_trigger()` |
| `interrupt-signal` | Client→VTuber | Stop current speech | `_handle_interrupt()` |
| `audio-play-start` | VTuber→Client | Audio playback signal | Frontend |
| `full-text` | VTuber→Client | Complete response text | Frontend display |
| `set-model-and-conf` | VTuber→Client | Model/config info | Frontend init |

---

# 4. Key Troubleshooting History

## 4.1 VAD Error Bypass Patch

### Problem Description
Voice Activity Detection (VAD) blocks the conversation pipeline when no microphone is connected.

**Symptoms:**
- Frontend shows "Listening..." indefinitely
- No conversation triggers fire
- Console shows VAD-related errors

### Root Cause
Silero VAD requires audio input to detect speech boundaries. Without microphone:
- `mic-audio-data` never received
- `mic-audio-end` never triggered
- Conversation pipeline blocked

### Solution: Disable VAD

**File:** `conf.yaml`
```yaml
vad_config:
  vad_model: null  # ← Set to null to disable VAD
```

**Alternative: Use Text Input Only**

When VAD is disabled, use `text-input` message type instead of audio:

```python
# Send text directly (bypasses ASR + VAD)
await websocket.send(json.dumps({
    "type": "text-input",
    "text": "Hello, how are you?"
}))
```

### Code Path Comparison

```
WITH Microphone:
mic-audio-data → VAD → mic-audio-end → ASR → LLM → TTS

WITHOUT Microphone (VAD disabled):
text-input → LLM → TTS
         OR
speak → TTS (direct)
```

## 4.2 404 Model Path Error Resolution

### Problem Description
Live2D model fails to load, returning 404 errors for model assets.

**Symptoms:**
- Avatar area shows blank/error
- Console: `GET /live2d/haruto/runtime/haruto.model3.json 404`
- Model info returns `null`

### Root Cause Analysis

1. **Relative path resolution:** Server runs from different working directory
2. **Static mount missing:** Live2D directory not mounted in FastAPI
3. **Model name mismatch:** `conf.yaml` references non-existent model

### Solution: Verify Path Chain

**Step 1: Check conf.yaml model name**
```yaml
character_config:
  live2d_model_name: 'haruto'  # Must match folder name exactly
```

**Step 2: Verify physical path exists**
```
D:\AI_Project\Open-LLM-VTuber\live2d\haruto\runtime\haruto.model3.json
```

**Step 3: Check static mount in server.py**
```python
# Open-LLM-VTuber/src/open_llm_vtuber/server.py
app.mount("/live2d", StaticFiles(directory="live2d"), name="live2d")
```

**Step 4: Verify model_dict.json entry**
```json
// D:\AI_Project\Open-LLM-VTuber\model_dict.json
{
  "haruto": "live2d/haruto/runtime/haruto.model3.json"
}
```

### Debug Code

**File:** `websocket_handler.py:162-171`
```python
# Added null-check for model_info
if not model_info:
    logger.error(f"[WebSocket] Live2D model not initialized for client {client_uid}")
    logger.error(f"[WebSocket] live2d_model: {session_service_context.live2d_model}")
    logger.error(f"[WebSocket] live2d_model_name: {session_service_context.character_config.live2d_model_name}")
    model_info = {}  # Send empty dict instead of None
```

## 4.3 Browser Autoplay Policy Response

### Problem Description
Modern browsers block audio autoplay without user interaction.

**Symptoms:**
- TTS audio doesn't play
- Console: `DOMException: play() failed because the user didn't interact with the document first`
- Avatar moves but no sound

### Root Cause
Browser security policy (Chrome 66+, Firefox 66+, Safari 11+):
- AudioContext starts in "suspended" state
- `audio.play()` rejected without user gesture
- WebRTC audio blocked until interaction

### Solution: User Interaction Gate

**Frontend Implementation:**

```javascript
// Gate audio behind user click
let audioContext = null;
let audioUnlocked = false;

document.getElementById('startButton').addEventListener('click', async () => {
    // Create AudioContext after user gesture
    audioContext = new (window.AudioContext || window.webkitAudioContext)();

    // Resume if suspended
    if (audioContext.state === 'suspended') {
        await audioContext.resume();
    }

    audioUnlocked = true;
    console.log('Audio unlocked by user interaction');

    // Now safe to connect WebSocket and play audio
    connectWebSocket();
});

// Audio playback function
async function playAudio(audioData) {
    if (!audioUnlocked) {
        console.warn('Audio not unlocked - waiting for user interaction');
        return;
    }

    const audio = new Audio(audioData);
    await audio.play();  // Now this will succeed
}
```

**Backend Consideration:**

Send audio only after receiving confirmation that client has unlocked audio:

```python
# Optional: Client sends unlock confirmation
if msg_type == "audio-unlocked":
    context.audio_enabled = True
```

### Testing Checklist

1. [ ] User clicks button before any audio plays
2. [ ] AudioContext created after click
3. [ ] AudioContext.resume() called if suspended
4. [ ] First audio.play() happens in click handler or after

---

# 5. Stability Guide

## 5.1 Connection Loss Handling

### VTuberRelayService Reconnection Strategy

**File:** `mellow_link/services/vtuber_relay.py`

```python
class VTuberRelayService:
    def __init__(self, ws_url, reconnect_interval=5.0, heartbeat_interval=30.0):
        self.reconnect_interval = reconnect_interval  # 5 seconds
        self.heartbeat_interval = heartbeat_interval  # 30 seconds
```

### Reconnection Loop

```python
# Line 315-321
async def _reconnect_loop(self) -> None:
    """Background task for auto-reconnection."""
    while self._is_running:
        if self._status != VTuberConnectionStatus.CONNECTED:
            await self.connect()  # Attempt reconnection
        await asyncio.sleep(self.reconnect_interval)  # Wait 5 seconds
```

### Heartbeat Loop

```python
# Line 323-335
async def _heartbeat_loop(self) -> None:
    """Background task for heartbeat pings."""
    while self._is_running:
        if self.is_connected and self._websocket:
            try:
                await self._websocket.ping()
                self._last_heartbeat = datetime.now()
            except Exception as e:
                logger.warning(f"[VTuberRelay] Heartbeat failed: {e}")
                self._status = VTuberConnectionStatus.DISCONNECTED
        await asyncio.sleep(self.heartbeat_interval)  # Every 30 seconds
```

### Message Queue During Disconnect

```python
# Line 337-358: Messages queued when disconnected
async def _send_loop(self) -> None:
    while self._is_running:
        message = await self._message_queue.get()

        if self.is_connected and self._websocket:
            await self._send_to_vtuber(message)
        else:
            # Re-queue if not connected - will send when reconnected
            await self._message_queue.put(message)
            await asyncio.sleep(1.0)
```

### Connection State Diagram

```
                    ┌─────────────┐
                    │ DISCONNECTED│◄────────────────┐
                    └──────┬──────┘                 │
                           │                        │
                           │ connect()              │ heartbeat fail
                           ▼                        │ or error
                    ┌─────────────┐                 │
                    │ CONNECTING  │                 │
                    └──────┬──────┘                 │
                           │                        │
                           │ success                │
                           ▼                        │
                    ┌─────────────┐                 │
         ┌─────────│  CONNECTED  │─────────────────┘
         │         └─────────────┘
         │               ▲
         │               │
         │  heartbeat    │ reconnect
         │  every 30s    │ every 5s
         │               │
         └───────────────┘
```

## 5.2 Session Persistence

### Problem: LLM Context Loss on Reload

When user reopens a session, the LLM loses conversation context.

### Solution: Database-backed Context Restoration

**File:** `mellow_link/main.py:1990-2022`

```python
# [CRITICAL FIX] Load conversation history from DB when reopening session
if session:
    logger.info(f"[ChatAsk] Loading conversation history for session {session_id}")

    # Load all previous messages from DB
    previous_messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.timestamp.asc()).all()

    # Restore LLM context from DB history
    if previous_messages and llm_service:
        context = llm_service._get_context(str(session_id))
        context.messages.clear()

        # Restore system prompt if session has folder
        if session.folder_id:
            folder = db.query(AgentFolder).filter(AgentFolder.id == session.folder_id).first()
            if folder and folder.system_prompt:
                context.system_prompt = folder.system_prompt

        # Restore all previous messages
        for msg in previous_messages:
            context.add_message(msg.role, msg.content)

        logger.info(f"[ChatAsk] Restored {len(previous_messages)} messages")
```

### Session State Storage

```
┌─────────────────────────────────────────────────────┐
│                    SQLite Database                   │
│         (mellow_link/mellow_link.db)                │
├─────────────────────────────────────────────────────┤
│  users          │ id, username, hashed_password     │
│  agent_folders  │ id, user_id, name, system_prompt  │
│  chat_sessions  │ id, user_id, folder_id, title     │
│  chat_messages  │ id, session_id, role, content     │
└─────────────────────────────────────────────────────┘
```

## 5.3 Graceful Shutdown

### Mellow-Link Shutdown Sequence

**File:** `mellow_link/main.py:500-555`

```python
# Shutdown 순서: shutdown_event → autonomous_agent_task → SchedulerService → VRAM watchdog → VTuber relay → orchestrator → services
async def shutdown() -> None:
    """Gracefully shutdown all services."""
    shutdown_event.set()
    # autonomous_agent_task cancel → SchedulerService stop → VRAM watchdog → VTuber relay → orchestrator → services

    # 2. Stop VRAM watchdog
    if vram_watchdog and vram_watchdog.is_running():
        await vram_watchdog.stop()

    # 3. Stop VTuber relay
    if vtuber_relay:
        await vtuber_relay.stop()

    # 4. Shutdown orchestrator
    if orchestrator:
        await orchestrator.shutdown()

    # 5. Disconnect services
    if llm_service:
        await llm_service.disconnect()
    if image_service:
        await image_service.disconnect()
    if doc_service:
        await doc_service.shutdown()
```

## 5.4 Error Recovery Patterns

### Pattern 1: Retry with Exponential Backoff

```python
async def call_with_retry(func, max_retries=3, base_delay=1.0):
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(f"Retry {attempt + 1}/{max_retries} in {delay}s: {e}")
            await asyncio.sleep(delay)
```

### Pattern 2: Circuit Breaker

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=60):
        self.failures = 0
        self.threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure = None
        self.state = "CLOSED"

    async def call(self, func):
        if self.state == "OPEN":
            if time.time() - self.last_failure > self.reset_timeout:
                self.state = "HALF-OPEN"
            else:
                raise CircuitOpenError()

        try:
            result = await func()
            self.failures = 0
            self.state = "CLOSED"
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.threshold:
                self.state = "OPEN"
            raise
```

---

# 6. Conditional Persona Logic

## 6.1 Overview

The system supports **conditional persona switching** based on the connection source. When input comes through the VTuber interface (port 12393), the Aventurine persona is activated.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        CONDITIONAL PERSONA ROUTING                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   ┌──────────────┐                           ┌──────────────────────────────┐  │
│   │  Web UI      │──── Port 8000 ──────────▶│  default_system_prompt       │  │
│   │  (Browser)   │                           │  (General assistant mode)    │  │
│   └──────────────┘                           └──────────────────────────────┘  │
│                                                                                 │
│   ┌──────────────┐                           ┌──────────────────────────────┐  │
│   │  VTuber WS   │──── Port 12393 ─────────▶│  aventurine_persona_v1.txt   │  │
│   │  (Avatar)    │    (speak type)           │  (Character roleplay mode)   │  │
│   └──────────────┘                           └──────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 6.2 Trigger Condition

| Condition | Value | Action |
|-----------|-------|--------|
| **Source** | WebSocket from port 12393 | Use Aventurine persona |
| **Message Type** | `"type": "speak"` | Indicates VTuber relay |
| **Scope** | VTuber interface only | Does not affect Web UI |

## 6.3 Persona File Location

```
D:\AI_Project\mellow_link\prompts\aventurine_persona_v1.txt
```

## 6.4 Implementation Logic

### Detection Point: VTuberRelayService

**File:** `mellow_link/services/vtuber_relay.py`

When `relay_llm_response()` is called, the system knows the output is destined for the VTuber interface.

### Persona Override Logic

**File:** `mellow_link/main.py` - `/chat/ask` endpoint

```python
# Pseudo-code for conditional persona logic
async def chat_ask(request: Request, ...):
    # Detect if this will be relayed to VTuber
    relay = get_vtuber_relay()
    is_vtuber_output = relay and relay.is_connected

    # Load appropriate persona
    if is_vtuber_output:
        # Override with Aventurine persona for VTuber output
        persona_path = "mellow_link/prompts/aventurine_persona_v1.txt"
        with open(persona_path, 'r', encoding='utf-8') as f:
            system_prompt = f.read()
    else:
        # Use default or folder-specific prompt for Web UI
        system_prompt = folder.system_prompt or default_system_prompt

    # Continue with LLM call using selected persona
    async for chunk in llm_service.generate_stream(
        prompt=question,
        system_prompt=system_prompt,  # ← Conditional persona applied
        ...
    ):
```

### Alternative: Folder-Based Detection

If the request comes from a folder named "Secretary" or similar VTuber-linked folder:

```python
# Detect by folder name
if folder_name and "Secretary" in folder_name:
    system_prompt = load_aventurine_persona()
```

## 6.5 Persona Content Summary

**File:** `aventurine_persona_v1.txt`

| Aspect | Rule |
|--------|------|
| **Language** | Korean 반말 only |
| **Length** | 1-2 sentences maximum |
| **Address** | "멜로우", "친구" |
| **Style** | 능글맞고 여유있는 도박사 |
| **Forbidden** | Long explanations, lists, 존댓말 |

## 6.6 Scope Isolation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SCOPE DIAGRAM                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  [Web UI :8000]                    [VTuber :12393]                          │
│  ┌─────────────────────┐           ┌─────────────────────┐                  │
│  │ - Default persona   │           │ - Aventurine persona│                  │
│  │ - Formal/flexible   │           │ - Character locked  │                  │
│  │ - Long responses OK │           │ - Short responses   │                  │
│  │ - Multi-language    │           │ - Korean only       │                  │
│  └─────────────────────┘           └─────────────────────┘                  │
│           │                                 │                               │
│           │                                 │                               │
│           ▼                                 ▼                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        OLLAMA LLM :11434                            │   │
│  │                     (Same model, different prompts)                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 7. Future Expansion: Aventurin Character

## 7.1 Character Configuration

To switch from Haruto to Aventurin, update the following:

### Step 1: Add Live2D Model

```
D:\AI_Project\Open-LLM-VTuber\live2d\aventurin\
├── runtime\
│   ├── aventurin.model3.json
│   ├── aventurin.moc3
│   ├── textures\
│   │   └── texture_00.png
│   ├── motions\
│   │   ├── idle.motion3.json
│   │   └── speak.motion3.json
│   └── expressions\
│       ├── neutral.exp3.json
│       ├── happy.exp3.json
│       └── thinking.exp3.json
└── ReadMe.txt
```

### Step 2: Update model_dict.json

```json
{
    "haruto": "live2d/haruto/runtime/haruto.model3.json",
    "aventurin": "live2d/aventurin/runtime/aventurin.model3.json"
}
```

### Step 3: Update conf.yaml

```yaml
character_config:
  conf_name: 'aventurin'
  conf_uid: 'aventurin_001'
  live2d_model_name: 'aventurin'
  character_name: 'Aventurin'
  human_name: 'Mellow'

  persona_prompt: |
    당신은 어벤츄린입니다. 스타피스 컴퍼니의 고위 간부이며 능글맞은 도박사입니다.

    [핵심 성격]
    - 능글맞고 여유있는 말투
    - 도박과 확률에 대한 철학적 관점
    - 친근하지만 항상 한 수 앞서는 느낌

    [말투 규칙]
    - 한국어 반말 사용
    - 1-2문장으로 짧게
    - "멜로우", "친구"라고 호칭
```

### Step 4: Update TTS Voice (Optional)

```yaml
tts_config:
  edge_tts:
    voice: 'ko-KR-InJoonNeural'  # Keep same or choose different
    rate: '-10% \sim -15%'
    pitch: '-2Hz \sim +3Hz'
    volume: '+10%' # volume
```

## 7.2 Character Switching at Runtime

### API Endpoint for Config Switch

**File:** `websocket_handler.py`

```python
"switch-config": self._handle_config_switch
```

**Message Format:**
```json
{
    "type": "switch-config",
    "config_name": "aventurin"
}
```

### Characters Directory Structure

```
D:\AI_Project\Open-LLM-VTuber\characters\
├── aventurin.yaml    # Full character config
├── haruto.yaml
└── README.md
```

## 7.3 Aventurin-Specific Prompts

Create character-specific prompt file:

**File:** `Open-LLM-VTuber/prompts/characters/aventurin.txt`

```
당신은 어벤츄린(Aventurine)입니다.

[정체성]
- 스타피스 컴퍼니(IPC)의 전략투자부 고위 간부
- "도박사" 또는 "갬블러"로 불림
- 본명과 과거는 비밀

[성격]
- 겉으로는 능글맞고 가벼워 보이지만, 실제로는 치밀하고 계산적
- 항상 여유롭고 자신감 넘치는 태도
- 말장난과 비유를 즐김
- 확률과 운명에 대한 독특한 철학

[말투 특징]
- "후후", "흥미롭군", "재미있는 판이야"
- 상대방을 "친구"라고 부름
- 도박/게임 용어를 일상 대화에 섞음
- 핵심은 숨기고 빙빙 돌려 말하는 경향

[금지 사항]
- 긴 설명 금지 (1-2문장)
- 존댓말 금지 (반말 사용)
- 직접적인 감정 표현 자제
```

## 7.4 Expression Mapping

Map emotions to Aventurin expressions:

```python
# vtuber_relay.py - Update emotion detection for Aventurin
def _detect_emotion(self, text: str) -> str:
    text_lower = text.lower()

    # Aventurin-specific emotion keywords
    smirk_words = ["후후", "흥미", "재미있", "판이", "도박"]
    if any(word in text_lower for word in smirk_words):
        return "smirk"  # Aventurin signature expression

    confident_words = ["당연", "물론", "확실"]
    if any(word in text_lower for word in confident_words):
        return "confident"

    return "neutral"
```

---

# Appendix A: Quick Reference Card

## Startup Commands

```bash
# 1. Start Ollama (Terminal 1)
ollama serve

# 2. Start Open-LLM-VTuber (Terminal 2)
cd D:\AI_Project\Open-LLM-VTuber
uv run run_server.py

# 3. Start Mellow-Link (Terminal 3)
cd D:\AI_Project
python -m mellow_link.main
```

## Port Quick Check

```bash
# Windows: Check if ports are in use
netstat -ano | findstr :8000
netstat -ano | findstr :11434
netstat -ano | findstr :12393
```

## Key URLs

| Service | URL |
|---------|-----|
| Mellow-Link UI | http://localhost:8000/ui |
| Mellow-Link API Docs | http://localhost:8000/docs |
| VTuber Frontend | http://localhost:12393 |
| Ollama API | http://localhost:11434/api/tags |

## Emergency Fixes

| Problem | Quick Fix |
|---------|-----------|
| Avatar not speaking | Check `"type": "speak"` in relay |
| 404 on model | Verify `live2d_model_name` in conf.yaml |
| Audio not playing | User must click first (browser policy) |
| VAD blocking | Set `vad_model: null` in conf.yaml |
| Session context lost | Check DB restore in `/chat/ask` |

---

**End of System Architecture Guidebook**

*This document should be updated whenever significant changes are made to the system architecture.*
