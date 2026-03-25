Mellow-Link Agent UI Architecture v1
1. 설계 목적

Mellow-Link 에이전트 시스템은 동일한 실행(run_id)을
서로 다른 사용자 관점(View)에서 다르게 해석하고 보여주는 구조를 목표로 한다.

3개의 핵심 뷰를 정의한다:

Dev Console (개발자)

Operator View (운영자/작업자)

User View (일반 사용자)

핵심 원칙:

하나의 실행(run)을
3개의 다른 해석 계층으로 렌더링한다.

2. 전체 구조 (Global Layout)

모든 뷰는 동일한 run_id 기반으로 동작한다.

┌──────────────────────────────────────┐
│ Mellow-Link                [ View ▼ ] │
├──────────────────────────────────────┤
│                                      │
│              Main Area               │
│                                      │
└──────────────────────────────────────┘
View 전환 드롭다운
Admin

Dev Console

Operator View

User View

Operator

Operator View

User View

User

User View만 접근 가능

3. Dev Console (개발자 전용)
목적

내부 동작 검증

모드 분기 확인

도구 호출 추적

성능 분석

디버깅

화면 구조
┌───────────────┬──────────────────────┐
│ Run List      │ Run Detail           │
│ (좌측)        │ (우측)               │
└───────────────┴──────────────────────┘
3.1 좌측: Run List

표시 항목:

run_id

selected_mode

infer_ms

processing_time

tool_count

status

p95 이상치 플래그

3.2 우측: Run Detail
1️⃣ Timeline Panel

Turn 단위 흐름

mode 변화

tool_call 발생 시점

finish 시점

예시:

Turn 1 → fast
Turn 2 → tool_call: list_directory
Turn 3 → finish
2️⃣ Event Stream

plan_created

todo_done

tool_called

run_finished

escalation 기록

3️⃣ Raw Data Toggle

토글 시 표시:

prompt_chars

total_tokens

infer_ms

max_turns

routing log

fallback 여부

4️⃣ Performance Box

p50

p95

mean

infer_ms / processing_time 차이

escalation 발생 횟수

4. Operator View (운영자)
목적

작업이 정상적으로 진행되는지 확인

To-do 기반 진행 상태 확인

에러 감지

화면 구조
┌──────────────────────────────┐
│ Run Status Card              │
├──────────────────────────────┤
│ To-do List (T1~T7)           │
├──────────────────────────────┤
│ Current Step                 │
├──────────────────────────────┤
│ Recent Activity Log          │
└──────────────────────────────┘
표시 항목

전체 진행률 %

현재 실행 단계

완료된 To-do 체크 상태

마지막 활동 시간

에러 여부

표시하지 않을 것

tool JSON

내부 reasoning

prompt_chars

token 계산

routing debug

5. User View (일반 사용자)
목적

결과 소비

요약 중심

확장 기반 정보 구조

화면 구조
┌──────────────────────────────┐
│ Chat Window                  │
├──────────────────────────────┤
│ [ 확장 ] 버튼                │
└──────────────────────────────┘
특징

Summary-first 출력

확장 버튼 기반 progressive disclosure

최소 정보 노출

기술 메타 정보 비표시

6. View 전환 API 구조
GET /dev-console?run_id={run_id}
GET /operator-console?run_id={run_id}
GET /user-console?run_id={run_id}

공통 데이터 API:
GET /runs/{run_id}/dev
GET /runs/{run_id}/events

동일 run_id
→ 서로 다른 렌더러

7. 핵심 설계 원칙
1️⃣ Dev는 정보 최대

내부 상태를 숨기지 않는다

모든 판단 근거 확인 가능

2️⃣ Operator는 상태 중심

진행 여부가 가장 중요

세부 내부 정보는 감춘다

3️⃣ User는 결과 중심

결과 + 확장만

내부 시스템 구조 비노출

8. 현재 시스템 기준 진척도

이미 구현 완료:

plan_created 이벤트

thinking-lite 모드

summary-first 출력 정책

persona 최종 렌더링 분리

output_sanitizer

run_id 기반 구조

SSE 이벤트 스트림

performance metrics

현재 단계:

UX 구조 정리 단계

9. 다음 단계

Dev Console UI 완성

run_id 기준 3-view 렌더링 분리

Operator MVP 구현

User View 정제

Agent Progress UI 고도화

10. 장기 목표

도구를 보여주는 UI에서
판단 과정을 보여주는 UI로 진화
