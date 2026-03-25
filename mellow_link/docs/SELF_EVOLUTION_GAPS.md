# Mellow-Link 자가발전(자가 진화) — 현재 상태와 보완 항목

**목적:** “자가발전하는 Mellow-Link”를 위해 이미 있는 것과 아직 필요한 것을 정리.

---

## 1. 이미 갖춰진 것 (현재 구현)

| 항목 | 설명 | 비고 |
|------|------|------|
| **진화 파이프라인** | Tower(판단) → Verdict(수정안) → Audit(검수) → 적용/롤백 | `EvolutionManager`, `EvolutionService` |
| **진화 트리거** | 주기적으로 “진화할까?” 판단 후 `run_evolution_cycle` 호출 | `evolution_trigger.run_evolution_tick`, Scheduler 등록 |
| **자동 적용 범위** | Audit 통과 시 `workspace/` 등은 승인 없이 자동 적용 | `EVOLUTION_PROTOCOL.json` → `auto_apply_scope.path_prefixes` |
| **적용 후 검증** | 적용 직후 py_compile·smoke 테스트, 실패 시 롤백 | `post_apply_verify` |
| **비용/재시도 제한** | 단일·일일 비용 상한, 재시도 횟수, 쿨다운 | `EVOLUTION_PROTOCOL.json` (retry, cost_guard, evolution_rules) |
| **경험 기록** | 채팅/에이전트 완료 시 경험 저장 (성패·교훈) | `MemoryArchiver` → `experience_ledger` (Archiver 활성화 시) |
| **행동 통찰** | 경험·도구 통계 분석 → 개선 권고를 `behavior_insights`에 저장 | `ActionLogAnalyzer`, N회 완료마다 `_trigger_analysis` |
| **진단** | `experience_ledger`·`tool_stats` 기반 건강도 평가 | `DiagnosisService` |
| **트리거 입력** | 과거 Evolution 실패, 최근 통찰, 진단 요약, 로그 → Tower에 전달 | `run_evolution_tick` 내 컨텍스트 수집 |

즉, **“언제 진화할지 판단 → 수정안 생성 → 검수 → (범위 내) 자동 적용 → 검증/롤백”**까지의 루프는 코드상 존재한다.

---

## 2. 자가발전을 위해 “더 필요한 것”

### 2.1 설정/기동 (필수)

| 보완 항목 | 현재 | 목표 |
|-----------|------|------|
| **스케줄러 기동** | `ENABLE_SCHEDULER` 기본 꺼짐 | `ENABLE_SCHEDULER=true` 로 Scheduler 기동 |
| **진화 트리거 활성화** | `evolution_trigger.enabled: false`, env 미설정 | `EVOLUTION_PROTOCOL.json`에서 `evolution_trigger.enabled: true` 또는 `ENABLE_EVOLUTION_TRIGGER=1` |
| **경험 아카이빙** | AgentBrain에서 선택적 | 채팅/에이전트 플로우에서 메모리 아카이빙 활성화해 `experience_ledger` 채우기 |

**정리:** 위 세 가지가 켜져 있어야 “주기적 판단 → 경험/통찰/진단 기반 진화”가 실제로 돌아간다.

---

## 2.1-A. 운영 스위치 우선순위

운영 스위치는 아래 순서로 검토한다.

1. **메모리 아카이빙 활성화 검토**
2. **ENABLE_SCHEDULER 검토**
3. **ENABLE_EVOLUTION_TRIGGER 검토**

우선순위 이유:

- **메모리 아카이빙**은 turn 완료 후 기록 경로만 안정적이면 되고, 시스템을 스스로 흔드는 힘이 가장 약하다.
- **Scheduler**는 주기 실행이라 관측 가능성만 있으면 상대적으로 다루기 쉽지만, tick 폭주·중복 실행·롤백 절차가 필요하다.
- **Evolution Trigger**는 비용, 실패, 자동 적용 범위, 예측 불가능한 상태 변경이 가장 많이 몰리므로 마지막이다.

---

## 2.1-B. 운영 스위치 활성화 조건

운영 스위치는 코드보다 **켜는 조건**을 먼저 고정한다.

### 메모리 아카이빙

| 항목 | 기준 |
|------|------|
| 운영 기본값 | **off** 로 취급 |
| 현재 코드 상태 | 별도 env 스위치는 아직 없음. `AgentBrain(enable_memory_archiving=True)` + archiver 초기화 성공 시 경로가 살아난다. |
| 활성화 환경 | 로컬, 스테이징 우선 |
| 롤백 방법 | `enable_memory_archiving=False`로 생성하거나 archiver 초기화 경로 차단 |
| 자동 중지 조건 | 기록 실패 연속 발생 시 비활성화 검토. 최소 기준: 기록 실패가 사용자 응답 성공/실패 판단과 섞이면 안 됨 |
| 추가 조건 | 아카이빙 실패가 turn 성공 자체를 깨지 않아야 하며, `last_error/degraded` 기준과 분리되어야 함 |

코드 기준 확인:

- 아카이빙 실패는 메인 플로우를 깨지 않도록 `warning`만 남긴다: `core/agent_experience.py`
- 경험 장부 기록도 비동기이며 실패 시 `debug` 로그만 남긴다: `core/agent_experience.py`

### 스케줄러

| 항목 | 기준 |
|------|------|
| 기본값 | **off** |
| 현재 코드 상태 | `Settings.enable_scheduler=False` |
| 활성화 환경 | 단일 인스턴스 우선 |
| 롤백 방법 | `ENABLE_SCHEDULER=0` 또는 env off 후 재시작 |
| 자동 중지 조건 | tick 실패율 임계 초과, 중복 실행 감지, 시작/중지 루프 발생 시 off 유지 |
| 추가 조건 | tick 로그와 중복 실행 방지 근거가 있어야 함 |

코드 기준 확인:

- startup에서 `settings.enable_scheduler`가 true일 때만 `SchedulerService`를 시작한다: `main.py`

### Evolution Trigger

| 항목 | 기준 |
|------|------|
| 기본값 | **off** |
| 현재 코드 상태 | `Settings.enable_evolution_trigger=False`, `EVOLUTION_PROTOCOL.json`도 별도 게이트 사용 |
| 활성화 환경 | 스테이징 한정 |
| 롤백 방법 | `ENABLE_EVOLUTION_TRIGGER=0` 또는 프로토콜의 `evolution_trigger.enabled=false` |
| 자동 중지 조건 | 비용 한도 초과, 연속 실패, patch 적용 실패, 검증 실패 시 off 유지 |
| 추가 조건 | 자동 적용 범위와 비용 상한, 실패 시 중단 규칙이 먼저 고정되어야 함 |

코드 기준 확인:

- 설정 기본값은 off다: `config/settings.py`
- 실제 자가발전 tick은 Scheduler 경유로 연결된다.

---

## 2.1-C. 1차 검토 범위

### O-Review A. 메모리 아카이빙 검토

- runtime turn 완료 후 경험 축적 경로가 실제로 존재하는지 확인
- 기록 실패가 사용자 응답 경로와 `degraded` 판단을 오염시키지 않는지 확인
- 최소 로그와 최소 테스트 추가

현재 확인 결과:

- `mellow_link`의 `AgentBrain` 경로에는 경험 축적 경로가 있다.
  - `archive_experience()`와 `record_experience_ledger()`가 존재한다.
  - 실패 시 예외를 다시 올리지 않고 로그만 남긴다.
- `mellow_chat_runtime`의 `/runtime/turn` 경로에는 별도 메모리 아카이빙 연결이 아직 없다.
  - 즉, 현재 Runtime API 성공 turn이 곧바로 experience ledger 적재로 이어지지는 않는다.
  - Runtime에 메모리 아카이빙을 붙이려면 별도 브리지 작업이 필요하다.
- 현재 기준으로 아카이빙 실패와 Runtime `degraded/last_error`는 분리되어 있다.
  - 아카이빙 실패는 `ExperienceHelper` 경고 로그로만 남고,
  - Runtime `degraded`는 Runtime 요청/응답 실패 축에서만 관리된다.

### O-Review B. 스케줄러 검토

- tick 로그 존재 확인
- 중복 실행 방지 확인
- 롤백 절차 문서화
- 기본값은 계속 off 유지

현재 확인 결과:

- 기본값은 여전히 `off`다. `Settings.enable_scheduler=False`.
- tick 로그는 이미 존재한다.
  - 시작/정지
  - pending task 발견
  - task 실행/완료/실패
- 중복 방지는 두 층이다.
  - 서비스 중복 기동: `_is_running` 가드
  - 기본 예약 태스크 중복 등록: task name 중복 검사
- 롤백은 `ENABLE_SCHEDULER=0` 후 재시작을 기본 절차로 유지한다.

### O-Review C. Evolution Trigger 검토

- 지금은 문서와 가드만 정리
- 실제 활성화는 보류 가능

현재 확인 결과:

- 기본값은 `off`다. `Settings.enable_evolution_trigger=False`.
- trigger off 시 `run_evolution_tick()`은 `TRIGGER_DISABLED`로 즉시 반환한다.
- Guardian/API 게이트가 닫혀 있으면 Tower 호출 전에 `DISABLED/AIRGAP_BLOCK`으로 반환한다.
- 따라서 현재 단계에서는 문서/가드 유지가 맞고, 실제 활성화는 계속 보류한다.

---

### 2.2 데이터 루프 보강 (권장)

| 보완 항목 | 설명 |
|-----------|------|
| **통찰 생성이 트리거에 선행** | 통찰은 “태스크 N회 완료 시”에만 생성됨. 트리거 주기가 짧으면 insights가 비어 있을 수 있음. → 스케줄러에 “주기적 LogAnalyzer 실행” 태스크를 넣어, 트리거 tick 전에 통찰이 쌓이도록 하거나, 트리거 주기와 분석 주기 정합성 맞추기. |
| **진화 결과 피드백** | “이번에 이 제안을 적용했다”는 기록을 evolution 로그/DB에 남기고, 다음 Tower 판단 시 “방금 반영한 개선은 제외”하거나 “동일 실패 반복” 억제에 활용. (현재는 과거 실패만 주입.) |

---

### 2.3 목표 주도 진화 (선택)

| 보완 항목 | 설명 |
|-----------|------|
| **GoalManager와 트리거 연동** | 현재 진화 트리거는 “과거 실패 + 통찰 + 진단 + 로그”만 사용. `GoalManager`/`GoalPlanner`와 연동하면 “미완 목표 중 진화로 해결 가능한 것”을 Tower에 넣어 목표 주도 자가발전이 가능해짐. |
| **자기 설정 목표** | “테스트 커버리지 올리기”, “특정 API 에러율 낮추기” 등 목표를 시스템이 스스로 설정·갱신하고, 그에 맞는 `user_request`를 Tower가 생성하도록 확장. |

---

### 2.4 안전·운영 (선택)

| 보완 항목 | 설명 |
|-----------|------|
| **자동 적용 범위** | 현재 `auto_apply_scope`는 `workspace` 위주. `services/`, `custom_tools/` 등을 넣으면 더 넓은 자가발전이 가능하지만, 위험도에 맞게 단계적으로 확대하는 것이 좋음. |
| **알림 연동** | 자동 적용·롤백·비용 초과 시 `NotificationService`/Telegram 등으로 알림하면 무인 운영 시 점검에 유리함. |
| **core/ 수정 정책** | 프로토콜상 `core/`는 진화 대상에서 제외. “자가발전” 범위를 core까지 넣을지, 아니면 workspace·services만 할지 정책적으로 고정해 두는 것이 좋음. |

---

## 3. 체크리스트 (자가발전 “최소 동작”용)

- [ ] **ENABLE_SCHEDULER**=true (또는 동등 설정)
- [ ] **ENABLE_EVOLUTION_TRIGGER**=1 또는 `EVOLUTION_PROTOCOL.json` → `evolution_trigger.enabled: true`
- [ ] **메모리 아카이빙** 활성화 → `experience_ledger` 적재
- [ ] (선택) 스케줄러에 **주기적 LogAnalyzer** 등록 → 트리거 전에 `behavior_insights` 보강
- [ ] (선택) **진화 적용 결과**를 다음 Tower 판단 입력에 반영
- [ ] (선택) **GoalManager**와 진화 트리거 연동 → 목표 주도 자가발전

위 항목을 적용하면, “경험·통찰·진단·로그를 바탕으로 주기적으로 진화 여부를 판단하고, workspace 등에서 자동 적용까지 하는 Mellow-Link” 형태의 자가발전에 가까워진다.
