# Max Tokens Control for THINKING Mode

## 개요

THINKING 모드에서 장문 질문에 대한 응답 길이를 제어하여 p95 지연 시간을 감소시키는 패치입니다.

## 목표

- 장문 요약 모드: 작은 max_tokens (기본 900)로 빠른 응답
- 확장 모드: 더 큰 max_tokens (기본 1800)로 전체 답변 제공
- THINKING 모드에만 적용 (FAST/RESEARCH 모드 제외)

## 구현 위치

### 1. 환경변수 설정 (`.env`)

```env
# THINKING 모드 기본 요약 max_tokens (기본값: 900)
THINKING_MAX_TOKENS_DEFAULT=900

# THINKING 모드 확장 max_tokens (기본값: 1800)
THINKING_MAX_TOKENS_EXPANDED=1800

# 요약 우선 모드 최대 문자 수 (OUTPUT_POLICY와 연동, 기본값: 800)
SUMMARY_FIRST_MAX_CHARS=800
```

### 2. Max Tokens 결정 로직 (`agent_brain.py`)

**함수**: `_determine_max_tokens(effective_mode, user_input, force_expanded) -> Optional[int]`

**로직**:
1. THINKING 모드가 아니면 `None` 반환 (기본값 사용)
2. 확장 모드면 `THINKING_MAX_TOKENS_EXPANDED` 반환
3. 장문 요청이면 `THINKING_MAX_TOKENS_DEFAULT` 반환
4. 그 외에는 `None` 반환 (기본값 사용)

### 3. LLM 호출 시 적용 (`agent_brain.py`)

**`_call_llm` 메서드**:
- `max_tokens` 파라미터 추가
- Ollama API에 `options={"num_predict": max_tokens}` 전달
- 모든 재시도/폴백 경로에도 동일한 `max_tokens` 적용

**호출 경로**:
```python
max_tokens = self._determine_max_tokens(
    effective_mode=effective_mode,
    user_input=user_input,
    force_expanded=force_expanded,
)

llm_response, tool_calls, infer_ms = await self._call_llm(
    messages,
    tools=tools_schema,
    session_state=fallback_state,
    mode=effective_mode,
    max_tokens=max_tokens,
)
```

### 4. LLMService 통합 (`llm_service.py`)

LLMService.chat은 이미 `**kwargs`를 받아서 `options` 딕셔너리를 처리합니다:
- `options={"num_predict": max_tokens}`가 전달되면 Ollama API에 그대로 전달
- 기존 `num_ctx` 설정과 병합됨

## 동작 방식

### 시나리오 1: 장문 질문 (요약 모드)

**사용자 입력**: "이 프로젝트의 아키텍처를 분석해줘"

1. `_is_long_form_request()` → `True`
2. `force_expanded` → `False`
3. `_determine_max_tokens()` → `900`
4. OUTPUT_POLICY 블록 주입
5. LLM 호출 시 `options={"num_predict": 900}` 전달
6. 결과: 짧은 요약 응답 (800자 이내)

### 시나리오 2: 확장 요청

**사용자 입력**: "확장해줘"

1. `_is_expansion_request()` → `True`
2. `force_expanded` → `True`
3. `_determine_max_tokens()` → `1800`
4. OUTPUT_POLICY 블록 주입 안 함
5. LLM 호출 시 `options={"num_predict": 1800}` 전달
6. 결과: 전체 답변 제공

### 시나리오 3: 일반 질문

**사용자 입력**: "파일을 읽어줘"

1. `_is_long_form_request()` → `False`
2. `force_expanded` → `False`
3. `_determine_max_tokens()` → `None`
4. LLM 호출 시 `max_tokens` 제한 없음 (기본값 사용)
5. 결과: 정상 응답

## 로깅

TTFT_DEBUG 환경변수가 설정되면 max_tokens 값이 로깅됩니다:

```python
if os.getenv("TTFT_DEBUG", "").strip().lower() in ("1", "true", "yes"):
    logger.info(f"[_call_llm] TTFT_DEBUG: max_tokens={max_tokens} (num_predict={max_tokens})")
```

## 제약사항

1. **THINKING 모드만 적용**: FAST/RESEARCH 모드에는 영향 없음
2. **도구 호출 파싱 보존**: 도구 호출 로직에는 영향 없음
3. **최종 답변 생성에만 적용**: ReAct 루프의 모든 LLM 호출에 적용됨 (일관성 유지)

## 성능 영향

- **예상 효과**: THINKING 모드 장문 질문의 p95 지연 시간 감소
- **토큰 제한**: 요약 모드에서 900 토큰으로 제한하여 생성 시간 단축
- **확장 모드**: 1800 토큰으로 충분한 답변 제공

## 코드 변경 요약

### 수정된 파일

1. **`mellow_link/.env`**
   - `THINKING_MAX_TOKENS_DEFAULT` 환경변수 추가
   - `THINKING_MAX_TOKENS_EXPANDED` 환경변수 추가
   - `SUMMARY_FIRST_MAX_CHARS` 환경변수 추가

2. **`mellow_link/core/agent_brain.py`**
   - `_determine_max_tokens()` 메서드 추가
   - `_call_llm()` 메서드에 `max_tokens` 파라미터 추가
   - `_is_long_form_request` import 추가
   - 모든 `self._llm.chat()` 호출에 `chat_kwargs` 전달

### 새로 생성된 파일

1. **`mellow_link/docs/MAX_TOKENS_CONTROL.md`**
   - 이 문서

## 테스트

벤치마크 실행 시 장문 프롬프트의 p95 지연 시간이 감소하는지 확인:

```bash
# 벤치마크 실행
python mellow_link/scripts/run_mode_benchmark.py

# TTFT_DEBUG 활성화하여 max_tokens 로깅 확인
TTFT_DEBUG=1 python mellow_link/scripts/run_mode_benchmark.py
```

## 관련 패치

- **Long-form Output Policy**: 요약 우선 정책과 함께 작동
- 두 패치가 함께 적용되어 장문 질문의 응답 시간을 크게 단축
