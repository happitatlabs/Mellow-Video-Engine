┌───────────────────────────────────────────────────────────────────────────────┐
│ (0) USER / UI                                                                  │
│  - chat 요청, 파일 업로드, regenerate/feedback, (향후) 자율 tick 트리거        │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ request_id/session_id/mode
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (1) ENTRY / ROUTER / API                                                       │
│  - main.py / routers/chat.py / routers/folders.py                              │
│  - settings 로드: mellow_link/config/settings.py                               │
│  - MetricsCollector.start(request_id)                                          │
│  저장: (아직 없음)                                                             │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (2) ORCHESTRATOR / FSM                                                         │
│  - core/orchestrator.py / core/states.py                                       │
│  - state: IDLE↔TEXT↔IMAGE↔ERROR (VRAM 배타)                                    │
│  저장: (상태는 메모리/DB 둘 다 가능)                                           │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (3) SECURITY + PATH GATES                                                      │
│  - core/security_manager.py / core/path_manager.py / core/workspace_sandbox.py │
│  - tool whitelist, sandbox root, NO_AUTO_EVOLUTION, fail-closed                │
│  저장: 차단/제안은 outputs/proposals/ 또는 DB evolution_logs로                 │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ allowed?
     ┌──────────┴──────────┐
     │                     │
     ▼                     ▼
(3a) BLOCK             (4) AGENT BRAIN (ReAct)
- 결과 메시지 생성      - core/agent_brain.py / core/agent_parsers.py
- (선택) proposal 생성  - THINK→ACT→OBSERVE→FINISH
- 저장 위치:            - action_steps(관측 포함) 기록
  outputs/proposals/    - (모드별) observation strict
  infra/memory_database.py:evolution_logs
                        - 저장 위치: infra/memory_database.py:experience_ledger

                          ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (5) TOOL REGISTRY + TOOLS                                                      │
│  - core/tool_registry.py                                                      │
│  - core/agent_tools_filesystem.py 등                                          │
│  파일 읽기/쓰기/리스트는 아래 경로로 제한                                     │
│   - workspace/: mellow_link/workspace/   (실작업)                             │
│   - outputs/:   mellow_link/outputs/     (생성물)                             │
│   - vault/:     mellow_link/vault/       (민감 원문, 경로 노출 금지)           │
│  저장: 도구 실행 결과는 ReAct observation으로 experience_ledger에             │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ tool call 중 RAG 필요?
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (6) RAG SERVICE (Temp + Workspace + Folder)                                    │
│  - services/rag_service.py                                                    │
│                                                                              │
│  (6A) Temp Session RAG                                                        │
│   - 저장: 메모리(temp_store[session_id]) + (있다면) 임시 캐시(_search_cache) │
│   - 삭제: clear_temp_session(session_id)                                      │
│                                                                              │
│  (6B) Workspace RAG (코드/문서 청킹/임베딩)                                   │
│   - 입력: mellow_link/workspace/*                                             │
│   - 파이프라인: services/chunking_pipeline.py                                │
│              + services/workspace_chunk_runner.py                             │
│   - 저장 DB: mellow_link/outputs/workspace_rag.db                             │
│     테이블: workspace_chunks, chunk_feedback                                  │
│     (infra/workspace_rag_store.py)                                            │
│                                                                              │
│  (6C) Folder/Docs RAG (영구 문서 저장)                                        │
│   - 저장 폴더: mellow_link/data/uploads/... (설정에 따라)                     │
│   - 인덱싱: rag_service.process_document(...)                                 │
│   - 저장: 벡터/청크/메타 (구현 방식에 따라)                                   │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ rag_context + sources
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (7) PROMPT ASSEMBLY                                                            │
│  - core/agent_prompts.py                                                      │
│  - BASE_TEMPLATE(mode) + memories + history + rag                              │
│  - 섹션 단위 드롭: RAG → history → memories → base 유지                        │
│  - template_mode 시 sandbox phrase 검사                                        │
│  저장: (프롬프트 자체는 보통 저장 안 함, 필요 시 experience_ledger에 요약)   │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (8) LLM SERVICE                                                                │
│  - services/llm_service.py                                                    │
│  - generate_stream: TTFT 측정 (on_first_token)                                │
│  - chat(non-stream): INFER_MS + TPS_APPROX                                    │
│  - mode별 모델/num_ctx 적용                                                   │
│  저장: 없음(추론 결과는 다음 단계에서 DB/로그로)                              │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ output chunks / final answer
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (9) RESPONSE + SESSION RECORD                                                  │
│  - 최종 답변 반환                                                              │
│  - (가능) 세션/메시지 저장                                                     │
│  저장(대표 DB): infra/memory_database.py (experience_ledger)                  │
│                                                                              │
│  experience_ledger 주요 필드:                                                  │
│   - task_intent/task_hash/context_summary/action_steps/final_outcome           │
│   - is_success/critique_tag/lessons_learned                                    │
│   - used_tools/error_message/latency_ms                                        │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ metrics push (non-blocking)
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (10) METRICS COLLECTOR (async)                                                 │
│  - core/metrics_collector.py                                                  │
│  - in-memory queue (max size) + background flush                              │
│  - 저장 DB: infra/memory_database.py:performance_metrics                       │
│                                                                              │
│  기록되는 category 예:                                                        │
│   - TTFT_MS / TTFT_MEASURED                                                   │
│   - TPS / TPS_APPROX                                                          │
│   - TOKENS_IN / TOKENS_OUT                                                    │
│   - INFER_MS                                                                  │
│   - OBSERVATION_VIOLATION                                                     │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (11) DIAGNOSIS / KPI DASHBOARD                                                 │
│  - core/diagnosis_service.py                                                  │
│  - DB 집계: performance_metrics + experience_ledger                            │
│  산출 KPI 예:                                                                 │
│   - avg_latency_ms, tool_hit_rate                                             │
│   - verification_coverage                                                     │
│   - error_recurrence_rate                                                     │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ triggers / proposals
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (12) IMPROVEMENT LOOP (Controlled)                                             │
│  - core/log_analyzer.py / core/recovery_manager.py                             │
│  - core/evolution_manager.py (propose → audit → approve → apply/rollback)      │
│  저장 DB: infra/memory_database.py:evolution_logs / behavior_insights / goals  │
│  (별도) core/database.py: data/evolution_ledger.db (Guardian 원장)             │
└───────────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ (13) NEXT RUN                                                                   │
│  - 성공 패턴 재사용, 실패 재발률 감소, 안전 경로 강화                           │
│  - (미래) Companion 레이어가 KPI 기반 상태 요약/방향 제안                      │
└───────────────────────────────────────────────────────────────────────────────┘
