# SLM 기반 ReAct 루프 개선 사항

## 개요

SLM(Small Language Model)의 추론 한계를 고려하여 ReAct 루프의 붕괴 문제를 해결하기 위한 개선 사항입니다.

## 주요 문제점 및 해결 방안

### 1. Prompt Leakage & Echoing (프롬프트 복창)

**문제**: LLM이 시스템 프롬프트를 그대로 출력하며 과업을 회피함.

**해결**:
- **프롬프트 최적화**: 시스템 프롬프트를 간결하게 재구성 (147줄 → 약 30줄)
- **출력 필터링**: `_filter_prompt_echoing()` 함수로 프롬프트 복창 패턴 자동 제거
  - `[CRITICAL: ...]`, `[COMMAND: ...]` 형태의 프롬프트 복창 감지 및 제거
  - 경고 문구, 지시 문구 복창 패턴 제거

**구현 위치**: `agent_brain.py`
- `SYSTEM_PROMPT_TEMPLATE`: 간결한 프롬프트 구조
- `_filter_prompt_echoing()`: 출력 필터링 함수
- `parse_action()`: JSON 파싱 전 필터링 적용

### 2. Persistent Hallucination (도구 할루시네이션)

**문제**: 존재하지 않는 도구(예: `analyze_code`)를 반복 호출함.

**해결**:
- **화이트리스트 검증 강화**: 에러 메시지에 유사 도구명 제안 추가
- **명확한 차단 메시지**: 할루시네이션 감지 시 학습 데이터의 가상 도구임을 명시
- **프롬프트 개선**: 도구 목록을 명확하게 제시하고 "목록에 없는 도구 호출 금지" 강조

**구현 위치**: `agent_brain.py` (744-768줄)
```python
# 할루시네이션 감지 시 명확한 에러 메시지
err_msg = (
    f"[ERROR] 할루시네이션 감지: '{action.tool}'은(는) 존재하지 않는 도구입니다.\n"
    f"⚠️ 학습 데이터의 가상 도구를 호출하지 마세요..."
)
```

### 3. Argument Omission (인자 누락)

**문제**: 필수 인자(`file_path` 등)가 포함된 JSON 스키마를 준수하지 못하고 빈 인자를 송출함.

**해결**:
- **에러 메시지 개선**: 필수 인자 누락 시 정확한 JSON 형식 예시 제공
- **프롬프트 강화**: "필수 인자 반드시 포함" 명시 및 예시 추가

**구현 위치**: `tool_registry.py` (191-202줄)
```python
return (
    f"[Error] {tool_name} 필수 인자 누락: {', '.join(missing_required)}\n"
    f"제공된 인자: {list(filtered_args.keys())}\n"
    f"필수 인자 예시: {{\"tool\":\"{tool_name}\",\"args\":{{\"{missing_required[0]}\":\"값\"}}}}"
)
```

### 4. Negative Feedback Loop (부정적 피드백 루프)

**문제**: RAG를 통해 과거 실패 로그를 참조한 뒤, 동일한 오류 패턴을 재현함.

**해결**:
- **강제적 자가 치유 로직**: 에러 발생 시 자동으로 `get_past_failure_context` 호출 및 주입
- **과거 실패 패턴 분석**: 실패 컨텍스트를 LLM에 주입하여 동일 오류 방지

**구현 위치**: `agent_brain.py` (850-870줄)
```python
# [FORCED_SELF_CORRECTION] 에러 발생 시 자동으로 get_past_failure_context 호출
if observation.startswith("[Error]") or "[ERROR]" in observation.upper():
    failure_context = get_past_failure_context(target_file=None, limit=3)
    enhanced_observation = f"{observation}\n\n[과거 실패 패턴 분석]\n{failure_context}..."
```

## 개선된 프롬프트 구조

### Before (기존)
- 약 147줄의 복잡한 프롬프트
- 다중 우선순위 체제, 페르소나 지침 등 혼재
- SLM이 프롬프트 자체를 복창하는 문제

### After (개선)
- 약 30줄의 간결한 프롬프트
- 핵심 지침만 명확하게 제시
- JSON 출력 형식, 도구 화이트리스트, 필수 인자 등 핵심만 포함

```python
SYSTEM_PROMPT_TEMPLATE = """\
[CRITICAL: OUTPUT_FORMAT]
출력은 반드시 JSON만 허용. 다른 텍스트 금지.

[CRITICAL: TOOL_WHITELIST]
사용 가능한 도구는 아래 목록에만 있음. 목록에 없는 도구 호출 금지.
{tools_json}

[CRITICAL: REQUIRED_ARGS]
도구 호출 시 필수 인자 반드시 포함. args가 비어있으면 실패.

[CRITICAL: ERROR_RECOVERY]
[ERROR] 발생 시:
1. get_past_failure_context 호출하여 과거 실패 패턴 확인
2. 실패 원인 분석 후 올바른 도구/인자로 재시도
...
"""
```

## 출력 필터링 전략

### 페르소나와 실행력 충돌 방지

**문제**: 페르소나 스타일 텍스트가 JSON 파싱을 방해함.

**해결**:
- **Thought/Action 단계**: 페르소나 완전 분리, 기술적 분석만 허용
- **finish 도구 호출 시에만**: 페르소나 적용 (`_apply_persona_to_summary()`)
- **출력 필터링**: JSON 파싱 전 프롬프트 복창 텍스트 자동 제거

**구현 위치**: `agent_brain.py`
- `_filter_prompt_echoing()`: 프롬프트 복창 패턴 제거
- `parse_action()`: 필터링 후 JSON 파싱
- `_apply_persona_to_summary()`: finish 시에만 페르소나 적용

## 사용 방법

### 1. 자동 자가 치유 활성화

에러 발생 시 자동으로 `get_past_failure_context`가 호출되므로 별도 설정 불필요.

### 2. 출력 필터링

`parse_action()` 함수가 자동으로 프롬프트 복창 텍스트를 제거하므로 별도 설정 불필요.

### 3. 도구 할루시네이션 방지

화이트리스트 검증이 자동으로 수행되며, 할루시네이션 감지 시 명확한 에러 메시지와 함께 유사 도구명을 제안합니다.

## 테스트 권장 사항

1. **프롬프트 복창 테스트**: 시스템 프롬프트를 그대로 출력하는지 확인
2. **도구 할루시네이션 테스트**: 존재하지 않는 도구 호출 시도
3. **인자 누락 테스트**: 필수 인자 없이 도구 호출 시도
4. **자가 치유 테스트**: 에러 발생 시 `get_past_failure_context` 자동 호출 확인

## 추가 개선 가능 사항

1. **도구명 자동 보정**: Typo 감지 시 유사 도구명 자동 제안
2. **인자 타입 검증**: JSON 스키마 타입 검증 강화
3. **컨텍스트 윈도우 최적화**: SLM의 제한된 컨텍스트 윈도우 고려한 히스토리 관리

## 참고 파일

- `mellow_link/core/agent_brain.py`: ReAct 루프 및 프롬프트 관리
- `mellow_link/core/tool_registry.py`: 도구 실행 및 인자 검증
- `mellow_link/core/recovery_manager.py`: 에러 복구 로직
- `mellow_link/core/agent_tools.py`: `get_past_failure_context` 도구 구현
