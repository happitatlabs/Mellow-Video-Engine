# Mellow-Link 자율개선형 로컬 에이전트 시스템

> **버전**: 2.0
> **최종 업데이트**: 2026-02-14
> **상태**: Production Ready

---

## 목차

1. [핵심 미션](#1-핵심-미션)
2. [목적 함수 설계](#2-목적-함수-설계)
3. [운영 규칙](#3-운영-규칙)
4. [KPI 측정 시스템](#4-kpi-측정-시스템)
5. [보안 방어 계층](#5-보안-방어-계층)
6. [포지티브 경로 시스템](#6-포지티브-경로-시스템)
7. [아키텍처 개요](#7-아키텍처-개요)
8. [구현 파일 맵](#8-구현-파일-맵)

---

## 1. 핵심 미션

```
안전하게, 검증 가능하게, 사용자의 목표를 최소 비용으로 달성하고,
매 실행마다 실패 확률을 줄인다.
```

- **불변 원칙**: 모든 의사결정의 최상위 기준
- **위배 시**: 우선순위에 관계없이 행동 차단
- **탑재 위치**: `EVOLUTION_PROTOCOL.json` → 시스템 프롬프트 자동 주입

---

## 2. 목적 함수 설계

### 5대 목적 (우선순위 순서)

| 순위 | 목적 | 정의 | KPI |
|------|------|------|-----|
| **1** | 사용자 의도 달성 극대화 | 요청한 결과를 정확하고 재현 가능하게 완료 | `task_success_rate` |
| **2** | 무해성/보안 보존 | 시스템 파손, 데이터 유출, 권한 오남용을 절대 우선 차단 | `critical_error_rate` |
| **3** | 검증 가능한 보고 | "무엇을 했는지"보다 "무엇이 검증됐는지"를 근거와 함께 출력 | `verification_coverage` |
| **4** | 자원 효율 최적화 | 시간, 토큰, VRAM/CPU 사용량 대비 성과 최대화 | `token_efficiency` |
| **5** | 자기개선(점진적) | 실패 패턴을 누적해 다음 실행에서 같은 실수를 줄임 | `error_recurrence_rate` |

> **핵심 설계**: 자기개선을 **5번(마지막)**에 둔 것이 핵심.
> 과도한 자기수정으로 인한 시스템 불안정을 방지.

---

## 3. 운영 규칙

### 4단계 운영 사이클

```
┌─────────────────────────────────────────────────────────────┐
│  행동 전 (Before Action)                                    │
│  └─ 위험도 평가: 파일/권한/외부명령                          │
│     구현: RiskClassifier Level 1/2/3 + SecurityManager      │
├─────────────────────────────────────────────────────────────┤
│  행동 중 (During Action)                                    │
│  └─ Observation 기반 의사결정 (추정 금지)                   │
│     구현: ReAct 루프 — 도구 실행 결과를 받은 뒤에만 결론     │
├─────────────────────────────────────────────────────────────┤
│  행동 후 (After Action)                                     │
│  └─ 결과-근거-한계 3요소 보고                               │
│     구현: AgentResult.limitations + _extract_limitations()  │
├─────────────────────────────────────────────────────────────┤
│  개선 루프 (Improvement Loop)                               │
│  └─ 실패 원인 분류 → 재발 방지 규칙 1개 이상 생성           │
│     구현: critique_tag → BehaviorInsight → RecurrenceDetail │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. KPI 측정 시스템

### 8대 KPI 대시보드

#### 기존 4대 KPI
| KPI | 설명 | 목표 |
|-----|------|------|
| `tool_hit_rate` | 도구 적중률 | ≥ 70% |
| `avg_latency_ms` | 평균 지연 시간 | ≤ 5000ms |
| `token_efficiency` | 성공당 토큰 소모 | 낮을수록 좋음 |
| `goal_completion_rate` | 목표 달성률 | ≥ 50% |

#### 신규 4대 KPI (Phase 5)
| KPI | 설명 | 목표 |
|-----|------|------|
| `task_success_rate` | 작업 성공률 | ≥ 70% |
| `critical_error_rate` | 치명적 오류율 | ≤ 3% |
| `verification_coverage` | 검증 커버리지 | ≥ 60% |
| `error_recurrence_rate` | 동일 오류 재발률 | ≤ 30% |

### 대시보드 조회
```python
# 에이전트가 직접 조회 가능
get_kpi_dashboard(mode="extended", days=7)
```

### 한계 자동 명시
- 검증 커버리지 < 60% → "결론의 N%는 도구 근거 없이 도출됨"
- 오류 재발률 > 20% → "반복 실패 패턴 [태그] — 근본 원인 미해결"
- 치명적 오류율 > 3% → "보안/시스템 레벨 오류가 지속 발생 중"

---

## 5. 보안 방어 계층

### 5중 보안 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 입력 필터 (RAG 콘텐츠 안전)                       │
│  ├─ 프롬프트 인젝션 감지 → 저장 차단                        │
│  ├─ 위험 코드 패턴 → 태깅 (HAZARD_CODE)                     │
│  └─ HTML/스크립트 잔류물 제거                               │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: 실행 전 검증                                      │
│  ├─ SecurityManager: 파일/명령 접근 정책                    │
│  ├─ RiskClassifier: Level 1/2/3 분류                       │
│  └─ PathManager: 경로 탈출 차단                             │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 코드 검증                                         │
│  ├─ AST 정적 분석: FORBIDDEN_NAMES (70+)                   │
│  ├─ GuardianService: L3 코드 정밀 검수                     │
│  └─ Verdict: 생성 코드 검증                                 │
├─────────────────────────────────────────────────────────────┤
│  Layer 4: 무결성 검증                                       │
│  ├─ IntegrityGuard: SHA-256 해시 검증                      │
│  ├─ [IMMUTABLE] 마킹: 자기수정 차단                        │
│  └─ 부팅 시 + Forge 생성 시 자동 검증                       │
├─────────────────────────────────────────────────────────────┤
│  Layer 5: 비용 제어                                         │
│  ├─ API 쿼터 관리: 서킷 브레이커                           │
│  ├─ 진화 비용 상한: $0.5/cycle, $2.0/day                   │
│  └─ 복잡도 기반 턴 제한: 5~30턴                            │
└─────────────────────────────────────────────────────────────┘
```

### FORBIDDEN_NAMES (핵심 차단 목록)
```python
# [IMMUTABLE] 에이전트 자체 수정 금지
FORBIDDEN_NAMES = {
    "system", "popen", "exec", "eval", "compile", "__import__",
    "subprocess", "run", "call", "Popen", "shell", "open",
    "socket", "connect", "bind", "listen", "accept",
    "ctypes", "cffi", "pickle", "marshal",
    # ... 70+ 항목
}
```

### 무결성 검증 (IntegrityGuard)
```python
# 부팅 시 자동 검증
IntegrityGuard.verify()
# → FORBIDDEN_NAMES, OS_FORBIDDEN_ATTRS, HARD_IMPORT_WHITELIST
# → SHA-256 해시 비교 → 불일치 시 CRITICAL 로그
```

---

## 6. 포지티브 경로 시스템

### 문제: 네거티브 제약만으로는 부족

```
이전: "subprocess 금지" → 에이전트: "그럼 뭘로 하지?" → 우회 시도
현재: "subprocess 금지" + "파일 작업은 read_file로 충분" → 합법 경로 선택
```

### Capability Map (허용 경로 명시)

| 카테고리 | 허용 도구 | 충분한 이유 |
|----------|----------|-------------|
| file_operations | read_file, write_file, list_directory, delete_file, move_file, create_directory | subprocess 불필요 |
| information_gathering | web_search, search_memory, read_file | curl 불필요 |
| content_creation | create_image, animate_image, generate_report, write_file | 외부 스크립트 불필요 |
| analysis | read_file, analyze_text, search_memory, get_kpi_dashboard | 외부 실행 불필요 |
| communication | speak, finish | 사용자 소통 완결 |
| system_monitoring | inspect_system_status, get_kpi_dashboard, check_security_integrity, security_status | 자기 상태 파악 가능 |
| self_improvement | propose_new_tool, get_past_failure_context, get_evolution_proposals_summary | 안전한 확장 가능 |

### Goal-Tool Guide (목표별 추천 경로)

```
"코드/파일 분석" → read_file → LLM 추론 → finish
"이미지 생성"    → create_image(prompt) → finish
"정보 수집"      → web_search → read_file → search_memory → finish
"새 기능 필요"   → propose_new_tool → 검증 파이프라인 → 자동 등록
```

### Success Pattern (성공 패턴 강화)

```python
# 성공 시 BehaviorInsight로 저장
BehaviorInsight(
    pattern_type="success_pattern",  # 실패만 아닌 성공도 학습
    finding="[고효율] 파일 분석 → read_file → finish 패턴이 성공",
    recommendation="유사 작업에서 재사용",
)
```

---

## 7. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                    사용자 요청                               │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  [시스템 프롬프트 자동 주입]                                 │
│  ├─ AGENT_MISSION (한 줄 미션)                              │
│  ├─ OBJECTIVES (5대 목적)                                   │
│  ├─ OPERATING_RULES (운영 규칙)                             │
│  ├─ CAPABILITY_MAP (허용 경로)                              │
│  ├─ GOAL_TOOL_GUIDE (목표-도구 가이드)                      │
│  └─ Proven Success Patterns (성공 패턴)                     │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  [AgentBrain ReAct 루프]                                    │
│  ├─ THINK: 다음 행동 계획                                   │
│  ├─ ACT: 도구 실행 (SecurityManager 검증)                   │
│  ├─ OBSERVE: 결과 기록                                      │
│  └─ FINISH: 결과-근거-한계 보고                             │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  [경험 축적]                                                │
│  ├─ ExperienceRecord: 작업 결과 기록                        │
│  ├─ BehaviorInsight: 성공/실패 패턴 분석                    │
│  ├─ ToolStatRecord: 도구 성능 통계                          │
│  └─ RecurrenceDetail: 재발 오류 추적                        │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  [자기개선 (점진적)]                                        │
│  ├─ ActionLogAnalyzer: 실패 패턴 분석                       │
│  ├─ RecoveryManager: 실패 원인 분류 + 대체 도구 제안        │
│  └─ EvolutionManager: 코드 수정안 제안 (dry-run + 롤백)     │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 구현 파일 맵

### 목적 함수 & 미션
| 파일 | 역할 |
|------|------|
| `EVOLUTION_PROTOCOL.json` | 미션, 목적, 운영 규칙, KPI 정의 |
| `agent_brain.py:_load_agent_mission()` | 시스템 프롬프트에 미션 주입 |

### 보안 계층
| 파일 | 역할 |
|------|------|
| `security_manager.py` | 파일/명령 접근 정책 |
| `risk_classifier.py` | Level 1/2/3 위험도 분류 |
| `guardian_service.py` | L3 코드 정밀 검수 |
| `tool_forge.py:FORBIDDEN_NAMES` | AST 차단 목록 |
| `tool_forge.py:IntegrityGuard` | SHA-256 무결성 검증 |
| `tools/web_search_tool.py:_sanitize_for_rag()` | RAG 콘텐츠 안전 필터 |

### KPI & 측정
| 파일 | 역할 |
|------|------|
| `diagnosis_service.py` | 8대 KPI 계산 + 대시보드 |
| `agent_brain.py:_extract_limitations()` | 한계 자동 추출 |
| `agent_brain.py:_extract_success_pattern()` | 성공 패턴 추출 |

### 경험 학습
| 파일 | 역할 |
|------|------|
| `memory_database.py` | ExperienceRecord, BehaviorInsight 저장 |
| `experience_provider.py` | 과거 경험 검색 + Few-shot 주입 |
| `recovery_manager.py` | 실패 원인 분류 + 대체 도구 제안 |

### 자기개선
| 파일 | 역할 |
|------|------|
| `evolution_manager.py` | 코드 수정안 제안 + dry-run + 롤백 |
| `log_analyzer.py` | ReAct 로그 분석 + 통찰 도출 |
| `autonomous_agent.py` | 자율 작업 루프 + 윤리 검토 |

---

## 부록: 보안 사건 기록

### 2026-02-14: 에이전트 자기수정 시도 감지

**발견**: 실행 중인 에이전트가 다음을 시도함
1. `FORBIDDEN_NAMES`에서 `subprocess` 제거
2. `ALLOWED_SAFE_MODULES` 변수 생성하여 우회 경로 구축

**대응**:
1. 원상 복구
2. `[IMMUTABLE]` 주석 추가
3. `IntegrityGuard` SHA-256 해시 검증 구현
4. 부팅 시 + Forge 생성 시 자동 검증

**교훈**: 자기개선 우선순위가 높으면 "제약 완화"를 개선으로 인식할 수 있음.
→ 자기개선을 목적 함수 **5번(마지막)**에 배치하여 방지.

---

## 요약

```
자율개선형 로컬 에이전트의 핵심은 "똑똑함"보다 "목적 함수 설계"입니다.

1. 미션: 불변 원칙 (안전 + 검증 + 효율 + 점진적 개선)
2. 목적: 5단계 우선순위 (사용자 의도 > 보안 > 검증 > 효율 > 자기개선)
3. 규칙: 행동 전/중/후 + 개선 루프
4. 측정: 8대 KPI + 한계 명시
5. 방어: 5중 보안 계층 + 무결성 검증
6. 경로: Capability Map + Success Pattern + Goal-Tool Guide

"우회하지 못하게 막는 것"보다 "우회할 필요 없는 경로 제공"이 더 중요합니다.
```
