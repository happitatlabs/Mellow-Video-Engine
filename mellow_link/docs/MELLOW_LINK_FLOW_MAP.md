┌───────────────────────────────────────────────────────────────────────────────┐
│                               (0) USER / UI                                  │
│  - Chat request / Task request / Upload docs / Regenerate / Feedback          │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ request_id 생성, mode 전달(fast/thinking/research), session_id
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                         (1) ENTRY / ROUTER / API                              │
│  - routers/chat.py, main.py                                                   │
│  - 환경 변수/설정 로드(config/settings.py)                                     │
│  - MetricsCollector.start(request_id)  (L1 관측 시작)                          │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                         (2) ORCHESTRATOR / FSM (L3)                           │
│  core/orchestrator.py / core/states.py                                        │
│  - SystemState 전이: IDLE ↔ TEXT ↔ IMAGE ↔ ERROR                              │
│  - GPU/VRAM 배타, 큐/락/쿨다운, shutdown 정책                                 │
│  - (CHAT) orchestrator_chat._select_mode_for_query()                          │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ state=TEXT (chat) or state=IMAGE (image/video)
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                    (3) SECURITY + POLICY GATES (L2)                            │
│  core/security_manager.py / core/path_manager.py / core/workspace_sandbox.py  │
│  - sandbox root 강제(workspace)                                               │
│  - tool whitelist / required args / NO_AUTO_EVOLUTION                          │
│  - IntegrityGuard(sha256) / GuardianService(L3 risk audit)                     │
│  - Fail-Closed: 검수 실패/미설정 시 차단                                       │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ allowed? then proceed / else block + record
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                      (4) AGENT BRAIN / ReAct LOOP (L6)                         │
│  core/agent_brain.py                                                          │
│  THINK → ACT(tool) → OBSERVE → ... → FINISH                                   │
│  - observation strict: thinking/research만 강제                                │
│  - _has_valid_tool_execution() + _is_substantive_observation()                 │
│  - 실패/차단/재추론 1회 제한                                                   │
└───────────────┬───────────────────────────────┬───────────────────────────────┘
                │                               │
                │ tool call                     │ LLM call
                ▼                               ▼
┌───────────────────────────────────┐   ┌───────────────────────────────────────┐
│        (5A) TOOL REGISTRY (L4)     │   │         (5B) LLM SERVICE (L5)         │
│  core/tool_registry.py            │   │  services/llm_service.py               │
│  core/agent_tools_*.py            │   │  - mode별 model 선택                    │
│  - file ops / rag / etc           │   │  - n_ctx by mode (options.num_ctx)     │
│                                   │   │  - generate_stream: on_first_token TTFT│
└───────────────┬───────────────────┘   │  - chat: INFER_MS + TPS_APPROX         │
                │                       └───────────────┬───────────────────────┘
                │ tool result (Observation)              │ tokens streamed / answer
                ▼                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                           (6) RAG SERVICE PATH (L4/L5)                         │
│  services/rag_service.py                                                       │
│  - temp_store(session_id) / folder_store (있다면)                               │
│  - chunk_text / embedding / search(top_k, caps)                                 │
│  - cache/ttl, clear_temp_session, delete/cleanup                               │
│  - (upload) chunking_pipeline + workspace_rag_store 저장                         │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ rag_context + sources
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                      (7) PROMPT ASSEMBLY (L5)                                  │
│  core/agent_prompts.py                                                         │
│  - BASE_TEMPLATE(mode) + memories + history + rag (섹션 단위 drop)              │
│  - required sandbox phrase check (template_mode)                               │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ final system_prompt/user_prompt
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                      (8) OUTPUT / RESPONSE                                     │
│  - answer (assistant) + state_info + selected_mode + rag_used                   │
│  - UI에 표시 / 로그 기록                                                       │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ finish + post-processing
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                (9) MEASUREMENT + LOGGING PIPELINE (L1)                          │
│  core/metrics_collector.py (async queue + flush)                                │
│  - TTFT_MS / TTFT_MEASURED / TPS / TPS_APPROX / TOKENS_IN/OUT / INFER_MS        │
│  - OBSERVATION_VIOLATION count                                                  │
│  -> infra/memory_database.py: performance_metrics                               │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                  (10) MEMORY DB / EXPERIENCE LEDGER (L7)                       │
│  infra/memory_database.py                                                      │
│  - experience_ledger: task_intent/task_hash/action_steps/final_outcome/etc      │
│  - behavior_insights: failure/success pattern                                  │
│  - evolution_logs: proposal/apply 상태                                          │
│  - goals/scheduled_tasks: 자율 목표/스케줄러                                     │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                   (11) DIAGNOSIS / KPI DASHBOARD (L1/L7)                       │
│  core/diagnosis_service.py                                                     │
│  - avg_latency_ms / tool_hit_rate / verification_coverage / error_recurrence   │
│  - p50/p95 TTFT/TPS, violation rate                                             │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│            (12) IMPROVEMENT LOOP: RECOVERY + EVOLUTION (L7)                    │
│  core/recovery_manager.py / core/log_analyzer.py / core/evolution_manager.py   │
│  - 실패 원인 분류 → 재발 방지 규칙 제안                                         │
│  - propose → guardian audit → approval_pending → apply(승인형)                 │
│  - rollback / previous_content 저장                                             │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ (optional) approved changes only
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                 (13) NEXT RUN IMPROVED BEHAVIOR                                │
│  - capability map 강화 / 성공 패턴 재사용 / recurrence 감소                    │
│  - (미래) Companion layer가 KPI 기반 “방향 제안” 제공                           │
└───────────────────────────────────────────────────────────────────────────────┘
