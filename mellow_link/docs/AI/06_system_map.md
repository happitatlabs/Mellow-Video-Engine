# Mellow-Link 시스템 맵 (자기소개서)

에이전트가 자신의 내부 구조·파일 시스템·데이터베이스를 인지하기 위한 참조 문서입니다.  
**사용자가 구조나 데이터에 대해 물을 때는 이 문서와 DB 스키마 정보를 바탕으로 사실만 답변하라.**

*Updated: 2026-02-15*

---

## 프로젝트 개요

- **프로젝트 명**: Mellow-Link (Local AI Agent)
- **역할**: 멜로우(Mellow)를 보좌하는 로컬 자율 에이전트. 대화·도구 실행·자가발전(Evolution)·자율 목표·보고서 생성 등을 수행한다.

---

## 핵심 아키텍처 (디렉터리 구조)

| 경로 | 설명 |
|------|------|
| **core/** | 뇌(agent_brain), 도구(agent_tools), 보안(security_manager), 경로(path_manager), Evolution·Guardian·목표(goal_manager)·자율에이전트(autonomous_agent) 등 핵심 로직 |
| **services/** | 알림(notification_service), LLM(llm_service), 문서(doc_service), RAG(rag_service), 비디오(video_service)·이미지(image_service), VTuber 릴레이(vtuber_relay), 진화(evolution_service) 등 서비스 레이어 |
| **utils/** | 보고서 마스킹(report_masking), 시스템 제어(system_control) 등 공통 유틸리티 |
| **workspace/** | 에이전트의 실제 작업 공간. **가장 빈번하게 사용하는 곳.** fs_util.py, 스크립트·보고서 저장 등 |
| **infra/** | 메모리 DB(memory_database), 이벤트 로거, 아카이버 등 인프라 |
| **config/** | 설정(settings), 환경 변수 로딩 |
| **docs/** | 이 문서(system_map.md)를 비롯한 설계·검증 가이드 |
| **outputs/** | 생성물(보고서 outputs/reports/, 제안서 outputs/proposals/, 비디오 outputs/videos/, 이미지 outputs/images/) |
| **scripts/** | 검증·정리 스크립트 (verify_*, wipe_moltbook_experience 등) |
| **extensions/** | 확장 (molt-identity 등) |
| **vault/** | 민감 원문 저장(보고서 도구의 하이브리드 모드). .gitignore 처리. 절대 경로 노출 금지 |

---

## 데이터베이스 구조 (Schema)

Mellow-Link는 **메모리 DB(SQLite)** 와 **Evolution 원장 DB(SQLite)** 두 개를 사용한다.

### 1. 메모리 DB (infra/memory_database.py)

파일: `data/` 또는 설정에 따른 SQLite 파일.

| 테이블 | 용도 | 주요 컬럼 요약 |
|--------|------|----------------|
| **experience_ledger** | 대화·태스크 성패 및 교훈 저장 | id, task_intent, task_hash, context_summary, action_steps, final_outcome, is_success, critique_tag, lessons_learned, embedding, created_at, **latency_ms, used_tools, error_message** |
| **tool_stats** | 도구 사용·성공률 통계 | tool_name, use_count, success_count, last_error_msg, avg_runtime_ms |
| **session_checkpoints** | 세션 상태 복원(ReAct 중단·재개) | session_id, task_intent, current_step, history_json, status, pause_reason, original_max_turns, updated_at |
| **goals** | 목표 트리(자율 목표) | id, parent_id, title, description, priority, status(TO_DO/IN_PROGRESS/DONE/FAILED), depth, created_at, updated_at |
| **behavior_insights** | 행동 로그 분석 결과(인사이트) | id, pattern_type, finding, recommendation, confidence, is_applied, is_verified_by_guardian, created_at |
| **scheduled_tasks** | 자율 스케줄러(진단·진화 트리거 등) | id, task_name, task_type, schedule_expr, args_json, next_run_at, status, last_run_at, consecutive_failures, root_goal_id, created_at |
| **performance_metrics** | 성능 자가 진단 | metric_id, category(TOOL/LATENCY/TOKEN/GOAL), value, unit, timestamp |
| **dynamic_tools** | 동적 도구 확장(Phase 4) | id, tool_name, description, code, parameters_json, author_agent_id, status, created_at |
| **evolution_logs** | 자기 수정 제안서(Evolution) | id, target_file, proposed_code, reason, diff_preview, status(DRAFT/APPROVAL_PENDING/APPLIED/REJECTED 등), previous_content, author_agent_id, created_at, updated_at, applied_at, feedback_failed, root_goal_id |
| **autonomous_work_results** | 자율 틱 작업 결과·승인 대기 | id, task_type, tools_created, info_collected, ethics_review, ethics_approved, status(PENDING/WAITING_FOR_APPROVAL/APPROVED/REJECTED/COMPLETED 등), output, created_at, updated_at |
| **api_usage_logs** | Guardian API 비용 추적 | id, provider, endpoint, token_count, cost, created_at |

### 2. Flow 모니터링 (Monitor Flow)

**별도 테이블이 아니다.** 메모리 DB의 다음 테이블을 모아 타임라인으로 제공한다.

- **CHAT**: experience_ledger (의도, 성공 여부, used_tools)
- **EVOLUTION**: evolution_logs (제안·Guardian 판결·반려 사유)
- **INSIGHT**: behavior_insights (패턴·권고)
- **GOAL**: goals (자동 생성된 목표)

`get_monitor_flow_timeline()`이 위 네 소스를 시간순으로 합쳐 플로우 이벤트 리스트를 반환한다.

### 3. Evolution 원장 DB (core/database.py)

파일: `data/evolution_ledger.db` (메모리 DB와 별도 파일).

| 테이블 | 용도 | 주요 컬럼 요약 |
|--------|------|----------------|
| **evolution_history** | Guardian 검수 이력(진화 원장) | id, proposal_id, target_file, user_request, verdict_code, audit_critique, status(SUCCESS/FAIL/REJECTED), created_at, token_usage, cost, latency |

---

## 로그(Thought 과정) 확인 방법

ReAct 루프의 **Thought(사고) → Action(도구 호출) → Observation(결과)** 는 아래에서 확인할 수 있다.

| 방법 | 위치 | 내용 |
|------|------|------|
| **1. 실시간 로그** | 서버 실행 터미널(콘솔) | 로그 레벨 INFO 이상. 매 턴마다 `[Turn N] Thought: ...`, `[Turn N] Observation: ...` 출력. |
| **2. Flow 모니터 상세** | Admin → Flow 모니터 → 이벤트 클릭 → **상세 보기(Detail)** | CHAT 타입 이벤트에서 **ReAct 단계 (Thought → Action → Observation)** 섹션으로 전체 단계 확인. (아카이버가 저장한 experience_ledger 레코드에만 action_steps가 포함됨.) |
| **3. DB 직접 조회** | 메모리 DB `experience_ledger` 테이블 | `action_steps` 컬럼(JSON). 아카이버가 저장한 레코드는 `[{ "thought", "action", "observation" }, ...]` 형태. |

- Flow 모니터 페이지: `/monitor/flow/view` (Admin 인증 필요). 타임라인에서 CHAT 이벤트를 클릭한 뒤 **상세 보기** 버튼으로 해당 이벤트의 Thought/Observation을 본다.

---

## 참조 규칙

- **구조·데이터 질문**: 추측하지 말고, 이 문서와 위 스키마만을 근거로 답변한다.
- **상세 스키마**: 필요 시 `read_docs_file("system_map.md")` 또는 `list_docs()`로 문서 목록 확인 후 `inspect_system_status` 도구로 현재 상태를 확인한다.
