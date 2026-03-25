# Dev Console UX 설계 문서 v1.0

> **목적**: 에이전트 내부 동작 검증, 모드 분기 확인, 도구 호출 추적, 성능 분석, 디버깅
> **대상 사용자**: 개발자, 시스템 관리자

---

## 1. 와이어프레임

### 1.1 전체 레이아웃 (Desktop 1440px 기준)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ HEADER: Mellow-Link Dev Console                        [🔄 Refresh] [⚙️ Settings]│
├───────────────────────┬─────────────────────────────────────────────────────────┤
│                       │  TABS: [Timeline] [Events] [Raw Data] [Performance]     │
│   RUN LIST PANEL      ├─────────────────────────────────────────────────────────┤
│   (w: 320px)          │                                                         │
│                       │                    DETAIL PANEL                         │
│   ┌─────────────────┐ │                    (flex: 1)                            │
│   │ 🔍 Filter       │ │                                                         │
│   ├─────────────────┤ │                                                         │
│   │ run_abc123      │ │                                                         │
│   │ ├ mode: fast    │ │                                                         │
│   │ ├ 234ms         │ │                                                         │
│   │ └ 🟢 success    │ │                                                         │
│   ├─────────────────┤ │                                                         │
│   │ run_def456 ⚠️   │ │                                                         │
│   │ ├ mode: thinking│ │                                                         │
│   │ ├ 1,234ms       │ │                                                         │
│   │ └ 🟡 escalated  │ │                                                         │
│   ├─────────────────┤ │                                                         │
│   │ run_ghi789      │ │                                                         │
│   │ ├ mode: t-lite  │ │                                                         │
│   │ ├ 456ms         │ │                                                         │
│   │ └ 🔴 error      │ │                                                         │
│   └─────────────────┘ │                                                         │
│                       │                                                         │
├───────────────────────┴─────────────────────────────────────────────────────────┤
│ FOOTER: Connected ● | Last update: 14:32:05 | Runs: 127 | Avg: 342ms           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Detail Panel - Timeline Tab

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ TABS: [Timeline ●] [Events] [Raw Data] [Performance]                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  run_abc123                                          Total: 1,234ms             │
│  ─────────────────────────────────────────────────────────────────────────────  │
│                                                                                 │
│  Turn 1 ──●────────────────────────────────────────────────────────────────────│
│           │                                                                     │
│           ├─ [MODE] fast → thinking (escalated)                                │
│           │   └─ trigger: tool_keyword detected                                │
│           │                                                                     │
│           ├─ [TOOL] list_directory                               +156ms        │
│           │   └─ args: {"path": "/workspace"}                                  │
│           │   └─ result: 23 items                                              │
│           │                                                                     │
│  Turn 2 ──●─────────────────────────────────────────────────────────────────── │
│           │                                                                     │
│           ├─ [TOOL] read_file                                    +89ms         │
│           │   └─ args: {"path": "/workspace/main.py"}                          │
│           │   └─ result: 1,234 chars                                           │
│           │                                                                     │
│  Turn 3 ──●─────────────────────────────────────────────────────────────────── │
│           │                                                                     │
│           └─ [FINISH] success                                    +12ms         │
│               └─ summary: "분석 완료"                                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Detail Panel - Events Tab

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ TABS: [Timeline] [Events ●] [Raw Data] [Performance]                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Filter: [All ▼] [TOOL ▼] [MODE ▼] [ERROR ▼]              🔍 Search events       │
├──────────┬────────────────────────┬─────────────────────────────────┬───────────┤
│ TIME     │ EVENT                  │ DETAIL                          │ DELTA     │
├──────────┼────────────────────────┼─────────────────────────────────┼───────────┤
│ 14:32:01 │ 🚀 run_started         │ mode=fast, user="admin"         │ +0ms      │
│ 14:32:01 │ 📋 plan_created        │ 3 steps planned                 │ +45ms     │
│ 14:32:01 │ ⚡ mode_escalated      │ fast → thinking                 │ +52ms     │
│ 14:32:02 │ 🔧 tool_called         │ list_directory                  │ +156ms    │
│ 14:32:02 │ ✅ tool_success        │ 23 items returned               │ +178ms    │
│ 14:32:02 │ 🔧 tool_called         │ read_file                       │ +267ms    │
│ 14:32:02 │ ✅ tool_success        │ 1,234 chars                     │ +289ms    │
│ 14:32:03 │ ✅ todo_completed      │ "파일 분석" (1/3)               │ +456ms    │
│ 14:32:03 │ 🏁 run_finished        │ success, 3 tools used           │ +1,234ms  │
└──────────┴────────────────────────┴─────────────────────────────────┴───────────┘
```

### 1.4 Detail Panel - Raw Data Tab

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ TABS: [Timeline] [Events] [Raw Data ●] [Performance]                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│ [📋 Copy JSON] [📥 Export]                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│ ▼ Run Metadata                                                                  │
│ ┌─────────────────────────────────────────────────────────────────────────────┐ │
│ │ {                                                                           │ │
│ │   "run_id": "abc123",                                                       │ │
│ │   "selected_mode": "thinking",                                              │ │
│ │   "initial_mode": "fast",                                                   │ │
│ │   "escalated": true,                                                        │ │
│ │   "escalation_reason": "tool_keyword_detected"                              │ │
│ │ }                                                                           │ │
│ └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│ ▼ Prompt Stats                                                                  │
│ ┌─────────────────────────────────────────────────────────────────────────────┐ │
│ │   "prompt_chars": 12,456,                                                   │ │
│ │   "total_tokens": 3,456,                                                    │ │
│ │   "tools_schema_size": 8,234,                                               │ │
│ │   "max_turns": 10,                                                          │ │
│ │   "actual_turns": 3                                                         │ │
│ └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│ ▼ Routing Log                                                                   │
│ ▼ Tool Calls (3)                                                                │
│ ▼ LLM Response Chain                                                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.5 Detail Panel - Performance Tab

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ TABS: [Timeline] [Events] [Raw Data] [Performance ●]                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────┐  ┌─────────────────────────────┐              │
│  │      THIS RUN               │  │      COMPARISON (24h)       │              │
│  ├─────────────────────────────┤  ├─────────────────────────────┤              │
│  │  Total Time    1,234ms      │  │  p50         342ms          │              │
│  │  Infer Time      890ms      │  │  p95       1,456ms          │              │
│  │  Tool Time       312ms      │  │  mean        456ms          │              │
│  │  Overhead         32ms      │  │  max       3,234ms          │              │
│  └─────────────────────────────┘  └─────────────────────────────┘              │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  LATENCY BREAKDOWN                                                      │   │
│  │  ═══════════════════════════════════════════════════════════════════    │   │
│  │  LLM Inference  ████████████████████████████████░░░░░░░░  72%  890ms   │   │
│  │  Tool Execution ██████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  25%  312ms   │   │
│  │  Overhead       ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   3%   32ms   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  FLAGS & ALERTS                                                          │   │
│  │  ⚠️ p95 초과 (1,234ms > 1,200ms threshold)                              │   │
│  │  ⚡ Escalation 발생 (fast → thinking)                                    │   │
│  │  ✅ Tool call 성공률 100% (3/3)                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 컴포넌트 설명

### 2.1 Header Bar

| 요소 | 설명 |
|------|------|
| **Logo/Title** | "Mellow-Link Dev Console" - 현재 뷰 표시 |
| **Refresh Button** | 수동 새로고침 (Ctrl+R 단축키) |
| **Settings** | 자동 갱신 간격, 테마, 필터 프리셋 설정 |
| **Connection Status** | WebSocket/SSE 연결 상태 표시 |

### 2.2 Run List Panel (좌측 320px)

| 컴포넌트 | 속성 |
|----------|------|
| **Filter Bar** | 텍스트 검색 + 모드/상태 드롭다운 필터 |
| **Run Card** | 클릭 시 Detail Panel 업데이트 |
| **Status Badge** | 🟢 success / 🟡 escalated / 🔴 error / ⚪ pending |
| **Mode Tag** | `fast` `thinking` `t-lite` `research` |
| **Timing** | 총 처리 시간 (ms) |
| **p95 Flag** | ⚠️ 아이콘으로 이상치 표시 |

**Run Card 구조:**
```
┌─────────────────────────┐
│ run_abc123         🟢   │
│ ├─ mode: thinking       │
│ ├─ 1,234ms              │
│ ├─ tools: 3             │
│ └─ 14:32:05            │
└─────────────────────────┘
```

### 2.3 Detail Panel Tabs

#### Tab 1: Timeline
- **목적**: Turn 단위 시각적 흐름 추적
- **표시 항목**:
  - Turn 번호
  - Mode 변경 이벤트 (escalation 포함)
  - Tool call 발생 + 결과
  - Finish 시점

#### Tab 2: Events
- **목적**: 모든 이벤트를 시간순 테이블로 표시
- **필터**: 이벤트 타입별 (TOOL, MODE, ERROR, TODO)
- **컬럼**: TIME, EVENT, DETAIL, DELTA

#### Tab 3: Raw Data
- **목적**: JSON 원본 데이터 확인
- **섹션**:
  - Run Metadata (run_id, mode, escalation)
  - Prompt Stats (chars, tokens, schema size)
  - Routing Log (mode selection 근거)
  - Tool Calls (전체 도구 호출 기록)
  - LLM Response Chain (턴별 응답)

#### Tab 4: Performance
- **목적**: 성능 메트릭 시각화
- **표시**:
  - This Run vs 24h 비교
  - Latency Breakdown (LLM / Tool / Overhead)
  - Flags & Alerts (p95 초과, escalation, 에러)

### 2.4 Footer Bar

| 요소 | 설명 |
|------|------|
| **Connection** | `Connected ●` / `Disconnected ○` |
| **Last Update** | 마지막 데이터 갱신 시간 |
| **Run Count** | 현재 로드된 Run 수 |
| **Avg Latency** | 전체 평균 처리 시간 |

---

## 3. 정보 계층 (Information Hierarchy)

### Level 1: Glanceable (즉시 인지)
> **Run List에서 1초 내 파악 가능**

| 정보 | 시각적 표현 |
|------|------------|
| 성공/실패 | 컬러 배지 (🟢🟡🔴) |
| 모드 | 태그 (`fast` `thinking`) |
| 지연 여부 | ⚠️ 아이콘 |
| 총 시간 | 숫자 (1,234ms) |

### Level 2: Scannable (스캔 가능)
> **Detail Panel 진입 후 5초 내 파악 가능**

| 정보 | 위치 |
|------|------|
| Turn 흐름 | Timeline Tab 상단 |
| Tool 호출 수 | Timeline 노드 개수 |
| Escalation 여부 | Timeline에서 MODE 노드 |
| 주요 이벤트 | Events Tab 상위 5개 |

### Level 3: Detailed (상세 분석)
> **필요 시 Deep Dive**

| 정보 | 위치 |
|------|------|
| Tool 호출 인자/결과 | Timeline 노드 확장 |
| 전체 이벤트 로그 | Events Tab 스크롤 |
| JSON 원본 | Raw Data Tab |
| 성능 Breakdown | Performance Tab |

### Level 4: Debug (디버깅)
> **문제 해결 시에만 접근**

| 정보 | 위치 |
|------|------|
| Routing Decision Log | Raw Data > Routing Log |
| LLM Response Chain | Raw Data > LLM Response |
| Prompt 크기 상세 | Raw Data > Prompt Stats |
| Token 사용량 | Raw Data > Prompt Stats |

---

## 4. 인터랙션 패턴

### 4.1 Run 선택
```
1. Run List에서 카드 클릭
2. Detail Panel이 해당 run_id로 업데이트
3. 기본 Tab은 Timeline
4. URL 업데이트: /dev-console?run=abc123
```

### 4.2 실시간 업데이트
```
1. SSE로 새 이벤트 수신
2. 현재 선택된 run이면 Detail Panel 자동 업데이트
3. Run List에 새 run 추가 시 상단에 삽입
4. 애니메이션으로 변경 표시
```

### 4.3 필터링
```
1. Filter Bar에서 조건 선택
2. Run List 즉시 필터링 (debounce 150ms)
3. 빈 결과 시 "No runs match filter" 표시
```

### 4.4 키보드 단축키
| 키 | 동작 |
|----|------|
| `↑/↓` | Run List 이동 |
| `1-4` | Tab 전환 |
| `Ctrl+R` | 새로고침 |
| `Ctrl+C` | Raw Data 복사 |
| `Esc` | 필터 초기화 |

---

## 5. 색상 시스템

### 5.1 상태 색상
```css
--status-success: #10B981;    /* Green */
--status-warning: #F59E0B;    /* Amber */
--status-error: #EF4444;      /* Red */
--status-pending: #6B7280;    /* Gray */
--status-info: #3B82F6;       /* Blue */
```

### 5.2 모드 색상
```css
--mode-fast: #06B6D4;         /* Cyan */
--mode-thinking: #8B5CF6;     /* Purple */
--mode-thinking-lite: #A78BFA; /* Light Purple */
--mode-research: #EC4899;     /* Pink */
```

### 5.3 배경 (Dark Theme)
```css
--bg-primary: #0f0f23;
--bg-secondary: #1a1a2e;
--bg-tertiary: #2d3748;
--border: #374151;
```

---

## 6. 반응형 브레이크포인트

| 화면 | Run List | Detail Panel |
|------|----------|--------------|
| ≥1440px | 320px 고정 | flex: 1 |
| 1024-1439px | 280px 고정 | flex: 1 |
| 768-1023px | 240px 고정 | flex: 1 |
| <768px | 전체폭 (접기 가능) | 전체폭 (선택 시 표시) |

---

## 7. API 엔드포인트

```
GET  /api/dev/runs                    # Run 목록
GET  /api/dev/runs/{run_id}           # Run 상세
GET  /api/dev/runs/{run_id}/events    # 이벤트 목록
GET  /api/dev/runs/{run_id}/raw       # Raw JSON
GET  /api/dev/metrics                 # 성능 통계 (p50, p95, mean)
SSE  /api/dev/stream                  # 실시간 이벤트 스트림
```

---

## 8. 구현 우선순위 (MVP)

### Phase 1: Core (MVP)
- [ ] Run List Panel (기본 표시)
- [ ] Timeline Tab (Turn/Tool 흐름)
- [ ] 기본 필터 (mode, status)

### Phase 2: Enhanced
- [ ] Events Tab (테이블 뷰)
- [ ] Raw Data Tab (JSON 표시)
- [ ] SSE 실시간 업데이트

### Phase 3: Analytics
- [ ] Performance Tab (메트릭 시각화)
- [ ] p95 플래그 자동 감지
- [ ] Export 기능

---

## 9. 기존 구현 매핑

| 기존 파일 | Dev Console 역할 |
|-----------|-----------------|
| `flow_monitor.html` | Timeline/Events 기반 → 통합 예정 |
| `progress_ui.html` | Run List 기반 → 통합 예정 |
| `/api/admin/flow-events` | Events API → `/api/dev/runs/{id}/events` |

---

*문서 버전: 1.0*
*작성일: 2026-02-21*
*작성: Claude Code*
