# AI_Project — 전체 구조 및 기능 명세

**작성 목적:** 프로젝트 구조, 코어 모듈 역할·흐름, 기술 스택을 결론 중심으로 정리.  
**태그:** ✅ verified (코드/설정으로 확인) | ⚠️ possible (구조·문서 기반 추론) | ❌ hypothetical (미검증 가정)

---

## 1. Directory Tree (프로젝트 폴더·파일 구조)

```
AI_Project/
├── launcher.py                 # 단일 진입점: Ollama 검사 → Mellow-Link 기동 → 브라우저 오픈
├── moltbook_adapter.py         # Moltbook 연동 어댑터 (사용 중단/폐기)
├── promote_admin.py            # 관리자 권한 승격 유틸
├── rebuild_venvs.ps1           # 가상환경 재구성 스크립트
├── test_security_auditor.py    # 보안 감사 테스트
├── test_tts.py                 # TTS 테스트
├── AI_PROJECT_STRUCTURE_AND_SPEC.md
├── SYSTEM_ARCHITECTURE_GUIDEBOOK.md
├── TROUBLESHOOTING.md
├── network_info.md
├── .claude/                    # Claude 설정
├── .clawdhub/                  # ClawdHub 설정
├── .cursorrules                # Cursor 규칙
├── .gitignore
│
├── mellow_link/                # 핵심 오케스트레이션 서비스 (FastAPI, 포트 8000)
│   ├── main.py                 # FastAPI 앱 진입점, 라이프사이클, 라우터 include
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py         # pydantic-settings (env, security_level 등)
│   ├── core/                   # 상태·오케스트레이션·에이전트·보안·진화
│   │   ├── states.py, events.py
│   │   ├── orchestrator.py     # FSM, 채팅 파이프라인, GPU 자원 공유
│   │   ├── agent_brain.py      # LLM 호출·의도 분류·스트리밍
│   │   ├── agent_tools.py      # 에이전트 도구 (파일/명령/HTTP 등), SecurityManager 연동
│   │   ├── agent_group.py      # 다중 에이전트 그룹 관리
│   │   ├── autonomous_agent.py # 자율 에이전트 루프
│   │   ├── admin_tools.py      # 어드민 전용 도구
│   │   ├── security_manager.py # EASY/NORMAL/HARD 보안 단계
│   │   ├── security.py         # 보안 유틸
│   │   ├── path_manager.py     # 샌드박스 경로 제한
│   │   ├── tool_forge.py       # 코드 제안 → AST 검사 → 샌드박스 테스트 → Guardian
│   │   ├── tool_registry.py    # 도구 등록·조회
│   │   ├── guardian_service.py # 2차 검수(Guardian)
│   │   ├── evolution_manager.py# 진화(코드 수정안) 적용·롤백
│   │   ├── evolution_trigger.py, goal_planner.py, goal_manager.py
│   │   ├── checkpoint_manager.py, recovery_manager.py, diagnosis_service.py
│   │   ├── scheduler_service.py# 스케줄·진화 트리거
│   │   ├── complexity_evaluator.py, risk_classifier.py
│   │   ├── provider_factory.py # LLM/Guardian 프로바이더 팩토리
│   │   ├── experience_provider.py, dynamic_registry.py
│   │   ├── log_analyzer.py     # 로그 분석
│   │   ├── database.py         # Evolution 원장 DB (evolution_ledger.db)
│   │   ├── workspace_sandbox.py, workspace_scanner.py
│   │   ├── verdict_prompts.py  # 판결 프롬프트
│   │   ├── schemas.py
│   │   └── test_*.py           # 단위 테스트
│   ├── infra/
│   │   ├── watchdog.py         # VRAM 모니터링
│   │   ├── archiver.py         # 아카이브·에러 메시지
│   │   ├── database.py         # 사용자·세션·폴더·메시지 DB
│   │   ├── memory_database.py  # experience_ledger, evolution_logs 등
│   │   ├── env_loader.py, event_logger.py
│   ├── routers/                # API 라우터 (auth, chat, folders, admin, evolution 등)
│   ├── services/
│   │   ├── llm_service.py      # Ollama 연동
│   │   ├── image_service.py    # ComfyUI 연동
│   │   ├── video_service.py, video_processor.py
│   │   ├── doc_service.py      # 문서 처리
│   │   ├── vtuber_relay.py     # VTuber WebSocket 브리지
│   │   ├── rag_service.py      # RAG 검색
│   │   ├── evolution_service.py, notification_service.py
│   │   ├── visual_planner.py   # 비주얼 플래너
│   │   └── log_analyzer.py     # 로그 분석 서비스
│   ├── static/                 # 프론트 정적 파일
│   │   ├── index.html, flow_monitor.html
│   │   └── js/ (app.js, auth.js, chat.js, state.js, ui-render.js, api.js, folders.js, utils.js)
│   ├── utils/                  # 공통 유틸리티
│   │   ├── report_masking.py   # 보고서 마스킹
│   │   └── system_control.py   # 시스템 제어
│   ├── scripts/                # 스크립트 (검증, 정리 등)
│   ├── prompts/                # 시스템/캐릭터/판결 프롬프트
│   ├── workspace/              # 에이전트 작업 샌드박스 (fs_util, code_analyzer 등)
│   ├── custom_tools/           # 사용자 정의 도구
│   ├── extensions/             # 확장 (molt-identity 등)
│   ├── outputs/                # 생성 결과 (reports/, proposals/, videos/, images/)
│   ├── docs/                   # 설계·검증 가이드
│   ├── EVOLUTION_PROTOCOL.json
│   ├── Mellow_Link_Spec.md
│   ├── requirements.txt
│   └── .env                    # MELLOW_*, SECURITY_LEVEL, Guardian API 키 등
│
└── Open-LLM-VTuber/            # 음성·아바타 서비스 (선택 기동, 포트 12393)
    ├── run_server.py          # 서버 진입점
    ├── src/open_llm_vtuber/
    │   ├── server.py           # FastAPI + WebSocket
    │   ├── websocket_handler.py
    │   ├── routes.py, message_handler.py, proxy_handler.py
    │   ├── service_context.py  # 엔진 의존성 주입
    │   ├── chat_group.py       # 그룹 채팅
    │   ├── chat_history_manager.py
    │   ├── agent/              # LLM 에이전트 (Ollama, Claude 등)
    │   ├── asr/                # 음성 인식 (Sherpa-ONNX, Whisper 등)
    │   ├── tts/                # TTS (Edge TTS, Melo 등)
    │   ├── vad/                # 음성 활동 검출
    │   ├── conversations/      # 대화·TTS 매니저
    │   ├── config_manager/     # YAML 설정
    │   ├── mcpp/               # MCP 도구
    │   ├── translate/          # 번역 (DeepLX, Tencent 등)
    │   ├── utils/              # sensitive_filter, sentence_divider 등
    │   ├── live/               # Bilibili 라이브
    │   ├── live2d_model.py
    │   └── proxy_message_queue.py
    ├── frontend/               # 웹 클라이언트 (서브모듈)
    ├── live2d/                 # Live2D 모델 (haruto, koharu 등)
    ├── config_templates/
    ├── requirements.txt, pyproject.toml
    └── CLAUDE.md
```

**태그:** ✅ verified (실제 디렉터리·주요 파일 존재 및 역할 확인)

---

## 2. Core Modules — 디렉터리별 역할 및 핵심 기능

### 2.1 루트 (AI_Project)

| 항목 | 설명 | 태그 |
|------|------|------|
| **launcher.py** | Ollama 헬스체크 → Mellow-Link 서브프로세스 기동 → 준비 시 브라우저 오픈. VTuber는 Mellow-Link API로 온디맨드 기동. | ✅ verified |
| **moltbook_adapter.py** | Moltbook API 어댑터. (사용 중단: MELLOW_EMERGENCY_LOCKDOWN, MOLTBOOK_AUTOPILOT=false) | ⚠️ deprecated |
| **SYSTEM_ARCHITECTURE_GUIDEBOOK.md** | 3-Tier(LLM / Mellow-Link / VTuber), 포트·역할 문서. | ✅ verified |

### 2.2 mellow_link — 오케스트레이션 핵심

| 디렉터리/모듈 | 역할 | 핵심 기능 | 태그 |
|----------------|------|-----------|------|
| **main.py** | FastAPI 앱. 라이프사이클에서 Watchdog·Orchestrator·LLM/Image/Doc/VTuber Relay·RAG 초기화. | 인증(JWT), 채팅·스트리밍, 업로드, Avatar 기동 API, RAG/임시 컨텍스트. | ✅ verified |
| **config/settings.py** | 단일 설정 소스. pydantic-settings, `.env` 로드. | `security_level`(EASY/NORMAL/HARD), Ollama/ComfyUI/API/Guardian 등. | ✅ verified |
| **core/orchestrator.py** | FSM 기반 오케스트레이터. | `SystemState`, GPU 자원 공유, 채팅 상태(Idle→Analyzing→Retrieving→Generating→Completed). | ✅ verified |
| **core/agent_brain.py** | 채팅 요청 처리. | 의도 분류, RAG 연동, Ollama 스트리밍, 이미지/문서 라우팅. | ✅ verified |
| **core/agent_tools.py** | 에이전트용 도구 등록. | 파일 읽기/쓰기, 터미널 명령, HTTP(curl), 보안 정책 조회(`security_status`). Import 시 `SECURITY_LEVEL` 고정. | ✅ verified |
| **core/agent_group.py** | 다중 에이전트 그룹 관리. | 에이전트 그룹화·조정. | ⚠️ possible |
| **core/autonomous_agent.py** | 자율 에이전트 루프. | ENABLE_AUTONOMOUS_AGENT 시 주기적 자율 작업. | ✅ verified |
| **core/admin_tools.py** | 어드민 전용 도구. | 관리자 전용 기능. | ⚠️ possible |
| **core/database.py** | Evolution 원장 DB. | evolution_history (Guardian 검수 이력). | ✅ verified |
| **core/security_manager.py** | 보안 단계 정책. | EASY/NORMAL/HARD에 따른 파일·명령·아웃바운드 HTTP 제어. | ✅ verified |
| **core/path_manager.py** | 경로 제한. | 샌드박스 내 접근만 허용. | ✅ verified |
| **core/tool_forge.py** | 코드 제안 검증. | AST 보안 검사 → 샌드박스 실행 → Guardian 승인 → 등록. | ✅ verified |
| **core/guardian_service.py** | 2차 검수. | 코드/논리·보안·비용 검토. | ✅ verified |
| **core/evolution_manager.py** | 진화(코드 수정) 적용. | dry-run, diff, 롤백, 보안 정책. | ✅ verified |
| **core/scheduler_service.py** | 스케줄·트리거. | 주기 작업, 진화 트리거 옵션. | ⚠️ possible |
| **infra/watchdog.py** | GPU 상태. | VRAM 임계값 초과 시 이벤트. | ✅ verified |
| **infra/database.py** | persistence. | 사용자·세션·폴더·게스트 사용량·chat_messages. | ✅ verified |
| **infra/memory_database.py** | 메모리 DB. | experience_ledger, evolution_logs, goals, tool_stats 등. | ✅ verified |
| **services/llm_service.py** | LLM. | Ollama API 호출. | ✅ verified |
| **services/image_service.py** | 이미지 생성. | ComfyUI API. | ✅ verified |
| **services/vtuber_relay.py** | 아바타 연동. | WebSocket 브리지(speak 등). | ✅ verified |
| **services/rag_service.py** | RAG. | 문서 검색. | ✅ verified |
| **services/doc_service.py** | 문서 처리. | DocumentService, 문서 QA. | ✅ verified |
| **services/video_service.py** | 비디오 생성. | 비디오 파이프라인. | ⚠️ possible |

### 2.3 Open-LLM-VTuber — 음성·아바타

| 디렉터리/모듈 | 역할 | 핵심 기능 | 태그 |
|----------------|------|-----------|------|
| **server.py** | FastAPI + WebSocket. | 프론트·Live2D·정적 자원, WebSocket 엔드포인트. | ✅ verified |
| **websocket_handler.py** | 메시지 라우팅. | 오디오·대화 트리거·Live2D 제어. | ✅ verified |
| **service_context.py** | 엔진 컨테이너. | LLM/ASR/TTS/VAD 등 per-connection. | ✅ verified |
| **agent/** | 대화 에이전트. | Ollama/Claude 등, 팩토리 패턴. | ✅ verified |
| **asr/** | 음성 인식. | Sherpa-ONNX, Whisper 등. | ✅ verified |
| **tts/** | 음성 합성. | Edge TTS, Melo 등. | ✅ verified |
| **conversations/** | 대화·TTS. | 단일/그룹 대화, TTS 매니저. | ✅ verified |

**태그:** ✅ verified (main/orchestrator/agent_tools/security/watchdog/DB/서비스 연동 코드 기준), ⚠️ possible (스케줄러·진화 트리거 실제 사용처는 설정·호출 경로 추가 확인 시 검증 가능)

---

## 3. 데이터·제어 흐름 (흐름 위주)

### 3.1 기동 흐름

```
[사용자] → launcher.py
    → Ollama 헬스체크 (필수)
    → subprocess: mellow_link (uvicorn main:app)
    → Mellow-Link 준비 시 브라우저 오픈 (localhost:8000)
VTuber: launcher가 직접 기동하지 않음 → Admin이 Mellow-Link API로 아바타 기동 요청 시 프로세스 생성
```

**태그:** ✅ verified (launcher.py 내 Ollama 체크·mellow_proc Popen·VTuber 주석 처리 확인)

### 3.2 채팅·의도 처리 흐름 (Mellow-Link)

```
[클라이언트] → POST /chat/ask (routers/chat.py) 또는 스트리밍
    → Chat 라우터 → Orchestrator
    → AgentBrain: 의도 분류 (simple_chat | image_request | document_qa)
    → RAG 사용 시 RAG 검색 후 컨텍스트 주입
    → target_service: llm → LLMService(Ollama) / image → ImageService(ComfyUI) / document → DocumentService
    → 응답 스트리밍 또는 최종 JSON
```

**태그:** ✅ verified (routers/chat.py·orchestrator·agent_brain import 및 처리 경로 존재)

### 3.3 에이전트 도구·보안 흐름

```
[에이전트 호출] → agent_tools 내 도구 (파일 읽기/쓰기, run_command, curl 등)
    → _get_security() → SecurityManager (import 시 고정된 EASY/NORMAL/HARD)
    → resolve_for_read / resolve_for_write / parse_and_validate_command / is_outbound_http_allowed
    → 통과 시에만 실제 I/O·명령·HTTP 실행
```

**태그:** ✅ verified (agent_tools.py 내 _get_security·resolve_*·parse_and_validate_command·is_outbound_http_allowed 사용처 확인)

### 3.4 코드 제안·진화 흐름

```
[제안 코드] → tool_forge (문법 → AST 보안 검사 → 샌드박스 테스트)
    → Guardian 2차 검수
    → 승인 시 evolution_manager 등으로 적용
    → 실패 시 롤백
```

**태그:** ✅ verified (tool_forge·guardian_service·evolution_manager 역할 및 연동 코드 존재), ⚠️ possible (실제 API 엔드포인트·스케줄 트리거 경로는 추가 확인 시 검증)

---

## 4. 프로젝트 운영을 위한 핵심 기술 스택

| 구분 | 기술 | 용도 | 태그 |
|------|------|------|------|
| **언어·런타임** | Python 3.x | 전 프로젝트 | ✅ verified |
| **웹·API** | FastAPI, Uvicorn, Starlette | Mellow-Link·Open-LLM-VTuber 서버 | ✅ verified |
| **설정** | pydantic-settings, python-dotenv | config/settings, .env | ✅ verified |
| **DB** | SQLAlchemy, aiosqlite | 사용자·세션·폴더·게스트 사용량 | ✅ verified |
| **인증** | JWT (python-jose, PyJWT), OAuth2PasswordRequestForm | main.py 인증 플로우 | ✅ verified |
| **LLM** | Ollama (ollama 패키지), OpenAI 호환 API | 로컬 LLM | ✅ verified |
| **이미지** | ComfyUI (HTTP API) | 이미지 생성 | ✅ verified |
| **RAG·문서** | LlamaIndex, LangChain 등 | rag_service, doc_service | ✅ verified |
| **Guardian·외부 LLM** | Anthropic, OpenAI (API 키) | guardian_service, Verdict/Audit | ✅ verified |
| **보안** | SecurityManager, PathManager, EASY/NORMAL/HARD | agent_tools·파일·명령·HTTP | ✅ verified |
| **GPU·모니터** | psutil, VRAMWatchdog | infra/watchdog | ✅ verified |
| **VTuber** | WebSocket, Edge TTS, Live2D, Sherpa-ONNX | Open-LLM-VTuber | ✅ verified |
| **패키지 관리** | requirements.txt, (Open-LLM-VTuber) uv, pyproject.toml | 의존성 | ✅ verified |

**태그:** ✅ verified (requirements.txt·main.py·config·core·infra·services import 기준)

---

## 5. 문서 작성 원칙 및 태그 요약

- **결론 중심·간결:** 디렉터리 트리, 모듈 역할, 흐름, 기술 스택만 기술.
- **태그 의미:**
  - **✅ verified:** 코드·설정 파일에서 역할·연동·기술 스택 확인됨.
  - **⚠️ possible:** 디렉터리 구조·기존 문서(CLAUDE.md, SYSTEM_ARCHITECTURE_GUIDEBOOK.md) 기반 추론, 호출 경로 일부 미확인.
  - **❌ hypothetical:** 본 문서에서 가정한 내용 없음(현재 미사용).

이 명세는 **AI_Project**의 전체 구조, 코어 모듈 역할·흐름, 기술 스택을 운영·유지보수에 필요한 수준으로 정리한 것이다.
