# Output Policy - 출력 정제 정책

사용자 응답의 품질과 일관성을 보장하기 위한 출력 정제 시스템입니다.

## 개요

Output Sanitizer는 다음을 보장합니다:
1. **Tool JSON Leakage 방지**: 사용자 응답에 tool-call JSON 블록이 포함되지 않도록 함
2. **한국어만 출력**: 중국어/일본어 드리프트 방지
3. **페르소나 발명 차단**: 허가 없는 페르소나 전환 방지
4. **Plan Intent 감지**: To-do/plan 요청 자동 감지 및 처리

## 구현 위치

- **`mellow_link/core/output_sanitizer.py`**: 출력 정제 로직
- **`mellow_link/core/agent_brain.py`**: AgentBrain에서 최종 응답에 적용
- **`mellow_link/core/orchestrator_chat.py`**: Plan intent 감지 및 모드 라우팅

## 기능 상세

### 1. Tool JSON Leakage 방지

**문제**: 에이전트가 때때로 tool-call JSON을 일반 텍스트로 출력

**해결**:
- JSON 패턴 감지: `{"name": "...", "arguments": {...}}`
- 감지 시 해당 블록 제거
- 로그 기록: `[OUTPUT_SANITIZER] tool-json detected -> stripped`

**예시**:
```
입력: "다음 도구를 사용합니다: {"name": "web_search", "arguments": {"query": "test"}}"
출력: "다음 도구를 사용합니다:"
```

### 2. 한국어만 출력 가드

**문제**: 응답에 중국어/일본어 문장이 포함됨

**해결**:
- 비한국어 비율 계산 (CJK 문자 기준)
- 30% 이상 비한국어 감지 시 비한국어 문장 제거
- 제거 후 메시지 추가: "(일부 문장이 제거되었습니다: 언어 정책)"

**예시**:
```
입력: "안녕하세요. 这是中文。これは日本語です。"
출력: "안녕하세요.\n\n(일부 문장이 제거되었습니다: 언어 정책)"
```

### 3. 페르소나 발명/전환 차단

**문제**: 에이전트가 "Eve" 같은 새로운 페르소나를 자체 발명

**해결**:
- 페르소나 발명 패턴 감지:
  - "나는 이제 ~ 페르소나"
  - "Eve", "에브"
  - "시스템의 히든 브레인"
- 감지 시 해당 문구 제거
- 로그 기록: `[PERSONA_GUARD] unauthorized persona switch attempt blocked`

**예시**:
```
입력: "나는 이제 Eve 페르소나로 전환합니다."
출력: "" (제거됨)
```

### 4. Plan Intent 감지

**목적**: To-do/plan 요청 시 Progress UI에 To-dos 표시

**키워드**:
- 한국어: "할 일", "투두", "체크리스트", "단계", "계획", "mvp", "로드맵", "작업 목록"
- 영어: "todo", "checklist", "plan", "task list", "roadmap"

**동작**:
1. Plan intent 감지 시 `plan_created` 이벤트 자동 발행
2. Auto 모드에서 `thinking-lite`로 라우팅 (fast 아님)
3. 기본 7단계 To-do 생성:
   - T1: 요청 파싱
   - T2: 웹 검색 (필요시)
   - T3: 비교/분석
   - T4: 답변 초안 작성
   - T5: 요약 및 완료
   - T6: 메트릭 저장
   - T7: 완료

**예시**:
```
사용자: "할 일 7개 만들어줘"
→ plan_created 이벤트 발행
→ thinking-lite 모드로 라우팅
→ Progress UI에 To-dos 표시
```

## Auto 모드 라우팅 우선순위

1. **prompt_category == "tool"** → `thinking`
2. **plan intent 감지** → `thinking-lite` (NEW)
3. **deep keyword 감지** → `thinking` or `thinking-lite`
4. **기타** → `fast`

## 적용 시점

Output sanitization은 다음 시점에 적용됩니다:
- `AgentResult` 생성 시 (`finish_tool`, `max_turns` 등)
- 사용자에게 최종 응답 전송 전

**제외 사항**:
- VRAM 크리티컬 메시지 (시스템 메시지)
- 보안 차단 메시지 (시스템 메시지)
- 관찰 요구 미충족 메시지 (시스템 메시지)

## 테스트

`mellow_link/tests/test_output_sanitizer.py`에서 다음을 테스트:
- Tool JSON 감지 및 제거
- 한국어만 출력 강제
- 페르소나 발명 차단
- Plan intent 감지
- 비한국어 비율 계산

## 설정

현재는 하드코딩된 임계값 사용:
- 비한국어 비율 임계값: 30%
- Tool JSON 패턴: 정규식 기반
- 페르소나 패턴: 정규식 기반

향후 환경 변수로 조정 가능하도록 확장 예정.
