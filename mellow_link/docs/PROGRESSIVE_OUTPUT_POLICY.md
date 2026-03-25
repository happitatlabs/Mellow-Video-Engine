# Progressive Output Policy (3-Layer System)

## 개요

Progressive Output Policy는 사용자의 요구에 따라 점진적으로 상세한 정보를 제공하는 3단계 시스템입니다.

## 3단계 구조

### Layer 1: Summary-First (기본)
- **조건**: 장문 요청 감지 (`long_form=True`) AND 확장 요청 없음
- **출력**: 10~15줄, 800자 이내
- **템플릿**: 구조화된 요약 형식
- **max_tokens**: 900

### Layer 2: Expansion Steps (확장 단계)
- **확장 v1** (`expansion_level=1`): "확장" 요청
  - 상세 분석 템플릿
  - max_tokens: 1800
- **확장 v2** (`expansion_level=2`): "확장2" 요청
  - 사례/비유 중심 템플릿
  - max_tokens: 2500
- **확장 v3** (`expansion_level=3`): "확장3" 요청
  - 기술/논문 톤 템플릿
  - max_tokens: 3500

### Layer 3: Thinking-Lite Mode
- **조건**: `mode=auto` AND deep keyword 있음 AND tool 키워드 없음 AND "report" 명시 없음
- **출력**: 12줄 이내, 900자 이내
- **도구 호출**: 최대 1개
- **max_tokens**: 900
- **max_turns**: 2

## 구현 위치

### 1. 확장 레벨 감지 (`agent_prompts.py`)

**함수**: `_get_expansion_level(user_input: str) -> int`

**감지 방법**:
- Level 0: 확장 요청 없음
- Level 1: "확장", "expand", "더 자세히" 등
- Level 2: "확장2", "expand2", "v2" 등
- Level 3: "확장3", "expand3", "v3" 등

### 2. OUTPUT_POLICY 템플릿 (`agent_prompts.py`)

**템플릿 상수**:
- `OUTPUT_POLICY_SUMMARY_FIRST`: 기본 요약 템플릿
- `OUTPUT_POLICY_EXPAND_V1`: 확장 v1 템플릿
- `OUTPUT_POLICY_EXPAND_V2`: 확장 v2 템플릿
- `OUTPUT_POLICY_EXPAND_V3`: 확장 v3 템플릿
- `OUTPUT_POLICY_THINKING_LITE`: thinking-lite 템플릿

**함수**: `_get_output_policy_block(expansion_level, is_thinking_lite) -> str`

### 3. Thinking-Lite 모드 감지 (`orchestrator_chat.py`)

**함수**: `_select_mode_for_query(query, prompt_category) -> str`

**조건**:
- Deep keyword 감지됨
- Tool keyword 없음
- "report" 명시 없음
- → `"thinking-lite"` 반환

### 4. 도구 호출 제한 (`agent_brain.py`)

**thinking-lite 모드**:
- `tool_call_count` 추적
- `max_tool_calls_lite = 1`
- 제한 초과 시 finish 도구로 강제 전환

### 5. Max Tokens 결정 (`agent_brain.py`)

**함수**: `_determine_max_tokens(effective_mode, user_input, force_expanded, expansion_level, is_thinking_lite)`

**로직**:
- thinking-lite: 900
- expansion_level=3: 3500
- expansion_level=2: 2500
- expansion_level=1: 1800
- long_form (level 0): 900

## 환경변수 설정

```env
# THINKING 모드 max_tokens
THINKING_MAX_TOKENS_DEFAULT=900
THINKING_MAX_TOKENS_EXPANDED=1800
THINKING_MAX_TOKENS_EXPANDED_V2=2500
THINKING_MAX_TOKENS_EXPANDED_V3=3500

# THINKING-LITE 모드
THINKING_LITE_MAX_TOKENS=900
```

## 사용 예시

### 시나리오 1: 장문 질문 (Summary-First)

**사용자**: "이 프로젝트의 아키텍처를 분석해줘"

1. `long_form=True` 감지
2. `expansion_level=0`
3. OUTPUT_POLICY_SUMMARY_FIRST 적용
4. `max_tokens=900`
5. 결과: 구조화된 요약 (800자 이내)

### 시나리오 2: 확장 요청 (Expand v1)

**사용자**: "확장"

1. `expansion_level=1` 감지
2. OUTPUT_POLICY_EXPAND_V1 적용
3. `max_tokens=1800`
4. 결과: 상세 분석 응답

### 시나리오 3: 확장2 요청 (Expand v2)

**사용자**: "확장2"

1. `expansion_level=2` 감지
2. OUTPUT_POLICY_EXPAND_V2 적용
3. `max_tokens=2500`
4. 결과: 사례/비유 중심 응답

### 시나리오 4: 확장3 요청 (Expand v3)

**사용자**: "확장3"

1. `expansion_level=3` 감지
2. OUTPUT_POLICY_EXPAND_V3 적용
3. `max_tokens=3500`
4. 결과: 기술/논문 톤 응답

### 시나리오 5: Thinking-Lite 모드

**사용자**: "이 데이터를 분석해줘" (auto 모드)

1. Deep keyword ("분석") 감지
2. Tool keyword 없음
3. "report" 명시 없음
4. `effective_mode="thinking-lite"`
5. OUTPUT_POLICY_THINKING_LITE 적용
6. `max_tokens=900`, `max_turns=2`, `max_tool_calls=1`
7. 결과: 간결한 분석 응답

## 코드 변경 요약

### 수정된 파일

1. **`mellow_link/core/agent_prompts.py`**
   - 확장 레벨별 템플릿 상수 추가
   - `_get_expansion_level()` 함수 추가
   - `_get_output_policy_block()` 함수 추가
   - `build_system_prompt()` 및 `build_system_prompt_assembled()`에 확장 레벨 파라미터 추가

2. **`mellow_link/core/agent_brain.py`**
   - `_get_expansion_level` import 추가
   - 확장 레벨 감지 및 전달
   - thinking-lite 모드 감지 및 처리
   - `_determine_max_tokens()`에 확장 레벨 및 thinking-lite 지원 추가
   - thinking-lite 모드에서 도구 호출 제한 로직 추가
   - thinking-lite 모드에서 max_turns 제한 (2턴)

3. **`mellow_link/core/orchestrator_chat.py`**
   - `_select_mode_for_query()`에 thinking-lite 모드 감지 로직 추가

4. **`mellow_link/services/llm_service.py`**
   - `get_model_for_mode()`에 thinking-lite 모드 지원 추가 (thinking 모델 사용)

5. **`mellow_link/.env`**
   - 확장 레벨별 max_tokens 환경변수 추가
   - thinking-lite 모드 환경변수 추가

### 새로 생성된 파일

1. **`mellow_link/tests/test_progressive_output_policy.py`**
   - Progressive Output Policy 테스트 코드

2. **`mellow_link/docs/PROGRESSIVE_OUTPUT_POLICY.md`**
   - 이 문서

## 테스트 결과

모든 테스트 통과:
- 확장 레벨 감지 (0, 1, 2, 3)
- OUTPUT_POLICY 블록 선택
- 장문 요청 감지
- Progressive Disclosure 흐름

## 성능 영향

- **예상 효과**: 장문 질문의 p95 지연 시간 감소
- **Summary-First**: 900 토큰으로 빠른 응답
- **Progressive Expansion**: 사용자 요구에 따라 점진적 상세화
- **Thinking-Lite**: 분석 요청에 대한 경량 모드로 빠른 응답

## 보안 및 도구 라우팅

- 보안 정책 변경 없음
- 도구 라우팅 동작 변경 없음
- thinking-lite 모드에서만 도구 호출 제한 (최대 1개)
