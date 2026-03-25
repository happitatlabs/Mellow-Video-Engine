Mellow-Link 레이어 시스템 맵 (Below → Above)

┌──────────────────────────────────────────────────────────────────────────┐
│ L8. 동반자 레이어 (Strategy Companion)                                   │
│  - 상태 요약(에너지/집중/리스크), 질문형 제안/승인형 행동                 │
│  - (아직 “상층 앱” 단계, 아래 레이어 위에 얹는 UI/UX)                    │
│  후보: core/companion_*.py (향후) / services/notification_service.py(향후)│
└───────────────▲──────────────────────────────────────────────────────────┘
                │ relies on KPI+insights+policies
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L7. 학습/개선 레이어 (SBMA / Recurrence / KPI Trigger)                    │
│  - recurrence 감소, success_pattern 강화, 안전한 자기개선(제안/승인/롤백) │
│  core/evolution_manager.py  core/recovery_manager.py  core/log_analyzer.py│
│  infra/memory_database.py (experience_ledger/behavior_insights/recurrence │
│                          /evolution_logs/dynamic_tools/performance_metrics)│
└───────────────▲──────────────────────────────────────────────────────────┘
                │ requires observation-grounded logs
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L6. 에이전트 레이어 (ReAct / Observation-first)                           │
│  - THINK→ACT(tool)→OBSERVE→FINISH, 추정 금지(모드별 강제), 보고서/한계     │
│  core/agent_brain.py   core/agent_parsers.py   core/agent_prompts.py      │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ calls LLM + Tools under security
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L5. 추론/대화 레이어 (LLM + Prompt/Mode + Context Budget)                 │
│  - fast/thinking/research, 프롬프트 템플릿/슬리밍, n_ctx 예산, RAG 주입 상한│
│  services/llm_service.py   core/agent_prompts.py   config/settings.py     │
│  (Metrics Hook) core/metrics_collector.py                                 │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ uses tools (RAG/FS/etc.)
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L4. 도구 레이어 (Tools)                                                   │
│  - file ops / RAG / (옵션)web / (향후)robot adapters                       │
│  core/tool_registry.py   core/agent_tools_*.py   core/agent_tools_base.py │
│  services/rag_service.py (검색/인덱싱)                                     │
│  services/chunking_pipeline.py  services/workspace_chunk_runner.py         │
│  infra/workspace_rag_store.py  (workspace_rag.db, chunk_feedback)          │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ executed under FSM + security gates
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L3. 실행 레이어 (FSM / Orchestrator)                                      │
│  - SystemState: IDLE↔TEXT↔IMAGE↔ERROR, GPU/VRAM 배타, 큐/락/쿨다운/셧다운   │
│  core/orchestrator.py   core/states.py   (events/handlers, task queue)     │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ relies on policy & integrity
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L2. 경계 레이어 (Security / Policy / Integrity / Guardian)                │
│  - sandbox 경로, tool whitelist, NO_AUTO_EVOLUTION, fail-closed, 무결성   │
│  core/security_manager.py   core/path_manager.py   core/workspace_sandbox.py│
│  core/guardian_service.py   core/tool_forge.py(IntegrityGuard/FORBIDDEN)   │
│  docs/system_map.md  EVOLUTION_PROTOCOL.json (미션/목적/룰 자동 주입)      │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ instrumented by metrics/logs
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L1. 관측 레이어 (Measurement / Metrics / KPI)                             │
│  - TTFT/TPS/INFER_MS/TOKENS, recurrence/violations, 대시보드               │
│  core/metrics_collector.py   core/diagnosis_service.py                     │
│  infra/memory_database.py (performance_metrics/api_usage_logs/...)         │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ grounded on isolation (test vs prod)
┌───────────────┴──────────────────────────────────────────────────────────┐
│ L0. 환경 격리 레이어 (Isolation / Paths / Storage)                         │
│  - workspace/ (실작업)   outputs/ (생성물)   vault/ (민감, 경로노출 금지)  │
│  - (테스트) mellow_link_test/workspace_test, outputs_test, 별도 DB 권장     │
│  - scripts/ (verify/wipe)   docs/ (설계/가이드)                             │
└──────────────────────────────────────────────────────────────────────────┘
